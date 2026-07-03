# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Formats and persists blocked Kotlin coverage reports.
"""Blocked/skipped coverage reporting for Kotlin generation flows."""

from __future__ import annotations

import os
from pathlib import Path
from pydantic import BaseModel, ConfigDict

from UnitTest_gen.core.logging_utils import log_message
from UnitTest_gen.kotlin.coverage_analysis import compact_ranges
from UnitTest_gen.kotlin.project_context import find_gradle_project_root


class BlockedCoverageItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    source: str
    entry: str
    lines: str
    branches: str
    category: str
    reason: str
    evidence: str
    recommended_fix: str
    suggested_test_after_fix: str


def markdown_escape(value: str) -> str:
    return str(value or "").replace("\n", "<br>").replace("|", "\\|")


def reason_code(reason: str) -> str:
    return (reason or "unknown").split(":", 1)[0].strip() or "unknown"


def is_excluded_reason(reason: str) -> bool:
    return reason_code(reason) == "already_tested_no_kover_delta"


def blocked_category(reason: str) -> str:
    code = reason_code(reason)
    if code in {"missing_dependency", "hilt_graph_unclosed", "qualifier_mismatch", "inaccessible_binding"}:
        return "dependency"
    if code in {"needs_static_wrapper", "needs_verified_sdk_fixture", "android_environment_unavailable"}:
        return "environment"
    if code in {
        "invalid_fixture",
        "invalid_hilt_lifecycle_setup",
        "invalid_carui_static_fixture",
        "invalid_nav_deeplink_fixture",
        "duplicate_shared_hilt_test_binding",
    }:
        return "test_setup"
    if code == "already_tested_no_kover_delta":
        return "excluded"
    return "source_code"


def recommended_fix_for_reason(reason: str) -> str:
    code = reason_code(reason)
    if code == "needs_dispatcher_seam":
        return "Inject a CoroutineDispatcher or CoroutineScope and keep production defaults unchanged."
    if code in {"needs_static_wrapper", "needs_verified_sdk_fixture"}:
        return "Wrap the static/global SDK call behind an injectable collaborator or verified test wrapper."
    if code == "needs_verified_exception_fixture":
        return "Expose a deterministic seam, such as an injectable collaborator or public input, before testing this exception branch."
    if code == "private_only_path":
        return "Cover through a public API/lifecycle/callback path, or accept the private implementation gap."
    if code == "declarative_di_no_runtime_body":
        return "Exclude this compile-time DI declaration from executable coverage targets."
    if code == "fragment_path_triggers_uncontrolled_io":
        return "Separate stable Fragment UI assertions from delegated ViewModel IO, or add dispatcher/static seams."
    if code == "already_tested_no_kover_delta":
        return (
            "Excluded from generation: existing tests already exercise this path but Kover still reports a gap. "
            "Change production code (implement the branch, remove dead code) or accept the gap."
        )
    if code == "acceptable_branch_gap":
        return (
            "Excluded from generation: remaining branches are defensive no-ops on chained safe-calls or "
            "companion lateinit fields; happy-path behavior is already covered."
        )
    if code == "fixed_build_variant_branch":
        return "Use a real build variant with the opposite BuildConfig value, or expose runtime configuration through an injectable seam."
    if code == "fixed_internal_branch_input":
        return "Expose the fixed local value as a public input or injectable collaborator before testing the opposite branch."
    if code == "needs_callback_seam":
        return "Inject the callback owner or a registration wrapper so tests can deliver the callback deterministically."
    if code == "needs_private_state_seam":
        return "Expose the required state through an injectable collaborator or public API; do not replace private Activity fields."
    if code in {"qualifier_mismatch", "hilt_graph_unclosed", "missing_dependency", "inaccessible_binding"}:
        return "Add the verified direct test dependency or shared Hilt test binding before lifecycle testing."
    if code.startswith("invalid_") or code == "duplicate_shared_hilt_test_binding":
        return "Use the canonical fixture/setup for this source shape before attempting coverage."
    return "Review the blocked path and add a verified public execution path before generating tests."


def suggested_test_for_reason(reason: str) -> str:
    code = reason_code(reason)
    if code == "needs_dispatcher_seam":
        return "Use runTest with the injected dispatcher/scope and assert the coroutine-body state or collaborator result."
    if code in {"needs_static_wrapper", "needs_verified_sdk_fixture"}:
        return "Mock/fake the injectable wrapper and assert the public behavior produced by the SDK branch."
    if code == "needs_callback_seam":
        return "Inject a fake callback owner, deliver the callback through the public entry, and assert the resulting state."
    if code == "needs_verified_exception_fixture":
        return "Use the public API to trigger the fake/throwing collaborator and assert the visible fallback behavior."
    if code == "private_only_path":
        return "Drive the nearest public method/lifecycle callback that reaches the private code."
    if code == "declarative_di_no_runtime_body":
        return "Test a consumer or executable @Provides selector that uses this binding."
    if code == "fragment_path_triggers_uncontrolled_io":
        return "After seams exist, click the public UI and assert stable Fragment output plus ViewModel interaction/result."
    if code == "already_tested_no_kover_delta":
        return "Do not generate another duplicate test for this branch; it is excluded from the generation queue."
    if code == "acceptable_branch_gap":
        return "Do not generate null-receiver or lateinit accessor probes; accept the defensive branch gap."
    if code == "fixed_build_variant_branch":
        return "Run the public lifecycle in a verified variant where the compile-time flag selects this branch."
    if code == "fixed_internal_branch_input":
        return "Call the public API with the new configurable input and assert the opposite branch result."
    if code == "needs_private_state_seam":
        return "Set the state through the new public/injected seam, run the Activity lifecycle, and assert the visible interaction."
    if code in {"qualifier_mismatch", "hilt_graph_unclosed", "missing_dependency", "inaccessible_binding"}:
        return "Run the attached Hilt lifecycle test after graph closure is verified."
    if code.startswith("invalid_") or code == "duplicate_shared_hilt_test_binding":
        return "Retest the same public behavior with the valid fixture shape."
    return "Add a focused public-contract test after the required dependency/setup/source issue is fixed."


def item_from_opportunity(source_file_path: str, opportunity, evidence: str = "") -> BlockedCoverageItem:
    reason = getattr(opportunity, "blocked_reason", "") or getattr(opportunity, "reason", "") or "unknown"
    lines = _compact_ranges(getattr(opportunity, "lines", []) or [])
    branches = _compact_ranges(getattr(opportunity, "branches", []) or [])
    if reason_code(reason) == "needs_verified_exception_fixture":
        evidence_value = " ".join(
            part for part in (evidence, reason) if part
        )
    else:
        evidence_value = evidence or getattr(opportunity, "execution_proof", "") or getattr(opportunity, "reason", "")
    return BlockedCoverageItem(
        source=source_file_path,
        entry=", ".join(getattr(opportunity, "entry_points", []) or []) or getattr(opportunity, "name", "unknown"),
        lines=lines,
        branches=branches,
        category=blocked_category(reason),
        reason=reason,
        evidence=evidence_value,
        recommended_fix=recommended_fix_for_reason(reason),
        suggested_test_after_fix=suggested_test_for_reason(reason),
    )


def item_from_rejected_opportunity(source_file_path: str, opportunity, evidence: str = "") -> BlockedCoverageItem:
    reason = (
        "already_tested_no_kover_delta: Candidate passed Gradle/Kover execution but did not improve "
        "the selected coverage opportunity."
    )
    lines = _compact_ranges(getattr(opportunity, "lines", []) or [])
    branches = _compact_ranges(getattr(opportunity, "branches", []) or [])
    original_reason = getattr(opportunity, "reason", "") or "No original opportunity reason available."
    no_delta_evidence = (
        evidence
        + " Candidate passed Gradle/Kover but did not reduce the selected line/branch gap. "
        "This likely means the remaining branch needs a different runtime state or a source seam."
    ).strip()
    if branches != "none":
        no_delta_evidence += (
            " For safe-call expressions, the remaining branch may require a framework state that is not "
            "reachable through public APIs after measured no-delta."
        )
    return BlockedCoverageItem(
        source=source_file_path,
        entry=", ".join(getattr(opportunity, "entry_points", []) or []) or getattr(opportunity, "name", "unknown"),
        lines=lines,
        branches=branches,
        category=blocked_category(reason),
        reason=reason,
        evidence=(no_delta_evidence + f" Original opportunity: {original_reason}").strip(),
        recommended_fix=recommended_fix_for_reason(reason),
        suggested_test_after_fix=suggested_test_for_reason(reason),
    )


def item_from_stop(
    source_file_path: str,
    entry: str,
    reason: str,
    evidence: str,
    lines: str = "none",
    branches: str = "none",
) -> BlockedCoverageItem:
    return BlockedCoverageItem(
        source=source_file_path,
        entry=entry or "source/test setup",
        lines=lines or "none",
        branches=branches or "none",
        category=blocked_category(reason),
        reason=reason,
        evidence=evidence,
        recommended_fix=recommended_fix_for_reason(reason),
        suggested_test_after_fix=suggested_test_for_reason(reason),
    )


def _items_from_opportunity_plan(source_file_path: str, opportunity_plan) -> list[BlockedCoverageItem]:
    if not opportunity_plan:
        return []
    return [
        item_from_opportunity(source_file_path, opportunity)
        for opportunity in opportunity_plan.get("blocked", [])
    ]


def dedupe_blocked_items(items):
    deduped = []
    seen = set()
    for item in items:
        key = (item.entry, item.lines, item.branches, item.reason)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _dedupe_items(items: list[BlockedCoverageItem]) -> list[BlockedCoverageItem]:
    return dedupe_blocked_items(items)


def write_hard_stop_blocked_report(
    source_file_path: str,
    entry: str,
    reason: str,
    evidence: str,
    opportunity_plan=None,
    lines: str = "none",
    branches: str = "none",
) -> str | None:
    """Write a blocked report for a hard stop plus any known blocked gaps."""
    items = [
        item_from_stop(
            source_file_path,
            entry,
            reason,
            evidence,
            lines=lines,
            branches=branches,
        )
    ]
    items.extend(_items_from_opportunity_plan(source_file_path, opportunity_plan))
    return write_blocked_coverage_report(source_file_path, _dedupe_items(items))


def write_blocked_coverage_report(source_file_path: str, items: list[BlockedCoverageItem]) -> None:
    items = [item for item in items if item]
    if not items:
        return
    log_message(
        f"⚠️ Blocked coverage: {len(items)} item(s) for {os.path.basename(source_file_path)} "
        "(see Kover gap dashboard).",
        category="warning",
    )
    project_root = find_gradle_project_root(source_file_path, markers=("gradlew",))
    if project_root:
        refresh_kover_gap_reports(project_root)


def clear_blocked_coverage_report(source_file_path: str) -> bool:
    """Refresh dashboard state after accepted coverage; legacy per-source log files are no longer written."""
    project_root = find_gradle_project_root(source_file_path, markers=("gradlew",))
    if project_root:
        refresh_kover_gap_reports(project_root)
    return False


def refresh_kover_gap_reports(project_root: str, output_dir: str | None = None) -> list[str]:
    """Build Kover-only gap dashboards for all release variants."""
    from UnitTest_gen.helper.dashboard.blocked_coverage_kover_report import write_kover_gap_reports

    root = Path(project_root).resolve()
    if not root.is_dir():
        return []
    out_dir = Path(output_dir).resolve() if output_dir else None
    written, _models = write_kover_gap_reports(root, output_dir=out_dir)
    if written:
        log_message(
            "🧭 Refreshed Kover gap dashboards: " + ", ".join(str(path) for path in written[:4]),
            category="warning",
        )
    return [str(path) for path in written]


def _compact_ranges(values) -> str:
    numbers = sorted({int(value) for value in values if str(value).isdigit() or isinstance(value, int)})
    return compact_ranges(numbers)
