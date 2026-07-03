# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Semgrep parse-error fallback for generated Kotlin tests.
"""Tests that semgrep parse failures do not crash Kotlin static analysis."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from UnitTest_gen.core.semgrep_runner import SemgrepUnavailableError
from UnitTest_gen.kotlin.static_analysis import analyze_kotlin_test_code


class SemgrepParseFallbackTest(unittest.TestCase):
    def test_analyze_kotlin_test_code_survives_semgrep_parse_error(self):
        test_code = """
package com.example

import androidx.lifecycle.MutableLiveData
import org.junit.Test
import java.lang.reflect.Field

class ExampleViewModelTest {
    @Test
    fun `sets_live_data_via_reflection`() {
        val viewModel = ExampleViewModel()
        val field: Field = ExampleViewModel::class.java.getDeclaredField("state")
        field.isAccessible = true
        (field.get(viewModel) as? MutableLiveData<State>)?.value = true
    }
}

class ExampleViewModel
class State
"""
        report = analyze_kotlin_test_code(test_code)
        self.assertEqual("complete", report.status)
        self.assertTrue(report.functions)

    @patch(
        "UnitTest_gen.kotlin.static_analysis.scan_text_with_semgrep",
        side_effect=SemgrepUnavailableError(
            "Semgrep-core reported analysis errors: expression was unexpected"
        ),
    )
    def test_broad_semgrep_syntax_error_also_falls_back(self, _scan):
        report = analyze_kotlin_test_code(
            "import org.mockito.Mockito\nclass Broken { fun run() { = true } }"
        )
        self.assertEqual("complete", report.status)


if __name__ == "__main__":
    unittest.main()
