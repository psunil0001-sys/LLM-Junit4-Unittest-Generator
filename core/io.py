"""File cache, JSON stores, logging, ranges, project walk, ADB probe."""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import zipfile
from collections import OrderedDict
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from UnitTest_gen.core.config import get_config

def coerce_int_set(values: Iterable[Any] | None) -> set[int]:
    """Coerce an iterable of values into a set of ints, skipping bad entries."""
    out: set[int] = set()
    for n in values or []:
        try:
            out.add(int(n))
        except (TypeError, ValueError):
            continue
    return out

def compact_ranges(numbers) -> str:
    """Format integers as a compact comma-separated range list (e.g. ``1-3, 5``)."""
    numbers = sorted(set(numbers))
    if not numbers:
        return "none"

    ranges = []
    start = previous = numbers[0]

    for value in numbers[1:]:
        if value == previous + 1:
            previous = value
            continue
        ranges.append((start, previous))
        start = previous = value

    ranges.append((start, previous))

    return ", ".join(
        str(start) if start == end else f"{start}-{end}"
        for start, end in ranges
    )

_FENCE_ANY = re.compile(r"```([A-Za-z0-9_+-]*)\s*\n?(.*?)\s*```", re.DOTALL)
_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)

_LANG_ALIASES: dict[str, frozenset[str]] = {
    "json": frozenset({"", "json"}),
    "kotlin": frozenset({"", "kotlin", "kt"}),
    "markdown": frozenset({"", "markdown", "md"}),
    "md": frozenset({"", "markdown", "md"}),
}

def iter_fenced_blocks(text: str, lang: str | None = None) -> Iterator[str]:
    """Yield fenced block bodies, optionally filtered by language tag."""
    if not text:
        return
    allowed: frozenset[str] | None = None
    if lang is not None:
        key = lang.strip().lower()
        allowed = _LANG_ALIASES.get(key, frozenset({key, ""}))
    for match in _FENCE_ANY.finditer(text):
        tag = (match.group(1) or "").strip().lower()
        body = (match.group(2) or "").strip()
        if not body:
            continue
        if allowed is not None and tag not in allowed:
            continue
        yield body

def extract_fenced_block(text: str, lang: str | None = None) -> str | None:
    """Return the first fenced block body, optionally filtered by language tag."""
    return next(iter_fenced_blocks(text, lang=lang), None)

def extract_fenced_json(text: str) -> dict | None:
    """Parse the first JSON object found inside a fenced block, if any."""
    if not text:
        return None
    for match in _JSON_FENCE.finditer(text):
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    block = extract_fenced_block(text, lang="json")
    if not block:
        return None
    try:
        payload = json.loads(block)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None

def iter_json_candidates(text: str) -> Iterator[str]:
    """Yield candidate JSON object strings from fences and brace-sliced text."""
    if not text:
        return
    seen: set[str] = set()
    ordered: list[str] = []

    def _push(candidate: str) -> None:
        blob = candidate.strip()
        if not blob or blob in seen:
            return
        seen.add(blob)
        ordered.append(blob)

    for match in _JSON_FENCE.finditer(text):
        _push(match.group(1))
    stripped = text.strip()
    if stripped.startswith("{"):
        _push(stripped)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        _push(text[start : end + 1])
    yield from ordered

DEFAULT_SKIP_DIRS = frozenset(
    {
        "build",
        ".gradle",
        ".git",
        ".cache",
        ".idea",
        "generated",
        "ksp",
        "node_modules",
        "UnitTest_gen",
    }
)

def _under_roots(dirpath: str, roots: tuple[str, ...]) -> bool:
    if not roots:
        return True
    normalized = dirpath.replace("\\", "/")
    for marker in roots:
        marker = marker.replace("\\", "/").strip("/")
        if not marker:
            continue
        if f"/{marker}/" in f"{normalized}/" or normalized.endswith(f"/{marker}"):
            return True
    return False

def walk_source_roots(
    project_root,
    *,
    roots: Iterable[str] = ("src/main",),
    skip_dirs: Iterable[str] = DEFAULT_SKIP_DIRS,
    extensions: Iterable[str] = (".kt",),
) -> Iterator[str]:
    """Yield absolute source file paths under *project_root* matching *extensions*.

    Directories named in *skip_dirs* (and any name starting with ``.``) are pruned.
    When *roots* is non-empty, only directories whose path contains one of the root
    markers (e.g. ``src/main``) are scanned for files.
    """
    root = os.path.abspath(str(project_root))
    if not os.path.isdir(root):
        return
    skip = frozenset(skip_dirs)
    root_markers = tuple(str(r) for r in roots)
    exts = tuple(extensions)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            name
            for name in dirnames
            if name not in skip and not name.startswith(".")
        ]
        if not _under_roots(dirpath, root_markers):
            continue
        for file_name in filenames:
            if not file_name.endswith(exts):
                continue
            yield os.path.join(dirpath, file_name)

_DEVICE_LINE = re.compile(r"^\s*([0-9a-fA-F:.]+|\S+)\s+device\s*$", re.MULTILINE)

def adb_device_available(*, timeout: float = 5.0) -> bool:
    """Return True when ``adb devices`` lists at least one device in ``device`` state."""
    try:
        result = subprocess.run(
            ["adb", "devices"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    if result.returncode != 0:
        return False
    return bool(_DEVICE_LINE.search(result.stdout or ""))

_DEFAULT_MAX_BYTES = 64_000_000

_INIT_LOCK = threading.Lock()
_INSTANCE: "FileCache | None" = None

class FileCacheError(OSError):
    """Raised when a cached read fails and no default was provided."""

    def __init__(self, path: str, cause: BaseException | None = None) -> None:
        msg = f"failed to read {path}"
        if cause is not None:
            msg = f"{msg}: {cause}"
        super().__init__(msg)
        self.path = path
        self.__cause__ = cause

def _config_defaults() -> tuple[bool, int]:
    """Read enable + max_bytes from pipeline_config."""
    cfg = get_config()
    return bool(cfg.file_cache_enabled), max(0, int(cfg.file_cache_max_bytes))

def _norm_key(path: str | Path) -> str:
    return str(Path(path).resolve())

def _fingerprint(path: str) -> tuple[int, int, int] | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size, st.st_ino)

class FileCache:
    """Process-local LRU text cache keyed by absolute path."""

    def __init__(self, *, max_bytes: int | None = None, enabled: bool | None = None) -> None:
        cfg_enabled, cfg_max = _config_defaults()
        self._lock = threading.RLock()
        self._entries: OrderedDict[str, tuple[tuple[int, int, int], str]] = OrderedDict()
        self._max_bytes = cfg_max if max_bytes is None else max(0, max_bytes)
        self._enabled = cfg_enabled if enabled is None else bool(enabled)
        self._bytes = 0
        self._hits = 0
        self._misses = 0
        self._reloads = 0
        self._evictions = 0

    def read_text(
        self,
        path: str | Path,
        *,
        default: str | None = None,
        encoding: str = "utf-8",
        errors: str = "replace",
    ) -> str:
        """Return file text from RAM when fingerprint matches; else read disk once."""
        key = _norm_key(path)
        if not self._enabled:
            return self._read_disk(key, default=default, encoding=encoding, errors=errors)

        with self._lock:
            fp = _fingerprint(key)
            if fp is None:
                self._misses += 1
                if key in self._entries:
                    self._drop(key)
                if default is not None:
                    return default
                raise FileCacheError(key, FileNotFoundError(key))

            cached = self._entries.get(key)
            if cached is not None and cached[0] == fp:
                self._entries.move_to_end(key)
                self._hits += 1
                return cached[1]

            text = self._read_disk(key, default=None, encoding=encoding, errors=errors)
            if cached is not None:
                self._reloads += 1
                self._drop(key)
            else:
                self._misses += 1
            self._put(key, fp, text)
            return text

    def read_json(
        self,
        path: str | Path,
        *,
        default_factory: Callable[[], Any] = dict,
    ) -> Any:
        """Parse JSON from cached text. Callers may mutate the returned object."""
        try:
            text = self.read_text(path)
        except FileCacheError:
            return default_factory()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return default_factory()

    def invalidate(self, path: str | Path) -> None:
        key = _norm_key(path)
        with self._lock:
            if key in self._entries:
                self._drop(key)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._bytes = 0

    def stats(self) -> dict[str, int | bool]:
        with self._lock:
            return {
                "enabled": self._enabled,
                "entries": len(self._entries),
                "bytes": self._bytes,
                "max_bytes": self._max_bytes,
                "hits": self._hits,
                "misses": self._misses,
                "reloads": self._reloads,
                "evictions": self._evictions,
            }

    def _put(self, key: str, fp: tuple[int, int, int], text: str) -> None:
        size = len(text.encode("utf-8", errors="replace"))
        if self._max_bytes > 0 and size > self._max_bytes:
            # Too large for the cache — serve without storing.
            return
        self._entries[key] = (fp, text)
        self._entries.move_to_end(key)
        self._bytes += size
        while self._max_bytes > 0 and self._bytes > self._max_bytes and self._entries:
            old_key, (old_fp, old_text) = self._entries.popitem(last=False)
            self._bytes -= len(old_text.encode("utf-8", errors="replace"))
            self._evictions += 1
            del old_key, old_fp

    def _drop(self, key: str) -> None:
        entry = self._entries.pop(key, None)
        if entry is None:
            return
        self._bytes -= len(entry[1].encode("utf-8", errors="replace"))

    @staticmethod
    def _read_disk(
        key: str,
        *,
        default: str | None,
        encoding: str,
        errors: str,
    ) -> str:
        try:
            return Path(key).read_text(encoding=encoding, errors=errors)
        except OSError as exc:
            if default is not None:
                return default
            raise FileCacheError(key, exc) from exc

def get_file_cache() -> FileCache:
    """Return the process-wide singleton cache (sized from PipelineConfig)."""
    global _INSTANCE
    if _INSTANCE is not None:
        return _INSTANCE
    with _INIT_LOCK:
        if _INSTANCE is None:
            enabled, max_bytes = _config_defaults()
            _INSTANCE = FileCache(max_bytes=max_bytes, enabled=enabled)
        return _INSTANCE

def reset_file_cache_for_tests() -> None:
    """Drop the singleton so tests can construct a fresh cache."""
    global _INSTANCE
    with _INIT_LOCK:
        _INSTANCE = None

def read_text(
    path: str | Path,
    *,
    default: str | None = None,
    encoding: str = "utf-8",
    errors: str = "replace",
) -> str:
    return get_file_cache().read_text(path, default=default, encoding=encoding, errors=errors)

def read_json(path: str | Path, *, default_factory: Callable[[], Any] = dict) -> Any:
    return get_file_cache().read_json(path, default_factory=default_factory)

def invalidate(path: str | Path) -> None:
    get_file_cache().invalidate(path)

def clear() -> None:
    get_file_cache().clear()

def stats() -> dict[str, int | bool]:
    return get_file_cache().stats()

def save_json(
    path,
    data,
    *,
    indent: int = 2,
    sort_keys: bool = True,
    touch_updated_at: bool = False,
) -> None:
    """Write *data* as JSON, creating parent directories as needed."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = data
    if touch_updated_at and isinstance(data, dict):
        payload = dict(data)
        payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
    target.write_text(
        json.dumps(payload, indent=indent, sort_keys=sort_keys),
        encoding="utf-8",
    )
    invalidate(target)

PACKAGE_DIR = str(Path(__file__).resolve().parents[1])
AI_MEMORY_FILE = os.environ.get("TESTGEN_AI_MEMORY_FILE", os.path.join(PACKAGE_DIR, "ai_test_memory.json"))

def _load_memory() -> dict:
    payload = read_json(AI_MEMORY_FILE, default_factory=dict)
    return payload if isinstance(payload, dict) else {}

def _save_memory(payload: dict) -> None:
    save_json(AI_MEMORY_FILE, payload)

def retrieve_generation_lessons(source_tags, *, class_name: str = "", limit: int = 5) -> str:
    del class_name
    tags = {str(tag).lower() for tag in (source_tags or [])}
    memory = _load_memory()
    lessons = memory.get("generation_lessons") or []
    selected = []
    for lesson in lessons:
        lesson_tags = {str(tag).lower() for tag in (lesson.get("tags") or [])}
        if tags and lesson_tags and not (tags & lesson_tags):
            continue
        text = str(lesson.get("summary") or lesson.get("notes") or "").strip()
        if text:
            selected.append(text)
        if len(selected) >= limit:
            break
    return "\n".join(f"- {item}" for item in selected)

def retrieve_repair_lessons(categories, *, limit: int = 5) -> str:
    cats = {str(c).lower() for c in (categories or [])}
    memory = _load_memory()
    lessons = memory.get("repair_lessons") or []
    selected = []
    for lesson in lessons:
        lesson_tags = {str(tag).lower() for tag in (lesson.get("tags") or [])}
        if cats and lesson_tags and not (cats & lesson_tags):
            continue
        text = str(lesson.get("fix") or lesson.get("summary") or "").strip()
        if text:
            selected.append(text)
        if len(selected) >= limit:
            break
    return "\n".join(f"- {item}" for item in selected)

def add_generation_lesson(
    *,
    summary: str,
    tags: list[str] | None = None,
    notes: str = "",
    class_name: str = "",
) -> str:
    del class_name
    if not summary.strip():
        return "skipped_invalid"
    memory = _load_memory()
    lessons = list(memory.get("generation_lessons") or [])
    entry = {"summary": summary.strip(), "notes": notes.strip(), "tags": list(tags or [])}
    if entry in lessons:
        return "skipped_duplicate"
    lessons.append(entry)
    memory["generation_lessons"] = lessons[-200:]
    _save_memory(memory)
    return "added"

def add_repair_lesson(
    *,
    summary: str,
    tags: list[str] | None = None,
    fix: str = "",
    root_cause: str = "",
) -> str:
    if not (summary.strip() or fix.strip()):
        return "skipped_invalid"
    memory = _load_memory()
    lessons = list(memory.get("repair_lessons") or [])
    entry = {
        "summary": summary.strip(),
        "fix": fix.strip(),
        "root_cause": root_cause.strip(),
        "tags": list(tags or []),
    }
    if entry in lessons:
        return "skipped_duplicate"
    lessons.append(entry)
    memory["repair_lessons"] = lessons[-200:]
    _save_memory(memory)
    return "added"

_RESET = "\033[0m"
_KINDS = {
    "plan": "\033[38;5;208m",    # Bright Orange  — plan agent
    "gen": "\033[96m",  # cyan — generation / context
    "fix": "\033[93m",  # yellow — fix passes
    "gradle": "\033[94m",  # blue — gradle / kover
    "ok": "\033[92m",  # green
    "warn": "\033[33m",  # amber
    "err": "\033[91m",  # red
    "dim": "\033[2m",
    "info": "\033[96m",
    "claude": "\033[95m",  # magenta — Claude live model tokens
}

_CATEGORY_KIND = {
    "info": "info",
    "success": "ok",
    "warning": "warn",
    "error": "err",
    "reasoning": "dim",
    "code": "gen",
    "context": "gen",
    "gradle": "gradle",
    "fix": "fix",
    "plan": "plan",
    "gen": "gen",
    "ok": "ok",
    "warn": "warn",
    "err": "err",
    "dim": "dim",
    "claude": "claude",
}

def color_enabled() -> bool:
    return bool(get_config().enable_console_color)

def paint(text: str, kind: str) -> str:
    if not color_enabled():
        return text
    code = _KINDS.get(kind) or _KINDS.get(_CATEGORY_KIND.get(kind, ""), "")
    if not code:
        return text
    return f"{code}{text}{_RESET}"

def prompt_kind_from_title(title: str) -> str:
    upper = (title or "").upper()
    if "PLAN" in upper:
        return "plan"
    if "FIX" in upper:
        return "fix"
    return "gen"

class PipelineLogger:
    def __init__(self, log_path: str, use_color: bool = True):
        self.log_path = os.path.abspath(log_path)
        self.use_color = bool(use_color)
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        with open(self.log_path, "w", encoding="utf-8") as f:
            f.write(f"# Test generation log\nStarted: {datetime.now().isoformat()}\n\n")

    def log(self, message: str = "", category: str = "info", end: str = "\n", console: bool = True):
        text = "" if message is None else str(message)
        if console:
            kind = _CATEGORY_KIND.get(category, category)
            out = paint(text, kind) if self.use_color else text
            print(out, end=end, flush=True)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(text + end)

    def section(self, title: str, category: str = "info", console: bool = True):
        border = "=" * 96
        self.log(f"\n{border}\n{title}\n{border}", category=category, console=console)

    def block(self, title: str, content: str, category: str = "info", console: bool = False):
        self.section(title, category=category, console=console)
        self.log(content or "", category="dim" if category != "err" else category, console=console)

ACTIVE_LOGGER = None

def set_active_logger(logger):
    global ACTIVE_LOGGER
    ACTIVE_LOGGER = logger

def _console_print(text: str, category: str, *, end: str = "\n") -> None:
    print(paint(text, _CATEGORY_KIND.get(category, category)), end=end, flush=True)

def log_message(message: str = "", category: str = "info", end: str = "\n", console: bool = True):
    if ACTIVE_LOGGER:
        ACTIVE_LOGGER.log(message, category=category, end=end, console=console)
        return
    text = "" if message is None else str(message)
    print(paint(text, _CATEGORY_KIND.get(category, category)) if console else text, end=end, flush=True)

def log_section(title: str, category: str = "info", console: bool = True):
    if ACTIVE_LOGGER:
        ACTIVE_LOGGER.section(title, category=category, console=console)
    elif console:
        _console_print(f"\n{title}", category)

def log_block(title: str, content: str, category: str = "info", console: bool = False):
    if ACTIVE_LOGGER:
        ACTIVE_LOGGER.block(title, content, category=category, console=console)
    elif console:
        _console_print(content or "", "dim")

def get_pipeline_log_dir() -> str:
    package_dir = str(Path(__file__).resolve().parents[1])
    return os.path.join(package_dir, "log")

def archive_generated_side_files(output_file_path: str, source_file_path: str):
    test_dir = os.path.dirname(output_file_path)
    test_name = os.path.basename(output_file_path)
    pipeline_log_dir = get_pipeline_log_dir()
    os.makedirs(pipeline_log_dir, exist_ok=True)

    candidates = []
    if os.path.isdir(test_dir):
        for file_name in os.listdir(test_dir):
            if not file_name.startswith(test_name + "."):
                continue
            path = os.path.join(test_dir, file_name)
            if os.path.isfile(path) and not path.endswith(".kt"):
                candidates.append(path)

    if not candidates:
        return None

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    source_stem = os.path.basename(source_file_path).replace(".kt", "")
    zip_path = os.path.join(pipeline_log_dir, f"{source_stem}.generated-artifacts-{timestamp}.zip")

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(candidates):
            archive.write(path, arcname=os.path.basename(path))

    for path in candidates:
        try:
            os.remove(path)
        except OSError as exc:
            log_message(f"⚠️ Could not remove archived side file {path}: {exc}", category="warning")

    log_message(f"📦 Archived {len(candidates)} generated side file(s) to {zip_path}", category="success")
    return zip_path
