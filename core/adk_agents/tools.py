"""ADK FunctionTools with in-process policy (former Claude PreToolUse rules) + file-cache LRU."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from google.adk.tools import FunctionTool

from UnitTest_gen.core import io as file_cache
from UnitTest_gen.core.hooks import (
    clear_read_once_path,
    decide_bash_input,
    decide_edit_path,
    decide_read_once,
    decide_read_path,
    decide_write_path,
    extract_paths_from_tool_output,
    record_read_failure,
    register_discovery_hits,
)


def build_agent_tools() -> list[FunctionTool]:
    """Shared tool surface for planner / coder / fixer."""
    return [
        FunctionTool(Read),
        FunctionTool(Bash),
        FunctionTool(Edit),
        FunctionTool(Write),
        FunctionTool(Glob),
        FunctionTool(Grep),
    ]


def Read(file_path: str) -> str:
    """Read a UTF-8 text file. Prefer absolute paths from the prompt (SOURCE/TARGET/imports)."""
    decision = decide_read_path(file_path)
    if decision.permission != "allow":
        record_read_failure(file_path)
        return f"Error: {decision.reason or 'Read denied'}"
    path = decision.file_path or file_path
    once = decide_read_once(path)
    if once is not None and once.permission != "allow":
        return f"Error: {once.reason or 'Read denied (read-once)'}"
    try:
        text = file_cache.read_text(path)
    except Exception as exc:  # noqa: BLE001
        record_read_failure(path)
        return f"Error reading {path}: {exc}"
    return text


def Bash(command: str, description: str = "") -> str:
    """Run a local shell command (ls/cat/python3/find/grep/./gradlew). No network commands."""
    _ = description
    decision = decide_bash_input(command)
    if decision.permission != "allow":
        return f"Error: {decision.reason or 'Bash denied'}"
    cwd = os.environ.get("TESTGEN_AGENT_CWD") or os.getcwd()
    try:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "Error: Bash command timed out"
    except OSError as exc:
        return f"Error: {exc}"
    out = (completed.stdout or "") + (("\n" + completed.stderr) if completed.stderr else "")
    if completed.returncode != 0 and not out.strip():
        return f"Error: exit {completed.returncode}"
    # Register absolute file paths from find/grep/cat so later Reads clear basename poison.
    paths = extract_paths_from_tool_output("Bash", out)
    if paths:
        register_discovery_hits(paths, tool_name="Bash")
    if completed.returncode != 0:
        return f"(exit {completed.returncode})\n{out}"
    return out or "(no output)"


def Write(file_path: str, content: str) -> str:
    """Create or overwrite a file (test roots / plan / UnitTest_gen / build.gradle only)."""
    # Must use decide_write_path (not decide_edit_path): Edit denies missing *.plan.md
    # so the planner is forced to Write first; Write must be allowed to create it.
    decision = decide_write_path(file_path)
    if decision.permission != "allow":
        return f"Error: {decision.reason or 'Write denied'}"
    path = Path(decision.file_path or file_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        file_cache.invalidate(str(path))
        clear_read_once_path(str(path))
    except OSError as exc:
        return f"Error writing {path}: {exc}"
    return f"Wrote {path} ({len(content)} bytes)"


def Edit(file_path: str, old_string: str, new_string: str) -> str:
    """Replace the first exact occurrence of old_string with new_string in a file."""
    decision = decide_edit_path(file_path)
    if decision.permission != "allow":
        return f"Error: {decision.reason or 'Edit denied'}"
    path = Path(decision.file_path or file_path)
    try:
        text = file_cache.read_text(str(path))
    except Exception as exc:  # noqa: BLE001
        return f"Error reading {path}: {exc}"
    if old_string not in text:
        return "Error: No match found for Edit old_string"
    path.write_text(text.replace(old_string, new_string, 1), encoding="utf-8")
    file_cache.invalidate(str(path))
    clear_read_once_path(str(path))
    return f"Edited {path}"


def Glob(pattern: str, path: str = "") -> str:
    """List files matching a glob pattern under path (default: owning module or cwd)."""
    root = (path or os.environ.get("TESTGEN_OWNING_MODULE_DIR") or os.getcwd()).strip()
    base = Path(root)
    if not base.is_dir():
        return f"Error: not a directory: {root}"
    try:
        matches = sorted(str(p.resolve()) for p in base.glob(pattern) if p.is_file())[:200]
    except OSError as exc:
        return f"Error: {exc}"
    if matches:
        register_discovery_hits(matches, tool_name="Glob")
    return "\n".join(matches) if matches else "(no matches)"


def Grep(pattern: str, path: str = "", glob: str = "") -> str:
    """Search file contents with a Python regex (scoped; prefer owning module)."""
    root = (path or os.environ.get("TESTGEN_OWNING_MODULE_DIR") or os.getcwd()).strip()
    base = Path(root)
    if not base.exists():
        return f"Error: path not found: {root}"
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return f"Error: invalid regex: {exc}"
    files: list[Path]
    if base.is_file():
        files = [base]
    else:
        files = list(base.rglob(glob or "*")) if glob else list(base.rglob("*"))
        files = [p for p in files if p.is_file()][:400]
    lines: list[str] = []
    hit_paths: list[str] = []
    seen_hits: set[str] = set()
    for file_path in files:
        if file_path.suffix.lower() in {".class", ".jar", ".png", ".jpg", ".dex", ".so"}:
            continue
        try:
            text = file_cache.read_text(str(file_path))
        except Exception:  # noqa: BLE001
            continue
        for index, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                resolved = str(file_path.resolve())
                lines.append(f"{resolved}:{index}:{line}")
                if resolved not in seen_hits:
                    seen_hits.add(resolved)
                    hit_paths.append(resolved)
                if len(lines) >= 200:
                    if hit_paths:
                        register_discovery_hits(hit_paths, tool_name="Grep")
                    return "\n".join(lines)
    if hit_paths:
        register_discovery_hits(hit_paths, tool_name="Grep")
    return "\n".join(lines) if lines else "(no matches)"
