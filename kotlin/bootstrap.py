"""androidTest readiness check — agents invent harness files."""

from __future__ import annotations

from dataclasses import dataclass

from UnitTest_gen.core.io import log_message
from UnitTest_gen.kotlin.project import (
    instrumented_manifest_missing,
    module_ready_for_instrumented_tests,
)

@dataclass(frozen=True)
class BootstrapResult:
    ready: bool
    patched: bool
    changes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

def ensure_instrumented_module_ready(
    *,
    module_dir: str,
    source_file_path: str,
    source_root: str,
    source_code: str = "",
    categories: frozenset[str] | set[str] | None = None,
    output_base_directory: str | None = None,
) -> BootstrapResult:
    """Log missing androidTest harness; do not write files or patch Gradle."""
    del module_dir, source_root, source_code, categories, output_base_directory
    warnings: list[str] = []
    ready, missing = module_ready_for_instrumented_tests(source_file_path)
    if not ready:
        detail = ", ".join(missing[:3]) if missing else "unknown"
        msg = (
            f"Module not instrumented-ready ({detail}); "
            "coder/plan must invent harness under src/androidTest and gradle test deps."
        )
        log_message(f"⚠️ {msg}", category="warning")
        warnings.append(msg)
    if instrumented_manifest_missing(source_file_path):
        msg = "androidTest AndroidManifest.xml missing — agent should Write it if needed."
        log_message(f"⚠️ {msg}", category="warning")
        warnings.append(msg)
    return BootstrapResult(
        ready=ready,
        patched=False,
        changes=(),
        warnings=tuple(warnings),
    )
