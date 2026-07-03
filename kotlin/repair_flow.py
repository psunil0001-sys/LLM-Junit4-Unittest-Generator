# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Runs bounded Gradle verification and generated-test repair orchestration.
"""Gradle verification and repair orchestration."""

import json
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from UnitTest_gen.kotlin.gradle_analysis.errors import (
    build_causal_repair_plan,
    causal_repair_fingerprint,
    classify_hilt_missing_binding_issue,
    extract_error_line_numbers,
    extract_error_symbols,
    extract_unresolved_nested_receiver_symbols,
    format_hilt_binding_issue,
    format_causal_repair_block,
    group_gradle_errors,
    summarize_all_error_groups,
    summarize_causal_repair_plan,
)
from UnitTest_gen.kotlin.gradle_analysis.junit import (
    classify_junit_failure_for_group,
    extract_failed_test_selectors,
    is_gradle_success,
    is_junit_or_runtime_failure_group,
    normalize_junit_failure_message,
    should_try_batch_compile_repair,
    test_case_matches_selector,
    text_fingerprint,
    trim_stacktrace,
)
from UnitTest_gen.kotlin.kotlin_analysis import classify_source
from UnitTest_gen.core.logging_utils import log_block, log_message, log_section
from UnitTest_gen.kotlin.blocked_coverage_report import item_from_stop, write_blocked_coverage_report
from UnitTest_gen.core.mcp_tools import LocalMcpTools
from UnitTest_gen.kotlin.memory_context import add_repair_lesson, retrieve_repair_lessons
from UnitTest_gen.kotlin.mcp_tool_adapter import (
    find_kotlin_declaration,
    read_kotlin_file_around_symbol,
    run_gradle_with_heartbeat,
)
from UnitTest_gen.kotlin.project_context import (
    check_and_add_dependencies,
    find_owning_module_dir,
    should_auto_add_missing_test_dependencies,
)
from UnitTest_gen.kotlin.prompting.repair import repair_focused_error_block_streaming, repair_focused_error_patch_streaming
from UnitTest_gen.kotlin.test_code_utils.validate import (
    normalize_kotlin_test_code,
    remember_successful_generation_if_high_quality,
    validate_generated_test_code,
)

from UnitTest_gen.core.pipeline_config import get_config


def max_repair_rounds() -> int:
    return get_config().max_repair_rounds


def max_unchanged_error_repair_attempts() -> int:
    return get_config().max_unchanged_error_repair_attempts


def expected_generated_test_class(output_file_path: str) -> str:
    return Path(output_file_path).stem


def _normalize_evidence_path(text: str) -> str:
    return text.replace("file://", "").replace("\\", "/")


def group_mentions_generated_test_file(group_lines: list[str], output_file_path: str) -> bool:
    evidence = _normalize_evidence_path("\n".join(group_lines))
    expected_path = _normalize_evidence_path(os.path.abspath(output_file_path))
    expected_name = os.path.basename(output_file_path)
    return expected_path in evidence or expected_name in evidence


def group_mentions_generated_test_class(group_lines: list[str], expected_class: str) -> bool:
    evidence = "\n".join(group_lines)
    xml_class_pattern = re.compile(
        rf"(?m)^XML test class:\s+(?:[\w.$]+\.)?{re.escape(expected_class)}\s*$"
    )
    selector_pattern = re.compile(
        rf"(?m)(?:^|\s){re.escape(expected_class)}\s*>\s*.+\s+FAILED\b"
    )
    return bool(xml_class_pattern.search(evidence) or selector_pattern.search(evidence))


def is_error_group_owned_by_generated_test(
    group_key: str,
    group_lines: list[str],
    output_file_path: str,
) -> bool:
    expected_class = expected_generated_test_class(output_file_path)
    group_text = "\n".join(group_lines)
    if is_junit_or_runtime_failure_group(group_key, group_text):
        return group_mentions_generated_test_class(group_lines, expected_class)

    return group_mentions_generated_test_file(group_lines, output_file_path)


def split_error_groups_by_generated_test(
    groups: dict,
    output_file_path: str,
) -> tuple[dict, dict]:
    owned_groups = {}
    external_groups = {}
    for group_key, group_lines in groups.items():
        target = (
            owned_groups
            if is_error_group_owned_by_generated_test(group_key, group_lines, output_file_path)
            else external_groups
        )
        target[group_key] = group_lines
    return owned_groups, external_groups


def groups_include_owned_mockito_misuse(groups: dict) -> bool:
    markers = (
        "any(...) must not be null",
        "capture(...) must not be null",
        "InvalidUseOfMatchersException",
        "UnfinishedVerificationException",
        "UnfinishedStubbingException",
        "Misplaced or misused argument matcher",
        "Missing method call for verify",
    )
    combined = "\n".join([key for key in groups] + [line for lines in groups.values() for line in lines])
    return any(marker in combined for marker in markers)










def group_junit_failures_by_report(project_root: str, output_file_path: str, groups: dict) -> dict:
    """
    Gradle console output often collapses runtime failures into AssertionError
    or Exception summaries. The XML report contains the failed test method and
    exact failure message, so use it to create focused runtime repair groups.
    """
    has_runtime_group = any(
        key.startswith("JUnit")
        or key.startswith("Exception:")
        or key.startswith("Runtime exception:")
        or key == "Unclassified Gradle/JUnit failure"
        or any("FAILED" in line for line in lines)
        or any("UncaughtExceptionsBeforeTest" in line for line in lines)
        for key, lines in groups.items()
    )
    if groups and not has_runtime_group:
        return groups

    module_dir = Path(find_owning_module_dir(project_root, output_file_path))
    test_results_dir = module_dir / "build" / "test-results"
    if not test_results_dir.exists():
        return groups
    expected_test_class = expected_generated_test_class(output_file_path)

    selector_lines = {}
    for group_lines in groups.values():
        for line in group_lines:
            for selector_class, selector_method in extract_failed_test_selectors(line):
                selector_lines[(selector_class, selector_method)] = line
                selector_lines[(selector_class.split(".")[-1], selector_method)] = line

    failed_test_class_names = {
        selector_class.split(".")[-1]
        for selector_class, _ in selector_lines
    }

    failures_by_test = {}
    for xml_file in sorted(test_results_dir.glob("**/TEST-*.xml")):
        try:
            root = ET.parse(xml_file).getroot()
        except Exception:
            continue

        for testcase in root.iter("testcase"):
            failures = list(testcase.findall("failure")) + list(testcase.findall("error"))
            if not failures:
                continue

            failure = failures[0]
            testcase_class = testcase.attrib.get("classname", "")
            testcase_name = testcase.attrib.get("name", "")
            testcase_simple_class = testcase_class.split(".")[-1]
            if testcase_simple_class != expected_test_class:
                log_message(
                    "↪️ Skipping external JUnit XML failure while repairing "
                    f"{expected_test_class}: {testcase_simple_class} > {testcase_name} "
                    f"({xml_file})",
                    category="context",
                )
                continue
            if failed_test_class_names and testcase_simple_class not in failed_test_class_names:
                continue

            failures_by_test[(testcase_class, testcase_name)] = {
                "type": failure.attrib.get("type", ""),
                "message": failure.attrib.get("message", ""),
                "xml_file": str(xml_file),
            }

    if not failures_by_test:
        return groups

    regrouped = {
        key: list(lines)
        for key, lines in groups.items()
        if not (
            key.startswith("JUnit")
            or key.startswith("Exception:")
            or key.startswith("Runtime exception:")
            or key == "Unclassified Gradle/JUnit failure"
            or any("FAILED" in line for line in lines)
            or any("UncaughtExceptionsBeforeTest" in line for line in lines)
        )
    }

    xml_runtime_groups = {}
    for (testcase_class, testcase_name), failure in failures_by_test.items():
        group_key = classify_junit_failure_for_group(
            failure["type"],
            failure["message"],
        )
        selector_key = (testcase_class.split(".")[-1], testcase_name)
        selector_line = selector_lines.get(
            selector_key,
            f"{testcase_class} > {testcase_name} FAILED",
        )
        message = normalize_junit_failure_message(failure["message"], max_len=600)
        xml_runtime_groups.setdefault(group_key, []).extend(
            [
                selector_line,
                f"XML test class: {testcase_class}",
                f"XML test method: {testcase_name}",
                f"XML failure type: {failure['type']}",
                f"XML failure message: {message}",
                f"XML report file: {failure['xml_file']}",
            ]
        )

    if xml_runtime_groups:
        regrouped.update(xml_runtime_groups)
        return regrouped

    for group_key, group_lines in groups.items():
        if not group_key.startswith("JUnit test failure:"):
            regrouped.setdefault(group_key, []).extend(group_lines)
            continue

        selectors = extract_failed_test_selectors("\n".join(group_lines))
        if not selectors:
            regrouped.setdefault(group_key, []).extend(group_lines)
            continue

        matched_any = False
        for selector_class, selector_method in selectors:
            matched_failure = None
            for (testcase_class, testcase_name), failure in failures_by_test.items():
                class_matches = (
                    testcase_class == selector_class
                    or testcase_class.endswith("." + selector_class.split(".")[-1])
                )
                method_matches = testcase_name == selector_method
                if class_matches and method_matches:
                    matched_failure = failure
                    break

            if not matched_failure:
                continue

            matched_any = True
            new_key = classify_junit_failure_for_group(
                matched_failure["type"],
                matched_failure["message"],
            )
            message = (matched_failure["message"] or "").replace("\n", " ")
            if len(message) > 600:
                message = message[:600] + "..."
            regrouped.setdefault(new_key, []).extend(
                [
                    *group_lines,
                    f"XML failure type: {matched_failure['type']}",
                    f"XML failure message: {message}",
                    f"XML report file: {matched_failure['xml_file']}",
                ]
            )

        if not matched_any:
            regrouped.setdefault(group_key, []).extend(group_lines)

    return regrouped

def collect_junit_failure_report_context(
    project_root: str,
    output_file_path: str,
    focused_error_block: str,
    group_key: str = "",
) -> str:
    if not is_junit_or_runtime_failure_group(group_key, focused_error_block):
        return ""

    module_dir = Path(find_owning_module_dir(project_root, output_file_path))
    test_results_dir = module_dir / "build" / "test-results"
    if not test_results_dir.exists():
        return ""

    selectors = extract_failed_test_selectors(focused_error_block)
    expected_test_class = Path(output_file_path).stem
    xml_files = sorted(
        test_results_dir.glob("**/TEST-*.xml"),
        key=lambda path: path.stat().st_mtime if path.exists() else 0,
        reverse=True,
    )
    sections = []
    seen_failures = set()

    for xml_file in xml_files:
        try:
            root = ET.parse(xml_file).getroot()
        except Exception:
            continue

        for testcase in root.iter("testcase"):
            failures = list(testcase.findall("failure")) + list(testcase.findall("error"))
            if not failures or not test_case_matches_selector(testcase, selectors):
                continue

            testcase_class = testcase.attrib.get("classname", "")
            testcase_name = testcase.attrib.get("name", "")
            if not selectors and testcase_class.split(".")[-1] != expected_test_class:
                continue
            dedupe_key = (testcase_class, testcase_name)
            if dedupe_key in seen_failures:
                continue
            seen_failures.add(dedupe_key)
            failure_blocks = []

            for failure in failures:
                failure_type = failure.attrib.get("type", "")
                failure_message = failure.attrib.get("message", "")
                stacktrace = trim_stacktrace(failure.text or "")
                failure_blocks.append(
                    "\n".join(
                        [
                            f"Type: {failure_type}",
                            f"Message: {failure_message}",
                            "Stacktrace:",
                            stacktrace,
                        ]
                    )
                )

            system_out = root.findtext("system-out") or ""
            system_err = root.findtext("system-err") or ""
            extra_output = "\n".join(
                part.strip()
                for part in [system_out[-2000:], system_err[-2000:]]
                if part.strip()
            )

            section = (
                "### JUNIT FAILURE REPORT CONTEXT\n"
                f"Report file: {xml_file}\n"
                f"Test class: {testcase_class}\n"
                f"Test method: {testcase_name}\n\n"
                + "\n\n".join(failure_blocks)
            )

            if extra_output:
                section += "\n\nCaptured stdout/stderr tail:\n" + extra_output

            sections.append(section)
            if len(sections) >= 3:
                break
        if len(sections) >= 3:
            break

    if not sections:
        return ""

    return "\n\n".join(sections)[:9000]


def _join_verified_context_sections(sections: list[str], max_chars: int = 12000) -> str:
    output = []
    used = 0
    for section in sections:
        section = (section or "").strip()
        if not section:
            continue
        additional = len(section) + 2
        if output and used + additional > max_chars:
            continue
        output.append(section)
        used += additional
    return "\n\n".join(output)

async def collect_verified_repair_context(
    mcp_tools: LocalMcpTools,
    project_root: str,
    output_file_path: str,
    focused_error_block: str,
    group_key: str = "",
):
    """
    Tool-grounded context collection before repair.
    This prevents the model from guessing signatures, nullability, constructors, or dependencies.
    """
    parts = []

    junit_report_context = collect_junit_failure_report_context(
        project_root=project_root,
        output_file_path=output_file_path,
        focused_error_block=focused_error_block,
        group_key=group_key,
    )
    if junit_report_context:
        parts.append(junit_report_context)

    line_numbers = list(dict.fromkeys(extract_error_line_numbers(focused_error_block)))
    generated_error_ranges = []
    for line_no in line_numbers[:10]:
        start = max(1, line_no - 20)
        end = line_no + 20
        try:
            file_range = await mcp_tools.read_file_range(output_file_path, start, end)
            if file_range:
                generated_error_ranges.append(file_range)
                parts.append(f"### GENERATED TEST AROUND ERROR LINE {line_no}\n{file_range}")
        except Exception as e:
            parts.append(f"### COULD NOT READ GENERATED TEST RANGE {line_no}\n{e}")

    symbols = extract_error_symbols(focused_error_block)
    nested_receiver_symbols = extract_unresolved_nested_receiver_symbols(
        focused_error_block + "\n" + "\n".join(generated_error_ranges)
    )
    for receiver_symbol in nested_receiver_symbols:
        if receiver_symbol not in symbols:
            symbols.insert(0, receiver_symbol)

    if nested_receiver_symbols:
        parts.append(
            "### UNVERIFIED NESTED API SHAPE\n"
            "The error references a nested member path on a receiver type, but the unresolved member shape is not verified.\n"
            "Repair intent: verify the receiver type's constructors/usages before using nested members; "
            "if no verified construction path exists, remove or replace that branch test instead of inventing enum/class names.\n"
            f"Receiver symbols requiring verification: {', '.join(nested_receiver_symbols)}"
        )

    for symbol in symbols[:6]:
        try:
            declarations = await find_kotlin_declaration(
                mcp_tools,
                project_root,
                symbol,
                max_results=30,
            )
            if declarations:
                parts.append(f"### VERIFIED DECLARATIONS FOR SYMBOL `{symbol}`\n{declarations}")

            around_symbol = await read_kotlin_file_around_symbol(
                mcp_tools,
                project_root,
                symbol,
                context_lines=50,
                max_results=3,
            )
            if around_symbol:
                parts.append(f"### VERIFIED SOURCE CONTEXT AROUND SYMBOL `{symbol}`\n{around_symbol}")

        except Exception as e:
            parts.append(f"### COULD NOT LOOK UP SYMBOL `{symbol}`\n{e}")

    searched_symbols = set()
    for symbol in symbols[:4]:
        if symbol in searched_symbols:
            continue
        searched_symbols.add(symbol)

        try:
            search_context = await mcp_tools.search_in_project(
                project_root,
                symbol,
                    max_results=25,
            )
            if search_context:
                parts.append(f"### VERIFIED PROJECT SEARCH FOR `{symbol}`\n{search_context}")
        except Exception as e:
            parts.append(f"### COULD NOT SEARCH PROJECT FOR `{symbol}`\n{e}")

    if not parts:
        fallback_query = symbols[0] if symbols else ""
        if fallback_query:
            try:
                search_context = await mcp_tools.search_in_project(
                    project_root,
                    fallback_query,
                    max_results=25,
                )
                if search_context:
                    parts.append(f"### VERIFIED PROJECT SEARCH FOR `{fallback_query}`\n{search_context}")
            except Exception as e:
                parts.append(f"### COULD NOT SEARCH PROJECT\n{e}")

    return _join_verified_context_sections(parts)


async def _apply_repair_patches(
    mcp_tools,
    output_file_path: str,
    patches: list[dict],
    failure_message: str,
    failure_log_title: str,
) -> tuple[list[str], int]:
    applied = []
    failed = 0
    for patch in patches or []:
        result = await mcp_tools.patch_file(output_file_path, patch["old_text"], patch["new_text"])
        if "old_text not found" in result:
            failed += 1
            log_message(failure_message, category="warning")
            log_block(failure_log_title, json.dumps(patch, indent=2), category="fix", console=False)
            continue
        applied.append(result)
    return applied, failed


async def verify_and_repair_test_with_mcp(
    mcp_tools: LocalMcpTools,
    class_name: str,
    source_code: str,
    output_file_path: str,
    project_root: str,
    source_file_path: str | None = None,
    gradle_offline: bool = False,
    gradle_tasks=None,
    source_risk_context="",
    record_successful_generation: bool = True,
):
    source_profile = classify_source(source_code)
    source_categories = source_profile.categories

    skipped_repair_locations = set()
    repair_location_attempt_counts = {}
    applied_repairs = []
    repair_round = 0

    while True:
        log_message(
            f"🧪 Running Gradle via MCP "
            f"(repair rounds: {repair_round}, skipped locations: {len(skipped_repair_locations)})...",
            category="gradle",
        )

        gradle_output = await run_gradle_with_heartbeat(
            mcp_tools,
            project_root,
            offline=gradle_offline,
            tasks=gradle_tasks,
        )

        log_block(
            f"GRADLE OUTPUT ROUND {repair_round}",
            gradle_output or "No Gradle output captured.",
            category="gradle",
            console=True,
        )

        if is_gradle_success(gradle_output):
            log_message("✅ Gradle verification passed.", category="success")

            final_test_code = await mcp_tools.read_file(output_file_path)
            log_block("FINAL VERIFIED TEST CODE", final_test_code, category="code", console=False)

            log_message("🧪 Running final Gradle verification...", category="gradle")
            final_gradle_output = await run_gradle_with_heartbeat(
                mcp_tools,
                project_root,
                offline=gradle_offline,
                tasks=gradle_tasks,
            )
            log_block("FINAL GRADLE VERIFICATION OUTPUT", final_gradle_output, category="gradle", console=False)

            if is_gradle_success(final_gradle_output):
                if record_successful_generation:
                    remember_successful_generation_if_high_quality(
                        class_name,
                        final_test_code,
                        output_file_path,
                        source_code=source_code,
                    )
                for repair in applied_repairs:
                    add_repair_lesson(
                        repair["group_key"],
                        patch=repair.get("patch"),
                        fixed_code=repair.get("fixed_code"),
                        source_categories=source_categories,
                        repair_categories=repair.get("repair_categories"),
                    )
                log_message("✅ Final Gradle verification passed.", category="success")
                return True

            log_message("⚠️ Final Gradle verification failed unexpectedly.", category="warning")
            log_message(final_gradle_output or "No Gradle output captured.", category="gradle")
            return False

        groups = group_gradle_errors(gradle_output)
        groups = group_junit_failures_by_report(
            project_root=project_root,
            output_file_path=output_file_path,
            groups=groups,
        )
        log_message(f"📊 Found {len(groups)} error groups", category="fix")

        all_error_summary = summarize_all_error_groups(groups)

        log_block("ALL ERROR GROUPS BEFORE REPAIR", all_error_summary, category="fix", console=True)

        full_gradle_path = output_file_path + f".full-gradle-{repair_round + 1}.log"
        await mcp_tools.write_file(full_gradle_path, gradle_output or "No Gradle output captured.")

        owned_groups, external_groups = split_error_groups_by_generated_test(groups, output_file_path)
        owned_summary = summarize_all_error_groups(owned_groups)
        external_summary = summarize_all_error_groups(external_groups)
        log_block(
            "OWNED REPAIR GROUPS",
            owned_summary or "No Gradle/JUnit failures are owned by this generated test file.",
            category="fix",
            console=True,
        )
        if external_groups:
            contamination_note = ""
            if groups_include_owned_mockito_misuse(owned_groups):
                contamination_note = (
                    "\n\nNote: owned Mockito misuse was present in this same Gradle run. "
                    "These unowned failures may be downstream test-worker contamination, "
                    "so they are logged for diagnosis but not patched directly."
                )
            log_block(
                "EXTERNAL/UNOWNED GRADLE FAILURES",
                external_summary + contamination_note,
                category="warning",
                console=True,
            )

        if not owned_groups:
            write_blocked_coverage_report(
                output_file_path,
                [
                    item_from_stop(
                        output_file_path,
                        "External Gradle/JUnit failure",
                        "external_gradle_failure: Gradle failed, but no repairable error evidence belongs to this generated test file.",
                        external_summary or all_error_summary,
                    )
                ],
            )
            log_message(
                "⚠️ Gradle failed, but no owned repair groups reference the generated test file. "
                "Stopping without patching this file.",
                category="warning",
            )
            return False

        groups = owned_groups

        hilt_binding_issue = classify_hilt_missing_binding_issue(gradle_output or "")
        if hilt_binding_issue and hilt_binding_issue.is_qualifier_mismatch:
            issue_report = format_hilt_binding_issue(hilt_binding_issue)
            log_section("HILT GRAPH CLOSURE STOP", category="warning")
            log_block("HILT BINDING KEY CLASSIFICATION", issue_report, category="warning", console=True)
            write_blocked_coverage_report(
                output_file_path,
                [
                    item_from_stop(
                        output_file_path,
                        "Hilt graph closure",
                        "qualifier_mismatch: Hilt requested one binding key but production provides the same type with a different qualifier.",
                        issue_report,
                    )
                ],
            )
            log_message(
                "⚠️ Hilt graph repair stopped before model repair because the missing binding is a qualifier mismatch, "
                "not a generated-test syntax or fixture issue.",
                category="warning",
            )
            log_message(
                "🧭 Do not create per-file @Module/@InstallIn bindings, remove Hilt setup, or bypass the attached Fragment lifecycle fixture. "
                "Fix the production DI contract or provide a deliberate shared test graph binding outside this generator.",
                category="warning",
            )
            return False

        if repair_round >= max_repair_rounds():
            log_message(
                f"❌ Reached max repair rounds ({max_repair_rounds()}). "
                "Stopping with the generated test intact instead of pruning coverage.",
                category="error",
            )
            return False

        current_test_code_for_dependency_check = await mcp_tools.read_file(output_file_path)
        if should_auto_add_missing_test_dependencies(current_test_code_for_dependency_check, groups):
            log_message(
                "📦 Missing test dependency detected from unresolved test imports; updating the owning Gradle file before model repair.",
                category="fix",
            )
            check_and_add_dependencies(project_root, output_file_path, source_profile)
            repair_round += 1
            log_message("🔁 Re-running Gradle after dependency update...", category="gradle")
            continue

        if should_try_batch_compile_repair(groups, max_evidence_lines=15):
            batch_group_key = "Batch Kotlin compile repair"
            batch_error_block = "ALL CURRENT COMPILE ERRORS\n\n" + "\n\n".join(
                f"ERROR: {key}\n\n" + "\n".join(lines)
                for key, lines in groups.items()
            )

            log_section("BATCH COMPILE REPAIR FAST PATH", category="fix")
            log_message(
                "🧩 Compile-only errors are at or under 15 evidence lines; repairing all current errors together as peer causes.",
                category="fix",
            )
            log_message(
                "Treat these errors as possibly independent compile issues; fix them in one minimal patch set.",
                category="fix",
            )
            log_block("BATCH COMPILE ERROR BLOCK", batch_error_block, category="fix", console=True)

            current_test_code = await mcp_tools.read_file(output_file_path)
            current_test_fingerprint = text_fingerprint(current_test_code)
            verified_context = await collect_verified_repair_context(
                mcp_tools=mcp_tools,
                project_root=project_root,
                output_file_path=output_file_path,
                focused_error_block=batch_error_block,
                group_key=batch_group_key,
            )
            repair_memory_context = retrieve_repair_lessons(
                source_categories,
                repair_categories={"syntax_or_file_shape", "api_signature_mismatch"},
            )

            patches = repair_focused_error_patch_streaming(
                class_name=class_name,
                source_code=source_code,
                current_test_code=current_test_code,
                focused_error_block=batch_error_block,
                verified_context=verified_context,
                group_key=batch_group_key,
                source_risk_context=source_risk_context,
                output_file_path=output_file_path,
                source_categories=source_categories,
                memory_context=repair_memory_context,
            )

            applied_patch_results, failed_patch_count = await _apply_repair_patches(
                mcp_tools,
                output_file_path,
                patches,
                "⚠️ Batch compile patch failed because old_text was not found.",
                "FAILED BATCH COMPILE PATCH",
            )

            if applied_patch_results:
                patched_test_code = await mcp_tools.read_file(output_file_path)
                if text_fingerprint(patched_test_code) != current_test_fingerprint:
                    repair_round += 1
                    log_message(
                        f"🔧 Batch compile repair applied {len(applied_patch_results)} JSON patch(es); "
                        f"{failed_patch_count} failed exact matching.",
                        category="fix",
                    )
                    log_block("APPLIED BATCH COMPILE PATCHES", json.dumps(patches, indent=2), category="fix", console=False)
                    applied_repairs.append(
                        {
                            "group_key": batch_group_key,
                            "patch": json.dumps(patches, indent=2),
                            "repair_categories": {"syntax_or_file_shape", "api_signature_mismatch"},
                        }
                    )
                    log_message("🔁 Re-running Gradle after batch compile repair...", category="gradle")
                    continue

            log_message(
                "⚠️ Batch compile JSON patch produced no effective change; trying one full-file batch repair.",
                category="warning",
            )
            fixed_code = repair_focused_error_block_streaming(
                class_name=class_name,
                source_code=source_code,
                current_test_code=current_test_code,
                focused_error_block=batch_error_block,
                verified_context=verified_context,
                group_key=batch_group_key,
                source_risk_context=source_risk_context,
                output_file_path=output_file_path,
                source_categories=source_categories,
                memory_context=repair_memory_context,
            )
            fixed_code = normalize_kotlin_test_code(fixed_code, source_code=source_code)
            validation_issues = validate_generated_test_code(
                fixed_code,
                output_file_path,
                source_code=source_code,
            )
            if fixed_code.strip() and not validation_issues and text_fingerprint(fixed_code) != current_test_fingerprint:
                repair_round += 1
                await mcp_tools.write_file(output_file_path, fixed_code)
                log_block("BATCH FULL-FILE REPAIR CODE", fixed_code, category="code", console=False)
                applied_repairs.append(
                    {
                        "group_key": batch_group_key,
                        "fixed_code": fixed_code,
                        "repair_categories": {"syntax_or_file_shape", "api_signature_mismatch"},
                    }
                )
                log_message("🔧 Batch full-file repair updated the generated test.", category="fix")
                log_message("🔁 Re-running Gradle after batch full-file repair...", category="gradle")
                continue

            if validation_issues:
                log_message("⚠️ Batch full-file repair failed local validation:", category="warning")
                for issue in validation_issues:
                    log_message(f"   - {issue}", category="warning")
            log_message(
                "⚠️ Small compile-error batch could not be repaired safely; stopping instead of switching to group-wise repair.",
                category="warning",
            )
            return False

        causal_plan = build_causal_repair_plan(groups)
        log_block(
            "ROOT CAUSE PLAN BEFORE REPAIR",
            summarize_causal_repair_plan(causal_plan) or "No causal repair causes found.",
            category="fix",
            console=True,
        )

        remaining_causes = []
        for cause in causal_plan:
            repair_location_key = causal_repair_fingerprint(cause)
            if repair_location_key in skipped_repair_locations:
                continue
            remaining_causes.append((cause, repair_location_key))

        if not remaining_causes:
            log_message("⚠️ No remaining causal error locations to repair.", category="warning")
            return False

        selected_cause, repair_location_key = remaining_causes[0]
        group_key = selected_cause.primary_key
        focused_error_block = format_causal_repair_block(selected_cause)

        repair_location_attempt_counts[repair_location_key] = (
            repair_location_attempt_counts.get(repair_location_key, 0) + 1
        )

        if repair_location_attempt_counts[repair_location_key] > max_unchanged_error_repair_attempts():
            log_message(
                f"⚠️ Root cause '{group_key}' stayed at the same causal location after "
                f"{repair_location_attempt_counts[repair_location_key] - 1} repair attempts. "
                "Moving to the next causal location without pruning generated coverage.",
                category="warning",
            )
            if group_key.startswith("JUnit test failure") or "Assertion" in group_key or "Runtime exception" in group_key:
                log_message(
                    "🧭 This unresolved runtime/test failure may be a source-code bug candidate "
                    "if the generated test setup is valid.",
                    category="warning",
                )
                log_block(
                    "SOURCE BUG CANDIDATE CONTEXT",
                    f"{group_key}\n\n{source_risk_context}\n\n{focused_error_block}",
                    category="warning",
                    console=False,
                )

            skipped_repair_locations.add(repair_location_key)
            continue

        repair_round += 1

        safe_group_name = re.sub(
            r'[^a-zA-Z0-9_-]',
            '_',
            group_key,
        )[:50]

        log_path = (
            output_file_path
            + f".gradle-group-{repair_round}-{safe_group_name}.log"
        )

        await mcp_tools.write_file(
            log_path,
            focused_error_block,
        )

        log_section(f"REPAIR ROUND {repair_round}: {group_key}", category="fix")
        log_message(f"❌ Fixing causal root cause: {group_key}", category="fix")
        log_message(f"🔁 Repair round: {repair_round}", category="fix")
        log_message(f"🔎 Root cause category: {selected_cause.category}", category="fix")
        log_message(f"🎯 Confidence: {selected_cause.confidence:.2f}", category="fix")
        log_block("CAUSAL FOCUSED ERROR BLOCK", focused_error_block, category="fix", console=True)

        current_test_code = await mcp_tools.read_file(output_file_path)
        current_test_fingerprint = text_fingerprint(current_test_code)
        log_block("CURRENT TEST CODE BEFORE REPAIR", current_test_code, category="code", console=False)

        log_message("🔎 Collecting verified context through MCP tools...", category="context")
        verified_context = await collect_verified_repair_context(
            mcp_tools=mcp_tools,
            project_root=project_root,
            output_file_path=output_file_path,
            focused_error_block=focused_error_block,
            group_key=group_key,
        )

        repair_memory_context = retrieve_repair_lessons(
            source_categories,
            repair_categories={selected_cause.category},
        )

        context_path = (
            output_file_path
            + f".verified-context-{repair_round}-{safe_group_name}.log"
        )
        await mcp_tools.write_file(context_path, verified_context or "No verified context collected.")
        log_block("VERIFIED REPAIR CONTEXT", verified_context or "No verified context collected.", category="context", console=False)

        patches = repair_focused_error_patch_streaming(
            class_name=class_name,
            source_code=source_code,
            current_test_code=current_test_code,
            focused_error_block=focused_error_block,
            verified_context=verified_context,
            group_key=group_key,
            source_risk_context=source_risk_context,
            output_file_path=output_file_path,
            source_categories=source_categories,
            memory_context=repair_memory_context,
        )

        if patches:
            applied_patch_results, failed_patch_count = await _apply_repair_patches(
                mcp_tools,
                output_file_path,
                patches,
                "⚠️ MCP patch failed because old_text was not found.",
                "FAILED JSON PATCH",
            )

            if applied_patch_results:
                patched_test_code = await mcp_tools.read_file(output_file_path)
                if text_fingerprint(patched_test_code) == current_test_fingerprint:
                    log_message(
                        "⚠️ JSON patch reported success but did not change the test file. "
                        "Falling back to full-file repair.",
                        category="warning",
                    )
                else:
                    log_message(
                        f"🔧 Applied {len(applied_patch_results)} JSON patch(es); "
                        f"{failed_patch_count} failed exact matching.",
                        category="fix",
                    )
                    log_block("APPLIED JSON PATCHES", json.dumps(patches, indent=2), category="fix", console=False)

                    applied_repairs.append(
                        {
                            "group_key": group_key,
                            "patch": json.dumps(patches, indent=2),
                            "repair_categories": {selected_cause.category},
                        }
                    )

                    log_message("🔁 Re-running Gradle to verify this group is resolved...", category="gradle")
                    continue

            if applied_patch_results:
                log_message(
                    "⚠️ No effective JSON patch changes remained. Falling back to full-file repair.",
                    category="warning",
                )
            else:
                log_message("⚠️ No JSON patches applied. Falling back to full-file repair.", category="warning")
        else:
            log_message("⚠️ Patch repair did not return valid JSON patches. Falling back to full-file repair.", category="warning")

        fixed_code = repair_focused_error_block_streaming(
            class_name=class_name,
            source_code=source_code,
            current_test_code=current_test_code,
            focused_error_block=focused_error_block,
            verified_context=verified_context,
            group_key=group_key,
            source_risk_context=source_risk_context,
            output_file_path=output_file_path,
            source_categories=source_categories,
            memory_context=repair_memory_context,
        )

        if not fixed_code.strip():
            log_message("⚠️ Full-file fallback repair returned empty code. Stopping.", category="warning")
            return False

        fixed_code = normalize_kotlin_test_code(fixed_code, source_code=source_code)
        post_repair_validation_issues = validate_generated_test_code(
            fixed_code,
            output_file_path,
            source_code=source_code,
        )
        if post_repair_validation_issues:
            log_message("⚠️ Full-file repair failed local validation:", category="warning")
            for issue in post_repair_validation_issues:
                log_message(f"   - {issue}", category="warning")

            validation_error_block = (
                "RELATED ERROR GROUP: Local generated-test validation failure after full-file repair\n\n"
                + "\n".join(f"- {issue}" for issue in post_repair_validation_issues)
            )
            validation_repaired_code = repair_focused_error_block_streaming(
                class_name=class_name,
                source_code=source_code,
                current_test_code=fixed_code,
                focused_error_block=validation_error_block,
                verified_context=(
                    "Local deterministic validation failed after full-file repair. "
                    "Treat these validation messages as authoritative guardrails and repair only the generated test file."
                ),
                group_key="Local generated-test validation failure after full-file repair",
                source_risk_context=source_risk_context,
                output_file_path=output_file_path,
                source_categories=source_categories,
                memory_context=retrieve_repair_lessons(
                    source_categories,
                    repair_categories={"fixture_strategy"},
                ),
            )
            validation_repaired_code = normalize_kotlin_test_code(
                validation_repaired_code,
                source_code=source_code,
            )
            if validation_repaired_code.strip():
                remaining_validation_issues = validate_generated_test_code(
                    validation_repaired_code,
                    output_file_path,
                    source_code=source_code,
                )
                if not remaining_validation_issues:
                    fixed_code = validation_repaired_code
                    log_message("🔧 Applied local-validation repair after full-file fallback.", category="fix")
                else:
                    log_message(
                        "⚠️ Local-validation repair still violates guardrails; moving to the next repairable error location.",
                        category="warning",
                    )
                    skipped_repair_locations.add(repair_location_key)
                    continue

        if text_fingerprint(fixed_code) == current_test_fingerprint:
            log_message(
                "⚠️ Full-file fallback returned the same test file. "
                "Moving to the next repairable error location without pruning generated coverage.",
                category="warning",
            )
            skipped_repair_locations.add(repair_location_key)
            continue

        await mcp_tools.write_file(output_file_path, fixed_code)
        log_block("FULL-FILE REPAIR CODE", fixed_code, category="code", console=False)

        applied_repairs.append(
            {
                "group_key": group_key,
                "fixed_code": fixed_code,
                "repair_categories": {selected_cause.category},
            }
        )

        log_message(f"🔧 Full-file fallback updated generated test through MCP: {output_file_path}", category="fix")
        log_message("🔁 Re-running Gradle to verify this group is resolved...", category="gradle")


def _incremental_coverage_repair_context(coverage_opportunity_plan, source_code: str, gap, incremental_strategy: str) -> str:
    from UnitTest_gen.kotlin.incremental_coverage import _build_coverage_repair_context

    return _build_coverage_repair_context(coverage_opportunity_plan, source_code, gap, incremental_strategy)


def _is_destructive_test_deletion_patch(patch_text: str) -> bool:
    lower = (patch_text or "").lower()
    return "delete entire" in lower or '"op": "delete"' in lower


def _repair_shrinks_test_count(before: str, after: str) -> bool:
    before_count = len(re.findall(r"@Test\b", before or ""))
    after_count = len(re.findall(r"@Test\b", after or ""))
    return after_count < before_count
