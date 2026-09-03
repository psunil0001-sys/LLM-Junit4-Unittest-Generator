"""Kover/JaCoCo diagnostics model and diagnostics.json store."""

from __future__ import annotations

import os
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
    line_missed = len(gap.missed_lines)
    branch_missed = len(gap.missed_branches)
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
        "line_coverage_pct": _coverage_pct(gap.line_covered, line_missed),
        "branch_coverage_pct": _coverage_pct(gap.branch_covered, branch_missed),
        "instruction_coverage_pct": _coverage_pct(instruction_covered, instruction_missed),
        "method_coverage_pct": _coverage_pct(method_covered, method_missed),
        "class_coverage_pct": _coverage_pct(class_covered, class_missed),
        "coverage_sources": list(coverage_sources or ["kover"]),
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

def _merge_snapshot_rows(
    unit_row: dict[str, Any] | None,
    instr_row: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Merge Kover + JaCoCo dashboard rows using the same gap rule as the pipeline."""
    if unit_row is None and instr_row is None:
        return None
    if instr_row is None:
        return dict(unit_row) if unit_row else None
    if unit_row is None:
        return dict(instr_row)

    u_lines = set(unit_row.get("missed_lines") or [])
    i_lines = set(instr_row.get("missed_lines") or [])
    u_branches = set(unit_row.get("missed_branches") or [])
    i_branches = set(instr_row.get("missed_branches") or [])
    merged_lines = merge_missed_line_sets(u_lines, i_lines)
    merged_branches = merge_missed_line_sets(u_branches, i_branches)

    merged = dict(unit_row)
    merged["missed_lines"] = merged_lines
    merged["missed_branches"] = merged_branches
    merged["line_missed"] = len(merged_lines)
    merged["branch_missed"] = len(merged_branches)
    merged["line_coverage_pct"] = _coverage_pct(
        int(unit_row.get("line_covered") or 0), len(merged_lines),
    )
    merged["branch_coverage_pct"] = _coverage_pct(
        int(unit_row.get("branch_covered") or 0), len(merged_branches),
    )
    sources: list[str] = []
    if unit_row:
        sources.append("kover")
    if instr_row:
        sources.append("jacoco")
    merged["coverage_sources"] = sources
    merged["kover_missed_lines"] = sorted(u_lines)
    merged["jacoco_missed_lines"] = sorted(i_lines)
    return merged

def unified_coverage_snapshot(project_root: Path | str) -> dict[str, dict[str, Any]]:
    """Single source of truth: Kover unit gaps merged with JaCoCo instrumented gaps."""
    kover = kover_snapshot(project_root)
    jacoco = jacoco_snapshot(project_root)
    if not kover and not jacoco:
        return {}
    keys = set(kover) | set(jacoco)
    return {
        key: _merge_snapshot_rows(kover.get(key), jacoco.get(key))
        for key in keys
        if _merge_snapshot_rows(kover.get(key), jacoco.get(key)) is not None
    }

def _coverage_pct(covered: int, missed: int) -> float:
    total = covered + missed
    return round((covered / total) * 100, 1) if total else 0.0

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

    ``coverage`` may be a kover snapshot dict (keyed rows) or a
    ``files_projection`` list (as produced by ``build_files_projection``).
    """
    rows = coverage.values() if isinstance(coverage, dict) else coverage
    metric_keys = (
        "line_covered", "line_missed", "branch_covered", "branch_missed",
        "instruction_covered", "instruction_missed",
        "method_covered", "method_missed",
        "class_covered", "class_missed",
    )
    buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"module": "", "files": 0, **{k: 0 for k in metric_keys}},
    )
    for row in rows:
        if exclude_generated and str(row.get("gap_work_status") or "") == "generated":
            continue
        module = str(row.get("module") or "(unknown)")
        bucket = buckets[module]
        bucket["module"] = module
        bucket["files"] += 1
        for key in metric_keys:
            bucket[key] += int(row.get(key) or 0)
    projections = []
    for module in sorted(buckets):
        bucket = buckets[module]
        bucket["line_coverage_pct"] = _coverage_pct(bucket["line_covered"], bucket["line_missed"])
        bucket["branch_coverage_pct"] = _coverage_pct(bucket["branch_covered"], bucket["branch_missed"])
        bucket["instruction_coverage_pct"] = _coverage_pct(
            bucket["instruction_covered"], bucket["instruction_missed"],
        )
        bucket["method_coverage_pct"] = _coverage_pct(bucket["method_covered"], bucket["method_missed"])
        bucket["class_coverage_pct"] = _coverage_pct(bucket["class_covered"], bucket["class_missed"])
        projections.append(bucket)
    return projections

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
    deferred_pct = round(100.0 * deferred_credited / total, 1) if total else 0.0
    effective_pct = round(100.0 * effective_completed / total, 1) if total else 0.0
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

    def _rollup(entries: list[dict[str, Any]], *, line_covered: int, line_missed: int) -> dict[str, Any]:
        lines: set[int] = set()
        files: set[str] = set()
        categories: dict[str, int] = defaultdict(int)
        for e in entries:
            lines |= set(e.get("lines") or [])
            name = str(e.get("source_name") or "").strip()
            if name:
                files.add(name)
            cat = str(e.get("category") or "deferred").strip() or "deferred"
            categories[cat] += 1
        base = {
            "deferred_entries": entries,
            "deferred_entries_count": len(entries),
            "deferred_unique_lines_count": len(lines),
            "deferred_files_count": len(files),
            "deferred_lines_text": compact_ranges(sorted(lines)) if lines else "",
            "deferred_categories": dict(sorted(categories.items())),
        }
        base.update(
            _deferred_line_completion(
                line_covered=line_covered,
                line_missed=line_missed,
                deferred_unique_lines=len(lines),
            )
        )
        return base

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in modules_projection:
        mod = _normalize_module(row.get("module")) or str(row.get("module") or "(unknown)")
        seen.add(mod)
        enriched = dict(row)
        enriched.update(
            _rollup(
                by_mod.get(mod, []),
                line_covered=int(row.get("line_covered") or 0),
                line_missed=int(row.get("line_missed") or 0),
            )
        )
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
            "line_coverage_pct": 0.0,
            "branch_coverage_pct": 0.0,
            "instruction_coverage_pct": 0.0,
            "method_coverage_pct": 0.0,
            "class_coverage_pct": 0.0,
        }
        stub.update(_rollup(by_mod[mod], line_covered=0, line_missed=0))
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
            }
        )
    rows.sort(key=lambda r: (-int(r["line_missed"]), r["module"], r["source_name"]))
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

def derive_gap_work_status(
    *,
    line_missed: int,
    branch_missed: int,
    actionable_lines_count: int,
    actionable_branches_count: int,
    source_resolvable: bool,
) -> str:
    """Orchestrator-aligned work status for dashboard chips.

    Returns one of: covered, actionable, deferred_only, generated.
    """
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
    enriched["gap_work_status"] = derive_gap_work_status(
        line_missed=int(row.get("line_missed") or 0),
        branch_missed=int(row.get("branch_missed") or 0),
        actionable_lines_count=enriched["actionable_missed_lines_count"],
        actionable_branches_count=enriched["actionable_missed_branches_count"],
        source_resolvable=enriched["source_resolvable"],
    )
    return enriched

def summarize_gap_work_status(files_projection: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"covered": 0, "actionable": 0, "deferred_only": 0, "generated": 0}
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
    lines = []
    for n in entry.get("lines") or []:
        try:
            lines.append(int(n))
        except (TypeError, ValueError):
            continue
    source = str(entry.get("source") or "")
    return {
        "source": source,
        "source_name": str(entry.get("source_name") or (Path(source).name if source else "")),
        "module": str(entry.get("module") or ""),
        "category": str(entry.get("category") or ""),
        "reason": str(entry.get("reason") or ""),
        "methods": [str(m) for m in (entry.get("methods") or [])],
        "lines": lines,
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
    seen: set[tuple[str, tuple[int, ...]]] = set()
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
        key = (payload["source"] or payload["source_name"], tuple(payload["lines"]))
        if key in seen:
            continue
        seen.add(key)
        matched.append(payload)
    return matched

def deferred_line_set(unreachable_entries: list[dict[str, Any]] | None) -> set[int]:
    lines: set[int] = set()
    for entry in unreachable_entries or []:
        lines |= coerce_int_set(entry.get("lines"))
    return lines

def derive_coverage_status(
    *,
    accepted: bool,
    outcome: str | None,
    missed_lines=None,
    missed_branches=None,
    unreachable_entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Derive dashboard status from residual gaps after deferred lines.

    Returns status in {{completed, inprogress, untouched, rejected}} plus actionable counts.
    """
    outcome_key = str(outcome or "").strip().lower()
    deferred = deferred_line_set(unreachable_entries)
    missed_line_set = coerce_int_set(missed_lines)
    missed_branch_set = coerce_int_set(missed_branches)
    actionable_lines = sorted(missed_line_set - deferred)
    actionable_branches = sorted(missed_branch_set - deferred)

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
    gap_work_counts = summarize_gap_work_status(files_projection)

    kover_totals = _kover_totals([
        row for row in files_projection if str(row.get("gap_work_status") or "") != "generated"
    ])
    modules_projection = attach_module_deferred_coverage(
        build_modules_projection(files_projection, exclude_generated=True),
        unreachable_entries,
    )
    deferred_credited = sum(int(m.get("deferred_credited_lines") or 0) for m in modules_projection)
    actionable_pending_lines = sum(int(m.get("actionable_pending_lines") or 0) for m in modules_projection)
    line_covered = int(kover_totals.get("line_covered") or 0)
    line_missed = int(kover_totals.get("line_missed") or 0)
    # Prefer module-sum actionable when available; fall back to raw missed minus credited.
    if modules_projection:
        line_actionable = actionable_pending_lines
        # Align credited with project totals when module rollup under/over counts.
        deferred_credited = min(deferred_credited, line_missed)
        line_actionable = max(0, line_missed - deferred_credited)
    else:
        line_actionable = line_missed
        deferred_credited = 0
    total_lines = line_covered + line_missed
    deferred_coverage_pct = round(100.0 * deferred_credited / total_lines, 1) if total_lines else 0.0
    effective_line_coverage_pct = (
        round(100.0 * (line_covered + deferred_credited) / total_lines, 1) if total_lines else 0.0
    )

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
                1
                for row in files_projection
                if str(row.get("gap_work_status") or "") != "generated"
                and int(row.get("line_missed") or 0) > 0
            ),
            "unreachable_entries_count": len(unreachable_entries),
            "files_with_unreachable": len(files_with_unreachable),
            "files_fully_covered": gap_work_counts.get("covered", 0),
            "files_actionable": gap_work_counts.get("actionable", 0),
            "files_deferred_only": gap_work_counts.get("deferred_only", 0),
            "files_generated_pending": gap_work_counts.get("generated", 0),
            "modules": modules,
            **kover_totals,
            "line_deferred_credited": deferred_credited,
            "line_actionable_pending": line_actionable,
            "deferred_coverage_pct": deferred_coverage_pct,
            "effective_line_coverage_pct": effective_line_coverage_pct,
        },
        "kover_reports": [str(path) for path in discover_kover_xml(root)],
        "jacoco_reports": [str(path) for path in discover_jacoco_xml(root)],
        "modules_projection": modules_projection,
        "files_projection": files_projection,
        "sources": records,
        "untouched_gaps": untouched,
        "unreachable_entries": [_entry_payload(e) for e in unreachable_entries],
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
