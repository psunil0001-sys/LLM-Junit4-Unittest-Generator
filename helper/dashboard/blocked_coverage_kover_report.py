#!/usr/bin/env python3
# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Builds Kover-only gap dashboards from planner classification.
"""Build Kover-only gap dashboards used by blocked coverage reporting."""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

_REPO_ROOT = str(Path(__file__).resolve().parents[3])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from UnitTest_gen.helper.dashboard.kover_gap_dashboard import render_kover_gap_dashboard, render_kover_gap_index
from UnitTest_gen.kotlin.blocked_coverage_report import (
    is_excluded_reason,
    item_from_opportunity,
    reason_code,
    recommended_fix_for_reason,
    suggested_test_for_reason,
)
from UnitTest_gen.kotlin.coverage_analysis import (
    KoverGap,
    compact_ranges,
    is_generated_kover_source,
    load_kover_gaps,
)

DEFAULT_HTML_REPORT_DIR = "UnitTest_gen/data/htmlreport"
_SKIP_KOVER_PATH_PARTS = {".gradle", ".git"}


def variant_name_from_xml_path(path: Path) -> str:
    stem = path.stem
    if stem.startswith("report"):
        suffix = stem[len("report") :]
        return suffix or "default"
    return stem


def discover_kover_xml_groups(root: Path) -> dict[str, list[Path]]:
    """Group Kover XML files under module build/reports/kover/ by filename variant suffix."""
    groups: dict[str, set[Path]] = {}
    for path in root.glob("**/build/reports/kover/**/*.xml"):
        if not path.is_file() or any(part in _SKIP_KOVER_PATH_PARTS for part in path.parts):
            continue
        variant = variant_name_from_xml_path(path)
        groups.setdefault(variant, set()).add(path.resolve())
    return {variant: sorted(paths) for variant, paths in sorted(groups.items())}


def prepare_html_report_dir(out_dir: Path, *, clear: bool = True) -> None:
    if clear and out_dir.is_dir():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

def plain_why_text(reason: str) -> str:
    text = (reason or "").strip()
    if ":" in text:
        return text.split(":", 1)[1].strip() or text
    return text or "No reason recorded."


def _gap_namespace(gap: KoverGap) -> SimpleNamespace:
    return SimpleNamespace(
        missed_lines=sorted(gap.missed_lines),
        partial_branch_lines=sorted(gap.missed_branches),
        missed_methods=[],
        partial_branch_methods=[],
    )


def report_bucket_for_opportunity(opportunity, planner_bucket: str) -> str:
    blocked_reason = getattr(opportunity, "blocked_reason", "") or ""
    if is_excluded_reason(blocked_reason) or (
        planner_bucket == "blocked" and is_excluded_reason(getattr(opportunity, "reason", ""))
    ):
        return "excluded"
    return planner_bucket


def resolve_existing_test_code(source_path: Path, root: Path) -> str:
    from UnitTest_gen.kotlin.project_context import derive_test_output_path

    test_path = Path(derive_test_output_path(str(source_path.resolve()), str(root.resolve())))
    if not test_path.is_file():
        return ""
    try:
        return test_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _collect_plan_opportunities(plan: dict) -> list[tuple[str, object]]:
    rows: list[tuple[str, object]] = []
    for bucket, key in (
        ("blocked", "blocked"),
        ("safe", "selected_safe"),
        ("attemptable", "selected_attemptable"),
        ("safe", "alternatives"),
    ):
        for opportunity in plan.get(key, []):
            actual_bucket = bucket
            if key == "alternatives":
                actual_bucket = "attemptable" if getattr(opportunity, "bucket", "") == "attemptable" else "safe"
            elif bucket == "blocked":
                actual_bucket = "blocked"
            actual_bucket = report_bucket_for_opportunity(opportunity, actual_bucket)
            rows.append((actual_bucket, opportunity))
    return rows


def _opportunity_report_item(opportunity, bucket: str) -> dict:
    from UnitTest_gen.kotlin.incremental_coverage import opportunity_fingerprint

    bucket = report_bucket_for_opportunity(opportunity, bucket)
    blocked_reason = getattr(opportunity, "blocked_reason", "") or ""
    if bucket in {"blocked", "excluded"} or blocked_reason:
        blocked_item = item_from_opportunity("", opportunity)
        why_source = blocked_item.reason
        fix = blocked_item.recommended_fix
        next_test = blocked_item.suggested_test_after_fix
        evidence = blocked_item.evidence
        code = reason_code(blocked_item.reason)
    else:
        why_source = getattr(opportunity, "reason", "") or ""
        fix = recommended_fix_for_reason(blocked_reason) if blocked_reason else (getattr(opportunity, "action", "") or "Generate a focused public-contract test.")
        next_test = getattr(opportunity, "trigger_recipe", "") or getattr(opportunity, "action", "") or suggested_test_for_reason(blocked_reason)
        evidence = getattr(opportunity, "execution_proof", "") or ""
        code = reason_code(blocked_reason) if blocked_reason else ("safe_to_generate" if bucket == "safe" else "attemptable_gap")
    entry = " -> ".join(opportunity.coverage_path or opportunity.entry_points or [opportunity.name])
    lines = sorted(set(getattr(opportunity, "lines", []) or []))
    branches = sorted(set(getattr(opportunity, "branches", []) or []))
    return {
        "fingerprint": opportunity_fingerprint(opportunity),
        "bucket": bucket,
        "reason_code": code,
        "fixture": getattr(opportunity, "fixture", ""),
        "entry": entry,
        "name": getattr(opportunity, "name", ""),
        "lines": lines,
        "branches": branches,
        "lines_text": compact_ranges(lines),
        "branches_text": compact_ranges(branches),
        "why": plain_why_text(why_source),
        "fix": fix,
        "next_test": next_test,
        "evidence": evidence,
    }


def _merge_report_items(items: list[dict]) -> list[dict]:
    merged: dict[tuple[str, str, str, str], dict] = {}
    for item in items:
        key = (
            item["bucket"],
            item["reason_code"],
            item["fixture"],
            item.get("fingerprint") or item.get("name", ""),
        )
        existing = merged.get(key)
        if existing is None:
            merged[key] = dict(item)
            continue
        existing["lines"] = sorted(set(existing["lines"]) | set(item["lines"]))
        existing["branches"] = sorted(set(existing["branches"]) | set(item["branches"]))
        existing["lines_text"] = compact_ranges(existing["lines"])
        existing["branches_text"] = compact_ranges(existing["branches"])
        if len(item.get("entry", "")) > len(existing.get("entry", "")):
            existing["entry"] = item["entry"]
    return list(merged.values())


def _line_map_positions(missed_lines: list[int], line_count: int) -> list[float]:
    if not missed_lines or line_count <= 0:
        return []
    return [round((line_no / line_count) * 100.0, 2) for line_no in missed_lines]


def build_source_coverage_plan(
    source_code: str,
    gap,
    *,
    source_path: Path | None = None,
    existing_test_code: str = "",
) -> dict:
    from UnitTest_gen.kotlin.incremental_coverage import build_coverage_opportunity_plan
    from UnitTest_gen.kotlin.kotlin_analysis import classify_source
    from UnitTest_gen.kotlin.static_analysis import analyze_kotlin_code

    source_path_text = str(source_path) if source_path else ""
    analysis_report = analyze_kotlin_code(source_code, source_path_text)
    profile = classify_source(source_code, analysis_report)
    return build_coverage_opportunity_plan(
        source_code,
        gap,
        profile.categories,
        existing_test_code=existing_test_code,
        source_path=source_path_text,
    )


def build_kover_gap_report(
    root: Path,
    kover_xml_paths: Path | list[Path],
    *,
    include_generated: bool = False,
) -> dict:
    from UnitTest_gen.kotlin.incremental_coverage import opportunity_fingerprint
    from UnitTest_gen.kotlin.kotlin_analysis import classify_source
    from UnitTest_gen.kotlin.static_analysis import analyze_kotlin_code
    from UnitTest_gen.kotlin.strategy_contracts import select_strategy_contracts

    if isinstance(kover_xml_paths, Path):
        xml_paths = [kover_xml_paths.resolve()]
    else:
        xml_paths = [Path(path).resolve() for path in kover_xml_paths]
    if not xml_paths:
        raise ValueError("At least one Kover XML path is required")

    variant = variant_name_from_xml_path(xml_paths[0])
    kover_gaps = load_kover_gaps(xml_paths, root, include_generated=include_generated)
    files: list[dict] = []
    flat_items: list[dict] = []
    by_bucket: dict[str, int] = {
        "blocked": 0,
        "safe": 0,
        "attemptable": 0,
        "excluded": 0,
        "attempted_but_failed": 0,
        "acceptable_branch_gap": 0,
    }
    by_reason: dict[str, int] = {}
    total_missed_lines = 0
    total_missed_branches = 0
    classified_lines: set[int] = set()
    classified_branch_lines: set[int] = set()

    for gap in sorted(kover_gaps.values(), key=lambda item: (item.module_hint, item.source_file_name)):
        if not gap.source_file_name.endswith(".kt"):
            continue
        if is_generated_kover_source(gap.package_name, gap.source_file_name) and not include_generated:
            continue
        if not gap.missed_lines and not gap.missed_branches:
            continue
        source_path = resolve_kover_source_path(root, gap)
        if source_path is None:
            continue
        try:
            source_code = source_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        analysis_report = analyze_kotlin_code(source_code, str(source_path))
        profile = classify_source(source_code, analysis_report)
        contracts = [contract.id for contract in select_strategy_contracts(profile.categories)]
        existing_test_code = resolve_existing_test_code(source_path, root)
        plan = build_source_coverage_plan(
            source_code,
            _gap_namespace(gap),
            source_path=source_path,
            existing_test_code=existing_test_code,
        )

        seen_fingerprints: set[str] = set()
        raw_items: list[dict] = []
        for bucket, opportunity in _collect_plan_opportunities(plan):
            fingerprint = opportunity_fingerprint(opportunity)
            if fingerprint in seen_fingerprints:
                continue
            seen_fingerprints.add(fingerprint)
            item = _opportunity_report_item(opportunity, bucket)
            raw_items.append(item)

        file_items = _merge_report_items(raw_items)
        assigned_lines: set[int] = set()
        assigned_branches: set[int] = set()
        for item in file_items:
            assigned_lines.update(item["lines"])
            assigned_branches.update(item["branches"])
            by_bucket[item["bucket"]] = by_bucket.get(item["bucket"], 0) + 1
            by_reason[item["reason_code"]] = by_reason.get(item["reason_code"], 0) + 1
            flat_items.append({**item, "file": source_path.name})

        unassigned_lines = sorted(set(gap.missed_lines) - assigned_lines)
        unassigned_branches = sorted(set(gap.missed_branches) - assigned_branches)
        classified_lines.update(assigned_lines)
        classified_branch_lines.update(assigned_branches)
        total_missed_lines += len(gap.missed_lines)
        total_missed_branches += len(gap.missed_branches)

        line_count = max(len(source_code.splitlines()), 1)
        files.append(
            {
                "name": gap.source_file_name,
                "path": str(source_path),
                "module": gap.module_hint,
                "package": gap.package_name,
                "categories": sorted(profile.categories),
                "contracts": contracts,
                "line_count": line_count,
                "line_map": _line_map_positions(sorted(gap.missed_lines), line_count),
                "missed_lines_count": len(gap.missed_lines),
                "missed_branches_count": len(gap.missed_branches),
                "missed_lines": sorted(gap.missed_lines),
                "missed_branches": sorted(gap.missed_branches),
                "items": file_items,
                "unassigned_lines_text": compact_ranges(unassigned_lines) if unassigned_lines else "",
                "unassigned_branches_text": compact_ranges(unassigned_branches) if unassigned_branches else "",
            }
        )

    planner_line_pct = round(len(classified_lines) / total_missed_lines * 100.0, 1) if total_missed_lines else 100.0
    module_hints = sorted({file_row["module"] for file_row in files})
    model = {
        "variant": variant,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "root": str(root),
        "kover_xml": str(xml_paths[0]),
        "kover_xmls": [str(path) for path in xml_paths],
        "summary": {
            "missed_lines": total_missed_lines,
            "missed_branches": total_missed_branches,
            "file_count": len(files),
            "module_count": len(module_hints),
            "kover_xml_count": len(xml_paths),
            "by_bucket": by_bucket,
            "by_reason": by_reason,
            "planner_coverage_pct": planner_line_pct,
        },
        "files": files,
        "flat_items": flat_items,
    }
    from UnitTest_gen.kotlin.attempted_failed_report import overlay_attempted_failed_on_report

    return overlay_attempted_failed_on_report(model, root)


def write_kover_gap_reports(
    root: Path,
    *,
    variants: list[str] | None = None,
    include_generated: bool = False,
    output_dir: Path | None = None,
    clear_output: bool = True,
) -> tuple[list[Path], list[dict]]:
    root = root.resolve()
    out_dir = output_dir or (root / DEFAULT_HTML_REPORT_DIR)
    prepare_html_report_dir(out_dir, clear=clear_output)
    written: list[Path] = []
    models: list[dict] = []
    variant_filter = set(variants) if variants else None
    for variant, xml_paths in discover_kover_xml_groups(root).items():
        if variant_filter is not None and variant not in variant_filter:
            continue
        model = build_kover_gap_report(root, xml_paths, include_generated=include_generated)
        html_path = out_dir / f"kover_gaps_{variant}.html"
        html_path.write_text(render_kover_gap_dashboard(model), encoding="utf-8")
        written.append(html_path)
        models.append(model)
    if models:
        index_path = out_dir / "kover_gaps_index.html"
        index_path.write_text(render_kover_gap_index(models, out_dir), encoding="utf-8")
        written.append(index_path)
    return written, models


def write_all_kover_html_reports(
    root: Path,
    *,
    variants: list[str] | None = None,
    include_generated: bool = False,
    output_dir: Path | None = None,
    gaps: bool = True,
    summaries: bool = True,
) -> list[Path]:
    from UnitTest_gen.helper.dashboard.kover_coverage_summary import (
        render_kover_reports_hub,
        write_coverage_summary_reports,
    )

    root = root.resolve()
    out_dir = output_dir or (root / DEFAULT_HTML_REPORT_DIR)
    prepare_html_report_dir(out_dir)
    written: list[Path] = []
    summary_models: list[dict] = []
    gap_models: list[dict] = []

    if summaries:
        summary_paths, summary_models = write_coverage_summary_reports(
            root,
            variants=variants,
            output_dir=out_dir,
            clear_output=False,
        )
        written.extend(summary_paths)

    if gaps:
        gap_paths, gap_models = write_kover_gap_reports(
            root,
            variants=variants,
            include_generated=include_generated,
            output_dir=out_dir,
            clear_output=False,
        )
        written.extend(gap_paths)

    if summary_models or gap_models:
        hub_path = out_dir / "kover_reports_index.html"
        hub_path.write_text(render_kover_reports_hub(summary_models, gap_models), encoding="utf-8")
        written.append(hub_path)
    return written


def resolve_kover_source_path(root: Path, gap: KoverGap) -> Path | None:
    module_dir = root / gap.module_hint
    package_path = Path(*gap.package_name.split("/")) if gap.package_name else Path()
    for source_root in ("java", "kotlin"):
        candidate = module_dir / "src" / "main" / source_root / package_path / gap.source_file_name
        if candidate.is_file():
            return candidate.resolve()
    suffix = str(package_path / gap.source_file_name)
    matches = [
        path.resolve()
        for path in root.glob(f"**/{gap.source_file_name}")
        if "/src/main/" in str(path).replace("\\", "/")
        and str(path).replace("\\", "/").endswith(suffix.replace("\\", "/"))
    ]
    return matches[0] if len(matches) == 1 else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build Kover HTML reports (coverage summaries + planner gap dashboards)",
    )
    parser.add_argument(
        "--root",
        default=str(Path(__file__).resolve().parents[3]),
        help="Gradle project root (default: repo root)",
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help=f"HTML output directory (default: {DEFAULT_HTML_REPORT_DIR})",
    )
    parser.add_argument(
        "--variant",
        action="append",
        dest="variants",
        help="Filter to discovered variant group (repeatable). Default: all groups on disk",
    )
    parser.add_argument(
        "--gaps-only",
        action="store_true",
        help="Only build planner gap dashboards",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Only build coverage summary dashboards",
    )
    parser.add_argument(
        "--include-generated",
        action="store_true",
        help="Include generated/Hilt sources in gap dashboards",
    )
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    output_dir = Path(args.output_dir).resolve() if args.output_dir else None
    summaries = not args.gaps_only
    gaps = not args.summary_only
    written = write_all_kover_html_reports(
        root,
        variants=args.variants,
        include_generated=args.include_generated,
        output_dir=output_dir,
        gaps=gaps,
        summaries=summaries,
    )
    if not written:
        print("No Kover XML reports found. Run Gradle Kover tasks first, e.g.:")
        print("  ./gradlew compileThenKoverAllProdReports")
        return 1
    print(f"Wrote {len(written)} report(s):")
    for path in written:
        print(f"  {path}")
    print(f"\nOpen: {(output_dir or root / DEFAULT_HTML_REPORT_DIR) / 'kover_reports_index.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
