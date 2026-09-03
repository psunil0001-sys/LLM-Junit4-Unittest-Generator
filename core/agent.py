"""Claude Code CLI subprocess, JSONL tee, session logging."""

from __future__ import annotations

from UnitTest_gen.core.io import log_message, log_section
from UnitTest_gen.core.config import (
    agent_env_display_name,
    get_config,
)

def csv_from_repeated_flag(flags: list[str], name: str) -> str:
    values: list[str] = []
    for index, flag in enumerate(flags):
        if flag == name and index + 1 < len(flags):
            values.append(flags[index + 1])
    return ",".join(values)

def csv_from_equals_flag(flags: list[str], prefix: str) -> str:
    values: list[str] = []
    for flag in flags:
        if flag.startswith(prefix):
            values.append(flag.split("=", 1)[1])
    return ",".join(values)

def format_agent_session_line(
    *,
    agent_label: str,
    endpoint: str,
    model: str,
    stream: bool,
    available: str = "",
    denied: str = "",
    temperature: float | None = None,
    presence_penalty: float | None = None,
    enable_thinking: bool | None = None,
    reasoning_budget: int | None = None,
    offline: bool = False,
) -> str:
    """One-line startup banner: endpoint, model, stream, and tool surface."""
    bits = [f"model {model or '?'}"]
    if offline:
        bits.append("offline")
    bits.append(f"stream={'on' if stream else 'off'}")
    if temperature is not None:
        bits.append(f"temp={temperature}")
    if presence_penalty is not None:
        bits.append(f"presence={presence_penalty}")
    if enable_thinking is not None:
        bits.append(f"thinking={'on' if enable_thinking else 'off'}")
    if enable_thinking and reasoning_budget is not None:
        bits.append(f"budget={reasoning_budget}")
    if available:
        bits.append(f"available={available}")
    if denied:
        bits.append(f"denied={denied}")
    return f"🤖 {agent_label} → {endpoint} ({', '.join(bits)})"

def describe_coder_endpoint() -> tuple[str, str]:
    """Return ``(endpoint, model)`` the coder CLI will use, without starting the proxy."""
    config = get_config()
    model = config.agent_model or ""
    if not model:
        try:
            from UnitTest_gen.core.llm import resolve_agent_model

            model = resolve_agent_model(config).alias
        except RuntimeError:
            model = ""
    if config.use_sampling_proxy:
        from UnitTest_gen.core.llm import DEFAULT_PROXY_HOST, proxy_base_url

        proxy = proxy_base_url(DEFAULT_PROXY_HOST, config.proxy_port)
        endpoint = proxy.removesuffix("/v1")
        return endpoint, model
    from UnitTest_gen.core.llm import provider_base_url

    upstream = provider_base_url()
    endpoint = upstream.removesuffix("/v1")
    return endpoint, model

def coder_tool_surface() -> tuple[str, str]:
    """Return ``(available, denied)`` CSV for the coder CLI tool policy."""
    from UnitTest_gen.core.config import CODER_ALLOWED_TOOLS, CODER_DENIED_TOOLS
    from UnitTest_gen.core.agent import _claude_tool_flags

    flags = _claude_tool_flags(
        allowed_tools=CODER_ALLOWED_TOOLS,
        denied_tools=CODER_DENIED_TOOLS,
        available_tools=None,
    )
    return (
        csv_from_repeated_flag(flags, "--allowedTools"),
        csv_from_repeated_flag(flags, "--disallowedTools"),
    )

def log_coder_slice_preamble(
    *,
    slice_tag: str,
    lines_text: str,
    methods_text: str,
    plan_lines: list[str] | tuple[str, ...] = (),
    gradle_command: str = "",
    batch_n: int = 0,
    batch_total: int = 0,
    endpoint: str = "",
    model: str = "",
    available: str = "",
    denied: str = "",
) -> None:
    """Print slice lines, plan, tools, and endpoint before the coder prompt."""
    agent = agent_env_display_name()
    batch_note = f" batch={batch_n}/{batch_total}" if batch_total else ""
    log_section(
        f"CODER SLICE PREAMBLE tag={slice_tag}{batch_note}",
        category="gen",
    )
    log_message(f"  lines attempted: [{lines_text}]", category="info")
    log_message(f"  methods: [{methods_text or '?'}]", category="info")
    for line in plan_lines:
        if line.strip():
            log_message(f"  {line}", category="info")
    log_message(f"  agent: {agent}", category="info")
    if endpoint:
        log_message(f"  endpoint: {endpoint}", category="info")
    if model:
        log_message(f"  model: {model}", category="info")
    if available:
        log_message(f"  available tools: {available}", category="info")
    if denied:
        log_message(f"  denied tools: {denied}", category="info")
    if gradle_command:
        log_message(f"  Python FAST_VERIFY: {gradle_command}", category="info")
        log_message(
            "  Agents must not run or script Gradle; Python verifies after the agent finishes.",
            category="info",
        )

import codecs
import json
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

from UnitTest_gen.core.io import log_message, paint

def _parse_jsonl_object(line: str) -> dict[str, Any] | None:
    stripped = line.strip()
    if not stripped:
        return None
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None

def _iter_jsonl_objects(text: str) -> Iterator[dict[str, Any]]:
    for line in text.splitlines():
        obj = _parse_jsonl_object(line)
        if obj is not None:
            yield obj

class _ClaudeToolTracker:
    """Maps Claude ``tool_use_id`` → tool name/path for done-line summaries."""

    def __init__(self) -> None:
        self._names: dict[str, str] = {}
        self._paths: dict[str, str] = {}

    def register(self, tool_id: str, name: str, arguments: Any = None) -> None:
        if tool_id and name:
            self._names[tool_id] = name
        path = _first_str(_tool_args_dict(arguments), "path", "filePath", "file", "target_file", "file_path")
        if tool_id and path:
            self._paths[tool_id] = path

    def name_for(self, tool_use_id: str) -> str:
        return self._names.get(tool_use_id, "tool")

    def path_for(self, tool_use_id: str) -> str:
        return self._paths.get(tool_use_id, "")

class StuckDetector:
    """Abort when the same failed (tool, path, error) repeats ``threshold`` times."""

    def __init__(self, threshold: int = 3) -> None:
        self.threshold = max(0, int(threshold or 0))
        self._counts: dict[tuple[str, str, str], int] = {}
        self.reason = ""

    def observe(self, name: str, path: str, *, is_error: bool, error_sig: str = "") -> bool:
        if self.threshold <= 0 or not is_error:
            return False
        key = (
            (name or "tool").strip().lower(),
            (path or "").strip(),
            " ".join((error_sig or "fail").split())[:160],
        )
        self._counts[key] = self._counts.get(key, 0) + 1
        if self._counts[key] < self.threshold:
            return False
        self.reason = (
            f"stuck self-interrupt: {key[0]} {key[1] or '(no path)'} "
            f"error={key[2]!r} x{self._counts[key]}"
        )
        return True

def _tool_start_ready(name: str, arguments: Any) -> bool:
    """True when tool args are complete enough to log a start line (skip streaming partials)."""
    args = _tool_args_dict(arguments)
    if not args:
        return False
    lowered = (name or "tool").strip().lower()
    if lowered in {"read", "edit", "write"} or lowered.endswith(":read") or lowered.endswith(":edit"):
        return bool(_first_str(args, "path", "filePath", "file", "target_file", "file_path"))
    if lowered == "grep" or "grep" in lowered:
        pattern = _first_str(args, "pattern", "query", "regex")
        path = _first_str(args, "path")
        return bool(pattern and path)
    return any(v not in (None, "", {}, []) for v in args.values())

def _claude_tool_start_line(name: str, arguments: Any) -> str:
    if not _tool_start_ready(name, arguments):
        return ""
    args = _tool_arg_summary(arguments)
    suffix = f" {args}" if args else ""
    return f"\n[tool:start] {name}{suffix}\n"

def _claude_tool_done_line(name: str, *, is_error: bool = False) -> str:
    status = "fail" if is_error else "done"
    return f"\n[tool:done] {name} {status}\n"

def _claude_stream_event_piece(
    event: dict[str, Any],
    *,
    tool_tracker: _ClaudeToolTracker | None = None,
) -> tuple[str, str]:
    """Return ``(text, role)`` from a Claude/Anthropic stream_event payload."""
    etype = event.get("type")
    if etype == "content_block_delta":
        delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}
        dtype = delta.get("type")
        if dtype == "text_delta" and isinstance(delta.get("text"), str):
            return delta["text"], "answer"
        if dtype == "thinking_delta" and isinstance(delta.get("thinking"), str):
            # Tee applies print_reasoning_in_terminal (verbose vs Thinking timer).
            return delta["thinking"], "reasoning"
    if etype == "content_block_start":
        block = event.get("content_block") if isinstance(event.get("content_block"), dict) else {}
        btype = block.get("type")
        if btype == "text" and isinstance(block.get("text"), str):
            return block["text"], "answer"
        if btype == "tool_use":
            name = str(block.get("name") or "tool")
            tool_id = str(block.get("id") or "")
            if tool_tracker:
                tool_tracker.register(tool_id, name, block.get("input"))
            line = _claude_tool_start_line(name, block.get("input"))
            if not line and name and name != "tool":
                line = f"\n[tool:start] {name}\n"
            return (line, "tool") if line else ("", "")
    return "", ""

def _iter_claude_message_pieces(
    obj: dict[str, Any],
    *,
    tool_tracker: _ClaudeToolTracker | None = None,
    skip_text_if_streamed: bool = False,
    skip_thinking_if_streamed: bool = False,
) -> list[tuple[str, str]]:
    """Extract live tee pieces from Claude ``assistant`` / ``user`` envelopes."""
    typ = str(obj.get("type") or "")
    if typ not in {"assistant", "user"}:
        return []
    message = obj.get("message") if isinstance(obj.get("message"), dict) else {}
    content = message.get("content")
    if not isinstance(content, list):
        return []
    pieces: list[tuple[str, str]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text" and isinstance(block.get("text"), str):
            if not skip_text_if_streamed:
                pieces.append((block["text"], "answer"))
        elif btype == "thinking" and isinstance(block.get("thinking"), str):
            if not skip_thinking_if_streamed:
                pieces.append((block["thinking"], "reasoning"))
        elif btype == "tool_use":
            name = str(block.get("name") or "tool")
            tool_id = str(block.get("id") or "")
            if tool_tracker:
                tool_tracker.register(tool_id, name, block.get("input"))
            line = _claude_tool_start_line(name, block.get("input"))
            if line:
                pieces.append((line, "tool"))
        elif btype == "tool_result":
            tool_id = str(block.get("tool_use_id") or "")
            name = tool_tracker.name_for(tool_id) if tool_tracker else "tool"
            is_error = block.get("is_error") is True
            pieces.append((_claude_tool_done_line(name, is_error=is_error), "tool"))
    return pieces

def _claude_tool_quiet_from_envelope(
    obj: dict[str, Any],
    tool_tracker: _ClaudeToolTracker | None = None,
) -> str | None:
    """Minimal start/done line for Claude stream-json when tools are quiet on TTY."""
    typ = str(obj.get("type") or "")
    if typ == "assistant":
        message = obj.get("message") if isinstance(obj.get("message"), dict) else {}
        content = message.get("content")
        if not isinstance(content, list):
            return None
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                name = str(block.get("name") or "tool")
                if not _tool_start_ready(name, block.get("input")):
                    return None
                focus = _tool_focus(name, block.get("input"))
                return f"\n[tool] {name} start  {focus}\n"
        return None
    if typ == "user":
        message = obj.get("message") if isinstance(obj.get("message"), dict) else {}
        content = message.get("content")
        if not isinstance(content, list):
            return None
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                tool_id = str(block.get("tool_use_id") or "")
                name = tool_tracker.name_for(tool_id) if tool_tracker else "tool"
                status = "fail" if block.get("is_error") is True else "ok"
                return f"\n[tool] {name} done   {status}\n"
        return None
    if typ == "stream_event":
        event = obj.get("event") if isinstance(obj.get("event"), dict) else {}
        if event.get("type") == "content_block_start":
            block = event.get("content_block") if isinstance(event.get("content_block"), dict) else {}
            if block.get("type") == "tool_use":
                name = str(block.get("name") or "tool")
                if not _tool_start_ready(name, block.get("input")):
                    return None
                focus = _tool_focus(name, block.get("input"))
                return f"\n[tool] {name} start  {focus}\n"
    return None

def _tool_arg_summary(arguments: Any) -> str:
    """Serialize tool arguments in full for debugging (no truncation)."""
    if arguments is None:
        return ""
    try:
        raw = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        raw = str(arguments)
    return " ".join(raw.split())

def _tool_args_dict(arguments: Any) -> dict[str, Any]:
    """Normalize tool arguments to a dict when possible."""
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        text = arguments.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}

def _first_str(args: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())
    return ""

def _tool_focus(name: str, arguments: Any) -> str:
    """Short human focus for quiet TTY (path/command), never raw stdout."""
    args = _tool_args_dict(arguments)
    lowered = (name or "tool").strip().lower()
    path = _first_str(args, "path", "filePath", "file", "target_file", "file_path")
    command = _first_str(args, "command", "cmd")
    if lowered in {"view", "read", "read_file", "open"} or "view" in lowered or lowered.endswith(":read"):
        return f"Reading {path}" if path else "Reading"
    if lowered in {"bash", "shell", "run_terminal_cmd"} or "bash" in lowered:
        return command or "bash"
    if lowered in {"grep", "search"} or "grep" in lowered:
        pattern = _first_str(args, "pattern", "query", "regex")
        if pattern and path:
            return f"{pattern} in {path}"
        return pattern or path or "grep"
    if lowered in {"write", "edit", "create", "apply_patch"} or "write" in lowered:
        return f"Writing {path}" if path else "Writing"
    if path:
        return path
    if command:
        return command
    return name or "tool"

def _tool_done_status(data: dict[str, Any]) -> str:
    success = data.get("success")
    if success is True:
        return "ok"
    if success is False:
        err = data.get("error")
        if isinstance(err, dict) and isinstance(err.get("message"), str):
            msg = " ".join(err["message"].split())
            if len(msg) > 60:
                msg = msg[:59] + "…"
            return f"fail ({msg})" if msg else "fail"
        return "fail"
    return "done"

def _tool_name(data: dict[str, Any]) -> str:
    return str(data.get("toolName") or data.get("mcpToolName") or "tool")

def _tool_lifecycle_text(typ: str, data: dict[str, Any]) -> str:
    """Format tool.execution_start / complete lines for the live tee."""
    name = _tool_name(data)
    if typ == "tool.execution_start":
        if not _tool_start_ready(name, data.get("arguments")):
            return ""
        args = _tool_arg_summary(data.get("arguments"))
        suffix = f" {args}" if args else ""
        return f"\n[tool:start] {name}{suffix}\n"
    if typ == "tool.execution_complete":
        return f"\n[tool:done] {name} {_tool_done_status(data)}\n"
    return ""

def _tool_quiet_summary(typ: str, data: dict[str, Any]) -> str | None:
    """Minimal start/done line for quiet TTY; None suppresses (e.g. partials)."""
    name = _tool_name(data)
    if typ == "tool.execution_start":
        return f"\n[tool] {name} start  {_tool_focus(name, data.get('arguments'))}\n"
    if typ == "tool.execution_complete":
        return f"\n[tool] {name} done   {_tool_done_status(data)}\n"
    return None

def _thinking_wrapper_delta(text: str) -> bool:
    """True when the model stuffed chain-of-thought into a message_delta wrapper."""
    if not text:
        return False
    sample = text.lstrip()[:24].lower()
    if sample.startswith("[thinking]") or sample.startswith("<think>"):
        return True
    lowered = text.lower()
    return "[/thinking]" in lowered or "</think>" in lowered

def live_text_from_agent_event(obj: dict[str, Any]) -> tuple[str, str]:
    """Return ``(text, role)`` for one Claude JSONL event.

    ``role`` is ``answer``, ``reasoning``, ``tool``, or ``\"\"`` when nothing to print.
    Reasoning/tool text is always returned; ``_tee_jsonl_live`` decides TTY vs log-only.
    """
    pieces = iter_live_pieces_from_agent_event(obj)
    if pieces:
        return pieces[0]
    return "", ""

def iter_live_pieces_from_agent_event(
    obj: dict[str, Any],
    *,
    tool_tracker: _ClaudeToolTracker | None = None,
    skip_text_if_streamed: bool = False,
    skip_thinking_if_streamed: bool = False,
) -> list[tuple[str, str]]:
    """Return all printable ``(text, role)`` pairs for one JSONL event."""
    typ = str(obj.get("type") or "")
    data = obj.get("data") if isinstance(obj.get("data"), dict) else {}

    # Legacy OpenAI-style streaming events
    if typ in {"assistant.message_delta", "assistant.reasoning_delta"}:
        delta = data.get("deltaContent")
        if isinstance(delta, str):
            if typ == "assistant.reasoning_delta" or _thinking_wrapper_delta(delta):
                return [(delta, "reasoning")]
            return [(delta, "answer")]
    if typ in {"tool.execution_start", "tool.execution_complete"}:
        payload = data if data else obj
        text = _tool_lifecycle_text(typ, payload if isinstance(payload, dict) else {})
        return [(text, "tool")] if text else []
    if typ == "tool.execution_partial_result":
        partial = data.get("partialOutput")
        if isinstance(partial, str):
            return [(partial, "tool")]

    # Flattened JSONL variants
    if isinstance(obj.get("deltaContent"), str) and "delta" in typ:
        role = "reasoning" if "reasoning" in typ or _thinking_wrapper_delta(obj["deltaContent"]) else "answer"
        return [(obj["deltaContent"], role)]

    # Claude Code stream-json (+ --include-partial-messages)
    if typ in {"assistant", "user"}:
        return _iter_claude_message_pieces(
            obj,
            tool_tracker=tool_tracker,
            skip_text_if_streamed=skip_text_if_streamed,
            skip_thinking_if_streamed=skip_thinking_if_streamed,
        )
    if typ == "stream_event":
        event = obj.get("event") if isinstance(obj.get("event"), dict) else {}
        piece, role = _claude_stream_event_piece(event, tool_tracker=tool_tracker)
        return [(piece, role)] if piece and role else []
    if typ in {"content_block_delta", "content_block_start"}:
        piece, role = _claude_stream_event_piece(obj, tool_tracker=tool_tracker)
        return [(piece, role)] if piece and role else []

    return []

def _paint_live_piece(text: str, role: str, stream_kind: str) -> str:
    """Color TTY output by role; answer uses the agent stream kind."""
    if not text:
        return text
    if role == "answer":
        return paint(text, stream_kind)
    if role in {"reasoning", "tool"}:
        return paint(text, "dim")
    return text

class _ThinkingTimer:
    """``Thinking... MM:SS elapsed`` while reasoning is hidden on the console.

    TTY: live overwrite with ``\\r``. Non-TTY: periodic newline updates (~1s).
    """

    _TTY_INTERVAL_S = 0.15
    _PLAIN_INTERVAL_S = 1.0

    def __init__(self, sink) -> None:
        self.sink = sink
        self.started_at: float | None = None
        self.last_paint_at = 0.0
        self.active = False
        self._tty = bool(getattr(sink, "isatty", lambda: False)())

    def on_reasoning(self) -> None:
        now = time.monotonic()
        if self.started_at is None:
            self.started_at = now
            self.active = True
            self._paint(0)
            return
        if not self.active:
            return
        interval = self._TTY_INTERVAL_S if self._tty else self._PLAIN_INTERVAL_S
        if now - self.last_paint_at < interval:
            return
        self._paint(int(now - self.started_at))

    def _paint(self, elapsed_s: int) -> None:
        minutes, seconds = divmod(max(0, elapsed_s), 60)
        line = f"Thinking... {minutes:02d}:{seconds:02d} elapsed"
        if self._tty:
            # Pad so shorter times clear leftover characters from longer lines.
            padded = f"{line:<36}"
            self.sink.write("\r" + paint(padded, "dim"))
        else:
            self.sink.write(paint(f"{line}\n", "dim"))
        self.sink.flush()
        self.last_paint_at = time.monotonic()

    def stop(self) -> None:
        if not self.active:
            return
        self.active = False
        if self._tty and self.started_at is not None:
            self.sink.write("\r" + (" " * 40) + "\r")
            self.sink.flush()
        # Allow a later reasoning turn (after tools/answer) to restart the timer.
        self.started_at = None
        self.last_paint_at = 0.0

class _AgentHeartbeat:
    """Pipeline log heartbeat while the agent CLI subprocess is still running."""

    def __init__(self) -> None:
        self.last_tool = "(starting)"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def note_tool(self, name: str) -> None:
        text = (name or "").strip()
        if text:
            self.last_tool = text

    def start(self) -> None:
        from UnitTest_gen.core.config import get_config

        interval = max(5, get_config().agent_heartbeat_seconds)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(interval,), daemon=True)
        self._thread.start()

    def _run(self, interval: int) -> None:
        while not self._stop.wait(interval):
            log_message(
                f"💓 Agent heartbeat: still running (last tool: {self.last_tool})",
                category="info",
                console=False,
            )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

def final_text_from_agent_jsonl(raw: str, *, live_parts: list[str] | None = None) -> str:
    """Prefer the agent's final answer text; fall back to concatenated live deltas."""
    result_text = ""
    assistant_texts: list[str] = []
    for obj in _iter_jsonl_objects(raw):
        typ = str(obj.get("type") or "")
        if typ == "result" and isinstance(obj.get("result"), str):
            result_text = obj["result"]
            continue
        if typ == "assistant.message":
            data = obj.get("data") if isinstance(obj.get("data"), dict) else obj
            content = data.get("content")
            if isinstance(content, str) and content.strip():
                assistant_texts.append(content)
            continue
        if typ == "assistant":
            message = obj.get("message") if isinstance(obj.get("message"), dict) else {}
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                assistant_texts.append(content)
            elif isinstance(content, list):
                parts = [
                    block.get("text", "")
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                ]
                text = "".join(parts)
                if text.strip():
                    assistant_texts.append(text)
    if result_text.strip():
        return result_text
    if assistant_texts:
        return assistant_texts[-1]
    joined = "".join(live_parts or [])
    return joined if joined.strip() else raw

def _error_sig(value: object) -> str:
    if isinstance(value, dict):
        value = value.get("message") or value.get("error") or value
    text = " ".join(str(value or "").split())
    return text[:160]

def _observe_stuck_event(
    obj: dict[str, Any],
    detector: StuckDetector,
    tool_tracker: _ClaudeToolTracker | None,
) -> bool:
    typ = str(obj.get("type") or "")
    if typ == "tool.execution_complete":
        data = obj.get("data") if isinstance(obj.get("data"), dict) else obj
        name = _tool_name(data)
        args = _tool_args_dict(data.get("arguments"))
        path = _first_str(args, "path", "filePath", "file", "target_file", "file_path")
        status = _tool_done_status(data)
        return detector.observe(name, path, is_error=status.startswith("fail"), error_sig=status)
    if typ != "user":
        return False
    message = obj.get("message") if isinstance(obj.get("message"), dict) else {}
    content = message.get("content")
    if not isinstance(content, list):
        return False
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        is_error = block.get("is_error") is True
        tool_id = str(block.get("tool_use_id") or "")
        name = tool_tracker.name_for(tool_id) if tool_tracker else "tool"
        path = tool_tracker.path_for(tool_id) if tool_tracker else ""
        if detector.observe(name, path, is_error=is_error, error_sig=_error_sig(block.get("content"))):
            return True
    return False

def _tee_jsonl_live(
    stream,
    sink,
    raw_chunks: list[str],
    live_parts: list[str],
    stream_kind: str = "claude",
    reasoning_trace: list[str] | None = None,
    tool_trace: list[str] | None = None,
    stuck_detector: StuckDetector | None = None,
    on_stuck: Callable[[str], None] | None = None,
    heartbeat: _AgentHeartbeat | None = None,
) -> None:
    """Parse agent JSONL and print token/tool deltas as they arrive (colored on TTY).

    ``TESTGEN_PRINT_REASONING`` / ``print_reasoning_in_terminal`` gates reasoning text vs
    the live ``Thinking... MM:SS elapsed`` timer. ``TESTGEN_PRINT_TOOLS`` /
    ``print_tools_in_terminal`` gates verbose tool lines vs minimal start/done summaries.
    When tools are quiet, full tool text and partial stdout stay in the pipeline log
    (``console=False``); the TTY only gets short ``[tool] name start/done`` lines.
    """
    from UnitTest_gen.core.llm import print_reasoning_enabled, print_tools_enabled

    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    buf = ""
    show_reasoning = print_reasoning_enabled()
    show_tools = print_tools_enabled()
    thinking = _ThinkingTimer(sink)
    tool_tracker = _ClaudeToolTracker() if stream_kind == "claude" else None
    saw_stream_answer = False
    saw_stream_reasoning = False

    def _emit(piece: str, role: str) -> None:
        if not piece:
            return
        live_parts.append(piece)
        sink.write(_paint_live_piece(piece, role, stream_kind))
        sink.flush()

    def _handle_piece(piece: str, role: str, event: dict[str, Any] | None = None) -> None:
        if not piece or not role:
            return
        if role == "reasoning":
            if reasoning_trace is not None:
                reasoning_trace.append(piece)
            if show_reasoning:
                thinking.stop()
                _emit(piece, role)
            else:
                thinking.on_reasoning()
            return
        if role == "tool":
            if tool_trace is not None and piece.strip():
                tool_trace.append(piece)
            thinking.stop()
            if piece.strip():
                log_message(piece.strip(), category="dim", console=False)
            if show_tools:
                _emit(piece, role)
                if event is not None:
                    typ = str(event.get("type") or "")
                    data = event.get("data") if isinstance(event.get("data"), dict) else {}
                    payload = data if data else event
                    summary = None
                    if isinstance(payload, dict):
                        summary = _tool_quiet_summary(typ, payload)
                    if summary is None and stream_kind == "claude":
                        summary = _claude_tool_quiet_from_envelope(event, tool_tracker)
                    if summary:
                        if tool_trace is not None:
                            tool_trace.append(summary)
                        _emit(summary, role)
                return
            if event is not None:
                typ = str(event.get("type") or "")
                data = event.get("data") if isinstance(event.get("data"), dict) else {}
                payload = data if data else event
                summary = None
                if isinstance(payload, dict):
                    summary = _tool_quiet_summary(typ, payload)
                if summary is None and stream_kind == "claude":
                    summary = _claude_tool_quiet_from_envelope(event, tool_tracker)
                if summary:
                    if tool_trace is not None:
                        tool_trace.append(summary)
                    _emit(summary, role)
            return
        # answer (and any other printable role)
        thinking.stop()
        _emit(piece, role)

    def _process_event(obj: dict[str, Any]) -> None:
        nonlocal saw_stream_answer, saw_stream_reasoning
        if heartbeat is not None and str(obj.get("type") or "") == "tool.execution_start":
            data = obj.get("data") if isinstance(obj.get("data"), dict) else obj
            heartbeat.note_tool(_tool_name(data))
        for piece, role in iter_live_pieces_from_agent_event(
            obj,
            tool_tracker=tool_tracker,
            skip_text_if_streamed=saw_stream_answer if stream_kind == "claude" else False,
            skip_thinking_if_streamed=(
                saw_stream_reasoning if stream_kind == "claude" else False
            ),
        ):
            if stream_kind == "claude" and str(obj.get("type") or "") == "stream_event":
                if role == "answer":
                    saw_stream_answer = True
                elif role == "reasoning":
                    saw_stream_reasoning = True
            _handle_piece(piece, role, obj)
        if stuck_detector is not None and _observe_stuck_event(obj, stuck_detector, tool_tracker):
            reason = stuck_detector.reason
            log_message(f"🛑 {reason}", category="warning")
            if tool_trace is not None:
                tool_trace.append(f"\n[{reason}]\n")
            if on_stuck is not None:
                on_stuck(reason)

    try:
        while True:
            data = stream.read(256)
            if not data:
                break
            text = decoder.decode(data)
            if not text:
                continue
            raw_chunks.append(text)
            buf += text
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                obj = _parse_jsonl_object(line)
                if obj is None:
                    if line.strip():
                        thinking.stop()
                        sink.write(line + "\n")
                        sink.flush()
                    continue
                _process_event(obj)
        tail = decoder.decode(b"", final=True)
        if tail:
            raw_chunks.append(tail)
            buf += tail
        obj = _parse_jsonl_object(buf)
        if obj is not None:
            _process_event(obj)
        elif buf.strip():
            thinking.stop()
            sink.write(buf)
            sink.flush()
    finally:
        thinking.stop()
        try:
            stream.close()
        except Exception:
            pass

import codecs
import json
import os
import pty
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from collections.abc import Callable, Iterator
from typing import Any

from UnitTest_gen.core.io import log_block, log_message, paint
from UnitTest_gen.core.config import get_config
from UnitTest_gen.core.llm import get_sampling_timings

_DURATION_RE = re.compile(
    r"Duration\s+(\d+)\s*m(?:in(?:ute)?s?)?\s*(\d+)\s*s(?:ec(?:ond)?s?)?",
    re.IGNORECASE,
)
_DURATION_SEC_RE = re.compile(r"Duration\s+(\d+(?:\.\d+)?)\s*s(?:ec(?:ond)?s?)?", re.IGNORECASE)
_TOKENS_RE = re.compile(
    r"Tokens\s+[↑^]?\s*([\d.]+)\s*([kKmM])?(?:\s*\(([\d.]+)\s*([kKmM])?\s*cached\))?"
    r"\s*[•·]?\s*[↓v]?\s*([\d.]+)\s*([kKmM])?",
    re.IGNORECASE,
)

class AgentOfflineError(RuntimeError):
    """Raised when the agent runtime cannot be proven to stay on the local machine."""

class AgentUnavailableError(RuntimeError):
    """Raised when the agent CLI binary or the local model server is missing."""

@dataclass(frozen=True)
class AgentUsage:
    prompt_tokens: float | None = None
    completion_tokens: float | None = None
    cached_tokens: float | None = None
    duration_seconds: float | None = None
    decode_tokens_per_second: float | None = None

    @property
    def tokens_per_second(self) -> float | None:
        """Wall-clock completion rate (misleading for tool loops); prefer decode_*."""
        if not self.completion_tokens or not self.duration_seconds or self.duration_seconds <= 0:
            return None
        return self.completion_tokens / self.duration_seconds

@dataclass(frozen=True)
class AgentResult:
    stdout: str
    stderr: str
    exit_code: int
    duration_seconds: float
    timed_out: bool = False
    usage: AgentUsage | None = None
    reasoning_log: str = ""
    tool_log: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    @property
    def failure_reason(self) -> str:
        if self.timed_out:
            return "agent run exceeded its timeout"
        if self.exit_code != 0:
            return f"agent exited with code {self.exit_code}: {self.stderr.strip()[:400]}"
        return ""

_TRACE_LOG_CAP = 65_536

def _cap_trace(text: str, limit: int = _TRACE_LOG_CAP) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n… (trace truncated)\n"

def log_agent_traces(result: AgentResult) -> None:
    """Write reasoning and tool traces to the pipeline log (always, not TTY-gated)."""
    if result.reasoning_log.strip():
        log_block("MODEL REASONING", result.reasoning_log, category="reasoning", console=False)
    if result.tool_log.strip():
        log_block("AGENT TOOL TRACE", result.tool_log, category="dim", console=False)

def _parse_token_count(value: str, suffix: str | None) -> float:
    amount = float(value)
    unit = (suffix or "").lower()
    if unit == "k":
        return amount * 1000.0
    if unit == "m":
        return amount * 1_000_000.0
    return amount

def _format_token_count(value: float) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1000:
        text = f"{value / 1000:.1f}".rstrip("0").rstrip(".")
        return f"{text}k"
    if value == int(value):
        return str(int(value))
    return f"{value:.1f}"

_CHARS_PER_TOKEN_EST = 3.0

def prompt_size_metrics(prompt: str) -> tuple[int, int]:
    """Return ``(chars, estimated_tokens)`` using ~3 chars/token."""
    chars = len(prompt or "")
    if chars <= 0:
        return 0, 0
    return chars, max(1, int(round(chars / _CHARS_PER_TOKEN_EST)))

def log_prompt_metrics(title: str, prompt: str) -> None:
    """Log prompt char/estimated-token size for plan/coder/fix prompts."""
    from UnitTest_gen.core.io import prompt_kind_from_title

    chars, est_tokens = prompt_size_metrics(prompt)
    kind = prompt_kind_from_title(title)
    log_message(
        f"📏 Prompt size ({kind}): {chars:,} chars (~{est_tokens:,} tokens est.)",
        category="info",
    )

def parse_agent_usage(text: str, *, fallback_duration_seconds: float | None = None) -> AgentUsage | None:
    """Parse agent session footer (Duration / Tokens) when present."""
    if not text:
        return None
    duration_seconds: float | None = None
    match_dur = _DURATION_RE.search(text)
    if match_dur:
        duration_seconds = int(match_dur.group(1)) * 60 + int(match_dur.group(2))
    else:
        match_sec = _DURATION_SEC_RE.search(text)
        if match_sec:
            duration_seconds = float(match_sec.group(1))
    if duration_seconds is None:
        duration_seconds = fallback_duration_seconds

    match_tok = _TOKENS_RE.search(text)
    if not match_tok:
        return None
    prompt_tokens = _parse_token_count(match_tok.group(1), match_tok.group(2))
    cached_tokens = None
    if match_tok.group(3) is not None:
        cached_tokens = _parse_token_count(match_tok.group(3), match_tok.group(4))
    completion_tokens = _parse_token_count(match_tok.group(5), match_tok.group(6))
    return AgentUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cached_tokens=cached_tokens,
        duration_seconds=duration_seconds,
    )

def _with_usage(result: AgentResult) -> AgentResult:
    combined = f"{result.stdout or ''}\n{result.stderr or ''}"
    usage = parse_agent_usage(combined, fallback_duration_seconds=result.duration_seconds)
    if usage is None:
        return result
    return AgentResult(
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=result.exit_code,
        duration_seconds=result.duration_seconds,
        timed_out=result.timed_out,
        usage=usage,
    )

def _usage_log_suffix(result: AgentResult, *, prompt: str | None = None) -> str:
    """Finish-line metrics: wall duration + llama decode tok/s when available."""
    parts: list[str] = []
    duration = float(result.duration_seconds or 0.0)
    if duration > 0:
        parts.append(f"{duration:.1f}s")

    proxy = get_sampling_timings()
    usage = result.usage
    decode = None
    if usage is not None and usage.decode_tokens_per_second is not None:
        decode = usage.decode_tokens_per_second
    elif proxy.decode_tokens_per_second is not None:
        decode = proxy.decode_tokens_per_second

    if proxy.request_count > 0 and (proxy.prompt_n > 0 or proxy.predicted_n > 0):
        parts.append(f"↑{_format_token_count(proxy.prompt_n)}")
        parts.append(f"↓{_format_token_count(proxy.predicted_n)}")
    elif (
        usage is not None
        and usage.prompt_tokens is not None
        and usage.completion_tokens is not None
    ):
        prompt_part = f"tokens ↑{_format_token_count(usage.prompt_tokens)}"
        if usage.cached_tokens is not None:
            prompt_part += f" ({_format_token_count(usage.cached_tokens)} cached)"
        parts.append(prompt_part)
        parts.append(f"↓{_format_token_count(usage.completion_tokens)}")
    else:
        prompt_chars, prompt_est = prompt_size_metrics(prompt or "")
        out_text = f"{result.stdout or ''}{result.stderr or ''}"
        out_chars, out_est = prompt_size_metrics(out_text)
        if prompt_est:
            parts.append(f"prompt ~{_format_token_count(prompt_est)} tok est.")
        if out_est:
            parts.append(f"↓~{_format_token_count(out_est)} tok est.")

    head = " | " + " ".join(parts) if parts else ""
    if decode is None:
        return head
    decode_part = f"decode {decode:.1f} tok/s"
    if proxy.request_count > 1:
        decode_part += f" ({proxy.request_count} req)"
    if head:
        return f"{head} | {decode_part}"
    return f" | {decode_part}"

def print_prompt_to_terminal(title: str, prompt: str) -> None:
    from UnitTest_gen.core.io import paint, prompt_kind_from_title
    from UnitTest_gen.core.config import get_config

    kind = prompt_kind_from_title(title)
    border = "=" * 120
    header = paint(f"\n{border}\n{title}\n{border}", kind)
    if get_config().print_prompts_in_terminal:
        body = paint(prompt or "", "dim")
    else:
        body = paint("Prompt Processing...", "dim")
    footer = paint(border, kind)
    print(f"{header}\n{body}\n{footer}\n", flush=True)

def _tee_raw_bytes(
    read_fn: Callable[[], bytes],
    close_fn: Callable[[], None],
    sink,
    chunks: list[str],
) -> None:
    """Decode UTF-8 bytes from *read_fn* into *sink* and *chunks*."""
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    try:
        while True:
            data = read_fn()
            if not data:
                break
            text = decoder.decode(data)
            if not text:
                continue
            chunks.append(text)
            sink.write(text)
            sink.flush()
        tail = decoder.decode(b"", final=True)
        if tail:
            chunks.append(tail)
            sink.write(tail)
            sink.flush()
    finally:
        close_fn()

def _tee_byte_stream(stream, sink, chunks: list[str]) -> None:
    """Copy subprocess bytes to the terminal while keeping a capture buffer."""
    def close() -> None:
        try:
            stream.close()
        except Exception:
            pass

    _tee_raw_bytes(stream.read, close, sink, chunks)

def _tee_pty(master_fd: int, sink, chunks: list[str]) -> None:
    """Copy PTY master bytes to the terminal while keeping a capture buffer."""
    def read() -> bytes:
        try:
            return os.read(master_fd, 256)
        except OSError:
            return b""

    def close() -> None:
        try:
            os.close(master_fd)
        except OSError:
            pass

    _tee_raw_bytes(read, close, sink, chunks)

def _kill_timed_out_process(process: subprocess.Popen) -> None:
    process.kill()
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass

def _timeout_agent_result(exc: subprocess.TimeoutExpired, started: float) -> AgentResult:
    def _decode(value: Any) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", "replace")
        return value or ""

    return AgentResult(
        stdout=_decode(exc.stdout),
        stderr=_decode(exc.stderr),
        exit_code=124,
        duration_seconds=time.monotonic() - started,
        timed_out=True,
    )

def _run_cli_subprocess(
    command: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    timeout: int,
    stream: bool,
    jsonl_live: bool = False,
    stream_kind: str = "claude",
) -> AgentResult:
    """Run an agent CLI subprocess, optionally teeing live output.

    ``jsonl_live`` (preferred for streaming): read JSONL events on a pipe and print
    text deltas immediately. Plain PTY tee is a fallback for non-JSONL CLIs.
    ``stream_kind`` selects the TTY color for answer tokens (``claude``).
    """
    started = time.monotonic()
    if not stream:
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return _timeout_agent_result(exc, started)
        return AgentResult(
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            exit_code=completed.returncode,
            duration_seconds=time.monotonic() - started,
        )

    if jsonl_live:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        raw_chunks: list[str] = []
        live_parts: list[str] = []
        reasoning_trace: list[str] = []
        tool_trace: list[str] = []
        stderr_chunks: list[str] = []
        stuck_reason: list[str] = []
        from UnitTest_gen.core.agent import _AgentHeartbeat

        heartbeat = _AgentHeartbeat()
        heartbeat.start()
        stdout_thread = threading.Thread(
            target=_tee_jsonl_live,
            args=(
                process.stdout,
                sys.stdout,
                raw_chunks,
                live_parts,
                stream_kind,
                reasoning_trace,
                tool_trace,
            ),
            kwargs={
                "stuck_detector": StuckDetector(get_config().stuck_repeat_threshold),
                "on_stuck": lambda reason: (
                    stuck_reason.append(reason),
                    _kill_timed_out_process(process),
                ),
                "heartbeat": heartbeat,
            },
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=_tee_byte_stream,
            args=(process.stderr, sys.stderr, stderr_chunks),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()
        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_timed_out_process(process)
        stdout_thread.join(timeout=30)
        stderr_thread.join(timeout=30)
        heartbeat.stop()
        if live_parts:
            sys.stdout.write("\n")
            sys.stdout.flush()
        raw = "".join(raw_chunks)
        tool_text = "".join(tool_trace)
        if stuck_reason:
            tool_text = f"{tool_text}\n[{stuck_reason[0]}]\n"
        return AgentResult(
            stdout=final_text_from_agent_jsonl(raw, live_parts=live_parts),
            stderr="".join(stderr_chunks),
            exit_code=124 if timed_out else (process.returncode or 0),
            duration_seconds=time.monotonic() - started,
            timed_out=timed_out,
            reasoning_log=_cap_trace("".join(reasoning_trace)),
            tool_log=_cap_trace(tool_text),
        )

    # Fallback: PTY so CLIs that buffer on pipes still flush.
    master_fd, slave_fd = pty.openpty()
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
        )
    finally:
        try:
            os.close(slave_fd)
        except OSError:
            pass

    chunks: list[str] = []
    reader = threading.Thread(
        target=_tee_pty, args=(master_fd, sys.stdout, chunks), daemon=True
    )
    reader.start()
    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_timed_out_process(process)
    reader.join(timeout=30)
    return AgentResult(
        stdout="".join(chunks),
        stderr="",
        exit_code=124 if timed_out else (process.returncode or 0),
        duration_seconds=time.monotonic() - started,
        timed_out=timed_out,
    )

import os
import shutil
from pathlib import Path
from typing import Literal

from UnitTest_gen.core.config import (
    AGENT_PHASE_ENV,
    AGENT_PHASE_PLAN,
    PLAN_ALLOWED_TOOLS,
    PLAN_AVAILABLE_TOOLS,
    PLAN_DENIED_TOOLS,
)
from UnitTest_gen.core.llm import (
    OFFLINE_PROVIDER_API_KEY,
    is_local_url,
    is_server_healthy,
    load_local_llm_env,
    load_vendor_env,
    provider_base_url,
    resolve_agent_model,
)
from UnitTest_gen.core.io import log_block, log_message
from UnitTest_gen.core.config import get_config
from UnitTest_gen.core.hooks import (
    DISCOVERY_ENV,
    OWNING_MODULE_ENV,
    READ_ONCE_ENV,
    SOURCE_IMPORTS_ENV,
    claude_read_hook_script_path,
    write_claude_read_hook_settings,
    write_discovery_sidecar,
    write_read_once_sidecar,
    write_source_import_sidecar,
)
from UnitTest_gen.core.llm import (
    reset_sampling_timings,
    set_active_sampling,
    start_sampling_proxy,
)

_CLOUD_CREDENTIAL_VARS = (
    "COPILOT_GITHUB_TOKEN",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "GITHUB_COPILOT_TOKEN",
    "OPENAI_API_KEY",
)

AgentPhase = Literal["plan", "gen", "fix"]

def claude_binary() -> str:
    override = os.environ.get("CLAUDE_BIN", "").strip()
    for candidate in (override, Path.home() / ".local" / "bin" / "claude", shutil.which("claude")):
        if candidate and Path(candidate).is_file():
            return str(candidate)
    raise AgentUnavailableError(
        "Claude CLI not found. Run AgenticLLM/scripts/install-claude.sh first."
    )

def build_claude_env(
    *,
    temperature: float | None = None,
    presence_penalty: float | None = None,
    enable_thinking: bool | None = None,
    reasoning_budget: int | None = None,
    phase: AgentPhase = "gen",
) -> dict[str, str]:
    config = get_config()
    shared = load_local_llm_env()
    claude_keys = load_vendor_env("claude")
    upstream = provider_base_url(shared)
    if not is_local_url(upstream):
        raise AgentOfflineError(
            f"Refusing to run Claude: provider base URL is not local ({upstream})."
        )
    if config.use_sampling_proxy:
        proxy_url = start_sampling_proxy(
            port=config.proxy_port,
        )
        anthropic_base = proxy_url.removesuffix("/v1")
        set_active_sampling(
            temperature=temperature,
            presence_penalty=presence_penalty,
            enable_thinking=enable_thinking,
            reasoning_budget=reasoning_budget,
        )
    else:
        host = shared.get("LLAMA_HOST", "127.0.0.1")
        anthropic_base = f"http://{host}:{config.llama_port}"

    env = dict(os.environ)
    for key in _CLOUD_CREDENTIAL_VARS:
        env.pop(key, None)

    model = resolve_agent_model(config).alias
    api_key = (
        claude_keys.get("ANTHROPIC_API_KEY")
        or claude_keys.get("ANTHROPIC_AUTH_TOKEN")
        or OFFLINE_PROVIDER_API_KEY
    )
    env.update(
        {
            "ANTHROPIC_BASE_URL": anthropic_base.rstrip("/"),
            "ANTHROPIC_API_KEY": api_key,
            "ANTHROPIC_AUTH_TOKEN": claude_keys.get("ANTHROPIC_AUTH_TOKEN") or api_key,
            "ANTHROPIC_MODEL": model,
            "CI": "1",
        }
    )
    if not model:
        raise AgentOfflineError(
            "No local model configured. Set ANTHROPIC_MODEL in AgenticLLM/.env or pass --agent-model."
        )
    return env

def _split_tool_csv(value: str | None) -> set[str]:
    return {item.strip() for item in (value or "").split(",") if item.strip()}

def _is_plan_call(
    *,
    allowed_tools: str | None,
    available_tools: str | None,
    denied_tools: str | None,
) -> bool:
    available = _split_tool_csv(available_tools)
    allowed = _split_tool_csv(allowed_tools)
    denied = _split_tool_csv(denied_tools)
    if available == _split_tool_csv(PLAN_AVAILABLE_TOOLS):
        return True
    if allowed == _split_tool_csv(PLAN_ALLOWED_TOOLS) and _split_tool_csv(
        PLAN_DENIED_TOOLS
    ).issubset(denied):
        return True
    return False

def _claude_tool_flags(
    *,
    allowed_tools: str | None,
    denied_tools: str | None,
    available_tools: str | None,
) -> list[str]:
    _ = allowed_tools, denied_tools, available_tools
    flags = ["--permission-mode", "bypassPermissions"]
    for tool in ("Read", "Bash", "Edit", "Write"):
        flags.extend(["--allowedTools", tool])
    return flags

def run_claude(
    prompt: str,
    *,
    cwd: str,
    system_prompt: str | None = None,
    allowed_tools: str | None = None,
    denied_tools: str | None = None,
    available_tools: str | None = None,
    excluded_tools: str | None = None,
    timeout: int | None = None,
    title: str = "CLAUDE PROMPT",
    require_healthy_server: bool = True,
    stream: bool | None = None,
    temperature: float | None = None,
    presence_penalty: float | None = None,
    enable_thinking: bool | None = None,
    reasoning_budget: int | None = None,
    phase: AgentPhase = "gen",
    owning_module_dir: str = "",
) -> AgentResult:
    """Execute one non-interactive Claude Code request."""
    _ = excluded_tools
    config = get_config()
    if require_healthy_server and not is_server_healthy():
        raise AgentUnavailableError(
            "Local model server is not healthy. Start AgenticLLM/scripts/start-llama-server.sh."
        )

    stream_enabled = config.agent_stream if stream is None else stream
    agent_phase: AgentPhase = "plan" if _is_plan_call(
        allowed_tools=allowed_tools,
        available_tools=available_tools,
        denied_tools=denied_tools,
    ) else phase
    env = build_claude_env(
        temperature=temperature,
        presence_penalty=presence_penalty,
        enable_thinking=enable_thinking,
        reasoning_budget=reasoning_budget,
        phase=agent_phase,
    )
    plan_call = agent_phase == "plan"
    prior_phase = env.get(AGENT_PHASE_ENV)
    if plan_call:
        env[AGENT_PHASE_ENV] = AGENT_PHASE_PLAN
    elif AGENT_PHASE_ENV in env:
        env.pop(AGENT_PHASE_ENV, None)
    log_text = prompt
    if system_prompt and system_prompt.strip():
        log_text = f"SYSTEM (append):\n{system_prompt.strip()}\n\nUSER:\n{prompt}"
    sidecar = write_source_import_sidecar(log_text)
    env[SOURCE_IMPORTS_ENV] = str(sidecar)
    if not env.get(READ_ONCE_ENV):
        env[READ_ONCE_ENV] = str(write_read_once_sidecar())
    if not env.get(DISCOVERY_ENV):
        env[DISCOVERY_ENV] = str(write_discovery_sidecar())
    if owning_module_dir.strip():
        env[OWNING_MODULE_ENV] = str(Path(owning_module_dir).resolve())
    tool_flags = _claude_tool_flags(
        allowed_tools=allowed_tools,
        denied_tools=denied_tools,
        available_tools=available_tools,
    )
    settings_path = write_claude_read_hook_settings()
    hook_script = claude_read_hook_script_path()
    log_message(
        f"Claude hooks: settings={settings_path} script={hook_script} "
        f"exists={hook_script.is_file()} "
        f"discovery={env.get(DISCOVERY_ENV, '')} "
        f"owning_module={env.get(OWNING_MODULE_ENV, '')}",
        category="info",
    )
    command = [
        claude_binary(),
        "-p",
        prompt,
        "--bare",
        "--model",
        env["ANTHROPIC_MODEL"],
        "--output-format",
        "stream-json" if stream_enabled else "text",
        *tool_flags,
    ]
    if hook_script.is_file():
        command.extend(["--settings", str(settings_path)])
    if system_prompt and system_prompt.strip():
        p_idx = command.index("-p")
        command[p_idx + 2 : p_idx + 2] = [
            "--append-system-prompt",
            system_prompt.strip(),
        ]
    if stream_enabled:
        command.extend(["--include-partial-messages", "--verbose"])

    print_prompt_to_terminal(title, log_text)
    log_block(title, log_text, category="context", console=False)
    log_prompt_metrics(title, log_text)
    log_message(
        format_agent_session_line(
            agent_label="Claude Code",
            endpoint=env["ANTHROPIC_BASE_URL"],
            model=env["ANTHROPIC_MODEL"],
            stream=stream_enabled,
            available=csv_from_repeated_flag(tool_flags, "--allowedTools"),
            denied=csv_from_repeated_flag(tool_flags, "--disallowedTools"),
            temperature=temperature,
            presence_penalty=presence_penalty,
            enable_thinking=enable_thinking,
        ),
        category="info",
    )

    reset_sampling_timings()
    result: AgentResult | None = None
    try:
        result = _with_usage(
            _run_cli_subprocess(
                command,
                cwd=cwd,
                env=env,
                timeout=timeout or config.agent_timeout_seconds,
                stream=stream_enabled,
                jsonl_live=stream_enabled,
                stream_kind="claude",
            )
        )
    finally:
        _ = prior_phase
        if result is not None:
            log_agent_traces(result)
            if result.timed_out and result.tool_log.strip():
                log_message(
                    "⏱️ Claude Code timed out — partial AGENT TOOL TRACE flushed above.",
                    category="warning",
                )
    if result is None:
        raise AgentUnavailableError("Claude Code subprocess did not return a result.")
    log_block("CLAUDE RESPONSE", result.stdout or "(no output)", category="code", console=False)
    if result.timed_out:
        log_message("⏱️ Claude Code timed out.", category="warning")
    elif result.exit_code != 0:
        log_message(
            f"⚠️ Claude Code exited {result.exit_code}: {result.stderr.strip()[:300]}"
            f"{_usage_log_suffix(result, prompt=prompt)}",
            category="warning",
        )
    else:
        log_message(
            f"🤖 Claude Code finished in {result.duration_seconds / 60:.1f} min "
            f"(exit {result.exit_code}){_usage_log_suffix(result, prompt=prompt)}.",
            category="success",
        )
    return result
