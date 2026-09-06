"""Prompt skeletons, decision tables, coder/fix prompt builders."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1]
SKELETON_DIR = PACKAGE_DIR / "data" / "prompt_skeletons"
_SECTION_RE = re.compile(r"^## ([A-Z_][A-Z0-9_]*)\s*$", re.MULTILINE)

@lru_cache
def load_skeleton(name: str) -> str:
    return (SKELETON_DIR / name).read_text(encoding="utf-8")

@lru_cache
def load_catalog_sections(catalog: str = "prompt_catalog.md") -> dict[str, str]:
    text = load_skeleton(catalog)
    sections: dict[str, str] = {}
    matches = list(_SECTION_RE.finditer(text))
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[match.group(1)] = text[start:end].strip()
    return sections

def catalog_section(name: str) -> str:
    return load_catalog_sections()[name]

from functools import lru_cache

import yaml

GENERATION_SYSTEM_PROMPT = catalog_section("GENERATION_SYSTEM_PROMPT")
INSTRUMENTED_GENERATION_SYSTEM_PROMPT = catalog_section("INSTRUMENTED_GENERATION_SYSTEM_PROMPT")
TEST_QUALITY_RULES = catalog_section("TEST_QUALITY_RULES")
TEST_METHOD_NAMING_RULE = catalog_section("TEST_METHOD_NAMING_RULE")
CANONICAL_PIPELINE_CONTRACT = catalog_section("CANONICAL_PIPELINE_CONTRACT")

_CORE_PROSE = catalog_section("CORE_GENERATION_RULES")
_KOTLIN_ANDROID_PROSE = catalog_section("KOTLIN_ANDROID_TEST_RULES")
_INSTRUMENTED_KOTLIN_ANDROID_PROSE = catalog_section("INSTRUMENTED_KOTLIN_ANDROID_TEST_RULES")

CORE_GENERATION_RULES = "\n\n".join(
    part.strip()
    for part in (_CORE_PROSE, TEST_METHOD_NAMING_RULE)
    if part and part.strip()
)

KOTLIN_ANDROID_TEST_RULES = _KOTLIN_ANDROID_PROSE
INSTRUMENTED_KOTLIN_ANDROID_TEST_RULES = _INSTRUMENTED_KOTLIN_ANDROID_PROSE

# Plan-only stop contract (EXIT tables deleted — Python owns coder/fix verify loops).
PLAN_EXIT_STRATEGY = """## PLAN EXIT
One-pass classify DELTA → Write YAML+body to PLAN OUTPUT FILE (optional: missing harness under test roots) → reply PLAN_SAVED → stop.
Do not paste the plan into chat. Do not re-analyze, second-guess, or start another pass.
Stuck or unsure: put remaining lines in not_testable with a concrete category, then Write + PLAN_SAVED.
Empty testable with empty not_testable is invalid — every DELTA line must be classified once."""

FORBIDDEN_LOCAL_JVM_TEST_SYMBOLS: tuple[str, ...] = (
    "org.junit.jupiter",
    "androidx.test.ext.junit.runners.AndroidJUnit4",
)

_DEFAULT_REPAIR = "Repair the stable invalid pattern before running Gradle."

@lru_cache
def _validation_repair_intents() -> dict[str, str]:
    data = yaml.safe_load(load_skeleton("validation_repair_intents.yaml"))
    return data if isinstance(data, dict) else {}

VALIDATION_REPAIR_INTENTS: dict[str, str] = _validation_repair_intents()

def validation_repair_intent(category: str) -> str:
    return VALIDATION_REPAIR_INTENTS.get(category, _DEFAULT_REPAIR)

from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import yaml

TABLES_DIR = PACKAGE_DIR / "data" / "prompt_skeletons" / "decision_tables"
MANIFEST_NAME = "manifest.yaml"

def _escape_cell(value: object) -> str:
    """Escape Markdown table cell content.

    Bare ``|`` breaks tables; leave doubled ``||`` as the word ``OR`` via
    pre-normalization so logical-or in rules stays readable.
    """
    text = str(value or "").replace("\n", " ").strip()
    text = text.replace("||", " OR ")
    return text.replace("|", "/")

@lru_cache
def load_manifest() -> dict[str, list[str]]:
    raw = yaml.safe_load((TABLES_DIR / MANIFEST_NAME).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[str]] = {}
    for surface, ids in raw.items():
        if isinstance(ids, list):
            out[str(surface)] = [str(i) for i in ids]
    return out

@lru_cache
def load_table(table_id: str) -> dict[str, Any]:
    path = TABLES_DIR / f"{table_id}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"decision table {table_id!r} must be a mapping")
    return data

def _tag_set(tags: Iterable[str] | None) -> set[str] | None:
    if tags is None:
        return None
    return {str(t).strip() for t in tags if str(t).strip()}

def _table_applies(table: dict[str, Any], tags: set[str]) -> bool:
    required = table.get("when_any_tags") or []
    if not required:
        return True
    wanted = {str(t).strip() for t in required if str(t).strip()}
    return bool(wanted & tags)

def render_table(table: dict[str, Any], *, tags: Iterable[str] | None = None) -> str:
    """Render one table as Markdown. Empty string if filtered out.

    ``tags=None`` means no filtering (catalog/coder). When ``tags`` is an
    iterable (even empty), tables with ``when_any_tags`` require an overlap.
    """
    if tags is not None and not _table_applies(table, _tag_set(tags) or set()):
        return ""

    columns = [str(c) for c in (table.get("columns") or [])]
    if not columns:
        return ""
    rows = table.get("rows") or []
    title = str(table.get("title") or table.get("id") or "DECISION TABLE").strip()
    header = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"
    body_lines = [header, sep]
    for row in rows:
        if not isinstance(row, dict):
            continue
        cells = [_escape_cell(row.get(col, "")) for col in columns]
        body_lines.append("| " + " | ".join(cells) + " |")
    return f"## {title}\n" + "\n".join(body_lines)

def render_bundle(surface: str, *, tags: Iterable[str] | None = None) -> str:
    """Join all tables listed for ``surface`` in manifest.yaml."""
    ids = load_manifest().get(surface, [])
    parts: list[str] = []
    for table_id in ids:
        try:
            table = load_table(table_id)
        except (OSError, ValueError, yaml.YAMLError):
            continue
        rendered = render_table(table, tags=tags)
        if rendered.strip():
            parts.append(rendered.strip())
    return "\n\n".join(parts)

from UnitTest_gen.core.config import get_config

_ZONE_SEP = "\n\n"

def _join_zone(parts: list[str] | tuple[str, ...] | str) -> str:
    if isinstance(parts, str):
        return parts.strip()
    return "\n".join(part for part in parts if str(part).strip()).strip()

def compose_user_prompt(
    *,
    static: list[str] | tuple[str, ...] | str = "",
    cached: list[str] | tuple[str, ...] | str = "",
    dynamic: list[str] | tuple[str, ...] | str = "",
) -> str:
    """Join three ordered zones, dropping empty ones deterministically."""
    zones = [_join_zone(static), _join_zone(cached), _join_zone(dynamic)]
    return _ZONE_SEP.join(zone for zone in zones if zone)

def cap_dynamic(
    system: str,
    static: list[str] | tuple[str, ...] | str,
    cached: list[str] | tuple[str, ...] | str,
    dynamic: list[str] | tuple[str, ...] | str,
    budget: int | None = None,
) -> PromptParts:
    """Budget only the dynamic zone so the cacheable prefix stays byte-identical."""
    budget = budget if budget is not None else get_config().prompt_context_char_budget
    overhead = len("SYSTEM:\n\n\nUSER:\n")
    static_text = _join_zone(static)
    cached_text = _join_zone(cached)
    dynamic_text = _join_zone(dynamic)
    prefix = _ZONE_SEP.join(z for z in (static_text, cached_text) if z)
    prefix_len = len(prefix) + (len(_ZONE_SEP) if prefix and dynamic_text else 0)
    max_dynamic = max(0, budget - len(system) - overhead - prefix_len)
    if len(dynamic_text) > max_dynamic:
        dynamic_text = dynamic_text[:max_dynamic]
    user = compose_user_prompt(static=static_text, cached=cached_text, dynamic=dynamic_text)
    return PromptParts(system=system, user=user)

from dataclasses import dataclass
from functools import lru_cache

from UnitTest_gen.kotlin.codegen import TestFileState
from UnitTest_gen.kotlin.codegen import is_skeleton_test

@dataclass(frozen=True)
class PromptParts:
    """Stable system rules vs per-source user context for plan/coder agents."""

    system: str
    user: str

def format_system_user_prompt(parts: PromptParts) -> str:
    """Label SYSTEM/USER for test introspection (Claude uses --append-system-prompt)."""
    return f"SYSTEM:\n{parts.system.strip()}\n\nUSER:\n{parts.user.strip()}"

@lru_cache
def _tool_block(name: str) -> str:
    return load_skeleton(name).strip()

def plan_tool_block() -> str:
    return _tool_block("plan_tool_block.md")

def coder_tool_block() -> str:
    return _tool_block("coder_tool_block.md")

def fix_tool_block() -> str:
    return _tool_block("fix_tool_block.md")

def plan_target_status_line(state: TestFileState, existing_code: str | None = None) -> str:
    code = existing_code
    if code is None and state.exists:
        from UnitTest_gen.core import io as file_cache

        code = file_cache.read_text(state.path, default="")
    code = code or ""
    if "@Test" in code:
        return (
            "TARGET STATUS: existing file with @Test methods "
            "(supplement DELTA only; do not wipe or rewrite)"
        )
    if state.has_content and is_skeleton_test(code):
        return "TARGET STATUS: file exists without @Test — coder Writes/Edits a full JUnit4 class and harness"
    if state.has_content:
        return "TARGET STATUS: existing file with content (supplement DELTA only)"
    if state.exists:
        return "TARGET STATUS: existing file is empty — coder will Write the full test class (no pipeline seed)"
    return "TARGET STATUS: TARGET does not exist — coder will Write the full JUnit4 class and any missing harness"

def target_file_block(
    *,
    state: TestFileState,
    abs_test: str,
    abs_source: str,
    existing_test_code: str = "",
    test_layer=None,
) -> list[str]:
    """Coder/fix TARGET status. Mutation roots and harness invention live in the tool block."""
    _ = test_layer
    stem_rule = "Class name must match the file stem (e.g. FooTest.kt → class FooTest)."
    code = (existing_test_code or "").strip()
    source_line = f"SOURCE FILE (absolute): {abs_source}"
    if code and "@Test" in code:
        return [
            f"PIPELINE TARGET TEST FILE (absolute): {abs_test}",
            source_line,
            "SUPPLEMENT ONLY: TARGET already has @Test methods — add DELTA cases; "
            "do not wipe, empty, or replace the class body. Missing harness files may still be Written.",
            f"Use Edit on {abs_test} for tests; Write new harness paths if they do not exist.",
        ]
    if state.has_content and code and is_skeleton_test(existing_test_code):
        return [
            f"PIPELINE TARGET TEST FILE (absolute, no @Test yet): {abs_test}",
            source_line,
            "TARGET exists without @Test. Write or Edit a complete JUnit4 class now, plus missing harness files.",
            f"Use Edit (or Write if a full-file rewrite is clearer) on {abs_test}.",
            stem_rule,
        ]
    if state.has_content:
        return [
            f"PIPELINE TARGET TEST FILE (absolute): {abs_test}",
            source_line,
            "SUPPLEMENT ONLY: add tests for the DELTA plan items; preserve already-covered cases.",
            f"Use Edit on {abs_test}. Write missing harness files if needed.",
        ]
    if state.exists:
        return [
            f"PIPELINE TARGET TEST FILE (absolute, empty): {abs_test}",
            source_line,
            "TARGET exists but is empty — Write the full JUnit4 test class at this path (no pipeline seed).",
            f"Use Write (or Edit if clearer) ONLY to create the class at {abs_test}, plus missing harness files.",
            stem_rule,
        ]
    return [
        f"PIPELINE TARGET TEST FILE (does not exist yet): {abs_test}",
        source_line,
        "TARGET does not exist. Call Write once with this exact file_path and a complete JUnit4 class "
        "derived from SOURCE (package, imports, class = file stem, annotations/rules, @Test for this slice). "
        "Also Write any missing harness files under the owning module test roots.",
        stem_rule,
    ]

from pathlib import Path

from UnitTest_gen.core import io as file_cache
from UnitTest_gen.core.config import get_config
from UnitTest_gen.core.io import compact_ranges
from UnitTest_gen.kotlin.recipes import (
    format_fix_recipe_context,
    format_instrumented_fix_recipe_context,
    format_instrumented_recipe_catalog,
    format_recipe_catalog,
)
from UnitTest_gen.kotlin.layer import TestLayer
from UnitTest_gen.kotlin.codegen import TestFileState, read_test_file_state
from UnitTest_gen.kotlin.coverage import AGENT_DONE_INSTRUCTIONS
from UnitTest_gen.kotlin.codegen import format_fix_error_context
from UnitTest_gen.kotlin.project import (
    resolve_module_sdk_prompt_block_for_path,
    format_owning_module_anchor,
    format_instrumented_bootstrap_paths_block,
)
from UnitTest_gen.kotlin.codegen import RepairTicket, build_repair_tickets, format_tickets_for_prompt
from UnitTest_gen.kotlin.codegen import format_slice_source_projection, format_target_excerpt
from UnitTest_gen.kotlin.validate import mocking_lane_prompt_block, resolve_mocking_lane

_CODER_EXECUTE = load_skeleton("coder_execute_block.md")
_CODER_USER = load_skeleton("coder_user_prompt.md")
_FIX_INTRO = load_skeleton("fix_prompt_intro.md").strip()

def _read_text(path: str, default: str = "") -> str:
    return file_cache.read_text(path, default=default)

def _pipeline_verify_lines(
    gradle_command: str,
    kover_command: str,
    *,
    fix: bool = False,
    test_layer: TestLayer = TestLayer.UNIT,
    compile_command: str = "",
) -> list[str]:
    _ = fix
    lines = [
        "PIPELINE VERIFY",
        f"You MAY run this module compile+test for THIS TARGET: {gradle_command}",
        f"KOVER_GATE / JACOCO_GATE (Python, after compile-green): {kover_command}",
        "Python runs FAST_VERIFY once as fallback only if you never reported BUILD SUCCESSFUL for THIS TARGET.",
    ]
    if test_layer == TestLayer.INSTRUMENTED:
        from UnitTest_gen.core.emulator import format_emulator_prompt_block

        lines.append(format_emulator_prompt_block(compile_command=compile_command))
    return lines

def _resolve_slice_views(
    *,
    abs_source: str,
    abs_test: str,
    source_code: str,
    existing_test_code: str,
    method_names,
    delta_lines,
    slice_source: str | None,
    target_excerpt: str | None,
):
    if slice_source is None:
        slice_source = format_slice_source_projection(
            abs_source, source_code,
            method_names=set(method_names or ()),
            delta_lines=set(delta_lines or ()),
        )
    if target_excerpt is None:
        target_excerpt = format_target_excerpt(existing_test_code, abs_test)
    return slice_source, target_excerpt

def build_agent_prompt(
    *,
    source_path: str,
    test_path: str,
    gradle_command: str,
    kover_command: str,
    kover_text: str,
    static_analysis_path: str,
    memory_context: str,
    plan_text: str = "",
    plan_path: str = "",
    api_doc_text: str = "",
    test_file_state: TestFileState | None = None,
    sibling_test_context: str = "",
    slice_tag: str = "",
    source_code: str = "",
    existing_test_code: str = "",
    slice_source: str | None = None,
    target_excerpt: str | None = None,
    method_names: set[str] | frozenset[str] | tuple[str, ...] | list[str] = (),
    delta_lines: set[int] | frozenset[int] | tuple[int, ...] | None = None,
    source_categories: list[str] | tuple[str, ...] | frozenset[str] | set[str] = (),
    pinned_recipe_id: str = "",
    recipe_lane: str = "",
    test_layer: TestLayer = TestLayer.UNIT,
    compile_command: str = "",
) -> PromptParts:
    budget = get_config().prompt_context_char_budget
    config = get_config()
    abs_test = str(Path(test_path).resolve())
    abs_source = str(Path(source_path).resolve())
    state = test_file_state or read_test_file_state(test_path)
    code_for_target = existing_test_code or (_read_text(test_path) if state.has_content else "")
    lane = resolve_mocking_lane(
        source_code=source_code,
        existing_test_code=existing_test_code or code_for_target,
        output_file_path=abs_test,
    )
    lane_block = mocking_lane_prompt_block(lane)
    recipe_block = (
        format_instrumented_recipe_catalog(
            source_code,
            categories=list(source_categories or ()),
        )
        if test_layer == TestLayer.INSTRUMENTED
        else format_recipe_catalog(
            source_code,
            categories=list(source_categories or ()),
            pinned_recipe_id=pinned_recipe_id,
            recipe_lane=recipe_lane,
        )
    )
    test_status = target_file_block(
        state=state, abs_test=abs_test, abs_source=abs_source,
        existing_test_code=code_for_target,
        test_layer=test_layer,
    )
    source_read = "Read"
    slice_source, target_excerpt = _resolve_slice_views(
        abs_source=abs_source, abs_test=abs_test, source_code=source_code,
        existing_test_code=code_for_target, method_names=method_names,
        delta_lines=delta_lines, slice_source=slice_source, target_excerpt=target_excerpt,
    )
    execute_block = _CODER_EXECUTE.format(source_read=source_read).strip().splitlines()
    test_kind = "instrumented test" if test_layer == TestLayer.INSTRUMENTED else "unit test"
    generation_role = (
        INSTRUMENTED_GENERATION_SYSTEM_PROMPT.strip()
        if test_layer == TestLayer.INSTRUMENTED
        else GENERATION_SYSTEM_PROMPT.strip()
    )
    android_rules = (
        INSTRUMENTED_KOTLIN_ANDROID_TEST_RULES.strip()
        if test_layer == TestLayer.INSTRUMENTED
        else KOTLIN_ANDROID_TEST_RULES.strip()
    )
    # EXIT/catalog decision tables moved to Python loops (RepairTicket + FAST_VERIFY).
    # One canonical cross-cutting contract; the redundant system tail is dropped.
    system = "\n".join([
        generation_role,
        CANONICAL_PIPELINE_CONTRACT,
        coder_tool_block(),
        *execute_block,
        CORE_GENERATION_RULES.strip(),
        TEST_QUALITY_RULES.strip(),
        android_rules,
        AGENT_DONE_INSTRUCTIONS.strip(),
    ])
    static = _CODER_USER.format(
        abs_test=abs_test,
        abs_source=abs_source,
        test_kind=test_kind,
        recipe_block="",
        plan_file_line=(
            f"Plan file (authoritative): {plan_path}" if plan_path else "Plan file: (inline blueprint below)"
        ),
        slice_line=(
            f"THIS SLICE tag={slice_tag}: implement only the plan items for this tag. "
            "Later slices run in separate passes."
            if slice_tag else "Cover only the DELTA plan / Kover lines below."
        ),
        source_read=source_read,
        slice_source="",
        target_excerpt="",
    ).strip()
    # Drop empty placeholders left by the template zones we fill below.
    static = "\n".join(line for line in static.splitlines() if line.strip())
    cached_parts = [
        slice_source,
        target_excerpt,
        recipe_block,
        sibling_test_context.strip(),
        api_doc_text.strip(),
        resolve_module_sdk_prompt_block_for_path(abs_source),
    ]
    dynamic_parts = [
        format_owning_module_anchor(abs_source),
        (
            format_instrumented_bootstrap_paths_block(
                abs_source,
                source_code=source_code,
                categories=frozenset(source_categories or ()),
            )
            if test_layer == TestLayer.INSTRUMENTED
            else ""
        ),
        f"TEST LAYER: {test_layer.value} — use ONLY {test_layer.value} recipes and harness.",
        (
            "Forbidden: RobolectricTestRunner, src/test-only patterns."
            if test_layer == TestLayer.INSTRUMENTED
            else "Forbidden: connectedAndroidTest-only patterns when layer is unit."
        ),
        "\n".join(test_status),
        lane_block,
        "\n".join(
            _pipeline_verify_lines(
                gradle_command,
                kover_command,
                test_layer=test_layer,
                compile_command=compile_command,
            )
        ),
        plan_text.strip() or "CODER PLAN: (none provided)",
        "KOVER VERIFICATION CONTEXT (do not replan from this)",
        kover_text,
        f"STATIC ANALYSIS JSON: {static_analysis_path or 'none'}",
    ]
    if memory_context.strip():
        dynamic_parts.extend(["PRIOR LESSONS / ATTEMPTS", memory_context.strip()])
    del config
    return cap_dynamic(system, static, cached_parts, dynamic_parts, budget)

def build_fix_prompt(
    *,
    source_path: str,
    test_path: str,
    gradle_command: str,
    kover_command: str,
    gradle_output: str,
    validation_issues: list[str] | None = None,
    tickets: list[RepairTicket] | None = None,
    sibling_test_context: str = "",
    slice_tag: str = "",
    project_root: str = "",
    api_doc_text: str = "",
    source_code: str = "",
    existing_test_code: str = "",
    slice_source: str | None = None,
    target_excerpt: str | None = None,
    method_names: set[str] | frozenset[str] | tuple[str, ...] | list[str] = (),
    delta_lines: set[int] | frozenset[int] | tuple[int, ...] | None = None,
    plan_path: str = "",
    pinned_recipe_id: str = "",
    recipe_lane: str = "",
    source_categories: list[str] | tuple[str, ...] | frozenset[str] | set[str] = (),
    test_layer: TestLayer = TestLayer.UNIT,
    compile_command: str = "",
) -> PromptParts:
    """Slim ticket-driven fix prompt — no CORE_GENERATION_RULES / EXIT catalog re-injection."""
    abs_test = str(Path(test_path).resolve())
    abs_source = str(Path(source_path).resolve())
    lane = resolve_mocking_lane(
        source_code=source_code, existing_test_code=existing_test_code, output_file_path=abs_test,
    )
    lane_block = mocking_lane_prompt_block(lane)
    if tickets is None:
        tickets = build_repair_tickets(
            validation_issues=validation_issues or [],
            gradle_output=gradle_output or "",
        )
    ticket_block = format_tickets_for_prompt(tickets)
    # Keep a short gradle snippet only when tickets already summarize failures.
    failure_block = ""
    if any(t.source == "gradle" for t in tickets):
        failure_block = format_fix_error_context(
            gradle_output or "", project_root=project_root, test_path=abs_test,
        )
    slice_source, target_excerpt = _resolve_slice_views(
        abs_source=abs_source, abs_test=abs_test, source_code=source_code,
        existing_test_code=existing_test_code, method_names=method_names,
        delta_lines=delta_lines, slice_source=slice_source, target_excerpt=target_excerpt,
    )
    lines_text = compact_ranges(sorted(int(n) for n in (delta_lines or ())))
    fix_recipe_block = (
        format_instrumented_fix_recipe_context(
            source_code,
            categories=list(source_categories or ()),
            api_doc_text=api_doc_text,
        )
        if test_layer == TestLayer.INSTRUMENTED
        else format_fix_recipe_context(
            source_code,
            project_root=project_root,
            categories=list(source_categories or ()),
            pinned_recipe_id=pinned_recipe_id,
            recipe_lane=recipe_lane,
            api_doc_text=api_doc_text,
            gradle_output=gradle_output or "",
        )
    )
    system = "\n\n".join([
        _FIX_INTRO,
        CANONICAL_PIPELINE_CONTRACT,
        fix_tool_block(),
        AGENT_DONE_INSTRUCTIONS.strip(),
    ])
    static = "\n".join([
        f"Plan file (authoritative): {plan_path}" if plan_path else "Stay within the original blueprint scope for this slice.",
        f"SOURCE FILE (absolute): {abs_source}",
        f"PIPELINE TARGET TEST FILE (absolute): {abs_test}",
        f"Use Edit on {abs_test} for TARGET tests; Write missing harness under "
        "src/test or src/androidTest and gradle test deps. "
        "Sibling *JvmTest.kt / *RobolectricTest.kt / other non-target *Test.kt files are reference-only.",
    ])
    # Ticket-only: TARGET excerpt + this-slice lines; skip recipe/api re-paste.
    cached = [
        target_excerpt,
        slice_source,
        fix_recipe_block,
        sibling_test_context.strip() if sibling_test_context.strip() else "",
    ]
    dynamic = [
        format_owning_module_anchor(abs_source, project_root=project_root),
        (
            format_instrumented_bootstrap_paths_block(
                abs_source,
                project_root=project_root,
                source_code=source_code,
                categories=frozenset(source_categories or ()),
            )
            if test_layer == TestLayer.INSTRUMENTED
            else ""
        ),
        f"TEST LAYER: {test_layer.value} — use ONLY {test_layer.value} recipes and harness.",
        lane_block,
        f"THIS SLICE tag={slice_tag} lines={lines_text}: fix only this slice."
        if slice_tag else f"Fix only listed tickets (lines={lines_text or 'n/a'}).",
        "\n".join(
            _pipeline_verify_lines(
                gradle_command,
                kover_command,
                fix=True,
                test_layer=test_layer,
                compile_command=compile_command,
            )
        ),
        "REPAIR TICKETS",
        ticket_block,
    ]
    if failure_block.strip():
        dynamic.extend(["GRADLE / JUNIT FAILURE DETAIL", failure_block])
    return PromptParts(
        system=system,
        user=compose_user_prompt(static=static, cached=cached, dynamic=dynamic),
    )
