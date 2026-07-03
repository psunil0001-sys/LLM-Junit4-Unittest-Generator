# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Validates generated test imports and packages.
"""Import and package alignment validation rules."""

from __future__ import annotations


def collect_validation_issues(test_code: str, output_file_path: str, source_code: str, test_report) -> list[str]:
    issues: list[str] = []
    top_lines = [line.strip() for line in test_code.splitlines()[:5]]
    if not any(line.startswith("package ") for line in top_lines):
        issues.append("Generated test is missing a package declaration near the top of the file.")
    return issues
