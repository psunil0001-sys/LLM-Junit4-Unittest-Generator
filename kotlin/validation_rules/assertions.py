# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Validates generated test assertions.
"""Trivial and tautology assertion validation rules."""

from __future__ import annotations

import re


def collect_validation_issues(test_code: str, output_file_path: str, source_code: str, test_report) -> list[str]:
    issues: list[str] = []

    suspicious_literals = []
    for match in re.finditer(
        r"assertEquals\(\s*\"([^\"]+)\"\s*,\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\)",
        test_code,
    ):
        literal = match.group(1)
        if len(literal) <= 1:
            continue
        arranged_in_test = re.search(
            rf"(?:=|\(|,|\bto)\s*\"{re.escape(literal)}\"",
            test_code[: match.start()],
        ) is not None
        appears_in_source = f'"{literal}"' in source_code or f"'{literal}'" in source_code
        if not arranged_in_test and not appears_in_source:
            suspicious_literals.append(f'assertEquals("{literal}", {match.group(2)})')

    if suspicious_literals:
        issues.append(
            "Suspicious expected string literal not backed by arranged input or source constants: "
            + "; ".join(suspicious_literals[:4])
            + ". Derive expected values from test setup or verified source behavior."
        )

    vacuous_assertions = []
    for match in re.finditer(
        r"assertEquals\(\s*([A-Za-z_][A-Za-z0-9_.]*)\s*,\s*\"([^\"]+)\"\s*\)",
        test_code,
    ):
        symbol_expr = match.group(1)
        literal = match.group(2)
        if "." in symbol_expr and len(literal) > 1:
            vacuous_assertions.append(f'assertEquals({symbol_expr}, "{literal}")')

    if vacuous_assertions:
        issues.append(
            "Vacuous assertion compares a source constant/property to a hard-coded literal without exercising behavior: "
            + "; ".join(vacuous_assertions[:4])
            + ". Assert a captured output, visible view state, emitted state, or collaborator interaction instead."
        )

    if re.search(r"\.background\.constantState\?\.resourceId\b", test_code):
        issues.append(
            "Robolectric Drawable ConstantState does not expose a stable resourceId. "
            "Assert stable UI structure/state instead, such as child count, item id/title, callback, or no thrown exception."
        )

    return issues
