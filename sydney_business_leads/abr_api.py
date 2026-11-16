"""ABN Lookup postcode and entity adapter (Python standard library only).

Contract checked against the official ABN Lookup methods, data dictionary and
ABNLookup_schema.xsd on 2026-11-16. No credentials are bundled with this module.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
import json
from pathlib import Path
import re
import socket
import sqlite3
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from lead_common import LeadError, utc_now


ENDPOINT = "https://abr.business.gov.au/ABRXMLSearch/AbrXmlSearch.asmx"
SOURCE = "ABN Lookup Web Services"
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
MAX_ATTEMPTS = 4


def _redact(text: str, guid: str) -> str:
    return text.replace(guid, "[REDACTED]") if guid else text


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(element: ET.Element | None, path: str = "") -> str:
    if element is None:
        return ""
    target = element.find(path) if path else element
    return (target.text or "").strip() if target is not None else ""


def _is_nil(element: ET.Element) -> bool:
    return any(_local_name(key) == "nil" and value.lower() in ("true", "1")
               for key, value in element.attrib.items())


def _date_value(value: str) -> date | None:
    if not value or value[:10] == "0001-01-01":
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise LeadError("ABN Lookup returned an invalid effective date.") from exc


def _currently_effective(element: ET.Element, today: date) -> bool:
    if _is_nil(element):
        return False
    starts = _date_value(_text(element, "effectiveFrom"))
    ends = _date_value(_text(element, "effectiveTo"))
    return (starts is None or starts <= today) and (ends is None or ends >= today)


def _current_elements(parent: ET.Element, tag: str, today: date) -> list[ET.Element]:
    return [item for item in parent.findall(tag) if _currently_effective(item, today)]


def _latest_current(parent: ET.Element, tag: str, today: date) -> ET.Element | None:
    items = _current_elements(parent, tag, today)
    return max(items, key=lambda item: _text(item, "effectiveFrom"), default=None)


def _unique_names(names: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for name in names:
        name = name.strip()
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            result.append(name)
    return result


def _normalise_abn(value: str) -> str:
    value = re.sub(r"\s+", "", str(value))
    if not re.fullmatch(r"[0-9]{11}", value):
        raise LeadError("An ABN must contain exactly 11 digits.")
    return value


def _response(raw: bytes, guid: str = "", allow_no_records: bool = False
              ) -> tuple[ET.Element, bool]:
    if len(raw) > MAX_RESPONSE_BYTES:
        raise LeadError("ABN Lookup response is too large; use the bulk extract instead.")
    if re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", raw, flags=re.I):
        raise LeadError("Unexpected XML document declarations in the ABN Lookup response.")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise LeadError("ABN Lookup returned invalid XML; no results were accepted.") from exc
    for element in root.iter():
        element.tag = _local_name(element.tag)
    response = root if root.tag == "response" else root.find(".//response")
    if response is None:
        raise LeadError("ABN Lookup response is missing its response element.")
    exception = response.find("exception")
    if exception is not None:
        code = _text(exception, "exceptionCode")
        description = _redact(_text(exception, "exceptionDescription"), guid)
        if allow_no_records and code.upper() == "SEARCH" and description.casefold().strip(" .") == "no records found":
            return response, True
        raise LeadError(f"ABN Lookup {code or 'service error'}: {description or 'No description supplied.'}")
    return response, False


def parse_postcode_response(raw: bytes, guid: str = "") -> dict[str, Any]:
    """Parse the official abnList shape and reject incomplete or unknown payloads."""
    response, no_records = _response(raw, guid, allow_no_records=True)
    result: dict[str, Any] = {
        "abns": [],
        "declared_count": 0,
        "register_last_updated": _text(response, "dateRegisterLastUpdated"),
        "retrieved_at": _text(response, "dateTimeRetrieved") or utc_now(),
        "cached": False,
    }
    if no_records:
        return result
    listing = response.find("abnList")
    if listing is None:
        raise LeadError("ABN Lookup returned no abnList; this is not an empty-postcode result.")
    try:
        count = int(_text(listing, "numberOfRecords"))
    except ValueError as exc:
        raise LeadError("ABN Lookup abnList has no valid numberOfRecords.") from exc
    abns = [_normalise_abn(_text(item)) for item in listing.findall("abn")]
    if count < 0 or count != len(abns) or len(set(abns)) != len(abns):
        raise LeadError(
            f"Incomplete ABN Lookup postcode result: declared {count}, received "
            f"{len(abns)} records ({len(set(abns))} unique). Use the bulk extract or retry."
        )
    result.update(abns=abns, declared_count=count)
    return result


def parse_entity_response(raw: bytes, expected_abn: str, guid: str = "",
                          today: date | None = None) -> dict[str, Any]:
    """Parse a current entity, preserving blank/suppressed values as blanks."""
    expected_abn = _normalise_abn(expected_abn)
    response, _ = _response(raw, guid)
    # The service timestamp carries its Australian calendar date. Using UTC's
    # date at this boundary could exclude a name that became effective today.
    today = today or _date_value(_text(response, "dateTimeRetrieved")) or datetime.now(timezone.utc).date()
    entity = response.find("businessEntity202001")
    if entity is None:
        raise LeadError("ABN Lookup response is missing businessEntity202001.")
    identifiers = [item for item in entity.findall("ABN")
                   if _text(item, "isCurrentIndicator").upper() in ("", "Y")]
    returned_abns = [_normalise_abn(_text(item, "identifierValue")) for item in identifiers]
    if expected_abn not in returned_abns:
        raise LeadError(f"ABN Lookup returned a different or non-current ABN for {expected_abn}.")

    status_node = _latest_current(entity, "entityStatus", today)
    status = _text(status_node, "entityStatusCode")
    if not status:
        raise LeadError(f"ABN Lookup returned no current status for {expected_abn}.")
    main_name = _latest_current(entity, "mainName", today)
    personal_name = _latest_current(entity, "legalName", today)
    legal_name = _text(main_name, "organisationName")
    if not legal_name and personal_name is not None:
        legal_name = " ".join(filter(None, (_text(personal_name, part) for part in
                                          ("givenName", "otherGivenName", "familyName"))))
    business_names = _unique_names([
        _text(item, "organisationName") for item in _current_elements(entity, "businessName", today)
    ])
    old_trading_names = _unique_names([
        _text(item, "organisationName") for tag in ("mainTradingName", "otherTradingName")
        for item in entity.findall(tag) if not _is_nil(item)
    ])
    location = _latest_current(entity, "mainBusinessPhysicalAddress", today)
    entity_type_node = entity.find("entityType")
    gst_nodes = entity.findall("goodsAndServicesTax")
    if any(_currently_effective(item, today) for item in gst_nodes):
        gst_registered = "Yes"
    elif (gst_nodes and all(_is_nil(item) for item in gst_nodes)) or (
        not gst_nodes and not legal_name and not business_names and location is None
    ):
        gst_registered = "Unknown"
    else:
        gst_registered = "No"
    return {
        "abn": expected_abn,
        "legal_name": legal_name,
        "business_names": business_names,
        "historical_trading_names": old_trading_names,
        "entity_type_code": _text(entity_type_node, "entityTypeCode"),
        "entity_type": _text(entity_type_node, "entityDescription"),
        "status": status,
        "status_from": _text(status_node, "effectiveFrom"),
        "gst_registered": gst_registered,
        "state": _text(location, "stateCode").upper(),
        "postcode": _text(location, "postcode"),
        "acn": _text(entity, "ASICNumber"),
        "source": SOURCE,
        "source_updated_at": _text(entity, "recordLastUpdatedDate"),
        "retrieved_at": _text(response, "dateTimeRetrieved") or utc_now(),
        "source_file": "",
    }


class ApiClient:
    """Rate-paced, resumable ABN Lookup client. Cache contains parsed public data only."""

    def __init__(self, guid: str, cache_path: Path, min_interval: float = 1.0,
                 timeout: float = 45, cache_days: float = 7, refresh: bool = False):
        self.guid = str(guid).strip()
        if not self.guid:
            raise LeadError("Set ABR_GUID to your own ABN Lookup web-services GUID.")
        if min_interval < 0 or timeout <= 0 or cache_days < 0:
            raise LeadError("Interval/cache days must be nonnegative and timeout must be positive.")
        self.min_interval = float(min_interval)
        self.timeout = float(timeout)
        self.cache_days = float(cache_days)
        self.refresh = bool(refresh)
        self._last_request: float | None = None
        self.cache_path = Path(cache_path)
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(str(self.cache_path))
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS abr_cache_v1 ("
                "operation TEXT NOT NULL, identifier TEXT NOT NULL, stored_at REAL NOT NULL, "
                "payload TEXT NOT NULL, PRIMARY KEY (operation, identifier))"
            )
            self._db.commit()
        except (OSError, sqlite3.Error) as exc:
            raise LeadError(f"Cannot open ABN Lookup cache: {exc}") from exc

    def __enter__(self) -> "ApiClient":
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()

    def close(self) -> None:
        self._db.close()

    def _cached(self, operation: str, identifier: str, ttl_seconds: float) -> dict[str, Any] | None:
        if self.refresh or ttl_seconds <= 0:
            return None
        try:
            row = self._db.execute(
                "SELECT stored_at, payload FROM abr_cache_v1 WHERE operation=? AND identifier=?",
                (operation, identifier),
            ).fetchone()
        except sqlite3.Error as exc:
            raise LeadError(f"Cannot read ABN Lookup cache: {exc}") from exc
        if row is None or not (0 <= time.time() - row[0] < ttl_seconds):
            return None
        try:
            result = json.loads(row[1])
        except (TypeError, ValueError):
            return None
        if not isinstance(result, dict):
            return None
        if operation == "postcode":
            if not isinstance(result.get("abns"), list) or result.get("declared_count") != len(result["abns"]):
                return None
            if not all(isinstance(abn, str) and re.fullmatch(r"[0-9]{11}", abn) for abn in result["abns"]):
                return None
        elif result.get("abn") != identifier or not isinstance(result.get("business_names"), list):
            return None
        return result

    def _store(self, operation: str, identifier: str, result: dict[str, Any]) -> None:
        try:
            with self._db:
                self._db.execute(
                    "INSERT OR REPLACE INTO abr_cache_v1 (operation, identifier, stored_at, payload) "
                    "VALUES (?, ?, ?, ?)",
                    (operation, identifier, time.time(), json.dumps(result, ensure_ascii=False)),
                )
        except sqlite3.Error as exc:
            raise LeadError(f"Cannot save ABN Lookup cache: {exc}") from exc

    def _pace(self) -> None:
        if self._last_request is not None:
            remaining = self.min_interval - (time.monotonic() - self._last_request)
            if remaining > 0:
                time.sleep(remaining)
        self._last_request = time.monotonic()

    @staticmethod
    def _retry_delay(attempt: int, retry_after: str | None) -> float:
        delay = float(2 ** attempt)
        if retry_after:
            try:
                delay = max(delay, float(retry_after))
            except ValueError:
                try:
                    retry_time = parsedate_to_datetime(retry_after)
                    if retry_time.tzinfo is None:
                        retry_time = retry_time.replace(tzinfo=timezone.utc)
                    delay = max(delay, retry_time.timestamp() - time.time())
                except (TypeError, ValueError, OverflowError):
                    pass
        if delay > 60:
            raise LeadError("ABN Lookup requested a retry later than 60 seconds; rerun after the service cooldown.")
        return max(0.0, delay)

    def _post(self, method: str, parameters: dict[str, str]) -> bytes:
        body = urlencode({**parameters, "authenticationGuid": self.guid}).encode("ascii")
        request = Request(
            f"{ENDPOINT}/{method}", data=body, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Accept": "application/xml, text/xml", "User-Agent": "SydneyBusinessLeads/1.0"},
        )
        for attempt in range(MAX_ATTEMPTS):
            self._pace()
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise LeadError("ABN Lookup response exceeded the size limit; use the bulk extract.")
                return raw
            except HTTPError as exc:
                retryable = exc.code == 429 or 500 <= exc.code < 600
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                exc.close()
                if not retryable or attempt == MAX_ATTEMPTS - 1:
                    raise LeadError(f"ABN Lookup {method} failed with HTTP {exc.code}.") from None
                time.sleep(self._retry_delay(attempt, retry_after))
            except (URLError, TimeoutError, socket.timeout, ConnectionError) as exc:
                if attempt == MAX_ATTEMPTS - 1:
                    detail = _redact(str(getattr(exc, "reason", exc)), self.guid)
                    raise LeadError(f"ABN Lookup {method} failed after {MAX_ATTEMPTS} attempts: {detail}") from None
                time.sleep(self._retry_delay(attempt, None))
        raise LeadError("ABN Lookup request did not complete.")

    def search_postcode(self, postcode: str) -> dict[str, Any]:
        postcode = str(postcode).strip()
        if not re.fullmatch(r"[0-9]{4}", postcode):
            raise LeadError("A postcode must contain exactly four digits.")
        ttl = min(3600.0, self.cache_days * 86400.0)
        cached = self._cached("postcode", postcode, ttl)
        if cached is not None:
            cached["cached"] = True
            return cached
        raw = self._post("SearchByPostcode", {"postcode": postcode})
        result = parse_postcode_response(raw, self.guid)
        self._store("postcode", postcode, result)
        return result

    def lookup(self, abn: str) -> dict[str, Any]:
        abn = _normalise_abn(abn)
        cached = self._cached("entity202001", abn, self.cache_days * 86400.0)
        if cached is not None:
            return cached
        raw = self._post("SearchByABNv202001", {
            "searchString": abn, "includeHistoricalDetails": "N",
        })
        result = parse_entity_response(raw, abn, self.guid)
        self._store("entity202001", abn, result)
        return result
