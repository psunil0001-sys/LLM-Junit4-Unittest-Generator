# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Fragment/ViewModel generation quality validators.
"""Fragment and ViewModel quality gates shared by full and incremental generation."""

from __future__ import annotations

import re

from UnitTest_gen.kotlin.kotlin_analysis import source_declares_android_fragment, source_declares_viewmodel_class
from UnitTest_gen.kotlin.strategy_contracts import validation_repair_intent


def _issue(code: str) -> str:
    return f"{code}: {validation_repair_intent(code)}"


def collect_fragment_viewmodel_quality_issues(
    test_code: str, output_file_path: str, source_code: str, test_report
) -> list[str]:
    del output_file_path, test_report
    issues: list[str] = []
    source = source_code or ""
    test = test_code or ""
    if not source.strip() or not test.strip():
        return issues

    lower_test = test.lower()
    lower_source = source.lower()

    if re.search(
        r'getDeclaredField\s*\(\s*["\'](?:_uiState|uiState|_state|state)["\']',
        test,
        flags=re.IGNORECASE,
    ):
        issues.append(_issue("invalid_stateflow_reflection"))

    if source_declares_android_fragment(source) and (
        "activityviewmodels" in lower_source
        or re.search(r"\bviewmodels\s*\(", lower_source)
        or "by viewmodels" in lower_source
    ):
        if re.search(r"ViewModelProvider\s*\([^)]*\)\s*\.\s*get\s*\(", test):
            issues.append(_issue("invalid_delegated_viewmodel_provider"))

    if source_declares_android_fragment(source) and "setmenuitems" in lower_source:
        if re.search(r"\bmenuitems?\b", lower_test) and not any(
            token in lower_test
            for token in ("captor", "capture", "onclick", "invoke", "performclick", "argumentcaptor")
        ):
            if re.search(r"\bverify\s*\(", test):
                issues.append(_issue("missing_coverage_trigger_menu"))

    if source_declares_android_fragment(source):
        detached_lifecycle = re.search(
            r"\bval\s+\w+\s*=\s*[A-Za-z_][A-Za-z0-9_]*Fragment\s*\(\s*\)"
            r"[\s\S]{0,500}\.\s*on(?:Resume|Start|Pause|Stop)\s*\(",
            test,
        )
        attached = any(
            token in test
            for token in (
                "commitNow",
                "supportFragmentManager",
                "activityController",
                "Robolectric.buildActivity",
            )
        )
        if detached_lifecycle and not attached and "isadded" in lower_source:
            issues.append(_issue("invalid_fragment_detached_lifecycle_probe"))

    if source_declares_viewmodel_class(source) and re.search(
        r"verify\s*\([^)]+\)\s*\.\s*\w+\s*=",
        test,
    ):
        issues.append(_issue("invalid_property_assignment_verification"))

    return issues
