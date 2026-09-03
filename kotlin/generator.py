# Proprietary — Sunilkumar Pathipati. Thin CLI coordinator for Claude Code Kotlin test generation.
"""Public CLI and per-target loop for the Claude Code agent pipeline."""

from __future__ import annotations

import argparse
import os
import signal
import time

from UnitTest_gen.core.diagnostics import write_diagnostics
from UnitTest_gen.core.llm import ensure_coding_server, is_server_healthy, resolve_agent_model, stop_coding_server
from UnitTest_gen.core.io import (
    PipelineLogger,
    archive_generated_side_files,
    get_pipeline_log_dir,
    log_message,
    log_section,
    set_active_logger,
)
from UnitTest_gen.core.config import (
    TEST_MODES,
    agent_env_display_name,
    apply_cli_args,
    get_config,
    load_config_from_env,
    set_active_config,
)
from UnitTest_gen.helper.dashboard import refresh_diagnostic_reports
from UnitTest_gen.kotlin.pipeline import run_agent_for_source
from UnitTest_gen.kotlin.project import (
    default_gradle_tasks_for_target,
    discover_target_sources,
    find_source_root_for_path,
    resolve_path_against_root,
)
from UnitTest_gen.kotlin.analysis import SemgrepUnavailableError, require_semgrep_preflight

def format_source_elapsed_minutes(start_time: float) -> str:
    return f"{(time.monotonic() - start_time) / 60.0:.2f} min"

async def generate_android_tests(
    target_path,
    output_base_directory,
    project_root,
    gradle_offline=False,
    gradle_tasks=None,
):
    try:
        semgrep_runtime_version = require_semgrep_preflight()
    except SemgrepUnavailableError as exc:
        print(f"❌ Mandatory Semgrep preflight failed: {exc}")
        return False
    print(f"🔎 Semgrep static-policy preflight: {semgrep_runtime_version}")

    project_root = os.path.abspath(project_root)
    target_path = resolve_path_against_root(target_path, project_root)
    if not gradle_tasks:
        gradle_tasks = default_gradle_tasks_for_target(project_root, target_path)

    target_source_root = find_source_root_for_path(target_path)
    targets = discover_target_sources(target_path, target_source_root)
    if not targets:
        print(f"❌ No Kotlin source files found for target: {target_path}")
        return False

    output_base_directory = (
        resolve_path_against_root(output_base_directory, project_root) if output_base_directory else None
    )

    cfg = get_config()
    print(
        f"\n🚀 Starting full-agent test generation pipeline "
        f"(phase={cfg.pipeline_phase}, agent={agent_env_display_name()})..."
    )
    print(f"📄 Target path: {target_path}")
    print(f"📦 Target source root: {target_source_root}")
    print(f"🧮 Files queued: {len(targets)}")
    all_targets_succeeded = True

    for index_key, discovered_file_data in targets.items():
        source_start_time = time.monotonic()
        class_name = discovered_file_data.get("class_name", index_key)
        source_file_path = discovered_file_data["path"]
        source_code = discovered_file_data["content"]

        pipeline_log_dir = get_pipeline_log_dir()
        test_log_path = os.path.join(
            pipeline_log_dir,
            os.path.basename(source_file_path).replace(".kt", ".testgen.log"),
        )
        set_active_logger(PipelineLogger(test_log_path))
        log_section(f"TEST GENERATION START: {source_file_path}", category="info")
        log_message(f"📁 Gradle project root: {project_root}", category="info")
        log_message(f"📝 Pipeline log file: {test_log_path}", category="info")

        result = await run_agent_for_source(
            source_file_path=source_file_path,
            project_root=project_root,
            class_name=class_name,
            source_code=source_code,
            output_base_directory=output_base_directory,
            gradle_tasks=gradle_tasks,
            gradle_offline=gradle_offline,
        )
        archive_generated_side_files(result.test_file_path, source_file_path)
        if not result.accepted:
            all_targets_succeeded = False
        status = "passed" if result.accepted else "failed"
        log_message(
            f"⏱️ Source file completed in {format_source_elapsed_minutes(source_start_time)} "
            f"({status}).",
            category="success" if result.accepted else "warning",
        )

    write_diagnostics(project_root)
    try:
        refresh_diagnostic_reports(project_root)
    except Exception as exc:  # noqa: BLE001 — dashboard must not fail the generator
        print(f"⚠️ Diagnostic HTML refresh failed (generator result unchanged): {exc}")
    return all_targets_succeeded

async def async_main():
    parser = argparse.ArgumentParser(
        description=(
            "AI Android Test Generator powered by Claude Code + local llama.cpp. "
            "The agent writes tests; Python verifies Gradle and Kover acceptance."
        )
    )
    parser.add_argument(
        "--env",
        dest="agent_env",
        choices=("claude",),
        default=None,
        help="Claude Code agent (only supported vendor). Shared llama :8080 / proxy :8081. "
        "Also TESTGEN_AGENT_ENV. Use --pipeline-phase for plan-only / coder-only.",
    )
    parser.add_argument(
        "--pipeline-phase",
        dest="pipeline_phase",
        choices=("both", "plan", "coder"),
        default=None,
        help="both = plan+coder; plan = save .plan.md only; "
        "coder = code only (uses saved plan if present, else Kover delta). "
        "Also TESTGEN_PIPELINE_PHASE. Legacy --plan-env / --coder-env still select phase only.",
    )
    parser.add_argument(
        "--plan-env",
        dest="plan_agent_env",
        choices=("claude",),
        default=None,
        help=argparse.SUPPRESS,  # legacy phase selector
    )
    parser.add_argument(
        "--coder-env",
        dest="coder_agent_env",
        choices=("claude",),
        default=None,
        help=argparse.SUPPRESS,  # legacy phase selector
    )

    target_group = parser.add_mutually_exclusive_group(required=True)
    target_group.add_argument(
        "-p",
        "--path",
        type=str,
        help="Target Kotlin file or source directory. Absolute or relative to --project-root.",
    )
    target_group.add_argument(
        "-F",
        "--folder",
        type=str,
        help="Target source folder. All .kt files under this folder are processed one by one.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        default=None,
        help="Optional output test source root. If omitted, src/main/java maps to src/test/java.",
    )
    parser.add_argument(
        "-R",
        "--project-root",
        type=str,
        required=True,
        help="Android/Gradle project root where ./gradlew exists.",
    )
    parser.add_argument(
        "--gradle-offline",
        action="store_true",
        help="Run Gradle with --offline.",
    )
    parser.add_argument(
        "-G",
        "--gradle-task",
        action="append",
        default=None,
        help="Gradle task for verification/Kover. Repeat for multiple tasks.",
    )
    parser.add_argument(
        "--local-llm-root",
        type=str,
        default=None,
        help="Path to AgenticLLM assets (default: UnitTest_gen/AgenticLLM).",
    )
    parser.add_argument(
        "--agent-model",
        type=str,
        default=None,
        help="Override agent model alias for all phases (TESTGEN_AGENT_MODEL).",
    )
    parser.add_argument(
        "--agent-model-path",
        type=str,
        default=None,
        help="Override default GGUF path (TESTGEN_AGENT_MODEL_PATH).",
    )
    parser.add_argument(
        "--agent-timeout",
        type=int,
        default=None,
        help="Seconds allowed for one agent invocation.",
    )
    parser.add_argument(
        "--server-startup-timeout",
        type=int,
        default=None,
        help="Seconds to wait for the local llama-server to become healthy.",
    )
    parser.add_argument(
        "--no-auto-start-local-llm",
        action="store_true",
        help="Do not auto-start the vendored llama-server; require it to already be running.",
    )
    parser.add_argument(
        "--skip-server-preflight",
        action="store_true",
        help="Skip the local /health check before generation.",
    )
    parser.add_argument(
        "--disable-kover-acceptance",
        action="store_true",
        help="Keep tests that pass Gradle even when Kover does not improve.",
    )
    parser.add_argument(
        "--disable-jacoco-acceptance",
        action="store_true",
        help="Skip JaCoCo delta checks for androidTest slices.",
    )
    parser.add_argument(
        "--test-mode",
        choices=sorted(TEST_MODES),
        default=None,
        help="auto (default) | unit | instrumented — auto assigns exclusive layer per plan item.",
    )
    parser.add_argument(
        "--no-post-validation",
        action="store_true",
        help="Skip post-agent static validation (sets TESTGEN_ENABLE_GUARDRAILS=0); FAST_VERIFY only.",
    )
    parser.add_argument(
        "--print-prompts",
        action="store_true",
        help="Print full agent prompts to the terminal.",
    )
    reasoning_group = parser.add_mutually_exclusive_group()
    reasoning_group.add_argument(
        "--print-reasoning",
        action="store_true",
        default=None,
        help="Show model thinking/reasoning in the terminal (also logged). "
        "Default off; also TESTGEN_PRINT_REASONING=1. Does not control tool lines.",
    )
    reasoning_group.add_argument(
        "--no-print-reasoning",
        action="store_true",
        help="Hide model thinking in the terminal; still write it to the pipeline log.",
    )
    tools_group = parser.add_mutually_exclusive_group()
    tools_group.add_argument(
        "--print-tools",
        action="store_true",
        default=None,
        help="Show verbose [tool:start]/[tool:done]/partial tool lines in the terminal. "
        "Default off; also TESTGEN_PRINT_TOOLS=1.",
    )
    tools_group.add_argument(
        "--no-print-tools",
        action="store_true",
        help="TTY shows minimal [tool] name start/done status only; full args and "
        "partial stdout go to the pipeline log.",
    )
    parser.add_argument(
        "--stop-server-on-exit",
        action="store_true",
        help="Stop the vendored llama-server when this process exits.",
    )

    args = parser.parse_args()
    pipeline_config = apply_cli_args(load_config_from_env(), args)
    set_active_config(pipeline_config)

    project_root = os.path.abspath(os.path.expanduser(args.project_root))
    requested_target = args.folder if args.folder else args.path
    target_path = resolve_path_against_root(requested_target, project_root)

    previous_sigterm_handler = signal.getsignal(signal.SIGTERM)

    def _handle_sigterm(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _handle_sigterm)

    started_server = False
    try:
        if pipeline_config.auto_start_local_llm and not is_server_healthy():
            try:
                resolve_agent_model(pipeline_config)
            except RuntimeError as exc:
                print(f"❌ {exc}")
                return False
            if not ensure_coding_server(
                pipeline_config.server_startup_timeout,
                model_path=pipeline_config.agent_model_path,
                model_alias=pipeline_config.agent_model,
            ):
                print("❌ Local model server did not become healthy.")
                return False
            started_server = True
        elif not args.skip_server_preflight and not is_server_healthy():
            print(
                "❌ Local model server is not healthy.\n"
                f"   Start {pipeline_config.local_llm_root}/scripts/start-llama-server.sh "
                f"(--env {pipeline_config.agent_env}), or omit --no-auto-start-local-llm."
            )
            return False

        return await generate_android_tests(
            target_path=target_path,
            output_base_directory=args.output,
            project_root=project_root,
            gradle_offline=pipeline_config.gradle_offline,
            gradle_tasks=args.gradle_task,
        )
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return False
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm_handler)
        if args.stop_server_on_exit and started_server:
            stop_coding_server()
