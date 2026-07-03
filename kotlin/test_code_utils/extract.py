# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Kotlin test-code extraction, validation, and merge helpers (extract).
"""Kotlin test-code extraction, validation, and merge helpers — extract submodule."""

from __future__ import annotations

import json
import re

from UnitTest_gen.kotlin.kotlin_analysis import (
    ast_text,
    declaration_name,
    parse_kotlin_ast,
    walk_ast,
)

def _candidate_test_code_score(candidate: str) -> int:
    text = candidate.strip()
    if not text:
        return -1000

    score = 0
    if re.search(r"(?m)^\s*package\s+[\w.]+", text):
        score += 6
    if re.search(r"(?m)^\s*import\s+", text):
        score += 4
    if re.search(r"\bclass\s+\w+Test\b", text):
        score += 12
    elif re.search(r"\bclass\s+\w+", text):
        score += 4
    if "@Test" in text:
        score += 16
    if "import org.junit.Test" in text:
        score += 6
    if "RunWith" in text or "MockitoJUnitRunner" in text:
        score += 3
    if re.search(r"(?m)^\s*(Wait|Let's|Actually|However|Hypothesis|Resolution):?\b", text):
        score -= 10
    if "```" in text:
        score -= 20
    return score


def _trim_after_balanced_top_level_class(candidate: str) -> str:
    """
    Keep only the Kotlin file through the first balanced top-level class body.
    This removes common model spillover such as reasoning text or misplaced
    imports after the generated test class.
    """
    text = candidate.strip()
    class_match = re.search(r"\bclass\s+\w+Test\b|\bclass\s+\w+\b", text)
    if not class_match:
        return text

    brace_start = text.find("{", class_match.end())
    if brace_start == -1:
        return text

    depth = 0
    in_string = False
    string_quote = ""
    escaped = False
    for index in range(brace_start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == string_quote:
                in_string = False
            continue

        if char in {'"', "'"}:
            in_string = True
            string_quote = char
            continue

        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                trailing = text[index + 1:].strip()
                if not trailing or re.search(r"(?m)^\s*(import|Wait|Let's|Actually|However|One correction|Refining)\b", trailing):
                    return text[:index + 1].strip()
                return text

    return text


def _extract_fenced_kotlin_candidates(raw_output: str) -> list[str]:
    candidates = [
        match.group(1).strip()
        for match in re.finditer(
            r"```(?:kotlin|kt)?\s*(.*?)\s*```",
            raw_output,
            re.DOTALL | re.IGNORECASE,
        )
    ]
    return [candidate for candidate in candidates if candidate.strip()]


def extract_kotlin_code(raw_output: str) -> str:
    fenced_candidates = _extract_fenced_kotlin_candidates(raw_output)
    if fenced_candidates:
        best_candidate = max(fenced_candidates, key=_candidate_test_code_score)
        return _trim_after_balanced_top_level_class(best_candidate)

    lines = raw_output.strip().splitlines()
    filtered_lines = [
        line for line in lines
        if not line.strip().lower().startswith("here is")
        and not line.strip().lower().startswith("this test")
        and not re.match(r"^\s*(wait|let's|actually|however|hypothesis|resolution):?\b", line, re.IGNORECASE)
    ]
    return _trim_after_balanced_top_level_class("\n".join(filtered_lines).strip())


def looks_like_kotlin_test_output(text: str) -> bool:
    if not text or not text.strip():
        return False

    candidate = extract_kotlin_code(text)
    if not candidate.strip():
        candidate = text

    has_kotlin_shape = "class " in candidate or "object " in candidate or "fun " in candidate
    has_test_shape = "@Test" in candidate or "import org.junit.Test" in candidate or "RunWith" in candidate
    has_package_or_import = (
        re.search(r"(?m)^\s*package\s+", candidate) is not None
        or re.search(r"(?m)^\s*import\s+", candidate) is not None
    )
    return has_kotlin_shape and has_test_shape and has_package_or_import


def extract_nullable_boolean_functions(source_code: str):
    source_bytes = source_code.encode("utf8")
    root = parse_kotlin_ast(source_code)
    names = set()

    for node in walk_ast(root):
        if node.type != "function_declaration":
            continue

        name = declaration_name(source_bytes, node)
        if not name:
            continue

        for child in node.children:
            if child.type == "nullable_type" and ast_text(source_bytes, child) == "Boolean?":
                names.add(name)
                break

    return names


def normalize_json_patches(patch):
    if not isinstance(patch, dict):
        return None

    if "patches" in patch:
        raw_patches = patch.get("patches")
        if not isinstance(raw_patches, list):
            return None
    else:
        raw_patches = [patch]

    normalized = []
    for item in raw_patches:
        if not isinstance(item, dict):
            return None

        old_text = item.get("old_text")
        new_text = item.get("new_text")

        if not isinstance(old_text, str) or not isinstance(new_text, str):
            return None

        if not old_text.strip():
            return None

        normalized.append(
            {
                "old_text": old_text,
                "new_text": new_text,
            }
        )

    return normalized or None


def extract_json_patches(raw_output: str):
    """
    Extract one or more patch JSON objects from model output.
    Expected schemas:
      {"old_text": "...", "new_text": "..."}
      {"patches": [{"old_text": "...", "new_text": "..."}]}
    """
    if not raw_output:
        return None

    text = raw_output.strip()

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    else:
        first = text.find("{")
        last = text.rfind("}")
        if first != -1 and last != -1 and last > first:
            text = text[first:last + 1]

    try:
        patch = json.loads(text)
    except json.JSONDecodeError:
        return None

    return normalize_json_patches(patch)


