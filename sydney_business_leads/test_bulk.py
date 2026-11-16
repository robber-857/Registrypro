"""Meaningful parser checks using explicitly synthetic, local records only."""

import gzip
import tempfile
import unittest
import zipfile
from pathlib import Path

from abr_bulk import iter_bulk
from lead_common import LeadError


FIXTURE = Path(__file__).parent / "fixtures" / "demo_bulk.xml"


class BulkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def write(self, name, content):
        path = self.root / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_real_structure_names_addresses_status_and_dates(self):
        rows = list(iter_bulk([FIXTURE]))
        self.assertEqual(len(rows), 5)
        first, sole_trader, cancelled, interstate, chatswood = rows
        self.assertEqual(first["abn"], "11111111111")
        self.assertEqual(first["legal_name"], "DEMO HARBOUR SERVICES PTY LTD")
        self.assertEqual(first["business_names"], ["DEMO Harbour Repairs", "DEMO Harbour Studio"])
        self.assertEqual(first["historical_trading_names"],
                         ["DEMO Old Harbour Trading", "DEMO Legacy Workshop"])
        self.assertEqual(first["entity_type_code"], "PRV")
        self.assertEqual(first["status"], "Active")
        self.assertEqual(first["status_from"], "2025-01-15")
        self.assertEqual(first["source_updated_at"], "2026-11-10")
        self.assertEqual(first["gst_registered"], "Yes")
        self.assertEqual(first["state"], "NSW")
        self.assertEqual(first["postcode"], "2000")
        self.assertEqual(first["acn"], "111111111")
        self.assertEqual(sole_trader["legal_name"], "DEMO Alex EXAMPLE")
        self.assertEqual(sole_trader["postcode"], "2150")
        self.assertEqual(sole_trader["state"], "NSW")
        self.assertEqual(sole_trader["gst_registered"], "No")
        self.assertEqual(cancelled["status"], "Cancelled")
        self.assertEqual(cancelled["business_names"], [])
        self.assertEqual(interstate["state"], "VIC")
        self.assertEqual(interstate["postcode"], "3000")
        self.assertEqual(interstate["gst_registered"], "Unknown")
        self.assertEqual(chatswood["postcode"], "2067")

    def test_namespaces(self):
        text = FIXTURE.read_text(encoding="utf-8").replace(
            "<Transfer>", '<Transfer xmlns="urn:synthetic-abn-bulk-fixture">')
        path = self.write("namespaced.xml", text)
        self.assertEqual(list(iter_bulk([path]))[0]["business_names"],
                         ["DEMO Harbour Repairs", "DEMO Harbour Studio"])

    def test_zip_gzip_and_recursive_directory_no_disk_extraction(self):
        nested = self.root / "nested"
        nested.mkdir()
        with gzip.open(nested / "part.xml.gz", "wb") as stream:
            stream.write(FIXTURE.read_bytes())
        with zipfile.ZipFile(self.root / "part.zip", "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("../../not-extracted.xml", FIXTURE.read_bytes())
            archive.writestr("schema.xsd", "ignored schema")
        rows = list(iter_bulk([self.root]))
        self.assertEqual(len(rows), 10)
        self.assertTrue(any("::../../not-extracted.xml" in row["source_file"] for row in rows))
        self.assertEqual(len(list(self.root.rglob("*.xml"))), 0)

    def test_duplicate_input_paths_processed_once(self):
        rows = list(iter_bulk([FIXTURE, FIXTURE.resolve()]))
        self.assertEqual(len(rows), 5)

    def test_suppressed_missing_names_and_location_are_not_fabricated(self):
        path = self.write("suppressed.xml", '<Transfer><ABR><ABN status="ACT">99999999999</ABN></ABR></Transfer>')
        row = list(iter_bulk([path]))[0]
        self.assertEqual(row["legal_name"], "")
        self.assertEqual(row["state"], "")
        self.assertEqual(row["postcode"], "")
        self.assertEqual(row["business_names"], [])

    def test_bad_root_empty_malformed_and_missing_abn_fail(self):
        for text in (
            "<html><body>Not a data file</body></html>",
            "<Transfer></Transfer>",
            "<Transfer><ABR>",
            '<Transfer><ABR><ABN status="ACT">123</ABN></ABR></Transfer>',
            '<Transfer><ABR><ABN>11111111111</ABN></ABR></Transfer>',
            '<Transfer><Wrapper><ABR><ABN status="ACT">11111111111</ABN></ABR></Wrapper></Transfer>',
        ):
            with self.subTest(xml=text):
                path = self.write("bad.xml", text)
                with self.assertRaises(LeadError):
                    list(iter_bulk([path]))

    def test_missing_unsupported_and_empty_archives_fail(self):
        for paths in ([], [self.root / "missing.xml"], [self.write("bad.txt", "test")]):
            with self.subTest(paths=paths):
                with self.assertRaises(LeadError):
                    list(iter_bulk(paths))
        with zipfile.ZipFile(self.root / "empty.zip", "w") as archive:
            archive.writestr("readme.txt", "no XML")
        with self.assertRaises(LeadError):
            list(iter_bulk([self.root / "empty.zip"]))
        broken = self.root / "broken.xml.gz"
        broken.write_bytes(b"not gzip")
        with self.assertRaises(LeadError):
            list(iter_bulk([broken]))


if __name__ == "__main__":
    unittest.main()
