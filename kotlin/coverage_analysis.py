# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Parses Kover reports and describes source-specific coverage gaps.
import json
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from UnitTest_gen.kotlin.gradle_analysis.junit import text_fingerprint
from UnitTest_gen.kotlin.kotlin_analysis import kotlin_package_name
from UnitTest_gen.kotlin.static_analysis import analyze_kotlin_code

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
        return bool(
            self.missed_lines
            or self.partial_branch_lines
            or self.missed_methods
            or self.partial_branch_methods
        )

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


def kover_key(package_name: str, source_file_name: str) -> tuple[str, str]:
    return (package_name.replace(".", "/"), source_file_name)


def is_generated_kover_source(package_name: str, source_file_name: str) -> bool:
    package_path = package_name.replace(".", "/")
    source_name = source_file_name.removesuffix(".kt").removesuffix(".java")
    if package_path.startswith("dagger/hilt/internal"):
        return True
    if "build/generated" in package_path:
        return True
    if source_name in {"BuildConfig", "R", "Manifest"}:
        return True
    if source_name.startswith("R$") or source_name.startswith("_"):
        return True
    if "Aggregated" in source_name or "Hilt" in source_name and package_path.startswith("hilt_aggregated_deps"):
        return True
    return False


def module_hint_from_kover_xml(path: Path, root: Path) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError:
        return str(path.parent)
    parts = list(relative.parts)
    if "build" in parts:
        return str(Path(*parts[: parts.index("build")])) or "."
    return str(relative.parent)


def _line_gap_kind(missed_instructions: int, missed_branches: int, covered_instructions: int = 0) -> str:
    if missed_instructions > 0 and covered_instructions == 0 and missed_branches > 0:
        return "line_and_branch"
    if missed_branches > 0:
        return "branch"
    return "line"


def _parse_sourcefile_line_gaps(source_node) -> tuple[set[int], set[int], dict[int, LineGapDetail]]:
    missed_lines: set[int] = set()
    missed_branches: set[int] = set()
    line_details: dict[int, LineGapDetail] = {}
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
                line_no=line_no,
                missed_instructions=missed_instructions,
                missed_branches=missed_branch_count,
                covered_branches=covered_branch_count,
                gap_kind=_line_gap_kind(missed_instructions, missed_branch_count, covered_instructions),
            )
    return missed_lines, missed_branches, line_details


def _find_package_source_node(root, source_file_name: str, expected_package: str = ""):
    for candidate_package in root.findall("package"):
        package_name = candidate_package.attrib.get("name", "")
        candidate_source = candidate_package.find(f"sourcefile[@name='{source_file_name}']")
        if candidate_source is None:
            continue
        if expected_package and package_name != expected_package:
            continue
        return candidate_package, candidate_source
    if expected_package:
        return None, None
    for candidate_package in root.findall("package"):
        candidate_source = candidate_package.find(f"sourcefile[@name='{source_file_name}']")
        if candidate_source is not None:
            return candidate_package, candidate_source
    return None, None


def find_latest_kover_xml(module_dir: str | Path) -> str | None:
    """Find the most recently modified Kover XML coverage report in a module.
    
    Searches the module's build/reports/kover directory tree for XML files and
    returns the path to the most recently modified one.
    
    Args:
        module_dir: Path to a Gradle module directory
        
    Returns:
        Absolute path to the latest .xml file, or None if no coverage reports found
        
    Example:
        find_latest_kover_xml('/project/common/api') 
        -> '/project/common/api/build/reports/kover/report-dev-debug/index.xml'
    """
    report_root = os.path.join(str(module_dir), "build", "reports", "kover")
    candidates = []

    if os.path.isdir(report_root):
        for root, _, files in os.walk(report_root):
            for file_name in files:
                if file_name.endswith(".xml"):
                    path = os.path.join(root, file_name)
                    candidates.append((os.path.getmtime(path), path))

    if not candidates:
        return None

    candidates.sort(reverse=True)
    return candidates[0][1]


def package_path_from_source(source_code: str) -> str:
    """Extract the Kotlin package name from source code as a file path.
    
    Parses the source code to find the package declaration and converts it to
    a file path format (dots become slashes).
    
    Args:
        source_code: Kotlin source code as a string
        
    Returns:
        Package as a file path (e.g., 'com/example/app') or empty string
        if no package declaration found
        
    Example:
        source = 'package com.example.app\\nclass Activity { }'
        package_path_from_source(source)
        -> 'com/example/app'
    """
    return kotlin_package_name(source_code or "").replace(".", "/")


def parse_kover_xml(
    path: Path | str,
    root: Path | str,
    *,
    include_generated: bool = False,
) -> dict[tuple[str, str], KoverGap]:
    xml_path = Path(path)
    root_path = Path(root)
    try:
        xml_root = ET.parse(xml_path).getroot()
    except (ET.ParseError, OSError):
        return {}
    module_hint = module_hint_from_kover_xml(xml_path, root_path)
    gaps: dict[tuple[str, str], KoverGap] = {}
    for package in xml_root.findall("package"):
        package_name = package.attrib.get("name", "")
        for sourcefile in package.findall("sourcefile"):
            source_file_name = sourcefile.attrib.get("name", "")
            if not include_generated and is_generated_kover_source(package_name, source_file_name):
                continue
            missed_lines, missed_branches, _ = _parse_sourcefile_line_gaps(sourcefile)
            _, line_covered = counter_pair(sourcefile, "LINE")
            _, branch_covered = counter_pair(sourcefile, "BRANCH")
            if not missed_lines and not missed_branches:
                continue
            key = kover_key(package_name, source_file_name)
            gaps[key] = KoverGap(
                key=key,
                package_name=package_name,
                source_file_name=source_file_name,
                module_hint=module_hint,
                missed_lines=missed_lines,
                missed_branches=missed_branches,
                line_covered=line_covered,
                branch_covered=branch_covered,
            )
    return gaps


def merge_kover_snapshots(
    snapshots: list[dict[tuple[str, str], KoverGap]],
) -> dict[tuple[str, str], KoverGap]:
    merged: dict[tuple[str, str], KoverGap] = {}
    for snapshot in snapshots:
        for key, gap in snapshot.items():
            previous = merged.get(key)
            if previous is None:
                merged[key] = gap
                continue
            current_score = gap.line_covered + gap.branch_covered
            previous_score = previous.line_covered + previous.branch_covered
            if current_score > previous_score:
                merged[key] = gap
    return merged


def load_kover_gaps(
    paths: list[Path | str],
    root: Path | str,
    *,
    include_generated: bool = False,
) -> dict[tuple[str, str], KoverGap]:
    snapshots = [
        parse_kover_xml(path, root, include_generated=include_generated)
        for path in paths
    ]
    return merge_kover_snapshots(snapshots)


def load_kover_source_counters(
    xml_paths: list[Path | str],
) -> dict[tuple[str, str], tuple[int, int, int, int]]:
    counters: dict[tuple[str, str], tuple[int, int, int, int]] = {}
    for xml_path in xml_paths:
        try:
            root = ET.parse(xml_path).getroot()
        except (ET.ParseError, OSError):
            continue
        for package in root.findall("package"):
            pkg = package.attrib.get("name", "")
            for sourcefile in package.findall("sourcefile"):
                name = sourcefile.attrib.get("name", "")
                line_missed, line_covered = counter_pair(sourcefile, "LINE")
                branch_missed, branch_covered = counter_pair(sourcefile, "BRANCH")
                key = (pkg, name)
                previous = counters.get(key)
                if previous is None:
                    counters[key] = (line_missed, line_covered, branch_missed, branch_covered)
                    continue
                if line_covered + branch_covered > previous[1] + previous[3]:
                    counters[key] = (line_missed, line_covered, branch_missed, branch_covered)
    return counters


def parse_kover_gap(xml_path: str, source_file_path: str, source_code: str) -> CoverageGap | None:
    if not xml_path or not os.path.exists(xml_path):
        return None

    source_file_name = os.path.basename(source_file_path)
    expected_package = package_path_from_source(source_code)

    try:
        root = ET.parse(xml_path).getroot()
    except (ET.ParseError, OSError):
        return None

    package_node, source_node = _find_package_source_node(root, source_file_name, expected_package)
    if source_node is None or package_node is None:
        return None

    missed_line_set, partial_branch_set, line_details = _parse_sourcefile_line_gaps(source_node)
    missed_lines = sorted(missed_line_set)
    partial_branch_lines = sorted(partial_branch_set)

    missed_methods = []
    partial_branch_methods = []
    for class_node in package_node.findall("class"):
        if class_node.attrib.get("sourcefilename") != source_file_name:
            continue
        for method in class_node.findall("method"):
            counters = {counter.attrib.get("type"): counter for counter in method.findall("counter")}
            line_counter = counters.get("LINE")
            branch_counter = counters.get("BRANCH")

            if line_counter is not None and int(line_counter.attrib.get("missed", "0") or "0") > 0:
                missed_methods.append(method.attrib.get("name", ""))
            if branch_counter is not None and int(branch_counter.attrib.get("missed", "0") or "0") > 0:
                partial_branch_methods.append(method.attrib.get("name", ""))

    line_coverage = counter_pair(source_node, "LINE")
    branch_coverage = counter_pair(source_node, "BRANCH")

    return CoverageGap(
        source_file=source_file_name,
        package_name=package_node.attrib.get("name", ""),
        missed_lines=sorted(set(missed_lines)),
        partial_branch_lines=sorted(set(partial_branch_lines)),
        missed_methods=sorted(set(filter(None, missed_methods))),
        partial_branch_methods=sorted(set(filter(None, partial_branch_methods))),
        line_coverage=line_coverage,
        branch_coverage=branch_coverage,
        line_details=line_details,
    )


def counter_pair(node, counter_type: str) -> tuple[int, int]:
    counter = node.find(f"counter[@type='{counter_type}']")
    if counter is None:
        return 0, 0
    return (
        int(counter.attrib.get("missed", "0") or "0"),
        int(counter.attrib.get("covered", "0") or "0"),
    )


def private_declaration_context(source_code: str) -> tuple[set[str], set[int]]:
    private_names: set[str] = set()
    private_lines: set[int] = set()
    report = analyze_kotlin_code(source_code or "")
    for declaration in report.declarations:
        if declaration.visibility != "private":
            continue
        private_names.add(declaration.name)
        private_lines.update(range(declaration.span.start_line, declaration.span.end_line + 1))
    return private_names, private_lines
















def coverage_gap_context(
    gap: CoverageGap,
    source_code: str,
    context_radius: int = 2,
    max_lines: int = 120,
    cluster_plan_context: str = "",
    selected_lines: list[int] | None = None,
    selected_branch_lines: list[int] | None = None,
    selected_methods: list[str] | None = None,
    include_private_with_public_path: bool = False,
    coverage_buckets: tuple[str, ...] | list[str] | None = None,
) -> str:
    source_lines = source_code.splitlines()
    prompt_missed_lines = gap.missed_lines if selected_lines is None else selected_lines
    prompt_branch_lines = gap.partial_branch_lines if selected_branch_lines is None else selected_branch_lines
    interesting = sorted(set(prompt_missed_lines + prompt_branch_lines))
    private_names, private_lines = private_declaration_context(source_code)
    private_gap_lines = sorted(set(interesting) & private_lines)
    line_details = getattr(gap, "line_details", None) or {}
    selected = []

    for line_no in interesting:
        detail = line_details.get(line_no)
        is_branch_only_line = bool(
            detail
            and detail.gap_kind == "branch"
            or (line_no in prompt_branch_lines and line_no not in prompt_missed_lines)
        )
        radius = max(context_radius, 5) if is_branch_only_line else context_radius
        for candidate in range(max(1, line_no - radius), min(len(source_lines), line_no + radius) + 1):
            if candidate in private_lines and not (
                include_private_with_public_path and cluster_plan_context and candidate in interesting
            ):
                continue
            selected.append(candidate)

    selected = sorted(set(selected))[:max_lines]
    line_blocks = []
    for line_no in selected:
        if not (1 <= line_no <= len(source_lines)):
            continue
        detail = line_details.get(line_no)
        prefix = f"{line_no}"
        if detail:
            if detail.gap_kind == "branch":
                prefix = f"{line_no} [BRANCH-ONLY mb={detail.missed_branches} cb={detail.covered_branches}]"
            elif detail.gap_kind == "line_and_branch":
                prefix = (
                    f"{line_no} [LINE+BRANCH mi={detail.missed_instructions} "
                    f"mb={detail.missed_branches} cb={detail.covered_branches}]"
                )
            elif detail.missed_instructions > 0:
                prefix = f"{line_no} [LINE mi={detail.missed_instructions}]"
        line_blocks.append(f"{prefix}: {source_lines[line_no - 1]}")

    line_missed, line_covered = gap.line_coverage
    branch_missed, branch_covered = gap.branch_coverage
    if line_missed == 0 and branch_missed > 0:
        gap_type = "branch-only"
    elif line_missed > 0 and branch_missed == 0:
        gap_type = "line-only"
    elif line_missed > 0 and branch_missed > 0:
        gap_type = "line-and-branch"
    else:
        gap_type = "none"

    parts = [
            "### KOVER COVERAGE GAP TARGET",
            f"Source file: {gap.source_file}",
            f"Package: {gap.package_name}",
            f"Coverage gap type: {gap_type}",
            f"Line coverage missed/covered: {line_missed}/{line_covered}",
            f"Branch coverage missed/covered: {branch_missed}/{branch_covered}",
            "CLI coverage buckets: " + (", ".join(coverage_buckets or ()) or "none"),
    ]
    if cluster_plan_context:
        parts.extend(["", cluster_plan_context])
    prompt_methods = gap.missed_methods if selected_methods is None else selected_methods
    public_missed_methods = [method for method in prompt_methods if method not in private_names]
    public_branch_methods = [
        method
        for method in gap.partial_branch_methods
        if method not in private_names and (selected_methods is None or method in selected_methods)
    ]
    parts.extend(
        [
            f"Selected missed lines for this generation: {compact_ranges(prompt_missed_lines)}",
            f"Selected missed branch lines for this generation: {compact_ranges(prompt_branch_lines)}",
            "Public/non-private methods with missed lines: " + (", ".join(public_missed_methods[:30]) or "none"),
            "Public/non-private methods with missed branches: " + (", ".join(public_branch_methods[:30]) or "none"),
            "Private implementation gap ranges: " + (compact_ranges(private_gap_lines) if private_gap_lines else "none"),
            "",
            "Public/reachable source excerpts around missed lines/branches:",
            "\n".join(line_blocks) if line_blocks else "No line excerpts available.",
        ]
    )
    return "\n".join(parts)


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
    return text_fingerprint(json.dumps(payload, sort_keys=True))


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

    @property
    def line_improved(self) -> bool:
        return bool(
            self.resolved_lines
            or self.after_line_missed < self.before_line_missed
            or self.after_line_covered > self.before_line_covered
        )

    @property
    def branch_improved(self) -> bool:
        return bool(
            self.resolved_branches
            or self.after_branch_missed < self.before_branch_missed
            or self.after_branch_covered > self.before_branch_covered
        )

    @property
    def method_improved(self) -> bool:
        return bool(self.resolved_methods)

    @property
    def has_regression(self) -> bool:
        return bool(
            self.new_missed_lines
            or self.new_missed_branches
            or self.new_missed_methods
            or self.after_line_missed > self.before_line_missed
            or self.after_branch_missed > self.before_branch_missed
            or self.after_line_covered < self.before_line_covered
            or self.after_branch_covered < self.before_branch_covered
        )

    @property
    def improved(self) -> bool:
        return bool(
            self.resolved_lines
            or self.resolved_branches
            or self.resolved_methods
            or self.after_line_missed < self.before_line_missed
            or self.after_branch_missed < self.before_branch_missed
            or self.after_line_covered > self.before_line_covered
            or self.after_branch_covered > self.before_branch_covered
        )


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
        resolved_methods=sorted(
            set(before.missed_methods + before.partial_branch_methods)
            - set(after.missed_methods + after.partial_branch_methods)
        ),
        new_missed_methods=sorted(
            set(after.missed_methods + after.partial_branch_methods)
            - set(before.missed_methods + before.partial_branch_methods)
        ),
        before_line_missed=before_line_missed,
        before_line_covered=before_line_covered,
        after_line_missed=after_line_missed,
        after_line_covered=after_line_covered,
        before_branch_missed=before_branch_missed,
        before_branch_covered=before_branch_covered,
        after_branch_missed=after_branch_missed,
        after_branch_covered=after_branch_covered,
    )


def coverage_improved(before, after, *, allow_bootstrap: bool = False) -> bool:
    if after is None:
        return False
    if before is None:
        return bool(allow_bootstrap and after.has_any_coverage())
    delta = compute_coverage_delta(before, after)
    if delta is None:
        return False
    if delta.has_regression:
        return False
    return delta.improved


def coverage_gap_improved(before, after) -> bool:
    return coverage_improved(before, after)


def coverage_gap_delta_summary(before, after) -> str:
    delta = compute_coverage_delta(before, after)
    if delta is None:
        return "### KOVER COVERAGE DELTA\nCoverage delta unavailable: missing Kover gap data."
    return "\n".join(
        [
            "### KOVER COVERAGE DELTA",
            f"Before fingerprint: {coverage_gap_fingerprint(before)}",
            f"After fingerprint: {coverage_gap_fingerprint(after)}",
            f"Line missed/covered: {before.line_coverage} -> {after.line_coverage}",
            f"Branch missed/covered: {before.branch_coverage} -> {after.branch_coverage}",
            f"Remaining missed lines: {sorted(before.missed_lines) or 'none'} -> {sorted(after.missed_lines) or 'none'}",
            f"Remaining branch lines: {sorted(before.partial_branch_lines) or 'none'} -> {sorted(after.partial_branch_lines) or 'none'}",
            f"Remaining missed methods: {sorted(set(before.missed_methods + before.partial_branch_methods)) or 'none'} -> {sorted(set(after.missed_methods + after.partial_branch_methods)) or 'none'}",
            f"Resolved missed lines: {sorted(delta.resolved_lines) or 'none'}",
            f"New missed lines: {sorted(delta.new_missed_lines) or 'none'}",
            f"Resolved branch lines: {sorted(delta.resolved_branches) or 'none'}",
            f"New missed branch lines: {sorted(delta.new_missed_branches) or 'none'}",
            f"Resolved missed methods: {delta.resolved_methods or 'none'}",
            f"New missed methods: {delta.new_missed_methods or 'none'}",
        ]
    )


def coverage_acceptance_diagnostics(before, after) -> str:
    delta = compute_coverage_delta(before, after)
    if delta is None:
        return "Coverage acceptance unavailable: missing Kover gap data."
    return "\n".join(
        [
            "### COVERAGE ACCEPTANCE DIAGNOSTICS",
            f"Line improved: {delta.line_improved}",
            f"Branch improved: {delta.branch_improved}",
            f"Method improved: {delta.method_improved}",
            f"Only already-covered code likely changed: {not (delta.line_improved or delta.branch_improved or delta.method_improved)}",
            f"Coverage regression: {delta.has_regression}",
            f"Resolved line count: {len(delta.resolved_lines)}",
            f"New missed line count: {len(delta.new_missed_lines)}",
            f"Resolved branch count: {len(delta.resolved_branches)}",
            f"New missed branch count: {len(delta.new_missed_branches)}",
            f"Resolved method count: {len(delta.resolved_methods)}",
            f"New missed method count: {len(delta.new_missed_methods)}",
            "Acceptance rule: accept only when line/branch/method coverage improves without new missed lines, branches, methods, or counter regression.",
        ]
    )


def kover_report_module_dirs(project_root: str, gradle_tasks: list | None, fallback_module_dir: str) -> list[str]:
    """Return module directories that can own the requested Kover XML report.

    Aggregate Kover tasks can be run from a parent module while the source file
    lives in a child module, for example source under feature/login with
    :feature:koverXmlReport. In that case the XML is under feature/build, not
    feature/login/build.
    """
    module_dirs: list[str] = []
    project_root_path = Path(project_root).resolve()

    for task in gradle_tasks or []:
        task_text = str(task).strip()
        if "kover" not in task_text.lower():
            continue

        task_parts = [part for part in task_text.split(":") if part]
        if len(task_parts) <= 1:
            candidate = str(project_root_path)
        else:
            candidate = str(project_root_path.joinpath(*task_parts[:-1]))

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
    project_root: str,
    gradle_tasks: list | None,
    fallback_module_dir: str,
    source_file_path: str,
    source_code: str,
) -> str | None:
    """Return the newest task-context report containing the exact source/package."""
    task_hints = [
        match.group(1).lower()
        for task in gradle_tasks or []
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


def kover_searched_roots_text(project_root: str, gradle_tasks: list | None, fallback_module_dir: str) -> str:
    return ", ".join(
        os.path.join(candidate, "build", "reports", "kover")
        for candidate in kover_report_module_dirs(project_root, gradle_tasks, fallback_module_dir)
    )


def fallback_kover_gradle_tasks(project_root: str, source_file_path: str, gradle_tasks: list | None = None) -> list[str]:
    """Return module-local unit-test + Kover report tasks when aggregate tasks are unavailable."""
    from UnitTest_gen.kotlin.project_context import find_owning_module_dir, module_path_for_dir

    module_dir = find_owning_module_dir(project_root, source_file_path)
    module_path = module_path_for_dir(project_root, module_dir)
    if not module_path:
        # ponytail: path heuristic when Gradle module resolution fails in lightweight tests
        normalized = source_file_path.replace("\\", "/")
        for segment in ("common", "app", "feature"):
            token = f"/{segment}/"
            if token in normalized:
                module_path = f":{segment}"
                break
    if not module_path:
        return ["testDevDebugUnitTest", "koverXmlReportDevDebug"]
    return [
        f"{module_path}:testDevDebugUnitTest",
        f"{module_path}:koverXmlReportDevDebug",
    ]


def gradle_missing_task_hint(gradle_output: str) -> str:
    match = re.search(r"Missing Gradle task:\s*(:[^\s]+)", gradle_output or "")
    if not match:
        return ""
    missing = match.group(1)
    return (
        f"Gradle reported missing task `{missing}`. "
        "Retry with module-local tasks such as `:module:testDevDebugUnitTest` and `:module:koverXmlReportDevDebug`."
    )


def resolve_gradle_tasks_after_missing_kover_task(
    project_root: str,
    source_file_path: str,
    gradle_tasks: list,
    gradle_output: str,
) -> list[str] | None:
    if "Missing Gradle task" not in (gradle_output or ""):
        return None
    fallback = fallback_kover_gradle_tasks(project_root, source_file_path, gradle_tasks)
    if fallback == list(gradle_tasks or []):
        return None
    return fallback


def discover_kover_xml_or_none(
    project_root: str,
    gradle_tasks: list | None,
    module_dir: str,
    source_file_path: str = "",
    source_code: str = "",
) -> tuple[str | None, str]:
    if source_file_path:
        matched = find_latest_kover_xml_for_source_context(
            project_root,
            gradle_tasks,
            module_dir,
            source_file_path,
            source_code,
        )
        return matched, kover_searched_roots_text(project_root, gradle_tasks, module_dir)
    return (
        find_latest_kover_xml_for_context(project_root, gradle_tasks, module_dir),
        kover_searched_roots_text(project_root, gradle_tasks, module_dir),
    )


async def parse_latest_coverage_gap(
    module_dir: str,
    source_file_path: str,
    source_code: str,
    project_root: str | None = None,
    gradle_tasks: list | None = None,
):
    if project_root:
        kover_xml = find_latest_kover_xml_for_source_context(
            project_root,
            gradle_tasks,
            module_dir,
            source_file_path,
            source_code,
        )
        return parse_kover_gap(kover_xml, source_file_path, source_code) if kover_xml else None

    kover_xml = find_latest_kover_xml(module_dir)
    if not kover_xml:
        return None
    return parse_kover_gap(kover_xml, source_file_path, source_code)


def compact_ranges(numbers) -> str:
    numbers = sorted(set(numbers))
    if not numbers:
        return "none"

    ranges = []
    start = previous = numbers[0]

    for value in numbers[1:]:
        if value == previous + 1:
            previous = value
            continue
        ranges.append((start, previous))
        start = previous = value

    ranges.append((start, previous))

    return ", ".join(
        str(start) if start == end else f"{start}-{end}"
        for start, end in ranges
    )
