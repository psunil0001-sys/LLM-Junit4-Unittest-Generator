# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Coordinates Kover-guided incremental test generation and acceptance.
"""Kover-guided incremental coverage generation flow."""

import os
import re
from dataclasses import dataclass

from UnitTest_gen.core.pipeline_config import get_config
from UnitTest_gen.kotlin.coverage_analysis import coverage_gap_context, coverage_gap_delta_summary, find_latest_kover_xml_for_context, kover_report_module_dirs, parse_kover_gap, parse_latest_coverage_gap
from UnitTest_gen.kotlin.gradle_analysis.junit import is_gradle_success, text_fingerprint
from UnitTest_gen.kotlin.kotlin_analysis import parse_kotlin_ast, source_rule_categories, walk_ast
from UnitTest_gen.core.logging_utils import archive_generated_side_files, log_block, log_message, log_section
from UnitTest_gen.kotlin.blocked_coverage_report import item_from_opportunity, item_from_rejected_opportunity, item_from_stop, write_blocked_coverage_report
from UnitTest_gen.kotlin.attempted_failed_report import DISPOSITION_PIPELINE_UNRESOLVED, record_kover_rejected_attempt
from UnitTest_gen.kotlin.guardrail_catalog import enrich_validation_error_block, repair_intent_for
from UnitTest_gen.core.mcp_tools import LocalMcpTools
from UnitTest_gen.kotlin.memory_context import retrieve_repair_lessons
from UnitTest_gen.kotlin.mcp_tool_adapter import run_gradle_with_heartbeat
from UnitTest_gen.kotlin.project_context import derive_coverage_supplement_path, derive_indirect_coverage_test_path, find_owning_module_dir
from UnitTest_gen.kotlin.prompting.generation import generate_coverage_supplement_test_streaming
from UnitTest_gen.kotlin.prompting.repair import (
    repair_focused_error_block_streaming,
    repair_focused_error_patch_streaming,
)
from UnitTest_gen.kotlin.repair_flow import verify_and_repair_test_with_mcp
from UnitTest_gen.kotlin.test_code_utils.merge import force_generated_test_class_name, merge_supplemental_test_code
from UnitTest_gen.kotlin.test_code_utils.validate import normalize_kotlin_test_code, remember_successful_generation_if_high_quality, validate_generated_test_code, validation_issues_added
from UnitTest_gen.kotlin.static_analysis import analyze_kotlin_code


@dataclass
class CoverageOpportunity:
    name: str
    bucket: str
    fixture: str
    entry_points: list[str]
    lines: list[int]
    branches: list[int]
    reason: str
    action: str
    execution_proof: str = ""
    blocked_reason: str = ""
    coverage_path: list[str] | None = None
    trigger_requirement: str = "none"
    trigger_recipe: str = "direct public execution"


@dataclass
class CoverageAcceptanceDecision:
    accepted: bool
    reason: str
    resolved_selected_lines: list[int]
    resolved_unselected_lines: list[int]
    newly_exposed_lines: list[int]
    true_regression_lines: list[int]
    resolved_methods: list[str]
    new_missed_methods: list[str]


_REJECTED_INCREMENTAL_OPPORTUNITY_FINGERPRINTS: dict[str, set[str]] = {}
_INCREMENTAL_RETRY_FEEDBACK: dict[str, str] = {}
_RETRY_INCREMENTAL = object()
_RETRYABLE_ACCEPTANCE_REASONS = frozenset(
    {
        "no Kover improvement",
        "coverage candidate rejected",
    }
)


def _validation_issue_fingerprint(issues: list[str]) -> tuple[str, ...]:
    return tuple(sorted({issue.split(":", 1)[0].strip() for issue in issues}))


def _apply_exact_patches(code: str, patches: list[dict]) -> tuple[str, int]:
    applied = 0
    for patch in patches or []:
        old_text = patch.get("old_text", "")
        if not old_text or old_text not in code:
            continue
        code = code.replace(old_text, patch.get("new_text", ""), 1)
        applied += 1
    return code, applied

BLOCKED_NEEDS_DISPATCHER_SEAM = "needs_dispatcher_seam"
BLOCKED_NEEDS_STATIC_WRAPPER = "needs_static_wrapper"
BLOCKED_NEEDS_VERIFIED_EXCEPTION_FIXTURE = "needs_verified_exception_fixture"
BLOCKED_PRIVATE_ONLY_PATH = "private_only_path"
BLOCKED_ALREADY_TESTED_NO_DELTA = "already_tested_no_kover_delta"
BLOCKED_ACCEPTABLE_BRANCH_GAP = "acceptable_branch_gap"
BLOCKED_FIXED_BUILD_VARIANT_BRANCH = "fixed_build_variant_branch"
BLOCKED_FIXED_INTERNAL_BRANCH_INPUT = "fixed_internal_branch_input"
BLOCKED_NEEDS_CALLBACK_SEAM = "needs_callback_seam"


def incremental_line_budget() -> int:
    return get_config().incremental_line_budget


def incremental_safe_cap() -> int:
    return get_config().incremental_safe_cap


def relevant_gradle_tasks_executed(gradle_output: str) -> bool:
    relevant_task_lines = [
        line
        for line in (gradle_output or "").splitlines()
        if line.startswith("> Task ")
        and re.search(r":(?:test[^ ]*UnitTest|koverXmlReport[^ ]*|koverGenerateArtifact[^ ]*)", line)
    ]
    return any(
        not any(marker in line for marker in ("UP-TO-DATE", "FROM-CACHE", "NO-SOURCE", "SKIPPED"))
        for line in relevant_task_lines
    )


def _selected_opportunities(opportunity_plan) -> list:
    plan = opportunity_plan or {}
    return (
        list(plan.get("selected_safe", []))
        + list(plan.get("selected_attemptable", []))
        + list(plan.get("selected_blocked", []))
    )


def opportunity_fingerprint(opportunity: CoverageOpportunity) -> str:
    return _opportunity_fingerprint(opportunity)


def _primary_safe_call_receiver(text: str) -> str | None:
    match = re.search(r"(\w+)\?\.", text)
    return match.group(1) if match else None


def _same_line_null_guarded_safe_call(text: str) -> bool:
    if "?." not in text:
        return False
    for match in re.finditer(r"(\w+)\s*==\s*null", text):
        receiver = match.group(1)
        if re.search(rf"\b{re.escape(receiver)}\?\.", text):
            return True
    return False


def _else_arm_redundant_safe_call(source_lines: list[str], line_no: int) -> bool:
    if not (1 <= line_no <= len(source_lines)):
        return False
    text = source_lines[line_no - 1]
    receiver = _primary_safe_call_receiver(text)
    if not receiver or text.count("?.") != 1:
        return False
    else_index = None
    for index in range(line_no - 2, max(-1, line_no - 25), -1):
        if index < 0:
            break
        if re.search(r"\}\s*else\s*\{", source_lines[index]):
            else_index = index
            break
    if else_index is None:
        return False
    if_index = None
    for index in range(else_index - 1, max(-1, else_index - 20), -1):
        if index < 0:
            break
        if re.search(r"\bif\s*\(", source_lines[index]):
            if_index = index
            break
    if if_index is None:
        return False
    condition_text = "\n".join(source_lines[if_index : else_index + 1])
    return bool(re.search(rf"\b{re.escape(receiver)}\s*==\s*null\b", condition_text))


def _branch_line_is_defensive(source_lines: list[str], line_no: int) -> bool:
    if not (1 <= line_no <= len(source_lines)):
        return False
    text = source_lines[line_no - 1]
    if text.count("?.") >= 2:
        return True
    if re.search(r"\blateinit\s+var\b", text):
        return True
    if _same_line_null_guarded_safe_call(text):
        return True
    if _else_arm_redundant_safe_call(source_lines, line_no):
        return True
    return False


def is_acceptable_branch_gap_opportunity(
    opportunity,
    source_code: str,
    existing_test_code: str = "",
) -> bool:
    del existing_test_code
    if getattr(opportunity, "lines", None):
        return False
    branches = list(getattr(opportunity, "branches", None) or [])
    if not branches:
        return False
    source_lines = source_code.splitlines()
    return any(_branch_line_is_defensive(source_lines, line_no) for line_no in branches)


def _line_missed_branches(gap, line_no: int) -> int | None:
    details = getattr(gap, "line_details", None) or {}
    detail = details.get(line_no)
    return detail.missed_branches if detail is not None else None


def _selected_branch_mb_progress(before, after, selected_branch_lines: set[int]) -> bool:
    for line_no in selected_branch_lines:
        before_mb = _line_missed_branches(before, line_no)
        after_mb = _line_missed_branches(after, line_no)
        if before_mb is not None and after_mb is not None and after_mb < before_mb:
            return True
    return False


def _stalled_branch_probe_fingerprints(before, after, opportunity_plan, existing_test_code: str) -> set[str]:
    fingerprints: set[str] = set()
    for opportunity in _selected_opportunities(opportunity_plan):
        branches = set(opportunity.branches or [])
        if not branches or opportunity.lines:
            continue
        if not branches & set(getattr(after, "partial_branch_lines", None) or []):
            continue
        if _selected_branch_mb_progress(before, after, branches):
            continue
        entry = next((item for item in (opportunity.entry_points or []) if item), "")
        if not entry or not _test_mentions_entry_point(existing_test_code, entry):
            continue
        fingerprints.add(_opportunity_fingerprint(opportunity))
    return fingerprints
BLOCKED_FRAGMENT_UNCONTROLLED_VIEWMODEL_IO = "fragment_path_triggers_uncontrolled_io"


def _blocked_reason(code: str, detail: str) -> str:
    return f"{code}: {detail}"


def _compact_int_ranges(values: list[int]) -> str:
    values = sorted(set(values))
    if not values:
        return "none"
    ranges = []
    start = prev = values[0]
    for value in values[1:]:
        if value == prev + 1:
            prev = value
            continue
        ranges.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = value
    ranges.append(str(start) if start == prev else f"{start}-{prev}")
    return ", ".join(ranges)


def _function_ranges_from_analysis(source_code: str):
    report = analyze_kotlin_code(source_code)
    return [
        {
            "name": function.name,
            "start": function.span.start_line,
            "end": function.span.end_line,
            "visibility": function.visibility,
        }
        for function in report.functions
    ]


def _function_for_line(functions, line_no: int):
    candidates = [function for function in functions if function["start"] <= line_no <= function["end"]]
    return min(candidates, key=lambda function: function["end"] - function["start"], default=None)


def _public_entry_path(source_code: str, functions: list[dict], target: dict, report) -> tuple[list[str], str]:
    by_name = {function["name"]: function for function in functions}
    edges: dict[str, list[tuple[str, str]]] = {}
    nested_names = set()
    for child in functions:
        containers = [
            parent
            for parent in functions
            if parent is not child
            and parent["start"] <= child["start"]
            and child["end"] <= parent["end"]
        ]
        if containers:
            parent = min(containers, key=lambda item: item["end"] - item["start"])
            edges.setdefault(parent["name"], []).append((child["name"], "anonymous_callback"))
            nested_names.add(child["name"])
    for call in report.calls:
        callee = by_name.get(call.name)
        caller = _function_for_line(functions, call.span.start_line)
        if not callee or not caller or caller["name"] == callee["name"]:
            continue
        callbacks = [
            callback
            for callback in report.callbacks
            if callback.span.start_line <= call.span.start_line <= callback.span.end_line
        ]
        trigger = min(
            callbacks,
            key=lambda callback: callback.span.end_line - callback.span.start_line,
            default=None,
        )
        trigger_kind = trigger.kind if trigger else ""
        if trigger and any(name in trigger.called_identifiers for name in ("CoroutineScope", "Dispatchers")):
            trigger_kind += "|hardcoded_dispatcher"
        edges.setdefault(caller["name"], []).append((callee["name"], trigger_kind))

    queue = [
        ([function["name"]], "")
        for function in functions
        if function["visibility"] != "private" and function["name"] not in nested_names
    ]
    seen = set()
    while queue:
        path, trigger = queue.pop(0)
        current = path[-1]
        if current == target["name"]:
            return path, trigger
        if current in seen:
            continue
        seen.add(current)
        for callee, edge_trigger in edges.get(current, []):
            queue.append((path + [callee], trigger or edge_trigger))
    return [], ""


def _catch_ranges(source_code: str) -> list[tuple[int, int]]:
    return [
        (node.start_point[0] + 1, node.end_point[0] + 1)
        for node in walk_ast(parse_kotlin_ast(source_code))
        if node.type == "catch_block"
    ]


def _buildconfig_ranges(source_code: str) -> list[tuple[int, int]]:
    source_bytes = source_code.encode("utf-8")
    aliases = set(
        re.findall(
            r"\bval\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*BuildConfig\.[A-Za-z_][A-Za-z0-9_]*",
            source_code,
        )
    )
    return [
        (node.start_point[0] + 1, node.end_point[0] + 1)
        for node in walk_ast(parse_kotlin_ast(source_code))
        if node.type == "if_expression"
        and (
            "BuildConfig." in source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")
            or any(
                re.search(
                    rf"\b{re.escape(alias)}\b",
                    source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="ignore"),
                )
                for alias in aliases
            )
        )
    ]


def _line_owner_map(source_code: str) -> dict[int, str]:
    owners: dict[int, str] = {}
    for function in _function_ranges_from_analysis(source_code):
        if function["visibility"] == "private":
            continue
        for line_no in range(function["start"], function["end"] + 1):
            owners[line_no] = function["name"]
    return owners


def _snippet_for_range(lines: list[str], start: int, end: int) -> str:
    return "\n".join(lines[max(0, start - 1):min(len(lines), end)])


def _targeted_snippet_for_lines(lines: list[str], target_lines: list[int], function: dict, context: int = 4) -> str:
    if not target_lines:
        return _snippet_for_range(lines, function["start"], function["end"])
    start = max(function["start"], min(target_lines) - context)
    end = min(function["end"], max(target_lines) + context)
    return _snippet_for_range(lines, start, end)


def _risky_fragment_callback_ranges(source_code: str, function: dict, categories: set[str]) -> list[tuple[int, int]]:
    del categories
    report = analyze_kotlin_code(source_code)
    return [
        (callback.span.start_line, callback.span.end_line)
        for callback in report.callbacks
        if callback.function_name == function["name"]
    ]


def _lines_requiring_uncontrolled_coroutine(
    report,
    functions: list[dict],
    function: dict,
    coverage_path: list[str],
    target_lines: list[int],
) -> list[int]:
    """Return target lines gated by earlier same-file uncontrolled coroutine work."""
    hardcoded_owners = {
        usage.function_name
        for usage in report.coroutine_usages
        if usage.dispatcher in {"IO", "Default"} or usage.kind in {"global_scope", "standalone_scope"}
    }
    calls_by_owner: dict[str, list] = {}
    for call in report.calls:
        calls_by_owner.setdefault(call.function_name, []).append(call)

    risky = set(hardcoded_owners)
    changed = True
    while changed:
        changed = False
        for owner, calls in calls_by_owner.items():
            if owner not in risky and any(call.name in risky for call in calls):
                risky.add(owner)
                changed = True

    path_prefix = set((coverage_path or [])[:-1])
    if path_prefix & risky:
        return list(target_lines)

    if function["name"] not in risky:
        return []
    direct_boundaries = [
        usage.span.start_line
        for usage in report.coroutine_usages
        if usage.function_name == function["name"]
        and (usage.dispatcher in {"IO", "Default"} or usage.kind in {"global_scope", "standalone_scope"})
    ]
    direct_boundaries.extend(
        call.span.start_line
        for call in calls_by_owner.get(function["name"], [])
        if call.name in risky
    )
    if not direct_boundaries:
        return []
    first_boundary = min(direct_boundaries)
    return [line_no for line_no in target_lines if line_no >= first_boundary]


def _lines_in_ranges(values: list[int], ranges: list[tuple[int, int]]) -> list[int]:
    return [value for value in values if any(start <= value <= end for start, end in ranges)]


def _lines_outside_ranges(values: list[int], ranges: list[tuple[int, int]]) -> list[int]:
    return [value for value in values if not any(start <= value <= end for start, end in ranges)]


def _upstream_context_lines(source_code: str, target_lines: list[int], max_lookback: int = 5) -> list[str]:
    lines = source_code.splitlines()
    collected: list[str] = []
    for line_no in sorted(set(target_lines or [])):
        if line_no < 1 or line_no > len(lines):
            continue
        for offset in range(1, max_lookback + 1):
            index = line_no - 1 - offset
            if index < 0:
                break
            text = lines[index].strip()
            if not text:
                continue
            collected.append(lines[index])
            if re.search(r"\b(val|var)\s+\w+", text) or text.endswith("{") or text.endswith("}"):
                break
    return collected


def _branch_probe_bullets_for_lines(source_lines: list[str], line_numbers: list[int]) -> list[str]:
    bullets = []
    for line_no in line_numbers:
        if not (1 <= line_no <= len(source_lines)):
            continue
        text = source_lines[line_no - 1].strip()
        if re.search(r"\bas\?\s+\w+\s*\)\?\.(?:let|run|apply)\b", text):
            bullets.append(f"  line {line_no}: Probe cast-success vs cast-fail arms with matching activity/context fixture.")
        elif "->" in text and "when" in "\n".join(source_lines[max(0, line_no - 3):line_no]):
            bullets.append(f"  line {line_no}: For partial branch gaps, use the selected trigger recipe; often this is the complementary/default input, not this printed case label.")
        elif text.startswith("if (") or text.startswith("} else if ("):
            bullets.append(f"  line {line_no}: Cover false/null arm with input where the condition is false.")
        elif "?:" in text:
            bullets.append(f"  line {line_no}: Cover alternate boolean/null arm with inverted fixture input.")
        elif "catch (" in text:
            bullets.append(f"  line {line_no}: Throw matching exception from mocked collaborator before call.")
        elif "?." in text:
            bullets.append(f"  line {line_no}: Pass null receiver to hit safe-call short-circuit.")
    return bullets


def _when_case_labels(source_lines: list[str], function: dict) -> list[str]:
    labels: list[str] = []
    for line in source_lines[function["start"] - 1 : function["end"]]:
        text = line.strip()
        if "->" not in text:
            continue
        label = text.split("->", 1)[0].strip().strip("{}")
        if not label or label == "else":
            continue
        for part in label.split(","):
            value = part.strip()
            if value and value not in labels:
                labels.append(value)
    return labels


def _complementary_when_branch_recipe(source_lines: list[str], function: dict, branches: list[int], gap) -> str:
    branch_lines = set(branches or [])
    for line_no in branch_lines:
        if not (1 <= line_no <= len(source_lines)):
            continue
        text = source_lines[line_no - 1].strip()
        detail = (getattr(gap, "line_details", None) or {}).get(line_no)
        if "->" not in text or not detail:
            continue
        if getattr(detail, "missed_instructions", 0) != 0 or getattr(detail, "covered_branches", 0) < 1:
            continue
        labels = _when_case_labels(source_lines, function)
        if not labels:
            continue
        return "Use an unmatched/default input different from " + ", ".join(labels) + ". Do not repeat the printed case label when Kover already covered that arm."
    return ""


def _infer_callback_trigger_from_path(function: dict, source_snippet: str, source_code: str) -> tuple[str, str, str, str, str, str] | None:
    coverage_path = " ".join(function.get("coverage_path") or [])
    path_names = set(function.get("coverage_path") or [])
    source_lines = (source_code or "").splitlines()
    path_declarations = "\n".join(
        _snippet_for_range(source_lines, item["start"], item["end"])
        for item in _function_ranges_from_analysis(source_code or "")
        if item["name"] in path_names
    )
    trigger_context = f"{coverage_path}\n{source_snippet}\n{path_declarations}"
    lower = trigger_context.lower()
    path_lower = coverage_path.lower()
    if "callback" not in path_lower and not any(
        token in lower
        for token in ("setonclicklistener", "setmenuitems", "observe", "collect", "registerbacklistener")
    ):
        return None
    if "registerbacklistener" in lower:
        return (
            "attemptable",
            "verified_callback",
            "Public path registers a CarUi back callback.",
            "Capture the verified callback passed to registerBackListener, invoke it, and assert navigation/state.",
            "Concrete trigger: capture the registered callback and invoke it after Fragment attach.",
            "",
        )
    if "setmenuitems" in lower or (
        "menuitem" in lower and any(token in lower for token in ("toolbar", "setmenuitems", "menuitems"))
    ):
        return (
            "safe",
            "verified_menu_callback",
            "Public path registers a CarUi toolbar menu item listener.",
            "After Fragment attach, capture List<MenuItem> passed to setMenuItems and call performClick() on the target item.",
            "Concrete trigger: capture the registered MenuItem and call performClick().",
            "",
        )
    if "registerforactivityresult" in lower:
        return (
            "attemptable",
            "verified_activity_result_callback",
            "Public API registers an ActivityResult callback that requires controlled result delivery.",
            "Register the launcher, then deliver a verified ActivityResult through the Robolectric/registry fixture.",
            "Concrete trigger: controlled ActivityResult delivery after callback registration.",
            "",
        )
    if re.search(r"\b(?:flowwithlifecycle|stateflow|sharedflow)\b", lower) or re.search(
        r"\b(?:onEach|collect|collectLatest)\b", lower
    ):
        return (
            "attemptable",
            "verified_stream_emission",
            "Public path collects a Flow; supply a finite verified stream.",
            "Provide flowOf/emit with the required item and advance the test scheduler.",
            "Concrete trigger: verified finite Flow emission through the public entry.",
            "",
        )
    if "setonclicklistener" in lower and re.search(r"\b(?:observe|observeForever)\b", lower):
        return (
            "attemptable",
            "verified_observer_and_click",
            "Public path requires both observable emission and a registered click.",
            "post/set the required value on the same observable, then performClick() on the registered view.",
            "Concrete trigger: post/set followed by performClick().",
            "",
        )
    if "setonclicklistener" in lower and "isinternetavailable" in lower:
        return (
            "attemptable",
            "verified_ui_click",
            "Click path depends on Android network state that must be configured through Robolectric.",
            "Configure ShadowConnectivityManager for the required activeNetwork/NetworkCapabilities branch, configure browser resolution when needed, then performClick().",
            "Concrete trigger: verified Robolectric network state followed by performClick().",
            "",
        )
    if "setonclicklistener" in lower:
        return (
            "safe",
            "verified_ui_click",
            "Public path registers a concrete view click listener.",
            "Attach the Fragment and performClick() on the registered view.",
            "Concrete trigger: setOnClickListener followed by performClick().",
            "",
        )
    if any(token in lower for token in ("showalert", "alertdialoghelper", "fullscreendialogfragment")) and (
        "callback" in path_lower or "onuseraction" in lower
    ):
        return (
            "attemptable",
            "verified_dialog_callback",
            "Public path opens a dialog; drive the real button or captured callback.",
            "Open the dialog through the public entry, then click a button or invoke the captured callback.",
            "Concrete trigger: ArgumentCaptor/onUserAction.invoke() or getButton(...).performClick().",
            "",
        )
    if re.search(r"\b(?:observe|observeForever)\b", lower):
        return (
            "attemptable",
            "verified_observer_emission",
            "Public path reaches observer registration; emit through the same ViewModel/LiveData instance.",
            "Register the observer, then post/set the required value on the same observable instance.",
            "Concrete trigger: post/set",
            "",
        )
    if any(token in lower for token in ("showalert", "alertdialoghelper", "fullscreendialogfragment")):
        return (
            "attemptable",
            "verified_dialog_callback",
            "Public path opens a dialog; drive the real button or captured callback.",
            "Open the dialog through the public entry, then click a button or invoke the captured callback.",
            "Concrete trigger: ArgumentCaptor/onUserAction.invoke() or getButton(...).performClick().",
            "",
        )
    if "postdelayed" in lower or "handler.post" in lower:
        return (
            "attemptable",
            "robolectric_delayed_handler",
            "Delayed handler path can be advanced with Robolectric ShadowLooper.",
            "Advance the main looper past the production delay, then assert navigation/state.",
            "Concrete trigger: ShadowLooper.idleFor(...) after lifecycle attach.",
            "",
        )
    if re.search(r"\b(?:collect|collectLatest)\b", lower):
        return (
            "attemptable",
            "verified_stream_emission",
            "Public path collects a Flow; supply a finite verified stream.",
            "Provide flowOf/emit with the required item and advance the test scheduler.",
            "Concrete trigger: verified finite Flow emission through the public entry.",
            "",
        )
    if (
        "lifecycleScope.launch" in lower
        or "viewLifecycleOwner.lifecycleScope.launch" in lower
        or re.search(r"\blaunch\s*\{", lower)
    ) and "dispatchers.io" not in lower and "coroutinescope(" not in lower:
        return (
            "attemptable",
            "verified_coroutine_completion",
            "Coroutine path has no fixed dispatcher blocker; run under runTest.",
            "Invoke the public entry under runTest and advanceUntilIdle before asserting.",
            "Concrete trigger: runTest { entry(); advanceUntilIdle() }.",
            "",
        )
    return None


def _opportunity_line_count(opportunity: CoverageOpportunity) -> int:
    return len(set((opportunity.lines or []) + (opportunity.branches or [])))


def _select_largest_fixture_opportunities(
    opportunities: list[CoverageOpportunity],
    *,
    weight_fn,
    cap: int,
    line_budget: int,
) -> tuple[list[CoverageOpportunity], str | None]:
    if not opportunities:
        return [], None

    by_fixture: dict[str, list[CoverageOpportunity]] = {}
    for opportunity in opportunities:
        by_fixture.setdefault(opportunity.fixture, []).append(opportunity)

    def fixture_rank(fixture: str) -> tuple[int, int, str]:
        group = by_fixture[fixture]
        total = sum(weight_fn(item) for item in group)
        max_single = max(weight_fn(item) for item in group)
        return (-total, -max_single, fixture)

    selected_fixture = min(by_fixture.keys(), key=fixture_rank)
    group = sorted(
        by_fixture[selected_fixture],
        key=lambda item: (-weight_fn(item), item.name),
    )
    selected: list[CoverageOpportunity] = []
    used = 0
    for item in group:
        if len(selected) >= cap:
            break
        item_count = _opportunity_line_count(item)
        if selected and used + item_count > line_budget:
            break
        selected.append(item)
        used += item_count
    return selected, selected_fixture


def duplicate_incremental_candidate(existing_test_code: str, supplemental_test_code: str) -> bool:
    def _meaningful_lines(text: str) -> list[str]:
        lines = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("//"):
                continue
            if stripped.startswith("@") or stripped.startswith("class "):
                continue
            if stripped.startswith("fun ") and "(" in stripped:
                continue
            lines.append(stripped)
        return lines

    existing_lines = _meaningful_lines(existing_test_code)
    supplemental_lines = _meaningful_lines(supplemental_test_code)
    return bool(existing_lines) and existing_lines == supplemental_lines


def record_coverage_attempt(source_file_path: str, opportunity_plan, outcome: str, failure_stage: str = "") -> None:
    del source_file_path, opportunity_plan, outcome, failure_stage


def coverage_candidate_intent_issues(test_code: str, opportunity_plan) -> list[str]:
    selected = _selected_opportunities(opportunity_plan)
    if not selected:
        return []
    text = (test_code or "").strip()
    lower = text.lower()
    issues: list[str] = []
    fixture_codes = {
        "verified_menu_callback": "missing_coverage_trigger_menu",
        "verified_activity_result_callback": "missing_coverage_trigger_activity_result",
        "controlled_exception_path": "missing_coverage_trigger_exception",
        "controlled_countdown_callback": "missing_coverage_trigger_countdown",
        "robolectric_delayed_handler": "missing_coverage_trigger_countdown",
        "verified_ui_click": "missing_coverage_trigger_click",
        "verified_dialog_callback": "missing_coverage_trigger_dialog",
        "verified_observer_emission": "missing_coverage_trigger_observer",
        "verified_observer_and_click": "missing_coverage_trigger_observer_before_click",
        "verified_stream_emission": "missing_coverage_trigger_coroutine",
        "verified_coroutine_completion": "missing_coverage_trigger_coroutine",
        "verified_callback": "missing_coverage_trigger_callback",
    }
    for opportunity in selected:
        fixture = str(getattr(opportunity, "fixture", "") or "")
        code = fixture_codes.get(fixture, "")
        if not code:
            if fixture.startswith(("verified_", "controlled_")) and len(text) < 24:
                code = "missing_coverage_trigger_callback"
            if not code:
                continue
        elif fixture == "verified_menu_callback":
            if not any(token in lower for token in ("captor", "capture", "callback", "onclick", "invoke")):
                issues.append(f"{code}: {repair_intent_for(code)}")
            continue
        elif fixture == "verified_activity_result_callback":
            if not any(token in lower for token in ("captor", "capture", "callback", "activityresult", "invoke")):
                issues.append(f"{code}: {repair_intent_for(code)}")
            continue
        elif fixture == "controlled_exception_path":
            if "thenthrow" not in lower and "dothrow" not in lower:
                issues.append(f"{code}: {repair_intent_for(code)}")
            continue
        elif fixture in {"controlled_countdown_callback", "robolectric_delayed_handler"}:
            if "shadowlooper" not in lower:
                issues.append(f"{code}: {repair_intent_for(code)}")
            continue
        elif fixture == "verified_ui_click":
            if "performclick" not in lower:
                issues.append(f"{code}: {repair_intent_for(code)}")
            trigger_recipe = str(getattr(opportunity, "trigger_recipe", "") or "").lower()
            if "network" in trigger_recipe and not any(
                token in lower for token in ("setdefaultnetworkactive", "getnetworkcapabilities")
            ):
                issues.append(
                    "missing_coverage_trigger_network: network-dependent clicks require "
                    "setDefaultNetworkActive(...) or getNetworkCapabilities(...) setup before performClick()."
                )
            continue
        elif fixture == "verified_dialog_callback":
            if not any(
                token in lower
                for token in ("captor", "capture", "onuseraction", "shadowalertdialog", "getbutton", "performclick")
            ):
                issues.append(f"{code}: {repair_intent_for(code)}")
            continue
        elif fixture == "verified_observer_emission":
            if not any(token in lower for token in ("postvalue", "setvalue", ".emit(", "value =", "tryemit")):
                issues.append(f"{code}: {repair_intent_for(code)}")
            continue
        elif fixture == "verified_observer_and_click":
            has_emit = any(token in lower for token in ("postvalue", "setvalue", ".emit(", "tryemit"))
            has_click = "performclick" in lower
            if not (has_emit and has_click):
                issues.append(f"{code}: {repair_intent_for(code)}")
            continue
        elif fixture == "verified_stream_emission":
            if not any(token in lower for token in ("flowof", "emit(", "runtest", "advancetime", "advanceuntilidle")):
                issues.append(f"{code}: {repair_intent_for(code)}")
            continue
        elif fixture == "verified_coroutine_completion":
            if "runtest" not in lower or not any(token in lower for token in ("advanceuntilidle", "runcurrent")):
                issues.append(f"{code}: {repair_intent_for(code)}")
            continue
        elif fixture == "verified_callback":
            if not any(token in lower for token in ("captor", "capture", "callback", "invoke", "performclick", "postvalue", "emit(")):
                issues.append(f"{code}: {repair_intent_for(code)}")
            continue
        else:
            issues.append(f"{code}: {repair_intent_for(code)}")
    return issues


def collect_incremental_orchestration_issues(
    test_code: str,
    opportunity_plan,
    *,
    existing_test_code: str = "",
) -> list[str]:
    issues = coverage_candidate_intent_issues(test_code, opportunity_plan)
    if existing_test_code and duplicate_incremental_candidate(existing_test_code, test_code):
        issues.append(f"duplicate_incremental_candidate: {repair_intent_for('duplicate_incremental_candidate')}")
    return issues


def _blocked_report_items_for_rejected_plan(source_file_path: str, opportunity_plan, evidence: str):
    selected = _selected_opportunities(opportunity_plan)
    blocked = list((opportunity_plan or {}).get("blocked", []))
    items = [
        item_from_rejected_opportunity(source_file_path, opportunity, evidence=evidence)
        for opportunity in selected
    ]
    for opportunity in blocked:
        items.append(item_from_opportunity(source_file_path, opportunity, evidence=evidence))
    return items


def _build_coverage_repair_context(coverage_opportunity_plan, source_code: str, gap, incremental_strategy: str) -> str:
    del source_code, gap
    selected = _selected_opportunities(coverage_opportunity_plan)
    if not selected:
        return ""
    selected_lines = sorted({line for opportunity in selected for line in opportunity.lines})
    selected_branches = sorted({line for opportunity in selected for line in opportunity.branches})
    parts = [
        "### INCREMENTAL COVERAGE REPAIR",
        format_coverage_opportunity_plan(coverage_opportunity_plan, include_unselected=False),
        f"Selected lines: {_compact_int_ranges(selected_lines)}",
        f"Selected branches: {_compact_int_ranges(selected_branches)}",
    ]
    if incremental_strategy:
        parts.extend(["", incremental_strategy])
    return "\n".join(parts)


def _classify_gap_function(
    name: str,
    snippet: str,
    categories: set[str],
    analysis_report=None,
    target_lines=None,
) -> tuple[str, str, str, str, str, str]:
    lower_name = name.lower()
    lower = snippet.lower()
    target_line_set = set(target_lines or [])
    if "dispatchers.io" in lower or "dispatchers.default" in lower or "coroutinescope(" in lower:
        return (
            "blocked",
            "uncontrolled_coroutine_body",
            "The selected path runs on a hard-coded or detached coroutine dispatcher.",
            "Inject a dispatcher or scope before targeting this body for deterministic coverage.",
            "",
            _blocked_reason(BLOCKED_NEEDS_DISPATCHER_SEAM, "Production code controls the dispatcher/scope."),
        )
    if analysis_report is not None:
        blocking_coroutine_findings = analysis_report.confirmed_findings(
            "hardcoded_dispatcher",
            "detached_coroutine_scope",
        )
        if any(
            not target_line_set
            or any(finding.span.start_line <= line_no <= finding.span.end_line for line_no in target_line_set)
            for finding in blocking_coroutine_findings
        ):
            return (
                "blocked",
                "uncontrolled_coroutine_body",
                "Tree-sitter/Semgrep confirms the missed lines are inside hard-coded or detached coroutine work.",
                "Do not target unless verified context provides a dispatcher/scope seam and deterministic execution proof.",
                "",
                _blocked_reason(
                    BLOCKED_NEEDS_DISPATCHER_SEAM,
                    "Confirmed static analysis maps these missed lines to coroutine work outside test scheduler control.",
                ),
            )
    if "fragment_triggers_uncontrolled_viewmodel_io" in categories and "setonclicklistener" in lower and any(
        token in lower
        for token in (
            "continueprofileconnection",
            "disconnectprofileconnection",
            "deletedevice",
            "deleteonesharedkey",
            "deleteallsharedkey",
        )
    ):
        return (
            "blocked",
            "fragment_uncontrolled_viewmodel_io",
            "Fragment click path enters a delegated ViewModel method that is likely backed by hard-coded IO/static SDK work.",
            "Do not target as a normal coverage opportunity unless fixture context proves deterministic ViewModel state and SDK execution.",
            "",
            _blocked_reason(
                BLOCKED_FRAGMENT_UNCONTROLLED_VIEWMODEL_IO,
                "The public UI path can trigger background IO/static SDK work; immediate click tests are likely noise for missed coroutine-body lines.",
            ),
        )
    if any(
        token in lower
        for token in (
            "istestvin",
            "istestingenableddk",
            "carmanager.",
            "getvinnumber",
            "firebasecrashlytics.getinstance",
            "firebase.crashlytics",
        )
    ):
        return (
            "blocked",
            "platform_or_static_boundary",
            "Depends on global/static state or platform utility behavior.",
            "Do not target unless verified context proves the static/platform branch can be controlled.",
            "",
            _blocked_reason(
                BLOCKED_NEEDS_STATIC_WRAPPER,
                "No verified static/platform control; tests that only call the public method often execute already-covered code.",
            ),
        )
    if "startcountdown" in lower:
        return (
            "attemptable",
            "controlled_countdown_callback",
            "Static analysis places the missed lines inside the countdown completion callback reached from the public method.",
            "Invoke the public method, advance the verified clock or captured countdown callback, then assert final state.",
            "The selected Kover lines are inside the startCountdown callback body, so invoking only the public method cannot execute them.",
            "",
        )
    if (
        re.search(r"\bdeviceId\s*=\s*\"[^\"]+\"", snippet)
        and re.search(r"\.deviceId\.isNotEmpty\s*\(\s*\)", snippet)
    ):
        return (
            "blocked",
            "fixed_internal_branch_input",
            "The branch condition is fixed by a non-empty value constructed inside production code.",
            "Expose the value as an input or injectable factory before testing the opposite branch.",
            "",
            _blocked_reason(
                BLOCKED_FIXED_INTERNAL_BRANCH_INPUT,
                "Tests cannot replace the locally constructed non-empty value through the public API.",
            ),
        )
    if "storage_json_auth" in categories and any(
        token in lower_name for token in ("refreshtoken", "handleloginattempt", "token")
    ):
        return (
            "attemptable",
            "public_method",
            "AppAuth success coverage requires a verified real request/configuration graph and typed mock boundaries.",
            "Build real AppAuth request/config/token values, stub only project-owned collaborators with typed matchers, then invoke the public method.",
            "Requires a valid AppAuth request plus an authorized AuthState fixture.",
            "",
        )
    if "catch (" in lower:
        return (
            "attemptable",
            "controlled_exception_path",
            "Exception/fallback path needs controlled collaborator behavior.",
            "Attempt when a verified mock or real fixture can trigger the fallback through a public path.",
            "Requires verified throwable shape and deterministic public execution path.",
            "",
        )
    if "base_firebase_events" in categories and (
        "pushevent(" in lower
        or "pushcustomevents(" in lower
        or "setcrashlyticscollectionenable" in lower_name
    ):
        return (
            "safe",
            "buildconfig_gated_public_wrapper",
            "Public Firebase wrapper lines are reachable, but downstream firebase interactions are BuildConfig.IS_GAS_COUNTRY-gated.",
            "Call the public wrapper with verified inputs and assert only non-gated behavior/no throw; do not verify digitalKeyFirebase.pushEvents(...) or setCrashlyticsCollectionEnabled(...) unless the active variant is verified GAS-enabled.",
            "Public wrapper executes selected source lines before the BuildConfig-gated downstream call.",
            "",
        )
    if "android_fragment" in categories:
        return (
            "attemptable",
            "attached_hilt_fragment" if "hilt_fragment" in categories or "hilt_entrypoint" in categories else "public_method",
            "Fragment lifecycle coverage requires a verified attached host fixture.",
            "Attach the Fragment through its verified host, configure framework collaborators before attach, then drive the selected public lifecycle or UI behavior.",
            "Requires verified Fragment attachment and observable behavior after lifecycle execution.",
            "",
        )
    if "android_activity" in categories:
        return (
            "attemptable",
            "robolectric_activity_lifecycle",
            "Activity lifecycle coverage requires a verified Robolectric controller and manifest context.",
            "Drive the Activity through ActivityController and assert public lifecycle behavior.",
            "Requires verified Activity lifecycle execution.",
            "",
        )
    if "android_application" in categories:
        return (
            "attemptable",
            "robolectric_application_lifecycle",
            "Application lifecycle coverage requires the active variant and Robolectric application context.",
            "Create the verified application fixture and invoke only the active-variant public lifecycle path.",
            "Requires verified application and build-variant context.",
            "",
        )
    if any(token in lower_name for token in ("push", "event")):
        return (
            "safe",
            "viewmodel_sync_public",
            "Synchronous public event delegation with existing mock collaborators.",
            "Batch with other synchronous ViewModel public-method tests using the same direct-constructor fixture.",
            "Synchronous public method executes selected lines before return.",
            "",
        )
    if any(token in lower_name for token in ("set", "reset", "handle", "test", "connectedstate", "createuserdevicedata")):
        return (
            "safe",
            "viewmodel_sync_public",
            "Synchronous public state/helper method reachable without dispatcher control.",
            "Batch with other synchronous state/helper tests using compact table-style assertions where possible.",
            "Synchronous public method executes selected lines before return.",
            "",
        )
    if "viewmodel" in categories and "android_fragment" not in categories:
        return (
            "safe",
            "viewmodel_public_method",
            "Static analysis maps the selected lines to a public ViewModel entry before any detected callback, hard-coded dispatcher, or static boundary.",
            "Use direct-constructor fixture and assert stable state, events, or collaborator behavior.",
            "Static analysis maps the selected lines to this public ViewModel entry before any detected callback, hard-coded dispatcher, or static boundary.",
            "",
        )
    return (
        "safe",
        "public_method",
        "Static analysis maps the selected lines to a synchronous public-entry path without a detected callback, dispatcher, or static boundary.",
        "Generate public-contract tests with stable inputs and precise assertions.",
        "Static analysis maps the selected lines to a synchronous public-entry path without a detected callback, dispatcher, or static boundary.",
        "",
    )


def _opportunity_fingerprint(opportunity: CoverageOpportunity) -> str:
    return "|".join(
        [
            opportunity.name,
            opportunity.fixture,
            _compact_int_ranges(opportunity.lines),
            _compact_int_ranges(opportunity.branches),
        ]
    )


def _selected_opportunity_fingerprints(opportunity_plan) -> set[str]:
    selected = _selected_opportunities(opportunity_plan)
    return {_opportunity_fingerprint(opportunity) for opportunity in selected}


def _test_mentions_entry_point(existing_test_code: str, name: str) -> bool:
    if not existing_test_code or not name or name.startswith("<"):
        return False
    escaped = re.escape(name)
    patterns = [
        rf"\b{escaped}\s*\(",
        rf"fun\s+`[^`]*{escaped}[^`]*`",
        rf"fun\s+[A-Za-z_][A-Za-z0-9_]*{escaped}[A-Za-z0-9_]*\s*\(",
    ]
    return any(re.search(pattern, existing_test_code, flags=re.IGNORECASE) for pattern in patterns)


def _test_function_names(test_code: str) -> set[str]:
    if not (test_code or "").strip():
        return set()
    return {function.name for function in analyze_kotlin_code(test_code).tests.test_functions}


def _test_functions_named(test_code: str, names: set[str]) -> str:
    if not names or not (test_code or "").strip():
        return ""
    report = analyze_kotlin_code(test_code)
    lines = test_code.splitlines()
    return "\n\n".join(
        "\n".join(lines[function.span.start_line - 1 : function.span.end_line])
        for function in report.tests.test_functions
        if function.name in names
    )


def build_coverage_opportunity_plan(
    source_code: str,
    gap,
    source_categories=None,
    rejected_fingerprints=None,
    existing_test_code: str = "",
    acceptable_branch_gap_fingerprints=None,
    measured_no_delta_fingerprints=None,
    source_path: str = "",
    retry_feedback: str = "",
):
    retry_feedback = (retry_feedback or "").strip()
    if not gap:
        return {"selected_safe": [], "selected_attemptable": [], "selected_blocked": [], "alternatives": [], "blocked": []}

    categories = {str(category).lower() for category in (source_categories or [])}
    if "BaseFirebaseEvents" in (source_code or ""):
        categories.add("base_firebase_events")
    rejected_fingerprints = set(rejected_fingerprints or []) | set(measured_no_delta_fingerprints or [])
    acceptable_branch_gap_fingerprints = set(acceptable_branch_gap_fingerprints or [])
    source_lines = source_code.splitlines()
    analysis_report = analyze_kotlin_code(source_code)
    target_lines = sorted(set(gap.missed_lines + gap.partial_branch_lines))
    functions = _function_ranges_from_analysis(source_code)
    catch_ranges = _catch_ranges(source_code)
    buildconfig_ranges = _buildconfig_ranges(source_code)
    by_function = {}

    for line_no in target_lines:
        function = _function_for_line(functions, line_no)
        property_callback = next(
            (
                prop
                for prop in analysis_report.properties
                if prop.span.start_line <= line_no <= prop.span.end_line
                and "registerFor" in _snippet_for_range(source_lines, prop.span.start_line, prop.span.end_line)
            ),
            None,
        )
        if function is None and property_callback is not None:
            function = {
                "name": property_callback.name,
                "start": property_callback.span.start_line,
                "end": property_callback.span.end_line,
                "visibility": "public",
            }
            functions.append(function)
        path, path_trigger = _public_entry_path(source_code, functions, function, analysis_report) if function else ([], "")
        if property_callback is not None:
            path, path_trigger = [property_callback.name], "activity_result_callback"
        if not function or (function["visibility"] == "private" and not path):
            by_function.setdefault(
                f"<private-or-class-init:{line_no}>",
                {"function": {"name": "<private-or-class-init>", "start": line_no, "end": line_no}, "lines": [], "branches": [], "path": [], "trigger": ""},
            )
            bucket = by_function[f"<private-or-class-init:{line_no}>"]
        else:
            effective_path = path or [function["name"]]
            bucket = by_function.setdefault(
                function["name"],
                {"function": function, "lines": [], "branches": [], "path": effective_path, "trigger": path_trigger},
            )
        if line_no in gap.missed_lines:
            bucket["lines"].append(line_no)
        if line_no in gap.partial_branch_lines:
            bucket["branches"].append(line_no)

    safe = []
    attemptable = []
    blocked = []

    for name, bucket in by_function.items():
        function = bucket["function"]
        if function["name"] == "<private-or-class-init>":
            blocked.append(
                CoverageOpportunity(
                    name="Private/class-initializer gap",
                    bucket="blocked",
                    fixture="private_or_initializer",
                    entry_points=[],
                    lines=sorted(set(bucket["lines"])),
                    branches=sorted(set(bucket["branches"])),
                    reason="No public function owns these gap lines.",
                    action="Do not target directly; cover only when a selected public opportunity reaches the same lines.",
                    blocked_reason=_blocked_reason(
                        BLOCKED_PRIVATE_ONLY_PATH,
                        "No public entry point owns these gap lines.",
                    ),
                )
            )
            continue

        callback_ranges = _risky_fragment_callback_ranges(source_code, function, categories)
        exception_ranges = [
            (start, end)
            for start, end in catch_ranges
            if function["start"] <= start <= function["end"]
        ]
        split_specs = []
        remaining_lines = sorted(set(bucket["lines"]))
        remaining_branches = sorted(set(bucket["branches"]))
        coverage_path = list(bucket.get("path") or [name])
        dispatcher_lines = _lines_requiring_uncontrolled_coroutine(
            analysis_report,
            functions,
            function,
            coverage_path,
            remaining_lines,
        )
        dispatcher_branches = _lines_requiring_uncontrolled_coroutine(
            analysis_report,
            functions,
            function,
            coverage_path,
            remaining_branches,
        )
        if dispatcher_lines or dispatcher_branches:
            split_specs.append(
                (f"{name} coroutine", dispatcher_lines, dispatcher_branches, "dispatcher_prerequisite")
            )
            remaining_lines = [line for line in remaining_lines if line not in dispatcher_lines]
            remaining_branches = [line for line in remaining_branches if line not in dispatcher_branches]
        exception_lines = _lines_in_ranges(remaining_lines, exception_ranges)
        exception_branches = _lines_in_ranges(remaining_branches, exception_ranges)
        if exception_lines or exception_branches:
            split_specs.append((f"{name} exception", exception_lines, exception_branches, "exception"))
            remaining_lines = _lines_outside_ranges(remaining_lines, exception_ranges)
            remaining_branches = _lines_outside_ranges(remaining_branches, exception_ranges)
        for callback_range in callback_ranges:
            callback_lines = _lines_in_ranges(remaining_lines, [callback_range])
            callback_branches = _lines_in_ranges(remaining_branches, [callback_range])
            if callback_lines or callback_branches:
                split_specs.append((f"{name} callback", callback_lines, callback_branches, "callback"))
                remaining_lines = _lines_outside_ranges(remaining_lines, [callback_range])
                remaining_branches = _lines_outside_ranges(remaining_branches, [callback_range])
        if remaining_lines or remaining_branches:
            split_specs.append((name, remaining_lines, remaining_branches, "normal"))

        for opportunity_name, opportunity_lines, opportunity_branches, region in split_specs:
            target_lines_for_context = sorted(set(opportunity_lines + opportunity_branches))
            if function["start"] in target_lines_for_context:
                snippet = _snippet_for_range(source_lines, function["start"], function["end"])
            elif "base_firebase_events" in categories:
                snippet = _snippet_for_range(source_lines, function["start"], function["end"])
            elif region == "callback":
                snippet = _targeted_snippet_for_lines(
                    source_lines, target_lines_for_context, function, context=10
                )
            else:
                snippet = "\n".join(
                    _upstream_context_lines(source_code, target_lines_for_context)
                    + [
                        source_lines[line_no - 1]
                        for line_no in target_lines_for_context
                        if 0 < line_no <= len(source_lines)
                    ]
                )
            bucket_name, fixture, reason, action, execution_proof, blocked_reason = _classify_gap_function(
                opportunity_name,
                snippet,
                categories,
                analysis_report=analysis_report,
                target_lines=target_lines_for_context,
            )
            if region == "dispatcher_prerequisite":
                bucket_name = "blocked"
                fixture = "uncontrolled_coroutine_body"
                reason = "The selected lines require earlier hard-coded or detached coroutine work to complete."
                action = "Inject the dispatcher or scope before targeting these lines for deterministic coverage."
                execution_proof = ""
                blocked_reason = _blocked_reason(
                    BLOCKED_NEEDS_DISPATCHER_SEAM,
                    "A same-file execution prerequisite runs outside the test scheduler's control.",
                )
            function_snippet = _snippet_for_range(source_lines, function["start"], function["end"])
            fixed_else_line = next(
                (
                    line_no
                    for line_no in range(function["start"], function["end"] + 1)
                    if re.search(r"\}\s*else\s*\{", source_lines[line_no - 1])
                ),
                0,
            )
            if (
                opportunity_branches
                and re.search(r"\bdeviceId\s*=\s*\"[^\"]+\"", function_snippet)
                and re.search(r"\.deviceId\.isNotEmpty\s*\(\s*\)", function_snippet)
                and (not opportunity_lines or (fixed_else_line and min(opportunity_lines) >= fixed_else_line))
            ):
                bucket_name = "blocked"
                fixture = "fixed_internal_branch_input"
                reason = "The branch condition is fixed by a non-empty value constructed inside production code."
                action = "Expose the value as an input or injectable factory before testing the opposite branch."
                execution_proof = ""
                blocked_reason = _blocked_reason(
                    BLOCKED_FIXED_INTERNAL_BRANCH_INPUT,
                    "Tests cannot replace the locally constructed non-empty value through the public API.",
                )
            elif "startCountdown" in function_snippet and target_lines_for_context:
                bucket_name = "attemptable"
                fixture = "controlled_countdown_callback"
                reason = "Static analysis places the missed lines inside the countdown completion callback reached from the public method."
                action = "Invoke the public method, advance the verified clock or captured countdown callback, then assert final state."
                execution_proof = "The selected Kover lines are inside the startCountdown callback body, so invoking only the public method cannot execute them."
                blocked_reason = ""
            elif region == "callback" and re.search(
                r"\bval\s+\w+\s*=\s*[A-Z][A-Za-z0-9_]*(?:Manager|Client|Service)\s*\(",
                function_snippet,
            ):
                bucket_name = "blocked"
                fixture = "internally_constructed_callback_owner"
                reason = "Production constructs the callback owner internally, so tests cannot supply or fire the callback deterministically."
                action = "Inject the manager/client/service or a callback-registration wrapper before targeting this path."
                execution_proof = ""
                blocked_reason = _blocked_reason(
                    BLOCKED_NEEDS_CALLBACK_SEAM,
                    "The callback owner is created inside the public method and is not exposed to the test.",
                )
            call_lines = [
                call.span.start_line
                for call in analysis_report.calls
                if call.name == function["name"]
            ]
            buildconfig_gated = bool(target_lines_for_context) and all(
                any(start <= line_no <= end for start, end in buildconfig_ranges)
                for line_no in target_lines_for_context
            )
            helper_only_buildconfig_gated = (
                function.get("visibility") == "private"
                and bool(call_lines)
                and all(
                    any(start <= line_no <= end for start, end in buildconfig_ranges)
                    for line_no in call_lines
                )
            )
            if buildconfig_gated or helper_only_buildconfig_gated:
                bucket_name = "blocked"
                fixture = "fixed_build_variant"
                reason = "The missed path is reachable only through a fixed BuildConfig branch in the measured variant."
                action = "Run a variant with the opposite BuildConfig value or expose runtime configuration through a seam."
                execution_proof = ""
                blocked_reason = _blocked_reason(
                    BLOCKED_FIXED_BUILD_VARIANT_BRANCH,
                    "The current Kover variant fixes this BuildConfig condition before the test runs.",
                )
            elif (
                function.get("visibility") == "private"
                and opportunity_branches
                and not opportunity_lines
                and re.search(r"\(\s*activity\s+as\?\s+Activity\s*\)\?\.let", snippet)
            ):
                bucket_name = "blocked"
                fixture = "private_lifecycle_null_branch"
                reason = "The null activity branch cannot be reached through the attached Fragment lifecycle that calls this private helper."
                action = "Expose a callable seam or accept the defensive null branch as unreachable through public lifecycle behavior."
                execution_proof = ""
                blocked_reason = _blocked_reason(
                    BLOCKED_PRIVATE_ONLY_PATH,
                    "onViewCreated requires an attached Fragment, while this private branch requires activity to be null.",
                )
            elif (
                "android_fragment" in categories
                and function.get("name") in {"onStart", "onResume", "onViewCreated"}
                and opportunity_branches
                and not opportunity_lines
                and any(
                    re.search(r"\b(?:context|activity|dialog)\?\.", source_lines[line_no - 1])
                    for line_no in opportunity_branches
                    if 0 < line_no <= len(source_lines)
                )
            ):
                bucket_name = "blocked"
                fixture = "lifecycle_nullable_framework_branch"
                reason = "The remaining safe-call arm requires framework state that contradicts the lifecycle callback executing it."
                action = "Treat the defensive null arm as non-actionable unless production exposes a public state seam."
                execution_proof = ""
                blocked_reason = _blocked_reason(
                    BLOCKED_ACCEPTABLE_BRANCH_GAP,
                    "The attached lifecycle executes with the framework receiver available; the null short-circuit cannot be selected independently.",
                )
            controlled_coroutine = any(
                usage.function_name == function["name"]
                and any(usage.span.start_line <= line_no <= usage.span.end_line for line_no in target_lines_for_context)
                and usage.dispatcher in {"Main", "Unspecified"}
                for usage in analysis_report.coroutine_usages
            )
            if controlled_coroutine and bucket_name != "blocked":
                bucket_name = "attemptable"
                fixture = "verified_coroutine_completion"
                reason = "ViewModel coroutine work is reachable with a controlled Main test dispatcher."
                action = "Use runTest, install MainDispatcherRule, invoke the public method, and advanceUntilIdle()."
                execution_proof = "Concrete trigger: controlled coroutine scheduler completion."
            trigger_kind = bucket.get("trigger") or ""
            trigger_requirement = ""
            trigger_recipe = action or "direct public execution"
            if region == "exception" and bucket_name != "blocked" and function.get("visibility") == "private":
                bucket_name = "blocked"
                fixture = "untriggerable_exception_path"
                reason = "The public path reaches this helper, but the missed lines are limited to its exception/fallback path."
                action = "Add an injectable input or collaborator before testing this path."
                detail = "The throwing input or collaborator is fixed inside production code."
                if "PRIVACY_NOTICE_QR_URL" in source_code and "QRCodeWriter" in source_code:
                    detail = (
                        "PRIVACY_NOTICE_QR_URL is a production constant and QRCodeWriter is created internally; "
                        "tests cannot provide a bad string, fake writer, or throwing collaborator."
                    )
                blocked_reason = _blocked_reason(BLOCKED_NEEDS_VERIFIED_EXCEPTION_FIXTURE, detail)
                execution_proof = ""
            elif region == "exception" and bucket_name != "blocked":
                bucket_name = "attemptable"
                fixture = "controlled_exception_path"
                reason = "Public exception path can be attempted with a throwing mock collaborator."
                action = trigger_recipe = "Stub the verified collaborator to throw, call the public method, and assert fallback behavior."
                execution_proof = "Requires a mockable collaborator and verified exception type."
            elif "hardcoded_dispatcher" in trigger_kind:
                bucket_name = "blocked"
                fixture = "uncontrolled_coroutine_body"
                reason = "The public callback reaches this helper through hard-coded coroutine work."
                action = "Inject a dispatcher or scope before targeting this callback body."
                blocked_reason = _blocked_reason(BLOCKED_NEEDS_DISPATCHER_SEAM, "Production code controls the callback dispatcher/scope.")
                trigger_requirement = trigger_kind.split("|", 1)[0]
            elif (
                function["name"].lower() in {"onuseraction", "onpositiveaction", "onnegativeaction"}
                and "AlertDialogHelper" in source_code
            ):
                bucket_name, fixture = "attemptable", "verified_dialog_callback"
                reason = "Dialog callback lines require a real button click or captured callback invocation."
                action = trigger_recipe = "Open the dialog through the public entry, then click its button or invoke the captured callback."
                execution_proof = "Concrete trigger: real dialog button or captured callback invocation."
                trigger_requirement = "verified dialog callback"
            elif trigger_kind and bucket_name != "blocked":
                trigger_requirement = trigger_kind
                if trigger_kind == "setOnClickListener":
                    inferred = _infer_callback_trigger_from_path(
                        {"coverage_path": coverage_path + [trigger_kind]}, snippet, source_code
                    )
                    call_context = _targeted_snippet_for_lines(
                        source_lines,
                        call_lines,
                        {"start": 1, "end": len(source_lines)},
                        context=12,
                    )
                    if inferred and inferred[1] == "verified_menu_callback":
                        bucket_name, fixture, reason, action, execution_proof, blocked_reason = inferred
                        trigger_recipe = action
                    elif "isInternetAvailable" in call_context:
                        bucket_name, fixture = "attemptable", "verified_ui_click"
                        reason = "Private helper requires a click plus controlled Android network/browser state."
                        action = trigger_recipe = "Configure verified Robolectric network/browser state, then performClick() on the registered view."
                    else:
                        bucket_name, fixture = "safe", "verified_ui_click"
                        reason = "Private helper is reached through a verified public click listener."
                        action = trigger_recipe = "Attach the Fragment and performClick() on the registered view."
                elif trigger_kind == "setMenuItems":
                    bucket_name, fixture = "safe", "verified_menu_callback"
                    reason = "Private helper is reached through a verified CarUi toolbar menu listener."
                    action = trigger_recipe = "After Fragment attach, capture List<MenuItem> passed to setMenuItems and call performClick() on the target item."
                elif trigger_kind in {"observe", "observeForever"}:
                    if (
                        "delegated_viewmodel_fragment" in categories
                        and "verified_observer_emission" not in categories
                    ):
                        bucket_name, fixture = "blocked", "delegated_viewmodel_observer"
                        reason = "The observer uses a delegated Fragment ViewModel instance that the test cannot replace or emit through."
                        action = "Expose the ViewModel/state owner through an injectable factory or test-visible owner before targeting this observer path."
                        execution_proof = ""
                        blocked_reason = _blocked_reason(
                            BLOCKED_NEEDS_CALLBACK_SEAM,
                            "No verified test path reaches the same delegated ViewModel observable instance.",
                        )
                    else:
                        bucket_name, fixture = "attemptable", "verified_observer_emission"
                        reason = "Private helper is reached after a verified observable emission."
                        action = trigger_recipe = "Attach the Fragment and post/set the required value on the same observable instance."
                elif trigger_kind in {"onEach", "collect", "collectLatest"}:
                    bucket_name, fixture = "attemptable", "verified_stream_emission"
                    reason = "Private helper is reached after a verified finite stream emission."
                    action = trigger_recipe = "Provide a finite flow/stream, invoke the public entry under runTest, and advanceUntilIdle()."
                elif trigger_kind == "activity_result_callback":
                    bucket_name, fixture = "attemptable", "verified_activity_result_callback"
                    reason = "Property callback is reached through a controlled activity-result delivery."
                    action = trigger_recipe = "Capture or invoke the registered activity-result callback with a verified result."
                else:
                    inferred = _infer_callback_trigger_from_path(
                        {"coverage_path": coverage_path + [trigger_kind]}, snippet, source_code
                    )
                    if inferred:
                        bucket_name, fixture, reason, action, execution_proof, blocked_reason = inferred
                        trigger_recipe = action
                    else:
                        bucket_name, fixture = "blocked", "unverified_callback"
                        reason = "Static analysis found a callback path but no verified public trigger for that callback type."
                        action = "Expose or capture the callback through a testable registration seam before targeting its body."
                        execution_proof = ""
                        blocked_reason = _blocked_reason(
                            BLOCKED_NEEDS_CALLBACK_SEAM,
                            f"No deterministic trigger is known for the registered {trigger_kind} callback.",
                        )
                        trigger_recipe = ""
            elif region == "callback" and bucket_name != "blocked":
                callback_trigger = _infer_callback_trigger_from_path(
                    {"coverage_path": [name, "callback"]}, snippet, source_code
                )
                if callback_trigger:
                    bucket_name, fixture, reason, action, execution_proof, blocked_reason = callback_trigger
                    coverage_path.append("callback")
                    trigger_requirement = execution_proof or "verified callback trigger"
                    trigger_recipe = action
                    if fixture == "robolectric_delayed_handler" and any(
                        re.search(r"\bif\s*\(\s*isAdded\s*\)", source_lines[line_no - 1])
                        for line_no in opportunity_branches
                        if 0 < line_no <= len(source_lines)
                    ):
                        action = trigger_recipe = (
                            "Resume to schedule the delayed callback, detach the Fragment before advancing the looper, "
                            "then advance past the delay and assert no navigation."
                        )
                        reason = "The remaining isAdded branch requires delayed execution after Fragment detachment."
                else:
                    bucket_name, fixture = "blocked", "unverified_callback"
                    reason = "The selected lines are inside a callback body with no verified public trigger."
                    action = "Expose or capture the callback through a testable registration seam before targeting its body."
                    execution_proof = ""
                    blocked_reason = _blocked_reason(
                        BLOCKED_NEEDS_CALLBACK_SEAM,
                        "Static analysis found the callback body but could not prove how a test can invoke it.",
                    )
                    trigger_recipe = ""
            elif opportunity_branches and not opportunity_lines and bucket_name == "safe":
                fixture = "branch_probe"
                complementary_recipe = _complementary_when_branch_recipe(source_lines, function, opportunity_branches, gap)
                if complementary_recipe:
                    action = complementary_recipe
                    trigger_recipe = complementary_recipe
                    reason = "Branch-only Kover gap on a partially covered when case; target the complementary/default input instead of repeating the already-covered case label."
            if (
                bucket_name != "blocked"
                and "storage_json_auth" in categories
                and set(coverage_path) & {"handleLoginAttempt", "refreshToken"}
            ):
                bucket_name = "attemptable"
                fixture = "public_method"
                reason = "AppAuth state persistence is reached through a public token flow that requires verified typed request data."
                action = trigger_recipe = (
                    "Build a valid AppAuth response/request fixture, invoke the public token method, "
                    "and assert the project-owned storage interaction."
                )
                execution_proof = "Requires valid AppAuth request/configuration and token response data."
            delegated_observer_target = (
                "delegated_viewmodel_fragment" in categories
                and "verified_observer_emission" not in categories
                and any(
                    callback.kind in {"observe", "observeForever", "onEach", "collect", "collectLatest"}
                    and any(
                        callback.span.start_line <= line_no <= callback.span.end_line
                        for line_no in target_lines_for_context
                    )
                    for callback in analysis_report.callbacks
                )
            )
            if delegated_observer_target:
                bucket_name = "blocked"
                fixture = "delegated_viewmodel_observer"
                reason = "The selected callback runs from a delegated Fragment ViewModel stream that the test cannot control."
                action = "Expose the ViewModel/state owner through an injectable factory or test-visible owner before targeting this callback."
                execution_proof = ""
                blocked_reason = _blocked_reason(
                    BLOCKED_NEEDS_CALLBACK_SEAM,
                    "No verified test path emits through the same delegated ViewModel observable instance.",
                )
            default_parameter_names = [
                match.group(1)
                for line_no in target_lines_for_context
                if 0 < line_no <= len(source_lines)
                for match in [
                    re.search(
                        r"\b([A-Za-z_][A-Za-z0-9_]*)\s*:.*=",
                        source_lines[line_no - 1],
                    )
                ]
                if match
            ]
            source_calls = [
                _snippet_for_range(source_lines, call.span.start_line, call.span.end_line)
                for call in analysis_report.calls
                if call.name == function["name"]
                and not function["start"] <= call.span.start_line <= function["end"]
            ]
            if (
                function.get("visibility") == "private"
                and default_parameter_names
                and source_calls
                and all(
                    all(re.search(rf"\b{re.escape(parameter)}\s*=", call) for parameter in default_parameter_names)
                    for call in source_calls
                )
            ):
                bucket_name = "blocked"
                fixture = "unused_private_default_argument"
                reason = "The missed compiler branch is a private default argument, but every production call supplies that argument explicitly."
                action = "Remove the unused default or expose a public call shape that intentionally omits the argument."
                execution_proof = ""
                blocked_reason = _blocked_reason(
                    BLOCKED_FIXED_INTERNAL_BRANCH_INPUT,
                    "No public production call can select the private default-argument branch.",
                )
            if (
                bucket_name == "safe"
                and fixture in {"verified_ui_click", "verified_menu_callback"}
                and categories & {"android_fragment", "plain_fragment", "hilt_fragment", "hilt_entrypoint"}
            ):
                bucket_name = "attemptable"
                reason += " The trigger also requires a verified attached Fragment fixture."
                execution_proof = (
                    f"{execution_proof} Requires successful Fragment attachment before invoking the trigger."
                ).strip()
            opportunity = CoverageOpportunity(
                name=opportunity_name,
                bucket=bucket_name,
                fixture=fixture,
                entry_points=[coverage_path[0]],
                lines=opportunity_lines,
                branches=opportunity_branches,
                reason=reason,
                action=action,
                execution_proof=execution_proof,
                blocked_reason=blocked_reason,
                coverage_path=coverage_path,
                trigger_requirement=trigger_requirement,
                trigger_recipe=trigger_recipe,
            )
            if _opportunity_fingerprint(opportunity) in rejected_fingerprints:
                continue
            elif _opportunity_fingerprint(opportunity) in acceptable_branch_gap_fingerprints:
                opportunity.bucket = "blocked"
                opportunity.fixture = "acceptable_branch_gap"
                opportunity.reason = "Defensive branch gap already recorded; happy-path behavior is covered."
                opportunity.action = "Do not generate another duplicate branch probe for this fingerprint."
                opportunity.blocked_reason = _blocked_reason(
                    BLOCKED_ACCEPTABLE_BRANCH_GAP,
                    "acceptable_branch_gap: defensive branch gap already measured and accepted as non-actionable.",
                )
            elif _test_mentions_entry_point(existing_test_code, name):
                opportunity.reason += " Existing test code references this entry point; Kover still reports this gap and no measured no-delta rejection exists yet."
                opportunity.action += " Use branch-specific inputs/assertions instead of repeating the existing call shape."
            if bucket_name == "safe":
                if opportunity.bucket == "blocked":
                    blocked.append(opportunity)
                else:
                    safe.append(opportunity)
            elif bucket_name == "blocked":
                blocked.append(opportunity)
            else:
                if opportunity.bucket == "blocked":
                    blocked.append(opportunity)
                else:
                    attemptable.append(opportunity)

    missed_method_names = {str(method) for method in (getattr(gap, "missed_methods", []) + getattr(gap, "partial_branch_methods", []))}

    def coverage_weight(item: CoverageOpportunity) -> int:
        method_bonus = 8 if item.name in missed_method_names else 0
        line_bonus = len(set(item.lines)) * 2
        branch_bonus = len(set(item.branches))
        return method_bonus + line_bonus + branch_bonus

    safe.sort(key=lambda item: (-coverage_weight(item), item.fixture, item.name))
    attemptable.sort(key=lambda item: (-coverage_weight(item), item.fixture, item.name))
    blocked.sort(key=lambda item: (item.fixture, min(item.lines or item.branches or [999999]), item.name))

    cap = incremental_safe_cap()
    budget = incremental_line_budget()
    enabled_buckets = set(get_config().coverage_buckets)
    selected_safe, _ = _select_largest_fixture_opportunities(
        safe if "safe" in enabled_buckets else [],
        weight_fn=coverage_weight,
        cap=cap,
        line_budget=budget,
    )

    selected_attemptable = []
    if "attemptable" in enabled_buckets and attemptable and not selected_safe:
        selected_attemptable, _ = _select_largest_fixture_opportunities(
            attemptable,
            weight_fn=coverage_weight,
            cap=cap,
            line_budget=budget,
        )
    selected_blocked = []
    if "blocked" in enabled_buckets and blocked and not selected_safe and not selected_attemptable:
        selected_blocked, _ = _select_largest_fixture_opportunities(
            blocked,
            weight_fn=coverage_weight,
            cap=cap,
            line_budget=budget,
        )
    remaining_attemptable = [opportunity for opportunity in attemptable if opportunity not in selected_attemptable]

    alternatives = [opportunity for opportunity in safe if opportunity not in selected_safe] + remaining_attemptable
    plan = {
        "selected_safe": selected_safe,
        "selected_attemptable": selected_attemptable,
        "selected_blocked": selected_blocked,
        "alternatives": alternatives,
        "blocked": blocked,
    }
    if retry_feedback:
        plan["retry_feedback"] = retry_feedback
    return plan


def format_coverage_opportunity_plan(opportunity_plan, include_unselected: bool = True) -> str:
    if not opportunity_plan:
        return ""

    def format_opportunity(index: int, opportunity: CoverageOpportunity) -> str:
        entry_points = ", ".join(opportunity.entry_points) or "public entry point required"
        return "\n".join(
            [
                f"{index}. {opportunity.name}",
                f"   Bucket: {opportunity.bucket}",
                f"   Fixture: {opportunity.fixture}",
                f"   Entry points: {entry_points}",
                f"   Public entry coverage path: {' -> '.join(opportunity.coverage_path or opportunity.entry_points)}",
                f"   Trigger requirement: {opportunity.trigger_requirement or 'none'}",
                f"   Trigger recipe: {opportunity.trigger_recipe or 'direct public execution'}",
                f"   Missed lines: {_compact_int_ranges(opportunity.lines)}",
                f"   Missed branches: {_compact_int_ranges(opportunity.branches)}",
                f"   Reason: {opportunity.reason}",
                f"   Execution proof: {opportunity.execution_proof or 'none'}",
                f"   Blocked reason: {opportunity.blocked_reason or 'none'}",
                f"   Action: {opportunity.action}",
            ]
        )

    selected_safe = opportunity_plan.get("selected_safe", [])
    selected_attemptable = opportunity_plan.get("selected_attemptable", [])
    selected_blocked = opportunity_plan.get("selected_blocked", [])
    alternatives = opportunity_plan.get("alternatives", [])
    blocked = opportunity_plan.get("blocked", [])
    lines = [
        "### COVERAGE OPPORTUNITY PLAN",
        "Selected safe opportunities for this generation:",
        *(format_opportunity(index, opportunity) for index, opportunity in enumerate(selected_safe, 1)),
    ]
    if not selected_safe:
        lines.append("No safe opportunity selected.")
    if selected_attemptable:
        lines.extend(
            [
                "",
                "Selected controlled-risk opportunities:",
                *(format_opportunity(index, opportunity) for index, opportunity in enumerate(selected_attemptable, 1)),
            ]
        )
    if selected_blocked:
        lines.extend(
            [
                "",
                "Selected blocked opportunities by explicit CLI override:",
                *(format_opportunity(index, opportunity) for index, opportunity in enumerate(selected_blocked, 1)),
            ]
        )
    if include_unselected and alternatives:
        lines.extend(
            [
                "",
                "Alternative opportunities not selected for this generation:",
                *(format_opportunity(index, opportunity) for index, opportunity in enumerate(alternatives[:8], 1)),
            ]
        )
    if include_unselected and blocked:
        lines.extend(
            [
                "",
                "Blocked opportunities:",
                *(format_opportunity(index, opportunity) for index, opportunity in enumerate(blocked[:12], 1)),
            ]
        )
    retry_feedback = (opportunity_plan.get("retry_feedback") or "").strip()
    if retry_feedback:
        lines.extend(["", "### RETRY FEEDBACK FROM PREVIOUS ATTEMPT", retry_feedback])
    return "\n".join(lines)


def coverage_acceptance_diagnostics(before, after) -> str:
    if before is None or after is None:
        return "Coverage acceptance unavailable: missing Kover gap data."
    resolved_lines = sorted(set(before.missed_lines) - set(after.missed_lines))
    new_missed_lines = sorted(set(after.missed_lines) - set(before.missed_lines))
    resolved_branches = sorted(set(before.partial_branch_lines) - set(after.partial_branch_lines))
    new_missed_branches = sorted(set(after.partial_branch_lines) - set(before.partial_branch_lines))
    resolved_methods = sorted(set(before.missed_methods + before.partial_branch_methods) - set(after.missed_methods + after.partial_branch_methods))
    new_missed_methods = sorted(set(after.missed_methods + after.partial_branch_methods) - set(before.missed_methods + before.partial_branch_methods))
    before_line_missed, before_line_covered = before.line_coverage
    after_line_missed, after_line_covered = after.line_coverage
    before_branch_missed, before_branch_covered = before.branch_coverage
    after_branch_missed, after_branch_covered = after.branch_coverage
    line_improved = bool(resolved_lines or after_line_missed < before_line_missed or after_line_covered > before_line_covered)
    branch_improved = bool(resolved_branches or after_branch_missed < before_branch_missed or after_branch_covered > before_branch_covered)
    method_improved = bool(resolved_methods)
    has_regression = bool(
        new_missed_lines
        or new_missed_branches
        or new_missed_methods
        or after_line_missed > before_line_missed
        or after_branch_missed > before_branch_missed
        or after_line_covered < before_line_covered
        or after_branch_covered < before_branch_covered
    )
    return "\n".join(
        [
            "### COVERAGE ACCEPTANCE DIAGNOSTICS",
            f"Line improved: {line_improved}",
            f"Branch improved: {branch_improved}",
            f"Method improved: {method_improved}",
            f"Only already-covered code likely changed: {not (line_improved or branch_improved or method_improved)}",
            f"Coverage regression: {has_regression}",
            f"Resolved line count: {len(resolved_lines)}",
            f"New missed line count: {len(new_missed_lines)}",
            f"Resolved branch count: {len(resolved_branches)}",
            f"New missed branch count: {len(new_missed_branches)}",
            f"Resolved method count: {len(resolved_methods)}",
            f"New missed method count: {len(new_missed_methods)}",
            "Acceptance rule: accept only when line/branch/method coverage improves without new missed lines, branches, methods, or counter regression.",
        ]
    )


def selected_opportunity_lines(opportunity_plan) -> tuple[set[int], set[int]]:
    selected = _selected_opportunities(opportunity_plan)
    selected_lines: set[int] = set()
    selected_branches: set[int] = set()
    for opportunity in selected:
        selected_lines.update(opportunity.lines)
        selected_branches.update(opportunity.branches)
    return selected_lines, selected_branches


def opportunity_acceptance_diagnostics(opportunity_plan, before, after) -> str:
    selected_lines, selected_branches = selected_opportunity_lines(opportunity_plan)
    if before is None or after is None:
        return "Selected opportunity acceptance unavailable: missing Kover gap data."
    resolved_lines = sorted((set(before.missed_lines) - set(after.missed_lines)) & selected_lines)
    resolved_branches = sorted((set(before.partial_branch_lines) - set(after.partial_branch_lines)) & selected_branches)
    return "\n".join(
        [
            "### SELECTED OPPORTUNITY ACCEPTANCE",
            f"Selected opportunity lines: {_compact_int_ranges(sorted(selected_lines))}",
            f"Selected opportunity branches: {_compact_int_ranges(sorted(selected_branches))}",
            f"Resolved selected lines: {_compact_int_ranges(resolved_lines)}",
            f"Resolved selected branches: {_compact_int_ranges(resolved_branches)}",
            f"Selected opportunity improved: {bool(resolved_lines or resolved_branches)}",
        ]
    )


def coverage_candidate_acceptance_decision(opportunity_plan, before, after, source_code: str) -> CoverageAcceptanceDecision:
    selected_lines, selected_branches = selected_opportunity_lines(opportunity_plan)
    selected_names = {
        opportunity.name
        for opportunity in _selected_opportunities(opportunity_plan)
    }
    resolved_lines = sorted(set(before.missed_lines) - set(after.missed_lines))
    new_missed_lines = sorted(set(after.missed_lines) - set(before.missed_lines))
    resolved_methods = sorted(set(before.missed_methods + before.partial_branch_methods) - set(after.missed_methods + after.partial_branch_methods))
    new_missed_methods = sorted(set(after.missed_methods + after.partial_branch_methods) - set(before.missed_methods + before.partial_branch_methods))
    resolved_selected_lines = sorted(set(resolved_lines) & selected_lines)
    resolved_unselected_lines = sorted(set(resolved_lines) - selected_lines)

    owner_by_line = _line_owner_map(source_code)
    resolved_method_set = set(resolved_methods)
    newly_exposed_lines = []
    true_regression_lines = []
    for line_no in new_missed_lines:
        owner = owner_by_line.get(line_no)
        if owner and (
            owner in selected_names
            or owner in resolved_method_set
            or any(line in selected_lines for line in resolved_lines if owner_by_line.get(line) == owner)
        ):
            newly_exposed_lines.append(line_no)
        else:
            true_regression_lines.append(line_no)

    before_line_missed, before_line_covered = before.line_coverage
    after_line_missed, after_line_covered = after.line_coverage
    before_branch_missed, before_branch_covered = before.branch_coverage
    after_branch_missed, after_branch_covered = after.branch_coverage
    counter_regressed = bool(
        after_line_missed > before_line_missed
        or after_branch_missed > before_branch_missed
        or after_line_covered < before_line_covered
        or after_branch_covered < before_branch_covered
    )
    resolved_selected_branches = (
        set(before.partial_branch_lines) - set(after.partial_branch_lines)
    ) & selected_branches
    selected_method_improved = bool(set(resolved_methods) & selected_names)
    improved = bool(
        resolved_selected_lines
        or resolved_selected_branches
        or _selected_branch_mb_progress(before, after, selected_branches)
        or selected_method_improved
    )
    accepted = bool(improved and not true_regression_lines and not new_missed_methods and not counter_regressed)
    if accepted and resolved_selected_lines:
        reason = "selected opportunities improved"
    elif accepted:
        reason = "beneficial coverage expansion improved unselected valid opportunities"
    elif not improved:
        reason = "no Kover improvement"
    elif true_regression_lines or new_missed_methods or counter_regressed:
        reason = "true coverage regression"
    else:
        reason = "coverage candidate rejected"
    return CoverageAcceptanceDecision(
        accepted=accepted,
        reason=reason,
        resolved_selected_lines=resolved_selected_lines,
        resolved_unselected_lines=resolved_unselected_lines,
        newly_exposed_lines=sorted(newly_exposed_lines),
        true_regression_lines=sorted(true_regression_lines),
        resolved_methods=resolved_methods,
        new_missed_methods=new_missed_methods,
    )


def coverage_candidate_acceptance_diagnostics(decision: CoverageAcceptanceDecision) -> str:
    return "\n".join(
        [
            "### COVERAGE CANDIDATE ACCEPTANCE DECISION",
            f"Accepted: {decision.accepted}",
            f"Reason: {decision.reason}",
            f"Resolved selected opportunity lines: {_compact_int_ranges(decision.resolved_selected_lines)}",
            f"Resolved unselected but valid opportunity lines: {_compact_int_ranges(decision.resolved_unselected_lines)}",
            f"Newly exposed missed lines: {_compact_int_ranges(decision.newly_exposed_lines)}",
            f"True regression missed lines: {_compact_int_ranges(decision.true_regression_lines)}",
            f"Resolved methods: {', '.join(decision.resolved_methods) if decision.resolved_methods else 'none'}",
            f"New missed methods: {', '.join(decision.new_missed_methods) if decision.new_missed_methods else 'none'}",
        ]
    )

def filter_source_risk_context_for_gap(source_risk_context: str, gap) -> str:
    if not source_risk_context or not gap:
        return source_risk_context or ""

    target_lines = set(gap.missed_lines + gap.partial_branch_lines)
    if not target_lines:
        return source_risk_context

    kept_blocks = []
    current_block = []
    current_lines = set()

    for line in source_risk_context.splitlines():
        if re.match(r"^\d+\.\s+Risk:", line):
            if current_block and (not current_lines or current_lines & target_lines):
                kept_blocks.append("\n".join(current_block))
            current_block = [line]
            current_lines = set()
            continue

        if current_block:
            current_block.append(line)
        match = re.search(r"Evidence:\s+Line\s+(\d+):", line)
        if match:
            current_lines.add(int(match.group(1)))

    if current_block and (not current_lines or current_lines & target_lines):
        kept_blocks.append("\n".join(current_block))

    if kept_blocks:
        return "\n".join(["### GAP-RELEVANT STATIC BUG-HUNTING TARGETS", *kept_blocks])

    return (
        "### GAP-RELEVANT STATIC BUG-HUNTING TARGETS\n"
        "No static bug-hunting target directly overlaps the current Kover gap. "
        "Focus on the Kover-listed missed lines and branch probes."
    )

def build_incremental_coverage_strategy(
    class_name: str,
    source_code: str,
    existing_test_code: str,
    gap,
    incremental_rounds: int = 3,
    opportunity_plan=None,
) -> str:
    if not gap:
        return ""

    source_lines = source_code.splitlines()
    static_report = analyze_kotlin_code(source_code)
    confirmed_coroutine_spans = tuple(
        finding.span
        for finding in static_report.confirmed_findings("hardcoded_dispatcher", "detached_coroutine_scope")
    )
    selected_lines, selected_branches = selected_opportunity_lines(opportunity_plan or {})
    target_lines = sorted(selected_lines | selected_branches) or sorted(
        set(gap.missed_lines + gap.partial_branch_lines)
    )
    branch_only = gap.line_coverage[0] == 0 and gap.branch_coverage[0] > 0
    selected_blocked = bool((opportunity_plan or {}).get("selected_blocked"))
    bullets = [
        "### DETERMINISTIC INCREMENTAL COVERAGE STRATEGY",
        f"- Target class: {class_name}",
        f"- Incremental attempt budget: {incremental_rounds}. This controls retry count only, not how many uncovered gaps are selected.",
        "- Use the COVERAGE OPPORTUNITY PLAN as the primary targeting plan.",
        "- Generate tests only when the selected missed lines/branches will execute, not merely when the public method can be called.",
        "- Cover all selected safe opportunities that share the same fixture and fit the verified context.",
        "- Prioritize the largest selected opportunities (most missed lines/branches) in this round before smaller gaps in the same fixture group.",
        "- If selected controlled-risk opportunities are listed, attempt them only when the opportunity includes execution proof for the missed lines.",
        "- Compatible opportunities share the same fixture, runner, dispatcher/lifecycle setup, dependency surface, and public-test strategy.",
        "- Use compact table-style tests when multiple public methods or states share the same assertion pattern.",
        "- Do not mix unrelated or incompatible opportunities just to increase coverage quantity.",
        (
            "- The CLI explicitly selected blocked opportunities; attempt only their documented public path without reflection, private calls, invented seams, or production edits."
            if selected_blocked
            else "- Do not generate no-op tests for blocked opportunities; choose selected safe or controlled-risk opportunities only."
        ),
        "- Do not cover missed lines by calling delegated singleton helpers (getInstance companions, shared dialog utilities) directly from tests; exercise the target class public entry path that reaches those lines.",
        "- Immediate pre-launch state assertions are noise when Kover target lines are inside an uncontrolled coroutine body.",
        "- Cover the maximum selected reachable opportunities in this generation without speculative private access or unsupported framework setup.",
    ]

    if branch_only:
        bullets.extend(
            [
                "- This is a branch-only coverage gap: production line coverage is already complete.",
                "- Add compact branch-probe tests that complement existing success-path coverage.",
                "- Generate compact branch-probe supplemental tests that can later be merged only when Kover improves.",
            ]
        )
    else:
        bullets.append("- Target only the listed missed lines and missed branch lines.")

    if "dataAssertNoErrors" in source_code:
        bullets.extend(
            [
                "- Apollo dataAssertNoErrors rule:",
                "  - ApolloResponse data = null throws ApolloException before nullable result mapping executes.",
                "  - Use data=null transports only for exact thrown-exception assertions.",
                "  - To cover result?.mapper safe-call lines, provide verified non-null Operation.Data whose selected operation field is null.",
                "  - Target result?.mapper safe-call gaps when a verified non-null generated Data/null-field fixture is already available in existing helpers.",
            ]
        )

    const_false_flags = [
        name
        for name in ("isFailedDependencyTestEnabled", "isTestingEnabledDK")
        if name in source_code
    ]
    if const_false_flags:
        bullets.extend(
            [
                "- Compile-time constant test-flag rule:",
                "  - Project context defines "
                + ", ".join(const_false_flags)
                + " as const val false.",
                "  - Do not force these flags true with Mockito MockedStatic, MockK, reflection, or assignment.",
                "  - If a missed line is the if-condition itself, a normal false-path public call can cover that condition.",
                "  - If a missed line is only inside the true branch guarded by one of these flags, select another reachable false-path opportunity and report the true-only line as blocked only when no production seam exists.",
            ]
        )

    safe_call_lines = []
    forced_unwrap_lines = []
    when_case_lines = []
    cast_lines = []
    catch_lines = []
    resource_fallback_lines = []
    shared_pref_null_fallback_lines = []
    shared_pref_log_length_lines = []
    dialog_callback_lines = []
    hard_coded_coroutine_lines = []

    for line_no in target_lines:
        if not (1 <= line_no <= len(source_lines)):
            continue
        text = source_lines[line_no - 1].strip()
        if "?." in text:
            safe_call_lines.append((line_no, text))
        if "!!" in text:
            forced_unwrap_lines.append((line_no, text))
        if "->" in text:
            when_case_lines.append((line_no, text))
        if " as " in f" {text} ":
            cast_lines.append((line_no, text))
        nearby_start = max(0, line_no - 4)
        nearby_end = min(len(source_lines), line_no + 2)
        nearby = "\n".join(source_lines[nearby_start:nearby_end])
        wider_nearby_start = max(0, line_no - 10)
        wider_nearby_end = min(len(source_lines), line_no + 8)
        wider_nearby = "\n".join(source_lines[wider_nearby_start:wider_nearby_end])
        if "catch" in nearby:
            catch_lines.append((line_no, text))
        if "setBackgroundResource" in nearby:
            resource_fallback_lines.append((line_no, text))
        if "getString(" in nearby and "?:" in nearby:
            shared_pref_null_fallback_lines.append((line_no, text))
        if "oldData?.length!!" in nearby or "getLog()" in nearby and "length" in nearby:
            shared_pref_log_length_lines.append((line_no, text))
        if any(
            token in wider_nearby
            for token in [
                "onPositiveAction",
                "onNegativeAction",
                "onUserAction",
                "setButton(",
                "setOnClickListener",
                "FullScreenDialogFragment.newInstance",
                "AlertDialogHelper",
            ]
        ):
            dialog_callback_lines.append((line_no, text))
        if any(span.start_line <= line_no <= span.end_line for span in confirmed_coroutine_spans):
            hard_coded_coroutine_lines.append((line_no, text))

    if safe_call_lines:
        bullets.append(
            "- Safe-call branch gaps: cover null receiver/null returned-view paths with verified fixture setup."
        )
        for line_no, text in safe_call_lines[:12]:
            bullets.append(f"  line {line_no}: {text}")

    if forced_unwrap_lines:
        bullets.append(
            """
            - Forced-unwrap branch gaps are eligible only when their public execution path is deterministic with verified test-controlled dispatching.
            - When the path uses hard-coded Dispatchers.IO, an uncontrolled CoroutineScope, real delay, or unverified framework/static behavior:
            - attempt only stable public trigger, immediate state/collaborator effects, or pre-launch behavior;
            - do not generate an exception test solely for Kover;
            - select another deterministic public opportunity when no stable trigger exists.
            """
        )
        for line_no, text in forced_unwrap_lines[:4]:
            bullets.append(f"  line {line_no}: {text}")

    if when_case_lines:
        bullets.append(
            "- When/case branch gaps: follow the selected trigger recipe. For complementary/default probes, use an input different from the listed case labels; do not repeat an already-covered case label."
        )
        for line_no, text in when_case_lines[:8]:
            bullets.append(f"  line {line_no}: {text}")

    if cast_lines:
        bullets.append(
            "- Cast branch gaps: cover only when the cast line itself is in the Kover gap; use exact exception assertions for invalid public setup."
        )

    if catch_lines:
        bullets.append(
            "- Catch/fallback gaps: trigger the smallest public or controlled setup that enters the catch block, then assert stable observable structure or no exception."
        )
        for line_no, text in catch_lines[:6]:
            bullets.append(f"  line {line_no}: {text}")

    if dialog_callback_lines:
        bullets.extend(
            [
                "- Dialog/callback body gaps:",
                "  - A test that only verifies a dialog is shown does not cover missed lines inside the dialog's callback/lambda.",
                "  - Do not call delegated singleton helpers (getInstance companions, AlertDialogHelper, shared dialog utilities) directly from the test; trigger the target class public entry (toolbar/menu click, performClick, lifecycle host) that reaches the missed lines.",
                "  - Trigger the real callback path through a Robolectric AlertDialog button, displayed DialogFragment button click, or verified public callback storage before asserting collaborator/navigation effects.",
                "  - If the callback cannot be triggered through verified public UI or callback storage, treat that opportunity as blocked and select another reachable opportunity.",
            ]
        )
        for line_no, text in dialog_callback_lines[:8]:
            bullets.append(f"  line {line_no}: {text}")

    if hard_coded_coroutine_lines:
        bullets.extend(
            [
                "- Hard-coded dispatcher/coroutine gaps:",
                "  - Source launches work on Dispatchers.IO or standalone CoroutineScope, so runTest, runCurrent, and advanceUntilIdle do not prove final coroutine state unless the source exposes a dispatcher seam.",
                "  - Treat final-state coroutine-body assertions as blocked unless a verified source seam gives the test control over that dispatcher/scheduler.",
                "  - A test that only asserts the pre-launch state does not cover missed lines inside the launched coroutine and should not be generated for those gaps.",
                "  - Prefer synchronous public methods, immediate state setters, direct pure branch methods, and collaborator interactions that happen before or outside the hard-coded IO launch.",
                "  - Do not add another NotSet/state-emission test if existing tests already cover only the pre-launch effect and Kover still reports the coroutine body as missed.",
            ]
        )
        for line_no, text in hard_coded_coroutine_lines[:8]:
            bullets.append(f"  line {line_no}: {text}")

    if resource_fallback_lines:
        bullets.extend(
            [
                "- Android resource fallback gap:",
                "  - Use a guaranteed invalid drawable id such as -1 to trigger ImageView.setBackgroundResource failure; id 0 may silently clear the background and miss the catch.",
                "  - When the fallback helper is private and no public API can pass an invalid icon id, use one small reflection helper with the exact JVM signature.",
                "  - For Kotlin function parameters use kotlin.jvm.functions.Function0::class.java and Int::class.javaPrimitiveType.",
                "  - Assert stable outcomes such as parent.childCount, item id/title, and successful completion; prefer structural assertions over drawable internals such as constantState.resourceId.",
            ]
        )

    if shared_pref_null_fallback_lines:
        bullets.extend(
            [
                "- SharedPreferences null-Elvis fallback gap:",
                "  - Real Android/Robolectric SharedPreferences normally returns the provided default when a key is absent, and putString(key, null) is not a reliable Kover probe for getString(key, default) ?: fallback.",
                "  - Cover this gap with an existing verified seam that can make SharedPreferences.getString return null for that exact key.",
                "  - When the source constructs PreferenceManager/default preferences internally and no seam exists, treat the Elvis fallback as unreachable through stable public tests.",
            ]
        )
        for line_no, text in shared_pref_null_fallback_lines[:6]:
            bullets.append(f"  line {line_no}: {text}")

    if shared_pref_log_length_lines:
        bullets.extend(
            [
                "- SharedPreferences log-length branch gap:",
                "  - Arrange saveLog/getLog through context.getSharedPreferences(UserPreferencesSource.PREF_NAME, Context.MODE_PRIVATE), because default preferences do not drive the private prefManger.",
                "  - Use a short existing KEY_LOG value for the <= 30000 path and a value longer than 30000 chars for the truncation path.",
                "  - Assert stable observable output and keep the candidate when the Kover delta proves the branch moved.",
            ]
        )
        for line_no, text in shared_pref_log_length_lines[:6]:
            bullets.append(f"  line {line_no}: {text}")

    if class_name == "SideMenuUtils":
        missing_side_menu_cases = []
        for _, text in when_case_lines:
            match = re.search(r"SideMenuItem\.([A-Z_]+)\.id", text)
            if match:
                missing_side_menu_cases.append(match.group(1))
        missing_case_text = ", ".join(missing_side_menu_cases) or "the listed SideMenuItem cases"
        bullets.extend(
            [
                "- SideMenuUtils-specific plan:",
                "  1. Keep the existing real themed Robolectric TestMainActivity fixture.",
                "  2. Add one PS4 setup test where a mocked delegate returns null for drawer/navigation getters; assert NullPointerException only for the forced navigationView unwrap path.",
                "  3. Add one PS4 reset test where the delegate is null or mocked getters return null; assert it completes without changing real views.",
                f"  4. Add one compact navigation test that calls only these missing case labels with a null delegate: {missing_case_text}.",
                "  5. Reuse deviceContext/buildActivity helpers and extend the existing SideMenuUtils test class when it can express the branch probes.",
            ]
        )

    if class_name == "SideMenuRepository" and resource_fallback_lines:
        bullets.extend(
            [
                "- SideMenuRepository-specific plan:",
                "  1. Prefer patching the existing SideMenuRepositoryTest with exactly one compact fallback test.",
                "  2. Reflectively call private createCustomMenuItem with LinearLayout, SideMenuModel(id=999, title=\"Invalid\", iconResId=-1), selected id -1, and an empty Function0 lambda.",
                "  3. Assert that one child is added and that the child has the expected id/title.",
                "  4. Keep this candidate when Kover improves; otherwise restore the previous file before trying a different reachable gap.",
            ]
        )

    bullets.extend(
        [
            "- Acceptance: after Gradle/Kover, at least one listed missed line, branch line, method, or coverage counter must improve for this target.",
            "- Merge candidate tests that pass Gradle and reduce the Kover gap.",
            "- Select gaps that are reachable through stable public tests and verified setup.",
        ]
    )

    return "\n".join(bullets)

async def run_gradle_and_parse_gap(
    mcp_tools: LocalMcpTools,
    project_root: str,
    gradle_offline: bool,
    gradle_tasks: list,
    module_dir: str,
    source_file_path: str,
    source_code: str,
    log_title: str,
    verified_gradle_output: str | None = None,
):
    gradle_output = verified_gradle_output or await run_gradle_with_heartbeat(
        mcp_tools,
        project_root,
        offline=gradle_offline,
        tasks=gradle_tasks,
    )
    if verified_gradle_output and relevant_gradle_tasks_executed(gradle_output):
        log_message("✅ Reusing fresh Gradle/Kover output from repair verification.", category="success")
    else:
        log_block(log_title, gradle_output or "No Gradle output captured.", category="gradle", console=True)
    if not is_gradle_success(gradle_output):
        return False, None, gradle_output
    if not relevant_gradle_tasks_executed(gradle_output):
        log_message(
            "⚠️ No test/Kover task executed freshly; rerunning once with --rerun-tasks before coverage acceptance.",
            category="warning",
        )
        gradle_output = await run_gradle_with_heartbeat(
            mcp_tools,
            project_root,
            offline=gradle_offline,
            tasks=gradle_tasks,
            rerun_tasks=True,
        )
        log_block(
            f"{log_title} (--rerun-tasks)",
            gradle_output or "No Gradle output captured.",
            category="gradle",
            console=True,
        )
        if not is_gradle_success(gradle_output):
            return False, None, gradle_output
    return True, await parse_latest_coverage_gap(
        module_dir,
        source_file_path,
        source_code,
        project_root=project_root,
        gradle_tasks=gradle_tasks,
    ), gradle_output

async def _run_incremental_coverage_generation_impl(
    mcp_tools: LocalMcpTools,
    class_name: str,
    source_code: str,
    source_file_path: str,
    output_file_path: str,
    project_root: str,
    gradle_offline: bool,
    gradle_tasks: list,
    dependency_context: str,
    source_risk_context: str,
    source_categories=None,
    memory_context: str = "",
    incremental_rounds: int = 3,
    _rejected_opportunity_fingerprints=None,
) -> bool | None:
    source_categories = frozenset(source_categories or source_rule_categories(source_code))
    source_rejection_key = os.path.abspath(source_file_path)
    rejected_opportunity_fingerprints = _rejected_opportunity_fingerprints
    if rejected_opportunity_fingerprints is None:
        rejected_opportunity_fingerprints = _REJECTED_INCREMENTAL_OPPORTUNITY_FINGERPRINTS.setdefault(
            source_rejection_key,
            set(),
        )
    anchor_test_exists = os.path.exists(output_file_path)
    indirect_coverage_output_file_path = derive_indirect_coverage_test_path(output_file_path)
    if not anchor_test_exists and os.path.exists(indirect_coverage_output_file_path):
        output_file_path = indirect_coverage_output_file_path
        anchor_test_exists = True

    log_section(f"INCREMENTAL COVERAGE MODE: {class_name}", category="info")
    if anchor_test_exists:
        log_message(
            "Existing test file detected. Running Gradle/Kover first to find uncovered lines/branches.",
            category="gradle",
        )
    else:
        log_message(
            "No direct existing test file was found. Running Gradle/Kover to detect whether this source is already "
            "covered indirectly by other tests before choosing full generation. If Kover shows existing indirect "
            f"coverage with gaps, a standalone incremental test will be saved as {indirect_coverage_output_file_path}.",
            category="gradle",
        )

    baseline_gradle_output = await run_gradle_with_heartbeat(
        mcp_tools,
        project_root,
        offline=gradle_offline,
        tasks=gradle_tasks,
    )
    log_block(
        "BASELINE GRADLE/KOVER OUTPUT BEFORE INCREMENTAL GENERATION",
        baseline_gradle_output or "No Gradle output captured.",
        category="gradle",
        console=True,
    )

    if not is_gradle_success(baseline_gradle_output):
        if not anchor_test_exists:
            log_message(
                "⚠️ Baseline Gradle/Kover failed and there is no direct test file to repair; falling back to full generation.",
                category="warning",
            )
            return None

        log_message(
            "⚠️ Existing tests or requested Kover tasks failed before incremental generation. "
            "Repairing the existing test file before attempting coverage additions.",
            category="warning",
        )
        baseline_repair_success = await verify_and_repair_test_with_mcp(
            mcp_tools=mcp_tools,
            class_name=class_name,
            source_code=source_code,
            output_file_path=output_file_path,
            project_root=project_root,
            gradle_offline=gradle_offline,
            gradle_tasks=gradle_tasks,
            source_risk_context=(
                source_risk_context
                + "\n\nBaseline existing tests failed before incremental coverage generation. "
                "Repair the existing test file first; preserve existing coverage and keep this repair pass focused on the baseline failure."
            ),
        )
        if not baseline_repair_success:
            log_message(
            "❌ Baseline existing-test repair failed. Leaving the existing test file in its latest repaired state and stopping incremental coverage.",
                category="error",
            )
            return False

        baseline_gradle_output = await run_gradle_with_heartbeat(
            mcp_tools,
            project_root,
            offline=gradle_offline,
            tasks=gradle_tasks,
        )
        log_block(
            "BASELINE GRADLE/KOVER OUTPUT AFTER EXISTING TEST REPAIR",
            baseline_gradle_output or "No Gradle output captured.",
            category="gradle",
            console=True,
        )
        if not is_gradle_success(baseline_gradle_output):
            log_message(
                "❌ Existing tests still fail after baseline repair. Stopping before coverage additions.",
                category="error",
            )
            return False

    module_dir = find_owning_module_dir(project_root, source_file_path)
    kover_xml = find_latest_kover_xml_for_context(project_root, gradle_tasks, module_dir)
    if not kover_xml:
        searched_roots = ", ".join(
            os.path.join(candidate, "build", "reports", "kover")
            for candidate in kover_report_module_dirs(project_root, gradle_tasks, module_dir)
        )
        write_blocked_coverage_report(
            source_file_path,
            [
                item_from_stop(
                    source_file_path,
                    "Kover report discovery",
                    "android_environment_unavailable: No Kover XML report was found after coverage execution.",
                    searched_roots,
                )
            ],
        )
        log_message(
            f"⚠️ No Kover XML report found under searched Kover report roots: {searched_roots}. "
            "Leaving the existing test file unchanged. Use --disable-incremental-coverage to force full-file regeneration.",
            category="warning",
        )
        return False

    gap = parse_kover_gap(kover_xml, source_file_path, source_code)
    if not gap:
        if anchor_test_exists:
            log_message(
                f"⚠️ Kover XML did not contain coverage data for {os.path.basename(source_file_path)}. "
                "Leaving the existing test file unchanged. Use --disable-incremental-coverage to force full-file regeneration.",
                category="warning",
            )
            return False

        log_message(
            f"ℹ️ Kover XML has no existing coverage entry for {os.path.basename(source_file_path)}; using full generation.",
            category="info",
        )
        return None

    if not anchor_test_exists and not gap.has_any_coverage():
        log_message(
            #f"ℹ️ Kover has an entry for {os.path.basename(source_file_path)} but no covered lines/branches. "
            "No direct or indirect coverage exists yet; using full direct test generation.",
            category="info",
        )
        return None

    existing_test_code = await mcp_tools.read_file(output_file_path) if anchor_test_exists else ""
    source_key = os.path.abspath(source_file_path)
    coverage_opportunity_plan = build_coverage_opportunity_plan(
        source_code=source_code,
        gap=gap,
        source_categories=source_categories,
        rejected_fingerprints=rejected_opportunity_fingerprints,
        existing_test_code=existing_test_code,
        source_path=source_file_path,
        retry_feedback=_INCREMENTAL_RETRY_FEEDBACK.get(source_key, ""),
    )
    selected_opportunity_context = format_coverage_opportunity_plan(
        coverage_opportunity_plan,
        include_unselected=False,
    )
    if not _selected_opportunities(coverage_opportunity_plan):
        write_blocked_coverage_report(
            source_file_path,
            [
                item_from_opportunity(
                    source_file_path,
                    opportunity,
                    evidence="No selected safe or verified controlled-risk coverage opportunity remained.",
                )
                for opportunity in coverage_opportunity_plan.get("blocked", [])
            ],
        )
        log_message(
            "⚠️ Incremental coverage found no selected safe or controlled-risk opportunity. "
            "See Kover gap dashboard for remaining blocked gaps.",
            category="warning",
        )
        return False
    coverage_context = coverage_gap_context(
        gap,
        source_code,
        cluster_plan_context=selected_opportunity_context,
        selected_lines=sorted(selected_opportunity_lines(coverage_opportunity_plan)[0]),
        selected_branch_lines=sorted(selected_opportunity_lines(coverage_opportunity_plan)[1]),
        selected_methods=[item.name for item in _selected_opportunities(coverage_opportunity_plan)],
        coverage_buckets=get_config().coverage_buckets,
    )
    gap_relevant_risk_context = filter_source_risk_context_for_gap(source_risk_context, gap)
    incremental_strategy = build_incremental_coverage_strategy(
        class_name=class_name,
        source_code=source_code,
        existing_test_code=existing_test_code,
        gap=gap,
        incremental_rounds=incremental_rounds,
        opportunity_plan=coverage_opportunity_plan,
    )
    log_block("KOVER COVERAGE GAP CONTEXT", coverage_context, category="context", console=True)
    log_block("DETERMINISTIC INCREMENTAL COVERAGE STRATEGY", incremental_strategy, category="context", console=True)

    if not gap.has_gaps():
        log_message("✅ Existing tests already cover the selected source according to Kover.", category="success")
        if anchor_test_exists:
            remember_successful_generation_if_high_quality(
                class_name,
                await mcp_tools.read_file(output_file_path),
                output_file_path,
                source_code=source_code,
            )
        return True

    if anchor_test_exists:
        log_message(
            "ℹ️ Existing test file detected; generating a "
            "merge candidate for current Kover gaps and applying it directly to the existing test file.",
            category="info",
        )

    temp_output_file_path = (
        derive_coverage_supplement_path(output_file_path)
        if anchor_test_exists
        else indirect_coverage_output_file_path
    )
    if not anchor_test_exists:
        log_message(
            "ℹ️ No local test file exists, but Kover shows indirect coverage for this source. "
            "generating a standalone supplemental test for uncovered code.",
            category="info",
        )
    supplemental_test_class_name = os.path.basename(temp_output_file_path).replace(".kt", "")

    supplemental_code = generate_coverage_supplement_test_streaming(
        class_name=class_name,
        supplemental_test_class_name=supplemental_test_class_name,
        source_code=source_code,
        existing_test_code=existing_test_code,
        dependency_context=dependency_context,
        coverage_context=coverage_context,
        incremental_strategy=incremental_strategy,
        source_risk_context=gap_relevant_risk_context,
        output_file_path=output_file_path,
        standalone_mode=not anchor_test_exists,
        source_categories=source_categories,
        memory_context=memory_context,
        opportunity_plan=coverage_opportunity_plan,
    )
    supplemental_code = normalize_kotlin_test_code(supplemental_code, source_code=source_code)
    supplemental_code = force_generated_test_class_name(supplemental_code, supplemental_test_class_name)

    if not supplemental_code.strip():
        log_message("❌ Incremental coverage generation returned empty code.", category="error")
        return False

    def supplemental_validation_issues(code: str) -> list[str]:
        return list(
            dict.fromkeys(
                validate_generated_test_code(
                    code,
                    temp_output_file_path,
                    source_code=source_code,
                    opportunity_plan=coverage_opportunity_plan,
                    existing_test_code=existing_test_code,
                )
                + coverage_candidate_intent_issues(code, coverage_opportunity_plan)
                + collect_incremental_orchestration_issues(
                    code,
                    coverage_opportunity_plan,
                    existing_test_code=existing_test_code,
                )
            )
        )

    validation_issues = supplemental_validation_issues(supplemental_code)
    if validation_issues:
        seen_validation_failures = set(_validation_issue_fingerprint(validation_issues))
        log_message("⚠️ Supplemental generated test failed static validation:", category="warning")
        for issue in validation_issues:
            log_message(f"   - {issue}", category="warning")
        focused_error_block = (
            "RELATED ERROR GROUP: Local generated-test validation failure\n\n"
            + enrich_validation_error_block(validation_issues)
        )
        repaired_code = repair_focused_error_block_streaming(
            class_name=supplemental_test_class_name,
            source_code=source_code,
            current_test_code=supplemental_code,
            focused_error_block=focused_error_block,
            verified_context=(
                "Local deterministic validation failed before incremental merge. "
                "Repair the generated supplemental candidate first; keep it merge-compatible with the existing test file."
            ),
            group_key="Local generated-test validation failure",
            source_risk_context=gap_relevant_risk_context,
            output_file_path=temp_output_file_path,
            source_categories=source_categories,
            memory_context=retrieve_repair_lessons(
                source_categories,
                repair_categories={"fixture_strategy"},
            ),
        )
        repaired_code = normalize_kotlin_test_code(repaired_code, source_code=source_code)
        repaired_code = force_generated_test_class_name(repaired_code, supplemental_test_class_name)
        repaired_validation_issues = supplemental_validation_issues(repaired_code)
        patch_round = 0
        while repaired_code.strip() and repaired_validation_issues:
            failure_fingerprint = _validation_issue_fingerprint(repaired_validation_issues)
            if seen_validation_failures.intersection(failure_fingerprint):
                log_message(
                    "❌ Static validation failure repeated after repair; stopping this candidate.",
                    category="error",
                )
                break
            if patch_round >= get_config().max_repair_rounds:
                log_message("❌ Static validation repair limit reached.", category="error")
                break

            seen_validation_failures.update(failure_fingerprint)
            log_message(
                "⚠️ Repair introduced different static validation issue(s); trying focused JSON patch repair:",
                category="warning",
            )
            for issue in repaired_validation_issues:
                log_message(f"   - {issue}", category="warning")
            focused_error_block = (
                "RELATED ERROR GROUP: Local generated-test validation failure after repair\n\n"
                + enrich_validation_error_block(repaired_validation_issues)
            )
            patches = repair_focused_error_patch_streaming(
                class_name=supplemental_test_class_name,
                source_code=source_code,
                current_test_code=repaired_code,
                focused_error_block=focused_error_block,
                verified_context=(
                    "The previous full-file repair fixed a different validation failure. "
                    "Apply only the exact minimal correction for the current deterministic issue."
                ),
                group_key="Local generated-test validation failure after repair",
                source_risk_context=gap_relevant_risk_context,
                output_file_path=temp_output_file_path,
                source_categories=source_categories,
                memory_context=retrieve_repair_lessons(
                    source_categories,
                    repair_categories={"fixture_strategy"},
                ),
            )
            patched_code, applied_patch_count = _apply_exact_patches(repaired_code, patches)
            if not applied_patch_count or text_fingerprint(patched_code) == text_fingerprint(repaired_code):
                log_message("❌ Static validation JSON patch made no effective change.", category="error")
                break
            patch_round += 1
            repaired_code = force_generated_test_class_name(
                normalize_kotlin_test_code(patched_code, source_code=source_code),
                supplemental_test_class_name,
            )
            repaired_validation_issues = supplemental_validation_issues(repaired_code)

        if repaired_code.strip() and not repaired_validation_issues:
            supplemental_code = repaired_code
            validation_issues = []
            log_block("STATIC VALIDATION REPAIR CODE FOR SUPPLEMENT", repaired_code, category="code", console=False)
            log_message("🔧 Applied static validation repair for supplemental test before merge.", category="fix")
        else:
            log_message("❌ Supplemental generated test still failed static validation after repair:", category="error")
            for issue in repaired_validation_issues:
                log_message(f"   - {issue}", category="error")
            record_kover_rejected_attempt(
                project_root,
                source_file_path=source_file_path,
                opportunity_plan=coverage_opportunity_plan,
                gradle_tasks=gradle_tasks,
                failure_stage="static_validation",
                evidence="; ".join(repaired_validation_issues),
                source_code=source_code,
                existing_test_code=existing_test_code,
                disposition=DISPOSITION_PIPELINE_UNRESOLVED,
            )
            return False

    if anchor_test_exists:
        original_existing_test_code = existing_test_code or await mcp_tools.read_file(output_file_path)
        required_incremental_tests = _test_function_names(supplemental_code) - _test_function_names(
            original_existing_test_code
        )
        try:
            merged_code = merge_supplemental_test_code(original_existing_test_code, supplemental_code)
        except Exception as exc:
            log_message(f"❌ Could not merge generated supplemental tests into existing file: {exc}", category="error")
            return False
        merged_code = normalize_kotlin_test_code(
            merged_code,
            source_code=source_code,
            output_file_path=output_file_path,
        )

        merged_validation_issues = validation_issues_added(
            original_existing_test_code,
            merged_code,
            output_file_path,
            source_code=source_code,
        )
        if merged_validation_issues:
            log_message("⚠️ Merged incremental test failed local validation:", category="warning")
            for issue in merged_validation_issues:
                log_message(f"   - {issue}", category="warning")
            focused_error_block = (
                "RELATED ERROR GROUP: Local generated-test validation failure after incremental merge\n\n"
                + "\n".join(f"- {issue}" for issue in merged_validation_issues)
            )
            repaired_merged_code = repair_focused_error_block_streaming(
                class_name=class_name,
                source_code=source_code,
                current_test_code=merged_code,
                focused_error_block=focused_error_block,
                verified_context=(
                    "Incremental coverage generation updates the existing test file directly. "
                    "Repair the merged full test file; preserve existing tests and keep only one valid test class."
                ),
                group_key="Local generated-test validation failure after incremental merge",
                source_risk_context=gap_relevant_risk_context,
                output_file_path=output_file_path,
            )
            repaired_merged_code = normalize_kotlin_test_code(repaired_merged_code, source_code=source_code)
            repaired_merged_validation_issues = validation_issues_added(
                original_existing_test_code,
                repaired_merged_code,
                output_file_path,
                source_code=source_code,
            )
            if repaired_merged_code.strip() and not repaired_merged_validation_issues:
                merged_code = repaired_merged_code
                log_block("LOCAL-VALIDATION REPAIR CODE AFTER INCREMENTAL MERGE", merged_code, category="code", console=False)
                log_message("🔧 Applied local-validation repair after direct incremental merge.", category="fix")
            else:
                await mcp_tools.write_file(output_file_path, original_existing_test_code)
                log_message("❌ Merged incremental test still failed local validation after repair:", category="error")
                for issue in repaired_merged_validation_issues:
                    log_message(f"   - {issue}", category="error")
                log_message(
                    "Restored original existing test file without fallback.",
                    category="error",
                )
                return False

        await mcp_tools.write_file(output_file_path, merged_code)
        log_block("DIRECTLY MERGED INCREMENTAL TEST CODE", merged_code, category="code", console=False)
        log_message(
            f"🔗 Updated existing test file directly with incremental coverage additions: {output_file_path}",
            category="success",
        )

        repair_verification = {}
        merge_success = await verify_and_repair_test_with_mcp(
            mcp_tools=mcp_tools,
            class_name=class_name,
            source_code=source_code,
            output_file_path=output_file_path,
            project_root=project_root,
            gradle_offline=gradle_offline,
            gradle_tasks=gradle_tasks,
            source_risk_context=(
                gap_relevant_risk_context
                + "\n\nThis is direct incremental coverage mode. Preserve existing tests and keep the newly merged "
                "coverage additions only when they compile and improve the target Kover gap."
                + (
                    "\nRequired newly merged @Test functions that repair must preserve: "
                    + ", ".join(sorted(required_incremental_tests))
                    if required_incremental_tests
                    else ""
                )
            ),
            verification_result=repair_verification,
        )
        if not merge_success:
            await mcp_tools.write_file(output_file_path, original_existing_test_code)
            record_kover_rejected_attempt(
                project_root,
                source_file_path=source_file_path,
                opportunity_plan=coverage_opportunity_plan,
                gradle_tasks=gradle_tasks,
                failure_stage="gradle_repair",
                evidence="Merged candidate did not pass Gradle/JUnit repair.",
                source_code=source_code,
                existing_test_code=existing_test_code,
                disposition=DISPOSITION_PIPELINE_UNRESOLVED,
            )
            log_message(
                "❌ Direct incremental merge did not pass Gradle repair loop. "
                "Restored original existing test file without fallback.",
                category="error",
            )
            return False

        repaired_test_code = await mcp_tools.read_file(output_file_path)
        missing_incremental_tests = required_incremental_tests - _test_function_names(repaired_test_code)
        if missing_incremental_tests:
            await mcp_tools.write_file(output_file_path, original_existing_test_code)
            evidence = "Gradle repair removed selected incremental tests: " + ", ".join(
                sorted(missing_incremental_tests)
            )
            record_kover_rejected_attempt(
                project_root,
                source_file_path=source_file_path,
                opportunity_plan=coverage_opportunity_plan,
                gradle_tasks=gradle_tasks,
                failure_stage="repair_dropped_candidate",
                evidence=evidence,
                source_code=source_code,
                existing_test_code=existing_test_code,
                disposition=DISPOSITION_PIPELINE_UNRESOLVED,
            )
            log_message(f"❌ {evidence}. Restored original existing test file.", category="error")
            return False

        repaired_intent_issues = coverage_candidate_intent_issues(
            _test_functions_named(repaired_test_code, required_incremental_tests),
            coverage_opportunity_plan,
        )
        if repaired_intent_issues:
            await mcp_tools.write_file(output_file_path, original_existing_test_code)
            evidence = "Gradle repair removed selected coverage intent: " + "; ".join(repaired_intent_issues)
            record_kover_rejected_attempt(
                project_root,
                source_file_path=source_file_path,
                opportunity_plan=coverage_opportunity_plan,
                gradle_tasks=gradle_tasks,
                failure_stage="repair_dropped_intent",
                evidence=evidence,
                source_code=source_code,
                existing_test_code=existing_test_code,
                disposition=DISPOSITION_PIPELINE_UNRESOLVED,
            )
            log_message(f"❌ {evidence}. Restored original existing test file.", category="error")
            return False

        final_passed, final_gap, _ = await run_gradle_and_parse_gap(
            mcp_tools=mcp_tools,
            project_root=project_root,
            gradle_offline=gradle_offline,
            gradle_tasks=gradle_tasks,
            module_dir=module_dir,
            source_file_path=source_file_path,
            source_code=source_code,
            log_title="GRADLE/KOVER OUTPUT AFTER DIRECT INCREMENTAL MERGE",
            verified_gradle_output=repair_verification.get("gradle_output"),
        )
        if not final_passed or not final_gap:
            await mcp_tools.write_file(output_file_path, original_existing_test_code)
            write_blocked_coverage_report(
                source_file_path,
                [
                    item_from_stop(
                        source_file_path,
                        "Direct incremental merge verification",
                        "invalid_fixture: Gradle or Kover parsing failed after merging supplemental tests.",
                        "Direct incremental merge failed Gradle or Kover parsing; restored original existing test file.",
                    )
                ],
            )
            log_message(
                "❌ Direct incremental merge failed Gradle or Kover parsing. "
                "Restored original existing test file without fallback.",
                category="error",
            )
            return False

        final_delta = coverage_gap_delta_summary(gap, final_gap)
        log_block("DIRECT INCREMENTAL MERGE COVERAGE DELTA", final_delta, category="context", console=False)
        log_block(
            "DIRECT INCREMENTAL MERGE COVERAGE ACCEPTANCE",
            coverage_acceptance_diagnostics(gap, final_gap),
            category="context",
            console=False,
        )
        log_block(
            "DIRECT INCREMENTAL MERGE SELECTED OPPORTUNITY ACCEPTANCE",
            opportunity_acceptance_diagnostics(coverage_opportunity_plan, gap, final_gap),
            category="context",
            console=False,
        )
        direct_acceptance = coverage_candidate_acceptance_decision(
            coverage_opportunity_plan,
            gap,
            final_gap,
            source_code,
        )
        log_block(
            "DIRECT INCREMENTAL MERGE COVERAGE CANDIDATE ACCEPTANCE",
            coverage_candidate_acceptance_diagnostics(direct_acceptance),
            category="context",
            console=False,
        )
        if direct_acceptance.accepted:
            _INCREMENTAL_RETRY_FEEDBACK.pop(source_key, None)
            final_merged_code = await mcp_tools.read_file(output_file_path)
            remember_successful_generation_if_high_quality(
                class_name,
                final_merged_code,
                output_file_path,
                source_code=source_code,
            )
            log_message(
                "✅ Direct incremental merge passed Gradle/Kover and improved target coverage.",
                category="success",
            )
            return True

        rejected_opportunity_fingerprints.update(_selected_opportunity_fingerprints(coverage_opportunity_plan))
        _INCREMENTAL_RETRY_FEEDBACK[source_key] = "\n".join(
            [
                opportunity_acceptance_diagnostics(coverage_opportunity_plan, gap, final_gap),
                coverage_candidate_acceptance_diagnostics(direct_acceptance),
                f"Reason: {direct_acceptance.reason}",
            ]
        )
        write_blocked_coverage_report(
            source_file_path,
            [
                item_from_rejected_opportunity(
                    source_file_path,
                    opportunity,
                    evidence=f"Kover candidate rejected after Gradle pass: {direct_acceptance.reason}",
                )
                for opportunity in _selected_opportunities(coverage_opportunity_plan)
            ],
        )
        record_kover_rejected_attempt(
            project_root,
            source_file_path=source_file_path,
            opportunity_plan=coverage_opportunity_plan,
            gradle_tasks=gradle_tasks,
            failure_stage="merge_kover_no_delta",
            evidence=f"Kover candidate rejected: {direct_acceptance.reason}",
            source_code=source_code,
            existing_test_code=existing_test_code,
        )
        log_message(
            "🧭 Recorded selected incremental opportunities as rejected for this source run because Kover did not improve.",
            category="warning",
        )
        await mcp_tools.write_file(output_file_path, original_existing_test_code)
        log_message(
            f"⚠️ Direct incremental merge passed Gradle but was rejected: {direct_acceptance.reason}. "
            "Restored original existing test file without fallback.",
            category="warning",
        )
        if incremental_rounds > 1 and direct_acceptance.reason in _RETRYABLE_ACCEPTANCE_REASONS:
            return await _retry_next_incremental_opportunity(
                source_file_path=source_file_path,
                coverage_opportunity_plan=coverage_opportunity_plan,
                rejected_opportunity_fingerprints=rejected_opportunity_fingerprints,
                incremental_rounds=incremental_rounds,
                reason=direct_acceptance.reason,
                reject_current=False,
            )
        return False

    await mcp_tools.write_file(temp_output_file_path, supplemental_code)
    log_block("INITIAL SUPPLEMENTAL COVERAGE TEST CODE", supplemental_code, category="code", console=False)
    log_message(f"✅ Saved temporary supplemental test file: {temp_output_file_path}", category="success")

    repair_verification = {}
    temp_success = await verify_and_repair_test_with_mcp(
        mcp_tools=mcp_tools,
        class_name=supplemental_test_class_name,
        source_code=source_code,
        output_file_path=temp_output_file_path,
        project_root=project_root,
        gradle_offline=gradle_offline,
        gradle_tasks=gradle_tasks,
        source_risk_context=gap_relevant_risk_context,
        verification_result=repair_verification,
    )

    if not temp_success:
        log_message(
            "❌ Temporary supplemental coverage test did not pass Gradle. fix temp file manually.",
            category="error",
        )
        #await mcp_tools.delete_file(temp_output_file_path)
        archive_generated_side_files(temp_output_file_path, source_file_path)
        return False

    temp_passed, temp_gap, _ = await run_gradle_and_parse_gap(
        mcp_tools=mcp_tools,
        project_root=project_root,
        gradle_offline=gradle_offline,
        gradle_tasks=gradle_tasks,
        module_dir=module_dir,
        source_file_path=source_file_path,
        source_code=source_code,
        log_title="GRADLE/KOVER OUTPUT AFTER TEMP SUPPLEMENTAL TEST VERIFICATION",
        verified_gradle_output=repair_verification.get("gradle_output"),
    )
    if not temp_passed or not temp_gap:
        write_blocked_coverage_report(
            source_file_path,
            [
                item_from_stop(
                    source_file_path,
                    "Temporary supplemental verification",
                    "invalid_fixture: Temporary supplemental test passed repair flow but Kover verification could not be parsed.",
                    "Temporary file deleted and current tests left unchanged.",
                )
            ],
        )
        record_kover_rejected_attempt(
            project_root,
            source_file_path=source_file_path,
            opportunity_plan=coverage_opportunity_plan,
            gradle_tasks=gradle_tasks,
            failure_stage="supplemental_kover_no_delta",
            evidence=f"Temporary Kover candidate rejected: {temp_acceptance.reason}",
            source_code=source_code,
            existing_test_code=existing_test_code,
        )
        log_message(
            "❌ Temporary supplemental test passed repair flow but Kover verification could not be parsed. "
            "Deleting temp file and leaving current tests unchanged.",
            category="error",
        )
        await mcp_tools.delete_file(temp_output_file_path)
        archive_generated_side_files(temp_output_file_path, source_file_path)
        return False

    temp_delta = coverage_gap_delta_summary(gap, temp_gap)
    log_block("TEMP SUPPLEMENTAL COVERAGE DELTA", temp_delta, category="context", console=False)
    log_block(
        "TEMP SUPPLEMENTAL COVERAGE ACCEPTANCE",
        coverage_acceptance_diagnostics(gap, temp_gap),
        category="context",
        console=False,
    )
    log_block(
        "TEMP SUPPLEMENTAL SELECTED OPPORTUNITY ACCEPTANCE",
        opportunity_acceptance_diagnostics(coverage_opportunity_plan, gap, temp_gap),
        category="context",
        console=False,
    )
    temp_acceptance = coverage_candidate_acceptance_decision(
        coverage_opportunity_plan,
        gap,
        temp_gap,
        source_code,
    )
    log_block(
        "TEMP SUPPLEMENTAL COVERAGE CANDIDATE ACCEPTANCE",
        coverage_candidate_acceptance_diagnostics(temp_acceptance),
        category="context",
        console=False,
    )
    if not temp_acceptance.accepted:
        rejected_opportunity_fingerprints.update(_selected_opportunity_fingerprints(coverage_opportunity_plan))
        _INCREMENTAL_RETRY_FEEDBACK[source_key] = "\n".join(
            [
                opportunity_acceptance_diagnostics(coverage_opportunity_plan, gap, temp_gap),
                coverage_candidate_acceptance_diagnostics(temp_acceptance),
                f"Reason: {temp_acceptance.reason}",
            ]
        )
        write_blocked_coverage_report(
            source_file_path,
            [
                item_from_rejected_opportunity(
                    source_file_path,
                    opportunity,
                    evidence=f"Temporary candidate rejected after Gradle pass: {temp_acceptance.reason}",
                )
                for opportunity in _selected_opportunities(coverage_opportunity_plan)
            ],
        )
        log_message(
            "🧭 Recorded selected temporary incremental opportunities as rejected for this source run because Kover did not improve.",
            category="warning",
        )
        log_message(
            f"⚠️ Temporary supplemental tests passed Gradle but were rejected: {temp_acceptance.reason}. "
            "Deleting temp file and leaving current tests unchanged.",
            category="warning",
        )
        await mcp_tools.delete_file(temp_output_file_path)
        archive_generated_side_files(temp_output_file_path, source_file_path)
        if incremental_rounds > 1 and temp_acceptance.reason in _RETRYABLE_ACCEPTANCE_REASONS:
            return await _retry_next_incremental_opportunity(
                source_file_path=source_file_path,
                coverage_opportunity_plan=coverage_opportunity_plan,
                rejected_opportunity_fingerprints=rejected_opportunity_fingerprints,
                incremental_rounds=incremental_rounds,
                reason=temp_acceptance.reason,
                reject_current=False,
            )
        return False

    fixed_supplemental_code = await mcp_tools.read_file(temp_output_file_path)
    final_output_code = normalize_kotlin_test_code(fixed_supplemental_code, source_code=source_code)
    if text_fingerprint(final_output_code) != text_fingerprint(fixed_supplemental_code):
        await mcp_tools.write_file(temp_output_file_path, final_output_code)
    remember_successful_generation_if_high_quality(
        class_name,
        final_output_code,
        temp_output_file_path,
        source_code=source_code,
    )
    log_block("STANDALONE INCREMENTAL COVERAGE TEST CODE", final_output_code, category="code", console=False)
    log_message(
        "✅ Source already had Kover coverage from other tests; kept only a standalone coverage supplement for remaining gaps: "
        f"{temp_output_file_path}",
        category="success",
    )
    return True


async def _retry_next_incremental_opportunity(
    *,
    source_file_path: str,
    coverage_opportunity_plan,
    rejected_opportunity_fingerprints: set[str],
    incremental_rounds: int,
    reason: str,
    reject_current: bool,
    disposition: str = "pipeline_unresolved",
):
    if reject_current:
        rejected_opportunity_fingerprints.update(
            _selected_opportunity_fingerprints(coverage_opportunity_plan or {})
        )
    accepted = disposition == "covered"
    if accepted:
        log_message(
            f"✅ Incremental opportunity accepted for {os.path.basename(source_file_path)}; rebuilding the remaining plan.",
            category="success",
        )
    else:
        log_message(
            f"⚠️ Incremental opportunity requires another attempt ({reason}); {max(0, incremental_rounds - 1)} attempt(s) remain.",
            category="warning",
        )
    return _RETRY_INCREMENTAL, accepted, "" if accepted else reason


async def run_incremental_coverage_generation(
    mcp_tools: LocalMcpTools,
    class_name: str,
    source_code: str,
    source_file_path: str,
    output_file_path: str,
    project_root: str,
    gradle_offline: bool,
    gradle_tasks: list,
    dependency_context: str,
    source_risk_context: str,
    source_categories=None,
    memory_context: str = "",
    incremental_rounds: int = 3,
    _rejected_opportunity_fingerprints=None,
) -> bool | None:
    rejected = _rejected_opportunity_fingerprints
    if rejected is None:
        rejected = _REJECTED_INCREMENTAL_OPPORTUNITY_FINGERPRINTS.setdefault(
            os.path.abspath(source_file_path), set()
        )
    accepted_any = False
    remaining = max(1, incremental_rounds)
    while remaining > 0:
        result = await _run_incremental_coverage_generation_impl(
            mcp_tools,
            class_name,
            source_code,
            source_file_path,
            output_file_path,
            project_root,
            gradle_offline,
            gradle_tasks,
            dependency_context,
            source_risk_context,
            source_categories,
            memory_context,
            remaining,
            rejected,
        )
        if isinstance(result, tuple) and result and result[0] is _RETRY_INCREMENTAL:
            accepted_any = accepted_any or bool(result[1])
            remaining -= 1
            continue
        return bool(result) or accepted_any if result is not None else (True if accepted_any else None)
    return accepted_any
