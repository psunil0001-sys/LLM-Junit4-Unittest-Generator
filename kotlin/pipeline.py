"""Plan→coder orchestration, slices, instrumented pass, finish hooks."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from UnitTest_gen.core import io as file_cache
from UnitTest_gen.core.io import log_message
from UnitTest_gen.core.config import agent_env_display_name
from UnitTest_gen.kotlin.codegen import (
    TestFileState,
    read_test_file_state,
    seed_test_skeleton_if_needed,
    target_has_junit_tests,
    text_has_junit_tests,
)

def restore_or_delete(path: str, existed: bool, original: str) -> None:
    target = Path(path)
    if existed:
        target.write_text(original, encoding="utf-8")
        file_cache.invalidate(path)
        log_message("↩️ Restored original test file after rejection.", category="warning")
        return
    if target.exists():
        target.unlink()
        file_cache.invalidate(path)
        log_message("🗑️ Deleted generated test file after rejection.", category="warning")

def snapshot_test(path: str) -> tuple[bool, str]:
    target = Path(path)
    if not target.is_file():
        return False, ""
    return True, file_cache.read_text(path, default="")

def seed_test_skeleton(
    test_path: str,
    *,
    state: TestFileState,
    test_layer=None,
    categories: frozenset[str] | set[str] | None = None,
    source_code: str = "",
) -> None:
    if target_has_junit_tests(test_path):
        log_message(
            f"🔒 Preserving TARGET with @Test (skip skeleton seed): {test_path}",
            category="info",
        )
        return
    if state.has_content:
        return
    agent = agent_env_display_name()
    was_missing = not state.exists
    if not seed_test_skeleton_if_needed(
        test_path,
        state=state,
        test_layer=test_layer,
        categories=categories,
        source_code=source_code,
    ):
        return
    log_message(
        f"{'🌱 Seeded test skeleton' if was_missing else '📄 Upgraded empty TARGET to skeleton'} before {agent}: {test_path}",
        category="info",
    )

def restore_if_junit_tests_wiped(path: str, *, before: str, label: str = "agent") -> bool:
    """Restore ``before`` when it had ``@Test`` and the on-disk file lost them.

    Returns True when a restore was performed.
    """
    if not text_has_junit_tests(before):
        return False
    after = file_cache.read_text(path, default="") if Path(path).is_file() else ""
    if text_has_junit_tests(after) and after.strip():
        return False
    write_test_snapshot(path, True, before)
    log_message(
        f"↩️ Restored TARGET after {label} wiped @Test content: {path}",
        category="warning",
    )
    return True

def refresh_last_green_snapshot(test_path: str) -> tuple[bool, str]:
    state = read_test_file_state(test_path)
    if not state.has_content:
        return state.exists, ""
    contents = file_cache.read_text(test_path, default="")
    if not contents and not Path(test_path).is_file():
        return False, ""
    return True, contents

def write_test_snapshot(path: str, existed: bool, contents: str) -> None:
    target = Path(path)
    if existed or contents.strip():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")
        file_cache.invalidate(path)
        return
    if target.exists():
        target.unlink()
        file_cache.invalidate(path)

def restrict_gap_to_lines(gap, lines: set[int]):
    if gap is None or not lines:
        return gap
    missed = [n for n in (getattr(gap, "missed_lines", None) or []) if int(n) in lines]
    partial = [n for n in (getattr(gap, "partial_branch_lines", None) or []) if int(n) in lines]
    try:
        return replace(gap, missed_lines=missed, partial_branch_lines=partial)
    except TypeError:
        gap.missed_lines = missed
        gap.partial_branch_lines = partial
        return gap

import os

from UnitTest_gen.core.diagnostics import record_source_run
from UnitTest_gen.core.io import log_message, log_section
from UnitTest_gen.core.config import agent_env_log_tag
from UnitTest_gen.kotlin.coverage import reconcile_unreachable_entries

def finish_run(
    result,
    project_root: str,
    source_file_path: str,
    gradle_tasks: list[str],
    module: str,
    *,
    before_gap=None,
    after_gap=None,
    validation_issues: list[str] | None = None,
    coverage_summary: dict | None = None,
) -> None:
    record_source_run(
        source_file_path=source_file_path,
        test_file_path=result.test_file_path,
        module=module,
        outcome=result.outcome,
        accepted=result.accepted,
        gradle_passed=result.gradle_passed,
        kover_improved=result.kover_improved,
        failure_stage=result.failure_stage,
        evidence=result.evidence,
        duration_seconds=result.duration_seconds,
        before_gap=before_gap,
        after_gap=after_gap,
        validation_issues=validation_issues,
        coverage_summary=coverage_summary,
    )
    attempted_lines: set[int] = set()
    covered_lines: set[int] = set()
    for bucket, target in (("attempted", attempted_lines), ("covered", covered_lines)):
        for row in (coverage_summary or {}).get(bucket) or []:
            for line in row.get("lines") or []:
                try:
                    target.add(int(line))
                except (TypeError, ValueError):
                    continue
    reconciled_path = reconcile_unreachable_entries(
        source_file_path=source_file_path,
        attempted_lines=attempted_lines,
        covered_lines=covered_lines,
        accepted=bool(result.accepted),
    )
    if reconciled_path:
        log_message(
            f"🧹 Reconciled stale unreachable entries from newer run evidence → {reconciled_path}",
            category="info",
        )
    log_section(f"{agent_env_log_tag()} AGENT END: {source_file_path}", category="gen")
    status = "passed" if result.accepted else "failed"
    log_message(
        f"⏱️ Source completed in {result.duration_seconds / 60:.2f} min ({status}).",
        category="success" if result.accepted else "warning",
    )

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from UnitTest_gen.core.agent import run_claude
from UnitTest_gen.core.agent import (
    coder_tool_surface,
    describe_coder_endpoint,
    log_coder_slice_preamble)
from UnitTest_gen.core.config import (
    CODER_ALLOWED_TOOLS,
    CODER_AVAILABLE_TOOLS,
    CODER_DENIED_TOOLS,
    FIX_ALLOWED_TOOLS,
    FIX_AVAILABLE_TOOLS,
    FIX_DENIED_TOOLS)
from UnitTest_gen.core.agent import AgentUnavailableError
from UnitTest_gen.core import io as file_cache
from UnitTest_gen.core.gradle import is_gradle_success, log_gradle_result, run_gradle_with_heartbeat
from UnitTest_gen.core.io import log_block, log_message
from UnitTest_gen.core.config import agent_env_display_name, run_instrumented_tests
from UnitTest_gen.core.io import compact_ranges
from UnitTest_gen.kotlin.plan import chunk_plan_by_method, format_plan_for_coder, testable_lines
from UnitTest_gen.kotlin.recipes import format_api_doc_catalog
from UnitTest_gen.kotlin.codegen import read_test_file_state
from UnitTest_gen.kotlin.codegen import validate_generated_test_code, validation_issues_added
from UnitTest_gen.kotlin.coverage import format_kover_for_prompt, parse_latest_coverage_gap
from UnitTest_gen.kotlin.codegen import count_edit_no_match
from UnitTest_gen.kotlin.project import (
    format_gradle_command,
    resolve_fast_verify_invocation,
    resolve_instrumented_compile_invocation,
    resolve_instrumented_gate_tasks,
    resolve_instrumented_verify_invocation,
    GradleInvocation,
)
from UnitTest_gen.kotlin.analysis import PLAIN_UNIT_TAG
from UnitTest_gen.kotlin.coverage import coverage_gap_delta_summary, coverage_improved
from UnitTest_gen.kotlin.layer import TestLayer, test_layer_guidance
from UnitTest_gen.kotlin.prompts import build_agent_prompt, build_fix_prompt
from UnitTest_gen.kotlin.codegen import format_slice_source_projection, format_target_excerpt
from UnitTest_gen.kotlin.project import find_owning_module_dir
from UnitTest_gen.kotlin.coverage import compute_coverage_delta

def _log_edit_no_match(agent_stdout: str, *, phase: str, slice_tag: str) -> None:
    hits = count_edit_no_match(agent_stdout)
    if hits:
        log_message(
            f"⚠️ Edit No match found x{hits} ({phase}, slice={slice_tag or 'n/a'}); "
            "agent should re-view TARGET — pipeline will still run FAST_VERIFY.",
            category="warning")

def _slice_plan_preamble_lines(slice_plan) -> list[str]:
    lines: list[str] = []
    for index, item in enumerate(getattr(slice_plan, "testable", ()) or (), start=1):
        item_lines = list(getattr(item, "lines", ()) or ())
        lines.append(f"item {index} lines={item_lines}")
        for label, value in (
            ("approach", getattr(item, "approach", "")),
            ("fixture_hint", getattr(item, "fixture_hint", ""))):
            text = str(value or "").strip()
            if text:
                lines.append(f"{label}: {text}")
    return lines

@dataclass(frozen=True)
class _SliceOutcome:
    gradle_ok: bool
    kover_improved: bool
    validation_issues: list[str]
    after_gap: Any = None
    after_gap_raw: Any = None
    kover_verdict: str = "unchanged"
    unavailable: bool = False
    empty_output: bool = False
    evidence: str = ""

@dataclass
class _CoderRound:
    any_green: bool = False
    any_improved: bool = False
    last_outcome: _SliceOutcome | None = None
    last_after_gap: Any = None
    last_after_gap_raw: Any = None
    agent_unavailable: bool = False
    empty_output_only: bool = True
    last_green_existed: bool = False
    last_green_contents: str = ""

async def _run_coder_slice(
    *,
    slice_tag: str,
    slice_plan,
    later_tags: list[str],
    source_file_path: str,
    test_path: str,
    project_root: str,
    source_code: str,
    before_gap,
    before_gap_raw,
    module_dir: str,
    static_path: str,
    memory: str,
    sibling_context: str,
    validation_anchor: str,
    source_categories: frozenset[str] | set[str],
    existed: bool,
    acceptance_tasks: list[str],
    baseline_output: str,
    gradle_offline: bool,
    kover_command: str,
    agent_timeout: int,
    config,
    batch_n: int = 0,
    batch_total: int = 0,
    test_layer: TestLayer = TestLayer.UNIT,
    skip_device_verify: bool = False,
) -> _SliceOutcome:
    agent = agent_env_display_name()
    line_set = testable_lines(slice_plan)
    coder_gap = restrict_gap_to_lines(before_gap, line_set)
    coder_gap_raw = restrict_gap_to_lines(
        before_gap_raw if before_gap_raw is not None else before_gap,
        line_set)
    plan_text = format_plan_for_coder(slice_plan, slice_tag=slice_tag, later_tags=later_tags)
    if test_layer == TestLayer.INSTRUMENTED:
        plan_text = f"{test_layer_guidance()}\n\n{plan_text}"
    plan_path = str(getattr(slice_plan, "markdown_path", "") or "")
    if test_layer == TestLayer.INSTRUMENTED:
        if skip_device_verify:
            fast_invocation = resolve_instrumented_compile_invocation(
                project_root=project_root,
                test_file_path=test_path,
                cli_tasks=acceptance_tasks,
                gradle_output=baseline_output,
            )
            gradle_command = format_gradle_command(fast_invocation)
        else:
            fast_invocation = resolve_instrumented_verify_invocation(
                project_root=project_root,
                test_file_path=test_path,
                cli_tasks=acceptance_tasks,
                gradle_output=baseline_output,
            )
            gradle_command = format_gradle_command(fast_invocation)
    else:
        fast_invocation = resolve_fast_verify_invocation(
            project_root=project_root,
            test_file_path=test_path,
            cli_tasks=acceptance_tasks,
            gradle_output=baseline_output,
        )
        gradle_command = format_gradle_command(fast_invocation)
    doc_categories = sorted(set(source_categories) | {slice_tag})
    if test_layer != TestLayer.INSTRUMENTED and slice_tag.startswith("android"):
        doc_categories.append("robolectric")
    api_doc_text = format_api_doc_catalog(
        source_code if slice_tag == PLAIN_UNIT_TAG else "",
        categories=doc_categories,
        test_layer=test_layer.value,
    )
    test_state = read_test_file_state(test_path)
    methods = sorted(
        {
            str(m)
            for item in slice_plan.testable
            for m in (getattr(item, "methods", ()) or ())
            if str(m).strip()
        }
    )
    try:
        current_target = file_cache.read_text(test_path)
    except file_cache.FileCacheError:
        current_target = validation_anchor
    pre_agent_snapshot = current_target
    slice_source = format_slice_source_projection(
        source_file_path, source_code, method_names=set(methods), delta_lines=line_set)
    target_excerpt = format_target_excerpt(current_target, test_path)
    raw = getattr(slice_plan, "raw", None)
    raw_map = raw if isinstance(raw, dict) else {}
    pinned_recipe = str(raw_map.get("recipe_id") or "")
    pinned_lane = str(raw_map.get("recipe_lane") or "")
    prompt_parts = build_agent_prompt(
        source_path=source_file_path, test_path=test_path, gradle_command=gradle_command,
        kover_command=kover_command,
        kover_text=format_kover_for_prompt(
            source_file_path, coder_gap, only_lines=line_set or None,
            deferred_note=(
                "Deferred/unreachable lines were removed from this prompt; do not retry them."
                if slice_plan.not_testable else ""
            ),
            include_windows=False),
        static_analysis_path=static_path, memory_context=memory, plan_text=plan_text,
        plan_path=plan_path, api_doc_text=api_doc_text, test_file_state=test_state,
        sibling_test_context=sibling_context, slice_tag=slice_tag, source_code=source_code,
        existing_test_code=validation_anchor,
        slice_source=slice_source, target_excerpt=target_excerpt, method_names=methods,
        delta_lines=line_set,
        source_categories=source_categories,
        pinned_recipe_id=str(pinned_recipe),
        recipe_lane=str(pinned_lane),
        test_layer=test_layer,
    )
    endpoint, model = describe_coder_endpoint()
    available, denied = coder_tool_surface()
    log_coder_slice_preamble(
        slice_tag=slice_tag, lines_text=compact_ranges(sorted(line_set)),
        methods_text=", ".join(methods), plan_lines=_slice_plan_preamble_lines(slice_plan),
         gradle_command=gradle_command,
        batch_n=batch_n, batch_total=batch_total, endpoint=endpoint, model=model,
        available=available, denied=denied)
    try:
        owning_module = module_dir or find_owning_module_dir(project_root, source_file_path)
        gen_result = run_claude(
            prompt_parts.user, cwd=project_root, system_prompt=prompt_parts.system,
            title=f"GENERATION AGENT PROMPT (slice={slice_tag})",
            temperature=config.gen_temperature, presence_penalty=config.gen_presence_penalty,
            available_tools=CODER_AVAILABLE_TOOLS, allowed_tools=CODER_ALLOWED_TOOLS,
            denied_tools=CODER_DENIED_TOOLS, timeout=agent_timeout,
            enable_thinking=config.gen_enable_thinking,
            reasoning_budget=config.gen_thinking_budget if config.gen_enable_thinking else 0,
            phase="gen",
            owning_module_dir=owning_module,
        )
        _log_edit_no_match(getattr(gen_result, "stdout", "") or "", phase="gen", slice_tag=slice_tag)
    except (AgentUnavailableError, OSError) as exc:
        return _SliceOutcome(False, False, [], unavailable=True, evidence=str(exc))

    if not Path(test_path).is_file() or not file_cache.read_text(test_path, default="").strip():
        if restore_if_junit_tests_wiped(test_path, before=pre_agent_snapshot, label="gen empty_output"):
            return _SliceOutcome(
                False, False, [], empty_output=True,
                evidence=f"{agent} wiped TARGET @Test content; snapshot restored.")
        return _SliceOutcome(
            False, False, [], empty_output=True,
            evidence=f"{agent} finished without creating the expected test file.")

    if restore_if_junit_tests_wiped(test_path, before=pre_agent_snapshot, label="gen"):
        return _SliceOutcome(
            False, False, [], empty_output=True,
            evidence=f"{agent} wiped TARGET @Test content; snapshot restored.")

    new_issues: list[str] = []
    gradle_ok = False
    full_gradle_output = ""
    for attempt in range(config.fix_max_attempts + 1):
        generated = file_cache.read_text(test_path, default="")
        new_issues = []
        if config.enable_guardrails:
            if validation_anchor.strip():
                new_issues = validation_issues_added(
                    validation_anchor, generated, test_path, source_code=source_code)
            else:
                new_issues = validate_generated_test_code(
                    generated, test_path, source_code=source_code, existing_test_code=validation_anchor)
            if new_issues:
                log_message("⚠️ Post-agent static validation issues (new):", category="warning")
                for issue in new_issues:
                    log_message(f"   - {issue}", category="warning")

        if test_layer == TestLayer.INSTRUMENTED:
            if skip_device_verify:
                fast_invocation = resolve_instrumented_compile_invocation(
                    project_root=project_root,
                    test_file_path=test_path,
                    cli_tasks=acceptance_tasks,
                    gradle_output=baseline_output,
                )
            else:
                fast_invocation = resolve_instrumented_verify_invocation(
                    project_root=project_root,
                    test_file_path=test_path,
                    cli_tasks=acceptance_tasks,
                    gradle_output=baseline_output,
                )
        else:
            fast_invocation = resolve_fast_verify_invocation(
                project_root=project_root,
                test_file_path=test_path,
                cli_tasks=acceptance_tasks,
                gradle_output=baseline_output,
            )
        gradle_command = format_gradle_command(fast_invocation)
        verify_label = (
            "FAST INSTRUMENTED COMPILE VERIFY"
            if test_layer == TestLayer.INSTRUMENTED and skip_device_verify
            else "FAST INSTRUMENTED VERIFY"
            if test_layer == TestLayer.INSTRUMENTED
            else "FAST UNIT-TEST VERIFY"
        )
        final_result = await run_gradle_with_heartbeat(
            project_root,
            tasks=list(fast_invocation.tasks),
            gradle_args=list(fast_invocation.gradle_args),
            offline=gradle_offline,
        )
        log_gradle_result(f"{verify_label} (slice={slice_tag})", final_result)
        full_gradle_output = final_result.full_output or ""
        gradle_ok = is_gradle_success(final_result)
        if gradle_ok and not new_issues:
            break
        if attempt >= config.fix_max_attempts:
            break
        log_message(
            f"🔧 {agent} fix pass {attempt + 1}/{config.fix_max_attempts} "
            f"(slice={slice_tag}, temp={config.fix_temperature})",
            category="info")
        from UnitTest_gen.kotlin.codegen import build_repair_tickets

        tickets = build_repair_tickets(
            validation_issues=new_issues,
            gradle_output=full_gradle_output if not gradle_ok else "",
        )
        fix_parts = build_fix_prompt(
            source_path=source_file_path, test_path=test_path, gradle_command=gradle_command,
            kover_command=kover_command, gradle_output=full_gradle_output,
            validation_issues=new_issues, tickets=tickets, sibling_test_context=sibling_context,
            slice_tag=slice_tag, project_root=project_root, api_doc_text=api_doc_text,
            source_code=source_code, existing_test_code=validation_anchor,
            slice_source=slice_source,
            target_excerpt=format_target_excerpt(
                file_cache.read_text(test_path, default=current_target)
                if Path(test_path).is_file() else current_target,
                test_path),
            method_names=methods, delta_lines=line_set, plan_path=plan_path,
            pinned_recipe_id=str(pinned_recipe),
            recipe_lane=str(pinned_lane),
            source_categories=source_categories,
            test_layer=test_layer)
        if "FIX RECIPE CONTEXT" in fix_parts.user:
            log_message(
                "📎 Fix pass includes compact recipe/harness context.",
                category="info",
            )
        try:
            pre_fix_content = file_cache.read_text(test_path, default="")
            fix_result = run_claude(
                fix_parts.user, cwd=project_root, title=f"FIX PASS {attempt + 1} (slice={slice_tag})",
                system_prompt=fix_parts.system,
                temperature=config.fix_temperature, presence_penalty=config.fix_presence_penalty,
                available_tools=FIX_AVAILABLE_TOOLS, allowed_tools=FIX_ALLOWED_TOOLS,
                denied_tools=FIX_DENIED_TOOLS, timeout=agent_timeout,
                enable_thinking=config.fix_enable_thinking,
                reasoning_budget=config.fix_thinking_budget if config.fix_enable_thinking else 0,
                phase="fix",
                owning_module_dir=owning_module,
            )
            _log_edit_no_match(
                getattr(fix_result, "stdout", "") or "",
                phase=f"fix{attempt + 1}",
                slice_tag=slice_tag)
            restore_if_junit_tests_wiped(
                test_path, before=pre_agent_snapshot, label=f"fix{attempt + 1}")
            post_fix_content = file_cache.read_text(test_path, default="")
            if post_fix_content.strip() == pre_fix_content.strip():
                log_message(
                    "ℹ️ Fix pass left TARGET unchanged — post-validation will re-check file as-is.",
                    category="info",
                )
        except (AgentUnavailableError, OSError) as exc:
            log_message(f"⚠️ Fix pass unavailable: {exc}", category="warning")
            break

    if new_issues or not gradle_ok:
        evidence = (
            "New static validation issues remain." if new_issues and gradle_ok
            else "Generated test failed Gradle/JUnit verification." if not gradle_ok
            else "Verification failed."
        )
        return _SliceOutcome(False, False, new_issues, evidence=evidence)

    if test_layer == TestLayer.INSTRUMENTED and skip_device_verify:
        log_message(
            "⚠️ Instrumented compile verify passed (no adb). Skipping connected androidTest + JaCoCo.",
            category="warning",
        )
        return _SliceOutcome(
            True,
            False,
            [],
            evidence="instrumented compile verified; device verify skipped (no adb)",
        )

    if test_layer == TestLayer.INSTRUMENTED:
        gate_tasks = resolve_instrumented_gate_tasks(
            project_root=project_root,
            test_file_path=test_path,
            cli_tasks=acceptance_tasks,
        )
        gate_label = "JACOCO GATE"
        acceptance_enabled = config.jacoco_acceptance_enabled
    else:
        gate_tasks = list(acceptance_tasks)
        gate_label = "KOVER GATE"
        acceptance_enabled = config.kover_acceptance_enabled

    if not acceptance_enabled:
        return _SliceOutcome(True, True, [], kover_verdict="skipped")

    gate_result = await run_gradle_with_heartbeat(
        project_root, tasks=gate_tasks, offline=gradle_offline)
    log_gradle_result(f"{gate_label} (slice={slice_tag})", gate_result)
    if not is_gradle_success(gate_result):
        return _SliceOutcome(
            True, False, [], evidence=f"{gate_label} failed after green FAST_VERIFY.")

    if test_layer == TestLayer.INSTRUMENTED or run_instrumented_tests(config):
        from UnitTest_gen.kotlin.coverage import parse_latest_unified_coverage_gap

        include_instr = test_layer == TestLayer.INSTRUMENTED or run_instrumented_tests(config)
        _, _, after_gap_raw = await parse_latest_unified_coverage_gap(
            module_dir,
            source_file_path,
            source_code,
            project_root=project_root,
            gradle_tasks=acceptance_tasks,
            include_instrumented=include_instr,
        )
    else:
        after_gap_raw = await parse_latest_coverage_gap(
            module_dir, source_file_path, source_code,
            project_root=project_root, gradle_tasks=acceptance_tasks)
    after_gap = filter_gap(after_gap_raw, source_file_path)
    after_gap_scoped = restrict_gap_to_lines(after_gap, line_set)
    after_gap_raw_scoped = restrict_gap_to_lines(after_gap_raw, line_set)
    improved = coverage_improved(coder_gap, after_gap_scoped, allow_bootstrap=coder_gap is None)
    delta = compute_coverage_delta(coder_gap, after_gap_scoped)
    verdict = "regressed" if delta is not None and delta.has_regression else "improved" if improved else "unchanged"
    log_block(
        f"{'JACOCO' if test_layer == TestLayer.INSTRUMENTED else 'KOVER'} ACCEPTANCE DELTA (slice={slice_tag})",
        coverage_gap_delta_summary(coder_gap, after_gap_scoped, before_raw=coder_gap_raw, after_raw=after_gap_raw_scoped),
        category="gradle", console=True)
    return _SliceOutcome(
        True, improved, [], kover_verdict=verdict, after_gap=after_gap, after_gap_raw=after_gap_raw)

async def _run_coder_slices(
    *,
    plan,
    source_file_path: str,
    test_path: str,
    project_root: str,
    source_code: str,
    before_gap,
    before_gap_raw,
    module_dir: str,
    static_path: str,
    memory: str,
    sibling_context: str,
    validation_anchor: str,
    source_categories,
    acceptance_tasks: list[str],
    baseline_output: str,
    gradle_offline: bool,
    kover_command: str,
    agent_timeout: int,
    config,
    agent: str,
    last_green_existed: bool,
    last_green_contents: str,
    any_green: bool,
    any_improved: bool,
    empty_output_only: bool,
    test_layer: TestLayer = TestLayer.UNIT,
    skip_device_verify: bool = False,
) -> _CoderRound:
    slices = [
        (tag, slice_plan)
        for tag, slice_plan in chunk_plan_by_method(plan, source_code=source_code)
        if slice_plan.testable
    ]
    if not config.enable_guardrails:
        log_message(
            "Post-validation disabled (TESTGEN_ENABLE_GUARDRAILS=0); FAST_VERIFY only.",
            category="info",
        )
    last_outcome: _SliceOutcome | None = None
    last_after_gap = last_after_gap_raw = None
    agent_unavailable = False
    tag_batch_totals: dict[str, int] = {}
    for tag, _ in slices:
        tag_batch_totals[tag] = tag_batch_totals.get(tag, 0) + 1
    tag_batch_index: dict[str, int] = {}
    from UnitTest_gen.kotlin.pipeline import (
        refresh_last_green_snapshot as _refresh_last_green_snapshot,
        seed_test_skeleton as _seed_test_skeleton,
        snapshot_test as _snapshot_test,
        write_test_snapshot as _write_test_snapshot,
    )

    for index, (tag, slice_plan) in enumerate(slices):
        tag_batch_index[tag] = tag_batch_index.get(tag, 0) + 1
        batch_n, batch_total = tag_batch_index[tag], tag_batch_totals[tag]
        later_labels = [f"{tag}#{later_i + 1}" for later_i in range(batch_n, batch_total)]
        seen_later_tags: set[str] = set()
        for later_tag, later_plan in slices[index + 1 :]:
            if later_tag == tag or not later_plan.testable or later_tag in seen_later_tags:
                continue
            seen_later_tags.add(later_tag)
            later_labels.append(later_tag)

        if not Path(test_path).is_file():
            _seed_test_skeleton(test_path, state=read_test_file_state(test_path))
            last_green_existed, last_green_contents = _refresh_last_green_snapshot(test_path)

        outcome = await _run_coder_slice(
            slice_tag=tag, slice_plan=slice_plan, later_tags=later_labels,
            source_file_path=source_file_path, test_path=test_path, project_root=project_root,
            source_code=source_code, before_gap=before_gap, before_gap_raw=before_gap_raw,
            module_dir=module_dir, static_path=static_path, memory=memory,
            sibling_context=sibling_context,
            validation_anchor=last_green_contents if last_green_contents.strip() else validation_anchor,
            source_categories=source_categories, existed=last_green_existed,
            acceptance_tasks=acceptance_tasks, baseline_output=baseline_output,
            gradle_offline=gradle_offline, kover_command=kover_command,
            agent_timeout=agent_timeout, config=config, batch_n=batch_n, batch_total=batch_total,
            test_layer=test_layer, skip_device_verify=skip_device_verify)
        last_outcome = outcome

        if outcome.unavailable:
            agent_unavailable = True
            log_message(f"⚠️ Slice tag={tag} {agent} unavailable; restoring last green snapshot.", category="warning")
            _write_test_snapshot(test_path, last_green_existed, last_green_contents)
            break

        if outcome.empty_output:
            log_message(f"⚠️ Slice tag={tag} produced an empty test file; restoring last green snapshot.", category="warning")
            _write_test_snapshot(test_path, last_green_existed, last_green_contents)
            continue

        empty_output_only = False
        if outcome.gradle_ok:
            snap_existed, snap_contents = _snapshot_test(test_path)
            if snap_existed and snap_contents.strip():
                last_green_existed, last_green_contents = True, snap_contents
                any_green = True
                if outcome.after_gap is not None:
                    last_after_gap = outcome.after_gap
                if outcome.after_gap_raw is not None:
                    last_after_gap_raw = outcome.after_gap_raw
                if outcome.kover_improved:
                    any_improved = True
                verdict = str(getattr(outcome, "kover_verdict", "") or "")
                verdict_text = {"improved": "Kover improved", "regressed": "Kover regressed"}.get(verdict, "Kover unchanged")
                log_message(
                    f"✅ Slice tag={tag} FAST_VERIFY + validation green; {verdict_text}; snapshot kept.",
                    category="success" if verdict == "improved" else "error")
                continue
            log_message(f"⚠️ Slice tag={tag} Gradle reported success but the test file is empty.", category="warning")

        reason = outcome.evidence or "Gradle/validation failed"
        log_message(f"↩️ Slice tag={tag} failed ({reason}); restored last green snapshot; continuing.", category="warning")
        _write_test_snapshot(test_path, last_green_existed, last_green_contents)

    return _CoderRound(
        any_green=any_green, any_improved=any_improved, last_outcome=last_outcome,
        last_after_gap=last_after_gap, last_after_gap_raw=last_after_gap_raw,
        agent_unavailable=agent_unavailable, empty_output_only=empty_output_only,
        last_green_existed=last_green_existed, last_green_contents=last_green_contents)

import os

from UnitTest_gen.core.io import adb_device_available
from UnitTest_gen.core.io import log_message
from UnitTest_gen.core.config import get_config, run_instrumented_tests
from UnitTest_gen.kotlin.project import (
    GradleInvocation,
    format_gradle_command,
    resolve_instrumented_gate_tasks,
)
from UnitTest_gen.kotlin.project import derive_instrumented_test_output_path
from UnitTest_gen.kotlin.layer import TestLayer

async def run_instrumented_pass(
    *,
    plan,
    source_file_path: str,
    source_root: str,
    project_root: str,
    source_code: str,
    before_gap,
    before_gap_raw,
    module_dir: str,
    static_path: str,
    memory: str,
    sibling_context: str,
    source_categories,
    gradle_tasks: list[str],
    baseline_output: str,
    gradle_offline: bool,
    agent: str,
    output_base_directory: str | None = None,
) -> _CoderRound | None:
    """Generate androidTests when test_mode allows; verify only when adb device present."""
    config = get_config()
    if not run_instrumented_tests(config):
        return None

    instr_path = derive_instrumented_test_output_path(
        source_file_path=source_file_path,
        source_root=source_root,
        output_base_directory=output_base_directory,
    )
    device_ready = adb_device_available()
    skip_verify = not device_ready

    if skip_verify:
        log_message(
            "⚠️ Hybrid instrumented mode: no adb device — will generate androidTest and skip connected verify.",
            category="warning",
        )
    else:
        log_message(
            f"📱 Instrumented pass: adb device detected — verify via connectedAndroidTest + JaCoCo.",
            category="info",
        )

    log_message(f"🧪 Instrumented output: {instr_path}", category="info")
    if not plan.has_testable:
        log_message("ℹ️ Instrumented pass: no instrumented-tagged plan items; skipping.", category="info")
        return None

    gate_tasks = resolve_instrumented_gate_tasks(
        project_root=project_root,
        test_file_path=instr_path,
        cli_tasks=gradle_tasks,
    )
    jacoco_command = format_gradle_command(GradleInvocation(tasks=gate_tasks, gradle_args=[]))
    log_message(f"🧪 JaCoCo gate tasks: {jacoco_command}", category="gradle")

    from UnitTest_gen.kotlin.codegen import read_test_file_state
    from UnitTest_gen.kotlin.bootstrap import ensure_instrumented_module_ready
    from UnitTest_gen.kotlin.pipeline import refresh_last_green_snapshot, seed_test_skeleton

    ensure_instrumented_module_ready(
        module_dir=module_dir,
        source_file_path=source_file_path,
        source_root=source_root,
        source_code=source_code,
        categories=source_categories,
        output_base_directory=output_base_directory,
    )

    test_state = read_test_file_state(instr_path)
    existed = test_state.exists
    original = ""
    if existed:
        from UnitTest_gen.core import io as file_cache

        original = file_cache.read_text(instr_path, default="")

    seed_test_skeleton(
        instr_path,
        state=test_state,
        test_layer=TestLayer.INSTRUMENTED,
        categories=source_categories,
        source_code=source_code,
    )
    last_green_existed, last_green_contents = refresh_last_green_snapshot(instr_path)

    return await _run_coder_slices(
        plan=plan,
        source_file_path=source_file_path,
        test_path=instr_path,
        project_root=project_root,
        source_code=source_code,
        before_gap=before_gap,
        before_gap_raw=before_gap_raw,
        module_dir=module_dir,
        static_path=static_path,
        memory=memory,
        sibling_context=sibling_context,
        validation_anchor=original if original.strip() else "",
        source_categories=source_categories,
        acceptance_tasks=gradle_tasks,
        baseline_output=baseline_output,
        gradle_offline=gradle_offline,
        kover_command=jacoco_command,
        agent_timeout=max(config.agent_timeout_seconds, config.gradle_timeout_seconds),
        config=config,
        agent=agent,
        last_green_existed=last_green_existed,
        last_green_contents=last_green_contents,
        any_green=False,
        any_improved=False,
        empty_output_only=True,
        test_layer=TestLayer.INSTRUMENTED,
        skip_device_verify=skip_verify,
    )

import os
import time
from dataclasses import dataclass, replace
from pathlib import Path

from UnitTest_gen.core import io as file_cache
from UnitTest_gen.core.gradle import is_gradle_success, log_gradle_result, run_gradle_with_heartbeat
from UnitTest_gen.core.io import log_block, log_message, log_section
from UnitTest_gen.core.io import retrieve_generation_lessons
from UnitTest_gen.core.config import (
    agent_env_display_name,
    agent_env_log_tag,
    get_config,
    run_coder_phase,
    run_instrumented_tests,
    run_plan_phase,
    run_unit_tests,
)
from UnitTest_gen.core.io import compact_ranges
from UnitTest_gen.kotlin.plan import (
    clamp_plan_tags,
    clamp_plan_to_delta,
    plan_coverage_gaps,
    testable_lines,
    uncovered_planned_lines,
)
from UnitTest_gen.kotlin.codegen import read_test_file_state
from UnitTest_gen.kotlin.coverage import (
    build_coverage_summary,
    find_latest_kover_xml_for_context,
    parse_latest_coverage_gap,
)
from UnitTest_gen.kotlin.coverage import parse_latest_unified_coverage_gap
from UnitTest_gen.kotlin.project import GradleInvocation, cli_acceptance_tasks, format_gradle_command
from UnitTest_gen.kotlin.coverage import format_acceptance_summary
from UnitTest_gen.kotlin.analysis import coverage_slice_tags
from UnitTest_gen.kotlin.plan import load_plan_markdown, parse_plan_markdown, plan_path_for_source
from UnitTest_gen.kotlin.project import (
    derive_test_output_path,
    discover_sibling_test_files,
    find_owning_module_dir,
    find_source_root_for_path,
    format_sibling_test_paths,
    module_path_for_dir,
    resolve_module_robolectric_sdk,
)
from UnitTest_gen.kotlin.analysis import analyze_kotlin_code, persist_static_analysis_report
from UnitTest_gen.kotlin.coverage import (
    entries_from_attempted_no_delta,
    entries_from_plan_not_testable,
    filter_gap,
    gap_has_actionable_lines,
    merge_unreachable_entries,
)

_RETRYABLE_PLAN_CATEGORIES = {"sliced_for_complexity", "untagged_or_invalid_slice"}

@dataclass(frozen=True)
class AgentRunResult:
    accepted: bool
    outcome: str
    test_file_path: str
    gradle_passed: bool = False
    kover_improved: bool = False
    failure_stage: str = ""
    evidence: str = ""
    duration_seconds: float = 0.0

def _finish_run(
    result: AgentRunResult,
    project_root: str,
    source_file_path: str,
    gradle_tasks: list[str],
    module: str,
    *,
    before_gap=None,
    after_gap=None,
    validation_issues: list[str] | None = None,
    coverage_summary: dict | None = None,
) -> None:
    from UnitTest_gen.kotlin.pipeline import finish_run as finish_hook

    finish_hook(
        result, project_root, source_file_path, gradle_tasks, module,
        before_gap=before_gap, after_gap=after_gap,
        validation_issues=validation_issues, coverage_summary=coverage_summary,
    )

def _finish_early(
    started: float,
    *,
    accepted: bool,
    outcome: str,
    test_path: str,
    project_root: str,
    source_file_path: str,
    acceptance_tasks: list[str],
    module: str,
    gradle_passed: bool = False,
    kover_improved: bool = False,
    failure_stage: str = "",
    evidence: str = "",
    before_gap=None,
    after_gap=None,
    validation_issues: list[str] | None = None,
    coverage_summary: dict | None = None,
    log: str = "",
    log_category: str = "info",
) -> AgentRunResult:
    if log:
        log_message(log, category=log_category)
    stats = file_cache.stats()
    log_message(
        "📦 FileCache "
        f"hits={stats['hits']} misses={stats['misses']} reloads={stats['reloads']} "
        f"evictions={stats['evictions']} entries={stats['entries']} bytes={stats['bytes']}",
        category="info",
    )
    result = AgentRunResult(
        accepted=accepted,
        outcome=outcome,
        test_file_path=test_path,
        gradle_passed=gradle_passed,
        kover_improved=kover_improved,
        failure_stage=failure_stage,
        evidence=evidence,
        duration_seconds=time.monotonic() - started,
    )
    _finish_run(
        result, project_root, source_file_path, acceptance_tasks, module,
        before_gap=before_gap, after_gap=after_gap,
        validation_issues=validation_issues, coverage_summary=coverage_summary,
    )
    return result

def _defer_all(finish, evidence, before_gap, *, outcome="all_gaps_deferred", log=None, log_category="info"):
    return finish(
        accepted=True,
        outcome=outcome,
        gradle_passed=True,
        evidence=evidence,
        before_gap=before_gap,
        log=log if log is not None else f"⏭️ {evidence}",
        log_category=log_category,
    )

def _persist_plan_unreachable(plan, *, source_file_path: str, module: str) -> None:
    rows = entries_from_plan_not_testable(
        [
            item.as_dict()
            for item in plan.not_testable
            if str(item.category or "") not in _RETRYABLE_PLAN_CATEGORIES
        ],
        source_file_path=source_file_path,
        module=module,
    )
    if not rows:
        return
    path = merge_unreachable_entries(rows)
    log_message(f"🗂️ Persisted {len(rows)} unreachable gap(s) → {path}", category="info")

def _persist_kover_no_coverage(plan, *, source_file_path: str, module: str, before_gap, after_gap) -> None:
    rows = entries_from_attempted_no_delta(
        list(getattr(plan, "testable", ()) or ()),
        before_gap=before_gap, after_gap=after_gap,
        source_file_path=source_file_path, module=module,
    )
    if not rows:
        return
    path = merge_unreachable_entries(rows)
    log_message(f"🗂️ Persisted {len(rows)} Kover no-delta gap(s) → {path}", category="warning")

def _log_python_acceptance(*, plan, before_gap, after_gap, test_path: str) -> None:
    log_block(
        "PYTHON COVERAGE SUMMARY",
        format_acceptance_summary(plan=plan, before_gap=before_gap, after_gap=after_gap, test_path=test_path),
        category="success", console=True,
    )

def _load_cached_plan(source_file_path: str, test_path: str, test_state, before_gap, source_categories, sibling_context, project_root):
    from UnitTest_gen.kotlin.plan import (
        demote_partial_branch_residuals,
        ensure_not_testable_details,
        exclusive_partition_plan_lines,
        _validate_and_repair_plan,
    )
    from UnitTest_gen.kotlin.recipes import selected_recipe_selection
    from UnitTest_gen.kotlin.plan import (
        delete_plan_markdown,
        drop_unwanted_plan_data,
        render_plan_markdown,
        save_plan_markdown,
        sync_frontmatter_after_clamp,
    )
    from UnitTest_gen.kotlin.plan import validate_plan_markdown
    from UnitTest_gen.core.config import get_config

    _ = test_state, sibling_context
    expected = plan_path_for_source(source_file_path)
    md_text = load_plan_markdown(source_file_path)
    if not md_text.strip() or not expected.is_file():
        return None, (
            f"Coder-only run requires an existing plan at {expected}. "
            "Run with --plan-env (or --env) first."
        )
    plan = parse_plan_markdown(md_text)
    if not plan.parsed or (not plan.testable and not plan.not_testable):
        return None, (
            f"Saved plan at {expected} is missing/unparseable frontmatter. "
            "Re-run planning with --plan-env."
        )
    plan = replace(plan, markdown_path=str(expected.resolve()), markdown_body=md_text)
    slice_tags = coverage_slice_tags(source_categories)
    plan = clamp_plan_tags(clamp_plan_to_delta(plan, before_gap), slice_tags=slice_tags)
    try:
        target_for_demote = file_cache.read_text(test_path)
    except file_cache.FileCacheError:
        target_for_demote = ""
    pre_demote_testable = len(plan.testable)
    plan = ensure_not_testable_details(
        exclusive_partition_plan_lines(
            demote_partial_branch_residuals(plan, before_gap, target_code=target_for_demote),
        )
    )
    if len(plan.testable) != pre_demote_testable or not plan.has_testable:
        if not plan.has_testable:
            removed = delete_plan_markdown(source_file_path)
            plan = replace(plan, markdown_path="", markdown_body="")
            log_message(
                "🧭 Cached plan all not_testable after residual demote — skipping plan.md"
                + (" (removed stale plan)" if removed else "")
                + f"; not_testable={len(plan.not_testable)}.",
                category="info",
            )
        else:
            body = drop_unwanted_plan_data(render_plan_markdown(
                plan, source_path=source_file_path, test_path=test_path,
                reason=plan.fallback_reason or "partial-branch residual demote",
            ))
            saved = save_plan_markdown(source_file_path, body)
            plan = replace(plan, markdown_path=str(saved), markdown_body=body)
            synced = sync_frontmatter_after_clamp(plan, source_file_path)
            if synced is not None:
                body = file_cache.read_text(synced, default=body)
                plan = replace(plan, markdown_path=str(synced), markdown_body=body)
            log_message(f"🧭 Saved plan refreshed after residual demote: {plan.markdown_path}", category="info")

    if plan.has_testable:
        source_code = file_cache.read_text(source_file_path, default="")
        recipe_lane, recipe_id = selected_recipe_selection(
            source_code, categories=list(source_categories),
        )
        raw = getattr(plan, "raw", None)
        raw_map = raw if isinstance(raw, dict) else {}
        pinned = str(raw_map.get("recipe_id") or "").strip()
        if pinned and pinned != "none":
            recipe_id = pinned
            recipe_lane = str(raw_map.get("recipe_lane") or recipe_lane)
        tickets = validate_plan_markdown(
            plan, source_code, before_gap, recipe_id=recipe_id, recipe_lane=recipe_lane,
        )
        if tickets:
            config = get_config()
            plan = _validate_and_repair_plan(
                plan,
                source_path=source_file_path,
                source_code=source_code,
                gap=before_gap,
                project_root=project_root or "",
                recipe_id=recipe_id,
                recipe_lane=recipe_lane,
                existing_for_lane=target_for_demote,
                slice_tags=slice_tags,
                existing_test_path=test_path,
                pipeline_test_path=test_path,
                config=config,
            )

    log_message(
        f"🧭 Loaded saved plan: {expected} (testable={len(plan.testable)} not_testable={len(plan.not_testable)})",
        category="info",
    )
    return plan, None

async def run_agent_for_source(
    *,
    source_file_path: str,
    project_root: str,
    class_name: str,
    source_code: str,
    output_base_directory: str | None,
    gradle_tasks: list[str],
    gradle_offline: bool = False,
) -> AgentRunResult:
    """Baseline Kover → plan → generate → FAST_VERIFY/validation → KOVER_GATE → Python verdict."""
    from UnitTest_gen.core.hooks import (
        READ_ONCE_ENV,
        write_read_once_sidecar,
    )
    from UnitTest_gen.kotlin.plan import archive_plan_markdown

    source_file_path = os.path.abspath(source_file_path)
    read_once = write_read_once_sidecar()
    prior_read_once = os.environ.get(READ_ONCE_ENV)
    os.environ[READ_ONCE_ENV] = str(read_once)
    try:
        return await _run_agent_for_source_body(
            source_file_path=source_file_path,
            project_root=project_root,
            class_name=class_name,
            source_code=source_code,
            output_base_directory=output_base_directory,
            gradle_tasks=gradle_tasks,
            gradle_offline=gradle_offline,
        )
    finally:
        # Keep plan.md in place for plan-only → coder-only handoff.
        if run_coder_phase(get_config()):
            archive_plan_markdown(source_file_path)
        try:
            Path(read_once).unlink(missing_ok=True)
        except OSError:
            pass
        if prior_read_once is None:
            os.environ.pop(READ_ONCE_ENV, None)
        else:
            os.environ[READ_ONCE_ENV] = prior_read_once

async def _run_agent_for_source_body(
    *,
    source_file_path: str,
    project_root: str,
    class_name: str,
    source_code: str,
    output_base_directory: str | None,
    gradle_tasks: list[str],
    gradle_offline: bool = False,
) -> AgentRunResult:
    """Baseline Kover → plan → generate → FAST_VERIFY/validation → KOVER_GATE → Python verdict."""
    started = time.monotonic()
    config = get_config()
    agent = agent_env_display_name(config.agent_env)
    agent_tag = agent_env_log_tag(config.agent_env)
    project_root = os.path.abspath(project_root)
    source_root = find_source_root_for_path(source_file_path)
    test_path = derive_test_output_path(
        source_file_path=source_file_path, source_root=source_root,
        output_base_directory=output_base_directory,
    )
    sibling_paths = discover_sibling_test_files(source_file_path, test_path)
    sibling_context = format_sibling_test_paths(sibling_paths)
    sibling_anchor = ""
    for sibling in sibling_paths:
        sibling_anchor = file_cache.read_text(sibling, default="")
        if sibling_anchor.strip():
            break
    module_dir = find_owning_module_dir(project_root, source_file_path)
    module = module_path_for_dir(project_root, module_dir)
    report = analyze_kotlin_code(source_code, source_file_path, persist=True)
    static_path = persist_static_analysis_report(report) if report else ""
    from UnitTest_gen.kotlin.analysis import classify_from_report

    source_profile = classify_from_report(report, source_file_path=source_file_path, source_code=source_code)
    source_categories = source_profile.categories
    acceptance_tasks = cli_acceptance_tasks(gradle_tasks)

    log_section(f"{agent_tag} AGENT START: {source_file_path}", category="gen")
    log_message(
        f"🧭 Pipeline phase: {config.pipeline_phase} "
        f"(plan={run_plan_phase(config)}, coder={run_coder_phase(config)}, "
        f"test_mode={config.test_mode})",
        category="info",
    )
    log_message(f"🧪 Output test file: {test_path}", category="info")
    if sibling_paths:
        log_message(f"📎 Sibling test file(s): {', '.join(sibling_paths)}", category="info")
    log_message(f"🧪 CLI Kover gate tasks: {' '.join(acceptance_tasks)}", category="gradle")

    test_state = read_test_file_state(test_path)
    existed, original = test_state.exists, (
        file_cache.read_text(test_path, default="") if test_state.exists else ""
    )
    validation_anchor = original if original.strip() else sibling_anchor
    finish = lambda **kw: _finish_early(started, test_path=test_path, project_root=project_root,
                                         source_file_path=source_file_path, acceptance_tasks=acceptance_tasks,
                                         module=module, **kw)

    baseline_result = await run_gradle_with_heartbeat(project_root, tasks=acceptance_tasks, offline=gradle_offline)
    log_gradle_result("BASELINE GRADLE/KOVER", baseline_result)
    baseline_output = baseline_result.full_output
    if not is_gradle_success(baseline_result):
        return finish(
            accepted=False, outcome="pipeline_unresolved", failure_stage="baseline_gradle",
            evidence=f"Baseline Gradle/Kover failed before {agent} generation.",
        )

    if config.kover_acceptance_enabled and not find_latest_kover_xml_for_context(
        project_root, acceptance_tasks, module_dir,
    ):
        return finish(
            accepted=False, outcome="pipeline_unresolved", gradle_passed=True,
            failure_stage="baseline_kover_xml_missing", evidence="No Kover XML report found before generation.",
        )

    if run_instrumented_tests(config):
        merged, unit_part, instr_part = await parse_latest_unified_coverage_gap(
            module_dir,
            source_file_path,
            source_code,
            project_root=project_root,
            gradle_tasks=acceptance_tasks,
            include_instrumented=True,
        )
        before_gap_raw = merged or unit_part or instr_part
        if instr_part:
            log_message("📊 Unified baseline: Kover + JaCoCo merged gap.", category="info")
    else:
        before_gap_raw = await parse_latest_coverage_gap(
            module_dir, source_file_path, source_code, project_root=project_root, gradle_tasks=acceptance_tasks,
        )
    raw_had_actionable = gap_has_actionable_lines(before_gap_raw)
    before_gap = filter_gap(before_gap_raw, source_file_path)
    if not gap_has_actionable_lines(before_gap):
        evidence = (
            "All remaining Kover gaps are already deferred as unreachable."
            if raw_had_actionable
            else "Baseline Kover has no actionable uncovered lines for this source."
        )
        return _defer_all(
            finish, evidence, before_gap,
            outcome="all_gaps_deferred" if raw_had_actionable else "no_open_gaps",
        )

    sdk_resolution = resolve_module_robolectric_sdk(module_dir)
    if not sdk_resolution.ok:
        reason = sdk_resolution.reason or "Android SDK platform unavailable for Robolectric."
        return finish(
            accepted=False,
            outcome="pipeline_unresolved",
            gradle_passed=True,
            failure_stage="android_sdk_unavailable",
            evidence=reason,
            before_gap=before_gap,
            log=f"❌ {reason}",
            log_category="error",
        )

    do_plan, do_coder = run_plan_phase(config), run_coder_phase(config)
    if do_coder and not do_plan:
        from UnitTest_gen.kotlin.plan import build_kover_fallback_plan

        plan, err = _load_cached_plan(
            source_file_path, test_path, test_state, before_gap, source_categories, sibling_context, project_root,
        )
        if err:
            plan = build_kover_fallback_plan(
                gap=before_gap,
                source_path=source_file_path,
                source_code=source_code,
                existing_test_path=test_path if test_state.has_content else "",
                source_categories=source_categories,
                test_path=test_path,
            )
            if plan is None:
                return _defer_all(finish, "Kover delta empty for coder-only fallback.", before_gap)
            if not plan.has_testable:
                return _defer_all(
                    finish,
                    "Kover fallback plan has no testable gaps after residual demote.",
                    before_gap,
                )
            log_message(
                "🧭 No saved plan — using Kover-derived coder blueprint (coder-only).",
                category="info",
            )
        _persist_plan_unreachable(plan, source_file_path=source_file_path, module=module)
    else:
        plan = plan_coverage_gaps(
            source_path=source_file_path, gap=before_gap, project_root=project_root,
            existing_test_path=test_path if test_state.has_content else "",
            sibling_test_context=sibling_context, pipeline_test_path=test_path,
            source_code=source_code,
        )
        _persist_plan_unreachable(plan, source_file_path=source_file_path, module=module)

    if do_plan and not do_coder:
        plan_path = str(getattr(plan, "markdown_path", "") or "")
        if plan_path and Path(plan_path).is_file():
            evidence = f"Plan saved to {plan_path}" + (
                f" (testable={len(plan.testable)}, not_testable={len(plan.not_testable)})." if plan.parsed else "."
            )
            if not plan.has_testable:
                evidence += " No testable gaps; coder would be skipped."
            return finish(
                accepted=True, outcome="plan_saved", gradle_passed=True, evidence=evidence,
                before_gap=before_gap, log=f"✅ {evidence}", log_category="success",
            )
        if plan.not_testable and not plan.has_testable:
            evidence = (
                f"All delta gaps deferred as not_testable ({len(plan.not_testable)} item(s)); "
                "no plan.md written; unreachable store updated."
            )
            return _defer_all(finish, evidence, before_gap, log=f"✅ {evidence}", log_category="success")
        evidence = "Plan-only run finished without a saved Markdown plan."
        return _defer_all(finish, evidence, before_gap, log_category="warning")

    if not plan.has_testable:
        evidence = "Plan agent deferred all remaining gaps as not unit-testable."
        return _defer_all(finish, evidence, before_gap)

    from UnitTest_gen.kotlin.layer import TestLayer, assign_test_layers, filter_plan_by_layer

    if not any(item.test_layer for item in plan.testable):
        plan = assign_test_layers(
            plan, categories=source_categories, config=config, source_file_path=source_file_path,
        )
    unit_plan = filter_plan_by_layer(plan, TestLayer.UNIT)
    instr_plan = filter_plan_by_layer(plan, TestLayer.INSTRUMENTED)
    if not unit_plan.has_testable and not instr_plan.has_testable:
        evidence = "Plan has no testable items after layer assignment."
        return _defer_all(finish, evidence, before_gap)

    memory = retrieve_generation_lessons(source_categories, class_name=class_name) if config.enable_memory_lessons else ""
    kover_command = format_gradle_command(GradleInvocation(tasks=list(acceptance_tasks), gradle_args=[]))
    log_message(f"🧪 Kover gate: {kover_command}", category="gradle")
    agent_timeout = max(config.agent_timeout_seconds, config.gradle_timeout_seconds)

    initial_gap, initial_gap_raw = before_gap, before_gap_raw
    last_green_existed, last_green_contents = existed, original
    any_green = any_improved = False
    last_outcome: _SliceOutcome | None = None
    last_after_gap = last_after_gap_raw = None
    agent_unavailable = False
    empty_output_only = True

    if unit_plan.has_testable:
        seed_test_skeleton(
            test_path,
            state=test_state,
            test_layer=TestLayer.UNIT,
            categories=source_categories,
            source_code=source_code,
        )
        last_green_existed, last_green_contents = refresh_last_green_snapshot(test_path)

    from UnitTest_gen.kotlin.project import derive_instrumented_test_output_path

    instr_path = derive_instrumented_test_output_path(
        source_file_path=source_file_path,
        source_root=source_root,
        output_base_directory=output_base_directory,
    )
    if unit_plan.has_testable:
        log_message(
            f"🧪 Unit pass: {len(unit_plan.testable)} item(s) → {test_path}",
            category="info",
        )
    if instr_plan.has_testable:
        log_message(
            f"📱 Instrumented pass: {len(instr_plan.testable)} item(s) → {instr_path}",
            category="info",
        )

    allow_replan, rounds = bool(do_plan and do_coder and run_unit_tests(config) and unit_plan.has_testable), 0
    while run_unit_tests(config) and do_coder and unit_plan.has_testable:
        round_out: _CoderRound = await _run_coder_slices(
            plan=unit_plan, source_file_path=source_file_path, test_path=test_path,
            project_root=project_root, source_code=source_code, before_gap=before_gap,
            before_gap_raw=before_gap_raw, module_dir=module_dir, static_path=static_path,
            memory=memory, sibling_context=sibling_context, validation_anchor=validation_anchor,
            source_categories=source_categories, acceptance_tasks=acceptance_tasks,
            baseline_output=baseline_output, gradle_offline=gradle_offline,
            kover_command=kover_command, agent_timeout=agent_timeout, config=config, agent=agent,
            last_green_existed=last_green_existed, last_green_contents=last_green_contents,
            any_green=any_green, any_improved=any_improved, empty_output_only=empty_output_only,
        )
        any_green, any_improved = round_out.any_green, round_out.any_improved
        last_outcome, last_after_gap, last_after_gap_raw = round_out.last_outcome, round_out.last_after_gap, round_out.last_after_gap_raw
        agent_unavailable, empty_output_only = round_out.agent_unavailable, round_out.empty_output_only
        last_green_existed, last_green_contents = round_out.last_green_existed, round_out.last_green_contents

        if not allow_replan or agent_unavailable or last_after_gap is None:
            break
        uncovered = uncovered_planned_lines(unit_plan, last_after_gap)
        planned = testable_lines(unit_plan)
        log_message(
            "🧭 REVIEW planned lines: "
            f"covered={compact_ranges(sorted(planned - uncovered)) or 'none'} "
            f"remaining={compact_ranges(sorted(uncovered)) or 'none'}",
            category="info" if not uncovered else "warning",
        )
        if not uncovered or rounds >= config.review_replan_max:
            break
        rounds += 1
        log_message(
            f"🧭 REVIEW replan {rounds}/{config.review_replan_max} from remaining {compact_ranges(sorted(uncovered))}",
            category="info",
        )
        before_gap_raw = last_after_gap_raw
        before_gap = filter_gap(last_after_gap_raw, source_file_path)
        test_state = read_test_file_state(test_path)
        plan = plan_coverage_gaps(
            source_path=source_file_path, gap=before_gap, project_root=project_root,
            existing_test_path=test_path if test_state.has_content else "",
            sibling_test_context=sibling_context, pipeline_test_path=test_path,
            source_code=source_code,
        )
        plan = assign_test_layers(
            plan, categories=source_categories, config=config, source_file_path=source_file_path,
        )
        unit_plan = filter_plan_by_layer(plan, TestLayer.UNIT)
        instr_plan = filter_plan_by_layer(plan, TestLayer.INSTRUMENTED)
        _persist_plan_unreachable(plan, source_file_path=source_file_path, module=module)
        if not unit_plan.has_testable:
            log_message("🧭 REVIEW replan deferred remaining gaps as not_testable; skip extra coder.", category="info")
            break

    instr_round: _CoderRound | None = None
    if do_coder and run_instrumented_tests(config) and instr_plan.has_testable:
        instr_before_gap = last_after_gap if last_after_gap is not None else before_gap
        instr_before_gap_raw = last_after_gap_raw if last_after_gap_raw is not None else before_gap_raw
        instr_round = await run_instrumented_pass(
            plan=instr_plan,
            source_file_path=source_file_path,
            source_root=source_root,
            project_root=project_root,
            source_code=source_code,
            before_gap=instr_before_gap,
            before_gap_raw=instr_before_gap_raw,
            module_dir=module_dir,
            static_path=static_path,
            memory=memory,
            sibling_context=sibling_context,
            source_categories=source_categories,
            gradle_tasks=gradle_tasks,
            baseline_output=baseline_output,
            gradle_offline=gradle_offline,
            agent=agent,
            output_base_directory=output_base_directory,
        )
        if instr_round is not None:
            if instr_round.any_green:
                any_green = any_green or instr_round.any_green
            if instr_round.any_improved:
                any_improved = any_improved or instr_round.any_improved
            if not instr_round.empty_output_only:
                empty_output_only = False
            if last_outcome is None:
                last_outcome = instr_round.last_outcome
            if instr_round.last_after_gap is not None:
                last_after_gap = instr_round.last_after_gap
                last_after_gap_raw = instr_round.last_after_gap_raw
            if instr_round.last_green_existed or instr_round.last_green_contents:
                last_green_existed = instr_round.last_green_existed
                last_green_contents = instr_round.last_green_contents
            agent_unavailable = agent_unavailable or instr_round.agent_unavailable

    if run_instrumented_tests(config) and last_after_gap_raw is not None:
        merged_after, _, _ = await parse_latest_unified_coverage_gap(
            module_dir,
            source_file_path,
            source_code,
            project_root=project_root,
            gradle_tasks=acceptance_tasks,
            include_instrumented=True,
        )
        if merged_after is not None:
            last_after_gap_raw = merged_after
            last_after_gap = filter_gap(merged_after, source_file_path)

    validation_issues = list(last_outcome.validation_issues) if last_outcome else []
    coverage_summary = build_coverage_summary(
        plan=plan, before_gap=initial_gap, after_gap=last_after_gap,
        before_gap_raw=initial_gap_raw, after_gap_raw=last_after_gap_raw,
        improved=any_improved if any_green else False,
        why_not_improved="" if any_improved else "Gradle/validation passed but Kover delta did not improve.",
    )
    common = dict(
        before_gap=initial_gap, after_gap=last_after_gap,
        validation_issues=validation_issues, coverage_summary=coverage_summary,
    )

    if agent_unavailable and not any_green:
        return finish(
            accepted=False, outcome="pipeline_unresolved", failure_stage="agent_unavailable",
            evidence=(last_outcome.evidence if last_outcome else f"{agent} unavailable."), **common,
        )

    if any_green and any_improved:
        write_test_snapshot(test_path, last_green_existed, last_green_contents)
        _log_python_acceptance(plan=plan, before_gap=initial_gap, after_gap=last_after_gap, test_path=test_path)
        return finish(accepted=True, outcome="accepted", gradle_passed=True, kover_improved=True, **common)

    if any_green:
        _persist_kover_no_coverage(
            plan, source_file_path=source_file_path, module=module, before_gap=initial_gap, after_gap=last_after_gap,
        )
        restore_or_delete(test_path, existed, original)
        return finish(
            accepted=False, outcome="rejected", gradle_passed=True, kover_improved=False,
            failure_stage="kover_no_coverage_gain",
            evidence="Gradle passed but Kover coverage did not improve.", **common,
        )

    if empty_output_only and not agent_unavailable:
        stage, evidence = "empty_model_output", f"{agent} finished without creating the expected test file."
    elif validation_issues:
        stage, evidence = "static_validation", "New static validation issues remain after fix attempts."
    else:
        stage, evidence = "gradle_repair", "Generated test failed Gradle/JUnit verification."
    restore_or_delete(test_path, existed, original)
    return finish(
        accepted=False, outcome="pipeline_unresolved", gradle_passed=False, kover_improved=False,
        failure_stage=stage, evidence=evidence, **common,
    )
