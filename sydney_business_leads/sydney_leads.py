#!/usr/bin/env python3
"""Export active ABN holders by postcode. Python 3.10+, standard library only.

Use `python sydney_leads.py --help` and read README_zh.md before a large run.
The public register identifies a main-business postcode, not a storefront.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlencode

from abr_api import ApiClient
from abr_bulk import iter_bulk
from lead_common import LeadError, utc_now

VERSION = "1.0.0"
HERE = Path(__file__).resolve().parent
STATES = ("NSW", "VIC", "QLD", "ACT", "SA", "WA", "TAS", "NT")
# These are delivery-area lookup conveniences, never inferred entity suburbs.
# Official lookup pages and verification date are in suburbs_example.csv.
BUILTIN_SUBURBS = {
    "sydney": ("Sydney", "NSW", "2000"),
    "haymarket": ("Haymarket", "NSW", "2000"),
    "chatswood": ("Chatswood", "NSW", "2067"),
    "chatswood west": ("Chatswood West", "NSW", "2067"),
    "burwood": ("Burwood", "NSW", "2134"),
    "burwood north": ("Burwood North", "NSW", "2134"),
    "parramatta": ("Parramatta", "NSW", "2150"),
    "harris park": ("Harris Park", "NSW", "2150"),
}

FIELDNAMES = [
    "display_name", "business_name", "legal_name", "name_type", "abn", "asic_number",
    "entity_type_code", "entity_type", "abn_status", "abn_status_from",
    "gst_registered", "state", "postcode", "requested_suburbs",
    "location_precision", "street_address", "website", "public_phone",
    "google_search_url", "google_maps_search_url", "abn_lookup_url",
    "data_source", "source_updated_at", "retrieved_at", "source_file",
]


def note(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def split_tokens(values: Iterable[str] | None) -> list[str]:
    return [part.strip() for value in (values or [])
            for part in value.split(",") if part.strip()]


def valid_postcode(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"[0-9]{4}", value):
        raise LeadError(f"邮编必须是四位数字，收到：{value!r}。例如 2000、2150。")
    return value


def resolve_locations(args: argparse.Namespace) -> tuple[set[str], dict[str, list[str]]]:
    postcodes = {valid_postcode(value) for value in split_tokens(args.postcodes)}
    requested: dict[str, list[str]] = {}
    if args.postcodes_file:
        for line in args.postcodes_file.read_text(encoding="utf-8-sig").splitlines():
            line = line.split("#", 1)[0].strip()
            for value in re.split(r"[,\s]+", line) if line else []:
                postcodes.add(valid_postcode(value))

    mapping: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for display, state, postcode in BUILTIN_SUBURBS.values():
        mapping.setdefault((display.casefold(), state), []).append((display, postcode))
    if args.suburb_map:
        with args.suburb_map.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not {"suburb", "state", "postcode"}.issubset(reader.fieldnames or []):
                raise LeadError("suburb map CSV 必须包含 suburb,state,postcode 列。")
            custom: dict[tuple[str, str], list[tuple[str, str]]] = {}
            for number, row in enumerate(reader, start=2):
                display = (row.get("suburb") or "").strip()
                state = (row.get("state") or "").strip().upper()
                if not display or state not in STATES:
                    raise LeadError(f"suburb map 第 {number} 行的 suburb/state 无效。")
                postcode = valid_postcode(row.get("postcode") or "")
                custom.setdefault((display.casefold(), state), []).append((display, postcode))
            mapping.update(custom)

    for query in split_tokens(args.suburbs):
        key = (" ".join(query.split()).casefold(), args.state)
        if key not in mapping:
            raise LeadError(
                f"尚未配置 {query!r} ({args.state})。请使用 --postcodes，"
                "或用 --suburb-map 提供该地区的邮编 CSV；脚本不会猜测邮编。"
            )
        for display, postcode in mapping[key]:
            postcodes.add(postcode)
            requested.setdefault(postcode, [])
            if display not in requested[postcode]:
                requested[postcode].append(display)
    if not postcodes:
        raise LeadError("请指定 --postcodes、--postcodes-file 或 --suburbs。")
    return postcodes, requested


def parse_entity_types(values: Iterable[str]) -> set[str] | None:
    codes = {value.upper() for value in split_tokens(values)}
    if codes == {"ALL"}:
        return None
    if not codes or any(not re.fullmatch(r"[A-Z]{3}", code) or code == "ALL"
                        for code in codes):
        raise LeadError("--entity-types 使用三个字母的代码，例如 PRV PUB，或单独使用 ALL。")
    return codes


def csv_safe(value: Any) -> str:
    """Treat external names as text when a CSV is opened in a spreadsheet."""
    text = "" if value is None else str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text


def distinct_names(names: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result = []
    for name in names:
        name = " ".join(str(name).split())
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            result.append(name)
    return result


def entity_rows(record: dict[str, Any], requested: dict[str, list[str]]) -> list[dict[str, str]]:
    legal = record.get("legal_name", "")
    names = distinct_names(record.get("business_names") or [])
    chosen = [(name, name, "registered_business_name") for name in names]
    if not chosen and legal:
        chosen = [(legal, "", "legal_name_fallback")]
    abn = record.get("abn", "")
    postcode = record.get("postcode", "")
    state = record.get("state", "")
    rows = []
    for display, business, kind in chosen:
        query = f'"{display}" {state} {postcode} Australia'
        row = {
            "display_name": display, "business_name": business, "legal_name": legal,
            "name_type": kind, "abn": abn, "asic_number": record.get("acn", ""),
            "entity_type_code": record.get("entity_type_code", ""),
            "entity_type": record.get("entity_type", ""),
            "abn_status": record.get("status", ""),
            "abn_status_from": record.get("status_from", ""),
            "gst_registered": record.get("gst_registered", "Unknown"),
            "state": state, "postcode": postcode,
            "requested_suburbs": " | ".join(requested.get(postcode, [])),
            "location_precision": "main_business_state_and_postcode_only",
            # Intentionally blank: not part of the public ABN source.
            "street_address": "", "website": "", "public_phone": "",
            "google_search_url": "https://www.google.com/search?" + urlencode({"q": query}),
            "google_maps_search_url": "https://www.google.com/maps/search/?" + urlencode(
                {"api": "1", "query": f"{display} {state} {postcode} Australia"}),
            "abn_lookup_url": "https://abr.business.gov.au/ABN/View?" + urlencode({"abn": abn}),
            "data_source": record.get("source", ""),
            "source_updated_at": record.get("source_updated_at", ""),
            "retrieved_at": record.get("retrieved_at", ""),
            "source_file": record.get("source_file", ""),
        }
        rows.append(row)
    return rows


class Export:
    """Write atomically on success; visibly mark retained partial work on failure."""

    def __init__(self, out: Path, overwrite: bool):
        self.out = out.resolve()
        if self.out.suffix.lower() != ".csv":
            raise LeadError("--out 必须以 .csv 结尾。")
        self.meta_path = self.out.with_suffix(".meta.json")
        if not overwrite and (self.out.exists() or self.meta_path.exists()):
            raise LeadError(f"输出已存在：{self.out}。请更换文件名或加 --overwrite。")
        self.out.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8-sig", newline="",
                                             prefix=f".{self.out.stem}_", suffix=".csv",
                                             dir=self.out.parent, delete=False)
        self.handle = handle
        self.temp = Path(handle.name)
        self.writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        self.writer.writeheader()
        self.rows = 0

    def write(self, row: dict[str, str]) -> None:
        self.writer.writerow({key: csv_safe(value) for key, value in row.items()})
        self.rows += 1

    def finish(self, meta: dict[str, Any], success: bool) -> Path:
        self.handle.close()
        destination = self.out if success else self.out.with_name(self.out.stem + ".partial.csv")
        os.replace(self.temp, destination)
        meta["output_file"] = str(destination)
        meta["name_rows"] = self.rows
        meta["finished_at"] = utc_now()
        meta_path = self.meta_path if success else self.out.with_name(self.out.stem + ".partial.meta.json")
        # Same-directory replacement keeps metadata readers from seeing half a JSON document.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.out.parent,
                                         suffix=".json", delete=False) as temporary:
            json.dump(meta, temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
            temp_meta = Path(temporary.name)
        os.replace(temp_meta, meta_path)
        return destination


def api_records(args: argparse.Namespace, postcodes: set[str], meta: dict[str, Any]):
    guid = os.environ.get("ABR_GUID", "").strip()
    if not guid:
        raise LeadError(
            "尚未设置 ABR_GUID。请在官方 Web services 页面免费申请 GUID，"
            "然后设置环境变量；或者使用不需要 GUID 的 bulk 模式。"
        )
    seen: set[str] = set()
    with ApiClient(guid=guid, cache_path=args.cache, min_interval=args.interval,
                   timeout=args.timeout, cache_days=args.cache_days, refresh=args.refresh) as client:
        for postcode in sorted(postcodes):
            listing = client.search_postcode(postcode)
            count = listing["declared_count"]
            meta["postcode_requests"].append({
                "postcode": postcode, "declared_count": count,
                "register_last_updated": listing.get("register_last_updated", ""),
                "retrieved_at": listing.get("retrieved_at", ""),
                "cached": listing.get("cached", False),
            })
            note(f"{args.state} {postcode}：API 返回 {count:,} 个 active ABN，正在获取详情……")
            for abn in listing["abns"]:
                if abn in seen:
                    continue
                seen.add(abn)
                yield client.lookup(abn)


def run_export(args: argparse.Namespace) -> int:
    postcodes, requested = resolve_locations(args)
    entity_types = parse_entity_types(args.entity_types)
    keywords = [value.casefold() for value in split_tokens(args.name_contains)]
    is_demo = args.command == "demo"
    meta: dict[str, Any] = {
        "script_version": VERSION, "mode": args.command, "started_at": utc_now(),
        "state": args.state, "postcodes": sorted(postcodes), "requested_suburbs": requested,
        "entity_types": sorted(entity_types) if entity_types else "ALL",
        "name_contains_any": keywords, "limit_entities": args.limit,
        "status": "running", "sample_data": is_demo,
        "location_precision": "state_and_postcode_only; suburb_and_LGA_membership_not_verified",
        "coverage": "active_ABN_holders_with_visible_matching_fields; not_an_ASIC_company_census",
        "snapshot_completeness_verified": False,
        "postcode_requests": [], "all_returned_api_abns_processed": False,
        "api_cache_policy": ({"detail_days": args.cache_days, "postcode_seconds_max": 3600,
                              "refresh": args.refresh} if args.command == "api" else None),
        "notes": [
            "No street address, website, phone, or industry is supplied by this source.",
            "Search URLs are research shortcuts, not confirmed websites or matched premises.",
            "ABN Active does not prove the business is currently trading or a shop is open.",
            "abn_status_from is the start of the current ABN status, not company incorporation.",
            "One entity can produce multiple registered business name rows.",
        ],
    }
    if is_demo:
        inputs = [HERE / "fixtures" / "demo_bulk.xml"]
    elif args.command == "bulk":
        inputs = args.inputs
    else:
        inputs = []
    if inputs:
        meta["input_paths"] = [str(path.resolve()) for path in inputs]
        meta["notes"].append("All supplied XMLs are scanned unless limited; official distribution parts and snapshot consistency must be checked by the operator.")
    export = Export(args.out, args.overwrite)
    counts: Counter[str] = Counter()
    exported: set[str] = set()
    exported_fingerprints: dict[str, str] = {}
    seen_rows: set[tuple[str, str]] = set()
    limited = False
    success = False
    error: BaseException | None = None
    records = None
    note("筛选主营业地点邮编：" + ", ".join(sorted(postcodes)) + "；州：" + args.state)
    if requested:
        note("提示：suburb 仅用于选择邮编；同邮编其他 suburb 的企业也会进入候选名单。")
    if is_demo:
        note("DEMO：以下数据全部为测试用虚构记录，不是实际客户名单。")
    try:
        records = (api_records(args, postcodes, meta) if args.command == "api" else iter_bulk(inputs))
        for record in records:
            counts["records_read"] += 1
            if counts["records_read"] % 100000 == 0:
                note(f"已扫描 {counts['records_read']:,} 条记录，匹配 {len(exported):,} 个主体。")
            if record.get("status", "").casefold() != "active":
                counts["inactive_or_unknown_status"] += 1
                continue
            if record.get("state", "").upper() != args.state or record.get("postcode") not in postcodes:
                counts["outside_or_missing_location"] += 1
                continue
            if entity_types is not None and record.get("entity_type_code", "").upper() not in entity_types:
                counts["entity_type_filtered"] += 1
                continue
            text = " ".join([record.get("legal_name") or ""] + (record.get("business_names") or []))
            if keywords and not any(keyword in text.casefold() for keyword in keywords):
                counts["name_keyword_filtered"] += 1
                continue
            rows = entity_rows(record, requested)
            if not rows:
                counts["missing_visible_name"] += 1
                continue
            abn = record["abn"]
            fingerprint = json.dumps({
                "legal_name": record.get("legal_name", ""),
                "business_names": sorted(distinct_names(record.get("business_names") or [])),
                "state": record.get("state", ""), "postcode": record.get("postcode", ""),
                "entity_type_code": record.get("entity_type_code", ""),
                "status": record.get("status", ""), "acn": record.get("acn", ""),
            }, ensure_ascii=False, sort_keys=True)
            if abn in exported:
                counts["repeated_abn_records"] += 1
                if exported_fingerprints[abn] != fingerprint:
                    raise LeadError(f"ABN {abn} 的重复记录内容冲突。请勿混用不同周的 bulk 快照；本次输出将标记为部分结果。")
            exported_fingerprints[abn] = fingerprint
            exported.add(abn)
            for row in rows:
                key = (abn, row["display_name"].casefold())
                if key not in seen_rows:
                    if is_demo:
                        row["data_source"] = "SYNTHETIC_DEMO_ONLY"
                        # Do not create actionable links for invented businesses/identifiers.
                        row["google_search_url"] = ""
                        row["google_maps_search_url"] = ""
                        row["abn_lookup_url"] = ""
                    export.write(row)
                    seen_rows.add(key)
            if len(exported) % 100 == 0 and len(exported) > 0:
                if counts.get("last_reported", -1) != len(exported):
                    note(f"已筛选 {len(exported):,} 个主体，输出 {export.rows:,} 行名称。")
                    counts["last_reported"] = len(exported)
            if args.limit and len(exported) >= args.limit:
                limited = True
                break
        success = True
        meta["status"] = "limited" if limited else "finished"
        meta["stopped_at_limit"] = limited
        meta["all_returned_api_abns_processed"] = args.command == "api" and not limited
    except (Exception, KeyboardInterrupt) as exc:
        error = exc
        meta["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        meta["error"] = "用户中断运行。" if isinstance(exc, KeyboardInterrupt) else str(exc)
    finally:
        if records is not None and hasattr(records, "close"):
            records.close()
        counts.pop("last_reported", None)
        meta["counts"] = dict(counts)
        meta["entities_exported"] = len(exported)
        destination = export.finish(meta, success=success)
    if error:
        note(f"未完整完成：{meta['error']}\n已保留明确标记的部分结果：{destination}")
        return 130 if isinstance(error, KeyboardInterrupt) else 2
    note(f"完成：{len(exported):,} 个 ABN 主体 / {export.rows:,} 行名称。文件：{destination}")
    if limited:
        note("已达到 --limit；这是预览/部分名单，并非该地区的全部结果。")
    if args.command == "bulk":
        note("本次仅覆盖提供的文件；请确保下载同一批次的所有官方数据分卷。")
    return 0


def common_arguments(parser: argparse.ArgumentParser, demo: bool = False) -> None:
    parser.add_argument("--postcodes", nargs="+", help="邮编，支持空格或逗号分隔，例如 2000 2150")
    parser.add_argument("--postcodes-file", type=Path, help="纯文本邮编清单，可逐行填写")
    parser.add_argument("--suburbs", nargs="+", help='已配置 suburb，例如 Chatswood Burwood；含空格需加引号')
    parser.add_argument("--suburb-map", type=Path, help="扩充/覆盖 suburb 对照表 CSV：suburb,state,postcode")
    parser.add_argument("--state", choices=STATES, default="NSW", help="州筛选，默认 NSW")
    parser.add_argument("--entity-types", nargs="+", default=["PRV", "PUB"],
                        help="默认 PRV PUB；ALL 包含 sole trader、partnership、trust 等所有类型")
    parser.add_argument("--name-contains", nargs="+", help="名称包含任一关键词；仅名称匹配，不代表行业核验")
    parser.add_argument("--limit", type=int, default=0, help="最多导出多少个匹配 ABN；0 表示不主动限制")
    parser.add_argument("--out", type=Path, default=Path("demo.csv" if demo else "sydney_leads.csv"))
    parser.add_argument("--overwrite", action="store_true", help="替换同名输出 CSV 和 metadata")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="按主营业地点的州和邮编导出澳洲 Active ABN 企业名称。默认 NSW，非完整街道地址。"
    )
    parser.add_argument("--version", action="version", version=VERSION)
    commands = parser.add_subparsers(dest="command", required=True)
    api = commands.add_parser("api", help="通过免费 ABN Lookup API 查询，需要环境变量 ABR_GUID")
    common_arguments(api)
    api.add_argument("--cache", type=Path, default=Path(".cache/abn.sqlite3"))
    api.add_argument("--interval", type=float, default=1.0, help="网络请求之间的秒数，默认 1；不是官方配额")
    api.add_argument("--timeout", type=float, default=45.0, help="单次请求超时秒数")
    api.add_argument("--cache-days", type=float, default=7.0, help="ABN 详情缓存天数，默认 7")
    api.add_argument("--refresh", action="store_true", help="本次忽略缓存，重新获取 API 信息")
    bulk = commands.add_parser("bulk", help="筛选本地官方 XML/ZIP/.xml.gz，无需 GUID")
    common_arguments(bulk)
    bulk.add_argument("--input", dest="inputs", nargs="+", type=Path, required=True,
                      help="一个或多个官方数据文件/目录；目录会递归扫描")
    demo = commands.add_parser("demo", help="仅用虚构数据检查安装与 CSV 格式，不联网")
    common_arguments(demo, demo=True)
    commands.add_parser("sources", help="显示官方 API 注册与批量下载页面")
    commands.add_parser("suburbs", help="显示内置 suburb→postcode 对照（不是全悉尼清单）")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "sources":
        print("API 与免费 GUID： https://abr.business.gov.au/Tools/WebServices")
        print("服务协议： https://abr.business.gov.au/Tools/WebServicesAgreement")
        print("官方批量数据： https://data.gov.au/data/dataset/abn-bulk-extract")
        print("批量字段说明： https://abr.business.gov.au/Tools/BulkExtract")
        print("邮编核对： https://auspost.com.au/postcode")
        print("批量模式：手动下载同一批次的所有数据分卷，然后用 bulk --input 文件夹。")
        return 0
    if args.command == "suburbs":
        print("suburb,state,postcode")
        for row in sorted(BUILTIN_SUBURBS.values()):
            print(",".join(row))
        print("提示：不是全悉尼清单；可传入任意邮编或自备 suburb CSV。", file=sys.stderr)
        return 0
    if args.limit < 0:
        parser.error("--limit 不能小于 0")
    if args.command == "demo" and not any((args.postcodes, args.postcodes_file, args.suburbs)):
        args.postcodes = ["2000", "2067", "2134", "2150"]
    if args.command == "api":
        if args.interval < 0 or args.timeout <= 0 or args.cache_days < 0:
            parser.error("interval/cache-days 不能小于 0，timeout 必须大于 0")
    try:
        return run_export(args)
    except (LeadError, OSError, ValueError) as exc:
        note(f"错误：{exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
