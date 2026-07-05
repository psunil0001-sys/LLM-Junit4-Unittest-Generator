# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Smoke tests for path-based Kover XML discovery.
"""Smoke tests for discover_kover_xml_groups."""

from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path

from UnitTest_gen.helper.dashboard.blocked_coverage_kover_report import (
    _opportunity_report_item,
    discover_kover_xml_groups,
    module_name_from_kover_xml,
    prepare_html_report_dir,
    variant_name_from_xml_path,
)
from UnitTest_gen.kotlin.incremental_coverage import CoverageOpportunity
from UnitTest_gen.kotlin.attempted_failed_report import entries_for_variant

REPO_ROOT = Path(__file__).resolve().parents[3]
MINIMAL_XML = '<?xml version="1.0"?><report name="test"><counter type="LINE" missed="1" covered="0"/></report>'


class KoverXmlDiscoveryTest(unittest.TestCase):
    def test_blocked_dashboard_item_keeps_specific_action_and_provenance(self):
        opportunity = CoverageOpportunity(
            name="callback",
            bucket="blocked",
            fixture="callback_owner",
            entry_points=["publicEntry"],
            lines=[12],
            branches=[],
            reason="The callback owner is internal.",
            action="Inject CallbackOwner into the constructor.",
            blocked_reason="needs_callback_seam: callback cannot be fired",
        )
        item = _opportunity_report_item(opportunity, "blocked")
        self.assertEqual("The callback owner is internal.", item["why"])
        self.assertEqual("Inject CallbackOwner into the constructor.", item["fix"])
        self.assertIn("Kover reports this gap at lines 12", item["evidence"])
        self.assertEqual("Kover + Tree-sitter/static planner", item["provenance"])

    def test_module_name_comes_from_xml_path_even_without_gap_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            xml = root / "firebasehelper" / "build" / "reports" / "kover" / "report.xml"
            xml.parent.mkdir(parents=True)
            xml.write_text(MINIMAL_XML, encoding="utf-8")
            self.assertEqual("firebasehelper", module_name_from_kover_xml(root, xml))

    def test_prepare_html_report_dir_clears_stale_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "htmlreport"
            out_dir.mkdir()
            (out_dir / "stale.html").write_text("old", encoding="utf-8")
            prepare_html_report_dir(out_dir)
            self.assertTrue(out_dir.is_dir())
            self.assertEqual([], list(out_dir.iterdir()))

    def test_variant_name_from_report_xml_is_default(self):
        self.assertEqual("default", variant_name_from_xml_path(Path("report.xml")))
        self.assertEqual("ProdGlobalRelease", variant_name_from_xml_path(Path("reportProdGlobalRelease.xml")))

    @unittest.skipUnless(
        (REPO_ROOT / "common/build/reports/kover/report.xml").is_file(),
        "Kover XML not present",
    )
    def test_discover_groups_finds_repo_xmls(self):
        groups = discover_kover_xml_groups(REPO_ROOT)
        self.assertIn("default", groups)
        self.assertGreaterEqual(len(groups["default"]), 1)

    def test_two_modules_report_xml_one_default_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for module in ("common", "app"):
                kover_dir = root / module / "build" / "reports" / "kover"
                kover_dir.mkdir(parents=True)
                (kover_dir / "report.xml").write_text(MINIMAL_XML, encoding="utf-8")
            groups = discover_kover_xml_groups(root)
            self.assertEqual(["default"], list(groups))
            self.assertEqual(2, len(groups["default"]))

    def test_mixed_variant_suffixes_two_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            common_dir = root / "common" / "build" / "reports" / "kover"
            app_dir = root / "app" / "build" / "reports" / "kover"
            common_dir.mkdir(parents=True)
            app_dir.mkdir(parents=True)
            (common_dir / "report.xml").write_text(MINIMAL_XML, encoding="utf-8")
            (app_dir / "reportProdChinaRelease.xml").write_text(MINIMAL_XML, encoding="utf-8")
            groups = discover_kover_xml_groups(root)
            self.assertEqual({"default", "ProdChinaRelease"}, set(groups))
            self.assertEqual(1, len(groups["default"]))
            self.assertEqual(1, len(groups["ProdChinaRelease"]))

    def test_default_dashboard_reads_blocked_entries_from_all_variants(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "UnitTest_gen" / "data" / "blocked_report.json"
            report.parent.mkdir(parents=True)
            report.write_text(
                json.dumps(
                    {
                        "entries": [
                            {"variant": "ProdGlobalRelease", "fingerprint": "global"},
                            {"variant": "ProdChinaRelease", "fingerprint": "china"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(2, len(entries_for_variant(root, "default")))
            self.assertEqual(1, len(entries_for_variant(root, "ProdGlobalRelease")))


if __name__ == "__main__":
    unittest.main()
