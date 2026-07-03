# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Validates incremental coverage orchestration patterns.
"""Coverage orchestration validation for generated Kotlin tests."""

from __future__ import annotations

import re

from UnitTest_gen.kotlin.kotlin_analysis import kotlin_top_level_class_name, source_declares_android_fragment
from UnitTest_gen.kotlin.static_analysis import analyze_kotlin_code
from UnitTest_gen.kotlin.strategy_contracts import validation_repair_intent


def _extract_junit4_test_blocks(test_code: str) -> list[str]:
    report = analyze_kotlin_code(test_code or "")
    lines = (test_code or "").splitlines()
    return [
        "\n".join(lines[function.span.start_line - 1 : function.span.end_line])
        for function in report.functions
        if "Test" in function.annotations
    ]


def _source_singleton_delegation_helpers(source_code: str) -> set[str]:
    """Types reached via getInstance() inside the source under test."""
    helpers: set[str] = set()
    for match in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*)\.getInstance\s*\(\s*\)", source_code or ""):
        helpers.add(match.group(1))
    return helpers


def _source_has_dialog_callback_chain(source_code: str) -> bool:
    markers = (
        "onPositiveAction",
        "onNegativeAction",
        "onUserAction",
        "showAlert(",
        "FullScreenDialogFragment.newInstance",
        "AlertDialogHelper",
        "setButton(",
    )
    return any(marker in (source_code or "") for marker in markers)


def _block_exercises_target_class(block: str, target_class: str) -> bool:
    if target_class and target_class in block:
        return True
    if re.search(r"\b(?:fragment|sut|subject|target)\s*[=:.]", block):
        return True
    if re.search(r"\b(?:fragment|sut|subject|target)\s*\.", block):
        return True
    return False


def _block_calls_helper_get_instance(block: str, helper: str) -> bool:
    return bool(re.search(rf"\b{re.escape(helper)}\.getInstance\s*\(\s*\)", block))


def _lifecycle_state_after_stop_is_wrong(block: str) -> bool:
    if not re.search(r"(?:activityController|controller|host|scenario)\s*\.\s*stop\s*\(|\bonStop\s*\(", block):
        return False
    stop_at = None
    for match in re.finditer(
        r"(?:activityController|controller|host|scenario)\s*\.\s*stop\s*\(|\bonStop\s*\(",
        block,
    ):
        stop_at = match.start()
    if stop_at is None:
        return False
    tail = block[stop_at:]
    return bool(
        re.search(
            r"assertEquals\s*\(\s*Lifecycle\.State\.(?:STARTED|RESUMED)\s*,\s*\w+\.lifecycle\.currentState",
            tail,
        )
        or re.search(
            r"assertSame\s*\(\s*Lifecycle\.State\.(?:STARTED|RESUMED)\s*,\s*\w+\.lifecycle\.currentState",
            tail,
        )
    )


def collect_coverage_orchestration_validation_issues(
    test_code: str, output_file_path: str, source_code: str, test_report
) -> list[str]:
    """Reject tests that bypass the target class entry path or assert impossible lifecycle states."""
    issues: list[str] = []
    source_code = source_code or ""
    if not source_code.strip():
        return issues

    target_class = kotlin_top_level_class_name(source_code, "")
    helpers = _source_singleton_delegation_helpers(source_code)
    dialog_chain = _source_has_dialog_callback_chain(source_code)

    for block in _extract_junit4_test_blocks(test_code):
        if dialog_chain and helpers:
            for helper in sorted(helpers):
                if helper == target_class:
                    continue
                if _block_calls_helper_get_instance(block, helper) and not _block_exercises_target_class(
                    block, target_class
                ):
                    issues.append(
                        "missing_coverage_trigger_dialog: "
                        + validation_repair_intent("missing_coverage_trigger_dialog")
                    )
                    break

        if source_declares_android_fragment(source_code) and _lifecycle_state_after_stop_is_wrong(block):
            issues.append(
                "invalid_fragment_lifecycle_state_assertion: "
                + validation_repair_intent("invalid_fragment_lifecycle_state_assertion")
            )

    return issues
