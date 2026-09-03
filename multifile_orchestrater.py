# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Sequential multi-file UnitTest_gen smoke runs over smallest uncovered sources.
"""Orchestrate N one-file UnitTest_gen runs (sequential by default, optional parallel).

Selects uncovered Kotlin sources from on-disk Kover XML, ranked by ascending
actionable gap (missed lines/branches minus deferred unreachable lines), then
by LOC. Files whose gaps are fully deferred are skipped.

Each smoke run passes ``-G :module:koverXmlReport`` (module-scoped Kover gate);
FAST_VERIFY stays file-scoped (``:module:test… --tests Class``) inside the generator.

Example (from repo root, llama-server already healthy):

  python UnitTest_gen/multifile_orchestrater.py \\
    -R /path/to/conx-journey-log-app \\
    -n 5 \\
    --env claude \\
    --no-auto-start-local-llm

Parallel (jobs > 1):

  python UnitTest_gen/multifile_orchestrater.py -n 4 --parallel --jobs 2 \\
    --plan-env claude --coder-env claude --no-auto-start-local-llm
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

UNITTEST_GEN_DIR = Path(__file__).resolve().parent
REPO_DEFAULT = UNITTEST_GEN_DIR.parent
GENERATOR = UNITTEST_GEN_DIR / "AI_Unittestgenerator.py"

_REPO_ROOT = str(REPO_DEFAULT)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from UnitTest_gen.core.config import env_flag, _env_int  # noqa: E402


@dataclass(frozen=True)
class SmokeTarget:
    """One smoke source for a sequential generator invocation."""

    label: str
    relative_path: str
    gradle_tasks: tuple[str, ...]
    note: str
    loc: int = 0
    line_missed: int = 0
    branch_missed: int = 0
    actionable_line_missed: int = 0
    actionable_branch_missed: int = 0


def _normalize_module_filter(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    normalized = raw.replace("\\", "/").strip("/")
    if normalized.startswith(":"):
        normalized = normalized[1:].replace(":", "/").strip("/")
    return normalized


def _normalize_path_prefix(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return raw.replace("\\", "/").strip("/")


def _candidate_matches_filters(
    *,
    candidate_module: str,
    candidate_relative_path: str,
    module_filters: list[str] | tuple[str, ...],
    source_prefix_filters: list[str] | tuple[str, ...],
) -> bool:
    has_module_filters = bool(module_filters)
    has_prefix_filters = bool(source_prefix_filters)

    module_ok = True
    if has_module_filters:
        module_ok = candidate_module in module_filters

    prefix_ok = True
    if has_prefix_filters:
        prefix_ok = any(
            candidate_relative_path == prefix
            or candidate_relative_path.startswith(prefix + "/")
            for prefix in source_prefix_filters
        )

    if has_module_filters and has_prefix_filters:
        return module_ok and prefix_ok
    if has_module_filters:
        return module_ok
    if has_prefix_filters:
        return prefix_ok
    return True


def module_kover_xml_tasks(project_root: Path, source_path: Path) -> tuple[str, ...]:
    """Return ``(:module:unitCoverageReport,)`` for the source's Gradle module.

    Falls back to root ``unitCoverageReport`` when the owning module cannot be resolved.
    """
    from UnitTest_gen.kotlin.project import (
        find_owning_module_dir,
        module_path_for_dir,
    )

    module_dir = find_owning_module_dir(str(project_root), str(source_path))
    module_path = module_path_for_dir(str(project_root), module_dir)
    if module_path:
        return (f"{module_path}:unitCoverageReport",)
    return ("unitCoverageReport",)


def file_loc(path: Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        return 0


def _actionable_gaps_for_row(
    row: dict,
    source_path: Path,
    unreachable_entries: list[dict[str, Any]],
) -> tuple[int, int]:
    """Return actionable (line, branch) counts after subtracting deferred lines.

    When line lists are missing but aggregate counts are positive (e.g. thin test
    fixtures), fall back to the aggregates with no deferral subtraction.
    """
    from UnitTest_gen.core.diagnostics import (
        derive_coverage_status,
        match_unreachable_entries,
    )

    missed_lines = list(row.get("missed_lines") or [])
    missed_branches = list(row.get("missed_branches") or [])
    line_missed = int(row.get("line_missed") or 0)
    branch_missed = int(row.get("branch_missed") or 0)

    if not missed_lines and not missed_branches:
        return (line_missed, branch_missed)

    matched = match_unreachable_entries(
        unreachable_entries,
        source=str(source_path),
        source_name=str(row.get("source_name") or source_path.name),
        module=str(row.get("module") or ""),
    )
    status = derive_coverage_status(
        accepted=True,
        outcome=None,
        missed_lines=missed_lines,
        missed_branches=missed_branches,
        unreachable_entries=matched,
    )
    return (
        int(status.get("actionable_missed_lines_count") or 0),
        int(status.get("actionable_missed_branches_count") or 0),
    )


def select_uncovered_targets(
    project_root: Path,
    *,
    count: int,
    projection_rows: list[dict] | None = None,
    module_filters: list[str] | tuple[str, ...] = (),
    source_prefix_filters: list[str] | tuple[str, ...] = (),
) -> list[SmokeTarget]:
    """Pick up to ``count`` uncovered sources, smallest actionable gap first."""
    from UnitTest_gen.core.diagnostics import (
        build_files_projection,
        resolve_kover_source_path,
        unified_coverage_snapshot,
    )
    from UnitTest_gen.kotlin.coverage import load_store as load_unreachable_store

    if count < 1:
        raise ValueError("count must be >= 1")

    rows = projection_rows
    if rows is None:
        snapshot = unified_coverage_snapshot(project_root)
        if not snapshot:
            return []
        rows = build_files_projection(snapshot)

    unreachable_payload = load_unreachable_store()
    unreachable_entries = [
        entry
        for entry in (unreachable_payload.get("entries") or [])
        if isinstance(entry, dict)
    ]

    ranked: list[SmokeTarget] = []
    seen: set[str] = set()
    normalized_module_filters = tuple(
        x for x in (_normalize_module_filter(v) for v in module_filters) if x
    )
    normalized_prefix_filters = tuple(
        x for x in (_normalize_path_prefix(v) for v in source_prefix_filters) if x
    )
    for row in rows:
        line_missed = int(row.get("line_missed") or 0)
        branch_missed = int(row.get("branch_missed") or 0)
        if line_missed <= 0 and branch_missed <= 0:
            continue
        source = resolve_kover_source_path(project_root, row)
        if source is None:
            continue
        try:
            relative = str(source.relative_to(project_root))
        except ValueError:
            relative = str(source)
        relative_norm = relative.replace("\\", "/")
        module_norm = _normalize_module_filter(str(row.get("module") or ""))
        if not _candidate_matches_filters(
            candidate_module=module_norm,
            candidate_relative_path=relative_norm,
            module_filters=normalized_module_filters,
            source_prefix_filters=normalized_prefix_filters,
        ):
            continue
        if relative in seen:
            continue
        seen.add(relative)

        actionable_lines, actionable_branches = _actionable_gaps_for_row(
            row, source, unreachable_entries
        )
        if actionable_lines <= 0 and actionable_branches <= 0:
            continue

        loc = file_loc(source)
        label = Path(str(row.get("source_name") or source.name)).stem
        ranked.append(
            SmokeTarget(
                label=label,
                relative_path=relative,
                gradle_tasks=module_kover_xml_tasks(project_root, source),
                note=(
                    f"actionable lines={actionable_lines}; "
                    f"actionable branches={actionable_branches}; "
                    f"raw lines={line_missed}; raw branches={branch_missed}; "
                    f"LOC={loc}"
                ),
                loc=loc,
                line_missed=line_missed,
                branch_missed=branch_missed,
                actionable_line_missed=actionable_lines,
                actionable_branch_missed=actionable_branches,
            )
        )

    ranked.sort(
        key=lambda t: (
            t.actionable_line_missed + t.actionable_branch_missed,
            t.loc,
            t.relative_path,
        )
    )
    return ranked[:count]


def _build_cmd(
    *,
    project_root: Path,
    target: SmokeTarget,
    args: argparse.Namespace,
    force_no_auto_start: bool = False,
) -> list[str]:
    source = (project_root / target.relative_path).resolve()
    cmd = [
        sys.executable,
        str(GENERATOR),
        "-R",
        str(project_root),
        "-p",
        str(source),
    ]
    if getattr(args, "env", None):
        cmd.extend(["--env", args.env])
    if getattr(args, "plan_env", None):
        cmd.extend(["--plan-env", args.plan_env])
    if getattr(args, "coder_env", None):
        cmd.extend(["--coder-env", args.coder_env])
    for task in target.gradle_tasks:
        cmd.extend(["-G", task])
    if args.gradle_offline:
        cmd.append("--gradle-offline")
    if args.no_auto_start_local_llm or force_no_auto_start:
        cmd.append("--no-auto-start-local-llm")
    if args.skip_server_preflight:
        cmd.append("--skip-server-preflight")
    if args.stop_server_on_exit:
        cmd.append("--stop-server-on-exit")
    if getattr(args, "no_post_validation", False):
        cmd.append("--no-post-validation")
    if getattr(args, "agent_model", None):
        cmd.extend(["--agent-model", args.agent_model])
    if getattr(args, "agent_model_path", None):
        cmd.extend(["--agent-model-path", args.agent_model_path])
    if args.print_reasoning:
        cmd.append("--print-reasoning")
    if args.print_tools:
        cmd.append("--print-tools")
    if args.agent_timeout is not None:
        cmd.extend(["--agent-timeout", str(args.agent_timeout)])
    if getattr(args, "test_mode", None):
        cmd.extend(["--test-mode", args.test_mode])
    if getattr(args, "disable_jacoco_acceptance", False):
        cmd.append("--disable-jacoco-acceptance")
    return cmd


def _run_one(
    *,
    idx: int,
    total: int,
    project_root: Path,
    target: SmokeTarget,
    args: argparse.Namespace,
    force_no_auto_start: bool,
) -> dict[str, Any]:
    source = project_root / target.relative_path
    print("=" * 88)
    print(f"[{idx}/{total}] {target.label}")
    print(f"  path: {source}")
    print(f"  note: {target.note}")
    if not source.is_file():
        print("  ❌ Missing source file — skipping")
        return {
            **asdict(target),
            "exit_code": 127,
            "elapsed_seconds": 0.0,
            "ok": False,
            "error": "missing_source",
            "index": idx,
        }

    cmd = _build_cmd(
        project_root=project_root,
        target=target,
        args=args,
        force_no_auto_start=force_no_auto_start,
    )
    print(f"  cmd: {' '.join(cmd)}")
    started = time.monotonic()
    completed = subprocess.run(cmd, cwd=str(REPO_DEFAULT))
    elapsed = time.monotonic() - started
    ok = completed.returncode == 0
    status = "OK" if ok else f"FAIL (exit {completed.returncode})"
    print(f"  → [{idx}/{total}] {status} in {elapsed / 60:.2f} min")
    return {
        **asdict(target),
        "exit_code": completed.returncode,
        "elapsed_seconds": round(elapsed, 2),
        "ok": ok,
        "index": idx,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run N uncovered Kotlin sources (smallest actionable gap first; "
            "deferred-only files excluded) through UnitTest_gen. "
            "Sequential by default; use --parallel --jobs N for concurrent files."
        )
    )
    parser.add_argument(
        "-R",
        "--project-root",
        type=str,
        default=str(REPO_DEFAULT),
        help=f"Android/Gradle project root (default: {REPO_DEFAULT})",
    )
    parser.add_argument(
        "-n",
        "--count",
        type=int,
        default=5,
        help=(
            "How many uncovered files to run (smallest actionable gap first). "
            "Default: 5."
        ),
    )
    parser.add_argument(
        "--env",
        choices=("claude",),
        default=None,
        help="Run plan+coder with this CLI for both phases. Also TESTGEN_AGENT_ENV.",
    )
    parser.add_argument(
        "--plan-env",
        choices=("claude",),
        default=None,
        help="Planner CLI. Alone = plan-only. With --coder-env = plan+coder.",
    )
    parser.add_argument(
        "--coder-env",
        choices=("claude",),
        default=None,
        help="Coder/fix CLI. Alone = coder-only (saved plan if present, else Kover delta). With --plan-env = plan+coder.",
    )
    parser.add_argument(
        "--parallel",
        action="store_true",
        default=None,
        help="Run file generators concurrently (default off; also TESTGEN_MULTIFILE_PARALLEL=1).",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=None,
        help="Max concurrent file generators when parallel (default 1; TESTGEN_MULTIFILE_JOBS).",
    )
    parser.add_argument(
        "--gradle-offline",
        action="store_true",
        help="Pass --gradle-offline through to the generator.",
    )
    parser.add_argument(
        "--no-auto-start-local-llm",
        action="store_true",
        help="Do not auto-start llama-server (expect it already healthy).",
    )
    parser.add_argument(
        "--skip-server-preflight",
        action="store_true",
        help="Skip local-server health preflight.",
    )
    parser.add_argument(
        "--stop-server-on-exit",
        action="store_true",
        help="Stop vendored llama-server after each file (usually leave off for smoke).",
    )
    parser.add_argument(
        "--no-post-validation",
        action="store_true",
        help="Forward --no-post-validation to the generator (skip post-agent static validation).",
    )
    parser.add_argument("--agent-model", type=str, default=None, help="Forward --agent-model.")
    parser.add_argument("--agent-model-path", type=str, default=None, help="Forward --agent-model-path.")
    parser.add_argument(
        "--print-reasoning",
        action="store_true",
        help="Forward --print-reasoning to the generator.",
    )
    parser.add_argument(
        "--print-tools",
        action="store_true",
        help="Forward --print-tools to the generator.",
    )
    parser.add_argument(
        "--agent-timeout",
        type=int,
        default=None,
        help="Optional per-agent timeout seconds forwarded to the generator.",
    )
    parser.add_argument(
        "--test-mode",
        choices=("unit", "instrumented", "hybrid", "auto"),
        default=None,
        help="Forward --test-mode to the generator (auto / unit / instrumented).",
    )
    parser.add_argument(
        "--disable-jacoco-acceptance",
        action="store_true",
        help="Forward --disable-jacoco-acceptance to the generator.",
    )
    parser.add_argument(
        "--stop-on-fail",
        action="store_true",
        help="Stop scheduling further files after the first non-zero generator exit.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print the selected uncovered queue (after -n ranking) and exit.",
    )
    parser.add_argument(
        "--results-json",
        type=str,
        default="",
        help="Optional path to write a JSON results summary.",
    )
    parser.add_argument(
        "--module",
        action="append",
        default=[],
        help=(
            "Repeatable module filter. Accepts ':feature:recordtrip' or "
            "'feature/recordtrip'. Candidates must match module exactly."
        ),
    )
    parser.add_argument(
        "--source-prefix",
        action="append",
        default=[],
        help=(
            "Repeatable source-path prefix filter under project root, e.g. "
        ),
    )
    args = parser.parse_args(argv)

    if args.count < 1:
        print("❌ --count / -n must be >= 1", file=sys.stderr)
        return 2

    parallel = bool(args.parallel) if args.parallel is not None else env_flag(
        "TESTGEN_MULTIFILE_PARALLEL", False
    )
    jobs = args.jobs if args.jobs is not None else max(1, _env_int("TESTGEN_MULTIFILE_JOBS", 1))
    if jobs < 1:
        print("❌ --jobs must be >= 1", file=sys.stderr)
        return 2
    if not parallel:
        jobs = 1

    project_root = Path(os.path.abspath(os.path.expanduser(args.project_root)))
    if not (project_root / "gradlew").is_file():
        print(f"❌ No ./gradlew under project root: {project_root}", file=sys.stderr)
        return 2

    normalized_module_filters = [
        x for x in (_normalize_module_filter(v) for v in (args.module or [])) if x
    ]
    normalized_source_prefixes = [
        x for x in (_normalize_path_prefix(v) for v in (args.source_prefix or [])) if x
    ]

    selected = select_uncovered_targets(
        project_root,
        count=args.count,
        module_filters=normalized_module_filters,
        source_prefix_filters=normalized_source_prefixes,
    )
    if not selected:
        print(
            "❌ No actionable uncovered sources (all covered or fully deferred).",
            file=sys.stderr,
        )
        return 2

    if args.list:
        if normalized_module_filters:
            print(f"Module filters: {', '.join(normalized_module_filters)}")
        if normalized_source_prefixes:
            print(f"Source-prefix filters: {', '.join(normalized_source_prefixes)}")
        print(
            f"Selected {len(selected)} uncovered file(s) "
            f"(requested -n {args.count}, smallest actionable gap first; "
            f"deferred-only files excluded):"
        )
        for idx, target in enumerate(selected, start=1):
            print(f"{idx}. {target.label}")
            print(f"   {target.relative_path}")
            print(f"   {target.note}")
        return 0

    if not GENERATOR.is_file():
        print(f"❌ Generator not found: {GENERATOR}", file=sys.stderr)
        return 2

    plan_label = args.plan_env or args.env
    coder_label = args.coder_env or args.env
    print(f"Smoke orchestrator started at {datetime.now().isoformat(timespec='seconds')}")
    print(f"Plan env: {plan_label} | Coder env: {coder_label}")
    print(f"Project root: {project_root}")
    if normalized_module_filters:
        print(f"Module filters: {', '.join(normalized_module_filters)}")
    if normalized_source_prefixes:
        print(f"Source-prefix filters: {', '.join(normalized_source_prefixes)}")
    mode = f"parallel jobs={jobs}" if parallel and jobs > 1 else "sequential"
    print(
        f"Files: {len(selected)} ({mode}, smallest actionable gap first; "
        f"deferred-only excluded)\n"
    )

    results: list[dict] = []
    overall_ok = True

    if jobs <= 1:
        for idx, target in enumerate(selected, start=1):
            row = _run_one(
                idx=idx,
                total=len(selected),
                project_root=project_root,
                target=target,
                args=args,
                force_no_auto_start=False,
            )
            results.append(row)
            overall_ok = overall_ok and bool(row.get("ok"))
            if args.stop_on_fail and not row.get("ok"):
                print("Stopping early (--stop-on-fail).")
                break
    else:
        # First file may auto-start llama; remaining workers never auto-start.
        first = selected[0]
        first_row = _run_one(
            idx=1,
            total=len(selected),
            project_root=project_root,
            target=first,
            args=args,
            force_no_auto_start=False,
        )
        results.append(first_row)
        overall_ok = overall_ok and bool(first_row.get("ok"))
        remaining = list(enumerate(selected[1:], start=2))
        if args.stop_on_fail and not first_row.get("ok"):
            print("Stopping early (--stop-on-fail).")
            remaining = []

        if remaining:
            # Cap workers at jobs; first slot already finished.
            workers = max(1, min(jobs, len(remaining)))
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                future_map = {
                    pool.submit(
                        _run_one,
                        idx=idx,
                        total=len(selected),
                        project_root=project_root,
                        target=target,
                        args=args,
                        force_no_auto_start=True,
                    ): idx
                    for idx, target in remaining
                }
                done_rows: dict[int, dict] = {}
                cancel_rest = False
                for fut in concurrent.futures.as_completed(future_map):
                    row = fut.result()
                    done_rows[int(row["index"])] = row
                    if not row.get("ok"):
                        overall_ok = False
                        if args.stop_on_fail:
                            cancel_rest = True
                            for pending in future_map:
                                pending.cancel()
                            break
                if cancel_rest:
                    print("Stopping early (--stop-on-fail); in-flight workers may still finish.")
                for idx in sorted(done_rows):
                    results.append(done_rows[idx])

    results.sort(key=lambda r: int(r.get("index") or 0))

    print("\n" + "=" * 88)
    print("SMOKE SUMMARY")
    for row in results:
        mark = "✅" if row.get("ok") else "❌"
        print(
            f"  {mark} {row['label']}: exit={row['exit_code']} "
            f"({row['elapsed_seconds'] / 60:.2f} min)"
        )
    passed = sum(1 for row in results if row.get("ok"))
    print(f"Passed {passed}/{len(results)}")

    if args.results_json:
        out = Path(args.results_json).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "project_root": str(project_root),
            "count_requested": args.count,
            "parallel": parallel,
            "jobs": jobs,
            "plan_env": plan_label,
            "coder_env": coder_label,
            "results": results,
            "passed": passed,
            "total": len(results),
        }
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Wrote results → {out}")

    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
