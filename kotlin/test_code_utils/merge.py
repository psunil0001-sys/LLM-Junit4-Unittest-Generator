# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Kotlin test-code extraction, validation, and merge helpers (merge).
"""Kotlin test-code extraction, validation, and merge helpers — merge submodule."""

from __future__ import annotations

import re
import textwrap

from UnitTest_gen.core.logging_utils import log_message

def extract_import_lines(kotlin_code: str) -> list[str]:
    return re.findall(r"(?m)^import\s+[^\n]+$", kotlin_code)

def import_is_used_by_members(import_line: str, member_text: str) -> bool:
    imported = import_line.replace("import", "", 1).strip()
    if imported.endswith(".*"):
        return True

    alias_match = re.search(r"\s+as\s+([A-Za-z_][A-Za-z0-9_]*)$", imported)
    simple_name = alias_match.group(1) if alias_match else imported.rsplit(".", 1)[-1]
    simple_name = simple_name.strip("`")
    return bool(re.search(r"\b" + re.escape(simple_name) + r"\b", member_text))

def extract_class_body_members(kotlin_code: str) -> str:
    span = find_class_body_span(kotlin_code)
    if not span:
        return ""

    start, end = span
    return kotlin_code[start:end].strip("\n")

def _outer_class_body_span(kotlin_code: str) -> tuple[int, int] | None:
    class_match = re.search(
        r"(?m)^\s*(?:public\s+)?(?:class|object)\s+[A-Za-z_][A-Za-z0-9_]*[^{]*\{",
        kotlin_code,
    )
    if not class_match:
        return None

    start = class_match.end()
    depth = 1
    index = start
    quote = None
    escaped = False
    line_comment = False
    block_comment_depth = 0

    while index < len(kotlin_code):
        char = kotlin_code[index]
        next_char = kotlin_code[index + 1] if index + 1 < len(kotlin_code) else ""

        if line_comment:
            if char == "\n":
                line_comment = False
            index += 1
            continue

        if block_comment_depth:
            if char == "/" and next_char == "*":
                block_comment_depth += 1
                index += 2
                continue
            if char == "*" and next_char == "/":
                block_comment_depth -= 1
                index += 2
                continue
            index += 1
            continue

        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif quote == '"""' and kotlin_code.startswith('"""', index):
                quote = None
                index += 3
                continue
            elif char == quote:
                quote = None
            index += 1
            continue

        if char == "/" and next_char == "/":
            line_comment = True
            index += 2
            continue

        if char == "/" and next_char == "*":
            block_comment_depth = 1
            index += 2
            continue

        if kotlin_code.startswith('"""', index):
            quote = '"""'
            index += 3
            continue

        if char in ("\"", "'"):
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return start, index

        index += 1

    return None


def find_class_body_span(kotlin_code: str) -> tuple[int, int] | None:
    return _outer_class_body_span(kotlin_code)


def force_generated_test_class_name(test_code: str, expected_class_name: str) -> str:
    class_match = re.search(
        r"(?m)^(\s*(?:public\s+)?(?:class|object)\s+)([A-Za-z_][A-Za-z0-9_]*)\b",
        test_code,
    )
    if not class_match or class_match.group(2) == expected_class_name:
        return test_code

    return (
        test_code[:class_match.start(2)]
        + expected_class_name
        + test_code[class_match.end(2):]
    )


CLASS_MEMBER_DECLARATION_RE = re.compile(
    r"^\s*(?:(?:@[A-Za-z_][A-Za-z0-9_.]*(?::[A-Za-z_][A-Za-z0-9_]*)?(?:\([^)]*\))?)\s+)*"
    r"(?:(?:public|private|internal|protected|final|open|override|lateinit)\s+)*"
    r"(?:fun|val|var)\s+(`[^`]+`|[A-Za-z_][A-Za-z0-9_]*)"
)

def strip_kotlin_line_strings(line: str) -> str:
    result = []
    quote = None
    escaped = False

    for char in line:
        if escaped:
            escaped = False
            result.append(" ")
            continue

        if char == "\\" and quote:
            escaped = True
            result.append(" ")
            continue

        if quote:
            if char == quote:
                quote = None
            result.append(" ")
            continue

        if char in ("\"", "'"):
            quote = char
            result.append(" ")
        else:
            result.append(char)

    return "".join(result)

def kotlin_brace_delta(line: str) -> int:
    stripped = strip_kotlin_line_strings(line)
    return stripped.count("{") - stripped.count("}")

def class_member_name(lines: list[str]) -> str | None:
    for line in lines:
        match = CLASS_MEMBER_DECLARATION_RE.match(line)
        if match:
            return match.group(1).strip("`")
    return None

def is_function_member(lines: list[str]) -> bool:
    in_block_comment = False
    for line in lines:
        stripped = line.strip()
        if in_block_comment:
            in_block_comment = "*/" not in stripped
            continue
        if not stripped or stripped.startswith("//"):
            continue
        if stripped.startswith("/*"):
            in_block_comment = "*/" not in stripped
            continue
        if stripped.startswith("@") and not re.search(r"\bfun\b", stripped):
            continue
        return bool(
            re.match(
                r"(?:(?:@[A-Za-z_][A-Za-z0-9_.]*(?::[A-Za-z_][A-Za-z0-9_]*)?(?:\([^)]*\))?)\s+)*"
                r"(?:(?:public|private|internal|protected|final|open|override|suspend|inline)\s+)*fun\b",
                stripped,
            )
        )
    return False

def is_test_function_member(lines: list[str]) -> bool:
    return any(re.search(r"@\s*(?:org\.junit\.)?Test\b", line) for line in lines) and is_function_member(lines)

def split_top_level_class_members(class_body: str) -> list[list[str]]:
    lines = class_body.splitlines(keepends=True)
    members = []
    pending_prefix = []
    current = []
    depth = 0

    for line in lines:
        starts_member = depth == 0 and CLASS_MEMBER_DECLARATION_RE.match(line)
        starts_prefix = depth == 0 and line.strip().startswith("@") and not starts_member

        if starts_prefix and not current:
            pending_prefix.append(line)
            continue

        if starts_member and current:
            members.append(current)
            current = []

        if starts_member and pending_prefix:
            current.extend(pending_prefix)
            pending_prefix = []

        if current or starts_member:
            current.append(line)
        else:
            pending_prefix.append(line)

        depth += kotlin_brace_delta(line)

        if current and depth <= 0 and line.strip() and not line.rstrip().endswith(","):
            members.append(current)
            current = []
            depth = 0

    if current:
        members.append(current)
    elif pending_prefix:
        members.append(pending_prefix)

    return members

def existing_top_level_member_names(test_code: str) -> set[str]:
    body = extract_class_body_members(test_code)
    names = set()
    for member in split_top_level_class_members(body):
        name = class_member_name(member)
        if name:
            names.add(name)
    return names

def build_supplemental_merge_report(
    existing_test_code: str,
    supplemental_test_code: str,
    supplemental_members: str,
) -> dict:
    existing_names = existing_top_level_member_names(existing_test_code)

    kept_members = []
    added_tests = []
    added_helpers = []
    skipped_duplicate_tests = []
    skipped_duplicate_helpers = []
    skipped_variables = []
    skipped_unknown_members = []

    for member in split_top_level_class_members(supplemental_members):
        name = class_member_name(member)
        if not is_function_member(member):
            if name:
                skipped_variables.append(name)
            else:
                stripped = "".join(member).strip()
                if stripped:
                    skipped_unknown_members.append(stripped.splitlines()[0].strip())
            continue

        if name and name in existing_names:
            if is_test_function_member(member):
                skipped_duplicate_tests.append(name)
            else:
                skipped_duplicate_helpers.append(name)
            continue

        kept_members.append("".join(member))
        if name:
            if is_test_function_member(member):
                added_tests.append(name)
            else:
                added_helpers.append(name)

    kept_text = "\n\n".join(member.strip("\n") for member in kept_members).strip("\n")
    existing_imports = set(extract_import_lines(existing_test_code))
    supplemental_imports = extract_import_lines(supplemental_test_code)
    added_imports = [
        import_line
        for import_line in supplemental_imports
        if import_line not in existing_imports and import_is_used_by_members(import_line, kept_text)
    ]
    added_import_set = set(added_imports)
    skipped_existing_imports = [import_line for import_line in supplemental_imports if import_line in existing_imports]
    skipped_unused_imports = [
        import_line
        for import_line in supplemental_imports
        if import_line not in existing_imports and import_line not in added_import_set
    ]

    return {
        "member_blocks": [member.strip("\n") for member in kept_members],
        "added_tests": sorted(set(added_tests)),
        "added_helpers": sorted(set(added_helpers)),
        "added_imports": added_imports,
        "skipped_duplicate_tests": sorted(set(skipped_duplicate_tests)),
        "skipped_duplicate_helpers": sorted(set(skipped_duplicate_helpers)),
        "skipped_variables": sorted(set(skipped_variables)),
        "skipped_unknown_members": sorted(set(skipped_unknown_members)),
        "skipped_existing_imports": skipped_existing_imports,
        "skipped_unused_imports": skipped_unused_imports,
    }

def log_supplemental_merge_report(report: dict) -> None:
    def emit(label: str, values: list[str], category: str = "info") -> None:
        if values:
            log_message(f"{label}: " + ", ".join(values), category=category)

    log_message("📎 Supplemental merge summary:", category="info")
    emit("   Added test(s)", report.get("added_tests", []))
    emit("   Added helper function(s)", report.get("added_helpers", []))
    emit("   Added import(s)", report.get("added_imports", []))
    emit("   Skipped duplicate test(s)", report.get("skipped_duplicate_tests", []))
    emit("   Skipped duplicate helper function(s)", report.get("skipped_duplicate_helpers", []))
    emit("   Skipped variable/property member(s)", report.get("skipped_variables", []))
    emit("   Skipped unsupported class member(s)", report.get("skipped_unknown_members", []))
    emit("   Skipped import(s) already present", report.get("skipped_existing_imports", []))
    emit("   Skipped import(s) not used by merged members", report.get("skipped_unused_imports", []))

def mergeable_supplemental_member_blocks(existing_test_code: str, supplemental_test_code: str) -> list[str]:
    supplemental_members = extract_class_body_members(supplemental_test_code)
    if not supplemental_members:
        raise ValueError("Could not extract supplemental test class members for merge.")

    report = build_supplemental_merge_report(existing_test_code, supplemental_test_code, supplemental_members)
    log_supplemental_merge_report(report)
    kept_members = report["member_blocks"]

    if not kept_members:
        raise ValueError("No new supplemental class members remained after duplicate filtering.")

    return kept_members

def merge_test_code_with_member_blocks(
    existing_test_code: str,
    supplemental_test_code: str,
    member_blocks: list[str],
    comment: str = "Supplemental coverage tests merged from temporary generation.",
) -> str:
    if not member_blocks:
        raise ValueError("No supplemental class members provided for merge.")

    supplemental_members = "\n\n".join(member_blocks).strip("\n")
    existing_imports = set(extract_import_lines(existing_test_code))
    new_imports = [
        import_line
        for import_line in extract_import_lines(supplemental_test_code)
        if import_line not in existing_imports and import_is_used_by_members(import_line, supplemental_members)
    ]

    merged = existing_test_code.rstrip()

    if new_imports:
        import_matches = list(re.finditer(r"(?m)^import\s+[^\n]+$", merged))
        if import_matches:
            insert_at = import_matches[-1].end()
            merged = merged[:insert_at] + "\n" + "\n".join(new_imports) + merged[insert_at:]
        else:
            package_match = re.search(r"(?m)^package\s+[^\n]+$", merged)
            insert_at = package_match.end() if package_match else 0
            prefix = "\n\n" if package_match else ""
            merged = merged[:insert_at] + prefix + "\n".join(new_imports) + "\n" + merged[insert_at:]

    closing_index = find_outer_class_closing_brace_index(merged)
    if closing_index == -1:
        raise ValueError("Could not find existing test class closing brace for merge.")

    insertion = f"\n\n    // {comment}\n"
    insertion += indent_class_members(supplemental_members)
    insertion += "\n"

    return merged[:closing_index].rstrip() + insertion + merged[closing_index:]

def merge_supplemental_test_code(existing_test_code: str, supplemental_test_code: str) -> str:
    member_blocks = mergeable_supplemental_member_blocks(existing_test_code, supplemental_test_code)
    return merge_test_code_with_member_blocks(existing_test_code, supplemental_test_code, member_blocks)


def find_outer_class_closing_brace_index(kotlin_code: str) -> int:
    span = _outer_class_body_span(kotlin_code)
    return span[1] if span else -1

def indent_class_members(member_text: str) -> str:
    lines = textwrap.dedent(member_text.strip("\n")).splitlines()
    return "\n".join(
        "    " + line if line.strip() else ""
        for line in lines
    )

