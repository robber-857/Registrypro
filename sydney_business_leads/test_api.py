"""Offline API contract tests. XML below is synthetic, not a live client list."""

from datetime import date
import io
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs

from abr_api import ApiClient, parse_entity_response, parse_postcode_response
from lead_common import LeadError


ABN = "51835430479"
SECOND_ABN = "11111111111"
GUID = "test-only-guid-never-valid"


def payload(content: str, request: str = "") -> bytes:
    return (
        '<ABRPayloadSearchResults xmlns="http://abr.business.gov.au/ABRXMLSearch/">'
        f'<request>{request}</request><response><dateRegisterLastUpdated>2026-11-15</dateRegisterLastUpdated>'
        '<dateTimeRetrieved>2026-11-16T10:00:00+11:00</dateTimeRetrieved>'
        f'{content}</response></ABRPayloadSearchResults>'
    ).encode("utf-8")


def postcode_xml(abns: list[str] | None = None, count: int | None = None) -> bytes:
    abns = [ABN] if abns is None else abns
    count = len(abns) if count is None else count
    return payload(f'<abnList><numberOfRecords>{count}</numberOfRecords>'
                   + ''.join(f'<abn>{abn}</abn>' for abn in abns) + '</abnList>',
                   f'<postcodeSearchRequest><authenticationGUID>{GUID}</authenticationGUID></postcodeSearchRequest>')


def entity_xml(extra: str = "", abn: str = ABN) -> bytes:
    return payload(
        '<businessEntity202001><recordLastUpdatedDate>2026-10-01</recordLastUpdatedDate>'
        f'<ABN><identifierValue>{abn}</identifierValue><isCurrentIndicator>Y</isCurrentIndicator></ABN>'
        '<entityStatus><entityStatusCode>Active</entityStatusCode><effectiveFrom>2020-01-01</effectiveFrom>'
        '<effectiveTo>0001-01-01</effectiveTo></entityStatus>' + extra + '</businessEntity202001>'
    )


class ApiParserTests(unittest.TestCase):
    def test_official_lowercase_abn_list_and_namespaces(self):
        result = parse_postcode_response(postcode_xml([ABN, SECOND_ABN]))
        self.assertEqual(result["abns"], [ABN, SECOND_ABN])
        self.assertEqual(result["declared_count"], 2)
        self.assertFalse(result["cached"])

    def test_truncation_and_duplicate_results_fail(self):
        for raw in (postcode_xml([ABN], 2), postcode_xml([ABN, ABN])):
            with self.subTest(raw=raw), self.assertRaises(LeadError):
                parse_postcode_response(raw)

    def test_no_records_is_distinct_from_malformed_or_failed_response(self):
        empty = payload('<exception><exceptionCode>SEARCH</exceptionCode>'
                        '<exceptionDescription>No records found</exceptionDescription></exception>')
        self.assertEqual(parse_postcode_response(empty)["abns"], [])
        for raw in (payload(''), b'<html>error</html>', b'not XML'):
            with self.subTest(raw=raw), self.assertRaises(LeadError):
                parse_postcode_response(raw)

    def test_service_error_redacts_guid(self):
        raw = payload(f'<exception><exceptionCode>WEBSERVICES</exceptionCode>'
                      f'<exceptionDescription>Unrecognised GUID {GUID}</exceptionDescription></exception>')
        with self.assertRaises(LeadError) as caught:
            parse_postcode_response(raw, GUID)
        self.assertNotIn(GUID, str(caught.exception))
        self.assertIn("REDACTED", str(caught.exception))

    def test_registered_names_current_dates_and_legacy_names_stay_separate(self):
        raw = entity_xml(
            '<entityType><entityTypeCode>PRV </entityTypeCode><entityDescription>Australian Private Company</entityDescription></entityType>'
            '<mainName><organisationName>EXAMPLE PTY LTD</organisationName><effectiveFrom>2020-01-01</effectiveFrom><effectiveToBlank/></mainName>'
            '<businessName><organisationName>Example Cafe</organisationName><effectiveFrom>2022-01-01</effectiveFrom></businessName>'
            '<businessName><organisationName>Current Shop</organisationName><effectiveTo>0001-01-01</effectiveTo></businessName>'
            '<businessName><organisationName>Old Shop</organisationName><effectiveTo>2025-01-01</effectiveTo></businessName>'
            '<businessName><organisationName>Future Shop</organisationName><effectiveFrom>2028-01-01</effectiveFrom></businessName>'
            '<mainTradingName><organisationName>Pre-2012 Name</organisationName><effectiveTo>2012-01-01</effectiveTo></mainTradingName>'
            '<mainBusinessPhysicalAddress><stateCode>NSW</stateCode><postcode>2000</postcode><effectiveTo>0001-01-01</effectiveTo></mainBusinessPhysicalAddress>'
            '<goodsAndServicesTax><effectiveFrom>2020-01-01</effectiveFrom><effectiveTo>0001-01-01</effectiveTo></goodsAndServicesTax>'
        )
        result = parse_entity_response(raw, ABN, today=date(2026, 11, 16))
        self.assertEqual(result["legal_name"], "EXAMPLE PTY LTD")
        self.assertEqual(result["business_names"], ["Example Cafe", "Current Shop"])
        self.assertEqual(result["historical_trading_names"], ["Pre-2012 Name"])
        self.assertEqual(result["entity_type_code"], "PRV")
        self.assertEqual((result["state"], result["postcode"]), ("NSW", "2000"))
        self.assertEqual(result["gst_registered"], "Yes")
        self.assertNotIn("street_address", result)

    def test_suppression_and_personal_legal_names(self):
        suppressed = parse_entity_response(entity_xml(), ABN)
        self.assertEqual(suppressed["legal_name"], "")
        self.assertEqual(suppressed["business_names"], [])
        self.assertEqual(suppressed["state"], "")
        self.assertEqual(suppressed["gst_registered"], "Unknown")
        person = parse_entity_response(entity_xml(
            '<legalName><givenName>TEST</givenName><otherGivenName>A</otherGivenName>'
            '<familyName>PERSON</familyName><effectiveTo>0001-01-01</effectiveTo></legalName>'
        ), ABN)
        self.assertEqual(person["legal_name"], "TEST A PERSON")
        self.assertEqual(person["gst_registered"], "No")

    def test_wrong_abn_cannot_be_silently_attached_to_requested_abn(self):
        with self.assertRaises(LeadError):
            parse_entity_response(entity_xml(abn=SECOND_ABN), ABN)

    def test_effective_dates_use_the_service_calendar_date(self):
        raw = entity_xml('<businessName><organisationName>New Today</organisationName>'
                         '<effectiveFrom>2026-11-16</effectiveFrom></businessName>')
        with patch('abr_api.datetime') as clock:
            clock.now.return_value.date.return_value = date(2026, 11, 15)
            self.assertEqual(parse_entity_response(raw, ABN)['business_names'], ['New Today'])


class ApiClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cache = Path(self.temp.name) / 'cache.sqlite3'

    def tearDown(self):
        self.temp.cleanup()

    def client(self, **kwargs):
        return ApiClient(GUID, self.cache, min_interval=0, **kwargs)

    def test_post_body_has_guid_but_url_and_cache_do_not(self):
        with self.client() as client, patch('abr_api.urlopen', return_value=io.BytesIO(postcode_xml())) as request:
            first = client.search_postcode('2000')
            second = client.search_postcode('2000')
            self.assertFalse(first['cached'])
            self.assertTrue(second['cached'])
            self.assertEqual(request.call_count, 1)
            req = request.call_args.args[0]
            self.assertEqual(req.get_method(), 'POST')
            self.assertNotIn(GUID, req.full_url)
            self.assertEqual(parse_qs(req.data.decode())['authenticationGuid'], [GUID])
        self.assertNotIn(GUID.encode(), self.cache.read_bytes())

    def test_postcode_cache_expires_after_one_hour(self):
        with self.client() as client, patch('abr_api.urlopen', side_effect=[io.BytesIO(postcode_xml()), io.BytesIO(postcode_xml([]))]) as request:
            client.search_postcode('2000')
            client._db.execute('UPDATE abr_cache_v1 SET stored_at=?', (time.time() - 3601,))
            client._db.commit()
            result = client.search_postcode('2000')
            self.assertEqual(result['abns'], [])
            self.assertEqual(request.call_count, 2)

    def test_detail_cache_survives_new_client_and_refresh_bypasses_it(self):
        with self.client() as client, patch('abr_api.urlopen', return_value=io.BytesIO(entity_xml())):
            client.lookup(ABN)
        with self.client() as client, patch('abr_api.urlopen') as request:
            self.assertEqual(client.lookup(ABN)['abn'], ABN)
            request.assert_not_called()
        with self.client(refresh=True) as client, patch('abr_api.urlopen', return_value=io.BytesIO(entity_xml())) as request:
            client.lookup(ABN)
            self.assertEqual(request.call_count, 1)

    def test_error_payload_is_never_cached(self):
        with self.client() as client, patch('abr_api.urlopen', side_effect=[io.BytesIO(payload('')), io.BytesIO(postcode_xml())]) as request:
            with self.assertRaises(LeadError):
                client.search_postcode('2000')
            self.assertEqual(client.search_postcode('2000')['abns'], [ABN])
            self.assertEqual(request.call_count, 2)

    def test_transient_http_error_retries_but_nontransient_error_fails(self):
        transient = HTTPError('https://abr.business.gov.au/', 429, 'rate limited', {'Retry-After': '1'}, None)
        with self.client() as client, patch('abr_api.time.sleep') as sleep, patch('abr_api.urlopen', side_effect=[transient, io.BytesIO(postcode_xml())]) as request:
            self.assertEqual(client.search_postcode('2000')['abns'], [ABN])
            self.assertEqual(request.call_count, 2)
            sleep.assert_called_once_with(1.0)
        denied = HTTPError('https://abr.business.gov.au/', 403, 'forbidden', {}, None)
        with self.client(refresh=True) as client, patch('abr_api.urlopen', side_effect=denied) as request:
            with self.assertRaises(LeadError):
                client.search_postcode('2000')
            self.assertEqual(request.call_count, 1)


if __name__ == '__main__':
    unittest.main()
