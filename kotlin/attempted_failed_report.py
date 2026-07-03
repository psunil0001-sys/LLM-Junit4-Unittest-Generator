# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Persists attempted coverage outcomes and report overlays.
"""Dashboard-only JSON for safe/attemptable opportunities rejected by Kover after Gradle pass."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from UnitTest_gen.core.logging_utils import log_message
from UnitTest_gen.kotlin.gradle_tasks import gradle_variant_hint
from UnitTest_gen.kotlin.project_context import find_gradle_project_root, find_owning_module_dir, module_path_for_dir

DISPOSITION_MEASURED_NO_DELTA = "measured_no_delta"
DISPOSITION_ACCEPTABLE_BRANCH_GAP = "acceptable_branch_gap"
DISPOSITION_PIPELINE_UNRESOLVED = "pipeline_unresolved"


def _normalize_report_project_root(project_root: str | Path) -> Path:
    root = Path(project_root).resolve()
    gradle_root = find_gradle_project_root(str(root), markers=("gradlew",))
    if gradle_root:
        return Path(gradle_root).resolve()
    if (root / "data" / "blocked_report.json").is_file() and root.name == "UnitTest_gen":
        return root.parent
    return root


def _report_root(project_root: str | Path, source_file_path: str = "") -> str:
    if project_root:
        return str(Path(project_root).resolve())
    derived = find_gradle_project_root(source_file_path) if source_file_path else ""
    if derived:
        return str(Path(derived).resolve())
    return str(Path.cwd())


def blocked_report_path(project_root: str | Path) -> Path:
    return _normalize_report_project_root(project_root) / "UnitTest_gen" / "data" / "blocked_report.json"


def _empty_payload() -> dict[str, Any]:
    return {"schema_version": "1.0", "updated_at": "", "entries": []}


def load_blocked_report(project_root: str | Path) -> dict[str, Any]:
    path = blocked_report_path(project_root)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return _empty_payload()
        entries = payload.get("entries")
        if not isinstance(entries, list):
            payload["entries"] = []
        payload.setdefault("schema_version", "1.0")
        payload.setdefault("updated_at", "")
        return payload
    except json.JSONDecodeError as exc:
        log_message(f"⚠️ blocked_report.json is invalid JSON ({exc}); treating as empty.", category="warning")
        return _empty_payload()
    except OSError:
        return _empty_payload()


def save_blocked_report(project_root: str | Path, payload: dict[str, Any]) -> None:
    path = blocked_report_path(project_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(payload)
        payload["schema_version"] = "1.0"
        payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
        text = json.dumps(payload, indent=2) + "\n"
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            delete=False,
        ) as handle:
            handle.write(text)
            temp_name = handle.name
        os.replace(temp_name, path)
    except OSError as exc:
        log_message(f"⚠️ Could not write blocked attempt report: {exc}", category="warning")


def _entry_key(entry: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(entry.get("variant") or ""),
        os.path.abspath(str(entry.get("source") or "")),
        str(entry.get("fingerprint") or ""),
    )


def _selected_phase_bucket(opportunity_plan: dict | None) -> str | None:
    plan = opportunity_plan or {}
    if plan.get("selected_safe"):
        return "safe"
    if plan.get("selected_attemptable"):
        return "attemptable"
    return None


def _entry_from_opportunity(
    opportunity,
    *,
    variant: str,
    source_path: str,
    module: str,
    original_bucket: str,
    failure_stage: str,
    evidence: str,
    now: str,
    disposition: str = DISPOSITION_MEASURED_NO_DELTA,
) -> dict[str, Any]:
    from UnitTest_gen.kotlin.incremental_coverage import opportunity_fingerprint

    lines = sorted(set(getattr(opportunity, "lines", None) or []))
    branches = sorted(set(getattr(opportunity, "branches", None) or []))
    entry_points = getattr(opportunity, "coverage_path", None) or getattr(opportunity, "entry_points", None) or []
    entry = " -> ".join(entry_points) if entry_points else getattr(opportunity, "name", "")
    return {
        "variant": variant,
        "source": os.path.abspath(source_path),
        "module": module,
        "fingerprint": opportunity_fingerprint(opportunity),
        "original_bucket": original_bucket,
        "name": getattr(opportunity, "name", ""),
        "fixture": getattr(opportunity, "fixture", ""),
        "entry": entry,
        "lines": lines,
        "branches": branches,
        "failure_stage": failure_stage,
        "disposition": disposition,
        "evidence": evidence,
        "recorded_at": now,
        "last_attempt_at": now,
        "attempt_count": 1,
    }


def record_kover_rejected_attempt(
    project_root: str | Path,
    *,
    source_file_path: str,
    opportunity_plan: dict | None,
    gradle_tasks: list[str] | None,
    failure_stage: str,
    evidence: str,
    source_code: str = "",
    existing_test_code: str = "",
    disposition: str = DISPOSITION_MEASURED_NO_DELTA,
) -> None:
    """Upsert dashboard entries for selected safe/attemptable Kover rejections."""
    try:
        original_bucket = _selected_phase_bucket(opportunity_plan)
        if not original_bucket:
            return
        from UnitTest_gen.kotlin.incremental_coverage import (
            _selected_opportunities,
            is_acceptable_branch_gap_opportunity,
        )

        selected = _selected_opportunities(opportunity_plan or {})
        if not selected:
            return

        root = _report_root(project_root, source_file_path)
        variant = gradle_variant_hint(gradle_tasks)
        module_dir = find_owning_module_dir(root, source_file_path)
        module = module_path_for_dir(root, module_dir) if module_dir else ""
        now = datetime.now().isoformat(timespec="seconds")
        payload = load_blocked_report(root)
        entries = {_entry_key(item): dict(item) for item in payload.get("entries", []) if isinstance(item, dict)}

        for opportunity in selected:
            row_disposition = disposition
            row_evidence = evidence
            if is_acceptable_branch_gap_opportunity(opportunity, source_code, existing_test_code):
                row_disposition = DISPOSITION_ACCEPTABLE_BRANCH_GAP
                row_evidence = (
                    evidence
                    or "Defensive branch gap (chained safe-call or companion lateinit); happy path already covered."
                )
            row = _entry_from_opportunity(
                opportunity,
                variant=variant,
                source_path=source_file_path,
                module=module,
                original_bucket=original_bucket,
                failure_stage=failure_stage,
                evidence=row_evidence,
                now=now,
                disposition=row_disposition,
            )
            key = _entry_key(row)
            existing = entries.get(key)
            if existing:
                row["recorded_at"] = existing.get("recorded_at") or now
                row["attempt_count"] = int(existing.get("attempt_count") or 0) + 1
                if row_disposition == DISPOSITION_ACCEPTABLE_BRANCH_GAP:
                    row["disposition"] = DISPOSITION_ACCEPTABLE_BRANCH_GAP
            entries[key] = row

        payload["entries"] = list(entries.values())
        save_blocked_report(root, payload)
    except Exception as exc:
        log_message(f"⚠️ Could not record blocked attempt dashboard entry: {exc}", category="warning")


def _entry_still_open(entry: dict[str, Any], missed_lines: set[int], missed_branches: set[int]) -> bool:
    lines = set(entry.get("lines") or [])
    branches = set(entry.get("branches") or [])
    if lines & missed_lines:
        return True
    if branches & missed_branches:
        return True
    return not lines and not branches


def remove_resolved_attempts(
    project_root: str | Path,
    *,
    source_file_path: str,
    gap,
    opportunity_plan: dict | None = None,
    gradle_tasks: list[str] | None = None,
    acceptance=None,
) -> None:
    """Drop entries whose lines/branches no longer appear in the Kover gap."""
    try:
        if gap is None:
            return
        missed_lines = set(getattr(gap, "missed_lines", None) or [])
        missed_branches = set(getattr(gap, "partial_branch_lines", None) or getattr(gap, "missed_branches", None) or [])
        root = _report_root(project_root, source_file_path)
        variant = gradle_variant_hint(gradle_tasks)
        source_abs = os.path.abspath(source_file_path)
        payload = load_blocked_report(root)
        kept: list[dict[str, Any]] = []
        resolved_fingerprints: set[str] = set()
        if acceptance is not None and opportunity_plan:
            from UnitTest_gen.kotlin.incremental_coverage import (
                _selected_opportunity_fingerprints,
                opportunity_fingerprint,
            )

            selected_fps = _selected_opportunity_fingerprints(opportunity_plan)
            resolved_lines = set(getattr(acceptance, "resolved_selected_lines", None) or [])
            if resolved_lines:
                for opportunity in (
                    list((opportunity_plan or {}).get("selected_safe", []))
                    + list((opportunity_plan or {}).get("selected_attemptable", []))
                ):
                    opp_lines = set(getattr(opportunity, "lines", None) or [])
                    if opportunity_fingerprint(opportunity) in selected_fps and opp_lines <= resolved_lines:
                        resolved_fingerprints.add(opportunity_fingerprint(opportunity))

        for item in payload.get("entries", []):
            if not isinstance(item, dict):
                continue
            if str(item.get("variant") or "") != variant:
                kept.append(item)
                continue
            if os.path.abspath(str(item.get("source") or "")) != source_abs:
                kept.append(item)
                continue
            if str(item.get("fingerprint") or "") in resolved_fingerprints:
                continue
            if _entry_still_open(item, missed_lines, missed_branches):
                kept.append(item)
        if len(kept) != len(payload.get("entries", [])):
            payload["entries"] = kept
            save_blocked_report(root, payload)
    except Exception as exc:
        log_message(f"⚠️ Could not prune blocked attempt dashboard entries: {exc}", category="warning")


def prune_stale_entries_for_gap(
    project_root: str | Path,
    *,
    variant: str,
    source_path: str,
    missed_lines: set[int] | list[int],
    missed_branches: set[int] | list[int],
) -> bool:
    """Remove entries for source+variant with no remaining missed lines/branches. Returns True if changed."""
    try:
        root = str(project_root)
        source_abs = os.path.abspath(source_path)
        missed_line_set = set(missed_lines or [])
        missed_branch_set = set(missed_branches or [])
        payload = load_blocked_report(root)
        kept: list[dict[str, Any]] = []
        changed = False
        for item in payload.get("entries", []):
            if not isinstance(item, dict):
                changed = True
                continue
            if str(item.get("variant") or "") != variant:
                kept.append(item)
                continue
            if os.path.abspath(str(item.get("source") or "")) != source_abs:
                kept.append(item)
                continue
            if _entry_still_open(item, missed_line_set, missed_branch_set):
                kept.append(item)
            else:
                changed = True
        if changed:
            payload["entries"] = kept
            save_blocked_report(root, payload)
        return changed
    except Exception as exc:
        log_message(f"⚠️ Could not prune stale blocked attempt entries: {exc}", category="warning")
        return False


def entries_for_variant(project_root: str | Path, variant: str) -> list[dict[str, Any]]:
    payload = load_blocked_report(project_root)
    return [
        dict(item)
        for item in payload.get("entries", [])
        if isinstance(item, dict)
        and (variant == "default" or str(item.get("variant") or "") == variant)
    ]


def _entry_as_opportunity(entry: dict[str, Any]):
    from UnitTest_gen.kotlin.incremental_coverage import CoverageOpportunity

    entry_points = str(entry.get("entry") or entry.get("name") or "").split(" -> ")
    return CoverageOpportunity(
        name=str(entry.get("name") or ""),
        bucket="safe",
        fixture=str(entry.get("fixture") or ""),
        entry_points=[entry_points[-1].strip()] if entry_points else [],
        lines=list(entry.get("lines") or []),
        branches=list(entry.get("branches") or []),
        reason="",
        action="",
    )


def normalized_entry_disposition(entry: dict[str, Any], source_code: str = "") -> str:
    disposition = str(entry.get("disposition") or DISPOSITION_MEASURED_NO_DELTA)
    if disposition == DISPOSITION_ACCEPTABLE_BRANCH_GAP:
        return disposition
    if not source_code:
        return disposition
    from UnitTest_gen.kotlin.incremental_coverage import is_acceptable_branch_gap_opportunity

    if is_acceptable_branch_gap_opportunity(_entry_as_opportunity(entry), source_code):
        return DISPOSITION_ACCEPTABLE_BRANCH_GAP
    return disposition


def overlay_attempted_failed_on_report(model: dict, project_root: str | Path) -> dict:
    """Re-label safe/attemptable items that have dashboard rejection history."""
    try:
        variant = str(model.get("variant") or "")
        entries = entries_for_variant(project_root, variant)
        if not entries:
            return model

        by_match: dict[tuple[str, str], dict[str, Any]] = {}
        for entry in entries:
            by_match[(os.path.abspath(str(entry.get("source") or "")), str(entry.get("fingerprint") or ""))] = entry

        by_bucket = dict((model.get("summary") or {}).get("by_bucket") or {})
        for key in ("blocked", "safe", "attemptable", "excluded", "attempted_but_failed", "acceptable_branch_gap"):
            by_bucket.setdefault(key, 0)

        for file_row in model.get("files", []) or []:
            source_path = os.path.abspath(str(file_row.get("path") or ""))
            missed_lines = set()
            missed_branches = set()
            for item in file_row.get("items", []) or []:
                missed_lines.update(item.get("lines") or [])
                missed_branches.update(item.get("branches") or [])
            prune_stale_entries_for_gap(
                project_root,
                variant=variant,
                source_path=source_path,
                missed_lines=missed_lines,
                missed_branches=missed_branches,
            )

        entries = entries_for_variant(project_root, variant)
        by_match = {
            (os.path.abspath(str(entry.get("source") or "")), str(entry.get("fingerprint") or "")): entry
            for entry in entries
        }

        def _apply_overlay(item: dict, source_path: str, counted: set[tuple[str, str]]) -> None:
            if item.get("bucket") not in {"safe", "attemptable"}:
                return
            fingerprint = str(item.get("fingerprint") or "")
            match = by_match.get((source_path, fingerprint))
            if not match:
                return
            prior = item["bucket"]
            disposition = normalized_entry_disposition(match, "")
            if disposition == DISPOSITION_ACCEPTABLE_BRANCH_GAP:
                item["bucket"] = "acceptable_branch_gap"
                item["original_bucket"] = match.get("original_bucket") or prior
                item["attempt_count"] = match.get("attempt_count") or 1
                item["last_attempt_at"] = match.get("last_attempt_at") or ""
                evidence = str(match.get("evidence") or "").strip()
                if evidence:
                    item["evidence"] = evidence
                    prefix = "Acceptable defensive branch gap: "
                    if not str(item.get("why") or "").startswith(prefix):
                        item["why"] = prefix + evidence
                target_bucket = "acceptable_branch_gap"
            else:
                item["bucket"] = "attempted_but_failed"
                item["original_bucket"] = match.get("original_bucket") or prior
                item["attempt_count"] = match.get("attempt_count") or 1
                item["last_attempt_at"] = match.get("last_attempt_at") or ""
                evidence = str(match.get("evidence") or "").strip()
                if evidence:
                    item["evidence"] = evidence
                    prefix = f"Attempted but Kover rejected ({match.get('failure_stage') or 'kover_no_delta'}): "
                    if not str(item.get("why") or "").startswith(prefix):
                        item["why"] = prefix + evidence
                target_bucket = "attempted_but_failed"
            key = (source_path, fingerprint)
            if key in counted:
                return
            counted.add(key)
            by_bucket[prior] = max(0, int(by_bucket.get(prior, 0)) - 1)
            by_bucket[target_bucket] = int(by_bucket.get(target_bucket, 0)) + 1

        counted: set[tuple[str, str]] = set()
        for file_row in model.get("files", []) or []:
            source_path = os.path.abspath(str(file_row.get("path") or ""))
            for item in file_row.get("items", []) or []:
                _apply_overlay(item, source_path, counted)
        for item in model.get("flat_items", []) or []:
            file_name = str(item.get("file") or "")
            source_path = ""
            for file_row in model.get("files", []) or []:
                if file_row.get("name") == file_name:
                    source_path = os.path.abspath(str(file_row.get("path") or ""))
                    break
            if source_path:
                _apply_overlay(item, source_path, counted)

        if model.get("summary") is not None:
            model["summary"]["by_bucket"] = by_bucket
        return model
    except Exception as exc:
        log_message(f"⚠️ Could not overlay attempted-but-failed dashboard buckets: {exc}", category="warning")
        return model
