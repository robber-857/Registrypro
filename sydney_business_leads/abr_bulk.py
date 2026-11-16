"""Stream public ABN Lookup bulk extracts from local XML, ZIP or XML.GZ files.

The public extract has a Transfer root and ABR child records. Location is the
main business *state and postcode*, not a street address or an exact suburb.
Registered business names (BN) are kept separate from legacy trading names.
Only Python's standard library is required. Archives are never extracted.
"""

from __future__ import annotations

import gzip
import re
import zipfile
from datetime import date
from pathlib import Path
from typing import BinaryIO, Iterator
from xml.etree import ElementTree as ET

from lead_common import LeadError, utc_now


SOURCE = "ABN Lookup Bulk Extract"
SOURCE_URL = "https://abr.business.gov.au/Tools/BulkExtract"


def _tag(element: ET.Element) -> str:
    """Return a local XML name without requiring one particular namespace."""
    return element.tag.rsplit("}", 1)[-1]


def _child(element: ET.Element | None, name: str) -> ET.Element | None:
    if element is not None:
        for child in element:
            if _tag(child) == name:
                return child
    return None


def _at(element: ET.Element | None, *names: str) -> ET.Element | None:
    for name in names:
        element = _child(element, name)
    return element


def _text(element: ET.Element | None) -> str:
    return (element.text or "").strip() if element is not None else ""


def _date(value: str) -> str:
    value = value.strip()
    if re.fullmatch(r"\d{8}", value):
        try:
            return date(int(value[:4]), int(value[4:6]), int(value[6:])).isoformat()
        except ValueError:
            pass
    # Do not fabricate dates if upstream uses an unknown or placeholder format.
    return value


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _record(element: ET.Element, source_file: str, retrieved_at: str) -> dict:
    abn_element = _child(element, "ABN")
    abn = _text(abn_element)
    if not re.fullmatch(r"\d{11}", abn):
        raise LeadError(f"Invalid 11-digit ABN in bulk record: {source_file}")
    assert abn_element is not None
    status_raw = abn_element.get("status", "").strip()
    if not status_raw:
        raise LeadError(f"Missing ABN status for {abn}: {source_file}")
    status = {"ACT": "Active", "CAN": "Cancelled"}.get(status_raw.upper(), status_raw)

    entity_type_code = _text(_at(element, "EntityType", "EntityTypeInd"))
    entity_type = _text(_at(element, "EntityType", "EntityTypeText"))
    main_entity = _child(element, "MainEntity")
    legal_entity = _child(element, "LegalEntity")
    # Individual records use LegalEntity; all other entity types use MainEntity.
    owner = legal_entity if entity_type_code == "IND" else main_entity
    if owner is None:
        owner = main_entity if main_entity is not None else legal_entity

    legal_name = _text(_at(main_entity, "NonIndividualName", "NonIndividualNameText"))
    if not legal_name and legal_entity is not None:
        individual_name = _child(legal_entity, "IndividualName")
        if individual_name is not None:
            given_names = [_text(item) for item in individual_name if _tag(item) == "GivenName"]
            family_name = _text(_child(individual_name, "FamilyName"))
            legal_name = " ".join(value for value in given_names + [family_name] if value)

    address = _at(owner, "BusinessAddress", "AddressDetails")
    state = _text(_child(address, "State")).upper()
    postcode = _text(_child(address, "Postcode"))
    business_names: list[str] = []
    trading_names: list[str] = []
    for other_entity in element:
        if _tag(other_entity) != "OtherEntity":
            continue
        for name in other_entity:
            if _tag(name) != "NonIndividualName":
                continue
            name_type = name.get("type", "").upper().strip()
            name_text = _text(_child(name, "NonIndividualNameText"))
            if name_type == "BN":
                business_names.append(name_text)
            elif name_type in {"TRD", "OTN"}:
                trading_names.append(name_text)

    gst = _child(element, "GST")
    gst_status = gst.get("status", "").upper() if gst is not None else ""
    gst_registered = {"ACT": "Yes", "CAN": "No", "NON": "No"}.get(gst_status, "Unknown")

    return {
        "abn": abn,
        "legal_name": legal_name,
        "business_names": _unique(business_names),
        "historical_trading_names": _unique(trading_names),
        "entity_type_code": entity_type_code,
        "entity_type": entity_type,
        "status": status,
        "status_from": _date(abn_element.get("ABNStatusFromDate", "")),
        "gst_registered": gst_registered,
        "state": state,
        "postcode": postcode,
        # The official ASICNumber field may be ACN, ARBN, ARSN or ARFN.
        "acn": _text(_child(element, "ASICNumber")),
        "source": SOURCE,
        "source_updated_at": _date(element.get("recordLastUpdatedDate", "")),
        "retrieved_at": retrieved_at,
        "source_file": source_file,
    }


def _xml_records(stream: BinaryIO, source_file: str, retrieved_at: str) -> Iterator[dict]:
    """Keep only one complete ABR record in memory while validating the file."""
    root: ET.Element | None = None
    stack: list[ET.Element] = []
    seen = 0
    try:
        for event, element in ET.iterparse(stream, events=("start", "end")):
            if event == "start":
                if root is None:
                    root = element
                    if _tag(root) != "Transfer":
                        raise LeadError(
                            f"Not an ABN Lookup bulk XML file (expected Transfer root): {source_file}"
                        )
                stack.append(element)
                continue

            if _tag(element) == "ABR":
                if len(stack) != 2 or stack[0] is not root:
                    raise LeadError(f"Unexpected nested ABR record: {source_file}")
                row = _record(element, source_file, retrieved_at)
                seen += 1
                # Remove, rather than only clear, so millions of empty elements
                # cannot accumulate under the document root.
                assert root is not None
                root.remove(element)
                element.clear()
                stack.pop()
                yield row
                continue

            if len(stack) == 2 and stack[0] is root:
                # Discard any top-level header/trailer after it is complete.
                assert root is not None
                root.remove(element)
                element.clear()
            stack.pop()
        if root is None or seen == 0:
            raise LeadError(f"No ABR records found in bulk XML file: {source_file}")
    except ET.ParseError as exc:
        raise LeadError(f"Malformed bulk XML in {source_file}: {exc}") from exc


def _supported(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith(".xml") or name.endswith(".xml.gz") or name.endswith(".zip")


def _files(inputs: list[Path]) -> Iterator[Path]:
    seen: set[Path] = set()
    if not inputs:
        raise LeadError("Supply one or more local bulk XML, XML.GZ or ZIP files/directories.")
    for raw_path in inputs:
        path = Path(raw_path).expanduser()
        if not path.exists():
            raise LeadError(f"Bulk input does not exist: {path}")
        if path.is_dir():
            files = sorted(item for item in path.rglob("*") if item.is_file() and _supported(item))
            if not files:
                raise LeadError(f"No XML, XML.GZ or ZIP bulk files found in: {path}")
        else:
            if not path.is_file() or not _supported(path):
                raise LeadError(f"Unsupported bulk input (expected XML, XML.GZ or ZIP): {path}")
            files = [path]
        for item in files:
            resolved = item.resolve()
            if resolved not in seen:
                seen.add(resolved)
                yield item


def iter_bulk(inputs: list[Path]) -> Iterator[dict]:
    """Yield normalised records; malformed, unsupported and empty inputs fail.

    All ABNs are yielded; the caller applies state, postcode, status and entity
    filters, and owns ABN deduplication across files. ZIP members are read through
    ZipFile.open() and are never written to disk, including nested member paths.
    A corrupt tail can fail after earlier records were yielded; callers should
    write transactionally or clearly mark the result as incomplete on failure.
    """
    retrieved_at = utc_now()
    for path in _files(inputs):
        try:
            if path.name.lower().endswith(".zip"):
                with zipfile.ZipFile(path) as archive:
                    members = [member for member in archive.infolist()
                               if not member.is_dir() and member.filename.lower().endswith(".xml")]
                    if not members:
                        raise LeadError(f"ZIP contains no XML files: {path}")
                    for member in members:
                        source_file = f"{path}::{member.filename}"
                        with archive.open(member, "r") as stream:
                            yield from _xml_records(stream, source_file, retrieved_at)
            elif path.name.lower().endswith(".xml.gz"):
                with gzip.open(path, "rb") as stream:
                    yield from _xml_records(stream, str(path), retrieved_at)
            else:
                with path.open("rb") as stream:
                    yield from _xml_records(stream, str(path), retrieved_at)
        except LeadError:
            raise
        except (OSError, EOFError, zipfile.BadZipFile, RuntimeError) as exc:
            raise LeadError(f"Cannot read bulk input {path}: {exc}") from exc
