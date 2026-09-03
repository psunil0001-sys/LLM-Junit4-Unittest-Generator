"""Gradle subprocess runner and cross-process module locks."""

from __future__ import annotations

import atexit
import fcntl
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from UnitTest_gen.core.config import PACKAGE_DIR

_LOCK_DIR = Path(PACKAGE_DIR) / "data" / "locks"
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_OPEN_LOCKS: list[object] = []

def _lock_path_for_module(module_path: str) -> Path:
    key = module_path.strip() or "root"
    if not key.startswith(":"):
        key = f":{key}"
    safe = _SAFE.sub("_", key.strip(":").replace(":", "-")) or "root"
    return _LOCK_DIR / f"gradle-{safe}.lock"

def module_path_from_gradle_tasks(tasks: list[str] | None) -> str:
    """Best-effort ``:module:path`` from the first qualified Gradle task."""
    for task in tasks or []:
        text = str(task).strip()
        if not text.startswith(":"):
            continue
        parts = text.split(":")
        if len(parts) > 2:
            return ":".join(parts[:-1])
    return ":root"

def _release_all() -> None:
    for handle in _OPEN_LOCKS:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
        except Exception:
            pass
    _OPEN_LOCKS.clear()

atexit.register(_release_all)

@contextmanager
def gradle_module_lock(module_path: str) -> Iterator[None]:
    """Exclusive cross-process lock for one Gradle module (blocking)."""
    _LOCK_DIR.mkdir(parents=True, exist_ok=True)
    path = _lock_path_for_module(module_path)
    handle = open(path, "a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        _OPEN_LOCKS.append(handle)
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()} module={module_path}\n")
        handle.flush()
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            _OPEN_LOCKS.remove(handle)
        except ValueError:
            pass
        try:
            handle.close()
        except Exception:
            pass

import asyncio
import os
import re
import subprocess
import time
from dataclasses import dataclass

from UnitTest_gen.core.io import log_block, log_message
from UnitTest_gen.core.config import get_config

_ERROR_KEYWORDS = (
    "e: ",
    "w: ",
    "* What went wrong:",
    "* Try:",
    "> A failure occurred",
    "> Manifest merger failed",
    "> There were failing tests",
    "Caused by:",
    "Unresolved reference",
    "Type mismatch",
    "Cannot access",
    "Overload resolution ambiguity",
    "No value passed for parameter",
    "Too many arguments",
    "None of the following functions",
    "Null can not",
    "Conflicting declarations",
    "Redeclaration",
    "Cannot infer",
    "Not enough information to infer",
    "Argument type mismatch",
    "Return type mismatch",
    "Unresolved supertypes",
    "Cannot locate tasks",
    "Compilation error",
    "Execution failed for task",
    "FAILED",
    "FAILURE:",
    "BUILD FAILED",
    "Exception at",
    "AssertionError",
    "NotAMockException",
    " tests completed",
    "See the report at:",
)
_WIDE_CONTEXT_KEYWORDS = ("None of the following functions", "Overload resolution ambiguity", "Type mismatch")
_STACK_AT_LINE = re.compile(r"\bat\s+.+\.(kt|java):\d+\b")

@dataclass(frozen=True)
class GradleRunResult:
    """Full Gradle transcript for logs plus a compact tail for agent fix prompts."""

    full_output: str
    agent_tail: str

    def __str__(self) -> str:
        return self.full_output

def is_gradle_success(gradle_output: str | GradleRunResult) -> bool:
    output = gradle_output.full_output if isinstance(gradle_output, GradleRunResult) else (gradle_output or "")
    return "EXIT_CODE: 0" in output and "BUILD SUCCESSFUL" in output and "BUILD FAILED" not in output

def _is_error_line(line: str) -> bool:
    if any(keyword in line for keyword in _ERROR_KEYWORDS):
        return True
    return bool(_STACK_AT_LINE.search(line))

def summarize_gradle_output(output: str, *, max_error_lines: int = 40) -> str:
    """Compact TTY-oriented summary; full transcript stays in the pipeline log."""
    text = output or ""
    lines = text.splitlines()
    summary: list[str] = []

    for prefix in ("COMMAND:", "EXIT_CODE:"):
        for line in lines:
            if line.startswith(prefix):
                summary.append(line.strip())
                break

    status = "BUILD STATUS: unknown"
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("BUILD SUCCESSFUL"):
            status = stripped
            break
        if stripped.startswith("BUILD FAILED"):
            status = stripped
            break
        if "TIMEOUT" in stripped.upper() and "EXIT_CODE" not in stripped:
            status = stripped
    summary.append(status)

    executed = up_to_date = failed = 0
    for line in lines:
        if not line.startswith("> Task "):
            continue
        if " FAILED" in line:
            failed += 1
        elif " UP-TO-DATE" in line:
            up_to_date += 1
        elif " FROM-CACHE" in line:
            up_to_date += 1
        elif " NO-SOURCE" in line:
            continue
        else:
            executed += 1
    if executed or up_to_date or failed:
        summary.append(
            f"Tasks: executed={executed} up-to-date/cache={up_to_date} failed={failed}"
        )

    if "BUILD SUCCESSFUL" not in text or "BUILD FAILED" in text or "EXIT_CODE: 0" not in text:
        errors: list[str] = []
        for line in lines:
            if line.startswith("> Task ") and " UP-TO-DATE" in line:
                continue
            if line.startswith("> Task ") and " FROM-CACHE" in line:
                continue
            if not _is_error_line(line):
                continue
            # Skip noisy warning-only Kotlin lines for the TTY summary.
            if line.lstrip().startswith("w: "):
                continue
            errors.append(line.rstrip())
            if len(errors) >= max_error_lines:
                break
        if errors:
            summary.append("Errors:")
            summary.extend(f"  {item}" for item in errors)

    return "\n".join(summary)

def log_gradle_result(title: str, output: str | GradleRunResult) -> None:
    """Log full Gradle transcript to file; print errors + summary only on the TTY."""
    full = output.full_output if isinstance(output, GradleRunResult) else (output or "(empty)")
    if not full:
        full = "(empty)"
    summary = summarize_gradle_output(full)
    log_block(
        title,
        f"{full}\n---- SUMMARY ----\n{summary}",
        category="gradle",
        console=False,
    )
    log_block(f"{title} SUMMARY", summary, category="gradle", console=True)

def filter_gradle_errors(output: str, *, max_chars: int = 30_000) -> str:
    """Keep compiler/test failure lines plus a bounded tail of raw output."""
    lines = (output or "").splitlines()
    useful: list[str] = []
    context_after_match = 0
    for line in lines:
        if _is_error_line(line):
            useful.append(line)
            if any(keyword in line for keyword in _WIDE_CONTEXT_KEYWORDS):
                context_after_match = 8
            continue
        if context_after_match > 0:
            useful.append(line)
            context_after_match -= 1

    if not useful:
        return (output or "")[-12_000:]

    merged: list[str] = []
    seen: set[str] = set()
    for line in useful[-300:] + ["", "---- GRADLE OUTPUT TAIL ----"] + lines[-160:]:
        key = line.strip()
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        merged.append(line)
    return "\n".join(merged)[-max_chars:]

def _validate_tokens(values, label: str) -> str:
    for value in values:
        if not isinstance(value, str) or not value.strip():
            return f"Invalid Gradle {label}: {value!r}"
    return ""

def _agent_tail_for(raw: str, *, success: bool) -> str:
    if success:
        return "\n".join((raw or "").splitlines()[-80:])
    return filter_gradle_errors(raw)

def run_gradle(
    project_root: str,
    *,
    tasks: list[str],
    gradle_args: list[str] | None = None,
    offline: bool | None = None,
    rerun_tasks: bool = False,
) -> GradleRunResult:
    """Run Gradle tasks; return full transcript plus a compact agent tail."""
    config = get_config()
    project_root = os.path.abspath(os.path.expanduser(project_root))
    if not os.path.isfile(os.path.join(project_root, "gradlew")):
        text = f"COMMAND: (none)\nEXIT_CODE: 1\nBUILD FAILED\nNo ./gradlew found in {project_root}\n"
        return GradleRunResult(full_output=text, agent_tail=text)

    selected_tasks = [task for task in (tasks or []) if str(task).strip()]
    if not selected_tasks:
        text = "COMMAND: (none)\nEXIT_CODE: 1\nBUILD FAILED\nNo Gradle tasks were requested.\n"
        return GradleRunResult(full_output=text, agent_tail=text)

    extra_args = [arg.strip() for arg in (gradle_args or []) if str(arg).strip()]
    for values, label in ((selected_tasks, "task"), (extra_args, "arg")):
        error = _validate_tokens(values, label)
        if error:
            text = f"COMMAND: (none)\nEXIT_CODE: 1\nBUILD FAILED\n{error}\n"
            return GradleRunResult(full_output=text, agent_tail=text)

    command = ["./gradlew", *selected_tasks, *extra_args, "--no-daemon"]
    if config.gradle_offline if offline is None else offline:
        command.append("--offline")
    if rerun_tasks:
        command.append("--rerun-tasks")

    header = f"COMMAND: {' '.join(command)}\n"
    from UnitTest_gen.core.gradle import (
        gradle_module_lock,
        module_path_from_gradle_tasks,
    )

    module_path = module_path_from_gradle_tasks(selected_tasks)
    with gradle_module_lock(module_path):
        try:
            result = subprocess.run(
                command,
                cwd=project_root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=config.gradle_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raw = exc.stdout or b""
            output = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
            full = (
                f"{header}TIMEOUT: Gradle exceeded {config.gradle_timeout_seconds}s\n"
                f"EXIT_CODE: TIMEOUT\nBUILD FAILED\n{output}"
            )
            return GradleRunResult(full_output=full, agent_tail=filter_gradle_errors(full))

        output = result.stdout or ""
        header += f"EXIT_CODE: {result.returncode}\n"
        if result.returncode == 0:
            full = header + "BUILD SUCCESSFUL\n" + output
            return GradleRunResult(full_output=full, agent_tail=_agent_tail_for(output, success=True))
        full = header + output
        return GradleRunResult(full_output=full, agent_tail=filter_gradle_errors(output))

async def run_gradle_with_heartbeat(
    project_root: str,
    *,
    tasks: list[str],
    gradle_args: list[str] | None = None,
    offline: bool | None = None,
    rerun_tasks: bool = False,
) -> GradleRunResult:
    """Run Gradle off the event loop while printing progress for long builds."""
    task_text = " ".join(tasks) if tasks else "default Gradle tasks"
    started_at = time.monotonic()
    gradle_future = asyncio.create_task(
        asyncio.to_thread(
            run_gradle,
            project_root,
            tasks=tasks,
            gradle_args=gradle_args,
            offline=offline,
            rerun_tasks=rerun_tasks,
        )
    )
    interval = get_config().gradle_heartbeat_seconds
    while not gradle_future.done():
        done, _ = await asyncio.wait({gradle_future}, timeout=interval)
        if done:
            break
        log_message(
            f"💓 Gradle heartbeat: still running after {int(time.monotonic() - started_at)}s ({task_text})",
            category="gradle",
        )
    return await gradle_future
