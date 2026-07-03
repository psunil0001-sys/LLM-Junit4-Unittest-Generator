# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Smoke tests for Kover coverage summary parsing.
"""Smoke tests for Kover coverage summary reports."""

from __future__ import annotations

import unittest
from pathlib import Path

from UnitTest_gen.helper.dashboard.kover_coverage_summary import (
    build_variant_coverage_summary,
    parse_kover_summary_xml,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SAMPLE_XML = REPO_ROOT / "common/build/reports/kover/report.xml"
if not SAMPLE_XML.is_file():
    SAMPLE_XML = REPO_ROOT / "common/build/reports/kover/reportProdGlobalRelease.xml"


class KoverCoverageSummaryTest(unittest.TestCase):
    @unittest.skipUnless(SAMPLE_XML.is_file(), "Kover XML not present")
    def test_parse_kover_summary_xml_reads_line_counters(self):
        report = parse_kover_summary_xml(SAMPLE_XML, REPO_ROOT)
        self.assertEqual("common", report["module"])
        self.assertIn(report["variant"], ("default", "ProdGlobalRelease"))
        self.assertGreater(report["overall"]["line"]["total"], 0)
        self.assertTrue(report["packages"])
        self.assertTrue(report["files"])

    @unittest.skipUnless(SAMPLE_XML.is_file(), "Kover XML not present")
    def test_build_variant_coverage_summary_aggregates_modules(self):
        model = build_variant_coverage_summary(REPO_ROOT, [SAMPLE_XML])
        self.assertIn(model["variant"], ("default", "ProdGlobalRelease"))
        self.assertEqual(1, model["module_count"])
        self.assertIn("line", model["overall"])


if __name__ == "__main__":
    unittest.main()
