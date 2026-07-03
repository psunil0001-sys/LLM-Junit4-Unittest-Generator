# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Orchestrates Kotlin source discovery, generation, verification, and cleanup.
import os
import argparse
import sys
import signal
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from UnitTest_gen.kotlin.gradle_analysis.junit import is_gradle_success, text_fingerprint
from UnitTest_gen.kotlin.coverage_analysis import (
    coverage_gap_delta_summary,
    find_latest_kover_xml_for_context,
    kover_report_module_dirs,
    parse_latest_coverage_gap,
)
from UnitTest_gen.kotlin.blocked_coverage_report import (
    item_from_opportunity,
    item_from_stop,
    refresh_kover_gap_reports,
    write_blocked_coverage_report,
    write_hard_stop_blocked_report,
)
from UnitTest_gen.core.logging_utils import (
    PipelineLogger,
    archive_generated_side_files,
    get_pipeline_log_dir,
    log_block,
    log_message,
    log_section,
    set_active_logger,
)
from UnitTest_gen.core.mcp_tools import LocalMcpTools
from UnitTest_gen.kotlin.memory_context import retrieve_generation_lessons, retrieve_repair_lessons
from UnitTest_gen.core.pipeline_config import apply_cli_args, load_config_from_env, set_active_config
from UnitTest_gen.core.model_runtime import (
    CHAT_BASE_URL,
    apply_pipeline_config,
    configure_vector_cache_scope,
    is_model_server_available,
    load_llama_kv_cache,
    save_llama_kv_cache,
    set_model_stuck_detector_enabled,
    set_save_llama_slot_bin,
)
from UnitTest_gen.kotlin.prompting import register_kotlin_stream_hooks
from UnitTest_gen.core.server_manager import LocalServerManager
from UnitTest_gen.kotlin.kotlin_analysis import (
    analyze_source_bug_risks,
    classify_source,
)
from UnitTest_gen.kotlin.project_context import (
    check_and_add_dependencies,
    retrieve_classified_context,
    default_gradle_tasks_for_target,
    delete_vector_cache_file,
    derive_test_output_path,
    discover_target_sources,
    ensure_hilt_test_activity_support,
    ensure_project_index_cache,
    find_existing_test_file_referencing_class,
    find_file_data_for_path,
    find_owning_module_dir,
    find_source_root_for_path,
    is_path_inside,
    resolve_path_against_root,
    start_embedding_server_for_cache_if_needed,
    stop_gradle_daemons,
)
from UnitTest_gen.kotlin.prompting.generation import generate_test_code_streaming
from UnitTest_gen.kotlin.guardrail_catalog import enrich_validation_error_block
from UnitTest_gen.kotlin.prompting.repair import repair_focused_error_block_streaming
from UnitTest_gen.kotlin.repair_flow import verify_and_repair_test_with_mcp
from UnitTest_gen.kotlin.incremental_coverage import build_coverage_opportunity_plan, run_incremental_coverage_generation
from UnitTest_gen.kotlin.mcp_tool_adapter import run_gradle_with_heartbeat
from UnitTest_gen.kotlin.test_code_utils.validate import normalize_kotlin_test_code, remember_successful_generation_if_high_quality, validate_generated_test_code
from UnitTest_gen.kotlin.static_analysis import (
    SemgrepUnavailableError,
    analyze_kotlin_code,
    require_semgrep_preflight,
)


def format_source_elapsed_minutes(start_time: float) -> str:
    elapsed_minutes = (time.monotonic() - start_time) / 60.0
    return f"{elapsed_minutes:.2f} min"


def _full_generation_coverage_improved(before_gap, after_gap) -> bool:
    if after_gap is None:
        return False
    if before_gap is None:
        return after_gap.has_any_coverage()

    before_line_missed, before_line_covered = before_gap.line_coverage
    after_line_missed, after_line_covered = after_gap.line_coverage
    before_branch_missed, before_branch_covered = before_gap.branch_coverage
    after_branch_missed, after_branch_covered = after_gap.branch_coverage

    if after_line_covered < before_line_covered or after_branch_covered < before_branch_covered:
        return False
    if after_line_missed > before_line_missed or after_branch_missed > before_branch_missed:
        return False
    if set(after_gap.missed_lines) - set(before_gap.missed_lines):
        return False
    if set(after_gap.partial_branch_lines) - set(before_gap.partial_branch_lines):
        return False
    if set(after_gap.missed_methods + after_gap.partial_branch_methods) - set(
        before_gap.missed_methods + before_gap.partial_branch_methods
    ):
        return False

    return bool(
        after_line_covered > before_line_covered
        or after_branch_covered > before_branch_covered
        or after_line_missed < before_line_missed
        or after_branch_missed < before_branch_missed
        or set(before_gap.missed_lines) - set(after_gap.missed_lines)
        or set(before_gap.partial_branch_lines) - set(after_gap.partial_branch_lines)
        or set(before_gap.missed_methods + before_gap.partial_branch_methods)
        - set(after_gap.missed_methods + after_gap.partial_branch_methods)
    )


def _full_generation_coverage_delta_summary(before_gap, after_gap) -> str:
    if before_gap is None and after_gap is None:
        return "### FULL GENERATION KOVER DELTA\nNo baseline or final Kover entry was found for this source."
    if before_gap is None:
        return "\n".join(
            [
                "### FULL GENERATION KOVER DELTA",
                "Before: no Kover entry for this source.",
                f"After line missed/covered: {after_gap.line_coverage}",
                f"After branch missed/covered: {after_gap.branch_coverage}",
                f"After missed lines: {sorted(after_gap.missed_lines) or 'none'}",
                f"After branch lines: {sorted(after_gap.partial_branch_lines) or 'none'}",
            ]
        )
    if after_gap is None:
        return "\n".join(
            [
                "### FULL GENERATION KOVER DELTA",
                f"Before line missed/covered: {before_gap.line_coverage}",
                f"Before branch missed/covered: {before_gap.branch_coverage}",
                "After: no Kover entry for this source.",
            ]
        )
    return coverage_gap_delta_summary(before_gap, after_gap)


def _dedupe_blocked_items(items):
    deduped = []
    seen = set()
    for item in items:
        key = (item.entry, item.lines, item.branches, item.reason)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _full_generation_no_delta_report_items(source_file_path: str, source_code: str, source_categories, final_gap):
    evidence = (
        "Full generation passed Gradle/Kover verification but did not improve target source coverage. "
        "The generated test was restored or removed because accepted tests must improve Kover."
    )
    items = [
        item_from_stop(
            source_file_path,
            "Full generation coverage acceptance",
            "already_tested_no_kover_delta: Full generation passed Gradle/Kover but did not improve target source coverage.",
            evidence,
        )
    ]
    if final_gap:
        opportunity_plan = build_coverage_opportunity_plan(
            source_code,
            final_gap,
            source_categories,
        )
        items.extend(
            item_from_opportunity(source_file_path, opportunity)
            for opportunity in opportunity_plan.get("blocked", [])
        )
    return _dedupe_blocked_items(items)


def _opportunity_plan_for_gap(source_code: str, gap, source_categories):
    if not gap:
        return None
    opportunity_plan = build_coverage_opportunity_plan(
        source_code,
        gap,
        source_categories,
    )
    return opportunity_plan


async def _restore_or_delete_generated_output(mcp_tools: LocalMcpTools, output_file_path: str, existed_before: bool, original_code: str):
    if existed_before:
        await mcp_tools.write_file(output_file_path, original_code)
        log_message("↩️ Restored original test file after full-generation coverage rejection.", category="warning")
    else:
        await mcp_tools.delete_file(output_file_path)
        log_message("🗑️ Deleted generated test file after full-generation coverage rejection.", category="warning")


async def generate_android_tests(
    target_path,
    output_base_directory,
    project_root,
    mcp_tools: LocalMcpTools,
    gradle_offline=True,
    gradle_tasks=None,
    index_root=None,
    server_manager: LocalServerManager | None = None,
    incremental_coverage_enabled=True,
    incremental_coverage_rounds: int = 3,
):
    try:
        semgrep_runtime_version = require_semgrep_preflight()
    except SemgrepUnavailableError as exc:
        print(f"❌ Mandatory Semgrep preflight failed: {exc}")
        return False
    print(f"🔎 Semgrep static-policy preflight: {semgrep_runtime_version}")

    project_root = os.path.abspath(project_root)
    target_path = resolve_path_against_root(target_path, project_root)
    index_root = (
        resolve_path_against_root(index_root, project_root)
        if index_root
        else os.environ.get("TESTGEN_INDEX_ROOT")
    )
    if index_root:
        index_root = resolve_path_against_root(index_root, project_root)

    configure_vector_cache_scope(target_path)

    if not gradle_tasks:
        gradle_tasks = default_gradle_tasks_for_target(project_root, target_path)

    target_source_root = find_source_root_for_path(target_path)
    shared_index_mode = bool(index_root)
    index_scan_dir = index_root or target_source_root
    shared_project_index = None

    if shared_index_mode:
        delete_vector_cache_file("before shared --index-root cache build")
        try:
            await start_embedding_server_for_cache_if_needed(server_manager)
            shared_project_index = ensure_project_index_cache(index_scan_dir)
        finally:
            if server_manager:
                server_manager.stop_embedding_server_if_started()
        if not shared_project_index:
            print("❌ Pipeline ended: No target compilation units found.")
            delete_vector_cache_file("after empty shared --index-root cache build")
            return

        if os.path.isfile(target_path):
            index_key, file_data = find_file_data_for_path(shared_project_index, target_path)
            targets = {index_key: file_data} if file_data else {}
        else:
            targets = {
                class_name: data
                for class_name, data in shared_project_index.items()
                if is_path_inside(data["path"], target_path)
            }
    else:
        delete_vector_cache_file("before per-source cache lifecycle")
        targets = discover_target_sources(target_path, target_source_root)

    if not targets:
        print(f"❌ No Kotlin source files found for target: {target_path}")
        if shared_index_mode:
            delete_vector_cache_file("after no targets in shared --index-root cache")
        return

    output_base_directory = (
        resolve_path_against_root(output_base_directory, project_root)
        if output_base_directory
        else None
    )

    print("\n🚀 Starting Test Generation Pipeline with local MCP tools...")
    print(f"📄 Target path: {target_path}")
    print(f"📦 Target source root: {target_source_root}")
    print(f"🗂️ Index root: {index_scan_dir}")
    print(
        "📦 Vector cache lifecycle: "
        + ("shared once for --index-root, deleted at run end" if shared_index_mode else "per source file, deleted after each source")
    )
    print(f"🧮 Files queued: {len(targets)}")
    # print(f"📁 Gradle project root: {project_root}")
    # print(f"📄 Target path: {target_path}")
    # print(f"📦 Source root: {base_scan_dir}")
    # print(f"🧪 Gradle task(s) via MCP: {' '.join(gradle_tasks)}\n")

    all_targets_succeeded = True

    for index_key, discovered_file_data in targets.items():
            source_start_time = time.monotonic()
            source_success = False
            class_name = discovered_file_data.get("class_name", index_key)
            source_file_path = discovered_file_data["path"]

            if shared_index_mode:
                project_index = shared_project_index
                file_data = discovered_file_data
            else:
                source_index_root = find_source_root_for_path(source_file_path)
                configure_vector_cache_scope(source_file_path)
                delete_vector_cache_file("before per-source cache build")
                try:
                    await start_embedding_server_for_cache_if_needed(server_manager)
                    project_index = ensure_project_index_cache(source_index_root)
                finally:
                    if server_manager:
                        server_manager.stop_embedding_server_if_started()
            _, indexed_file_data = find_file_data_for_path(project_index, source_file_path)
            file_data = indexed_file_data or discovered_file_data
            if not indexed_file_data:
                print(
                    f"⚠️ Source file was not found in per-source project index; using discovered file data: {source_file_path}",
                )

            derived_output_file_path = derive_test_output_path(
                source_file_path=source_file_path,
                source_root=target_source_root,
                output_base_directory=output_base_directory,
            )
            output_file_path = derived_output_file_path
            referenced_existing_test_file = None
            if incremental_coverage_enabled:
                referenced_existing_test_file = find_existing_test_file_referencing_class(
                    class_name=class_name,
                    source_file_path=source_file_path,
                    source_root=target_source_root,
                    derived_output_file_path=derived_output_file_path,
                )
                if (
                    referenced_existing_test_file
                    and os.path.abspath(referenced_existing_test_file) == os.path.abspath(derived_output_file_path)
                ):
                    output_file_path = referenced_existing_test_file

            pipeline_log_dir = get_pipeline_log_dir()
            test_log_path = os.path.join(
                pipeline_log_dir,
                os.path.basename(source_file_path).replace(".kt", ".testgen.log"),
            )
            set_active_logger(PipelineLogger(test_log_path))
            log_section(f"TEST GENERATION START: {source_file_path}", category="info")
            log_message(f"📁 Gradle project root: {project_root}", category="info")
            log_message(f"📄 Source file: {source_file_path}", category="info")
            log_message(f"🧪 Output test file: {output_file_path}", category="info")
            if referenced_existing_test_file and referenced_existing_test_file != derived_output_file_path:
                log_message(
                    "🔎 Found another existing test file referencing "
                    f"{class_name}; using it as pattern/indirect-coverage context only: {referenced_existing_test_file}",
                    category="context",
                )
                log_message(
                    f"🧪 Source-owned generated-test path remains the output/merge anchor: {derived_output_file_path}",
                    category="context",
                )
            log_message(f"📝 Pipeline log file: {test_log_path}", category="info")
            log_message(f"🧪 Gradle task(s): {' '.join(gradle_tasks)}", category="gradle")

            raw_source_code = file_data["content"]
            source_code = raw_source_code
            source_analysis_report = analyze_kotlin_code(
                source_code,
                source_file_path,
                persist=True,
            )
            source_profile = classify_source(source_code, source_analysis_report)
            source_categories = source_profile.categories
            check_and_add_dependencies(project_root, source_file_path, source_profile)
            ensure_hilt_test_activity_support(output_file_path, source_code)
            source_risk_context = analyze_source_bug_risks(source_code)
            log_block("SOURCE CODE UNDER TEST", raw_source_code, category="context", console=False)
            log_block("STATIC SOURCE BUG-HUNTING TARGETS", source_risk_context, category="context", console=False)

            dependency_context = retrieve_classified_context(
                source_profile,
                source_file_path,
                source_code,
                project_index,
                project_root,
                phase="generation",
            )
            log_block("DEPENDENCY CONTEXT", dependency_context, category="context", console=False)

            generation_memory_context = retrieve_generation_lessons(
                source_categories,
                class_name=class_name,
            )

            if incremental_coverage_enabled:
                incremental_rounds = max(1, int(incremental_coverage_rounds or 1))
                direct_test_existed_at_incremental_start = os.path.exists(derived_output_file_path)
                incremental_any_success = False
                incremental_handled = False
                incremental_should_fallback_to_full = False

                for incremental_round in range(1, incremental_rounds + 1):
                    log_section(
                        f"INCREMENTAL COVERAGE ROUND {incremental_round}/{incremental_rounds}: {class_name}",
                        category="context",
                    )
                    incremental_result = await run_incremental_coverage_generation(
                        mcp_tools=mcp_tools,
                        class_name=class_name,
                        source_code=source_code,
                        source_file_path=source_file_path,
                        output_file_path=output_file_path,
                        project_root=project_root,
                        gradle_offline=gradle_offline,
                        gradle_tasks=gradle_tasks,
                        dependency_context=dependency_context,
                        source_risk_context=source_risk_context,
                        source_categories=source_categories,
                        memory_context=generation_memory_context,
                        incremental_rounds=incremental_rounds,
                    )

                    if incremental_result is None:
                        incremental_should_fallback_to_full = not incremental_any_success
                        incremental_handled = incremental_any_success
                        break

                    incremental_handled = True
                    if incremental_result:
                        incremental_any_success = True
                        module_dir = find_owning_module_dir(project_root, source_file_path)
                        latest_gap = await parse_latest_coverage_gap(
                            module_dir,
                            source_file_path,
                            source_code,
                            project_root=project_root,
                            gradle_tasks=gradle_tasks,
                        )
                        if latest_gap and not latest_gap.has_gaps():
                            log_message(
                                "✅ Kover shows no remaining target gaps after incremental coverage round "
                                f"{incremental_round}.",
                                category="success",
                            )
                            break
                        if not direct_test_existed_at_incremental_start:
                            log_message(
                                "✅ Created a source-owned standalone test for a previously indirect-only source. "
                                "Stopping this run before creating extra supplemental classes; rerun to target remaining gaps.",
                                category="success",
                            )
                            break
                        if incremental_round < incremental_rounds:
                            log_message(
                                "🔁 Incremental coverage improved verified Kover gaps; continuing with the "
                                f"next round for remaining gaps ({incremental_round + 1}/{incremental_rounds}).",
                                category="context",
                            )
                            output_file_path = derived_output_file_path
                            continue
                    break

                if incremental_handled and not incremental_should_fallback_to_full:
                    source_success = incremental_any_success
                    log_section(f"TEST GENERATION END: {source_file_path}", category="info")
                    status_label = "passed" if source_success else "failed"
                    log_message(
                        f"⏱️ Source file completed in {format_source_elapsed_minutes(source_start_time)} ({status_label}).",
                        category="success" if source_success else "warning",
                    )
                    archive_generated_side_files(output_file_path, source_file_path)
                    if not shared_index_mode:
                        delete_vector_cache_file("after per-source completion")
                    if not source_success:
                        all_targets_succeeded = False
                    continue

            full_generation_test_existed_before = os.path.exists(output_file_path)
            full_generation_original_test_code = (
                await mcp_tools.read_file(output_file_path)
                if full_generation_test_existed_before
                else ""
            )
            module_dir = find_owning_module_dir(project_root, source_file_path)
            baseline_gradle_output = await run_gradle_with_heartbeat(
                mcp_tools,
                project_root,
                offline=gradle_offline,
                tasks=gradle_tasks,
            )
            log_block(
                "FULL GENERATION BASELINE GRADLE/KOVER OUTPUT",
                baseline_gradle_output or "No Gradle output captured.",
                category="gradle",
                console=True,
            )
            if not is_gradle_success(baseline_gradle_output):
                write_blocked_coverage_report(
                    source_file_path,
                    [
                        item_from_stop(
                            source_file_path,
                            "Full generation baseline verification",
                            "invalid_fixture: Baseline Gradle/Kover failed before full generation.",
                            "Full generation was skipped because coverage acceptance requires a passing baseline Gradle/Kover task.",
                        )
                    ],
                )
                log_message(
                    "❌ Baseline Gradle/Kover failed before full generation. Skipping generated test write.",
                    category="error",
                )
                source_success = False
                all_targets_succeeded = False
                continue

            baseline_kover_xml = find_latest_kover_xml_for_context(project_root, gradle_tasks, module_dir)
            if not baseline_kover_xml:
                searched_roots = ", ".join(
                    os.path.join(candidate, "build", "reports", "kover")
                    for candidate in kover_report_module_dirs(project_root, gradle_tasks, module_dir)
                )
                write_blocked_coverage_report(
                    source_file_path,
                    [
                        item_from_stop(
                            source_file_path,
                            "Full generation Kover report discovery",
                            "android_environment_unavailable: No Kover XML report was found before full generation.",
                            searched_roots,
                        )
                    ],
                )
                log_message(
                    "❌ Full generation requires Kover XML for coverage acceptance, but no report was found. "
                    "Skipping generated test write.",
                    category="error",
                )
                source_success = False
                all_targets_succeeded = False
                continue

            baseline_gap = await parse_latest_coverage_gap(
                module_dir,
                source_file_path,
                source_code,
                project_root=project_root,
                gradle_tasks=gradle_tasks,
            )
            if baseline_gap:
                log_block(
                    "FULL GENERATION BASELINE KOVER GAP",
                    _full_generation_coverage_delta_summary(None, baseline_gap),
                    category="context",
                    console=True,
                )
            else:
                log_message(
                    "ℹ️ Baseline Kover has no entry for this source; full generation must create measurable source coverage.",
                    category="context",
                )

            clean_code = generate_test_code_streaming(
                class_name=class_name,
                source_code=source_code,
                dependency_context=dependency_context,
                source_risk_context=source_risk_context,
                output_file_path=output_file_path,
                source_categories=source_categories,
                memory_context=generation_memory_context,
            )
            clean_code = normalize_kotlin_test_code(
                clean_code,
                source_code=source_code,
                output_file_path=output_file_path,
            )

            if not clean_code.strip():
                write_hard_stop_blocked_report(
                    source_file_path,
                    "Full generation model output",
                    "invalid_generation_output: Model returned empty test code before Gradle verification.",
                    "Full generation produced no Kotlin test code, so coverage acceptance could not be attempted.",
                    opportunity_plan=_opportunity_plan_for_gap(source_code, baseline_gap, source_categories),
                )
                log_message(f"❌ Empty generated test for {class_name}. Skipping Gradle verification.", category="error")
                archive_generated_side_files(output_file_path, source_file_path)
                log_message(
                    f"⏱️ Source file completed in {format_source_elapsed_minutes(source_start_time)} (failed).",
                    category="warning",
                )
                all_targets_succeeded = False
                if not shared_index_mode:
                    delete_vector_cache_file("after per-source completion")
                continue

            validation_issues = validate_generated_test_code(
                clean_code,
                output_file_path,
                source_code=source_code,
                opportunity_plan=_opportunity_plan_for_gap(source_code, baseline_gap, source_categories),
            )
            if validation_issues:
                log_message("⚠️ Generated test failed static validation:", category="warning")
                for issue in validation_issues:
                    log_message(f"   - {issue}", category="warning")

            await mcp_tools.write_file(output_file_path, clean_code)
            log_block("INITIAL GENERATED TEST CODE", clean_code, category="code", console=False)
            log_message(f"✅ Saved Android test suite through MCP to {output_file_path}\n", category="success")

            if validation_issues:
                focused_error_block = (
                    "RELATED ERROR GROUP: Local generated-test validation failure\n\n"
                    + enrich_validation_error_block(validation_issues)
                )
                repaired_code = repair_focused_error_block_streaming(
                    class_name=class_name,
                    source_code=source_code,
                    current_test_code=clean_code,
                    focused_error_block=focused_error_block,
                    verified_context=(
                        "Local deterministic validation failed before Gradle. Treat these validation "
                        "messages as authoritative guardrails and repair only the generated test file."
                    ),
                    group_key="Local generated-test validation failure",
                    source_risk_context=source_risk_context,
                    output_file_path=output_file_path,
                    source_categories=source_categories,
                    memory_context=retrieve_repair_lessons(
                        source_categories,
                        repair_categories={"fixture_strategy"},
                    ),
                )
                repaired_code = normalize_kotlin_test_code(
                    repaired_code,
                    source_code=source_code,
                    output_file_path=output_file_path,
                )
                if repaired_code.strip() and text_fingerprint(repaired_code) != text_fingerprint(clean_code):
                    await mcp_tools.write_file(output_file_path, repaired_code)
                    log_block("STATIC VALIDATION REPAIR CODE", repaired_code, category="code", console=False)
                    log_message("🔧 Applied static validation repair before Gradle verification.", category="fix")
                else:
                    log_message("⚠️ Static validation repair returned no effective change; continuing to Gradle.", category="warning")

            source_success = await verify_and_repair_test_with_mcp(
                mcp_tools=mcp_tools,
                class_name=class_name,
                source_code=source_code,
                output_file_path=output_file_path,
                project_root=project_root,
                source_file_path=source_file_path,
                gradle_offline=gradle_offline,
                gradle_tasks=gradle_tasks,
                source_risk_context=source_risk_context,
                record_successful_generation=False,
            )

            if not source_success:
                write_hard_stop_blocked_report(
                    source_file_path,
                    "Full generation Gradle/repair verification",
                    "invalid_generated_fixture: Generated test failed Gradle or repair verification.",
                    "The generated test was restored or deleted because it did not pass Gradle/repair verification.",
                    opportunity_plan=_opportunity_plan_for_gap(source_code, baseline_gap, source_categories),
                )
                await _restore_or_delete_generated_output(
                    mcp_tools,
                    output_file_path,
                    full_generation_test_existed_before,
                    full_generation_original_test_code,
                )
            else:
                final_gradle_output = await run_gradle_with_heartbeat(
                    mcp_tools,
                    project_root,
                    offline=gradle_offline,
                    tasks=gradle_tasks,
                )
                log_block(
                    "FULL GENERATION FINAL GRADLE/KOVER OUTPUT",
                    final_gradle_output or "No Gradle output captured.",
                    category="gradle",
                    console=True,
                )
                final_gap = None
                if is_gradle_success(final_gradle_output):
                    final_gap = await parse_latest_coverage_gap(
                        module_dir,
                        source_file_path,
                        source_code,
                        project_root=project_root,
                        gradle_tasks=gradle_tasks,
                    )
                log_block(
                    "FULL GENERATION KOVER ACCEPTANCE DELTA",
                    _full_generation_coverage_delta_summary(baseline_gap, final_gap),
                    category="context",
                    console=True,
                )
                wrote_final_acceptance_report = False
                if not is_gradle_success(final_gradle_output):
                    write_hard_stop_blocked_report(
                        source_file_path,
                        "Full generation final Gradle/Kover verification",
                        "invalid_generated_fixture: Final Gradle/Kover verification failed after repair.",
                        "Full generation could not be accepted because the final Gradle/Kover task failed after repair.",
                        opportunity_plan=_opportunity_plan_for_gap(source_code, baseline_gap, source_categories),
                    )
                    wrote_final_acceptance_report = True
                if is_gradle_success(final_gradle_output) and final_gap is None:
                    write_hard_stop_blocked_report(
                        source_file_path,
                        "Full generation final Kover parsing",
                        "missing_kover_source_entry: Gradle passed but final Kover data for this source could not be parsed.",
                        "Full generation could not be accepted because the final Kover XML did not contain a parseable entry for the target source.",
                        opportunity_plan=_opportunity_plan_for_gap(source_code, baseline_gap, source_categories),
                    )
                    wrote_final_acceptance_report = True
                if not is_gradle_success(final_gradle_output) or not _full_generation_coverage_improved(baseline_gap, final_gap):
                    if not wrote_final_acceptance_report:
                        write_blocked_coverage_report(
                            source_file_path,
                            _full_generation_no_delta_report_items(
                                source_file_path,
                                source_code,
                                source_categories,
                                final_gap,
                            ),
                        )
                    await _restore_or_delete_generated_output(
                        mcp_tools,
                        output_file_path,
                        full_generation_test_existed_before,
                        full_generation_original_test_code,
                    )
                    log_message(
                        "⚠️ Full generation passed repair flow but was rejected because Kover did not improve.",
                        category="warning",
                    )
                    source_success = False
                else:
                    accepted_test_code = await mcp_tools.read_file(output_file_path)
                    remember_successful_generation_if_high_quality(
                        class_name,
                        accepted_test_code,
                        output_file_path,
                        source_code=source_code,
                    )
                    log_message(
                        "✅ Full generation passed Gradle/Kover and improved target source coverage.",
                        category="success",
                    )

                    if incremental_coverage_enabled and final_gap.has_gaps():
                        log_section(f"POST-FULL INCREMENTAL COVERAGE: {class_name}", category="context")
                        log_message(
                            "Full generation accepted but Kover gaps remain; continuing with incremental coverage.",
                            category="context",
                        )
                        # ponytail: copy pre-full incremental loop; no shared helper until a third caller exists
                        post_full_direct_test_existed = os.path.exists(derived_output_file_path)
                        for incremental_round in range(1, incremental_rounds + 1):
                            log_section(
                                f"POST-FULL INCREMENTAL ROUND {incremental_round}/{incremental_rounds}: {class_name}",
                                category="context",
                            )
                            incremental_result = await run_incremental_coverage_generation(
                                mcp_tools=mcp_tools,
                                class_name=class_name,
                                source_code=source_code,
                                source_file_path=source_file_path,
                                output_file_path=output_file_path,
                                project_root=project_root,
                                gradle_offline=gradle_offline,
                                gradle_tasks=gradle_tasks,
                                dependency_context=dependency_context,
                                source_risk_context=source_risk_context,
                                source_categories=source_categories,
                                memory_context=generation_memory_context,
                                incremental_rounds=incremental_rounds,
                            )

                            if incremental_result is None:
                                break

                            if incremental_result:
                                latest_gap = await parse_latest_coverage_gap(
                                    module_dir,
                                    source_file_path,
                                    source_code,
                                    project_root=project_root,
                                    gradle_tasks=gradle_tasks,
                                )
                                if latest_gap and not latest_gap.has_gaps():
                                    log_message(
                                        "✅ Kover shows no remaining target gaps after post-full incremental round "
                                        f"{incremental_round}.",
                                        category="success",
                                    )
                                    break
                                if not post_full_direct_test_existed:
                                    log_message(
                                        "✅ Created a source-owned standalone test for a previously indirect-only source. "
                                        "Stopping post-full incremental before extra supplemental classes.",
                                        category="success",
                                    )
                                    break
                                if incremental_round < incremental_rounds:
                                    log_message(
                                        "🔁 Post-full incremental improved verified Kover gaps; continuing with the "
                                        f"next round for remaining gaps ({incremental_round + 1}/{incremental_rounds}).",
                                        category="context",
                                    )
                                    output_file_path = derived_output_file_path
                                    continue
                            break

            log_section(f"TEST GENERATION END: {source_file_path}", category="info")
            status_label = "passed" if source_success else "failed"
            log_message(
                f"⏱️ Source file completed in {format_source_elapsed_minutes(source_start_time)} ({status_label}).",
                category="success" if source_success else "warning",
            )
            archive_generated_side_files(output_file_path, source_file_path)
            if not shared_index_mode:
                delete_vector_cache_file("after per-source completion")
            if not source_success:
                all_targets_succeeded = False
    if shared_index_mode:
        delete_vector_cache_file("after shared --index-root run completion")
    refresh_kover_gap_reports(project_root)
    return all_targets_succeeded


async def async_main():
    parser = argparse.ArgumentParser(
        description="AI Android Test Generator with local MCP tools, Gradle coverage, verified context, MCP patch repair, and focused repair loop"
    )

    target_group = parser.add_mutually_exclusive_group(required=True)

    target_group.add_argument(
        "-p",
        "--path",
        type=str,
        help="Target Kotlin file or source directory. Can be absolute or relative to --project-root.",
    )

    target_group.add_argument(
        "-F",
        "--folder",
        type=str,
        help=(
            "Target source folder. All .kt files under this folder are processed one by one. "
            "Can be absolute or relative to --project-root."
        ),
    )

    parser.add_argument(
        "-o",
        "--output",
        type=str,
        default=None,
        help="Optional output test source root. If omitted, src/main/java is mapped to src/test/java.",
    )

    parser.add_argument(
        "-R",
        "--project-root",
        type=str,
        required=True,
        help="Android/Gradle project root where ./gradlew exists.",
    )

    parser.add_argument(
        "--index-root",
        type=str,
        default=None,
        help=(
            "Optional Kotlin indexing root for dependency context. "
            "Use the project root to reuse a full-project vector cache. "
            "Can also be set with TESTGEN_INDEX_ROOT."
        ),
    )

    parser.add_argument(
        "-mcp",
        "--mcp-server",
        type=str,
        required=True,
        help="Path to mcp_gradle_server.py.",
    )

    parser.add_argument(
        "--gradle-offline",
        action="store_true",
        help="Run Gradle with --offline through MCP.",
    )

    parser.add_argument(
        "-G",
        "--gradle-task",
        action="append",
        default=None,
        help=(
            "Gradle task to run during verification. "
            "Repeat for multiple tasks. Defaults to the owning module's testDevDebugUnitTest."
        ),
    )

    parser.add_argument(
        "--skip-model-preflight",
        action="store_true",
        help="Skip the quick 127.0.0.1:8080 availability check before generation.",
    )

    parser.add_argument(
        "-A",
        "--auto-start-servers",
        action="store_true",
        help=(
            "Start configured llama.cpp servers from Python when needed. "
            "Commands can be passed with --coding-server-command/--embedding-server-command "
            "or TESTGEN_CODING_SERVER_COMMAND/TESTGEN_EMBEDDING_SERVER_COMMAND."
        ),
    )

    parser.add_argument(
        "--coding-server-command",
        type=str,
        default=None,
        help="Shell command used to start the coding/generation llama-server.",
    )

    parser.add_argument(
        "--coding-server-cwd",
        type=str,
        default=None,
        help="Working directory used to launch --coding-server-command. Defaults to --project-root.",
    )

    parser.add_argument(
        "--embedding-server-command",
        type=str,
        default=None,
        help="Shell command used to start the embedding llama-server.",
    )

    parser.add_argument(
        "--embedding-server-cwd",
        type=str,
        default=None,
        help="Working directory used to launch --embedding-server-command. Defaults to --project-root.",
    )

    parser.add_argument(
        "--server-startup-timeout",
        type=int,
        default=None,
        help="Seconds to wait for auto-started server /models endpoints. Defaults to TESTGEN_SERVER_STARTUP_TIMEOUT or 120.",
    )

    parser.add_argument(
        "-nst",
        "--no-server-terminal",
        action="store_true",
        help="Start configured servers as background processes instead of launching separate terminal windows.",
    )

    parser.add_argument(
        "--enable-stuck-detector",
        action="store_true",
        help=(
            "Enable strict repetitive stream detection and one retry. "
            "Disabled by default for slow local hardware."
        ),
    )

    parser.add_argument(
        "--disable-incremental-coverage",
        action="store_true",
        help=(
            "When a matching test file already exists, skip Kover-guided supplemental generation "
            "and use the normal full-file generation flow."
        ),
    )

    parser.add_argument(
        "--incremental-coverage-rounds",
        type=int,
        default=3,
        help=(
            "Maximum verified Kover-guided coverage improvement rounds per source file. "
            "Defaults to 3. Use 1 for the previous single-pass behavior."
        ),
    )

    parser.add_argument(
        "--disable-slot-bin-cache",
        action="store_true",
        help=(
            "Disable llama-server slot .bin restore/save for this run. "
            "The global default is controlled by TESTGEN_SAVE_LLAMA_SLOT_BIN."
        ),
    )
    parser.add_argument(
        "--enable-slot-bin-cache",
        action="store_true",
        help=(
            "Enable llama-server slot .bin restore/save for this run. "
            "By default this follows TESTGEN_SAVE_LLAMA_SLOT_BIN and remains disabled when the env var is unset."
        ),
    )

    args = parser.parse_args()

    pipeline_config = apply_cli_args(load_config_from_env(), args)
    set_active_config(pipeline_config)
    apply_pipeline_config(pipeline_config)
    register_kotlin_stream_hooks()

    if args.incremental_coverage_rounds < 1:
        parser.error("--incremental-coverage-rounds must be 1 or greater.")
    if args.enable_stuck_detector:
        set_model_stuck_detector_enabled(True)
    if args.enable_slot_bin_cache and args.disable_slot_bin_cache:
        parser.error("--enable-slot-bin-cache and --disable-slot-bin-cache cannot be used together.")
    if args.enable_slot_bin_cache:
        set_save_llama_slot_bin(True)
    elif args.disable_slot_bin_cache:
        set_save_llama_slot_bin(False)

    project_root = os.path.abspath(os.path.expanduser(args.project_root))
    requested_target = args.folder if args.folder else args.path
    target_path = resolve_path_against_root(requested_target, project_root)
    auto_start_servers = pipeline_config.auto_start_servers
    server_manager = None
    previous_sigterm_handler = signal.getsignal(signal.SIGTERM)

    def _handle_sigterm(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _handle_sigterm)

    if auto_start_servers:
        server_manager = LocalServerManager(
            project_root=project_root,
            coding_command=args.coding_server_command,
            coding_cwd=args.coding_server_cwd,
            embedding_command=args.embedding_server_command,
            embedding_cwd=args.embedding_server_cwd,
            startup_timeout_seconds=args.server_startup_timeout,
            use_terminal=not args.no_server_terminal,
        )

        try:
            if not is_model_server_available():
                server_manager.start_coding_server()
                if not server_manager.wait_until("coding", is_model_server_available):
                    server_manager.stop_all_started_servers()
                    print(f"❌ Auto-started coding server did not become reachable at {CHAT_BASE_URL}.")
                    return False

        except Exception as exc:
            server_manager.stop_all_started_servers()
            print(f"❌ Failed to auto-start configured server(s): {exc}")
            return False

    if not args.skip_model_preflight and not is_model_server_available():
        print(
            f"❌ Local model server is not reachable at {CHAT_BASE_URL}.\n"
            "   Start llama.cpp/OpenAI-compatible server first, use --auto-start-servers, "
            "or rerun with --skip-model-preflight."
        )
        if server_manager:
            server_manager.stop_all_started_servers()
        return False

    load_llama_kv_cache()

    completed_successfully = False
    try:
        async with LocalMcpTools(args.mcp_server, allowed_root=project_root) as mcp_tools:
            completed_successfully = await generate_android_tests(
                target_path=target_path,
                output_base_directory=args.output,
                project_root=project_root,
                mcp_tools=mcp_tools,
                gradle_offline=args.gradle_offline,
                gradle_tasks=args.gradle_task,
                index_root=args.index_root,
                server_manager=server_manager,
                incremental_coverage_enabled=not args.disable_incremental_coverage,
                incremental_coverage_rounds=args.incremental_coverage_rounds,
            )
    except KeyboardInterrupt:
        print("\n⚠️ Generation interrupted. Skipping llama-server binary KV cache save.")
        raise
    finally:
        if completed_successfully:
            save_llama_kv_cache()
        else:
            print("Skipping llama-server binary KV cache save because generation did not complete successfully.")
        delete_vector_cache_file("top-level generator cleanup")
        if server_manager:
            server_manager.stop_all_started_servers()
        stop_gradle_daemons(project_root)
        signal.signal(signal.SIGTERM, previous_sigterm_handler)

    return completed_successfully
