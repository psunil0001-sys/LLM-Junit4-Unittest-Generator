"""Kover/JaCoCo parse, merge, acceptance, unreachable store."""

from __future__ import annotations

from pathlib import Path

JACOCO_XML_GLOB_PATTERNS: tuple[str, ...] = (
    "**/build/reports/coverage/androidTest/**/*.xml",
    "**/build/reports/instrumented-coverage/**/*.xml",
    "**/build/reports/jacoco/**/*.xml",
    "**/build/outputs/code_coverage/**/*.xml",
)

JACOCO_MODULE_REPORT_ROOTS: tuple[str, ...] = (
    "build/reports/coverage/androidTest",
    "build/reports/instrumented-coverage",
    "build/reports/jacoco",
    "build/outputs/code_coverage",
)

def merge_missed_line_sets(
    unit_lines: set[int] | frozenset[int],
    instr_lines: set[int] | frozenset[int],
) -> list[int]:
    """Union rule: line actionable only if missed in every report that ran."""
    return sorted((set(unit_lines) & set(instr_lines)) | (set(instr_lines) - set(unit_lines)))

def discover_jacoco_xml(project_root: Path | str) -> list[Path]:
    """JaCoCo / AndroidTest coverage XML under standard AGP output paths."""
    root = Path(project_root)
    seen: set[Path] = set()
    for pattern in JACOCO_XML_GLOB_PATTERNS:
        for path in root.glob(pattern):
            if path.is_file():
                seen.add(path.resolve())
    return sorted(seen)

def iter_jacoco_xml_candidates(module_dir: str | Path) -> list[tuple[float, str]]:
    """Newest-first candidates under a single module build tree."""
    module = Path(module_dir)
    candidates: list[tuple[float, str]] = []
    for rel in JACOCO_MODULE_REPORT_ROOTS:
        root = module / rel
        if not root.is_dir():
            continue
        for path in root.rglob("*.xml"):
            if path.is_file():
                candidates.append((path.stat().st_mtime, str(path)))
    return candidates

import hashlib
import json
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from UnitTest_gen.core.config import get_config
from UnitTest_gen.core.io import compact_ranges
from UnitTest_gen.kotlin.analysis import kotlin_package_name

_SOURCE_WINDOW_RADIUS = 4
_SOURCE_EMBED_CHAR_BUDGET = 9000
_SKELETON_DIR = Path(__file__).resolve().parents[1] / "data" / "prompt_skeletons"
AGENT_DONE_INSTRUCTIONS = (_SKELETON_DIR / "agent_done_instructions.md").read_text(encoding="utf-8").strip()

@dataclass
class LineGapDetail:
    line_no: int
    missed_instructions: int
    missed_branches: int
    covered_branches: int
    gap_kind: str  # "line", "branch", "line_and_branch"

@dataclass
class CoverageGap:
    source_file: str
    package_name: str
    missed_lines: list[int]
    partial_branch_lines: list[int]
    missed_methods: list[str]
    partial_branch_methods: list[str]
    line_coverage: tuple[int, int]
    branch_coverage: tuple[int, int]
    line_details: dict[int, LineGapDetail] | None = None

    def has_gaps(self) -> bool:
        return bool(self.missed_lines or self.partial_branch_lines or self.missed_methods or self.partial_branch_methods)

    def has_any_coverage(self) -> bool:
        return self.line_coverage[1] > 0 or self.branch_coverage[1] > 0

@dataclass
class KoverGap:
    key: tuple[str, str]
    package_name: str
    source_file_name: str
    module_hint: str
    missed_lines: set[int]
    missed_branches: set[int]
    line_covered: int
    branch_covered: int
    instruction_missed: int = 0
    instruction_covered: int = 0
    method_missed: int = 0
    method_covered: int = 0
    class_missed: int = 0
    class_covered: int = 0

@dataclass
class CoverageDelta:
    resolved_lines: list[int]
    new_missed_lines: list[int]
    resolved_branches: list[int]
    new_missed_branches: list[int]
    resolved_methods: list[str]
    new_missed_methods: list[str]
    before_line_missed: int
    before_line_covered: int
    after_line_missed: int
    after_line_covered: int
    before_branch_missed: int
    before_branch_covered: int
    after_branch_missed: int
    after_branch_covered: int

    def _coverage_improved(self, resolved: list, before_missed: int, after_missed: int, before_covered: int, after_covered: int) -> bool:
        return bool(resolved or after_missed < before_missed or after_covered > before_covered)

    @property
    def line_improved(self) -> bool:
        return self._coverage_improved(self.resolved_lines, self.before_line_missed, self.after_line_missed, self.before_line_covered, self.after_line_covered)

    @property
    def branch_improved(self) -> bool:
        return self._coverage_improved(self.resolved_branches, self.before_branch_missed, self.after_branch_missed, self.before_branch_covered, self.after_branch_covered)

    @property
    def method_improved(self) -> bool:
        return bool(self.resolved_methods)

    @property
    def has_regression(self) -> bool:
        return bool(
            self.new_missed_lines or self.new_missed_branches or self.new_missed_methods
            or self.after_line_missed > self.before_line_missed or self.after_branch_missed > self.before_branch_missed
            or self.after_line_covered < self.before_line_covered or self.after_branch_covered < self.before_branch_covered
        )

    @property
    def improved(self) -> bool:
        return self.line_improved or self.branch_improved or self.method_improved

def kover_key(package_name: str, source_file_name: str) -> tuple[str, str]:
    return (package_name.replace(".", "/"), source_file_name)

def is_generated_kover_source(package_name: str, source_file_name: str) -> bool:
    package_path = package_name.replace(".", "/")
    source_name = source_file_name.removesuffix(".kt").removesuffix(".java")
    if package_path.startswith("dagger/hilt/internal") or "build/generated" in package_path:
        return True
    if source_name in {"BuildConfig", "R", "Manifest"} or source_name.startswith(("R$", "_")):
        return True
    return ("Aggregated" in source_name or "Hilt" in source_name) and package_path.startswith("hilt_aggregated_deps")

def module_hint_from_kover_xml(path: Path, root: Path) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError:
        return str(path.parent)
    parts = list(relative.parts)
    if "build" in parts:
        return str(Path(*parts[: parts.index("build")])) or "."
    return str(relative.parent)

def counter_pair(node, counter_type: str) -> tuple[int, int]:
    counter = node.find(f"counter[@type='{counter_type}']")
    if counter is None:
        return 0, 0
    return int(counter.attrib.get("missed", "0") or "0"), int(counter.attrib.get("covered", "0") or "0")

def _line_gap_kind(missed_instructions: int, missed_branches: int, covered_instructions: int = 0) -> str:
    if missed_instructions > 0 and covered_instructions == 0 and missed_branches > 0:
        return "line_and_branch"
    return "branch" if missed_branches > 0 else "line"

def _parse_sourcefile_line_gaps(source_node) -> tuple[set[int], set[int], dict[int, LineGapDetail]]:
    missed_lines, missed_branches, line_details = set(), set(), {}
    for line in source_node.findall("line"):
        line_no = int(line.attrib.get("nr", "0") or "0")
        if line_no <= 0:
            continue
        missed_instructions = int(line.attrib.get("mi", "0") or "0")
        covered_instructions = int(line.attrib.get("ci", "0") or "0")
        missed_branch_count = int(line.attrib.get("mb", "0") or "0")
        covered_branch_count = int(line.attrib.get("cb", "0") or "0")
        if missed_instructions > 0 and covered_instructions == 0:
            missed_lines.add(line_no)
        if missed_branch_count > 0:
            missed_branches.add(line_no)
        if missed_instructions > 0 or missed_branch_count > 0:
            line_details[line_no] = LineGapDetail(
                line_no=line_no, missed_instructions=missed_instructions, missed_branches=missed_branch_count,
                covered_branches=covered_branch_count,
                gap_kind=_line_gap_kind(missed_instructions, missed_branch_count, covered_instructions),
            )
    return missed_lines, missed_branches, line_details

def _find_package_source_node(root, source_file_name: str, expected_package: str = ""):
    for candidate_package in root.findall("package"):
        package_name = candidate_package.attrib.get("name", "")
        candidate_source = candidate_package.find(f"sourcefile[@name='{source_file_name}']")
        if candidate_source is None or (expected_package and package_name != expected_package):
            continue
        return candidate_package, candidate_source
    if expected_package:
        return None, None
    for candidate_package in root.findall("package"):
        candidate_source = candidate_package.find(f"sourcefile[@name='{source_file_name}']")
        if candidate_source is not None:
            return candidate_package, candidate_source
    return None, None

def _iter_kover_xml_candidates(module_dir: str | Path) -> list[tuple[float, str]]:
    roots = (
        os.path.join(str(module_dir), "build", "reports", "kover"),
        os.path.join(str(module_dir), "build", "reports", "unit-coverage"),
    )
    candidates: list[tuple[float, str]] = []
    for report_root in roots:
        if not os.path.isdir(report_root):
            continue
        for root, _, files in os.walk(report_root):
            for file_name in files:
                if file_name.endswith(".xml"):
                    path = os.path.join(root, file_name)
                    candidates.append((os.path.getmtime(path), path))
    return candidates

def find_latest_kover_xml(module_dir: str | Path) -> str | None:
    """Return the most recently modified Kover XML report under module_dir/build/reports/kover."""
    candidates = _iter_kover_xml_candidates(module_dir)
    return max(candidates, default=(0, None))[1]

def package_path_from_source(source_code: str) -> str:
    return kotlin_package_name(source_code or "").replace(".", "/")

def _sourcefile_method_class_counters(package_node, source_file_name: str) -> tuple[int, int, int, int]:
    """Sum METHOD counters and JaCoCo-style CLASS covered/missed for one source file."""
    method_missed = method_covered = class_missed = class_covered = 0
    for class_node in package_node.findall("class"):
        if class_node.attrib.get("sourcefilename") != source_file_name:
            continue
        m_missed, m_covered = counter_pair(class_node, "METHOD")
        method_missed += m_missed
        method_covered += m_covered
        _inst_missed, inst_covered = counter_pair(class_node, "INSTRUCTION")
        if inst_covered > 0 or m_covered > 0:
            class_covered += 1
        else:
            class_missed += 1
    return method_missed, method_covered, class_missed, class_covered

def parse_kover_xml(
    path: Path | str,
    root: Path | str,
    *,
    include_generated: bool = False,
    include_fully_covered: bool = False,
) -> dict[tuple[str, str], KoverGap]:
    try:
        xml_root = ET.parse(Path(path)).getroot()
    except (ET.ParseError, OSError):
        return {}
    module_hint = module_hint_from_kover_xml(Path(path), Path(root))
    gaps: dict[tuple[str, str], KoverGap] = {}
    for package in xml_root.findall("package"):
        package_name = package.attrib.get("name", "")
        for sourcefile in package.findall("sourcefile"):
            source_file_name = sourcefile.attrib.get("name", "")
            if not include_generated and is_generated_kover_source(package_name, source_file_name):
                continue
            missed_lines, missed_branches, _ = _parse_sourcefile_line_gaps(sourcefile)
            if not include_fully_covered and not missed_lines and not missed_branches:
                continue
            instruction_missed, instruction_covered = counter_pair(sourcefile, "INSTRUCTION")
            _, line_covered = counter_pair(sourcefile, "LINE")
            _, branch_covered = counter_pair(sourcefile, "BRANCH")
            method_missed, method_covered, class_missed, class_covered = (
                _sourcefile_method_class_counters(package, source_file_name)
            )
            key = kover_key(package_name, source_file_name)
            gaps[key] = KoverGap(
                key=key, package_name=package_name, source_file_name=source_file_name, module_hint=module_hint,
                missed_lines=missed_lines, missed_branches=missed_branches,
                line_covered=line_covered, branch_covered=branch_covered,
                instruction_missed=instruction_missed, instruction_covered=instruction_covered,
                method_missed=method_missed, method_covered=method_covered,
                class_missed=class_missed, class_covered=class_covered,
            )
    return gaps

def merge_kover_snapshots(snapshots: list[dict[tuple[str, str], KoverGap]]) -> dict[tuple[str, str], KoverGap]:
    merged: dict[tuple[str, str], KoverGap] = {}
    for snapshot in snapshots:
        for key, gap in snapshot.items():
            previous = merged.get(key)
            if previous is None or gap.line_covered + gap.branch_covered > previous.line_covered + previous.branch_covered:
                merged[key] = gap
    return merged

def load_kover_gaps(
    paths: list[Path | str], root: Path | str, *, include_generated: bool = False, include_fully_covered: bool = False,
) -> dict[tuple[str, str], KoverGap]:
    return merge_kover_snapshots([
        parse_kover_xml(path, root, include_generated=include_generated, include_fully_covered=include_fully_covered)
        for path in paths
    ])

def _method_gap_lists(package_node, source_file_name: str) -> tuple[list[str], list[str]]:
    missed_methods, partial_branch_methods = [], []
    for class_node in package_node.findall("class"):
        if class_node.attrib.get("sourcefilename") != source_file_name:
            continue
        for method in class_node.findall("method"):
            counters = {counter.attrib.get("type"): counter for counter in method.findall("counter")}
            name = method.attrib.get("name", "")
            line_counter, branch_counter = counters.get("LINE"), counters.get("BRANCH")
            if line_counter is not None and int(line_counter.attrib.get("missed", "0") or "0") > 0:
                missed_methods.append(name)
            if branch_counter is not None and int(branch_counter.attrib.get("missed", "0") or "0") > 0:
                partial_branch_methods.append(name)
    return missed_methods, partial_branch_methods

def parse_kover_gap(xml_path: str, source_file_path: str, source_code: str) -> CoverageGap | None:
    if not xml_path or not os.path.exists(xml_path):
        return None
    source_file_name = os.path.basename(source_file_path)
    try:
        root = ET.parse(xml_path).getroot()
    except (ET.ParseError, OSError):
        return None
    package_node, source_node = _find_package_source_node(root, source_file_name, package_path_from_source(source_code))
    if source_node is None or package_node is None:
        return None
    missed_line_set, partial_branch_set, line_details = _parse_sourcefile_line_gaps(source_node)
    missed_methods, partial_branch_methods = _method_gap_lists(package_node, source_file_name)
    return CoverageGap(
        source_file=source_file_name, package_name=package_node.attrib.get("name", ""),
        missed_lines=sorted(missed_line_set), partial_branch_lines=sorted(partial_branch_set),
        missed_methods=sorted(set(filter(None, missed_methods))),
        partial_branch_methods=sorted(set(filter(None, partial_branch_methods))),
        line_coverage=counter_pair(source_node, "LINE"), branch_coverage=counter_pair(source_node, "BRANCH"),
        line_details=line_details,
    )

def coverage_gap_fingerprint(gap) -> str:
    payload = {
        "source_file": gap.source_file,
        "missed_lines": sorted(gap.missed_lines),
        "partial_branch_lines": sorted(gap.partial_branch_lines),
        "missed_methods": sorted(gap.missed_methods),
        "partial_branch_methods": sorted(gap.partial_branch_methods),
        "line_coverage": gap.line_coverage,
        "branch_coverage": gap.branch_coverage,
    }
    return hashlib.md5(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

def compute_coverage_delta(before, after) -> CoverageDelta | None:
    if before is None or after is None:
        return None
    before_line_missed, before_line_covered = before.line_coverage
    after_line_missed, after_line_covered = after.line_coverage
    before_branch_missed, before_branch_covered = before.branch_coverage
    after_branch_missed, after_branch_covered = after.branch_coverage
    return CoverageDelta(
        resolved_lines=sorted(set(before.missed_lines) - set(after.missed_lines)),
        new_missed_lines=sorted(set(after.missed_lines) - set(before.missed_lines)),
        resolved_branches=sorted(set(before.partial_branch_lines) - set(after.partial_branch_lines)),
        new_missed_branches=sorted(set(after.partial_branch_lines) - set(before.partial_branch_lines)),
        resolved_methods=sorted(set(before.missed_methods + before.partial_branch_methods) - set(after.missed_methods + after.partial_branch_methods)),
        new_missed_methods=sorted(set(after.missed_methods + after.partial_branch_methods) - set(before.missed_methods + before.partial_branch_methods)),
        before_line_missed=before_line_missed, before_line_covered=before_line_covered,
        after_line_missed=after_line_missed, after_line_covered=after_line_covered,
        before_branch_missed=before_branch_missed, before_branch_covered=before_branch_covered,
        after_branch_missed=after_branch_missed, after_branch_covered=after_branch_covered,
    )

def kover_report_module_dirs(project_root: str, gradle_tasks: list | None, fallback_module_dir: str) -> list[str]:
    module_dirs: list[str] = []
    project_root_path = Path(project_root).resolve()
    for task in gradle_tasks or []:
        task_text = str(task).strip()
        if "kover" not in task_text.lower():
            continue
        task_parts = [part for part in task_text.split(":") if part]
        candidate = str(project_root_path) if len(task_parts) <= 1 else str(project_root_path.joinpath(*task_parts[:-1]))
        if os.path.isdir(candidate) and candidate not in module_dirs:
            module_dirs.append(candidate)
    if fallback_module_dir not in module_dirs:
        module_dirs.append(fallback_module_dir)
    return module_dirs

def find_latest_kover_xml_for_context(project_root: str, gradle_tasks: list | None, fallback_module_dir: str) -> str | None:
    for candidate_module_dir in kover_report_module_dirs(project_root, gradle_tasks, fallback_module_dir):
        kover_xml = find_latest_kover_xml(candidate_module_dir)
        if kover_xml:
            return kover_xml
    return None

def find_latest_kover_xml_for_source_context(
    project_root: str, gradle_tasks: list | None, fallback_module_dir: str, source_file_path: str, source_code: str,
) -> str | None:
    task_hints = [
        match.group(1).lower() for task in gradle_tasks or []
        if (match := re.search(r"koverXmlReport([A-Za-z0-9]+)", str(task)))
    ]
    candidates = []
    for module_dir in kover_report_module_dirs(project_root, gradle_tasks, fallback_module_dir):
        report_root = Path(module_dir) / "build" / "reports" / "kover"
        if not report_root.is_dir():
            continue
        for path in report_root.rglob("*.xml"):
            normalized_path = re.sub(r"[^a-z0-9]", "", str(path).lower())
            if task_hints and not any(hint in normalized_path for hint in task_hints):
                continue
            if parse_kover_gap(str(path), source_file_path, source_code) is not None:
                candidates.append((path.stat().st_mtime, str(path)))
    return max(candidates, default=(0, None))[1]

async def parse_latest_coverage_gap(
    module_dir: str, source_file_path: str, source_code: str, project_root: str | None = None, gradle_tasks: list | None = None,
):
    if project_root:
        kover_xml = find_latest_kover_xml_for_source_context(project_root, gradle_tasks, module_dir, source_file_path, source_code)
        return parse_kover_gap(kover_xml, source_file_path, source_code) if kover_xml else None
    kover_xml = find_latest_kover_xml(module_dir)
    return parse_kover_gap(kover_xml, source_file_path, source_code) if kover_xml else None

def build_coverage_summary(
    *,
    plan: Any,
    before_gap=None,
    after_gap=None,
    before_gap_raw=None,
    after_gap_raw=None,
    improved: bool | None = None,
    why_not_improved: str = "",
) -> dict[str, Any]:
    from UnitTest_gen.kotlin.coverage import build_python_coverage_summary

    return build_python_coverage_summary(
        plan=plan,
        before_gap=before_gap,
        after_gap=after_gap,
        before_gap_raw=before_gap_raw,
        after_gap_raw=after_gap_raw,
        improved=improved,
        why_not_improved=why_not_improved,
    )

def gap_summary_text(gap) -> str:
    if gap is None:
        return "No Kover entry exists for this source yet; all executable lines are uncovered."
    line_missed, line_covered = getattr(gap, "line_coverage", (0, 0))
    branch_missed, branch_covered = getattr(gap, "branch_coverage", (0, 0))
    return "\n".join([
        f"Lines missed/covered: {line_missed}/{line_covered}",
        f"Branches missed/covered: {branch_missed}/{branch_covered}",
        f"Missed lines: {compact_ranges(getattr(gap, 'missed_lines', ()) or ())}",
        f"Partial branch lines: {compact_ranges(getattr(gap, 'partial_branch_lines', ()) or ())}",
        f"Missed methods: {', '.join(sorted(set(getattr(gap, 'missed_methods', ()) or ()))) or 'none'}",
    ])

def uncovered_source_windows(
    source_path: str, gap, *, radius: int = _SOURCE_WINDOW_RADIUS, char_budget: int = _SOURCE_EMBED_CHAR_BUDGET, only_lines: set[int] | None = None,
) -> str:
    path = Path(source_path).resolve()
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return f"SOURCE WINDOWS UNAVAILABLE: {path} ({exc})"
    if not lines:
        return f"SOURCE WINDOWS: {path} is empty."
    raw_anchors = {
        int(n) for n in list(getattr(gap, "missed_lines", ()) or ()) + list(getattr(gap, "partial_branch_lines", ()) or [])
        if int(n) > 0
    }
    if only_lines is not None:
        raw_anchors &= {int(n) for n in only_lines}
    anchors = sorted(raw_anchors) or list(range(1, min(len(lines), 40) + 1))
    spans: list[tuple[int, int]] = []
    for line_no in anchors:
        start, end = max(1, line_no - radius), min(len(lines), line_no + radius)
        if spans and start <= spans[-1][1] + 1:
            spans[-1] = (spans[-1][0], max(spans[-1][1], end))
        else:
            spans.append((start, end))
    chunks, used = [f"SOURCE WINDOWS (absolute path: {path}):"], 0
    for start, end in spans:
        body = "\n".join(f"{idx:>5}| {lines[idx - 1]}" for idx in range(start, end + 1))
        block = f"--- lines {start}-{end} ---\n{body}"
        if used + len(block) + 1 > char_budget:
            chunks.append("... truncated; cover the remaining missed lines from Kover summary ...")
            break
        chunks.append(block)
        used += len(block) + 1
    return "\n".join(chunks)

def format_kover_for_prompt(
    source_path: str, gap, *, only_lines: set[int] | None = None, deferred_note: str = "",
    include_windows: bool = True, window_radius: int | None = None, window_char_budget: int | None = None,
) -> str:
    budget = get_config().prompt_context_char_budget
    sections = ["KOVER DELTA ONLY (cover these remaining uncovered lines — not the whole file)", gap_summary_text(gap)]
    if deferred_note.strip():
        sections += ["", deferred_note.strip()]
    if include_windows:
        sections += [
            "",
            uncovered_source_windows(
                source_path, gap,
                radius=_SOURCE_WINDOW_RADIUS if window_radius is None else window_radius,
                char_budget=_SOURCE_EMBED_CHAR_BUDGET if window_char_budget is None else window_char_budget,
                only_lines=only_lines,
            ),
        ]
    return "\n".join(sections)[:budget]

from pathlib import Path

def find_latest_jacoco_xml(module_dir: str | Path) -> str | None:
    """Newest JaCoCo/AndroidTest coverage XML under the module build dir."""
    candidates = iter_jacoco_xml_candidates(module_dir)
    return max(candidates, default=(0, None))[1]

def find_latest_jacoco_xml_for_context(
    project_root: str,
    gradle_tasks: list[str] | None,
    module_dir: str,
    source_file_path: str,
    source_code: str,
) -> str | None:
    _ = project_root, gradle_tasks, source_file_path, source_code
    return find_latest_jacoco_xml(module_dir)

def merge_coverage_gaps(
    unit_gap: CoverageGap | None,
    instrumented_gap: CoverageGap | None,
) -> CoverageGap | None:
    """Union coverage: line still actionable only if missed in every report that ran."""
    if unit_gap is None and instrumented_gap is None:
        return None
    if instrumented_gap is None:
        return unit_gap
    if unit_gap is None:
        return instrumented_gap

    if not instrumented_gap.has_any_coverage():
        return unit_gap

    instr_line_miss = set(instrumented_gap.missed_lines)
    instr_branch_miss = set(instrumented_gap.partial_branch_lines)
    still_lines = merge_missed_line_sets(set(unit_gap.missed_lines), instr_line_miss)
    still_branches = merge_missed_line_sets(
        set(unit_gap.partial_branch_lines), instr_branch_miss,
    )

    line_details = dict(unit_gap.line_details or {})
    if instrumented_gap.line_details:
        for line, detail in instrumented_gap.line_details.items():
            if line in still_lines or line in still_branches:
                line_details.setdefault(line, detail)

    return CoverageGap(
        source_file=unit_gap.source_file,
        package_name=unit_gap.package_name or instrumented_gap.package_name,
        missed_lines=still_lines,
        partial_branch_lines=still_branches,
        missed_methods=sorted(set(unit_gap.missed_methods) | set(instrumented_gap.missed_methods)),
        partial_branch_methods=sorted(
            set(unit_gap.partial_branch_methods) | set(instrumented_gap.partial_branch_methods)
        ),
        line_coverage=unit_gap.line_coverage,
        branch_coverage=unit_gap.branch_coverage,
        line_details=line_details or None,
    )

async def parse_latest_unified_coverage_gap(
    module_dir: str,
    source_file_path: str,
    source_code: str,
    *,
    project_root: str | None = None,
    gradle_tasks: list | None = None,
    include_instrumented: bool = True,
) -> tuple[CoverageGap | None, CoverageGap | None, CoverageGap | None]:
    """Return ``(merged, unit_gap, instrumented_gap)``."""
    if project_root:
        kover_xml = find_latest_kover_xml_for_source_context(
            project_root, gradle_tasks, module_dir, source_file_path, source_code,
        )
    else:
        kover_xml = find_latest_kover_xml(module_dir)
    unit_gap = parse_kover_gap(kover_xml, source_file_path, source_code) if kover_xml else None

    instr_gap = None
    if include_instrumented:
        jacoco_xml = find_latest_jacoco_xml_for_context(
            project_root or "", gradle_tasks, module_dir, source_file_path, source_code,
        )
        if jacoco_xml:
            instr_gap = parse_kover_gap(jacoco_xml, source_file_path, source_code)

    merged = merge_coverage_gaps(unit_gap, instr_gap)
    return merged, unit_gap, instr_gap

from dataclasses import dataclass
from typing import Any

from UnitTest_gen.core.io import compact_ranges

def coverage_improved(before, after, *, allow_bootstrap: bool = False) -> bool:
    if after is None:
        return False
    if before is None:
        return bool(allow_bootstrap and after.has_any_coverage())
    if (delta := compute_coverage_delta(before, after)) is None or delta.has_regression:
        return False
    return delta.improved

def _sorted_or_none(values) -> str:
    return str(sorted(values) or "none")

def coverage_gap_delta_summary(before, after, *, before_raw=None, after_raw=None) -> str:
    delta = compute_coverage_delta(before, after)
    if delta is None:
        return "### COVERAGE DELTA\nCoverage delta unavailable: missing Kover gap data."
    raw_before = before_raw if before_raw is not None else before
    raw_after = after_raw if after_raw is not None else after
    raw_delta = compute_coverage_delta(raw_before, raw_after)
    deferred_lines = deferred_branches = deferred_methods = []
    if raw_delta is not None:
        deferred_lines = sorted(set(delta.resolved_lines) - set(raw_delta.resolved_lines))
        deferred_branches = sorted(set(delta.resolved_branches) - set(raw_delta.resolved_branches))
        deferred_methods = sorted(set(delta.resolved_methods) - set(raw_delta.resolved_methods))
    rows = [
        "### COVERAGE DELTA",
        f"Before fingerprint (raw): {coverage_gap_fingerprint(raw_before)}",
        f"After fingerprint (raw): {coverage_gap_fingerprint(raw_after)}",
        f"Before fingerprint (defer-filtered): {coverage_gap_fingerprint(before)}",
        f"After fingerprint (defer-filtered): {coverage_gap_fingerprint(after)}",
        f"Line missed/covered (raw): {raw_before.line_coverage} -> {raw_after.line_coverage}",
        f"Line missed/covered (defer-filtered): {before.line_coverage} -> {after.line_coverage}",
        f"Branch missed/covered (raw): {raw_before.branch_coverage} -> {raw_after.branch_coverage}",
        f"Branch missed/covered (defer-filtered): {before.branch_coverage} -> {after.branch_coverage}",
        f"Remaining missed lines (raw): {_sorted_or_none(raw_before.missed_lines)} -> {_sorted_or_none(raw_after.missed_lines)}",
        f"Remaining missed lines: {_sorted_or_none(before.missed_lines)} -> {_sorted_or_none(after.missed_lines)}",
        f"Remaining branch lines (raw): {_sorted_or_none(raw_before.partial_branch_lines)} -> {_sorted_or_none(raw_after.partial_branch_lines)}",
        f"Remaining branch lines: {_sorted_or_none(before.partial_branch_lines)} -> {_sorted_or_none(after.partial_branch_lines)}",
        f"Resolved by Kover (raw) missed lines: {compact_ranges(raw_delta.resolved_lines) if raw_delta else 'none'}",
        f"Resolved by Kover (raw) branch lines: {compact_ranges(raw_delta.resolved_branches) if raw_delta else 'none'}",
        f"Resolved by Kover (raw) methods: {(raw_delta.resolved_methods or 'none') if raw_delta else 'none'}",
        f"Deferred/filtered out this run (missed lines): {deferred_lines or 'none'}",
        f"Deferred/filtered out this run (branch lines): {deferred_branches or 'none'}",
        f"Deferred/filtered out this run (methods): {deferred_methods or 'none'}",
        f"New missed lines (raw): {_sorted_or_none(raw_delta.new_missed_lines) if raw_delta else 'none'}",
        f"New missed branch lines (raw): {_sorted_or_none(raw_delta.new_missed_branches) if raw_delta else 'none'}",
        f"New missed methods (raw): {raw_delta.new_missed_methods if raw_delta else 'none'}",
    ]
    return "\n".join(rows)

def _short_text(text: str, *, limit: int = 120) -> str:
    collapsed = " ".join((text or "").split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"

@dataclass(frozen=True)
class AcceptanceSummaryParts:
    header: str
    deferred: str
    covered: str

def format_acceptance_summary_parts(*, plan, before_gap, after_gap, test_path: str) -> AcceptanceSummaryParts:
    header_lines = ["✅ Accepted: Gradle passed and Kover improved.", f"   Target: {test_path}"]
    testable = list(getattr(plan, "testable", ()) or ())
    if testable:
        header_lines.append("   Attempted (plan):")
        for item in testable:
            item_lines = list(getattr(item, "lines", ()) or ())
            methods = list(getattr(item, "methods", ()) or ())
            approach = _short_text(getattr(item, "approach", "") or "")
            bits = [f"lines={item_lines}"]
            if methods:
                bits.append(f"methods={methods}")
            row = "     - " + " ".join(bits)
            if approach:
                row += f" — {approach}"
            header_lines.append(row)
    else:
        header_lines.append("   Attempted (plan): (none recorded)")

    deferred_lines: list[str] = []
    not_testable = list(getattr(plan, "not_testable", ()) or ())
    if not_testable:
        deferred_lines.append("   Deferred (plan):")
        for item in not_testable:
            item_lines = list(getattr(item, "lines", ()) or ())
            reason = _short_text(
                str(getattr(item, "reason", "") or getattr(item, "category", "") or "not_testable"),
                limit=80,
            )
            deferred_lines.append(f"     - lines={item_lines} — {reason}")

    covered_lines = ["   Covered (Kover):"]
    delta = compute_coverage_delta(before_gap, after_gap)
    if delta is None or before_gap is None or after_gap is None:
        covered_lines.append("     - coverage delta unavailable")
    else:
        covered_lines.extend(
            [
                f"     - resolved lines: {compact_ranges(delta.resolved_lines)}",
                *(
                    [f"     - resolved branch lines: {compact_ranges(delta.resolved_branches)}"]
                    if delta.resolved_branches
                    else []
                ),
                f"     - resolved methods: {', '.join(delta.resolved_methods) if delta.resolved_methods else 'none'}",
                f"     - remaining missed lines: {compact_ranges(list(getattr(after_gap, 'missed_lines', ()) or []))}",
                f"     - line missed/covered: {before_gap.line_coverage} -> {after_gap.line_coverage}",
            ]
        )
    return AcceptanceSummaryParts(
        header="\n".join(header_lines),
        deferred="\n".join(deferred_lines),
        covered="\n".join(covered_lines),
    )

def format_acceptance_summary(*, plan, before_gap, after_gap, test_path: str) -> str:
    parts = format_acceptance_summary_parts(plan=plan, before_gap=before_gap, after_gap=after_gap, test_path=test_path)
    return "\n".join(section for section in (parts.header, parts.deferred, parts.covered) if section)

def build_python_coverage_summary(
    *,
    plan: Any = None,
    before_gap=None,
    after_gap=None,
    before_gap_raw=None,
    after_gap_raw=None,
    improved: bool | None = None,
    why_not_improved: str = "",
) -> dict[str, Any]:
    """JSON-serializable Attempted/Covered/Deferred from plan + Kover delta."""
    attempted = [
        {
            "lines": list(getattr(item, "lines", ()) or ()),
            "methods": list(getattr(item, "methods", ()) or ()),
            "what": str(getattr(item, "approach", "") or ""),
        }
        for item in getattr(plan, "testable", ()) or ()
    ]
    deferred = [
        {
            "lines": list(getattr(item, "lines", ()) or ()),
            "reason": str(getattr(item, "reason", "") or getattr(item, "category", "") or ""),
            "from_plan": True,
        }
        for item in getattr(plan, "not_testable", ()) or ()
    ]
    filtered_delta = compute_coverage_delta(before_gap, after_gap) if before_gap and after_gap else None
    raw_before = before_gap_raw if before_gap_raw is not None else before_gap
    raw_after = after_gap_raw if after_gap_raw is not None else after_gap
    raw_delta = compute_coverage_delta(raw_before, raw_after) if raw_before and raw_after else None
    covered: list[dict[str, Any]] = []
    if raw_delta is not None:
        if raw_delta.resolved_lines:
            covered.append({"lines": sorted(raw_delta.resolved_lines), "note": "resolved missed lines"})
        if raw_delta.resolved_branches:
            covered.append({"lines": sorted(raw_delta.resolved_branches), "note": "resolved partial-branch lines"})
    if filtered_delta is not None and raw_delta is not None:
        for lines, note in (
            (sorted(set(filtered_delta.resolved_lines) - set(raw_delta.resolved_lines)), "deferred via unreachable filter (not newly covered)"),
            (sorted(set(filtered_delta.resolved_branches) - set(raw_delta.resolved_branches)), "deferred branch probes via unreachable filter (not newly covered)"),
        ):
            if lines:
                covered.append({"lines": lines, "note": note})
    verdict = improved
    if verdict is None and filtered_delta is not None:
        verdict = bool(filtered_delta.improved and not filtered_delta.has_regression)
    return {
        "coverage_improved": verdict,
        "attempted": attempted,
        "covered": covered,
        "deferred": deferred,
        "why_not_improved": str(why_not_improved or ""),
    }

import os
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from UnitTest_gen.core import io as file_cache
from UnitTest_gen.core.io import save_json
from UnitTest_gen.core.io import coerce_int_set

PACKAGE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_STORE_PATH = PACKAGE_DIR / "data" / "unreachable_coverage.json"
SCHEMA_VERSION = "1.0"

def store_path() -> Path:
    override = os.environ.get("TESTGEN_UNREACHABLE_COVERAGE_JSON", "").strip()
    return Path(override).expanduser().resolve() if override else DEFAULT_STORE_PATH

def _empty() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "updated_at": "", "entries": []}

def load_store(path: Path | str | None = None) -> dict[str, Any]:
    target = Path(path) if path else store_path()
    payload = file_cache.read_json(target, default_factory=_empty)
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
        return _empty()
    payload.setdefault("schema_version", SCHEMA_VERSION)
    return payload

def save_store(payload: dict[str, Any], path: Path | str | None = None) -> Path:
    target = Path(path) if path else store_path()
    payload = dict(payload)
    payload["schema_version"] = SCHEMA_VERSION
    save_json(target, payload, touch_updated_at=True)
    return target

def deferred_lines_for_source(source_file_path: str, *, path: Path | str | None = None) -> set[int]:
    key = os.path.abspath(source_file_path)
    lines: set[int] = set()
    for entry in load_store(path).get("entries") or []:
        if os.path.abspath(str(entry.get("source") or "")) != key:
            continue
        lines |= coerce_int_set(entry.get("lines"))
    return lines

def filter_gap(gap, source_file_path: str, *, path: Path | str | None = None):
    """Return a gap with deferred lines removed (or the original gap if none)."""
    if gap is None:
        return None
    deferred = deferred_lines_for_source(source_file_path, path=path)
    if not deferred:
        return gap
    missed = [n for n in (getattr(gap, "missed_lines", None) or []) if int(n) not in deferred]
    partial = [n for n in (getattr(gap, "partial_branch_lines", None) or []) if int(n) not in deferred]
    methods = list(getattr(gap, "missed_methods", None) or [])
    partial_methods = list(getattr(gap, "partial_branch_methods", None) or [])
    try:
        return replace(
            gap,
            missed_lines=missed,
            partial_branch_lines=partial,
            missed_methods=methods,
            partial_branch_methods=partial_methods,
        )
    except TypeError:
        filtered = deepcopy(gap)
        filtered.missed_lines = missed
        filtered.partial_branch_lines = partial
        return filtered

def gap_has_actionable_lines(gap) -> bool:
    if gap is None:
        return True
    return bool(getattr(gap, "missed_lines", None) or getattr(gap, "partial_branch_lines", None))

def _entry_line_key(source: str, lines) -> tuple[str, tuple[int, ...]]:
    return (os.path.abspath(source or ""), tuple(sorted(coerce_int_set(lines))))

def merge_unreachable_entries(entries: list[dict[str, Any]], *, path: Path | str | None = None) -> Path:
    """Merge unreachable rows by source+sorted lines; upsert reason/category when key exists."""
    store = load_store(path)
    by_key: dict[tuple[str, tuple[int, ...]], dict[str, Any]] = {}
    ordered: list[dict[str, Any]] = []
    for row in store.get("entries") or []:
        if not isinstance(row, dict):
            continue
        key = _entry_line_key(str(row.get("source") or ""), row.get("lines"))
        if not key[0] or not key[1] or key in by_key:
            continue
        by_key[key] = row
        ordered.append(row)

    for entry in entries:
        source = os.path.abspath(str(entry.get("source") or ""))
        lines = tuple(sorted(coerce_int_set(entry.get("lines"))))
        key = (source, lines)
        if not source or not lines:
            continue
        reason = str(entry.get("reason") or "").strip()
        category = str(entry.get("category") or "not_unit_testable").strip() or "not_unit_testable"
        methods = list(entry.get("methods") or [])
        evidence = str(entry.get("evidence") or "plan_agent")
        recorded = datetime.now().isoformat(timespec="seconds")
        if key in by_key:
            row = by_key[key]
            if reason and (
                not str(row.get("reason") or "").strip()
                or category == "kover_residual_branch"
                or len(reason) > len(str(row.get("reason") or ""))
            ):
                row.update(
                    reason=reason,
                    category=category,
                    methods=methods or row.get("methods"),
                    evidence=evidence,
                    recorded_at=recorded,
                )
            continue
        row = {
            "source": source,
            "source_name": entry.get("source_name") or os.path.basename(source),
            "module": entry.get("module") or "",
            "lines": list(lines),
            "methods": methods,
            "reason": reason,
            "category": category,
            "evidence": evidence,
            "recorded_at": recorded,
        }
        by_key[key] = row
        ordered.append(row)
    store["entries"] = ordered
    return save_store(store, path)

def reconcile_unreachable_entries(
    *,
    source_file_path: str,
    attempted_lines: set[int] | list[int] | tuple[int, ...],
    covered_lines: set[int] | list[int] | tuple[int, ...] = (),
    accepted: bool = False,
    path: Path | str | None = None,
) -> Path | None:
    """Remove stale deferred rows superseded by newer run evidence."""
    evidence_lines = coerce_int_set(attempted_lines) | coerce_int_set(covered_lines)
    if not evidence_lines:
        return None
    store = load_store(path)
    src = os.path.abspath(source_file_path)
    kept: list[dict[str, Any]] = []
    changed = False
    for row in store.get("entries") or []:
        if not isinstance(row, dict):
            continue
        row_source = os.path.abspath(str(row.get("source") or ""))
        row_category = str(row.get("category") or "").strip()
        row_lines = coerce_int_set(row.get("lines"))
        should_drop = (
            row_source == src
            and row_category == "plan_empty_or_unparseable"
            and bool(row_lines & evidence_lines)
            and (accepted or bool(coerce_int_set(covered_lines)))
        )
        if should_drop:
            changed = True
            continue
        kept.append(row)
    if not changed:
        return None
    store["entries"] = kept
    return save_store(store, path)

def entries_from_plan_not_testable(
    not_testable: list[dict[str, Any]],
    *,
    source_file_path: str,
    module: str = "",
) -> list[dict[str, Any]]:
    source = os.path.abspath(source_file_path)
    rows: list[dict[str, Any]] = []
    for item in not_testable or []:
        lines = list(item.get("lines") or [])
        category = str(item.get("category") or "not_unit_testable").strip() or "not_unit_testable"
        reason = str(item.get("reason") or "").strip()
        if not reason:
            reason = f"Deferred ({category}): lines {lines or 'none'} marked not_testable by planner."
        rows.append(
            {
                "source": source,
                "source_name": os.path.basename(source),
                "module": module,
                "lines": lines,
                "methods": list(item.get("methods") or []),
                "reason": reason,
                "category": category,
                "evidence": "plan_residual_demote" if category == "kover_residual_branch" else "plan_agent",
            }
        )
    return rows

def _item_field(item: Any, key: str, default: Any = "") -> Any:
    return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)

def _truncate(text: str, *, limit: int = 240) -> str:
    collapsed = " ".join((text or "").split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 3].rstrip() + "..."

def _coverage_attempt_summary(before_gap, after_gap) -> str:
    before_line = getattr(before_gap, "line_coverage", None) or (0, 0)
    after_line = getattr(after_gap, "line_coverage", None) or (0, 0)
    before_branch = getattr(before_gap, "branch_coverage", None) or (0, 0)
    after_branch = getattr(after_gap, "branch_coverage", None) or (0, 0)
    before_missed = coerce_int_set(getattr(before_gap, "missed_lines", None))
    after_missed = coerce_int_set(getattr(after_gap, "missed_lines", None))
    before_partial = coerce_int_set(getattr(before_gap, "partial_branch_lines", None))
    after_partial = coerce_int_set(getattr(after_gap, "partial_branch_lines", None))
    return (
        f"Kover line missed/covered {tuple(before_line)} -> {tuple(after_line)}; "
        f"branch missed/covered {tuple(before_branch)} -> {tuple(after_branch)}; "
        f"resolved lines={sorted(before_missed - after_missed) or 'none'}; "
        f"resolved branches={sorted(before_partial - after_partial) or 'none'}."
    )

def entries_from_attempted_no_delta(
    testable: list[Any],
    *,
    before_gap,
    after_gap,
    source_file_path: str,
    module: str = "",
) -> list[dict[str, Any]]:
    """Legacy helper: planned lines still open after Python Coverage no-delta."""
    if after_gap is None or not testable:
        return []
    still_open = coerce_int_set(getattr(after_gap, "missed_lines", None)) | coerce_int_set(
        getattr(after_gap, "partial_branch_lines", None)
    )
    if not still_open:
        return []
    source = os.path.abspath(source_file_path)
    summary = _coverage_attempt_summary(before_gap, after_gap)
    rows: list[dict[str, Any]] = []
    for item in testable:
        planned = coerce_int_set(_item_field(item, "lines", []))
        if not (open_lines := sorted(planned & still_open)):
            continue
        approach = _truncate(str(_item_field(item, "approach", "") or ""))
        fixture = _truncate(str(_item_field(item, "fixture_hint", "") or ""))
        reason_parts = [
            "Coder/fix attempted this plan item; Gradle/JUnit passed; Kover acceptance did not resolve these still-open probes.",
            summary,
        ]
        if approach:
            reason_parts.append(f"Planned approach: {approach}")
        if fixture:
            reason_parts.append(f"Fixture hint: {fixture}")
        reason_parts.append(f"Still-open lines after attempt: {open_lines}.")
        rows.append(
            {
                "source": source,
                "source_name": os.path.basename(source),
                "module": module,
                "lines": open_lines,
                "methods": list(_item_field(item, "methods", []) or []),
                "reason": " ".join(reason_parts),
                "category": "attempted_no_coverage_delta",
                "evidence": "coverage_no_delta",
            }
        )
    return rows
