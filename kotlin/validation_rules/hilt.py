# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Validates Hilt and Robolectric test graphs.
"""Hilt and Robolectric graph validation rules."""

from __future__ import annotations

from UnitTest_gen.kotlin.project_context import module_has_shared_test_hilt_binding
from UnitTest_gen.kotlin.strategy_contracts import validation_repair_intent


def collect_validation_issues(test_code: str, output_file_path: str, source_code: str, test_report) -> list[str]:
    issues: list[str] = []

    if "HiltAndroidRunner" in test_code:
        issues.append(
            "Local Robolectric JVM tests should use @RunWith(RobolectricTestRunner::class). "
            "Hilt fragment tests can still use @HiltAndroidTest, HiltAndroidRule, and "
            "@Config(application = HiltTestApplication::class) with RobolectricTestRunner."
        )

    if test_report.tests.has_hilt_module_install_in:
        issues.append(
            "invalid_global_hilt_graph_mutation: "
            + validation_repair_intent("invalid_global_hilt_graph_mutation")
        )

    if (
        module_has_shared_test_hilt_binding(output_file_path, "IDigitalKeyFirebase")
        and any(
            "BindValue" in property_info.annotations
            and property_info.type_text.split(".")[-1] == "IDigitalKeyFirebase"
            for property_info in test_report.properties
        )
    ):
        issues.append(
            "duplicate_shared_hilt_test_binding: "
            + validation_repair_intent("duplicate_shared_hilt_test_binding")
        )

    return issues
