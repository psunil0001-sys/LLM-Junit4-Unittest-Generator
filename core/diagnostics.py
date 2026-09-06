"""Kover/JaCoCo diagnostics model and diagnostics.json store."""

from __future__ import annotations

import os
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from UnitTest_gen.core import io as file_cache
from UnitTest_gen.core.io import save_json
from UnitTest_gen.core.io import coerce_int_set, compact_ranges
from UnitTest_gen.core.io import log_message
from UnitTest_gen.kotlin.coverage import discover_jacoco_xml, merge_missed_line_sets

SCHEMA_VERSION = "4.1"
PACKAGE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = PACKAGE_DIR / "data"
STORE_PATH = DATA_DIR / "diagnostic_store.json"
DIAGNOSTICS_PATH = DATA_DIR / "diagnostics.json"

def _empty_store() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "updated_at": "", "sources": {}}

def load_store(path: Path | str = STORE_PATH) -> dict[str, Any]:
    store = file_cache.read_json(path, default_factory=_empty_store)
    if not isinstance(store, dict) or not isinstance(store.get("sources"), dict):
        return _empty_store()
    store.setdefault("schema_version", SCHEMA_VERSION)
    return store

def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    save_json(path, payload)
    return Path(path)

def _gap_payload(gap) -> dict[str, Any] | None:
    if gap is None:
        return None
    line_missed, line_covered = getattr(gap, "line_coverage", (0, 0))
    branch_missed, branch_covered = getattr(gap, "branch_coverage", (0, 0))
    return {
        "line_missed": int(line_missed),
        "line_covered": int(line_covered),
        "branch_missed": int(branch_missed),
        "branch_covered": int(branch_covered),
        "missed_lines": sorted(set(getattr(gap, "missed_lines", None) or [])),
        "partial_branch_lines": sorted(set(getattr(gap, "partial_branch_lines", None) or [])),
        "missed_methods": sorted(set(getattr(gap, "missed_methods", None) or [])),
    }

def record_source_run(
    *,
    source_file_path: str,
    test_file_path: str = "",
    module: str = "",
    outcome: str = "",
    accepted: bool = False,
    gradle_passed: bool = False,
    kover_improved: bool = False,
    failure_stage: str = "",
    evidence: str = "",
    duration_seconds: float = 0.0,
    before_gap=None,
    after_gap=None,
    validation_issues: list[str] | None = None,
    coverage_summary: dict[str, Any] | None = None,
    store_path: Path | str = STORE_PATH,
) -> None:
    """Merge one source-file run outcome into the diagnostic store."""
    try:
        store = load_store(store_path)
        key = os.path.abspath(source_file_path)
        record = store["sources"].get(key, {})
        record.update(
            {
                "source": key,
                "source_name": os.path.basename(key),
                "test_file": os.path.abspath(test_file_path) if test_file_path else "",
                "module": module,
                "outcome": outcome,
                "accepted": bool(accepted),
                "gradle_passed": bool(gradle_passed),
                "kover_improved": bool(kover_improved),
                "failure_stage": failure_stage,
                "evidence": evidence,
                "duration_seconds": round(float(duration_seconds), 2),
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "validation_issues": list(validation_issues or []),
                "before_gap": _gap_payload(before_gap),
                "after_gap": _gap_payload(after_gap),
            }
        )
        if coverage_summary is not None:
            record["coverage_summary"] = coverage_summary
        store["sources"][key] = record
        store["updated_at"] = datetime.now().isoformat(timespec="seconds")
        _write_json(Path(store_path), store)
    except Exception:
        return

def discover_kover_xml(project_root: Path | str) -> list[Path]:
    root = Path(project_root)
    return sorted(root.glob("**/build/reports/kover/**/*.xml"))

def _snapshot_row_from_gap(gap, *, coverage_sources: list[str] | None = None) -> dict[str, Any]:
    key = f"{gap.module_hint}|{gap.package_name}|{gap.source_file_name}"
    line_missed = int(getattr(gap, "line_missed", 0) or 0) or len(gap.missed_lines)
    branch_missed = int(getattr(gap, "branch_missed", 0) or 0)
    instruction_missed = int(getattr(gap, "instruction_missed", 0) or 0)
    instruction_covered = int(getattr(gap, "instruction_covered", 0) or 0)
    method_missed = int(getattr(gap, "method_missed", 0) or 0)
    method_covered = int(getattr(gap, "method_covered", 0) or 0)
    class_missed = int(getattr(gap, "class_missed", 0) or 0)
    class_covered = int(getattr(gap, "class_covered", 0) or 0)
    return {
        "key": key,
        "source_name": gap.source_file_name,
        "package": gap.package_name,
        "module": gap.module_hint,
        "missed_lines": sorted(gap.missed_lines),
        "missed_branches": sorted(gap.missed_branches),
        "line_covered": gap.line_covered,
        "branch_covered": gap.branch_covered,
        "line_missed": line_missed,
        "branch_missed": branch_missed,
        "instruction_covered": instruction_covered,
        "instruction_missed": instruction_missed,
        "method_covered": method_covered,
        "method_missed": method_missed,
        "class_covered": class_covered,
        "class_missed": class_missed,
        "line_coverage_pct": _ratio_pct(gap.line_covered, line_missed),
        "branch_coverage_pct": _ratio_pct(gap.branch_covered, branch_missed),
        "instruction_coverage_pct": _coverage_pct(instruction_covered, instruction_missed),
        "method_coverage_pct": _coverage_pct(method_covered, method_missed),
        "class_coverage_pct": _coverage_pct(class_covered, class_missed),
        "coverage_sources": list(coverage_sources or ["kover"]),
        "covered_lines": sorted(getattr(gap, "covered_lines", None) or []),
        "metric_elements": {
            metric: {
                "covered": sorted(covered),
                "total": sorted(total),
            }
            for metric, (covered, total) in (getattr(gap, "metric_elements", None) or {}).items()
        },
    }

def jacoco_snapshot(project_root: Path | str) -> dict[str, dict[str, Any]]:
    """Project-wide JaCoCo coverage keyed by module|package|source_name."""
    from UnitTest_gen.kotlin.coverage import load_kover_gaps

    root = Path(project_root)
    reports = discover_jacoco_xml(root)
    if not reports:
        return {}
    snapshot: dict[str, dict[str, Any]] = {}
    for gap in load_kover_gaps(reports, root, include_fully_covered=True).values():
        row = _snapshot_row_from_gap(gap, coverage_sources=["jacoco"])
        snapshot[row["key"]] = row
    return snapshot

def _row_has_coverage_hits(row: dict[str, Any] | None) -> bool:
    """True when a report recorded at least one covered line, branch, or instruction."""
    if not row:
        return False
    return any(
        int(row.get(key) or 0) > 0
        for key in ("line_covered", "branch_covered", "instruction_covered")
    )


def _merged_covered_count(
    unit_row: dict[str, Any],
    instr_row: dict[str, Any],
    *,
    covered_key: str,
    missed_key: str,
    merged_missed_n: int,
) -> int:
    unit_covered = int(unit_row.get(covered_key) or 0)
    instr_covered = int(instr_row.get(covered_key) or 0)
    unit_missed = len(unit_row.get(missed_key) or [])
    instr_missed = len(instr_row.get(missed_key) or [])
    total = max(unit_covered + unit_missed, instr_covered + instr_missed)
    return max(0, total - merged_missed_n)


def _sorted_line_nums(values) -> list[int]:
    return sorted({int(n) for n in (values or []) if int(n) > 0})


_ENGINE_METRIC_NAMES = ("line", "branch", "instruction", "method", "class")


def _engine_metrics(row: dict[str, Any] | None) -> dict[str, Any]:
    empty = {
        **{f"{m}_covered": 0 for m in _ENGINE_METRIC_NAMES},
        **{f"{m}_missed": 0 for m in _ENGINE_METRIC_NAMES},
        **{f"{m}_pct": None for m in _ENGINE_METRIC_NAMES},
        "covered_lines": [],
        "metric_elements": {},
    }
    if row is None:
        return empty
    out = dict(empty)
    for metric in _ENGINE_METRIC_NAMES:
        covered = int(row.get(f"{metric}_covered") or 0)
        if f"{metric}_missed" in row:
            missed = int(row.get(f"{metric}_missed") or 0)
        elif metric == "line":
            missed = len(row.get("missed_lines") or [])
        elif metric == "branch":
            missed = len(row.get("missed_branches") or [])
        else:
            missed = 0
        out[f"{metric}_covered"] = covered
        out[f"{metric}_missed"] = missed
        # Zero hits → None (dashboard shows —), not 0.0%
        out[f"{metric}_pct"] = None if covered <= 0 else _ratio_pct(covered, missed)
    out["covered_lines"] = _sorted_line_nums(row.get("covered_lines"))
    out["metric_elements"] = row.get("metric_elements") or {}
    return out


def _apply_engine_metrics(
    out: dict[str, Any],
    unit_row: dict[str, Any] | None,
    instr_row: dict[str, Any] | None,
) -> None:
    kover = _engine_metrics(unit_row)
    jacoco = _engine_metrics(instr_row)
    for engine_name, metrics in (("kover", kover), ("jacoco", jacoco)):
        for metric in _ENGINE_METRIC_NAMES:
            out[f"{engine_name}_{metric}_covered"] = metrics[f"{metric}_covered"]
            out[f"{engine_name}_{metric}_missed"] = metrics[f"{metric}_missed"]
            out[f"{engine_name}_{metric}_pct"] = metrics[f"{metric}_pct"]
    out["kover_covered_lines"] = kover["covered_lines"]
    out["jacoco_covered_lines"] = jacoco["covered_lines"]
    out["kover_metric_elements"] = kover["metric_elements"]
    out["jacoco_metric_elements"] = jacoco["metric_elements"]
    # Back-compat aliases used by files_projection / dashboard file table
    out["kover_line_pct"] = kover["line_pct"]
    out["kover_branch_pct"] = kover["branch_pct"]
    out["jacoco_line_pct"] = jacoco["line_pct"]
    out["jacoco_branch_pct"] = jacoco["branch_pct"]
    out["covered_lines"] = sorted(set(kover["covered_lines"]) | set(jacoco["covered_lines"]))


def _merge_snapshot_rows(
    unit_row: dict[str, Any] | None,
    instr_row: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Merge Kover + JaCoCo dashboard rows using the same gap rule as the pipeline.

    A JaCoCo XML with zero hits (typical empty connected report) is still attached
    as a source, but must not wipe Kover missed/covered counts.
    """
    if unit_row is None and instr_row is None:
        return None
    if instr_row is None:
        out = dict(unit_row)
        _apply_engine_metrics(out, unit_row, None)
        return out
    if unit_row is None:
        out = dict(instr_row)
        _apply_engine_metrics(out, None, instr_row)
        return out

    u_lines = set(unit_row.get("missed_lines") or [])
    i_lines = set(instr_row.get("missed_lines") or [])
    u_branches = set(unit_row.get("missed_branches") or [])
    i_branches = set(instr_row.get("missed_branches") or [])
    jacoco_hits = _row_has_coverage_hits(instr_row)

    merged = dict(unit_row)
    merged["coverage_sources"] = ["kover", "jacoco"]
    merged["kover_missed_lines"] = sorted(u_lines)
    merged["jacoco_missed_lines"] = sorted(i_lines)
    merged["kover_missed_branches"] = sorted(u_branches)
    merged["jacoco_missed_branches"] = sorted(i_branches)
    merged["jacoco_zero_hits"] = not jacoco_hits
    _apply_engine_metrics(merged, unit_row, instr_row)

    if not jacoco_hits:
        return merged

    merged_lines = merge_missed_line_sets(u_lines, i_lines)
    merged_branches = merge_missed_line_sets(u_branches, i_branches)
    line_covered = _merged_covered_count(
        unit_row, instr_row, covered_key="line_covered", missed_key="missed_lines",
        merged_missed_n=len(merged_lines),
    )
    branch_covered = _merged_covered_count(
        unit_row, instr_row, covered_key="branch_covered", missed_key="missed_branches",
        merged_missed_n=len(merged_branches),
    )
    merged["missed_lines"] = merged_lines
    merged["missed_branches"] = merged_branches
    merged["line_missed"] = len(merged_lines)
    merged["branch_missed"] = len(merged_branches)
    merged["line_covered"] = line_covered
    merged["branch_covered"] = branch_covered
    merged["line_coverage_pct"] = _coverage_pct(line_covered, len(merged_lines))
    merged["branch_coverage_pct"] = _coverage_pct(branch_covered, len(merged_branches))
    return merged


def unified_coverage_snapshot(project_root: Path | str) -> dict[str, dict[str, Any]]:
    """Single source of truth: Kover unit gaps merged with JaCoCo instrumented gaps."""
    kover = kover_snapshot(project_root)
    jacoco = jacoco_snapshot(project_root)
    if not kover and not jacoco:
        return {}
    keys = set(kover) | set(jacoco)
    merged = {
        key: _merge_snapshot_rows(kover.get(key), jacoco.get(key))
        for key in keys
        if _merge_snapshot_rows(kover.get(key), jacoco.get(key)) is not None
    }
    return merged


def _coverage_pct(covered: int, missed: int) -> float | None:
    """Coverage percent, or None when there is nothing to cover (show NA in UI)."""
    return _ratio_pct(covered, missed)

def _ratio_pct(covered: int, missed: int) -> float | None:
    total = int(covered or 0) + int(missed or 0)
    if total <= 0:
        return None
    return round((int(covered or 0) / total) * 100, 1)


def _capped_sum_pct(left, right) -> float | None:
    """Total % when a unit may be covered by either engine, from per-engine percents.

    Kover/JaCoCo expose branch *counts* (no per-branch identity across the two
    reports), so a true union is not computable for branches. We cap the naive sum
    at 100 to keep the dashboard invariant Total <= 100% (a line/branch is never
    more than fully covered).
    """
    if left is None and right is None:
        return None
    return min(100.0, round(float(left or 0) + float(right or 0), 1))


def _metric_union_pct(rows: list[dict[str, Any]], metric: str) -> float | None:
    covered: set[str] = set()
    total: set[str] = set()
    for row in rows:
        for engine in ("kover", "jacoco"):
            elements = row.get(f"{engine}_metric_elements") or {}
            payload = elements.get(metric) or {}
            covered.update(str(value) for value in payload.get("covered") or [])
            total.update(str(value) for value in payload.get("total") or [])
    if not total:
        return None
    return _ratio_pct(len(covered), len(total) - len(covered))


def _union_line_pct(
    kover_covered, kover_missed, jacoco_covered, jacoco_missed,
    kover_pct, jacoco_pct,
) -> float | None:
    """Total line % = lines covered by unitTest OR androidTest, over the union of
    lines either engine instrumented. A line covered by both is counted once, so the
    result is always <= 100% (unlike summing the two per-engine percentages, which
    can double-count the overlap and reach 200%).

    When per-line sets are unavailable (e.g. a single-engine row that only carries
    counts), fall back to the capped sum of the per-engine percentages.
    """
    universe = (
        set(kover_covered or ()) | set(kover_missed or ())
        | set(jacoco_covered or ()) | set(jacoco_missed or ())
    )
    if universe:
        covered = set(kover_covered or ()) | set(jacoco_covered or ())
        return round(len(covered) / len(universe) * 100, 1)
    return _capped_sum_pct(kover_pct, jacoco_pct)

def kover_snapshot(project_root: Path | str) -> dict[str, dict[str, Any]]:
    """Project-wide Kover coverage keyed by module|package|source_name."""
    from UnitTest_gen.kotlin.coverage import load_kover_gaps

    root = Path(project_root)
    reports = discover_kover_xml(root)
    if not reports:
        return {}
    snapshot: dict[str, dict[str, Any]] = {}
    for gap in load_kover_gaps(reports, root, include_fully_covered=True).values():
        row = _snapshot_row_from_gap(gap, coverage_sources=["kover"])
        snapshot[row["key"]] = row
    return snapshot

def build_modules_projection(
    coverage: dict[str, dict[str, Any]] | list[dict[str, Any]],
    *,
    exclude_generated: bool = False,
) -> list[dict[str, Any]]:
    """Roll up per-file coverage rows into module totals.

    Emits separate unitTest (Kover) and androidTest (JaCoCo) counters/pcts for
    class / method / branch / line / instruction. Combined line % is the true
    per-file UT∪AT union summed across files. Other combined metrics use
    ``min(100, kover_pct + jacoco_pct)`` (no cross-engine identity). Legacy
    unprefixed counters remain for deferred/effective line rollups.
    """
    rows = coverage.values() if isinstance(coverage, dict) else coverage
    legacy_keys = (
        "line_covered", "line_missed", "branch_covered", "branch_missed",
        "instruction_covered", "instruction_missed",
        "method_covered", "method_missed",
        "class_covered", "class_missed",
    )
    engine_keys = tuple(
        f"{engine}_{metric}_{side}"
        for engine in ("kover", "jacoco")
        for metric in _ENGINE_METRIC_NAMES
        for side in ("covered", "missed")
    )

    def _engine_count(row: dict[str, Any], engine: str, metric: str, side: str) -> int:
        key = f"{engine}_{metric}_{side}"
        if key in row:
            return int(row.get(key) or 0)
        sources = list(row.get("coverage_sources") or [])
        if sources == [engine] or (engine == "kover" and not sources):
            return int(row.get(f"{metric}_{side}") or 0)
        return 0

    def _row_line_sets(row: dict[str, Any]) -> tuple[set[int], set[int]]:
        """Return ``(covered_lines, universe_lines)`` for one merged file row.

        Uses per-engine line sets plus the top-level ``missed_lines`` /
        ``covered_lines`` (single-source rows keep the real line numbers there).
        Returns empty sets for counter-only rows so the caller falls back to the
        capped-sum percentage rather than inventing colliding line numbers.
        """
        k_covered = set(_sorted_line_nums(row.get("kover_covered_lines") or []))
        j_covered = set(_sorted_line_nums(row.get("jacoco_covered_lines") or []))
        k_missed = set(_sorted_line_nums(row.get("kover_missed_lines") or []))
        j_missed = set(_sorted_line_nums(row.get("jacoco_missed_lines") or []))
        top_covered = set(_sorted_line_nums(row.get("covered_lines") or []))
        top_missed = set(_sorted_line_nums(row.get("missed_lines") or []))
        if not (k_covered or j_covered or k_missed or j_missed or top_covered or top_missed):
            return set(), set()
        covered = k_covered | j_covered | top_covered
        universe = covered | k_missed | j_missed | top_missed
        return covered, universe

    buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "module": "",
            "files": 0,
            **{k: 0 for k in legacy_keys},
            **{k: 0 for k in engine_keys},
            # Sum of per-file union sizes (NOT a set of bare line numbers — those
            # collide across files and under-count the module badly).
            "combined_line_covered": 0,
            "combined_line_total": 0,
            "metric_elements": {
                metric: {"covered": set(), "total": set()}
                for metric in _ENGINE_METRIC_NAMES
            },
        },
    )
    for row in rows:
        if exclude_generated and str(row.get("gap_work_status") or "") == "generated":
            continue
        module = str(row.get("module") or "(unknown)")
        bucket = buckets[module]
        bucket["module"] = module
        bucket["files"] += 1
        for key in legacy_keys:
            bucket[key] += int(row.get(key) or 0)
        for engine in ("kover", "jacoco"):
            for metric in _ENGINE_METRIC_NAMES:
                for side in ("covered", "missed"):
                    bucket[f"{engine}_{metric}_{side}"] += _engine_count(
                        row, engine, metric, side
                    )
        union_covered, union_total = _row_line_sets(row)
        if union_total:
            # Per-file union first, then sum — preserves identity across files.
            bucket["combined_line_covered"] += len(union_covered)
            bucket["combined_line_total"] += len(union_total)
        for engine in ("kover", "jacoco"):
            for metric in _ENGINE_METRIC_NAMES:
                payload = row.get(f"{engine}_metric_elements", {}).get(metric, {})
                bucket["metric_elements"][metric]["covered"].update(
                    str(value) for value in payload.get("covered") or []
                )
                bucket["metric_elements"][metric]["total"].update(
                    str(value) for value in payload.get("total") or []
                )

    projections = []
    for module in sorted(buckets):
        bucket = buckets[module]
        for engine in ("kover", "jacoco"):
            for metric in _ENGINE_METRIC_NAMES:
                covered = bucket[f"{engine}_{metric}_covered"]
                missed = bucket[f"{engine}_{metric}_missed"]
                # No engine hits → — in UI (not 0.0%), same as a missing report
                bucket[f"{engine}_{metric}_pct"] = (
                    None if covered <= 0 else _ratio_pct(covered, missed)
                )
        for metric in _ENGINE_METRIC_NAMES:
            elements = bucket["metric_elements"][metric]
            bucket[f"combined_{metric}_pct"] = (
                _ratio_pct(len(elements["covered"]), len(elements["total"]) - len(elements["covered"]))
                if elements["total"] else _capped_sum_pct(
                    bucket.get(f"kover_{metric}_pct"), bucket.get(f"jacoco_{metric}_pct")
                )
            )
        bucket["metric_elements"] = {
            metric: {
                "covered": sorted(values["covered"]),
                "total": sorted(values["total"]),
            }
            for metric, values in bucket["metric_elements"].items()
        }
        # Line: true per-line union when per-line data exists (covered if Kover
        # OR JaCoCo covered the line on that file). Other metrics stay capped-sum
        # (no cross-engine identity for branch/method/class/instruction).
        if bucket["combined_line_total"] > 0:
            bucket["combined_line_pct"] = _ratio_pct(
                bucket["combined_line_covered"],
                bucket["combined_line_total"] - bucket["combined_line_covered"],
            )
        # Legacy single-column pcts: prefer combined (capped), else whichever engine exists
        bucket["line_coverage_pct"] = (
            bucket["combined_line_pct"]
            if bucket["combined_line_pct"] is not None
            else _coverage_pct(bucket["line_covered"], bucket["line_missed"])
        )
        bucket["branch_coverage_pct"] = (
            bucket["combined_branch_pct"]
            if bucket["combined_branch_pct"] is not None
            else _coverage_pct(bucket["branch_covered"], bucket["branch_missed"])
        )
        bucket["instruction_coverage_pct"] = (
            bucket["combined_instruction_pct"]
            if bucket["combined_instruction_pct"] is not None
            else _coverage_pct(bucket["instruction_covered"], bucket["instruction_missed"])
        )
        bucket["method_coverage_pct"] = (
            bucket["combined_method_pct"]
            if bucket["combined_method_pct"] is not None
            else _coverage_pct(bucket["method_covered"], bucket["method_missed"])
        )
        bucket["class_coverage_pct"] = (
            bucket["combined_class_pct"]
            if bucket["combined_class_pct"] is not None
            else _coverage_pct(bucket["class_covered"], bucket["class_missed"])
        )
        projections.append(bucket)
    return projections

def _deferred_branch_completion(
    *,
    branch_covered: int,
    branch_missed: int,
    deferred_unique_branches: int,
) -> dict[str, Any]:
    covered = max(0, int(branch_covered or 0))
    missed = max(0, int(branch_missed or 0))
    credited = min(max(0, int(deferred_unique_branches or 0)), missed)
    total = covered + missed
    return {
        "deferred_credited_branches": credited,
        "actionable_pending_branches": missed - credited,
        "effective_branch_coverage_pct": (
            round(100.0 * (covered + credited) / total, 1) if total else None
        ),
        "deferred_branch_coverage_pct": (
            round(100.0 * credited / total, 1) if total else None
        ),
    }


def _deferred_line_completion(
    *,
    line_covered: int,
    line_missed: int,
    deferred_unique_lines: int,
) -> dict[str, Any]:
    """Treat deferred unique lines as completed (capped by Kover missed lines)."""
    covered = max(0, int(line_covered or 0))
    missed = max(0, int(line_missed or 0))
    deferred_raw = max(0, int(deferred_unique_lines or 0))
    total = covered + missed
    deferred_credited = min(deferred_raw, missed)
    actionable_pending = missed - deferred_credited
    effective_completed = covered + deferred_credited
    deferred_pct = round(100.0 * deferred_credited / total, 1) if total else None
    effective_pct = round(100.0 * effective_completed / total, 1) if total else None
    return {
        "deferred_credited_lines": deferred_credited,
        "actionable_pending_lines": actionable_pending,
        "effective_completed_lines": effective_completed,
        "deferred_coverage_pct": deferred_pct,
        "effective_line_coverage_pct": effective_pct,
    }

def attach_module_deferred_coverage(
    modules_projection: list[dict[str, Any]],
    unreachable_entries: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Attach deferred/unreachable rollups from ``unreachable_coverage.json`` to each module.

    Matching uses :func:`_normalize_module` so ``:feature:login`` and ``feature/login`` align.
    Modules that appear only in the unreachable store get a stub row (coverage zeros).

    Deferred unique lines are credited as completed for charts (capped by ``line_missed``).
    """
    by_mod: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in unreachable_entries or []:
        if not isinstance(entry, dict):
            continue
        payload = _entry_payload(entry)
        mod = _normalize_module(payload.get("module"))
        if not mod and payload.get("source"):
            # Infer module from absolute path segments when store omits module.
            parts = Path(payload["source"]).parts
            if "feature" in parts:
                i = parts.index("feature")
                if i + 1 < len(parts):
                    mod = f"feature/{parts[i + 1]}"
            elif "common" in parts:
                i = parts.index("common")
                if i + 1 < len(parts):
                    mod = f"common/{parts[i + 1]}"
            elif "core" in parts:
                mod = "core"
        if not mod:
            continue
        payload = dict(payload)
        payload["module"] = mod
        by_mod[mod].append(payload)

    def _rollup(entries: list[dict[str, Any]], *, line_covered: int, line_missed: int, branch_covered: int, branch_missed: int) -> dict[str, Any]:
        lines: set[int] = set()
        branches: set[int] = set()
        files: set[str] = set()
        categories: dict[str, int] = defaultdict(int)
        for e in entries:
            lines |= set(e.get("lines") or [])
            branches |= set(e.get("branches") or [])
            name = str(e.get("source_name") or "").strip()
            if name:
                files.add(name)
            cat = str(e.get("category") or "deferred").strip() or "deferred"
            categories[cat] += 1
        base = {
            "deferred_entries": entries,
            "deferred_entries_count": len(entries),
            "deferred_unique_lines_count": len(lines),
            "deferred_unique_branches_count": len(branches),
            "deferred_files_count": len(files),
            "deferred_lines_text": compact_ranges(sorted(lines)) if lines else "",
            "deferred_branches_text": compact_ranges(sorted(branches)) if branches else "",
            "deferred_categories": dict(sorted(categories.items())),
        }
        base.update(
            _deferred_line_completion(
                line_covered=line_covered,
                line_missed=line_missed,
                deferred_unique_lines=len(lines),
            )
        )
        base.update(_deferred_branch_completion(
            branch_covered=branch_covered,
            branch_missed=branch_missed,
            deferred_unique_branches=len(branches),
        ))
        return base

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in modules_projection:
        mod = _normalize_module(row.get("module")) or str(row.get("module") or "(unknown)")
        seen.add(mod)
        # Effective is credited over the true per-line union base when present.
        union_total = int(row.get("combined_line_total") or 0)
        if union_total > 0:
            eff_covered = int(row.get("combined_line_covered") or 0)
            eff_missed = union_total - eff_covered
        else:
            eff_covered = int(row.get("line_covered") or 0)
            eff_missed = int(row.get("line_missed") or 0)
        enriched = dict(row)
        enriched.update(
            _rollup(
                by_mod.get(mod, []),
                line_covered=eff_covered,
                line_missed=eff_missed,
                branch_covered=int(row.get("branch_covered") or 0),
                branch_missed=int(row.get("branch_missed") or 0),
            )
        )
        # Prefer deferred-credited effective % only when the metric has a non-empty base.
        # Empty branch/line totals stay None (dashboard NA), not 0.0%.
        if enriched.get("effective_line_coverage_pct") is not None:
            enriched["combined_line_pct"] = enriched["effective_line_coverage_pct"]
        if enriched.get("effective_branch_coverage_pct") is not None:
            enriched["combined_branch_pct"] = enriched["effective_branch_coverage_pct"]
        if enriched.get("branch_covered", 0) == 0 and enriched.get("branch_missed", 0) == 0:
            enriched["combined_branch_pct"] = None
            enriched["branch_coverage_pct"] = None
        out.append(enriched)

    for mod in sorted(by_mod):
        if mod in seen:
            continue
        stub = {
            "module": mod,
            "files": 0,
            "line_covered": 0,
            "line_missed": 0,
            "branch_covered": 0,
            "branch_missed": 0,
            "instruction_covered": 0,
            "instruction_missed": 0,
            "method_covered": 0,
            "method_missed": 0,
            "class_covered": 0,
            "class_missed": 0,
            "line_coverage_pct": None,
            "branch_coverage_pct": None,
            "instruction_coverage_pct": None,
            "method_coverage_pct": None,
            "class_coverage_pct": None,
            "combined_line_pct": None,
            "combined_branch_pct": None,
        }
        stub.update(
            _rollup(
                by_mod[mod],
                line_covered=0,
                line_missed=0,
                branch_covered=0,
                branch_missed=0,
            )
        )
        out.append(stub)
    return out

def _line_map_pct(missed_lines, max_line: int | None = None) -> list[float]:
    numbers = sorted({int(n) for n in (missed_lines or []) if str(n).isdigit() or isinstance(n, int)})
    if not numbers:
        return []
    ceiling = max(max_line or 0, numbers[-1], 1)
    return [round((n / ceiling) * 100, 2) for n in numbers]

def build_files_projection(coverage: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for row in coverage.values():
        missed_lines = list(row.get("missed_lines") or [])
        missed_branches = list(row.get("missed_branches") or [])
        line_covered = int(row.get("line_covered") or 0)
        line_missed = int(row.get("line_missed") or 0)
        branch_covered = int(row.get("branch_covered") or 0)
        branch_missed = int(row.get("branch_missed") or 0)
        instruction_covered = int(row.get("instruction_covered") or 0)
        instruction_missed = int(row.get("instruction_missed") or 0)
        method_covered = int(row.get("method_covered") or 0)
        method_missed = int(row.get("method_missed") or 0)
        class_covered = int(row.get("class_covered") or 0)
        class_missed = int(row.get("class_missed") or 0)
        sources = list(row.get("coverage_sources") or ["kover"])
        kover_in = "kover" in sources
        jacoco_in = "jacoco" in sources
        legacy_by_metric = {
            "line": (line_covered, line_missed),
            "branch": (branch_covered, branch_missed),
            "instruction": (instruction_covered, instruction_missed),
            "method": (method_covered, method_missed),
            "class": (class_covered, class_missed),
        }
        engine_counters: dict[str, int] = {}
        engine_pcts: dict[str, float | None] = {}
        for engine, in_engine in (("kover", kover_in), ("jacoco", jacoco_in)):
            for metric in _ENGINE_METRIC_NAMES:
                leg_c, leg_m = legacy_by_metric[metric]
                c_key = f"{engine}_{metric}_covered"
                m_key = f"{engine}_{metric}_missed"
                if c_key in row:
                    covered = int(row.get(c_key) or 0)
                else:
                    covered = leg_c if in_engine and (
                        engine == "kover" or not kover_in
                    ) else 0
                if m_key in row:
                    missed = int(row.get(m_key) or 0)
                elif metric == "line" and engine == "kover":
                    missed = len(row.get("kover_missed_lines") or []) or (
                        leg_m if in_engine else 0
                    )
                elif metric == "line" and engine == "jacoco":
                    missed = len(row.get("jacoco_missed_lines") or []) or (
                        leg_m if in_engine and not kover_in else 0
                    )
                elif metric == "branch" and engine == "kover":
                    missed = len(row.get("kover_missed_branches") or []) or (
                        leg_m if in_engine else 0
                    )
                elif metric == "branch" and engine == "jacoco":
                    missed = len(row.get("jacoco_missed_branches") or []) or (
                        leg_m if in_engine and not kover_in else 0
                    )
                else:
                    missed = leg_m if (
                        in_engine and (engine == "kover" or not kover_in) and m_key not in row
                    ) else int(row.get(m_key) or 0)
                engine_counters[c_key] = covered
                engine_counters[m_key] = missed
                pct_key = f"{engine}_{metric}_pct"
                if covered <= 0 or not in_engine:
                    engine_pcts[pct_key] = None
                elif pct_key in row and row[pct_key] is not None:
                    engine_pcts[pct_key] = row[pct_key]
                else:
                    engine_pcts[pct_key] = _ratio_pct(covered, missed)
        kover_line_covered = engine_counters["kover_line_covered"]
        kover_line_missed = engine_counters["kover_line_missed"]
        kover_branch_covered = engine_counters["kover_branch_covered"]
        kover_branch_missed = engine_counters["kover_branch_missed"]
        jacoco_line_covered = engine_counters["jacoco_line_covered"]
        jacoco_line_missed = engine_counters["jacoco_line_missed"]
        jacoco_branch_covered = engine_counters["jacoco_branch_covered"]
        jacoco_branch_missed = engine_counters["jacoco_branch_missed"]
        kover_line_pct = engine_pcts["kover_line_pct"]
        kover_branch_pct = engine_pcts["kover_branch_pct"]
        jacoco_line_pct = engine_pcts["jacoco_line_pct"]
        jacoco_branch_pct = engine_pcts["jacoco_branch_pct"]
        kover_missed_lines = list(row.get("kover_missed_lines") or [])
        jacoco_zero_hits = bool(row.get("jacoco_zero_hits"))
        jacoco_missed_lines = [] if jacoco_zero_hits else list(row.get("jacoco_missed_lines") or [])
        if "kover_covered_lines" in row:
            kover_covered_lines = _sorted_line_nums(row.get("kover_covered_lines"))
        else:
            kover_covered_lines = _sorted_line_nums(row.get("covered_lines") if kover_in else [])
        jacoco_covered_lines = [] if jacoco_zero_hits else _sorted_line_nums(row.get("jacoco_covered_lines"))
        covered_lines = _sorted_line_nums(row.get("covered_lines")) or sorted(
            set(kover_covered_lines) | set(jacoco_covered_lines)
        )
        both_covered_lines = sorted(set(kover_covered_lines) | set(jacoco_covered_lines))
        uncovered_lines = sorted(
            (set(kover_missed_lines) | set(jacoco_missed_lines))
            - set(both_covered_lines)
        )
        # Per-engine missed-line sets for the total-union computation. In the merged
        # (both engines) path the per-engine lists are populated; in a single-engine
        # row that engine's missed lines live in row["missed_lines"].
        if kover_in and jacoco_in and not jacoco_zero_hits:
            kover_missed_union = set(kover_missed_lines)
            jacoco_missed_union = set(jacoco_missed_lines)
        else:
            _sole_missed = set(row.get("missed_lines") or [])
            kover_missed_union = _sole_missed if kover_in else set()
            jacoco_missed_union = set()
        metric_elements = {
            engine: {
                metric: {
                    "covered": sorted(
                        str(value) for value in ((row.get(f"{engine}_metric_elements") or {}).get(metric, {}) or {}).get("covered") or []
                    ),
                    "total": sorted(
                        str(value) for value in ((row.get(f"{engine}_metric_elements") or {}).get(metric, {}) or {}).get("total") or []
                    ),
                }
                for metric in _ENGINE_METRIC_NAMES
            }
            for engine in ("kover", "jacoco")
        }
        if jacoco_zero_hits:
            metric_elements["jacoco"] = {
                metric: {"covered": [], "total": []}
                for metric in _ENGINE_METRIC_NAMES
            }
        combined_metric_pcts = {
            metric: _metric_union_pct(
                [{f"{engine}_metric_elements": metric_elements[engine] for engine in ("kover", "jacoco")}],
                metric,
            )
            for metric in _ENGINE_METRIC_NAMES
        }
        for metric in _ENGINE_METRIC_NAMES:
            if combined_metric_pcts[metric] is None:
                combined_metric_pcts[metric] = _capped_sum_pct(
                    engine_pcts.get(f"kover_{metric}_pct"), engine_pcts.get(f"jacoco_{metric}_pct")
                )
        rows.append(
            {
                "key": row.get("key") or "",
                "module": row.get("module") or "",
                "package": row.get("package") or "",
                "source_name": row.get("source_name") or "",
                "line_covered": line_covered,
                "line_missed": line_missed,
                "branch_covered": branch_covered,
                "branch_missed": branch_missed,
                "instruction_covered": instruction_covered,
                "instruction_missed": instruction_missed,
                "method_covered": method_covered,
                "method_missed": method_missed,
                "class_covered": class_covered,
                "class_missed": class_missed,
                "line_coverage_pct": float(row.get("line_coverage_pct") or 0.0),
                "branch_coverage_pct": _coverage_pct(branch_covered, branch_missed),
                "instruction_coverage_pct": _coverage_pct(instruction_covered, instruction_missed),
                "method_coverage_pct": _coverage_pct(method_covered, method_missed),
                "class_coverage_pct": _coverage_pct(class_covered, class_missed),
                "pending_lines": compact_ranges(missed_lines),
                "pending_branches": compact_ranges(missed_branches),
                "missed_lines": missed_lines,
                "missed_branches": missed_branches,
                "line_map": _line_map_pct(missed_lines),
                "branch_map": _line_map_pct(missed_branches),
                "coverage_sources": list(row.get("coverage_sources") or ["kover"]),
                "kover_metric_elements": metric_elements["kover"],
                "jacoco_metric_elements": metric_elements["jacoco"],
                "kover_missed_lines": kover_missed_lines,
                "jacoco_missed_lines": jacoco_missed_lines,
                "kover_missed_branches": list(row.get("kover_missed_branches") or []),
                "jacoco_missed_branches": list(row.get("jacoco_missed_branches") or []),
                **engine_counters,
                **engine_pcts,
                "jacoco_zero_hits": bool(row.get("jacoco_zero_hits")),
                "total_line_pct": (
                    kover_line_pct
                    if jacoco_zero_hits
                    else _union_line_pct(
                        kover_covered_lines, kover_missed_union,
                        jacoco_covered_lines, jacoco_missed_union,
                        kover_line_pct, jacoco_line_pct,
                    )
                ),
                "total_branch_pct": combined_metric_pcts["branch"],
                "combined_branch_pct": combined_metric_pcts["branch"],
                "combined_class_pct": combined_metric_pcts["class"],
                "combined_method_pct": combined_metric_pcts["method"],
                "combined_instruction_pct": combined_metric_pcts["instruction"],
                "kover_covered_lines": kover_covered_lines,
                "jacoco_covered_lines": jacoco_covered_lines,
                "kover_covered_lines_text": compact_ranges(kover_covered_lines),
                "jacoco_covered_lines_text": compact_ranges(jacoco_covered_lines),
                "kover_missed_lines_text": compact_ranges(kover_missed_lines),
                "jacoco_missed_lines_text": compact_ranges(jacoco_missed_lines),
                "both_covered_lines": both_covered_lines,
                "both_covered_lines_text": compact_ranges(both_covered_lines),
                "uncovered_lines": uncovered_lines,
                "uncovered_lines_text": compact_ranges(uncovered_lines),
                "covered_lines": covered_lines,
                "covered_lines_text": compact_ranges(covered_lines),
            }
        )
    rows.sort(
        key=lambda r: (
            r["total_line_pct"] if r.get("total_line_pct") is not None else -1.0,
            r["total_branch_pct"] if r.get("total_branch_pct") is not None else -1.0,
            r["module"],
            r["source_name"],
        )
    )
    return rows

def resolve_kover_source_path(project_root: Path | str, row: dict[str, Any]) -> Path | None:
    """Map a Kover projection row to an on-disk src/main .kt file, if present."""
    root = Path(project_root)
    module = str(row.get("module") or "").strip().strip("/")
    package = str(row.get("package") or "").strip().strip("/")
    source_name = str(row.get("source_name") or "").strip()
    if not module or not source_name:
        return None
    package_parts = Path(*package.split("/")) if package else Path()
    for src_root in ("src/main/java", "src/main/kotlin"):
        candidate = root / module / src_root / package_parts / source_name
        if candidate.is_file():
            return candidate.resolve()
    return None


def is_non_executable_kotlin_source(source_path: Path | str | None) -> bool:
    """True for Kotlin interface-only / Hilt ``@Binds`` module files (no executable lines).

    Coverage tools still list these classes (often under androidTest/JaCoCo) with
    null line/branch %, which looks like pending work. UnitTest_gen must not
    generate tests for them — cover ``*Impl`` / consumers instead.
    """
    if source_path is None:
        return False
    path = Path(source_path)
    if not path.is_file() or path.suffix != ".kt":
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    lines: list[str] = []
    for raw in text.splitlines():
        stripped = raw.split("//", 1)[0].strip()
        if stripped:
            lines.append(stripped)
    body = "\n".join(lines)
    if not re.search(r"\binterface\b", body):
        return False
    if re.search(r"\b(object|enum\s+class)\b", body):
        return False
    # Any non-empty function body ⇒ executable (default interface methods, etc.)
    for match in re.finditer(r"\bfun\b[^{;\n]*\{([^}]*)\}", body, flags=re.DOTALL):
        if match.group(1).strip():
            return False
    return True


def row_has_no_executable_counters(row: dict[str, Any]) -> bool:
    """True when coverage XML lists the class but records zero line/instruction probes."""
    line_total = int(row.get("line_covered") or 0) + int(row.get("line_missed") or 0)
    instr_total = int(row.get("instruction_covered") or 0) + int(
        row.get("instruction_missed") or 0
    )
    branch_total = int(row.get("branch_covered") or 0) + int(row.get("branch_missed") or 0)
    return line_total == 0 and instr_total == 0 and branch_total == 0


def derive_gap_work_status(
    *,
    line_missed: int,
    branch_missed: int,
    actionable_lines_count: int,
    actionable_branches_count: int,
    source_resolvable: bool,
    non_executable: bool = False,
) -> str:
    """Orchestrator-aligned work status for dashboard chips.

    Returns one of: covered, actionable, deferred_only, generated, no_exec.
    """
    if non_executable and line_missed <= 0 and branch_missed <= 0:
        return "no_exec"
    raw_pending = line_missed > 0 or branch_missed > 0
    if not raw_pending:
        return "covered"
    if not source_resolvable:
        return "generated"
    if actionable_lines_count > 0 or actionable_branches_count > 0:
        return "actionable"
    return "deferred_only"


def enrich_file_gap_status(
    project_root: Path | str,
    row: dict[str, Any],
    *,
    unreachable_entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Attach actionable counts and gap_work_status to a files_projection row."""
    enriched = dict(row)
    source_path = resolve_kover_source_path(project_root, row)
    enriched["source_resolvable"] = source_path is not None
    if source_path is not None:
        enriched["source_path"] = str(source_path)

    matched = list(unreachable_entries or enriched.get("unreachable_entries") or [])
    derived = derive_coverage_status(
        accepted=True,
        outcome=None,
        missed_lines=list(row.get("missed_lines") or []),
        missed_branches=list(row.get("missed_branches") or []),
        unreachable_entries=matched,
    )
    actionable_lines = list(derived.get("actionable_missed_lines") or [])
    actionable_branches = list(derived.get("actionable_missed_branches") or [])
    enriched["actionable_missed_lines"] = actionable_lines
    enriched["actionable_missed_branches"] = actionable_branches
    enriched["actionable_missed_lines_count"] = int(derived.get("actionable_missed_lines_count") or 0)
    enriched["actionable_missed_branches_count"] = int(
        derived.get("actionable_missed_branches_count") or 0
    )
    enriched["actionable_pending_lines"] = compact_ranges(actionable_lines)
    enriched["actionable_pending_branches"] = compact_ranges(actionable_branches)
    non_executable = bool(
        source_path is not None
        and row_has_no_executable_counters(enriched)
        and is_non_executable_kotlin_source(source_path)
    )
    enriched["non_executable"] = non_executable
    enriched["gap_work_status"] = derive_gap_work_status(
        line_missed=int(row.get("line_missed") or 0),
        branch_missed=int(row.get("branch_missed") or 0),
        actionable_lines_count=enriched["actionable_missed_lines_count"],
        actionable_branches_count=enriched["actionable_missed_branches_count"],
        source_resolvable=enriched["source_resolvable"],
        non_executable=non_executable,
    )
    return enriched


def keep_src_main_projection(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop generated/build-output rows that are not under src/main."""
    return [row for row in rows if row.get("source_resolvable")]


def summarize_gap_work_status(files_projection: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "covered": 0,
        "actionable": 0,
        "deferred_only": 0,
        "generated": 0,
        "no_exec": 0,
    }
    for row in files_projection:
        status = str(row.get("gap_work_status") or "covered")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _kover_totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    line_covered = sum(int(row.get("line_covered") or 0) for row in rows)
    line_missed = sum(int(row.get("line_missed") or 0) for row in rows)
    branch_covered = sum(int(row.get("branch_covered") or 0) for row in rows)
    branch_missed = sum(int(row.get("branch_missed") or 0) for row in rows)
    return {
        "files_in_kover": len(rows),
        "line_covered": line_covered,
        "line_missed": line_missed,
        "branch_covered": branch_covered,
        "branch_missed": branch_missed,
        "line_coverage_pct": _coverage_pct(line_covered, line_missed),
        "branch_coverage_pct": _coverage_pct(branch_covered, branch_missed),
    }

def _normalize_module(module: str | None) -> str:
    raw = str(module or "").strip()
    if not raw:
        return ""
    # ":feature:recordtrip" and "feature/recordtrip" → "feature/recordtrip"
    if raw.startswith(":"):
        raw = raw[1:]
    return raw.replace(":", "/").strip("/")

def _entry_payload(entry: dict[str, Any]) -> dict[str, Any]:
    from UnitTest_gen.kotlin.coverage import entry_lines_and_branches

    lines_set, branches_set = entry_lines_and_branches(entry)
    source = str(entry.get("source") or "")
    return {
        "source": source,
        "source_name": str(entry.get("source_name") or (Path(source).name if source else "")),
        "module": str(entry.get("module") or ""),
        "category": str(entry.get("category") or ""),
        "reason": str(entry.get("reason") or ""),
        "methods": [str(m) for m in (entry.get("methods") or [])],
        "lines": sorted(lines_set),
        "branches": sorted(branches_set),
        "evidence": str(entry.get("evidence") or ""),
        "recorded_at": str(entry.get("recorded_at") or ""),
    }

def match_unreachable_entries(
    entries: list[dict[str, Any]],
    *,
    source: str | None = None,
    source_name: str | None = None,
    module: str | None = None,
) -> list[dict[str, Any]]:
    """Match unreachable store rows by absolute source path, else module+name."""
    abs_source = os.path.abspath(source) if source else ""
    name = (source_name or (Path(source).name if source else "") or "").strip()
    mod = _normalize_module(module)
    matched: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[int, ...], tuple[int, ...]]] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_source = os.path.abspath(str(entry.get("source") or "")) if entry.get("source") else ""
        entry_name = str(entry.get("source_name") or "").strip()
        if not entry_name and entry_source:
            entry_name = Path(entry_source).name
        entry_mod = _normalize_module(entry.get("module"))
        by_path = bool(abs_source and entry_source and abs_source == entry_source)
        by_name = bool(
            name
            and entry_name
            and name == entry_name
            and (not mod or not entry_mod or mod == entry_mod)
        )
        if not (by_path or by_name):
            continue
        payload = _entry_payload(entry)
        key = (
            payload["source"] or payload["source_name"],
            tuple(payload["lines"]),
            tuple(payload.get("branches") or []),
        )
        if key in seen:
            continue
        seen.add(key)
        matched.append(payload)
    return matched

def deferred_line_set(unreachable_entries: list[dict[str, Any]] | None) -> set[int]:
    """Deferred missed-line probes only (not partial-branch numbers)."""
    lines, _branches = deferred_probe_sets(unreachable_entries)
    return lines


def deferred_probe_sets(
    unreachable_entries: list[dict[str, Any]] | None,
) -> tuple[set[int], set[int]]:
    from UnitTest_gen.kotlin.coverage import entry_lines_and_branches

    lines: set[int] = set()
    branches: set[int] = set()
    for entry in unreachable_entries or []:
        entry_lines, entry_branches = entry_lines_and_branches(entry)
        lines |= entry_lines
        branches |= entry_branches
    return lines, branches

def derive_coverage_status(
    *,
    accepted: bool,
    outcome: str | None,
    missed_lines=None,
    missed_branches=None,
    unreachable_entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Derive dashboard status from residual gaps after deferred lines/branches.

    Returns status in {{completed, inprogress, untouched, rejected}} plus actionable counts.
    """
    outcome_key = str(outcome or "").strip().lower()
    deferred_lines, deferred_branches = deferred_probe_sets(unreachable_entries)
    missed_line_set = coerce_int_set(missed_lines)
    missed_branch_set = coerce_int_set(missed_branches)
    actionable_lines = sorted(missed_line_set - deferred_lines)
    actionable_branches = sorted(missed_branch_set - deferred_branches)

    if outcome_key == "untouched":
        status = "untouched"
    elif not accepted:
        status = "rejected"
    elif actionable_lines or actionable_branches:
        status = "inprogress"
    else:
        status = "completed"

    return {
        "status": status,
        "actionable_missed_lines": actionable_lines,
        "actionable_missed_branches": actionable_branches,
        "actionable_missed_lines_count": len(actionable_lines),
        "actionable_missed_branches_count": len(actionable_branches),
    }

def build_diagnostics(
    project_root: Path | str,
    *,
    store_path: Path | str = STORE_PATH,
    unreachable_store_path: Path | str | None = None,
) -> dict[str, Any]:
    """Assemble the unified coverage dashboard model from local inputs."""
    from UnitTest_gen.kotlin.coverage import load_store as load_unreachable_store
    from UnitTest_gen.kotlin.coverage import load_suspected_bugs

    root = Path(project_root).resolve()
    store = load_store(store_path)
    kover_rows = kover_snapshot(root)
    jacoco_rows = jacoco_snapshot(root)
    coverage = unified_coverage_snapshot(root) or kover_rows
    files_projection = build_files_projection(coverage)

    unreachable_payload = load_unreachable_store(unreachable_store_path)
    unreachable_entries = [
        entry for entry in (unreachable_payload.get("entries") or []) if isinstance(entry, dict)
    ]
    suspected_bugs = [
        entry for entry in (load_suspected_bugs().get("entries") or []) if isinstance(entry, dict)
    ]

    by_name = {row.get("source_name"): row for row in coverage.values()}
    kover_by_name = {row.get("source_name"): row for row in kover_rows.values()}
    jacoco_by_name = {row.get("source_name"): row for row in jacoco_rows.values()}
    records: list[dict[str, Any]] = []
    for record in store.get("sources", {}).values():
        merged = dict(record)
        name = record.get("source_name", "")
        merged["coverage"] = by_name.get(name)
        merged["kover"] = kover_by_name.get(name)
        merged["jacoco"] = jacoco_by_name.get(name)
        merged["unreachable_entries"] = match_unreachable_entries(
            unreachable_entries,
            source=str(record.get("source") or ""),
            source_name=str(record.get("source_name") or ""),
            module=str(record.get("module") or ""),
        )
        records.append(merged)
    records.sort(key=lambda row: (not row.get("accepted", False), row.get("source_name", "")))

    for row in files_projection:
        row["unreachable_entries"] = match_unreachable_entries(
            unreachable_entries,
            source_name=str(row.get("source_name") or ""),
            module=str(row.get("module") or ""),
        )

    files_projection = [
        enrich_file_gap_status(root, row, unreachable_entries=row.get("unreachable_entries") or [])
        for row in files_projection
    ]
    files_projection = keep_src_main_projection(files_projection)
    gap_work_counts = summarize_gap_work_status(files_projection)

    kover_totals = _kover_totals(files_projection)
    modules_projection = attach_module_deferred_coverage(
        build_modules_projection(files_projection, exclude_generated=True),
        unreachable_entries,
    )
    deferred_credited = sum(int(m.get("deferred_credited_lines") or 0) for m in modules_projection)
    deferred_branches_credited = sum(int(m.get("deferred_credited_branches") or 0) for m in modules_projection)
    deferred_branches_credited = sum(int(m.get("deferred_credited_branches") or 0) for m in modules_projection)
    actionable_pending_lines = sum(int(m.get("actionable_pending_lines") or 0) for m in modules_projection)
    line_covered = int(kover_totals.get("line_covered") or 0)
    line_missed = int(kover_totals.get("line_missed") or 0)
    # Effective is credited over the true per-line union base (sum of module
    # combined_line_covered / combined_line_total) when present.
    union_covered = sum(int(m.get("combined_line_covered") or 0) for m in modules_projection)
    union_total = sum(int(m.get("combined_line_total") or 0) for m in modules_projection)
    if union_total > 0:
        union_missed = union_total - union_covered
    else:
        union_covered, union_missed = line_covered, line_missed
    # Prefer module-sum actionable when available; fall back to raw missed minus credited.
    if modules_projection:
        line_actionable = actionable_pending_lines
        # Align credited with union totals when module rollup under/over counts.
        deferred_credited = min(deferred_credited, union_missed)
        line_actionable = max(0, union_missed - deferred_credited)
    else:
        line_actionable = union_missed
        deferred_credited = 0
    unified_metric_totals: dict[str, dict[str, int | float | None]] = {}
    for metric in ("class", "method", "branch", "instruction"):
        covered_elements: set[str] = set()
        total_elements: set[str] = set()
        for module in modules_projection:
            payload = (module.get("metric_elements") or {}).get(metric) or {}
            covered_elements.update(str(value) for value in payload.get("covered") or [])
            total_elements.update(str(value) for value in payload.get("total") or [])
        covered_count = len(covered_elements)
        total_count = len(total_elements)
        unified_metric_totals[metric] = {
            "covered": covered_count,
            "missed": max(0, total_count - covered_count),
            "pct": round(100.0 * covered_count / total_count, 1) if total_count else None,
        }
    total_lines = union_covered + union_missed
    deferred_coverage_pct = round(100.0 * deferred_credited / total_lines, 1) if total_lines else 0.0
    branch_total = sum(
        int(m.get("branch_covered") or 0) + int(m.get("branch_missed") or 0)
        for m in modules_projection
    )
    branch_covered_effective = sum(
        int(m.get("branch_covered") or 0) + int(m.get("deferred_credited_branches") or 0)
        for m in modules_projection
    )
    branch_coverage_effective_pct = (
        round(100.0 * branch_covered_effective / branch_total, 1) if branch_total else 0.0
    )
    branch_total = sum(int(m.get("branch_covered") or 0) + int(m.get("branch_missed") or 0) for m in modules_projection)
    branch_covered_effective = sum(int(m.get("branch_covered") or 0) + int(m.get("deferred_credited_branches") or 0) for m in modules_projection)
    branch_coverage_effective_pct = round(100.0 * branch_covered_effective / branch_total, 1) if branch_total else 0.0
    effective_line_coverage_pct = (
        round(100.0 * (union_covered + deferred_credited) / total_lines, 1) if total_lines else 0.0
    )
    # Overview "Line coverage (unified)" must use the same UT∪AT base as Eff%.
    if union_total > 0:
        kover_totals = dict(kover_totals)
        kover_totals["line_coverage_pct"] = (
            effective_line_coverage_pct
        )
        kover_totals["line_covered_unified"] = union_covered + deferred_credited
        kover_totals["line_missed_unified"] = max(0, union_missed - deferred_credited)

    processed_names = {record.get("source_name") for record in records}
    untouched = [
        entry
        for entry in files_projection
        if entry.get("source_name") not in processed_names
        and (int(entry.get("line_missed") or 0) > 0 or int(entry.get("branch_missed") or 0) > 0)
    ]

    accepted = sum(1 for record in records if record.get("accepted"))
    rejected = sum(1 for record in records if not record.get("accepted"))
    improved = sum(1 for record in records if record.get("kover_improved"))
    modules = sorted({row["module"] for row in modules_projection if row.get("module")})
    files_with_unreachable = {
        (
            str(entry.get("source") or ""),
            str(entry.get("source_name") or ""),
            _normalize_module(entry.get("module")),
        )
        for entry in unreachable_entries
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "project_root": str(root),
        "summary": {
            "sources_processed": len(records),
            "accepted": accepted,
            "rejected": rejected,
            "kover_improved": improved,
            "acceptance_rate": round(accepted / len(records) * 100, 1) if records else 0.0,
            "kover_reports_found": len(discover_kover_xml(root)),
            "jacoco_reports_found": len(discover_jacoco_xml(root)),
            "coverage_source": "unified" if jacoco_rows else "kover",
            "sources_with_open_gaps": sum(
                1 for row in files_projection if int(row.get("line_missed") or 0) > 0
            ),
            "unreachable_entries_count": len(unreachable_entries),
            "suspected_bugs_count": len(suspected_bugs),
            "files_with_unreachable": len(files_with_unreachable),
            "files_fully_covered": gap_work_counts.get("covered", 0),
            "files_actionable": gap_work_counts.get("actionable", 0),
            "files_deferred_only": gap_work_counts.get("deferred_only", 0),
            "files_generated_pending": gap_work_counts.get("generated", 0),
            "files_no_exec": gap_work_counts.get("no_exec", 0),
            "modules": modules,
            **kover_totals,
            "line_deferred_credited": deferred_credited,
            "line_actionable_pending": line_actionable,
            "deferred_coverage_pct": deferred_coverage_pct,
            "effective_line_coverage_pct": effective_line_coverage_pct,
            "branch_coverage_pct": branch_coverage_effective_pct,
            "branch_covered_effective": branch_covered_effective,
            "branch_missed_effective": max(0, branch_total - branch_covered_effective),
            "branch_deferred_credited": deferred_branches_credited,
                        "line_deferred_credited": deferred_credited,
                        "branch_deferred_credited": deferred_branches_credited,
                        "branch_coverage_pct": branch_coverage_effective_pct,
                        "branch_covered_effective": branch_covered_effective,
                        "branch_missed_effective": max(0, branch_total - branch_covered_effective),
            "unified_class_covered": unified_metric_totals["class"]["covered"],
            "unified_class_missed": unified_metric_totals["class"]["missed"],
            "unified_class_coverage_pct": unified_metric_totals["class"]["pct"],
            "unified_method_covered": unified_metric_totals["method"]["covered"],
            "unified_method_missed": unified_metric_totals["method"]["missed"],
            "unified_method_coverage_pct": unified_metric_totals["method"]["pct"],
            "unified_branch_covered": unified_metric_totals["branch"]["covered"],
            "unified_branch_missed": unified_metric_totals["branch"]["missed"],
            "unified_branch_coverage_pct": unified_metric_totals["branch"]["pct"],
            "unified_instruction_covered": unified_metric_totals["instruction"]["covered"],
            "unified_instruction_missed": unified_metric_totals["instruction"]["missed"],
            "unified_instruction_coverage_pct": unified_metric_totals["instruction"]["pct"],
        },
        "kover_reports": [str(path) for path in discover_kover_xml(root)],
        "jacoco_reports": [str(path) for path in discover_jacoco_xml(root)],
        "modules_projection": modules_projection,
        "files_projection": files_projection,
        "sources": records,
        "untouched_gaps": untouched,
        "unreachable_entries": [_entry_payload(e) for e in unreachable_entries],
        "suspected_bugs": suspected_bugs,
    }

def write_diagnostics(
    project_root: Path | str,
    *,
    path: Path | str = DIAGNOSTICS_PATH,
    store_path: Path | str = STORE_PATH,
    unreachable_store_path: Path | str | None = None,
) -> Path:
    diagnostics = build_diagnostics(
        project_root,
        store_path=store_path,
        unreachable_store_path=unreachable_store_path,
    )
    written = _write_json(Path(path), diagnostics)
    log_message(f"🧭 Diagnostics model written to {written}", category="info")
    return written
