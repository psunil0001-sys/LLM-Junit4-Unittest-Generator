# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Resolves Gradle test and Kover task invocations.
"""Resolve Gradle task lists for verification and Kover gates.

All Gradle runs use the configured --gradle-task list (Kover suite); there is no
separate fast compile/test shortcut path.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from UnitTest_gen.kotlin.project_context import (
    default_gradle_tasks_for_target,
    find_project_root_for_path,
)


@dataclass(frozen=True)
class GradleInvocation:
    tasks: list[str]
    gradle_args: list[str]


def test_class_fqn(test_file_path: str) -> str:
    path = Path(test_file_path)
    package = ""
    if path.is_file():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        match = re.search(r"^\s*package\s+([A-Za-z_][A-Za-z0-9_.]*)\s*$", text, re.MULTILINE)
        if match:
            package = match.group(1)
    class_name = path.stem
    return f"{package}.{class_name}" if package else class_name


def gradle_variant_hint(gradle_tasks: list[str] | None) -> str:
    """Infer the release variant label from configured Gradle task names (reporting only)."""
    override = os.environ.get("TESTGEN_GRADLE_VARIANT", "").strip()
    if override:
        return override

    joined = " ".join(gradle_tasks or []).lower()
    if "prodglobalrelease" in joined or "compilethenkoverallprodreports" in joined:
        return "ProdGlobalRelease"
    if "prodchinarelease" in joined:
        return "ProdChinaRelease"
    if "prodkorearelease" in joined:
        return "ProdKoreaRelease"
    if "devdebug" in joined:
        return "DevDebug"
    return "ProdGlobalRelease"


def resolve_gradle_invocation(
    *,
    project_root: str,
    test_file_path: str,
    kover_tasks: list[str] | None,
) -> GradleInvocation:
    tasks = list(kover_tasks or [])
    if not tasks and test_file_path:
        tasks = default_gradle_tasks_for_target(project_root or find_project_root_for_path(test_file_path) or "", test_file_path)
    return GradleInvocation(tasks=tasks, gradle_args=[])

