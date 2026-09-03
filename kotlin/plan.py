"""Coverage plan agent, markdown parse/render, validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

@dataclass(frozen=True)
class PlanGapItem:
    lines: tuple[int, ...] = ()
    methods: tuple[str, ...] = ()
    approach: str = ""
    fixture_hint: str = ""
    risks: tuple[str, ...] = ()
    reason: str = ""
    category: str = ""
    tag: str = ""
    tc_ids: tuple[str, ...] = ()
    branch_targets: tuple[str, ...] = ()
    test_layer: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "lines": list(self.lines),
            "methods": list(self.methods),
            "approach": self.approach,
            "fixture_hint": self.fixture_hint,
            "risks": list(self.risks),
            "reason": self.reason,
            "category": self.category,
            "tag": self.tag,
            "tc_ids": list(self.tc_ids),
            "branch_targets": list(self.branch_targets),
            "test_layer": self.test_layer,
        }

@dataclass(frozen=True)
class CoveragePlan:
    testable: tuple[PlanGapItem, ...] = ()
    not_testable: tuple[PlanGapItem, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict)
    parsed: bool = False
    fallback_reason: str = ""
    markdown_path: str = ""
    markdown_body: str = ""

    @property
    def has_testable(self) -> bool:
        return bool(self.testable)

def _int_tuple(values) -> tuple[int, ...]:
    out: list[int] = []
    for value in values or []:
        try:
            out.append(int(value))
        except (TypeError, ValueError):
            continue
    return tuple(sorted(set(out)))

def _str_tuple(values) -> tuple[str, ...]:
    return tuple(str(v) for v in (values or []) if str(v).strip())

def _item_from_dict(payload: dict[str, Any], *, not_testable: bool = False) -> PlanGapItem:
    approach = str(payload.get("approach") or "")
    fixture = str(payload.get("fixture_hint") or "")
    tc_ids = _str_tuple(payload.get("tc_ids"))
    branch_targets = _str_tuple(payload.get("branch_targets"))
    if not tc_ids and fixture.startswith("tickets: "):
        tc_ids = tuple(p.strip() for p in fixture[9:].split(",") if p.strip())
    if not branch_targets and approach.startswith("branch: "):
        branch_targets = tuple(p.strip() for p in approach[8:].split(";") if p.strip())
    return PlanGapItem(
        lines=_int_tuple(payload.get("lines")),
        methods=_str_tuple(payload.get("methods")),
        approach=approach,
        fixture_hint=fixture,
        risks=_str_tuple(payload.get("risks")),
        reason=str(payload.get("reason") or ""),
        category=str(payload.get("category") or ("not_unit_testable" if not_testable else "")),
        tag=str(payload.get("tag") or ""),
        tc_ids=tc_ids,
        branch_targets=branch_targets,
        test_layer=str(payload.get("test_layer") or "").strip().lower(),
    )

def parse_coverage_plan(payload: dict[str, Any] | None) -> CoveragePlan:
    if not isinstance(payload, dict):
        return CoveragePlan(parsed=False, fallback_reason="missing json object")
    testable = tuple(
        _item_from_dict(item)
        for item in (payload.get("testable") or [])
        if isinstance(item, dict)
    )
    not_testable = tuple(
        _item_from_dict(item, not_testable=True)
        for item in (payload.get("not_testable") or [])
        if isinstance(item, dict)
    )
    return CoveragePlan(
        testable=testable,
        not_testable=not_testable,
        raw=payload,
        parsed=True,
    )

import os
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from UnitTest_gen.core import io as file_cache
from UnitTest_gen.core.io import log_message
from UnitTest_gen.core.io import compact_ranges
from UnitTest_gen.core.io import extract_fenced_block, iter_fenced_blocks
from UnitTest_gen.kotlin.prompts import plan_tool_block
from UnitTest_gen.kotlin.analysis import PLAIN_UNIT_TAG
from UnitTest_gen.kotlin.prompts import render_bundle
from UnitTest_gen.kotlin.prompts import PLAN_EXIT_STRATEGY
from UnitTest_gen.kotlin.prompts import PACKAGE_DIR, load_catalog_sections, load_skeleton

PLAN_MARKDOWN_TEMPLATE = load_skeleton("plan_markdown_template.md").strip()
DEFAULT_PLANS_DIR = PACKAGE_DIR / "data" / "plans"
_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)
_SCHEMA_VERSION_START = re.compile(r"---\s*\r?\nschema_version\s*:", re.IGNORECASE)
_SECTION_HEADER = re.compile(r"^##\s+(\d+)\.\s+(.+?)\s*$", re.MULTILINE)
_TABLE_ROW = re.compile(r"^\|(.+)\|$")
_NA_VALUE = re.compile(r"^(?:n/?a|none|-|\u2014)$", re.IGNORECASE)
_MERGE = load_catalog_sections("merge_target_rules.md")
_MERGE_TARGET_EXISTING, _MERGE_TARGET_NEW = _MERGE["MERGE_TARGET_EXISTING"], _MERGE["MERGE_TARGET_NEW"]
_RENDER_MID = (PACKAGE_DIR / "data" / "plans" / "render_fallback_mid.md").read_text(encoding="utf-8").strip()
_RENDER_TAIL = (PACKAGE_DIR / "data" / "plans" / "render_fallback_tail.md").read_text(encoding="utf-8").strip()
_PLAN_SYSTEM_RULES = PACKAGE_DIR / "data" / "prompt_skeletons" / "plan_agent_system_rules.md"
_PLAN_MARKDOWN_SKELETON_ABS = str((PACKAGE_DIR / "data" / "prompt_skeletons" / "plan_markdown_template.md").resolve())
_SECTION7_COLS = (
    "id", "kover lines", "tag", "branch target", "scenario", "given", "when", "then", "assertions",
)
_SECTION13_COLS = ("kover lines", "methods", "tag", "category", "reason")

def plans_dir() -> Path:
    override = os.environ.get("TESTGEN_PLANS_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    try:
        from UnitTest_gen.core.config import get_config

        configured = str(getattr(get_config(), "plans_dir", "") or "").strip()
        if configured:
            return Path(configured).expanduser().resolve()
    except Exception:
        pass
    return DEFAULT_PLANS_DIR.resolve()

def _plan_raw_map(plan: Any) -> dict[str, Any]:
    """Copy plan.raw only when it is a mapping (never coerce str → char dict)."""
    raw = getattr(plan, "raw", None)
    return dict(raw) if isinstance(raw, dict) else {}

def plan_frontmatter_mocking_lane(existing_test_code: str = "") -> str:
    from UnitTest_gen.kotlin.validate import resolve_mocking_lane

    lane = resolve_mocking_lane(existing_test_code=existing_test_code or "")
    return "mockito" if lane == "open" else lane

def ensure_plan_mocking_lane(plan: CoveragePlan, *, existing_test_code: str = "") -> CoveragePlan:
    raw = _plan_raw_map(plan)
    current = str(raw.get("mocking_lane") or "").strip().lower()
    if current in ("mockito", "mockk"):
        return plan
    raw["mocking_lane"] = plan_frontmatter_mocking_lane(existing_test_code)
    return replace(plan, raw=raw)

def ensure_plan_recipe_id(plan: CoveragePlan, recipe_id: str = "none") -> CoveragePlan:
    """Overwrite frontmatter recipe_id with the pipeline-selected playbook id."""
    return ensure_plan_recipe_selection(plan, recipe_id=recipe_id, recipe_lane="")

def ensure_plan_recipe_selection(
    plan: CoveragePlan,
    *,
    recipe_id: str = "none",
    recipe_lane: str = "",
) -> CoveragePlan:
    """Pin recipe_id and optional recipe_lane into plan.raw."""
    raw = _plan_raw_map(plan)
    pinned = str(recipe_id or "").strip() or "none"
    lane = str(recipe_lane or "").strip()
    changed = False
    if str(raw.get("recipe_id") or "").strip() != pinned:
        raw["recipe_id"] = pinned
        changed = True
    if lane and str(raw.get("recipe_lane") or "").strip() != lane:
        raw["recipe_lane"] = lane
        changed = True
    if not changed:
        return plan
    return replace(plan, raw=raw)

def plan_path_for_source(source_path: str) -> Path:
    return plans_dir() / f"{Path(source_path).stem or 'unknown'}.plan.md"

def plans_bak_dir() -> Path:
    return plans_dir() / "bak"

def archive_plan_markdown(source_path: str) -> Path | None:
    """Move ``Stem.plan.md`` into ``plans/bak/`` after a source run ends or is interrupted."""
    target = plan_path_for_source(source_path)
    if not target.is_file():
        return None
    bak = plans_bak_dir()
    bak.mkdir(parents=True, exist_ok=True)
    dest = bak / target.name
    if dest.exists():
        from datetime import datetime

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        stem = Path(source_path).stem or target.name.removesuffix(".plan.md") or "unknown"
        dest = bak / f"{stem}.{stamp}.plan.md"
    try:
        target.replace(dest)
    except OSError as exc:
        log_message(f"⚠️ Could not archive plan.md → bak/: {exc}", category="warning")
        return None
    file_cache.invalidate(target)
    file_cache.invalidate(dest)
    log_message(f"📦 Archived plan.md → {dest}", category="info")
    return dest.resolve()

def save_plan_markdown(source_path: str, text: str) -> Path:
    target = plan_path_for_source(source_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text.strip() + "\n", encoding="utf-8")
    file_cache.invalidate(target)
    return target.resolve()

def delete_plan_markdown(source_path: str) -> bool:
    target = plan_path_for_source(source_path)
    if not target.is_file():
        return False
    try:
        target.unlink()
    except OSError:
        return False
    file_cache.invalidate(target)
    return True

def _is_na_value(text: str) -> bool:
    return bool(_NA_VALUE.fullmatch((text or "").strip()))

def _is_na_only_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith("|") and stripped.endswith("|"):
        cells = [cell.strip() for cell in stripped.strip("|").split("|") if cell.strip()]
        return bool(cells) and all(_is_na_value(cell) for cell in cells)
    body = re.sub(r"^[-*+]\s+", "", stripped)
    if ":" in body:
        return _is_na_value(body.split(":", 1)[1])
    return _is_na_value(body)

def drop_unwanted_plan_data(text: str) -> str:
    meta, body = split_frontmatter(text)
    if meta is None:
        return text
    chunks = _SECTION_HEADER.split(body)
    kept = [chunks[0].rstrip()]
    for index in range(1, len(chunks), 3):
        number, title, content = chunks[index : index + 3]
        content_lines = [line for line in content.splitlines() if not _is_na_only_line(line)]
        meaningful = [
            line for line in content_lines
            if line.strip() and not re.fullmatch(r"\|[-| ]+\|", line.strip())
        ]
        if meaningful:
            kept.append(f"## {number}. {title}\n" + "\n".join(content_lines).strip())
    dumped = yaml.safe_dump(dict(meta), sort_keys=False, default_flow_style=False, allow_unicode=True)
    return f"---\n{dumped.rstrip()}\n---\n\n" + "\n\n".join(part for part in kept if part).rstrip()

def _dump_yaml_frontmatter(meta: dict, body: str) -> str:
    dumped = yaml.safe_dump(meta, sort_keys=False, default_flow_style=False, allow_unicode=True)
    return f"---\n{dumped.rstrip()}\n---\n\n{body.rstrip()}"

def _format_coder_plan_items(items, *, start: int = 1, indent: str = "   ") -> list[str]:
    out: list[str] = []
    for index, item in enumerate(items, start=start):
        tag = f" tag={item.tag}" if getattr(item, "tag", "") else ""
        out.append(f"{index}. lines={list(item.lines)} methods={list(item.methods)}{tag}")
        out.extend(format_approach_for_coder(getattr(item, "approach", "") or "", indent=indent))
        if getattr(item, "fixture_hint", ""):
            out.append(f"{indent}fixture_hint: {item.fixture_hint}")
        risks = getattr(item, "risks", ()) or ()
        if risks:
            out.append(f"{indent}risks: {', '.join(risks)}")
    return out

def _format_deferred_block(not_testable) -> list[str]:
    if not not_testable:
        return []
    return ["", "ALREADY DEFERRED (do not retry):"] + [
        f"- lines={list(item.lines)} reason={item.reason or item.category or 'not_testable'}"
        for item in not_testable
    ]

def _plan_system_prompt(*, tags: list[str] | tuple[str, ...] | None = None) -> str:
    """Build plan system prompt; ``tags`` filters ``when_any_tags`` decision tables."""
    filter_tags = list(tags) if tags is not None else None
    return file_cache.read_text(_PLAN_SYSTEM_RULES).format(
        plan_exit_strategy=PLAN_EXIT_STRATEGY.strip(),
        plan_tool_block=plan_tool_block(),
        skeleton_path=_PLAN_MARKDOWN_SKELETON_ABS,
        decision_tables=render_bundle("plan", tags=filter_tags),
    )

def _plan_lines(plan: CoveragePlan) -> list[int]:
    lines: list[int] = []
    for item in list(plan.testable) + list(plan.not_testable):
        lines.extend(int(n) for n in item.lines)
    return sorted(set(lines))

def _csv(values: object) -> str:
    if isinstance(values, (list, tuple, set)):
        return ", ".join(str(v) for v in values if str(v).strip())
    return str(values or "")

def _cell(value: object) -> str:
    return _csv(value).replace("|", "/").replace("\n", " ").strip()

def format_approach_for_coder(approach: str, *, indent: str = "   ") -> list[str]:
    text = (approach or "").strip()
    if not text:
        return []
    lines = text.splitlines()
    if len(lines) == 1:
        return [f"{indent}approach: {lines[0]}"]
    return [f"{indent}approach: |"] + [f"{indent}  {line.rstrip()}" for line in lines]

def render_plan_markdown(plan: CoveragePlan, *, source_path: str, test_path: str = "", reason: str = "") -> str:
    source, test = str(Path(source_path).resolve()), str(Path(test_path).resolve()) if test_path else ""
    from UnitTest_gen.kotlin.layer import plan_primary_layer

    primary_layer = plan_primary_layer(plan)
    layer_label = (
        "instrumented coverage plan (androidTest)"
        if primary_layer.value == "instrumented"
        else "unit coverage plan"
    )
    raw = _plan_raw_map(plan)
    meta = {
        "schema_version": 2, "source_file": source, "test_file": test,
        "mocking_lane": str(raw.get("mocking_lane") or "mockito"),
        "recipe_id": str(raw.get("recipe_id") or "none"),
        "recipe_lane": str(raw.get("recipe_lane") or "plain_jvm"),
        "delta_allow_list": _plan_lines(plan),
        "testable": [_item_to_frontmatter(item) for item in plan.testable],
        "not_testable": [_item_to_frontmatter(item, not_testable=True) for item in plan.not_testable],
    }
    reason_suffix = f"; {reason}" if reason else ""
    head = (
        f"# Test Plan\n\n## 1. Target\n- Class under test: {Path(source_path).stem}\n"
        "- CUT construction: follow source pattern (object call / constructor / Hilt)\n"
        "- Method/feature under test: coverage delta lines\n"
        "- Test scope: selected Kover uncovered lines only\n"
        "- Out of scope: already-covered lines and production refactors\n\n"
        "## 2. Test Type\n- Classification: "
        f"{layer_label}\n"
        f"- Reason: generated from planner structured output{reason_suffix}\n\n"
    )
    sec7 = "\n".join([
        "## 7. Test Cases",
        "| ID | Priority | Kover Lines | Tag | Branch Target | Scenario | Given | When | Then | Required Lifecycle State | Assertions |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
        *[
            f"| TC{i:02d} | P0 | {_cell(item.lines)} | {_cell(item.tag)} |  | "
            f"{_cell(item.approach or 'Reach selected lines through the public API')} | "
            f"{_cell(item.fixture_hint or 'Minimal fake/mock data required by selected lines')} | "
            "invoke public path | selected lines execute | as required | Assert observable output from selected lines |"
            for i, item in enumerate(plan.testable, start=1)
        ],
    ])
    sec13 = "\n".join([
        "## 13. Deferred Coverage\n| Kover Lines | Methods | Tag | Category | Reason |\n|---|---|---|---|---|",
        *[
            f"| {_cell(item.lines)} | {_cell(item.methods)} | {_cell(item.tag)} | "
            f"{_cell(item.category or 'not_unit_testable')} | {_cell(item.reason or 'not unit-testable from current target')} |"
            for item in plan.not_testable
        ],
    ])
    return _dump_yaml_frontmatter(meta, "\n\n".join([head + _RENDER_MID, sec7, _RENDER_TAIL, sec13]))

def load_plan_markdown(source_path: str) -> str:
    return file_cache.read_text(plan_path_for_source(source_path), default="")

def split_frontmatter(text: str) -> tuple[dict[str, Any] | None, str]:
    if not text:
        return None, ""
    match = _FRONTMATTER.match(text.strip())
    if not match:
        return None, text.strip()
    try:
        meta = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return None, match.group(2)
    return (meta, match.group(2)) if isinstance(meta, dict) else (None, match.group(2))

def _is_plan_meta(meta: dict[str, Any] | None) -> bool:
    if not isinstance(meta, dict) or meta.get("schema_version") is None:
        return False
    return isinstance(meta.get("testable"), list) or isinstance(meta.get("not_testable"), list)

def _iter_plan_candidates(text: str) -> list[str]:
    stripped = str(text or "").strip()
    if not stripped:
        return []
    seen: set[str] = set()
    out: list[str] = []

    def _add(candidate: str) -> None:
        normalized = candidate.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            out.append(normalized)

    for body in iter_fenced_blocks(stripped, lang="markdown"):
        _add(body)
    open_match = re.search(r"```(?:markdown|md)\s*\n", stripped, re.IGNORECASE)
    if open_match:
        _add(stripped[open_match.end() :])
    for match in _SCHEMA_VERSION_START.finditer(stripped):
        _add(stripped[match.start() :])
    pos = 0
    while True:
        start = stripped.find("---", pos)
        if start < 0:
            break
        after = start + 3
        if after < len(stripped) and stripped[after] not in ("\n", "\r"):
            pos = after
            continue
        _add(stripped[start:])
        pos = after
    for start in (0, stripped.find("---\n"), stripped.find("---\r\n")):
        if start >= 0:
            _add(stripped[start:])
    _add(stripped)
    return out

def extract_plan_markdown(text: str) -> str | None:
    for candidate in _iter_plan_candidates(text):
        meta, _ = split_frontmatter(candidate)
        if _is_plan_meta(meta):
            return candidate
        if candidate.lstrip().startswith("# Test Plan") or "schema_version" in candidate[:400]:
            return candidate
    return None

def _table_rows(section_text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in (section_text or "").splitlines():
        match = _TABLE_ROW.match(line.strip())
        if not match:
            continue
        cells = [c.strip() for c in match.group(1).split("|")]
        if cells and all(set(c) <= {"-", ":", " "} for c in cells):
            continue
        rows.append(cells)
    return rows

def _header_index(rows: list[list[str]]) -> dict[str, int]:
    if not rows:
        return {}
    return {
        str(cell or "").strip().lower(): idx
        for idx, cell in enumerate(rows[0])
        if str(cell or "").strip()
    }

def _table_cell(row: list[str], idx: int | None) -> str:
    if idx is None or idx < 0 or idx >= len(row):
        return ""
    return str(row[idx] or "").strip()

def _parse_kover_lines_cell(cell: str) -> set[int]:
    out: set[int] = set()
    for part in re.split(r"[,;\s]+", cell or ""):
        part = part.strip()
        if part:
            try:
                out.add(int(part))
            except ValueError:
                continue
    return out

def _split_words(cell: str) -> tuple[str, ...]:
    return tuple(p.strip() for p in re.split(r"[,;/]+", str(cell or "")) if p.strip())

def _parse_sections(body: str) -> dict[int, str]:
    matches = list(_SECTION_HEADER.finditer(body or ""))
    return {
        int(match.group(1)): body[match.start() : (matches[i + 1].start() if i + 1 < len(matches) else len(body))].rstrip()
        for i, match in enumerate(matches)
    }

def _items_from_table(rows: list[list[str]], *, section: int) -> list[PlanGapItem]:
    if len(rows) < 2:
        return []
    idx = _header_index(rows)
    kover_idx = idx.get("kover lines")
    items: list[PlanGapItem] = []
    for row in rows[1:]:
        lines = tuple(sorted(_parse_kover_lines_cell(_table_cell(row, kover_idx))))
        if not lines:
            continue
        if section == 7:
            cols = {name: _table_cell(row, idx.get(name)) for name in _SECTION7_COLS}
            branch, scenario = cols.get("branch target", ""), cols.get("scenario", "")
            given, when, then, asserts = (
                cols.get("given", ""), cols.get("when", ""), cols.get("then", ""), cols.get("assertions", ""),
            )
            approach = "\n".join([
                f"1. Scenario: {scenario or 'N/A'}", f"2. Given: {given or 'N/A'}",
                f"3. When: {when or 'N/A'}", f"4. Then: {then or 'N/A'}",
                f"5. Assertions: {asserts or 'N/A'}",
            ])
            if branch:
                approach = f"branch: {branch}\n{approach}"
            tc_id = cols.get("id", "")
            items.append(PlanGapItem(
                lines=lines, methods=(), approach=approach,
                fixture_hint=f"tickets: {tc_id}" if tc_id else "",
                tag=cols.get("tag", ""),
                tc_ids=(tc_id,) if tc_id else (),
            ))
        else:
            category = _table_cell(row, idx.get("category")) or "not_unit_testable"
            reason = _table_cell(row, idx.get("reason")) or (
                f"Deferred ({category}): lines {list(lines)} marked not_testable in Section 13."
            )
            items.append(PlanGapItem(
                lines=lines,
                methods=_split_words(_table_cell(row, idx.get("methods"))),
                reason=reason, category=category,
                tag=_table_cell(row, idx.get("tag")),
            ))
    return items

def _plan_from_frontmatter(meta: dict[str, Any], body: str) -> CoveragePlan:
    testable = tuple(
        _item_from_frontmatter_row(item)
        for item in (meta.get("testable") or [])
        if isinstance(item, dict)
    )
    not_testable = tuple(
        _item_from_frontmatter_row(item, not_testable=True)
        for item in (meta.get("not_testable") or [])
        if isinstance(item, dict)
    )
    return CoveragePlan(
        testable=testable, not_testable=not_testable, raw=meta, parsed=True, markdown_body=body,
    )

def _plan_from_body_tables(body: str) -> CoveragePlan | None:
    sections = _parse_sections(body)
    # Slim template uses §3 Test Cases / §4 Deferred; legacy used §7 / §13.
    testable = _items_from_table(_table_rows(sections.get(3, "") or sections.get(7, "")), section=7)
    not_testable = _items_from_table(
        _table_rows(sections.get(4, "") or sections.get(13, "")), section=13,
    )
    if not testable and not not_testable:
        return None
    return CoveragePlan(
        testable=tuple(testable), not_testable=tuple(not_testable),
        raw={"schema_version": 2}, parsed=True, markdown_body=body,
    )

def parse_plan_markdown(text: str) -> CoveragePlan:
    if not text or not str(text).strip():
        return CoveragePlan(parsed=False, fallback_reason="missing markdown plan")
    body_text = str(text).strip()
    meta, body = split_frontmatter(body_text)
    if meta is None:
        recovered = recover_plan_from_body_tables(body_text)
        if recovered.parsed and (recovered.testable or recovered.not_testable):
            return recovered
        return CoveragePlan(
            parsed=False, fallback_reason="missing or invalid YAML frontmatter", markdown_body=body_text,
        )
    return _plan_from_frontmatter(meta, body_text)

def recover_plan_from_body_tables(text: str) -> CoveragePlan:
    body = str(text or "").strip()
    if not body:
        return CoveragePlan(parsed=False, fallback_reason="missing markdown plan")
    _meta, split_body = split_frontmatter(body)
    if split_body.strip():
        body = split_body.strip()
    plan = _plan_from_body_tables(body)
    if plan is None:
        return CoveragePlan(
            parsed=False, fallback_reason="missing or invalid YAML frontmatter", markdown_body=str(text).strip(),
        )
    return replace(plan, markdown_body=str(text).strip())

def _item_from_frontmatter_row(payload: dict[str, Any], *, not_testable: bool = False) -> PlanGapItem:
    branch_targets, tc_ids = _str_tuple(payload.get("branch_targets")), _str_tuple(payload.get("tc_ids"))
    approach = str(payload.get("approach") or "").strip() or ("branch: " + "; ".join(branch_targets) if branch_targets else "")
    fixture = str(payload.get("fixture_hint") or "").strip() or ("tickets: " + ", ".join(tc_ids) if tc_ids else "")
    return _item_from_dict(
        {
            **payload,
            "approach": approach,
            "fixture_hint": fixture,
            "tc_ids": list(tc_ids) or list(payload.get("tc_ids") or ()),
            "branch_targets": list(branch_targets) or list(payload.get("branch_targets") or ()),
        },
        not_testable=not_testable,
    )

def sync_frontmatter_after_clamp(plan: CoveragePlan, source_path: str) -> Path | None:
    path = Path(plan.markdown_path) if plan.markdown_path else plan_path_for_source(source_path)
    text = plan.markdown_body or ""
    if not text and path.is_file():
        text = file_cache.read_text(path, default="")
        if not text:
            return None
    if not text.strip():
        return None
    meta, body = split_frontmatter(text)
    meta = dict(meta or {"schema_version": 2})
    meta["schema_version"] = int(meta.get("schema_version") or 2)
    if str(meta.get("mocking_lane") or "").strip().lower() not in ("mockito", "mockk"):
        meta["mocking_lane"] = plan_frontmatter_mocking_lane()
    raw = _plan_raw_map(plan)
    if str(raw.get("recipe_id") or "").strip():
        meta["recipe_id"] = str(raw.get("recipe_id")).strip()
    elif not str(meta.get("recipe_id") or "").strip():
        meta["recipe_id"] = "none"
    if str(raw.get("recipe_lane") or "").strip():
        meta["recipe_lane"] = str(raw.get("recipe_lane")).strip()
    primary = str(raw.get("primary_test_layer") or "").strip()
    if primary:
        meta["primary_test_layer"] = primary
    meta["testable"] = [_item_to_frontmatter(item) for item in plan.testable]
    meta["not_testable"] = [_item_to_frontmatter(item, not_testable=True) for item in plan.not_testable]
    meta["delta_allow_list"] = sorted({int(n) for item in list(plan.testable) + list(plan.not_testable) for n in item.lines})
    synced = _dump_yaml_frontmatter(meta, body.lstrip())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(synced.rstrip() + "\n", encoding="utf-8")
    file_cache.invalidate(path)
    return path.resolve()

def _item_to_frontmatter(item: PlanGapItem, *, not_testable: bool = False) -> dict[str, Any]:
    row: dict[str, Any] = {"lines": list(item.lines), "tag": item.tag or "", "methods": list(item.methods)}
    if not_testable or item.reason or item.category:
        row["reason"], row["category"] = item.reason or "", item.category or ("not_unit_testable" if not_testable else "")
    else:
        for key in ("approach", "fixture_hint"):
            if getattr(item, key, ""):
                row[key] = getattr(item, key)
        if item.risks:
            row["risks"] = list(item.risks)
        tc_ids = list(item.tc_ids) if item.tc_ids else (
            [p.strip() for p in item.fixture_hint[9:].split(",") if p.strip()]
            if item.fixture_hint.startswith("tickets: ") else []
        )
        if tc_ids:
            row["tc_ids"] = tc_ids
        branch_targets = list(item.branch_targets) if item.branch_targets else (
            [p.strip() for p in item.approach[8:].split(";") if p.strip()]
            if item.approach.startswith("branch: ") else []
        )
        if branch_targets:
            row["branch_targets"] = branch_targets
        if item.test_layer:
            row["test_layer"] = item.test_layer
    return row

def _filter_section7_table(section7: str, allow_lines: set[int]) -> str:
    lines = section7.splitlines()
    header_idx = next((i for i, line in enumerate(lines) if line.strip().startswith("|")), None)
    if header_idx is None:
        return section7
    header_cells = [c.strip() for c in lines[header_idx].strip().strip("|").split("|")]
    kover_idx = next((i for i, name in enumerate(header_cells) if "kover" in name.lower()), 0)
    kept = lines[: header_idx + 1]
    if header_idx + 1 < len(lines) and not lines[header_idx + 1].replace("|", "").replace("-", "").replace(":", "").replace(" ", ""):
        kept.append(lines[header_idx + 1])
        start = header_idx + 2
    else:
        start = header_idx + 1
    for line in lines[start:]:
        if not line.strip().startswith("|"):
            kept.append(line)
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if kover_idx < len(cells) and _parse_kover_lines_cell(cells[kover_idx]) & allow_lines:
            kept.append(line)
    return "\n".join(kept).rstrip()

def format_plan_slice_for_coder(
    md_text: str, slice_plan: CoveragePlan, *, slice_tag: str = "",
    later_tags: list[str] | tuple[str, ...] = (), plan_path: str = "",
) -> str:
    allow = {int(n) for item in (getattr(slice_plan, "testable", ()) or ()) for n in (getattr(item, "lines", ()) or ())}
    if not allow and not getattr(slice_plan, "testable", ()):
        return "CODER PLAN: no testable gaps."
    _meta, body = split_frontmatter(md_text or "")
    sections = _parse_sections(body)
    slice_line = f"tag={slice_tag} lines={sorted(allow)}" if slice_tag else f"lines={sorted(allow)}"
    header = [
        "EXECUTE BLUEPRINT ONLY",
        f"Plan file (authoritative): {plan_path or '(inline)'}",
        f"This slice: {slice_line}",
        "Implement only Test Cases rows whose Kover Lines intersect this slice.",
        "Do not implement Deferred rows. Do not replan, defer, or re-analyze Kover.",
        "Partial branches: implement the named Branch Target exactly.",
        "view/Read SOURCE or TARGET only to fix compile errors — not for coverage triage.",
        _MERGE_TARGET_EXISTING, _MERGE_TARGET_NEW + ".", "",
        "--- ORDERED APPROACH (write test code in this step order) ---",
        *_format_coder_plan_items(getattr(slice_plan, "testable", ()) or ()), "",
        "--- PLAN (slice-filtered) ---",
    ]
    # Slim template: 1 Target, 2 Infra, 3 Test Cases, 4 Deferred.
    # Legacy plans may still use §7 for Test Cases — include filtered if present.
    for num in (1, 2, 3):
        if num not in sections:
            continue
        chunk = sections[num]
        if num == 3:
            chunk = _filter_section7_table(chunk, allow)
        header.extend([chunk, ""])
    # Legacy plans used §7 for Test Cases — always include filtered when present.
    if 7 in sections:
        header.extend([_filter_section7_table(sections[7], allow), ""])
    if later_tags:
        header.append("LATER SLICES (do not implement in this pass): " + ", ".join(later_tags))
    header.extend(_format_deferred_block(getattr(slice_plan, "not_testable", ()) or ()))
    if not sections and _meta:
        header.extend(["(Plan body sections missing — using frontmatter items)", *_format_coder_plan_items(slice_plan.testable)])
    return "\n".join(header).rstrip()

import re
from pathlib import Path
from typing import Any

from UnitTest_gen.core import io as file_cache
from UnitTest_gen.kotlin.recipes import selected_recipe_id, selected_recipe_selection
from UnitTest_gen.kotlin.codegen import RepairTicket

_PRIVATE_ASSERT_RE = re.compile(
    r"(?:assert\w*|verify\w*|Truth\.assertThat)\s*\([^)\n]*\._[A-Za-z_]"
    r"|[A-Za-z_]\w*\._(?:binding|viewModel|adapter|presenter)\b"
    r"|getDeclaredField\s*\(",
    re.I,
)
_HOST_PLACEHOLDER_RE = re.compile(r"\bHostActivity\b")
_PLAIN_HILT_BLEED_RE = re.compile(
    r"\bHiltTestActivity\b|@HiltAndroidTest\b|HiltAndroidRule\b|HiltTestApplication\b|@BindValue\b"
)

def _gap_allow_lines(gap: Any) -> set[int]:
    if gap is None:
        return set()
    out: set[int] = set()
    for attr in ("missed_lines", "partial_branch_lines"):
        for n in getattr(gap, attr, None) or ():
            try:
                out.add(int(n))
            except (TypeError, ValueError):
                continue
    return out

def _plan_text(plan: Any) -> str:
    body = str(getattr(plan, "markdown_body", "") or "")
    if body.strip():
        return body
    path = str(getattr(plan, "markdown_path", "") or "")
    if path and Path(path).is_file():
        return file_cache.read_text(path, default="")
    chunks: list[str] = []
    for item in list(getattr(plan, "testable", ()) or ()) + list(getattr(plan, "not_testable", ()) or ()):
        chunks.append(str(getattr(item, "approach", "") or ""))
        chunks.append(str(getattr(item, "fixture_hint", "") or ""))
        chunks.append(str(getattr(item, "reason", "") or ""))
    return "\n".join(chunks)

def _resolve_lane_and_recipe(
    plan: Any,
    source_code: str,
    *,
    recipe_id: str,
    recipe_lane: str,
) -> tuple[str, str]:
    raw = getattr(plan, "raw", None)
    raw_map = raw if isinstance(raw, dict) else {}
    lane = (
        str(recipe_lane or "").strip()
        or str(raw_map.get("recipe_lane") or "").strip()
    )
    rid = (
        str(recipe_id or "").strip()
        or str(raw_map.get("recipe_id") or "").strip()
    )
    if not lane or not rid or rid == "none":
        sel_lane, sel_id = selected_recipe_selection(source_code)
        lane = lane or sel_lane
        rid = rid if rid and rid != "none" else sel_id
    if not rid:
        rid = selected_recipe_id(source_code)
    return lane, rid

def validate_plan_markdown(
    plan: Any,
    source_code: str = "",
    gap: Any = None,
    *,
    recipe_id: str = "",
    recipe_lane: str = "",
) -> list[RepairTicket]:
    """Return plan RepairTickets for policy the Python loop owns (not prompt tables)."""
    tickets: list[RepairTicket] = []
    text = _plan_text(plan)
    if not text.strip() and not getattr(plan, "testable", ()):
        return [
            RepairTicket(
                code="plan_empty",
                message="Plan body/frontmatter is empty after clamp.",
                source="plan",
                severity="error",
            )
        ]

    if _PRIVATE_ASSERT_RE.search(text):
        tickets.append(
            RepairTicket(
                code="plan_private_field_assert",
                message=(
                    "Plan approach/Test Cases asserts private fields "
                    "(e.g. ._binding / getDeclaredField). Use public UI/API only."
                ),
                source="plan",
                hint="Rewrite asserts to public members or ViewAssertions; never private CUT fields.",
            )
        )

    lane, rid = _resolve_lane_and_recipe(
        plan, source_code, recipe_id=recipe_id, recipe_lane=recipe_lane,
    )

    if _HOST_PLACEHOLDER_RE.search(text):
        if lane.startswith("hilt_"):
            hint = "Replace HostActivity with the module HiltTestActivity / EntryPoint host from the PRIMARY recipe."
        else:
            hint = "Replace HostActivity with AppCompatActivity (plain Robolectric host from the PRIMARY recipe)."
        tickets.append(
            RepairTicket(
                code="plan_placeholder_host",
                message=(
                    f"Plan uses HostActivity but lane {lane!r} / recipe {rid!r} requires the "
                    "PRIMARY recipe host — not a placeholder class."
                ),
                source="plan",
                hint=hint,
            )
        )

    if lane in ("robolectric_non_hilt", "plain_jvm") and _PLAIN_HILT_BLEED_RE.search(text):
        tickets.append(
            RepairTicket(
                code="plan_wrong_harness_lane",
                message=(
                    f"Plan uses Hilt host/annotations but lane is {lane!r} "
                    f"(PRIMARY recipe {rid!r}). Use AppCompatActivity / plain Robolectric only."
                ),
                source="plan",
                hint="Remove HiltTestActivity / @HiltAndroidTest / HiltAndroidRule; follow the plain PRIMARY recipe.",
            )
        )

    if lane.startswith("hilt_") and _HOST_PLACEHOLDER_RE.search(text) is None:
        # Already ticketed HostActivity above; also flag inventing plain-only attach when Hilt.
        if re.search(r"launchFragmentInContainer", text) and "hilt_fragment" in lane:
            tickets.append(
                RepairTicket(
                    code="plan_wrong_harness_lane",
                    message=(
                        "Hilt Fragment lane forbids FragmentScenario.launchFragmentInContainer "
                        "as the attach strategy — use Hilt host + commitNow per PRIMARY recipe."
                    ),
                    source="plan",
                    hint="Follow hilt_fragment PRIMARY Ordered write steps (HiltTestActivity / module host).",
                )
            )

    allow = _gap_allow_lines(gap)
    if allow:
        for item in getattr(plan, "testable", ()) or ():
            bad = [int(n) for n in (getattr(item, "lines", ()) or ()) if int(n) not in allow]
            if bad:
                tickets.append(
                    RepairTicket(
                        code="plan_lines_outside_delta",
                        message=f"Testable item lines outside DELTA allow-list: {bad}",
                        source="plan",
                        lines=tuple(bad),
                        hint="Keep only missed/partial-branch DELTA lines in testable items.",
                    )
                )

    for item in getattr(plan, "testable", ()) or ():
        layer = str(getattr(item, "test_layer", "") or "").strip().lower()
        approach = str(getattr(item, "approach", "") or "")
        fixture = str(getattr(item, "fixture_hint", "") or "")
        combined = f"{approach}\n{fixture}"
        if layer == "instrumented" and re.search(r"RobolectricTestRunner|@Config\(application", combined):
            tickets.append(
                RepairTicket(
                    code="plan_layer_harness_mismatch",
                    message="Instrumented item references Robolectric harness; use @HiltAndroidTest + ActivityScenario.",
                    source="plan",
                    hint="Follow instrumented PRIMARY recipe only for test_layer=instrumented items.",
                )
            )
        if layer == "unit" and re.search(r"ActivityScenario\.launch|@HiltAndroidTest", combined) and "plain_jvm" in lane:
            tickets.append(
                RepairTicket(
                    code="plan_layer_harness_mismatch",
                    message="Unit/plain_jvm item references instrumented-only harness.",
                    source="plan",
                    hint="Use JVM/Robolectric unit recipe for test_layer=unit items.",
                )
            )

    return tickets

import os
import re
from dataclasses import replace
from pathlib import Path
from typing import Any
from UnitTest_gen.core.config import (
    PLAN_ALLOWED_TOOLS,
    PLAN_AVAILABLE_TOOLS,
    PLAN_DENIED_TOOLS,
)
from UnitTest_gen.core.agent import AgentUnavailableError
from UnitTest_gen.core import io as file_cache
from UnitTest_gen.core.io import log_message
from UnitTest_gen.core.config import get_config
from UnitTest_gen.core.io import coerce_int_set, compact_ranges
from UnitTest_gen.core.agent import run_claude
from UnitTest_gen.kotlin.prompts import plan_target_status_line, plan_tool_block, PromptParts
from UnitTest_gen.kotlin.prompts import compose_user_prompt
from UnitTest_gen.kotlin.imports import (
    format_source_imports_block,
    resolve_source_imports,
)
from UnitTest_gen.kotlin.recipes import (
    format_api_doc_catalog,
    format_recipe_catalog,
    selected_recipe_id,
    selected_recipe_selection,
)
from UnitTest_gen.kotlin.analysis import (
    PLAIN_UNIT_TAG,
    COVERAGE_SLICE_PRIORITY,
    coverage_slice_tags,
    normalize_slice_tag,
)
from UnitTest_gen.kotlin.project import (
    find_owning_module_dir,
    format_nav_discovery_paths_block,
    format_owning_module_anchor,
    resolve_module_sdk_prompt_block_for_path,
)
from UnitTest_gen.kotlin.analysis import (
    classify_from_report,
    format_classification_brief,
)
from UnitTest_gen.kotlin.analysis import analyze_kotlin_code
from UnitTest_gen.kotlin.coverage import format_kover_for_prompt
from UnitTest_gen.kotlin.codegen import (
    format_slice_source_projection,
    format_target_excerpt,
)
from UnitTest_gen.kotlin.codegen import read_test_file_state
from UnitTest_gen.kotlin.prompts import load_skeleton

_PLAN_USER_PROMPT = load_skeleton("plan_user_prompt.md")
_PLAN_MARKDOWN_SKELETON_ABS_PATH = str(
    (Path(__file__).resolve().parents[1] / "data" / "prompt_skeletons" / "plan_markdown_template.md").resolve()
)

def delta_line_set(gap) -> set[int]:
    """Missed + partial-branch line numbers that form the Kover delta allow-list."""
    return coerce_int_set(
        list(getattr(gap, "missed_lines", None) or [])
        + list(getattr(gap, "partial_branch_lines", None) or [])
    )

def _clip_items(
    items: tuple[PlanGapItem, ...],
    allow: set[int],
    *,
    dropped: list[int],
) -> list[PlanGapItem]:
    kept: list[PlanGapItem] = []
    for item in items:
        original = set(item.lines)
        clipped = tuple(sorted(original & allow))
        dropped.extend(sorted(original - allow))
        if not clipped:
            continue
        kept.append(replace(item, lines=clipped) if clipped != item.lines else item)
    return kept

def _omitted_after_clamp_reason(lines) -> str:
    """Human reason for residual delta lines Python auto-defers after clamp."""
    ranges = compact_ranges(lines) if lines else "none"
    return (
        f"Auto-deferred: lines {ranges} were on the Kover DELTA allow-list but the planner "
        "never classified them in testable or not_testable (or only listed non-delta neighbors "
        "that were dropped during clamp). Pipeline filled them as not_testable so every delta "
        "line is accounted for — re-run planner to classify them explicitly."
    )

def clamp_plan_to_delta(plan: CoveragePlan, gap) -> CoveragePlan:
    """Keep only delta lines in testable/not_testable; fold leftover delta into testable."""
    allow = delta_line_set(gap)
    if not plan.parsed:
        return plan
    if not allow:
        if plan.testable or plan.not_testable:
            log_message(
                "🧭 Clamped plan: empty Kover delta — clearing invented plan rows.",
                category="warning",
            )
        return CoveragePlan(
            testable=(),
            not_testable=(),
            raw=plan.raw,
            parsed=True,
            fallback_reason=plan.fallback_reason,
            markdown_path=plan.markdown_path,
            markdown_body=plan.markdown_body,
        )

    dropped: list[int] = []
    testable = _clip_items(plan.testable, allow, dropped=dropped)
    not_testable = _clip_items(plan.not_testable, allow, dropped=dropped)
    if dropped:
        log_message(
            f"🧭 Clamped plan: dropped non-delta lines {sorted(set(dropped))}",
            category="warning",
        )

    accounted: set[int] = set()
    for item in testable + not_testable:
        accounted.update(item.lines)
    missing = sorted(allow - accounted)
    if missing:
        # Fold into testable so coder can cover; do not invent omitted_or_non_delta_after_clamp.
        # Residual/short-circuit demotion stays in demote_partial_branch_residuals.
        fold_tag = (testable[0].tag if testable else "") or PLAIN_UNIT_TAG
        testable = list(testable) + [
            PlanGapItem(
                lines=tuple(missing),
                tag=fold_tag.strip() or PLAIN_UNIT_TAG,
                approach=(
                    "Cover remaining DELTA lines omitted from planner lines[] "
                    "(same harness as other testable items)."
                ),
            )
        ]
        log_message(
            f"🧭 Clamped plan: residual delta lines → testable (folded) {missing}",
            category="info",
        )

    return CoveragePlan(
        testable=tuple(testable),
        not_testable=tuple(not_testable),
        raw=plan.raw,
        parsed=True,
        fallback_reason=plan.fallback_reason,
        markdown_path=plan.markdown_path,
        markdown_body=plan.markdown_body,
    )

def exclusive_partition_plan_lines(plan: CoveragePlan) -> CoveragePlan:
    """Ensure each delta line appears in at most one of testable / not_testable.

    Prefer not_testable on overlap (deferred/residual wins over a duplicate testable row).
    """
    if not plan.parsed:
        return plan
    deferred_lines: set[int] = set()
    for item in plan.not_testable or ():
        deferred_lines.update(int(n) for n in (item.lines or ()))
    if not deferred_lines:
        return plan

    stripped: list[int] = []
    kept: list[PlanGapItem] = []
    for item in plan.testable or ():
        remaining = tuple(n for n in (item.lines or ()) if int(n) not in deferred_lines)
        dropped = [int(n) for n in (item.lines or ()) if int(n) in deferred_lines]
        if dropped:
            stripped.extend(dropped)
        if not remaining:
            continue
        if remaining == item.lines:
            kept.append(item)
        else:
            kept.append(replace(item, lines=_int_tuple(remaining)))
    if not stripped:
        return plan
    log_message(
        f"🧭 Plan exclusive partition: lines in both arrays → not_testable only "
        f"{sorted(set(stripped))}",
        category="info",
    )
    return replace(plan, testable=tuple(kept), not_testable=tuple(plan.not_testable or ()))

def ensure_not_testable_details(plan: CoveragePlan) -> CoveragePlan:
    """Fill blank reason/category/tag on not_testable items; never truncate existing reasons."""
    if not plan.not_testable:
        return plan
    filled: list[PlanGapItem] = []
    changed = False
    for item in plan.not_testable:
        tag = (item.tag or "").strip() or PLAIN_UNIT_TAG
        category = (item.category or "").strip() or "not_unit_testable"
        reason = (item.reason or "").strip()
        if not reason:
            line_txt = compact_ranges(item.lines) if item.lines else "none"
            method_txt = ", ".join(item.methods) if item.methods else "none"
            reason = (
                f"Deferred ({category}) on lines {line_txt} (methods: {method_txt}): "
                "planner marked these delta lines not unit-testable."
            )
        elif (
            category == "omitted_or_non_delta_after_clamp"
            and reason == "omitted_or_non_delta_after_clamp"
        ):
            reason = _omitted_after_clamp_reason(item.lines)
        if tag != item.tag or category != item.category or reason != item.reason:
            changed = True
            filled.append(replace(item, tag=tag, category=category, reason=reason))
        else:
            filled.append(item)
    if not changed:
        return plan
    return replace(plan, not_testable=tuple(filled))

def _looks_public_item(item: PlanGapItem) -> bool:
    """Planner said testable with a method or approach — treat as leftover public logic."""
    return bool(item.methods) or bool(str(item.approach or "").strip())

def clamp_plan_tags(plan: CoveragePlan, *, slice_tags: list[str] | tuple[str, ...] = ()) -> CoveragePlan:
    """Normalize planner tags onto the slice allow-list.

    Unknown/missing tags become ``plain_unit`` when the item looks like public logic;
    otherwise they move to not_testable with category ``untagged_or_invalid_slice``.
    """
    if not plan.parsed:
        return plan
    allow = {normalize_slice_tag(t) for t in slice_tags if normalize_slice_tag(t)}
    allow.add(PLAIN_UNIT_TAG)

    testable: list[PlanGapItem] = []
    not_testable: list[PlanGapItem] = []

    def _keep_tag(item: PlanGapItem, mapped: str) -> PlanGapItem:
        return item if item.tag == mapped else replace(item, tag=mapped)

    for item in plan.testable:
        mapped = normalize_slice_tag(item.tag)
        if mapped and (mapped in allow or mapped in COVERAGE_SLICE_PRIORITY):
            testable.append(_keep_tag(item, mapped))
            continue
        if _looks_public_item(item):
            testable.append(replace(item, tag=PLAIN_UNIT_TAG))
            continue
        not_testable.append(
            replace(
                item,
                tag=PLAIN_UNIT_TAG,
                reason=item.reason or "untagged_or_invalid_slice",
                category="untagged_or_invalid_slice",
            )
        )

    for item in plan.not_testable:
        mapped = normalize_slice_tag(item.tag)
        not_testable.append(_keep_tag(item, mapped or PLAIN_UNIT_TAG))

    return CoveragePlan(
        testable=tuple(testable),
        not_testable=tuple(not_testable),
        raw=plan.raw,
        parsed=True,
        fallback_reason=plan.fallback_reason,
        markdown_path=plan.markdown_path,
        markdown_body=plan.markdown_body,
    )

def group_plan_by_tag(plan: CoveragePlan) -> list[tuple[str, CoveragePlan]]:
    """Split testable items into priority-ordered per-tag plans."""
    buckets: dict[str, list[PlanGapItem]] = {}
    for item in plan.testable:
        tag = normalize_slice_tag(item.tag) or PLAIN_UNIT_TAG
        buckets.setdefault(tag, []).append(item if item.tag == tag else replace(item, tag=tag))
    extra = [tag for tag in buckets if tag not in COVERAGE_SLICE_PRIORITY]
    ordered = [tag for tag in COVERAGE_SLICE_PRIORITY if tag in buckets] + extra
    slices: list[tuple[str, CoveragePlan]] = []
    for tag in ordered:
        slices.append(
            (
                tag,
                CoveragePlan(
                    testable=tuple(buckets[tag]),
                    not_testable=plan.not_testable,
                    raw=plan.raw,
                    parsed=True,
                    fallback_reason=plan.fallback_reason,
                    markdown_path=plan.markdown_path,
                    markdown_body=plan.markdown_body,
                ),
            )
        )
    return slices

SLICE_MAX_PLAN_ITEMS = 5
SLICE_MAX_DELTA_LINES = 30
SLICE_MAX_METHODS = 2

def _item_line_count(item: PlanGapItem) -> int:
    return len(item.lines)

def _owner_method_for_item(item: PlanGapItem, *, analysis_report=None) -> str:
    methods = list(item.methods or ())
    if methods:
        return str(methods[0]).strip() or "<unknown>"
    if analysis_report is not None and item.lines:
        try:
            line_no = int(min(item.lines))
        except (TypeError, ValueError):
            return "<unknown>"
        fn = analysis_report.function_for_line(line_no, public_only=False)
        if fn is not None and getattr(fn, "name", ""):
            return str(fn.name)
    return "<unknown>"

def _owner_start_line(name: str, items: list[PlanGapItem], *, analysis_report=None) -> int:
    lines: list[int] = []
    for item in items:
        lines.extend(int(n) for n in item.lines)
    if lines:
        return min(lines)
    if analysis_report is not None:
        for fn in getattr(analysis_report, "functions", ()) or ():
            if getattr(fn, "name", "") == name:
                return int(getattr(getattr(fn, "span", None), "start_line", 0) or 0)
    return 0

def _split_oversized_item(item: PlanGapItem, *, max_lines: int) -> list[PlanGapItem]:
    """Split one plan item into line-range chunks when it exceeds max_lines."""
    lines = list(item.lines)
    if len(lines) <= max_lines:
        return [item]
    chunks: list[PlanGapItem] = []
    for start in range(0, len(lines), max_lines):
        part = lines[start : start + max_lines]
        chunks.append(replace(item, lines=tuple(part)))
    return chunks

def chunk_plan_by_method(
    plan: CoveragePlan,
    *,
    source_code: str = "",
    max_plan_items: int = SLICE_MAX_PLAN_ITEMS,
    max_delta_lines: int = SLICE_MAX_DELTA_LINES,
    max_methods: int = SLICE_MAX_METHODS,
) -> list[tuple[str, CoveragePlan]]:
    """After tag grouping, sub-chunk each tag by method + item/line budgets."""
    from UnitTest_gen.kotlin.analysis import analyze_kotlin_code

    analysis = None
    if source_code.strip():
        try:
            analysis = analyze_kotlin_code(source_code)
        except Exception:
            analysis = None

    out: list[tuple[str, CoveragePlan]] = []
    for tag, tag_plan in group_plan_by_tag(plan):
        if not tag_plan.testable:
            continue
        # Expand oversize single items first.
        expanded: list[PlanGapItem] = []
        for item in tag_plan.testable:
            expanded.extend(_split_oversized_item(item, max_lines=max_delta_lines))

        by_owner: dict[str, list[PlanGapItem]] = {}
        for item in expanded:
            owner = _owner_method_for_item(item, analysis_report=analysis)
            by_owner.setdefault(owner, []).append(item)

        owners = sorted(
            by_owner.keys(),
            key=lambda name: (_owner_start_line(name, by_owner[name], analysis_report=analysis), name),
        )

        batch_items: list[PlanGapItem] = []
        batch_methods: set[str] = set()
        batch_lines = 0

        def _flush() -> None:
            nonlocal batch_items, batch_methods, batch_lines
            if not batch_items:
                return
            out.append(
                (
                    tag,
                    CoveragePlan(
                        testable=tuple(batch_items),
                        not_testable=tag_plan.not_testable,
                        raw=tag_plan.raw,
                        parsed=True,
                        fallback_reason=tag_plan.fallback_reason,
                        markdown_path=tag_plan.markdown_path,
                        markdown_body=tag_plan.markdown_body,
                    ),
                )
            )
            batch_items = []
            batch_methods = set()
            batch_lines = 0

        for owner in owners:
            owner_items = by_owner[owner]
            owner_line_count = sum(_item_line_count(i) for i in owner_items)
            # If adding this owner would exceed budgets and batch is non-empty, flush.
            would_exceed = (
                batch_items
                and (
                    len(batch_items) + len(owner_items) > max_plan_items
                    or batch_lines + owner_line_count > max_delta_lines
                    or (owner not in batch_methods and len(batch_methods) >= max_methods)
                )
            )
            if would_exceed:
                _flush()

            # Owner alone larger than budgets: emit as its own batch(es) by items.
            if (
                len(owner_items) > max_plan_items
                or owner_line_count > max_delta_lines
            ):
                _flush()
                for item in owner_items:
                    item_lines = _item_line_count(item)
                    if (
                        batch_items
                        and (
                            len(batch_items) + 1 > max_plan_items
                            or batch_lines + item_lines > max_delta_lines
                        )
                    ):
                        _flush()
                    batch_items.append(item)
                    batch_methods.add(owner)
                    batch_lines += item_lines
                _flush()
                continue

            batch_items.extend(owner_items)
            batch_methods.add(owner)
            batch_lines += owner_line_count

        _flush()
    return out

def defer_all_plan(
    gap,
    *,
    reason: str,
    category: str = "plan_empty_or_unparseable",
    default_tag: str = "",
) -> CoveragePlan:
    """Treat remaining delta lines as not_testable — never invent a coder target.

    Empty/missing plan Markdown means either no usable plan output, or the model
    asserted nothing is unit-testable. Both skip the coder agent.

    Splits missed vs partial-branch lines so each bag carries a concrete reason
    (not one blank-tagged mega-item).
    """
    missed = _int_tuple(getattr(gap, "missed_lines", ()) or [])
    partial = _int_tuple(getattr(gap, "partial_branch_lines", ()) or [])
    missed_methods = _str_tuple(getattr(gap, "missed_methods", ()) or [])
    partial_methods = _str_tuple(getattr(gap, "partial_branch_methods", ()) or [])
    tag = (default_tag or PLAIN_UNIT_TAG).strip() or PLAIN_UNIT_TAG
    base = (reason or "").strip() or "plan response missing or empty testable/not_testable"

    items: list[PlanGapItem] = []
    if missed:
        items.append(
            PlanGapItem(
                lines=missed,
                methods=missed_methods,
                reason=(
                    f"{base}. Missed lines were never classified by a parseable Markdown plan; "
                    "re-run planner to emit testable coverage or a specific not_testable reason."
                ),
                category=category,
                tag=tag,
            )
        )
    if partial:
        # Prefer residual category for partial-only bags so store/UI show a real cause.
        partial_category = (
            "kover_residual_branch"
            if category == "plan_empty_or_unparseable"
            else category
        )
        items.append(
            PlanGapItem(
                lines=partial,
                methods=partial_methods,
                reason=(
                    f"{base}. Partial-branch lines (Kover mb>0) were never classified; "
                    "treat as residual until planner emits Markdown with per-line reasons "
                    "(e.g. kover_residual_branch naming TARGET tests that cover both arms)."
                ),
                category=partial_category,
                tag=tag,
            )
        )
    if not items:
        return CoveragePlan(parsed=False, fallback_reason=base)
    return CoveragePlan(
        testable=(),
        not_testable=tuple(items),
        parsed=False,
        fallback_reason=base,
    )

_KOVER_FALLBACK_APPROACH = (
    "Coder-only: cover Kover delta lines via public API (no saved plan)."
)
_KOVER_FALLBACK_FIXTURE = "Use recipe catalog + TARGET style; minimal fakes for selected lines."

def build_kover_fallback_plan(
    *,
    gap,
    source_path: str,
    source_code: str = "",
    existing_test_path: str = "",
    source_categories,
    test_path: str = "",
) -> CoveragePlan | None:
    """Synthesize an in-memory plan from filtered Kover delta when no saved plan exists."""
    delta = sorted(delta_line_set(gap))
    if not delta:
        return None

    slice_tags = coverage_slice_tags(source_categories)
    primary_tag = slice_tags[0] if slice_tags else PLAIN_UNIT_TAG
    methods = _str_tuple(
        list(getattr(gap, "missed_methods", ()) or [])
        + list(getattr(gap, "partial_branch_methods", ()) or [])
    )
    item = PlanGapItem(
        lines=tuple(delta),
        methods=methods,
        tag=primary_tag,
        approach=_KOVER_FALLBACK_APPROACH,
        fixture_hint=_KOVER_FALLBACK_FIXTURE,
    )
    plan = CoveragePlan(
        testable=(item,),
        not_testable=(),
        parsed=True,
        fallback_reason="kover_fallback_no_saved_plan",
    )

    existing_for_lane = ""
    if existing_test_path:
        existing_for_lane = file_cache.read_text(existing_test_path, default="")
    plan = ensure_plan_mocking_lane(plan, existing_test_code=existing_for_lane)
    recipe_lane, recipe_id = selected_recipe_selection(
        source_code, categories=list(source_categories or ()),
    )
    plan = ensure_plan_recipe_selection(
        plan, recipe_id=recipe_id, recipe_lane=recipe_lane,
    )
    plan = clamp_plan_tags(clamp_plan_to_delta(plan, gap), slice_tags=slice_tags)
    plan = ensure_not_testable_details(
        exclusive_partition_plan_lines(
            demote_partial_branch_residuals(
                plan, gap, target_code=existing_for_lane,
            ),
        )
    )
    from UnitTest_gen.core.config import get_config
    from UnitTest_gen.kotlin.project import find_source_root_for_path
    from UnitTest_gen.kotlin.layer import assign_test_layers, plan_primary_layer, test_output_path

    plan = assign_test_layers(
        plan,
        categories=frozenset(source_categories or ()),
        config=get_config(),
        source_file_path=source_path,
    )
    primary = plan_primary_layer(plan)
    resolved_test_path = test_path or existing_test_path
    source_root = find_source_root_for_path(source_path) if source_path else ""
    if source_root:
        resolved_test_path = test_output_path(
            source_file_path=source_path,
            source_root=source_root,
            layer=primary,
            output_base_directory=None,
        )
    body = render_plan_markdown(
        plan,
        source_path=source_path,
        test_path=resolved_test_path,
        reason="kover fallback (coder-only)",
    )
    return replace(plan, markdown_path="", markdown_body=body)

_TEST_FUN_NAME = re.compile(
    r"fun\s+`([^`]+)`\s*\(|fun\s+([A-Za-z_][\w]*)\s*\(",
)
_APPROACH_CALL = re.compile(
    r"(?:viewModel|sut|cut|activity|fragment|adapter|\w+)\.([A-Za-z_][\w]*)\s*\(",
    re.IGNORECASE,
)

def _partial_line_set(gap) -> set[int]:
    return set(_int_tuple(getattr(gap, "partial_branch_lines", ()) or []))

def _item_public_methods(item: PlanGapItem) -> tuple[str, ...]:
    names = [m for m in (item.methods or ()) if str(m).strip()]
    if names:
        return tuple(dict.fromkeys(str(m).strip() for m in names))
    found: list[str] = []
    for blob in (item.approach or "", item.fixture_hint or ""):
        for match in _APPROACH_CALL.finditer(blob):
            name = match.group(1)
            if name and name not in found:
                found.append(name)
    return tuple(found)

def _target_tests_calling(target_code: str, methods: tuple[str, ...]) -> list[str]:
    """Return TARGET @Test names that invoke any of the given public methods."""
    if not (target_code or "").strip() or not methods:
        return []
    blocks = re.split(r"@Test\b", target_code)
    hits: list[str] = []
    seen: set[str] = set()
    for block in blocks[1:]:
        name_match = _TEST_FUN_NAME.search(block)
        test_name = ""
        if name_match:
            test_name = (name_match.group(1) or name_match.group(2) or "").strip()
        for method in methods:
            if not method:
                continue
            if re.search(rf"(?<![\w]){re.escape(method)}\s*\(", block):
                label = test_name or f"(unnamed test calling {method})"
                if label not in seen:
                    seen.add(label)
                    hits.append(label)
                break
    return hits

def _residual_reason(
    *,
    kind: str,
    lines: tuple[int, ...],
    methods: tuple[str, ...],
    test_names: list[str],
) -> str:
    line_txt = compact_ranges(lines) if lines else "none"
    method_txt = ", ".join(methods) if methods else "none"
    tests = ", ".join(f"`{name}`" for name in test_names[:6]) or "existing TARGET tests"
    extra = "" if len(test_names) <= 6 else f" (+{len(test_names) - 6} more)"
    if kind == "missed":
        return (
            f"Kover residual missed-line probe on lines {line_txt}: TARGET already exercises the "
            f"method path via {tests}{extra}. Methods: {method_txt}. Remaining line miss appears "
            "synthetic/residual after logical behavior is asserted; no extra unit test planned."
        )
    return (
        f"Kover residual branch (mb>0) on lines {line_txt}: TARGET already exercises the "
        f"public path via {tests}{extra}. Methods: {method_txt}. Remaining probe is a "
        "synthetic/residual Kover branch after logical arms are covered; no additional "
        "complementary-arm unit test planned."
    )

def demote_partial_branch_residuals(
    plan: CoveragePlan,
    gap,
    *,
    target_code: str = "",
) -> CoveragePlan:
    """Demote residual testable items when TARGET already calls those methods.

    Applies to:
    - items whose lines are entirely within Kover partial-branch lines (mb>0)
    - synthetic missed-line probes when strong TARGET evidence exists (>=2 tests)
    Produces not_testable rows with category ``kover_residual_branch`` and a concrete
    reason naming TARGET tests.
    """
    if not plan.parsed or not plan.testable:
        return plan
    partial = _partial_line_set(gap)
    missed = set(_int_tuple(getattr(gap, "missed_lines", ()) or []))
    if not partial and not missed:
        return plan
    target = target_code or ""
    kept: list[PlanGapItem] = []
    demoted: list[PlanGapItem] = list(plan.not_testable or ())
    moved = 0
    for item in plan.testable:
        methods = _item_public_methods(item)
        tests = _target_tests_calling(target, methods)
        if not tests:
            kept.append(item)
            continue
        item_line_set = {int(n) for n in (item.lines or ())}
        partial_lines = tuple(sorted(item_line_set & partial))
        missed_lines = tuple(sorted(item_line_set & missed))
        demote_reason = ""
        demote_lines: tuple[int, ...] = ()
        # Demote pure partial-branch bags (no missed-line mix).
        if item.lines and not (item_line_set - partial):
            demote_lines = _int_tuple(partial_lines)
            demote_reason = _residual_reason(
                kind="branch",
                lines=demote_lines,
                methods=methods or item.methods,
                test_names=tests,
            )
        # Demote synthetic missed-line probes only with stronger TARGET evidence.
        elif item.lines and not (item_line_set - missed) and len(tests) >= 2:
            demote_lines = _int_tuple(missed_lines)
            demote_reason = _residual_reason(
                kind="missed",
                lines=demote_lines,
                methods=methods or item.methods,
                test_names=tests,
            )
        if not demote_reason:
            kept.append(item)
            continue
        tag = (item.tag or "").strip() or PLAIN_UNIT_TAG
        demoted.append(
            PlanGapItem(
                lines=demote_lines,
                methods=methods or item.methods,
                approach=item.approach,
                fixture_hint=item.fixture_hint,
                risks=item.risks,
                reason=demote_reason,
                category="kover_residual_branch",
                tag=tag,
                tc_ids=item.tc_ids,
                branch_targets=item.branch_targets,
            )
        )
        moved += 1
    if not moved:
        return plan
    log_message(
        f"🧭 Demoted {moved} partial-branch residual item(s) → not_testable "
        f"(kover_residual_branch); testable={len(kept)} not_testable={len(demoted)}.",
        category="info",
    )
    return replace(
        plan,
        testable=tuple(kept),
        not_testable=tuple(demoted),
        parsed=True,
    )

def build_plan_prompt(
    *,
    source_path: str,
    gap,
    existing_test_path: str = "",
    source_code: str = "",
    sibling_test_context: str = "",
    pipeline_test_path: str = "",
    slice_tags: list[str] | tuple[str, ...] = (),
    classification_brief: str = "",
) -> PromptParts:
    from UnitTest_gen.core.config import get_config
    from UnitTest_gen.kotlin.project import find_source_root_for_path
    from UnitTest_gen.kotlin.layer import (
        layer_policy_block,
        resolve_exclusive_layer,
        test_layer_guidance,
        test_output_path,
    )

    config = get_config()
    abs_source = str(Path(source_path).resolve())
    source_root = find_source_root_for_path(source_path)
    target_test = pipeline_test_path or existing_test_path
    if target_test:
        abs_test = str(Path(target_test).resolve())
        test_line = f"PIPELINE TARGET TEST FILE (absolute): {abs_test}"
        state = read_test_file_state(abs_test)
        existing_target = file_cache.read_text(abs_test, default="")
        status_line = plan_target_status_line(state, existing_target)
    else:
        abs_test = ""
        test_line = "PIPELINE TARGET TEST FILE: none yet"
        existing_target = ""
        status_line = "TARGET STATUS: unknown"
    if not source_code:
        source_code = file_cache.read_text(source_path, default="")
    report = analyze_kotlin_code(source_code, source_path)
    profile = classify_from_report(
        report,
        source_file_path=source_path,
        source_code=source_code,
    )
    layer_decision = resolve_exclusive_layer(
        profile.categories, config=config, source_file_path=source_path,
    )
    if not (pipeline_test_path or existing_test_path):
        pipeline_test_path = test_output_path(
            source_file_path=source_path,
            source_root=source_root,
            layer=layer_decision.layer,
        )
        target_test = pipeline_test_path
        abs_test = str(Path(target_test).resolve())
        test_line = f"PIPELINE TARGET TEST FILE (absolute): {abs_test}"
        state = read_test_file_state(abs_test)
        existing_target = file_cache.read_text(abs_test, default="")
        status_line = plan_target_status_line(state, existing_target)
    tags = list(slice_tags) if slice_tags else coverage_slice_tags(profile.categories)
    if PLAIN_UNIT_TAG not in tags:
        tags.append(PLAIN_UNIT_TAG)
    tag_list = ", ".join(tags) or PLAIN_UNIT_TAG
    brief = classification_brief or format_classification_brief(profile, report)
    api_docs = format_api_doc_catalog(
        source_code,
        categories=list(profile.categories),
    )
    recipes = format_recipe_catalog(
        source_code,
        categories=list(profile.categories),
    )
    recipe_lane, recipe_id = selected_recipe_selection(
        source_code, categories=list(profile.categories),
    )
    slice_source = format_slice_source_projection(
        abs_source,
        source_code,
        method_names=(),
        delta_lines=delta_line_set(gap),
        analysis_report=report,
    )
    from UnitTest_gen.kotlin.validate import (
        mocking_lane_prompt_block,
        resolve_mocking_lane,
    )

    resolved_lane = resolve_mocking_lane(existing_test_code=existing_target)
    plan_lane = plan_frontmatter_mocking_lane(existing_target)
    lane_block = mocking_lane_prompt_block(resolved_lane)
    if resolved_lane == "open":
        lane_block = "\n".join(
            [
                lane_block,
                "For this plan: set frontmatter mocking_lane: mockito and use Mockito-Kotlin "
                "syntax everywhere (approach, fixture_hint, Test Cases).",
            ]
        )
    target_excerpt = format_target_excerpt(existing_target, abs_test)
    plan_output = str(plan_path_for_source(abs_source).resolve())
    static = _PLAN_USER_PROMPT.format(
        abs_source=abs_source,
        test_line=test_line,
        status_line=status_line,
        plan_output=plan_output,
        plan_lane=plan_lane,
        recipe_id=recipe_id,
        recipe_lane=recipe_lane,
        slice_source="",
        target_excerpt="",
    ).strip()
    static = "\n".join(line for line in static.splitlines() if line.strip())
    cached_parts = [
        slice_source,
        target_excerpt,
        format_source_imports_block(
            resolve_source_imports(abs_source, source_code),
            source_code=source_code,
        ),
    ]
    if sibling_test_context.strip():
        cached_parts.extend([
            sibling_test_context.strip(),
            "Sibling tests are pattern references only; new coverage goes in the pipeline target file.",
        ])
    if api_docs:
        cached_parts.append(api_docs)
    cached_parts.append(recipes)
    sdk_block = resolve_module_sdk_prompt_block_for_path(abs_source)
    if sdk_block:
        cached_parts.append(sdk_block)
    dynamic_parts = [
        format_owning_module_anchor(abs_source),
    ]
    if "android_navigation" in profile.categories or "findNavController" in (source_code or ""):
        nav_block = format_nav_discovery_paths_block(abs_source, project_root=source_root or "")
        if nav_block:
            dynamic_parts.append(nav_block)
    dynamic_parts.extend([
        f"DELTA LINE ALLOW-LIST (only these numbers may appear in lines[]): "
        f"{compact_ranges(sorted(delta_line_set(gap)))}.",
        f"COVERAGE TAGS (assign each delta line to exactly one): {tag_list}.",
        "SOURCE CLASSIFICATION (authoritative — from tree-sitter; do not contradict):",
        brief,
        lane_block,
        "DELTA KOVER GAPS",
        format_kover_for_prompt(source_path, gap, include_windows=False),
    ])
    dynamic_parts.extend([
        test_layer_guidance(),
        layer_policy_block(layer_decision),
        "Do not set test_layer in frontmatter — Python assigns it per item after clamp.",
    ])
    return PromptParts(
        system=_plan_system_prompt(
            tags=list(profile.categories) + list(tags),
        ),
        user=compose_user_prompt(static=static, cached=cached_parts, dynamic=dynamic_parts),
    )

def format_plan_for_coder(
    plan: CoveragePlan,
    *,
    slice_tag: str = "",
    later_tags: list[str] | tuple[str, ...] = (),
) -> str:
    """Format plan for the coder. Prefer Markdown blueprint when available."""
    body = plan.markdown_body
    if not body and plan.markdown_path:
        body = file_cache.read_text(plan.markdown_path, default="")
    if (plan.markdown_body or plan.markdown_path) and (body or "").strip():
        return format_plan_slice_for_coder(body, plan, slice_tag=slice_tag, later_tags=later_tags, plan_path=plan.markdown_path)
    if not plan.testable:
        return "CODER PLAN: no testable gaps."
    header = f"CODER PLAN (SLICE tag={slice_tag} ONLY — implement these items, nothing else)" if slice_tag else "CODER PLAN (DELTA ONLY — implement these items, nothing else)"
    lines = [header, *_format_coder_plan_items(plan.testable)]
    if later_tags:
        lines.extend(["", "LATER SLICES (do not implement in this pass): " + ", ".join(later_tags)])
    lines.extend(_format_deferred_block(plan.not_testable))
    return "\n".join(lines)

def _ingest_plan_file(
    *, source_path: str, existing_for_lane: str, recipe_id: str = "none", recipe_lane: str = "",
) -> CoveragePlan:
    """Load plan only from on-disk PLAN OUTPUT ``*.plan.md`` (agent Write/Edit)."""
    path = plan_path_for_source(source_path)
    file_cache.invalidate(path)
    md = (load_plan_markdown(source_path) or "").strip()
    if not md:
        log_message(
            f"🧭 Plan file missing or empty (expected Write to {path})",
            category="warning",
        )
        return ensure_plan_recipe_selection(
            ensure_plan_mocking_lane(
                CoveragePlan(parsed=False, fallback_reason="plan file missing or empty"),
                existing_test_code=existing_for_lane,
            ),
            recipe_id=recipe_id,
            recipe_lane=recipe_lane,
        )
    parsed = parse_plan_markdown(md)
    if not parsed.parsed:
        log_message(
            f"🧭 Plan file did not parse: {parsed.fallback_reason} ({path})",
            category="warning",
        )
    return replace(
        ensure_plan_recipe_selection(
            ensure_plan_mocking_lane(parsed, existing_test_code=existing_for_lane),
            recipe_id=recipe_id,
            recipe_lane=recipe_lane,
        ),
        markdown_path=str(path.resolve()),
        markdown_body=md,
    )

def _clamp_plan(plan: CoveragePlan, gap, slice_tags) -> CoveragePlan:
    return exclusive_partition_plan_lines(clamp_plan_tags(clamp_plan_to_delta(plan, gap), slice_tags=slice_tags))

def _persist_plan_markdown(
    plan: CoveragePlan, *, source_path: str, existing_test_path: str, pipeline_test_path: str,
) -> CoveragePlan:
    body = plan.markdown_body or render_plan_markdown(
        plan, source_path=source_path, test_path=pipeline_test_path or existing_test_path, reason=plan.fallback_reason,
    )
    if not plan.markdown_body:
        saved = save_plan_markdown(source_path, body)
        plan = replace(plan, markdown_path=str(saved), markdown_body=body)
        log_message(f"🧭 Plan markdown rendered from structured output: {saved}", category="info")
    body = drop_unwanted_plan_data(plan.markdown_body)
    saved = save_plan_markdown(source_path, body)
    plan = replace(plan, markdown_path=str(saved), markdown_body=body)
    synced = sync_frontmatter_after_clamp(plan, source_path)
    if synced is not None:
        try:
            body = synced.read_text(encoding="utf-8", errors="replace")
        except OSError:
            body = plan.markdown_body
        plan = replace(plan, markdown_path=str(synced), markdown_body=body)
    return plan

def _skip_plan_markdown(plan: CoveragePlan, *, source_path: str) -> CoveragePlan:
    removed = delete_plan_markdown(source_path)
    log_message(
        "🧭 All delta lines not_testable — skipping plan.md"
        + (" (removed stale plan)" if removed else "")
        + f"; not_testable={len(plan.not_testable)} for unreachable store.",
        category="info",
    )
    return replace(plan, markdown_path="", markdown_body="")

def _run_plan_agent_call(
    user_prompt: str,
    *,
    title: str,
    project_root: str,
    prompt_parts: PromptParts,
    config,
    source_path: str = "",
):
    module_dir = ""
    if source_path and project_root:
        module_dir = find_owning_module_dir(project_root, source_path)
    return run_claude(
        user_prompt, cwd=project_root, system_prompt=prompt_parts.system,
        allowed_tools=PLAN_ALLOWED_TOOLS, denied_tools=PLAN_DENIED_TOOLS,
        available_tools=PLAN_AVAILABLE_TOOLS, timeout=config.agent_timeout_seconds,
        title=title, stream=True, temperature=config.plan_temperature,
        presence_penalty=config.plan_presence_penalty,
        enable_thinking=config.plan_enable_thinking,
        reasoning_budget=config.plan_thinking_budget if config.plan_enable_thinking else 0,
        phase="plan",
        owning_module_dir=module_dir,
    )

def _defer_plan_validation_failures(plan: CoveragePlan, tickets: list) -> CoveragePlan:
    """Move remaining testable items to not_testable using ticket categories."""
    if not plan.testable:
        return plan
    codes = ",".join(sorted({t.code for t in tickets})) or "plan_invalid"
    reason = (
        f"Plan failed Python post-validate after one repair turn ({codes}). "
        + "; ".join(t.message for t in tickets[:3])
    )
    demoted = [
        PlanGapItem(
            lines=item.lines,
            methods=item.methods,
            approach=item.approach,
            fixture_hint=item.fixture_hint,
            risks=item.risks,
            reason=reason,
            category=tickets[0].code if tickets else "plan_invalid",
            tag=item.tag,
            tc_ids=item.tc_ids,
            branch_targets=item.branch_targets,
        )
        for item in plan.testable
    ]
    return replace(
        plan,
        testable=(),
        not_testable=tuple(list(plan.not_testable) + demoted),
        parsed=True,
    )

def _build_plan_repair_prompt(*, plan_path: str, tickets: list) -> PromptParts:
    from UnitTest_gen.kotlin.codegen import format_tickets_for_prompt

    system = "\n".join(
        [
            "You are repairing a coverage plan.md after Python validation failed.",
            "Edit ONLY the plan file. Fix every REPAIR TICKET. Do not invent new DELTA lines.",
            plan_tool_block(),
            "After Edit/Write, reply with one line: PLAN_SAVED",
        ]
    )
    user = "\n".join(
        [
            f"PLAN FILE (absolute): {plan_path}",
            "REPAIR TICKETS (fix these only)",
            format_tickets_for_prompt(tickets),
            "Use Edit/Write on the plan file, then PLAN_SAVED.",
        ]
    )
    return PromptParts(system=system, user=user)

def _validate_and_repair_plan(
    plan: CoveragePlan,
    *,
    source_path: str,
    source_code: str,
    gap,
    project_root: str,
    recipe_id: str,
    recipe_lane: str = "",
    existing_for_lane: str,
    slice_tags,
    source_categories: frozenset[str] | set[str] | None = None,
    existing_test_path: str,
    pipeline_test_path: str,
    config,
) -> CoveragePlan:
    from UnitTest_gen.kotlin.plan import validate_plan_markdown

    tickets = validate_plan_markdown(
        plan, source_code, gap, recipe_id=recipe_id, recipe_lane=recipe_lane,
    )
    if not tickets:
        return plan

    plan_file = plan.markdown_path or str(plan_path_for_source(source_path).resolve())
    log_message(
        f"🧭 Plan post-validate failed ({len(tickets)} ticket(s)); one repair turn on {plan_file}",
        category="warning",
    )
    for ticket in tickets:
        log_message(f"   - [{ticket.code}] {ticket.message}", category="warning")

    repair_parts = _build_plan_repair_prompt(plan_path=plan_file, tickets=tickets)
    try:
        _run_plan_agent_call(
            repair_parts.user,
            title="PLAN REPAIR PROMPT",
            project_root=project_root,
            prompt_parts=repair_parts,
            config=config,
            source_path=source_path,
        )
    except (AgentUnavailableError, OSError) as exc:
        log_message(f"🧭 Plan repair unavailable ({exc}); deferring testable gaps.", category="warning")
        return _skip_plan_markdown(_defer_plan_validation_failures(plan, tickets), source_path=source_path)

    plan = _ingest_plan_file(
        source_path=source_path,
        existing_for_lane=existing_for_lane,
        recipe_id=recipe_id,
        recipe_lane=recipe_lane,
    )
    if not plan.parsed:
        return _skip_plan_markdown(_defer_plan_validation_failures(plan, tickets), source_path=source_path)

    plan = _clamp_plan(plan, gap, slice_tags)
    plan = ensure_not_testable_details(exclusive_partition_plan_lines(
        demote_partial_branch_residuals(plan, gap, target_code=existing_for_lane),
    ))
    if source_categories is not None:
        from UnitTest_gen.kotlin.layer import assign_test_layers

        plan = assign_test_layers(
            plan, categories=source_categories, config=config, source_file_path=source_path,
        )
    plan = _persist_plan_markdown(
        plan, source_path=source_path, existing_test_path=existing_test_path, pipeline_test_path=pipeline_test_path,
    )
    remaining = validate_plan_markdown(
        plan, source_code, gap, recipe_id=recipe_id, recipe_lane=recipe_lane,
    )
    if remaining:
        log_message(
            f"🧭 Plan still invalid after repair ({len(remaining)} ticket(s)); deferring (no coder).",
            category="warning",
        )
        return _skip_plan_markdown(_defer_plan_validation_failures(plan, remaining), source_path=source_path)
    return plan

def plan_coverage_gaps(
    *,
    source_path: str,
    gap,
    project_root: str,
    existing_test_path: str = "",
    sibling_test_context: str = "",
    pipeline_test_path: str = "",
    source_code: str = "",
) -> CoveragePlan:
    """Run the read-only plan agent and return a structured coverage plan."""
    config = get_config()
    if not source_code:
        source_code = file_cache.read_text(source_path, default="")
    report = analyze_kotlin_code(source_code, source_path)
    profile = classify_from_report(
        report,
        source_file_path=source_path,
        source_code=source_code,
    )
    slice_tags = coverage_slice_tags(profile.categories)
    brief = format_classification_brief(profile, report)
    prompt_parts = build_plan_prompt(
        source_path=source_path,
        gap=gap,
        existing_test_path=existing_test_path,
        sibling_test_context=sibling_test_context,
        pipeline_test_path=pipeline_test_path or existing_test_path,
        source_code=source_code,
        slice_tags=slice_tags,
        classification_brief=brief,
    )
    try:
        result = _run_plan_agent_call(
            prompt_parts.user,
            title="PLAN AGENT PROMPT",
            project_root=project_root, prompt_parts=prompt_parts, config=config,
            source_path=source_path,
        )
    except (AgentUnavailableError, OSError) as exc:
        log_message(
            f"🧭 Plan agent unavailable ({exc}); deferring all delta as not_testable (no coder).",
            category="warning",
        )
        deferred = defer_all_plan(gap, reason=str(exc), category="plan_agent_unavailable")
        removed = delete_plan_markdown(source_path)
        if removed:
            log_message("🧭 Removed stale plan.md after plan agent unavailable.", category="info")
        return replace(deferred, markdown_path="", markdown_body="")

    ack = (result.stdout or "").strip()
    if ack:
        log_message(f"🧭 Plan agent ack: {ack.splitlines()[0][:200]}", category="info")

    existing_for_lane = ""
    if target_for_lane := pipeline_test_path or existing_test_path:
        existing_for_lane = file_cache.read_text(target_for_lane, default="")

    recipe_lane, recipe_id = selected_recipe_selection(
        source_code, categories=list(profile.categories),
    )
    plan = _ingest_plan_file(
        source_path=source_path,
        existing_for_lane=existing_for_lane,
        recipe_id=recipe_id,
        recipe_lane=recipe_lane,
    )
    if not plan.parsed or (not plan.testable and not plan.not_testable):
        log_message(
            "🧭 Plan agent file missing/empty/unparseable; deferring all delta as not_testable (no coder).",
            category="warning",
        )
        return _skip_plan_markdown(
            defer_all_plan(gap, reason="plan file missing or empty testable/not_testable", category="plan_empty_or_unparseable"),
            source_path=source_path,
        )

    plan = _clamp_plan(plan, gap, slice_tags)
    plan = ensure_not_testable_details(exclusive_partition_plan_lines(
        demote_partial_branch_residuals(plan, gap, target_code=existing_for_lane),
    ))
    from UnitTest_gen.kotlin.layer import assign_test_layers

    plan = assign_test_layers(
        plan, categories=profile.categories, config=config, source_file_path=source_path,
    )
    if not plan.has_testable:
        plan = _skip_plan_markdown(plan, source_path=source_path)
        log_message(f"🧭 Plan agent: no testable gaps (not_testable={len(plan.not_testable)}); skip coder.", category="info")
        return plan

    plan = _persist_plan_markdown(
        plan, source_path=source_path, existing_test_path=existing_test_path, pipeline_test_path=pipeline_test_path,
    )
    plan = _validate_and_repair_plan(
        plan,
        source_path=source_path,
        source_code=source_code,
        gap=gap,
        project_root=project_root,
        recipe_id=recipe_id,
        recipe_lane=recipe_lane,
        existing_for_lane=existing_for_lane,
        slice_tags=slice_tags,
        source_categories=profile.categories,
        existing_test_path=existing_test_path,
        pipeline_test_path=pipeline_test_path,
        config=config,
    )
    if not plan.has_testable:
        log_message(
            f"🧭 Plan agent: no testable gaps after validate "
            f"(not_testable={len(plan.not_testable)}); skip coder.",
            category="info",
        )
        return plan
    log_message(
        f"🧭 Plan agent: testable={len(plan.testable)} not_testable={len(plan.not_testable)}"
        + (f" path={plan.markdown_path}" if plan.markdown_path else ""),
        category="info",
    )
    return plan

def testable_lines(plan: Any) -> set[int]:
    """Line numbers listed on plan.testable items."""
    return {int(n) for item in getattr(plan, "testable", ()) or () for n in getattr(item, "lines", ()) or () if str(n).isdigit() or isinstance(n, int)}

def uncovered_planned_lines(plan: Any, after_gap: Any) -> set[int]:
    """Planned testable lines still in after_gap missed or partial-branch sets."""
    if after_gap is None:
        return set()
    open_lines = {
        int(n)
        for n in list(getattr(after_gap, "missed_lines", None) or [])
        + list(getattr(after_gap, "partial_branch_lines", None) or [])
        if str(n).isdigit() or isinstance(n, int)
    }
    return testable_lines(plan) & open_lines
