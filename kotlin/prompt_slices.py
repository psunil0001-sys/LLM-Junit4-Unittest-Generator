# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Builds scoped Kotlin source and test prompt slices.
"""Build scoped source/test Kotlin slices for incremental and patch-repair prompts."""

from __future__ import annotations

import re
from dataclasses import dataclass

from UnitTest_gen.kotlin.gradle_analysis.errors import extract_error_line_numbers, extract_error_symbols
from UnitTest_gen.kotlin.static_analysis import (
    KotlinStaticAnalysisReport,
    _owner_function_for_span,
    analyze_kotlin_code,
    analyze_kotlin_test_code,
)
from UnitTest_gen.kotlin.test_code_utils.merge import (
    class_member_name,
    extract_class_body_members,
    is_function_member,
    is_test_function_member,
    split_top_level_class_members,
)

OMITTED_SOURCE_TRAILER = "// --- omitted: other methods already covered or out of scope ---"
MAX_SOURCE_SLICE_LINES = 200
MAX_TEST_SLICE_LINES = 250


@dataclass
class PromptSliceResult:
    source_slice: str
    test_slice: str
    omitted_summary: str


def _merge_line_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    cleaned = sorted((start, end) for start, end in spans if start > 0 and end >= start)
    if not cleaned:
        return []
    merged = [cleaned[0]]
    for start, end in cleaned[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end + 1:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def _lines_from_spans(source_lines: list[str], spans: list[tuple[int, int]]) -> list[str]:
    selected: list[str] = []
    for start, end in spans:
        for line_no in range(start, min(end, len(source_lines)) + 1):
            selected.append(source_lines[line_no - 1])
    return selected


def _class_header_end_line(source_lines: list[str]) -> int:
    class_seen = False
    paren_depth = 0
    for index, line in enumerate(source_lines, start=1):
        if not class_seen and re.search(r"\b(?:class|object)\s+[A-Za-z_][A-Za-z0-9_]*", line):
            class_seen = True
        if not class_seen:
            continue
        paren_depth += line.count("(") - line.count(")")
        if "{" in line and paren_depth <= 0:
            return index
    return 1


def _preamble_line_count(source_lines: list[str]) -> int:
    end = _class_header_end_line(source_lines)
    for index in range(1, end + 1):
        if index > len(source_lines):
            break
        if source_lines[index - 1].strip() == "{":
            return index
    return end


def _function_by_name(report: KotlinStaticAnalysisReport, name: str):
    clean = (name or "").strip("`")
    matches = [
        function
        for function in report.functions
        if function.name.strip("`") == clean and _is_member_function(report, function)
    ]
    if not matches:
        return None
    return min(matches, key=lambda item: item.span.start_line)


def _is_member_function(report: KotlinStaticAnalysisReport, function) -> bool:
    return not any(
        other is not function
        and other.span.start_line <= function.span.start_line
        and function.span.end_line <= other.span.end_line
        for other in report.functions
    )


def _collect_production_spans(
    source_code: str,
    report: KotlinStaticAnalysisReport,
    *,
    entry_names: set[str],
    gap_lines: set[int],
) -> list[tuple[int, int]]:
    source_lines = source_code.splitlines()
    spans: list[tuple[int, int]] = [(1, _preamble_line_count(source_lines))]

    for prop in report.properties:
        if (
            prop.is_lateinit
            or "Inject" in prop.annotations
            or any(prop.span.start_line <= line <= prop.span.end_line for line in gap_lines)
        ):
            spans.append((prop.span.start_line, prop.span.end_line))

    for name in sorted(entry_names):
        function = _function_by_name(report, name)
        if function is not None:
            spans.append((function.span.start_line, function.span.end_line))
            function_text = "\n".join(source_lines[function.span.start_line - 1:function.span.end_line])
            for prop in report.properties:
                if re.search(rf"\b{re.escape(prop.name)}\b", function_text):
                    spans.append((prop.span.start_line, prop.span.end_line))

    for line_no in gap_lines:
        owner = _owner_function_for_span(report.functions, line_no)
        if owner is not None and _is_member_function(report, owner):
            spans.append((owner.span.start_line, owner.span.end_line))

    return _merge_line_spans(spans)


def slice_production_source(
    source_code: str,
    *,
    source_path: str = "",
    entry_names: set[str] | None = None,
    gap_lines: set[int] | None = None,
    analysis_report: KotlinStaticAnalysisReport | None = None,
) -> tuple[str, str]:
    if not (source_code or "").strip():
        return "", "No production source available."

    report = analysis_report or analyze_kotlin_code(source_code, source_path)
    source_lines = source_code.splitlines()
    spans = _collect_production_spans(
        source_code,
        report,
        entry_names=entry_names or set(),
        gap_lines=gap_lines or set(),
    )
    body_lines = _lines_from_spans(source_lines, spans)
    if len(body_lines) > MAX_SOURCE_SLICE_LINES:
        body_lines = body_lines[:MAX_SOURCE_SLICE_LINES]
        body_lines.append(OMITTED_SOURCE_TRAILER)

    included_names = {
        function.name.strip("`")
        for function in report.functions
        if _is_member_function(report, function)
        and any(function.span.start_line <= start <= function.span.end_line for start, _ in spans)
    }
    all_member_names = {
        function.name.strip("`")
        for function in report.functions
        if _is_member_function(report, function)
    }
    omitted = sorted(all_member_names - included_names)
    omitted_summary = (
        f"{len(omitted)} other member(s) omitted: {', '.join(omitted[:20])}"
        if omitted
        else "No additional production members omitted."
    )
    return "\n".join(body_lines).strip() + "\n", omitted_summary


def _test_class_prefix(test_code: str) -> str:
    match = re.search(
        r"(?ms)\A((?:package[^\n]*\n)?(?:import[^\n]*\n)*(?:@[^\n]+\n|\s)*class\s+[A-Za-z_][A-Za-z0-9_]*[^{]*\{)",
        test_code,
    )
    if match:
        return match.group(1) + "\n"
    return "class UnderTest {\n"


def _member_calls_entries(member_text: str, entry_names: set[str]) -> bool:
    for entry in entry_names:
        if entry and re.search(rf"\b{re.escape(entry)}\s*\(", member_text):
            return True
    return False


def _member_covers_lines(member_lines: list[str], test_code: str, line_numbers: set[int]) -> bool:
    if not line_numbers:
        return False
    body = extract_class_body_members(test_code)
    body_start = test_code.find(body)
    if body_start < 0:
        return False
    member_start = None
    needle = member_lines[0].strip()
    for index, line in enumerate(test_code.splitlines(), start=1):
        if line.strip() == needle and member_start is None:
            member_start = index
            break
    if member_start is None:
        return False
    member_line_count = len(member_lines)
    member_range = set(range(member_start, member_start + member_line_count))
    return bool(member_range & line_numbers)


def _is_setup_or_rule_member(member_lines: list[str]) -> bool:
    joined = "".join(member_lines)
    if any(token in joined for token in ("@Before", "@After", "@Rule", "@get:Rule")):
        return True
    if not is_function_member(member_lines):
        return bool(re.search(r"\b(?:val|var)\s+\w+", joined) and "Rule" in joined)
    return False


def slice_test_file(
    test_code: str,
    *,
    entry_names: set[str] | None = None,
    error_line_numbers: set[int] | None = None,
    helper_names: set[str] | None = None,
) -> tuple[str, str]:
    if not (test_code or "").strip():
        return "", "No existing test file."

    entry_names = entry_names or set()
    error_line_numbers = error_line_numbers or set()
    helper_names = helper_names or set()
    report = analyze_kotlin_test_code(test_code)

    included_members: list[str] = []
    omitted_tests: list[str] = []

    body = extract_class_body_members(test_code)
    for member_lines in split_top_level_class_members(body):
        member_text = "".join(member_lines)
        name = class_member_name(member_lines) or ""

        include = False
        if _is_setup_or_rule_member(member_lines):
            include = True
        elif is_test_function_member(member_lines):
            if _member_calls_entries(member_text, entry_names):
                include = True
            elif _member_covers_lines(member_lines, test_code, error_line_numbers):
                include = True
            elif name and name in helper_names:
                include = True
            else:
                if name:
                    omitted_tests.append(name)
                continue
        elif is_function_member(member_lines):
            if name in helper_names or _member_calls_entries(member_text, entry_names):
                include = True
            elif _member_covers_lines(member_lines, test_code, error_line_numbers):
                include = True
        elif name and name in helper_names:
            include = True

        if include:
            included_members.append(member_text.strip("\n"))

    for helper in report.tests.helper_functions:
        if helper.name in helper_names and helper.name not in {
            class_member_name(split_top_level_class_members(extract_class_body_members(test_code))[0])
        }:
            lines = test_code.splitlines()
            included_members.append(
                "\n".join(lines[helper.span.start_line - 1 : helper.span.end_line]).strip("\n")
            )

    # De-dupe member blocks while preserving order.
    seen: set[str] = set()
    unique_members: list[str] = []
    for member in included_members:
        key = member.strip()
        if key and key not in seen:
            seen.add(key)
            unique_members.append(member)

    slice_lines = (_test_class_prefix(test_code) + "\n\n".join(unique_members) + "\n}").splitlines()
    if len(slice_lines) > MAX_TEST_SLICE_LINES:
        slice_lines = slice_lines[:MAX_TEST_SLICE_LINES]
        slice_lines.append("    // --- omitted: additional test methods truncated ---")
        slice_lines.append("}")

    omitted_summary = (
        f"{len(omitted_tests)} other @Test method(s) omitted: {', '.join(omitted_tests[:25])}"
        if omitted_tests
        else "No additional @Test methods omitted."
    )
    return "\n".join(slice_lines).strip() + "\n", omitted_summary


def _entry_names_from_plan(opportunity_plan: dict | None, selected_entry_points: list[str] | None) -> set[str]:
    names: set[str] = set(selected_entry_points or [])
    for bucket in ("selected_safe", "selected_attemptable", "selected_blocked", "alternatives", "blocked"):
        for opportunity in opportunity_plan.get(bucket) or []:
            names.update(getattr(opportunity, "entry_points", None) or [])
            for part in getattr(opportunity, "coverage_path", None) or []:
                if part and not part.startswith("<"):
                    names.add(part.split("->")[-1].strip())
            opp_name = (getattr(opportunity, "name", "") or "").strip()
            if opp_name and not opp_name.startswith("Private"):
                token = opp_name.split()[0]
                if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", token):
                    names.add(token)
    return {name.strip("`") for name in names if name}


def _gap_lines_from_plan(opportunity_plan: dict | None) -> set[int]:
    lines: set[int] = set()
    for bucket in ("selected_safe", "selected_attemptable", "selected_blocked"):
        for opportunity in opportunity_plan.get(bucket) or []:
            lines.update(getattr(opportunity, "lines", None) or [])
            lines.update(getattr(opportunity, "branches", None) or [])
    return lines


def slice_for_incremental(
    source_code: str,
    existing_test_code: str,
    *,
    source_path: str = "",
    opportunity_plan: dict | None = None,
    selected_entry_points: list[str] | None = None,
    analysis_report: KotlinStaticAnalysisReport | None = None,
) -> PromptSliceResult:
    plan = opportunity_plan or {}
    entry_names = _entry_names_from_plan(plan, selected_entry_points)
    gap_lines = _gap_lines_from_plan(plan)
    report = analysis_report or analyze_kotlin_code(source_code, source_path)

    source_slice, source_omitted = slice_production_source(
        source_code,
        source_path=source_path,
        entry_names=entry_names,
        gap_lines=gap_lines,
        analysis_report=report,
    )
    test_slice, test_omitted = slice_test_file(
        existing_test_code,
        entry_names=entry_names,
        helper_names=set(report.tests.helper_functions if hasattr(report, "tests") else []),
    )
    return PromptSliceResult(
        source_slice=source_slice,
        test_slice=test_slice,
        omitted_summary=f"Production: {source_omitted} Test: {test_omitted}",
    )


def slice_for_patch_repair(
    source_code: str,
    current_test_code: str,
    *,
    source_path: str = "",
    focused_error_block: str = "",
    analysis_report: KotlinStaticAnalysisReport | None = None,
) -> PromptSliceResult:
    error_lines = set(extract_error_line_numbers(focused_error_block or ""))
    symbols = set(extract_error_symbols(focused_error_block or ""))
    report = analysis_report or analyze_kotlin_code(source_code, source_path)

    entry_names = {
        function.name.strip("`")
        for function in report.functions
        if _is_member_function(report, function) and function.name.strip("`") in symbols
    }
    for call in report.calls:
        if call.span.start_line in error_lines:
            entry_names.add(call.name.strip("`"))

    source_slice, source_omitted = slice_production_source(
        source_code,
        source_path=source_path,
        entry_names=entry_names,
        gap_lines=set(),
        analysis_report=report,
    )
    test_report = analyze_kotlin_test_code(current_test_code)
    helper_names = {helper.name for helper in test_report.tests.helper_functions}
    test_slice, test_omitted = slice_test_file(
        current_test_code,
        error_line_numbers=error_lines,
        helper_names=helper_names,
    )
    return PromptSliceResult(
        source_slice=source_slice,
        test_slice=test_slice,
        omitted_summary=f"Production: {source_omitted} Test: {test_omitted}",
    )
