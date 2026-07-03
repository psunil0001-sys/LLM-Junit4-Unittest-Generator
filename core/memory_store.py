# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Loads, ranks, sanitizes, and persists reusable generation and repair lessons.
import json
import os
import re
from pathlib import Path

from UnitTest_gen.core.logging_utils import log_message
from UnitTest_gen.core.rag_context import ContextItem, RetrievalQuery, format_context_blocks, rank_context_items


PACKAGE_DIR = str(Path(__file__).resolve().parents[1])
AI_MEMORY_FILE = os.environ.get("TESTGEN_AI_MEMORY_FILE", os.path.join(PACKAGE_DIR, "ai_test_memory.json"))

REPO_SPECIFIC_TEXT_REPLACEMENTS = [
    (r"(?:[a-z_][A-Za-z0-9_]*\.)+(?:digitalkey|journeylog|journetlog)(?:\.[A-Za-z0-9_]+)*", "<project.package>"),
    (r"/home/[^\\s\"']+", "<absolute-path>"),
]


def sanitize_memory_text(text, class_name=None, max_chars=None):
    """
    Keep AI memory reusable across repos by removing project-specific package,
    path, and current class identifiers from saved lessons.
    """
    if not text:
        return ""

    sanitized = str(text)
    for pattern, replacement in REPO_SPECIFIC_TEXT_REPLACEMENTS:
        sanitized = re.sub(pattern, replacement, sanitized)

    if class_name:
        sanitized = re.sub(rf"\b{re.escape(class_name)}\b", "<ClassUnderTest>", sanitized)
        sanitized = re.sub(rf"\b{re.escape(class_name)}Test\b", "<ClassUnderTest>Test", sanitized)

    sanitized = re.sub(r"^package\s+.+$", "package <project.package>", sanitized, flags=re.MULTILINE)
    sanitized = re.sub(r"import\s+<project\.package>[A-Za-z0-9_.*]*", "import <project.package>.<symbol>", sanitized)
    sanitized = re.sub(r"\n{3,}", "\n\n", sanitized).strip()

    if max_chars is not None:
        sanitized = sanitized[:max_chars]
    return sanitized


def generic_repair_memory_key(group_key):
    if not group_key:
        return group_key

    prefix_mappings = [
        ("Suspend function outside coroutine:", "Suspend function outside coroutine"),
        ("Argument type mismatch:", "Argument type mismatch"),
        ("CannotStubVoidMethodWithReturnValue:", "CannotStubVoidMethodWithReturnValue"),
        ("MockitoException: Only void methods can doNothing", "MockitoException: Only void methods can doNothing"),
    ]
    for prefix, generic_key in prefix_mappings:
        if group_key.startswith(prefix):
            return generic_key

    return group_key


def _load_ai_memory():
    if os.path.exists(AI_MEMORY_FILE):
        try:
            with open(AI_MEMORY_FILE, "r", encoding="utf-8") as f:
                memory = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            log_message(f"Failed to load AI memory from {AI_MEMORY_FILE}: {e}", category="warning")
            memory = {}
    else:
        memory = {}

    memory.setdefault("generation", {})
    memory.setdefault("repair", {})
    return memory


def _save_ai_memory(memory):
    tmp_path = AI_MEMORY_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(memory, f, indent=2)
        f.write("\n")
    os.replace(tmp_path, AI_MEMORY_FILE)


def _normalized_tags(tags) -> set[str]:
    if isinstance(tags, str):
        tags = [tags]
    return {str(tag).strip().lower() for tag in (tags or []) if str(tag).strip()}


def _lesson_text(key, item) -> str:
    fields = [key]
    if isinstance(item, dict):
        fields.extend(str(item.get(field, "")) for field in (
            "summary", "notes", "patch", "fixed_code_excerpt", "test_code_excerpt", "root_cause", "fix"
        ))
    return "\n".join(fields)


def _infer_memory_tags(key, item, tag_inference=None) -> set[str]:
    tags = _normalized_tags(item.get("tags") if isinstance(item, dict) else None)
    if tag_inference:
        tags.update(_normalized_tags(tag_inference(key, item)))
    if "universal" in tags:
        tags.add("universal")
    return tags


def _lesson_phase_matches(item, phase: str) -> bool:
    if not isinstance(item, dict):
        return False
    phases = _normalized_tags(item.get("phases"))
    return not phases or phase in phases


def _rank_memory_lessons(
    memory,
    section: str,
    source_categories,
    phase: str,
    limit: int,
    exact_key=None,
    tag_inference=None,
    required_categories=None,
    forbidden_categories=None,
):
    source_tags = _normalized_tags(source_categories)
    required_tags = _normalized_tags(required_categories)
    forbidden_tags = _normalized_tags(forbidden_categories)
    items = []
    for key, item in (memory.get(section, {}) or {}).items():
        if not isinstance(item, dict) or not _lesson_phase_matches(item, phase):
            continue
        lesson_tags = _infer_memory_tags(key, item, tag_inference)
        exact = bool(exact_key and key in {exact_key, generic_repair_memory_key(exact_key)})
        priority = int(item.get("priority", 50) or 50)
        items.append(
            ContextItem(
                key,
                _lesson_text(key, item),
                frozenset(lesson_tags),
                frozenset(_normalized_tags(item.get("phases"))),
                priority,
                (key, item, lesson_tags, exact),
            )
        )
    query = RetrievalQuery(frozenset(source_tags), frozenset(source_tags), phase)
    ranked = rank_context_items(
        items,
        query,
        limit=limit,
        eligibility_fn=lambda value, _: bool(
            not (forbidden_tags & set(value.tags))
            and (
                value.payload[3]
                or "universal" in value.tags
                or ((required_tags or source_tags) & set(value.tags))
            )
        ),
        extra_score_fn=lambda value, _: 1000 if value.payload[3] else 0,
    )
    return [(value.score, value.item.payload[0], value.item.payload[1], value.item.payload[2]) for value in ranked]


def _format_memory_lessons(selected, phase: str, max_chars: int = 5000) -> str:
    if not selected:
        return ""
    blocks = []
    for _, key, item, tags in selected:
        lines = [
            f"Lesson: {key}",
            f"Tags: {', '.join(sorted(tags)) or 'inferred'}",
            f"Summary: {item.get('summary', '')}",
        ]
        detail_fields = ("notes", "test_code_excerpt") if phase in {"generation", "incremental"} else ("patch", "fixed_code_excerpt", "fix")
        for field in detail_fields:
            value = str(item.get(field, "")).strip()
            if value:
                lines.append(f"{field.replace('_', ' ').title()}:\n{value}")
        blocks.append("\n".join(lines))
    return format_context_blocks(
        blocks,
        heading="### CLASSIFICATION-RETRIEVED MEMORY LESSONS",
        max_chars=max_chars,
    )


def retrieve_generation_lessons(
    source_categories,
    class_name=None,
    phase="generation",
    limit=3,
    tag_inference=None,
    required_categories=None,
    forbidden_categories=None,
):
    memory = _load_ai_memory()
    selected = _rank_memory_lessons(
        memory,
        section="generation",
        source_categories=source_categories,
        phase=phase,
        limit=limit,
        exact_key=class_name,
        tag_inference=tag_inference,
        required_categories=required_categories,
        forbidden_categories=forbidden_categories,
    )
    return _format_memory_lessons(selected, phase)


def add_generation_lesson(class_name, test_code, source_categories=None, source_code=""):
    memory = _load_ai_memory()
    memory.setdefault("generation", {})
    memory["generation"][class_name] = {
        "summary": "Previously generated meaningful compiling tests for this class shape.",
        "notes": (
            "Reuse only the generic testing pattern: runner choice, coroutine handling, "
            "mocking style, fixture shape, and assertion style. Preserve behavioral assertions, "
            "real public contracts, and verified lifecycle setup when the source requires it. "
            "Re-verify package names, class names, constructor signatures, and imports from the current repo."
        ),
        "test_code_excerpt": sanitize_memory_text(test_code, class_name=class_name, max_chars=2500),
        "tags": sorted(_normalized_tags(source_categories)),
        "phases": ["generation", "incremental"],
        "priority": 60,
    }
    _save_ai_memory(memory)


def retrieve_repair_lessons(
    source_categories,
    repair_categories=None,
    limit=2,
    tag_inference=None,
    required_categories=None,
):
    memory = _load_ai_memory()
    repair_tags = _normalized_tags(source_categories) | _normalized_tags(repair_categories)
    selected = _rank_memory_lessons(
        memory,
        section="repair",
        source_categories=repair_tags,
        phase="repair",
        limit=limit,
        exact_key=None,
        tag_inference=tag_inference,
        required_categories=required_categories or _normalized_tags(repair_categories),
    )
    return _format_memory_lessons(selected, "repair")


def add_repair_lesson(
    group_key,
    patch=None,
    fixed_code=None,
    source_categories=None,
    repair_categories=None,
    tag_inference=None,
):
    memory = _load_ai_memory()
    memory.setdefault("repair", {})
    memory["repair"][generic_repair_memory_key(group_key)] = {
        "summary": "Previous successful fix for this generic error shape.",
        "patch": sanitize_memory_text(patch, max_chars=2500),
        "fixed_code_excerpt": sanitize_memory_text(fixed_code, max_chars=1800),
        "tags": sorted(
            _normalized_tags(source_categories)
            | _normalized_tags(repair_categories)
            | _infer_memory_tags(group_key, {"summary": group_key}, tag_inference)
        ),
        "phases": ["repair"],
        "priority": 70,
    }
    _save_ai_memory(memory)
