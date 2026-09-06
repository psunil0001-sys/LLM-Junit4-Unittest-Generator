"""Tool policy decisions for ADK agents (in-process)."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from UnitTest_gen.core.config import AGENT_PHASE_ENV, AGENT_PHASE_PLAN

SOURCE_IMPORTS_ENV = "TESTGEN_SOURCE_IMPORTS_JSON"
READ_ONCE_ENV = "TESTGEN_READ_ONCE_JSON"
DISCOVERY_ENV = "TESTGEN_DISCOVERY_JSON"
OWNING_MODULE_ENV = "TESTGEN_OWNING_MODULE_DIR"
READ_FAIL_LOCK_THRESHOLD = 2
_ABS_FILE = re.compile(r"(/(?:[^\s]+)\.(?:kt|json|xml))\s*$")
_ABS_PATH_IN_OUTPUT = re.compile(
    r"(/(?:[^\s\"':]+)\.(?:kt|xml|json|gradle\.kts|gradle))(?:\:\d+)?"
)
# Prompt still names the SOURCE file; import paths are resolved for the Read-remap
# sidecar (no longer dumped as a SOURCE IMPORTS section in the user prompt).
_PROMPT_SOURCE_PATH = re.compile(
    r"(?:SOURCE FILE \(absolute\):\s*|for source\s+)(/(?:[^\s]+\.kt))",
    re.IGNORECASE,
)
_PLAN_MUTATION_DENY = (
    "planner may Write/Edit *.plan.md, test-root harness files (src/test, src/androidTest), "
    "or build.gradle(.kts) — never src/main or production sources."
)
_CODER_MUTATION_DENY = (
    "Write/Edit only under src/test, src/androidTest, build.gradle.kts, build.gradle, "
    "or UnitTest_gen — not src/main or production sources."
)
_DENIED_TOOLS = frozenset({"WebSearch", "WebFetch", "NotebookEdit"})
_DENIED_TOOL_REASON = (
    "WebSearch, WebFetch, and NotebookEdit are denied — this pipeline is local-only."
)
_GRADLE_BUILD_FILENAMES = frozenset({"build.gradle.kts", "build.gradle"})

def _load_json_file(path: Path) -> dict | None:
    try:
        from UnitTest_gen.core import io as file_cache

        payload = json.loads(file_cache.read_text(str(path)))
    except Exception:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    return payload if isinstance(payload, dict) else None

def _atomic_write_text(dest: Path, payload: str, *, prefix: str) -> None:
    handle, tmp_name = tempfile.mkstemp(prefix=prefix, suffix=".json", dir=str(dest.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp_name, dest)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise

def _deny_pretool(reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }

_MISSING_PLAN_DENY = (
    "PLAN OUTPUT FILE does not exist yet — expected on a first pass. Do not Read, find/grep, "
    "or Edit it. Call Write once with this exact file_path and the full frontmatter + body."
)
_TEST_ROOT_MARKERS = ("/src/test/", "/src/androidTest/")
_NETWORK_CMD = re.compile(
    r"\b(curl|wget|pip3?|npm|yarn|pnpm|ssh|scp|sftp|apt-get|dnf|yum|brew)\b"
    r"|\bgit\s+(clone|fetch|pull|push)\b"
    r"|\bdocker\s+pull\b"
)
_ABS_PATH_IN_COMMAND = re.compile(r"(/(?:[^\s\"';&|]+))")

@dataclass(frozen=True)
class ReadHookDecision:
    permission: str  # "allow" | "deny"
    file_path: str
    reason: str = ""
    updated: bool = False

def parse_source_import_paths(prompt_text: str) -> dict[str, str]:
    """Map unique basenames from absolute import paths (legacy prompt block or resolved).

    Prefer an explicit ``SOURCE IMPORTS`` listing when present (tests / older logs).
    Otherwise resolve imports from the SOURCE path named in the prompt so PreToolUse
    Read remaps keep working without dumping the list into the agent prompt.
    """
    grouped: dict[str, list[str]] = {}
    for line in (prompt_text or "").splitlines():
        path = _trailing_abs_file(line)
        if not path:
            continue
        # Only treat lines that look like the old SOURCE IMPORTS bullet list,
        # or any absolute .kt/.xml/.json trailing path on a bullet.
        stripped = line.strip()
        if not (stripped.startswith("- ") or stripped.startswith("SOURCE IMPORTS")):
            continue
        name = Path(path).name
        grouped.setdefault(name, [])
        resolved = str(Path(path))
        if resolved not in grouped[name]:
            grouped[name].append(resolved)
    mapping = {name: paths[0] for name, paths in grouped.items() if len(paths) == 1}
    if mapping:
        return mapping
    return _resolve_import_map_from_prompt_source(prompt_text)


def _resolve_import_map_from_prompt_source(prompt_text: str) -> dict[str, str]:
    match = _PROMPT_SOURCE_PATH.search(prompt_text or "")
    if not match:
        return {}
    source_path = match.group(1)
    try:
        from UnitTest_gen.kotlin.imports import resolve_source_imports

        resolved = resolve_source_imports(source_path)
    except Exception:
        return {}
    grouped: dict[str, list[str]] = {}
    for item in resolved:
        if getattr(item, "kind", None) != "file" or not getattr(item, "path", None):
            continue
        path = str(Path(item.path).resolve())
        name = Path(path).name
        grouped.setdefault(name, [])
        if path not in grouped[name]:
            grouped[name].append(path)
    return {name: paths[0] for name, paths in grouped.items() if len(paths) == 1}


def _trailing_abs_file(line: str) -> str:
    match = _ABS_FILE.search(line.strip())
    return match.group(1) if match else ""

def write_source_import_sidecar(prompt_text: str, dest: Path | None = None) -> Path:
    mapping = parse_source_import_paths(prompt_text)
    if dest is None:
        handle, name = tempfile.mkstemp(prefix="testgen_imports_", suffix=".json")
        os.close(handle)
        dest = Path(name)
    dest.write_text(json.dumps({"by_basename": mapping}) + "\n", encoding="utf-8")
    return dest

def load_source_import_map(path: str = "") -> dict[str, str]:
    sidecar = path or os.environ.get(SOURCE_IMPORTS_ENV, "")
    if not sidecar:
        return {}
    payload = _load_json_file(Path(sidecar))
    raw = payload.get("by_basename") if payload else None
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for name, mapped in raw.items():
        if isinstance(name, str) and isinstance(mapped, str) and mapped:
            out[name] = mapped
    return out

def write_read_once_sidecar(dest: Path | None = None) -> Path:
    """Empty path→mtime map for one source-file session (plan + coder + fix)."""
    if dest is None:
        handle, name = tempfile.mkstemp(prefix="testgen_read_once_", suffix=".json")
        os.close(handle)
        dest = Path(name)
    dest.write_text(json.dumps({"by_path": {}}) + "\n", encoding="utf-8")
    return dest

def load_read_once_map(path: str = "") -> dict[str, int]:
    sidecar = path or os.environ.get(READ_ONCE_ENV, "")
    if not sidecar:
        return {}
    payload = _load_json_file(Path(sidecar))
    raw = payload.get("by_path") if payload else None
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int] = {}
    for key, value in raw.items():
        if isinstance(key, str) and key and isinstance(value, int):
            out[key] = value
    return out

def save_read_once_map(by_path: dict[str, int], path: str = "") -> None:
    sidecar = path or os.environ.get(READ_ONCE_ENV, "")
    if not sidecar:
        return
    dest = Path(sidecar)
    _atomic_write_text(
        dest, json.dumps({"by_path": by_path}) + "\n", prefix="testgen_read_once_"
    )

def _empty_discovery_state() -> dict:
    return {
        "glob_hits": {},
        "denied_basenames": {},
        "read_fail_counts": {},
        "glob_grep_lock": False,
        "last_failed_read": "",
        "last_tool": "",
    }

def write_discovery_sidecar(dest: Path | None = None) -> Path:
    """Empty Bash find/grep discovery map for one agent session."""
    payload = _empty_discovery_state()
    if dest is None:
        handle, name = tempfile.mkstemp(prefix="testgen_discovery_", suffix=".json")
        os.close(handle)
        dest = Path(name)
    dest.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return dest

def load_discovery_state(path: str = "") -> dict:
    sidecar = path or os.environ.get(DISCOVERY_ENV, "")
    if not sidecar:
        return _empty_discovery_state()
    payload = _load_json_file(Path(sidecar))
    if not payload:
        return _empty_discovery_state()
    glob_hits = payload.get("glob_hits") if isinstance(payload.get("glob_hits"), dict) else {}
    denied = payload.get("denied_basenames") if isinstance(payload.get("denied_basenames"), dict) else {}
    read_fail = payload.get("read_fail_counts") if isinstance(payload.get("read_fail_counts"), dict) else {}
    last_tool = str(payload.get("last_tool") or "")
    return {
        "glob_hits": {str(k): int(v) for k, v in glob_hits.items() if isinstance(k, str)},
        "denied_basenames": {str(k): bool(v) for k, v in denied.items() if isinstance(k, str)},
        "read_fail_counts": {str(k): int(v) for k, v in read_fail.items() if isinstance(k, str)},
        "glob_grep_lock": bool(payload.get("glob_grep_lock")),
        "last_failed_read": str(payload.get("last_failed_read") or ""),
        "last_tool": last_tool,
    }

def save_discovery_state(state: dict, path: str = "") -> None:
    sidecar = path or os.environ.get(DISCOVERY_ENV, "")
    if not sidecar:
        return
    dest = Path(sidecar)
    payload = json.dumps(
        {
            "glob_hits": state.get("glob_hits") or {},
            "denied_basenames": state.get("denied_basenames") or {},
            "read_fail_counts": state.get("read_fail_counts") or {},
            "glob_grep_lock": bool(state.get("glob_grep_lock")),
            "last_failed_read": str(state.get("last_failed_read") or ""),
            "last_tool": str(state.get("last_tool") or ""),
        }
    ) + "\n"
    _atomic_write_text(dest, payload, prefix="testgen_discovery_")

def _resolve_discovery_path(file_path: str) -> str:
    requested = (file_path or "").strip()
    if not requested:
        return ""
    try:
        return str(Path(requested).resolve())
    except OSError:
        return requested

def path_in_discovery_hits(file_path: str, state: dict | None = None) -> bool:
    requested = (file_path or "").strip()
    if not requested:
        return False
    resolved = _resolve_discovery_path(requested)
    payload = state if state is not None else load_discovery_state()
    hits = payload.get("glob_hits") or {}
    return resolved in hits or requested in hits

def is_basename_denied(name: str, state: dict | None = None) -> bool:
    payload = state if state is not None else load_discovery_state()
    denied = payload.get("denied_basenames") or {}
    return bool(denied.get(name))

def record_denied_basename(name: str) -> None:
    if not name:
        return
    state = load_discovery_state()
    denied = dict(state.get("denied_basenames") or {})
    denied[name] = True
    state["denied_basenames"] = denied
    save_discovery_state(state)

def owning_module_dir() -> str:
    return (os.environ.get(OWNING_MODULE_ENV) or "").strip()

def is_glob_grep_locked(state: dict | None = None) -> bool:
    payload = state if state is not None else load_discovery_state()
    return bool(payload.get("glob_grep_lock"))

def clear_glob_grep_lock() -> None:
    state = load_discovery_state()
    if not state.get("glob_grep_lock") and not state.get("last_failed_read"):
        return
    state["glob_grep_lock"] = False
    state["last_failed_read"] = ""
    save_discovery_state(state)

def record_read_failure(path: str) -> int:
    requested = (path or "").strip()
    if not requested:
        return 0
    resolved = _resolve_discovery_path(requested) or requested
    state = load_discovery_state()
    counts = dict(state.get("read_fail_counts") or {})
    count = int(counts.get(resolved, 0)) + 1
    counts[resolved] = count
    state["read_fail_counts"] = counts
    state["last_failed_read"] = resolved
    state["last_tool"] = "Read"
    if count >= READ_FAIL_LOCK_THRESHOLD:
        state["glob_grep_lock"] = True
    save_discovery_state(state)
    return count

def _path_under_owning_module(path: str) -> bool:
    module = owning_module_dir()
    if not module:
        return True
    try:
        resolved = str(Path(path).resolve())
        module_res = str(Path(module).resolve())
    except OSError:
        return False
    return resolved == module_res or resolved.startswith(module_res + "/")

def _find_hint_for_missing(name: str, requested: str = "") -> str:
    module = owning_module_dir()
    scope = module or "OWNING MODULE DIR"
    if name.endswith(".xml"):
        return (
            f"Bash: find {scope} -name '*nav_graph.xml' (embedded bfs), "
            f"or find {scope} -name '{name}'. Do not retry this path."
        )
    _ = requested
    return f"Bash: find {scope} -name '{name}' (embedded bfs). Do not retry this path."

def _glob_hint_for_missing(name: str, requested: str = "") -> str:
    """Backward-compatible alias."""
    return _find_hint_for_missing(name, requested)

def _discovery_lock_read_deny_reason() -> str:
    module = owning_module_dir()
    scope = module or "OWNING MODULE DIR"
    return (
        f"2 Read failures on this path. find/grep-only lock active. "
        f"Next: Bash find {scope} -name '*nav_graph.xml' (embedded bfs), then Read the hit path."
    )

def _glob_lock_read_deny_reason() -> str:
    return _discovery_lock_read_deny_reason()

def register_discovery_hits(paths: list[str], *, tool_name: str = "") -> None:
    if not paths:
        return
    state = load_discovery_state()
    hits = dict(state.get("glob_hits") or {})
    denied = dict(state.get("denied_basenames") or {})
    for raw in paths:
        text = (raw or "").strip()
        if not text:
            continue
        resolved = _resolve_discovery_path(text)
        key = resolved or text
        hits[key] = int(hits.get(key, 0)) + 1
    state["glob_hits"] = hits
    state["denied_basenames"] = denied
    state["glob_grep_lock"] = False
    state["last_failed_read"] = ""
    if tool_name:
        state["last_tool"] = tool_name
    save_discovery_state(state)

def extract_paths_from_tool_output(tool_name: str, tool_response: object) -> list[str]:
    text = ""
    if isinstance(tool_response, str):
        text = tool_response
    elif isinstance(tool_response, dict):
        content = tool_response.get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts = [
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and isinstance(block.get("text"), str)
            ]
            text = "\n".join(parts)
        else:
            text = str(tool_response.get("result") or tool_response.get("output") or "")
    elif tool_response is not None:
        text = str(tool_response)
    found: list[str] = []
    seen: set[str] = set()
    for match in _ABS_PATH_IN_OUTPUT.finditer(text or ""):
        path = match.group(1)
        if path in seen:
            continue
        seen.add(path)
        found.append(path)
    _ = tool_name
    return found

def _is_partial_read(tool_input: dict) -> bool:
    return tool_input.get("offset") is not None or tool_input.get("limit") is not None

def decide_read_once(
    file_path: str,
    tool_input: dict | None = None,
) -> ReadHookDecision | None:
    """Record full-file Reads; never deny re-Read of an unchanged existing file."""
    if not os.environ.get(READ_ONCE_ENV):
        return None
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    if _is_partial_read(tool_input):
        return None
    requested = (file_path or "").strip()
    if not requested:
        return None
    path = Path(requested)
    try:
        mtime_ns = path.stat().st_mtime_ns
        resolved = str(path.resolve())
    except OSError:
        return None
    by_path = load_read_once_map()
    by_path[resolved] = mtime_ns
    save_read_once_map(by_path)
    return None

def clear_read_once_path(file_path: str) -> None:
    """Drop a path so the next Read after Edit/Write is allowed."""
    if not os.environ.get(READ_ONCE_ENV):
        return
    requested = (file_path or "").strip()
    if not requested:
        return
    try:
        resolved = str(Path(requested).resolve())
    except OSError:
        resolved = requested
    by_path = load_read_once_map()
    if resolved not in by_path and requested not in by_path:
        return
    by_path.pop(resolved, None)
    by_path.pop(requested, None)
    save_read_once_map(by_path)

def decide_read_paging(file_path: str, tool_input: dict | None = None) -> ReadHookDecision | None:
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    if not _is_partial_read(tool_input):
        return None
    return ReadHookDecision(
        permission="deny",
        file_path=(file_path or "").strip(),
        reason=(
            "Never Read with offset/limit to page through files for discovery. "
            "Bash find/grep first, then Read the full file once."
        ),
    )

def decide_read_path(file_path: str, by_basename: dict[str, str] | None = None) -> ReadHookDecision:
    requested = (file_path or "").strip()
    mapping = by_basename if by_basename is not None else load_source_import_map()
    if not requested:
        return ReadHookDecision(
            permission="deny",
            file_path="",
            reason=(
                "Read file_path is empty. Use an absolute path from Glob/Grep/Bash "
                "under the owning module (or SOURCE / TARGET paths in this prompt)."
            ),
        )
    name = Path(requested).name
    listed = (mapping or {}).get(name, "")
    listed_file = Path(listed) if listed else None
    listed_ok = bool(listed_file and listed_file.is_file())
    requested_ok = Path(requested).is_file()
    if is_glob_grep_locked():
        if requested_ok:
            return ReadHookDecision(permission="allow", file_path=requested)
        return ReadHookDecision(
            permission="deny",
            file_path=requested,
            reason=f"{_discovery_lock_read_deny_reason()} {_find_hint_for_missing(name, requested)}",
        )
    if listed_ok:
        listed_abs = str(listed_file.resolve())
        if requested_ok and str(Path(requested).resolve()) == listed_abs:
            return ReadHookDecision(permission="allow", file_path=requested)
        return ReadHookDecision(
            permission="allow",
            file_path=listed_abs,
            updated=True,
            reason=f"Rewrote Read to resolved import path for {name}",
        )
    if is_basename_denied(name):
        # Basename poison only blocks re-guessing *missing* paths. A real file at the
        # requested absolute path is always readable (e.g. after Glob found testsupport/).
        if requested_ok:
            return ReadHookDecision(permission="allow", file_path=requested)
        return ReadHookDecision(
            permission="deny",
            file_path=requested,
            reason=(
                f"Read denied for {name}: {_find_hint_for_missing(name, requested)} "
                "Do not retry without a find/grep hit path."
            ),
        )
    if path_in_discovery_hits(requested) and requested_ok:
        return ReadHookDecision(permission="allow", file_path=requested)
    if requested_ok:
        return ReadHookDecision(permission="allow", file_path=requested)
    if name.endswith(".plan.md"):
        return ReadHookDecision(
            permission="deny",
            file_path=requested,
            reason=_MISSING_PLAN_DENY,
        )
    record_denied_basename(name)
    find_hint = _find_hint_for_missing(name, requested)
    return ReadHookDecision(
        permission="deny",
        file_path=requested,
        reason=f"Path does not exist: {requested}. {find_hint}",
    )

def _agent_phase_is_plan() -> bool:
    return (os.environ.get(AGENT_PHASE_ENV) or "").strip() == AGENT_PHASE_PLAN

def _unittest_gen_root() -> Path:
    return Path(__file__).resolve().parents[1]

def _resolve_mutation_path(file_path: str) -> Path:
    path = Path(file_path)
    if path.is_absolute():
        return path.resolve()
    return (Path.cwd() / path).resolve()

def _is_production_source_path(resolved: Path) -> bool:
    return "/src/main/" in resolved.as_posix()

def _is_allowed_mutation_path(resolved: Path) -> bool:
    if _is_production_source_path(resolved):
        return False
    text = resolved.as_posix()
    if any(marker in text for marker in _TEST_ROOT_MARKERS):
        return True
    if resolved.name in _GRADLE_BUILD_FILENAMES:
        return True
    try:
        resolved.relative_to(_unittest_gen_root())
        return True
    except ValueError:
        return False

def decide_plan_mutation_path(file_path: str) -> ReadHookDecision:
    """Planner may Write/Edit ``*.plan.md`` or the same harness/gradle roots as coder."""
    requested = (file_path or "").strip()
    if not requested:
        return ReadHookDecision(
            permission="deny",
            file_path="",
            reason=_PLAN_MUTATION_DENY,
        )
    path = Path(requested)
    if path.name.endswith(".plan.md"):
        try:
            from UnitTest_gen.kotlin.plan import plans_dir

            plans = plans_dir().resolve()
            resolved = path.resolve() if path.is_absolute() else (plans / path).resolve()
            try:
                resolved.relative_to(plans)
            except ValueError:
                return ReadHookDecision(
                    permission="deny",
                    file_path=requested,
                    reason=_PLAN_MUTATION_DENY,
                )
            return ReadHookDecision(permission="allow", file_path=str(resolved))
        except Exception:
            return ReadHookDecision(permission="allow", file_path=requested)
    return decide_coder_mutation_path(requested)

def decide_coder_mutation_path(file_path: str) -> ReadHookDecision:
    """Coder/fix may Write/Edit only under test roots or UnitTest_gen."""
    requested = (file_path or "").strip()
    if not requested:
        return ReadHookDecision(
            permission="deny",
            file_path="",
            reason=_CODER_MUTATION_DENY,
        )
    try:
        resolved = _resolve_mutation_path(requested)
    except OSError:
        return ReadHookDecision(
            permission="deny",
            file_path=requested,
            reason=_CODER_MUTATION_DENY,
        )
    if not _is_allowed_mutation_path(resolved):
        return ReadHookDecision(
            permission="deny",
            file_path=requested,
            reason=_CODER_MUTATION_DENY,
        )
    resolved_text = str(resolved)
    updated = resolved_text != requested
    return ReadHookDecision(
        permission="allow",
        file_path=resolved_text,
        updated=updated,
        reason="Normalized Write/Edit path" if updated else "",
    )

def decide_write_path(file_path: str) -> ReadHookDecision:
    """Plan: create ``*.plan.md`` (or harness). Coder/fix: test roots + gradle.

    Unlike ``decide_edit_path``, a missing ``*.plan.md`` is allowed — Write creates it.
    """
    if _agent_phase_is_plan():
        return decide_plan_mutation_path(file_path)
    return decide_coder_mutation_path(file_path)


def decide_edit_path(file_path: str, by_basename: dict[str, str] | None = None) -> ReadHookDecision:
    """Plan: ``*.plan.md`` (Write first) plus harness roots. Coder/fix: test roots + gradle."""
    _ = by_basename
    if _agent_phase_is_plan():
        decision = decide_plan_mutation_path(file_path)
        plan_path = (decision.file_path or "").endswith(".plan.md")
        if decision.permission == "allow" and plan_path and not Path(decision.file_path).is_file():
            return ReadHookDecision(
                permission="deny",
                file_path=decision.file_path,
                reason=_MISSING_PLAN_DENY,
            )
        return decision
    return decide_coder_mutation_path(file_path)

def pretool_read_response(event: dict, by_basename: dict[str, str] | None = None) -> dict | None:
    """Return hook JSON for Read, or None to leave the call unchanged."""
    if (event or {}).get("tool_name") != "Read":
        return None
    tool_input = event.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}
    requested = str(tool_input.get("file_path") or tool_input.get("path") or "")
    paging = decide_read_paging(requested, tool_input)
    if paging is not None and paging.permission == "deny":
        return _deny_pretool(paging.reason)
    decision = decide_read_path(requested, by_basename)
    if decision.permission == "deny":
        if requested:
            try:
                missing = not Path(requested).is_file()
            except OSError:
                missing = True
            if missing:
                record_read_failure(requested)
        return _deny_pretool(decision.reason)
    once = decide_read_once(decision.file_path, tool_input)
    if once is not None and once.permission == "deny":
        return _deny_pretool(once.reason)
    if decision.permission == "allow" and not decision.updated:
        return None
    updated = dict(tool_input)
    updated["file_path"] = decision.file_path
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": updated,
        }
    }

def _pretool_mutation_response(
    event: dict,
    *,
    tool_name: str,
    by_basename: dict[str, str] | None = None,
) -> dict | None:
    """Shared PreToolUse JSON for Edit/Write path decisions."""
    if (event or {}).get("tool_name") != tool_name:
        return None
    tool_input = event.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}
    requested = str(tool_input.get("file_path") or tool_input.get("path") or "")
    if tool_name == "Write":
        decision = decide_write_path(requested)
    else:
        decision = decide_edit_path(requested, by_basename)

    if decision.permission == "deny":
        return _deny_pretool(decision.reason)

    clear_read_once_path(decision.file_path or requested)

    if decision.permission == "allow" and not decision.updated:
        if decision.file_path and decision.file_path != requested:
            updated = dict(tool_input)
            updated["file_path"] = decision.file_path
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "updatedInput": updated,
                }
            }
        return None

    updated = dict(tool_input)
    updated["file_path"] = decision.file_path
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": updated,
        }
    }

def pretool_edit_response(event: dict, by_basename: dict[str, str] | None = None) -> dict | None:
    """Return hook JSON for Edit, or None to leave the call unchanged."""
    return _pretool_mutation_response(event, tool_name="Edit", by_basename=by_basename)

def pretool_write_response(event: dict, by_basename: dict[str, str] | None = None) -> dict | None:
    """Plan: ``*.plan.md`` plus harness roots. Coder/fix: test roots + gradle."""
    _ = by_basename
    return _pretool_mutation_response(event, tool_name="Write")

def pretool_web_response(event: dict, by_basename: dict[str, str] | None = None) -> dict | None:
    """Always deny WebSearch/WebFetch/NotebookEdit — pipeline is local-only."""
    _ = by_basename
    name = str((event or {}).get("tool_name") or "")
    if name not in _DENIED_TOOLS:
        return None
    return _deny_pretool(_DENIED_TOOL_REASON)

def classify_bash_command(command: str) -> str:
    """Return empty | network | other. Only empty and network commands are denied."""
    cmd = (command or "").strip()
    if not cmd:
        return "empty"
    if _NETWORK_CMD.search(cmd):
        return "network"
    return "other"


def extract_bash_search_roots(command: str) -> list[str]:
    """Absolute paths referenced in a Bash find/grep command."""
    cmd = (command or "").strip()
    if not cmd:
        return []
    roots: list[str] = []
    seen: set[str] = set()
    for match in _ABS_PATH_IN_COMMAND.finditer(cmd):
        candidate = match.group(1).rstrip("/")
        if candidate in seen:
            continue
        seen.add(candidate)
        roots.append(candidate)
    return roots

def decide_bash_input(command: str) -> ReadHookDecision:
    """Allow any local Bash command; deny empty commands and network commands.

    Agents are local-only: curl/wget/pip/npm/ssh/scp/… and git clone|fetch|pull|push
    are denied. Everything else (ls, cat, python3, find/grep, ./gradlew incl. clean)
    is allowed.
    """
    cmd = (command or "").strip()
    kind = classify_bash_command(cmd)
    if kind == "empty":
        return ReadHookDecision(
            permission="deny",
            file_path="",
            reason="Bash command is empty.",
        )
    if kind == "network":
        return ReadHookDecision(
            permission="deny",
            file_path="",
            reason=(
                "Bash denied: no network commands (curl/wget/pip/npm/ssh/scp/apt-get/"
                "git clone|fetch|pull|push) — agents are local-only. "
                "All other local commands (ls/cat/python3/find/grep/./gradlew) are allowed."
            ),
        )
    # other → allow; keep find/grep scoped to owning module when the discovery lock is active
    if re.search(r"\b(find|bfs|grep|ugrep)\b", cmd) and is_glob_grep_locked():
        module = owning_module_dir()
        roots = extract_bash_search_roots(cmd)
        if module and roots and not all(_path_under_owning_module(root) for root in roots):
            return ReadHookDecision(
                permission="deny",
                file_path=cmd,
                reason=(
                    f"find/grep-only lock active after Read failures. "
                    f"Scope Bash to owning module: {module}. "
                    f"Try: find {module} -name '*nav_graph.xml'"
                ),
            )
    return ReadHookDecision(permission="allow", file_path=cmd)

def pretool_bash_response(event: dict, by_basename: dict[str, str] | None = None) -> dict | None:
    _ = by_basename
    if (event or {}).get("tool_name") != "Bash":
        return None
    tool_input = event.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}
    command = str(tool_input.get("command") or tool_input.get("cmd") or "")
    decision = decide_bash_input(command)
    if decision.permission == "allow":
        return None
    return _deny_pretool(decision.reason)

def _read_response_text(tool_response: object) -> str:
    if isinstance(tool_response, str):
        return tool_response
    if isinstance(tool_response, dict):
        content = tool_response.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and isinstance(block.get("text"), str)
            ]
            return "\n".join(parts)
        return str(tool_response.get("result") or tool_response.get("output") or tool_response.get("error") or "")
    if tool_response is not None:
        return str(tool_response)
    return ""

def _read_tool_failed(event: dict) -> bool:
    if (event or {}).get("is_error") is True:
        return True
    tool_response = (event or {}).get("tool_response")
    if tool_response is None:
        tool_response = (event or {}).get("tool_result")
    if isinstance(tool_response, dict) and tool_response.get("is_error") is True:
        return True
    text = _read_response_text(tool_response).lower()
    return any(
        needle in text
        for needle in (
            "does not exist",
            "file does not exist",
            "no such file",
            "enoent",
            "not found",
        )
    )

def posttool_read_response(event: dict) -> dict | None:
    """On Read failure, record sidecar state and return find/grep discovery hint to the agent."""
    if str((event or {}).get("tool_name") or "") != "Read":
        return None
    if not _read_tool_failed(event):
        return None
    tool_input = (event or {}).get("tool_input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}
    requested = str(tool_input.get("file_path") or tool_input.get("path") or "")
    name = Path(requested).name if requested else ""
    if name:
        record_denied_basename(name)
    count = record_read_failure(requested) if requested else 0
    reason = _find_hint_for_missing(name, requested) if name else (
        "Read failed. Bash find under OWNING MODULE DIR -name '<filename>', then Read the hit path."
    )
    if count >= READ_FAIL_LOCK_THRESHOLD:
        reason = f"{_discovery_lock_read_deny_reason()} {reason}"
    else:
        reason = f"Read failed for missing path. {reason}"
    return {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "permissionDecisionReason": reason,
            "additionalContext": reason,
        }
    }

def posttool_bash_response(event: dict) -> dict | None:
    """Record Bash find/grep file hits in the discovery sidecar."""
    if str((event or {}).get("tool_name") or "") != "Bash":
        return None
    tool_response = (event or {}).get("tool_response")
    if tool_response is None:
        tool_response = (event or {}).get("tool_result")
    paths = extract_paths_from_tool_output("Bash", tool_response)
    if paths:
        register_discovery_hits(paths, tool_name="Bash")
    return None

def posttool_response(event: dict) -> dict | None:
    """PostToolUse: Read failure hints take precedence; Bash find/grep registers hits."""
    read_response = posttool_read_response(event)
    posttool_bash_response(event)
    return read_response
