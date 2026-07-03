# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Validates generated Android resource references.
"""Verified Android resource reference validation."""

from __future__ import annotations

import re

from UnitTest_gen.kotlin.project_context import (
    _ANDROID_R_REF_PATTERN,
    find_owning_module_dir,
    find_project_root_for_path,
    find_single_same_type_resource_match,
    load_verified_android_resource_index,
    lookup_verified_android_resource,
    parse_module_namespace,
)


def collect_validation_issues(test_code: str, output_file_path: str, source_code: str, test_report) -> list[str]:
    issues: list[str] = []
    project_root = find_project_root_for_path(output_file_path)
    if not project_root or not test_code:
        return issues

    module_dir = find_owning_module_dir(project_root, output_file_path)
    index = load_verified_android_resource_index(module_dir)
    if not index:
        return issues

    qualified_import = re.search(r"(?m)^\s*import\s+([A-Za-z0-9_.]+)\.R\s*$", test_code)
    if qualified_import:
        module_namespace = parse_module_namespace(module_dir)
        if module_namespace and qualified_import.group(1) != module_namespace:
            return issues

    seen: set[str] = set()
    for match in _ANDROID_R_REF_PATTERN.finditer(test_code):
        if match.group("prefix"):
            continue
        resource_type = match.group("type")
        name = match.group("name")
        token = f"R.{resource_type}.{name}"
        if token in seen:
            continue
        seen.add(token)

        if lookup_verified_android_resource(index, resource_type, name):
            continue

        single_match = find_single_same_type_resource_match(index, resource_type, name)
        if single_match:
            issues.append(
                "unverified_android_resource: "
                f"{token} is not declared. Use verified `R.{resource_type}.{single_match.name}` "
                f"from {single_match.declaration_path}."
            )
            continue

        same_type = [
            candidate_name
            for (rtype, candidate_name) in index
            if rtype == resource_type and candidate_name != name
        ]
        if len(same_type) > 1:
            options = ", ".join(f"R.{resource_type}.{candidate}" for candidate in sorted(same_type)[:6])
            issues.append(
                "unverified_android_resource: "
                f"{token} is not declared and multiple same-type resources exist ({options}). "
                "Repair must choose the exact verified declaration; do not guess."
            )
        else:
            issues.append(
                "unverified_android_resource: "
                f"{token} is not declared in verified project resources. "
                "Remove or replace this test trigger instead of inventing a resource name."
            )

    return issues
