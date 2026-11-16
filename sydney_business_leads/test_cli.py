"""End-to-end checks for filters, reporting, deduplication, and partial outputs."""

import contextlib
import csv
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sydney_leads as cli
from abr_bulk import iter_bulk

FIXTURE = Path(__file__).parent / "fixtures" / "demo_bulk.xml"


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.out = self.root / "result.csv"

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, arguments):
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            return cli.main(arguments)

    def rows(self, path=None):
        with (path or self.out).open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def bulk(self, *extras):
        return self.run_cli(["bulk", "--input", str(FIXTURE), "--postcodes", "2000", "2067", "2150",
                             "--out", str(self.out), *extras])

    def test_default_active_companies_and_multiple_business_names(self):
        self.assertEqual(self.bulk(), 0)
        rows = self.rows()
        self.assertEqual(len(rows), 3)
        self.assertEqual({row["abn"] for row in rows}, {"11111111111", "55555555555"})
        self.assertTrue(all(row["street_address"] == "" for row in rows))
        self.assertFalse(any("Old Harbour" in row["display_name"] for row in rows))
        self.assertIn("asic_number", rows[0])
        meta = json.loads(self.out.with_suffix(".meta.json").read_text())
        self.assertEqual(meta["entities_exported"], 2)
        self.assertFalse(meta["snapshot_completeness_verified"])

    def test_all_entities_includes_sole_trader(self):
        self.assertEqual(self.bulk("--entity-types", "ALL"), 0)
        self.assertEqual(len(self.rows()), 4)
        self.assertIn("IND", {row["entity_type_code"] for row in self.rows()})

    def test_suburb_label_is_requested_scope_not_verified_address(self):
        self.assertEqual(self.run_cli(["bulk", "--input", str(FIXTURE), "--suburbs", "Haymarket",
                                       "--out", str(self.out)]), 0)
        self.assertEqual({row["requested_suburbs"] for row in self.rows()}, {"Haymarket"})
        self.assertTrue(all(row["location_precision"] == "main_business_state_and_postcode_only"
                            for row in self.rows()))

    def test_limit_counts_entities_not_name_rows_and_is_reported(self):
        self.assertEqual(self.bulk("--limit", "1"), 0)
        self.assertEqual(len(self.rows()), 2)
        meta = json.loads(self.out.with_suffix(".meta.json").read_text())
        self.assertEqual(meta["status"], "limited")
        self.assertTrue(meta["stopped_at_limit"])

    def test_corrupt_tail_retains_only_marked_partial_result(self):
        broken = self.root / "broken.xml"
        broken.write_bytes(FIXTURE.read_bytes().replace(b"</Transfer>", b"<broken>"))
        code = self.run_cli(["bulk", "--input", str(broken), "--postcodes", "2000", "--out", str(self.out)])
        self.assertEqual(code, 2)
        self.assertFalse(self.out.exists())
        self.assertTrue(self.root.joinpath("result.partial.csv").exists())
        meta = json.loads(self.root.joinpath("result.partial.meta.json").read_text())
        self.assertEqual(meta["status"], "failed")

    def test_unknown_suburb_and_existing_output_do_not_overwrite(self):
        self.out.write_text("previous", encoding="utf-8")
        self.assertEqual(self.bulk(), 2)
        self.assertEqual(self.out.read_text(), "previous")
        self.assertEqual(self.run_cli(["bulk", "--input", str(FIXTURE), "--suburbs", "UnknownVillage",
                                      "--out", str(self.root / "new.csv")]), 2)

    def test_demo_is_labelled_and_has_no_fake_research_links(self):
        self.assertEqual(self.run_cli(["demo", "--out", str(self.out)]), 0)
        self.assertTrue(all(row["data_source"] == "SYNTHETIC_DEMO_ONLY" for row in self.rows()))
        self.assertTrue(all(row["abn_lookup_url"] == "" for row in self.rows()))
        self.assertTrue(json.loads(self.out.with_suffix(".meta.json").read_text())["sample_data"])

    def test_formula_and_legal_fallback(self):
        record = {"abn": "66666666666", "legal_name": "=DEMO FORMULA", "business_names": [],
                  "postcode": "2000", "state": "NSW"}
        row = cli.entity_rows(record, {})[0]
        self.assertEqual(row["name_type"], "legal_name_fallback")
        self.assertEqual(row["business_name"], "")
        self.assertEqual(cli.csv_safe(row["display_name"]), "'=DEMO FORMULA")

    def test_explicit_demo_suburb_replaces_demo_default_scope(self):
        self.assertEqual(self.run_cli(["demo", "--suburbs", "Burwood", "--out", str(self.out)]), 0)
        self.assertEqual(self.rows(), [])
        meta = json.loads(self.out.with_suffix(".meta.json").read_text())
        self.assertEqual(meta["postcodes"], ["2134"])

    def test_conflicting_duplicate_business_names_fail_instead_of_merge(self):
        other = self.root / "other_snapshot.xml"
        other.write_bytes(FIXTURE.read_bytes().replace(b"DEMO Harbour Studio", b"DEMO Later Studio"))
        self.assertEqual(self.run_cli(["bulk", "--input", str(FIXTURE), str(other),
                                      "--postcodes", "2000", "--out", str(self.out)]), 2)
        self.assertFalse(self.out.exists())
        meta = json.loads(self.root.joinpath("result.partial.meta.json").read_text())
        self.assertEqual(meta["status"], "failed")
        self.assertIn("11111111111", meta["error"])

    def test_api_pipeline_checks_detail_location_and_deduplicates(self):
        source = {row["abn"]: row for row in iter_bulk([FIXTURE])}

        class FakeClient:
            def __init__(self, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def search_postcode(self, postcode):
                return {"abns": ["11111111111", "44444444444", "33333333333"], "declared_count": 3}

            def lookup(self, abn):
                return source[abn]

        with patch.object(cli, "ApiClient", FakeClient), patch.dict(os.environ, {"ABR_GUID": "demo-only"}):
            self.assertEqual(self.run_cli(["api", "--postcodes", "2000", "2067", "--out", str(self.out)]), 0)
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual({row["abn"] for row in self.rows()}, {"11111111111"})
        meta = json.loads(self.out.with_suffix(".meta.json").read_text())
        self.assertTrue(meta["all_returned_api_abns_processed"])


if __name__ == "__main__":
    unittest.main()
