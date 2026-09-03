"""Local llama-server, sampling proxy, Anthropic/OpenAI bridge."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

def _loads_json_dict(data: bytes) -> dict[str, Any] | None:
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None

def _parse_sse_data_line(line: bytes) -> dict[str, Any] | None:
    stripped = line.strip()
    if not stripped.startswith(b"data:"):
        return None
    data = stripped[5:].strip()
    if not data or data == b"[DONE]":
        return None
    return _loads_json_dict(data)

def _mutate_json_body(body: bytes, mutator: Callable[[dict[str, Any]], None]) -> bytes:
    if not body:
        return body
    payload = _loads_json_dict(body)
    if payload is None:
        return body
    mutator(payload)
    return json.dumps(payload).encode("utf-8")

def _for_openai_choice_blocks(
    payload: dict[str, Any],
    fn: Callable[[dict[str, Any]], None],
) -> None:
    choices = payload.get("choices")
    if not isinstance(choices, list):
        return
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        for key in ("delta", "message"):
            block = choice.get(key)
            if isinstance(block, dict):
                fn(block)

def _merge_reasoning_into_message(message: dict[str, Any]) -> None:
    reasoning = message.get("reasoning_content")
    if not isinstance(reasoning, str) or not reasoning:
        return
    content = message.get("content")
    prefix = f"[thinking]\n{reasoning}\n[/thinking]\n"
    message["content"] = f"{prefix}{content}" if isinstance(content, str) and content else prefix

def _merge_reasoning_into_delta(delta: dict[str, Any]) -> None:
    reasoning = delta.get("reasoning_content")
    if not isinstance(reasoning, str) or not reasoning:
        return
    content = delta.get("content")
    delta["content"] = f"{reasoning}{content}" if isinstance(content, str) and content else reasoning

def merge_reasoning_into_openai_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Merge deepseek reasoning_content into content for clients that only read content."""
    for choice in payload.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        delta = choice.get("delta")
        if isinstance(delta, dict):
            _merge_reasoning_into_delta(delta)
        message = choice.get("message")
        if isinstance(message, dict):
            _merge_reasoning_into_message(message)
    return payload

def _rewrite_openai_payload_bytes(
    raw: bytes,
    transform: Callable[[dict[str, Any]], None],
) -> bytes:
    return _mutate_json_body(raw, transform)

def rewrite_sse_line_merge_reasoning(line: bytes) -> bytes:
    """Rewrite one SSE line so reasoning_content is also visible in content."""
    return _rewrite_sse_openai_line(line, merge_reasoning_into_openai_payload)

def rewrite_json_body_merge_reasoning(body: bytes) -> bytes:
    return _rewrite_openai_payload_bytes(body, merge_reasoning_into_openai_payload)

def strip_think_tags(text: str) -> str:
    """Remove complete ``<think>…</think>`` blocks; drop a trailing unclosed ``<think>``."""
    if not text:
        return text
    cleaned = _THINK_BLOCK.sub("", text)
    lower = cleaned.lower()
    start = lower.rfind("<think>")
    if start >= 0:
        cleaned = cleaned[:start]
    return cleaned

def strip_think_tags_stateful_step(
    text: str, in_think: bool
) -> tuple[str, bool, str]:
    """Stateful ``<think>`` stripper for streaming deltas.

    Returns ``(visible_text, still_in_think, extracted_reasoning)``.
    Handles ``<think>``/``</think>`` split across multiple calls.
    """
    if not text:
        return ("", in_think, "")
    visible_parts: list[str] = []
    reasoning_parts: list[str] = []
    pos = 0
    lower = text.lower()
    while pos < len(text):
        if in_think:
            end_idx = lower.find("</think>", pos)
            if end_idx < 0:
                reasoning_parts.append(text[pos:])
                pos = len(text)
            else:
                reasoning_parts.append(text[pos:end_idx])
                pos = end_idx + len("</think>")
                in_think = False
        else:
            start_idx = lower.find("<think>", pos)
            if start_idx < 0:
                visible_parts.append(text[pos:])
                pos = len(text)
            else:
                visible_parts.append(text[pos:start_idx])
                pos = start_idx + len("<think>")
                in_think = True
    return ("".join(visible_parts), in_think, "".join(reasoning_parts))

def _strip_think_from_payload(
    payload: dict[str, Any], in_think: bool
) -> tuple[bool, str]:
    """Strip ``<think>`` from content fields in an OpenAI payload (stateful).

    Returns ``(next_in_think, extracted_reasoning)``.  Mutates *payload* in place.
    """
    reasoning_parts: list[str] = []

    def _strip_block(block: dict[str, Any]) -> None:
        nonlocal in_think
        block.pop("reasoning_content", None)
        content = block.get("content")
        if isinstance(content, str):
            visible, in_think, extracted = strip_think_tags_stateful_step(content, in_think)
            block["content"] = visible
            if extracted:
                reasoning_parts.append(extracted)

    _for_openai_choice_blocks(payload, _strip_block)
    return in_think, "".join(reasoning_parts)

def strip_reasoning_from_openai_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop ``reasoning_content`` and leaked think tags so Copilot does not print them."""

    def _strip_block(block: dict[str, Any]) -> None:
        block.pop("reasoning_content", None)
        content = block.get("content")
        if isinstance(content, str) and "<think>" in content.lower():
            block["content"] = strip_think_tags(content)

    _for_openai_choice_blocks(payload, _strip_block)
    return payload

def _rewrite_sse_openai_line(line: bytes, transform: Callable[[dict[str, Any]], None]) -> bytes:
    payload = _parse_sse_data_line(line)
    if payload is None:
        return line
    transform(payload)
    return b"data: " + json.dumps(payload, ensure_ascii=False).encode("utf-8")

def rewrite_sse_line_strip_reasoning(line: bytes) -> bytes:
    """Rewrite one SSE line so Copilot never sees reasoning_content / think tags."""
    return _rewrite_sse_openai_line(line, strip_reasoning_from_openai_payload)

def rewrite_json_body_strip_reasoning(body: bytes) -> bytes:
    return _rewrite_openai_payload_bytes(body, strip_reasoning_from_openai_payload)

def extract_reasoning_from_openai_payload(payload: dict[str, Any]) -> str:
    """Collect reasoning_content and leaked ``<think>`` blocks from an OpenAI payload."""
    parts: list[str] = []

    def _collect(block: dict[str, Any]) -> None:
        reasoning = block.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning:
            parts.append(reasoning)
        content = block.get("content")
        if isinstance(content, str) and "<think>" in content.lower():
            parts.extend(_THINK_BLOCK.findall(content))

    _for_openai_choice_blocks(payload, _collect)
    return "".join(parts)

import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

def _text_from_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "".join(parts)

def anthropic_messages_to_openai(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert Anthropic ``/v1/messages`` body to OpenAI chat.completions body."""
    messages: list[dict[str, Any]] = []
    system = payload.get("system")
    if isinstance(system, str) and system.strip():
        messages.append({"role": "system", "content": system})
    elif isinstance(system, list):
        text = _text_from_content(system)
        if text:
            messages.append({"role": "system", "content": text})

    for msg in payload.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role") or "user"
        content = msg.get("content")
        if isinstance(content, str):
            messages.append({"role": role, "content": content})
            continue
        if not isinstance(content, list):
            continue
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text" and isinstance(block.get("text"), str):
                text_parts.append(block["text"])
            elif btype == "tool_use":
                tool_calls.append(
                    {
                        "id": block.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                        "type": "function",
                        "function": {
                            "name": block.get("name") or "tool",
                            "arguments": json.dumps(block.get("input") or {}),
                        },
                    }
                )
            elif btype == "tool_result":
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": block.get("tool_use_id") or "",
                        "content": _text_from_content(block.get("content")),
                    }
                )
        if role == "assistant" and tool_calls:
            entry: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts) or None}
            entry["tool_calls"] = tool_calls
            messages.append(entry)
        elif text_parts:
            messages.append({"role": role, "content": "".join(text_parts)})

    out: dict[str, Any] = {
        "model": payload.get("model") or "local",
        "messages": messages,
        "stream": bool(payload.get("stream")),
    }
    if "max_tokens" in payload:
        out["max_tokens"] = payload["max_tokens"]
    if "temperature" in payload:
        out["temperature"] = payload["temperature"]
    if "top_p" in payload:
        out["top_p"] = payload["top_p"]
    tools = payload.get("tools")
    if isinstance(tools, list) and tools:
        out["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": tool.get("name") or "tool",
                    "description": tool.get("description") or "",
                    "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
                },
            }
            for tool in tools
            if isinstance(tool, dict)
        ]
    return out

def openai_completion_to_anthropic(payload: dict[str, Any], *, model: str) -> dict[str, Any]:
    """Convert a non-stream OpenAI chat completion to Anthropic message response."""
    choice = {}
    choices = payload.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        choice = choices[0]
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    content_blocks: list[dict[str, Any]] = []
    text = message.get("content")
    if isinstance(text, str) and text:
        content_blocks.append({"type": "text", "text": text})
    for call in message.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        fn = call.get("function") if isinstance(call.get("function"), dict) else {}
        args_raw = fn.get("arguments") or "{}"
        try:
            args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
        except json.JSONDecodeError:
            args = {"raw": args_raw}
        content_blocks.append(
            {
                "type": "tool_use",
                "id": call.get("id") or f"toolu_{uuid.uuid4().hex[:12]}",
                "name": fn.get("name") or "tool",
                "input": args if isinstance(args, dict) else {"value": args},
            }
        )
    if not content_blocks:
        content_blocks = [{"type": "text", "text": ""}]

    stop = choice.get("finish_reason") or "end_turn"
    if stop == "tool_calls":
        stop_reason = "tool_use"
    elif stop == "length":
        stop_reason = "max_tokens"
    else:
        stop_reason = "end_turn"

    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    return {
        "id": payload.get("id") or f"msg_{uuid.uuid4().hex[:12]}",
        "type": "message",
        "role": "assistant",
        "model": model or payload.get("model") or "local",
        "content": content_blocks,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
        },
    }

def anthropic_sse_event(event: dict[str, Any]) -> bytes:
    """Serialize one Anthropic Messages SSE event."""
    return f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n".encode(
        "utf-8"
    )

def anthropic_sse_from_openai_text(text: str, *, model: str, message_id: str | None = None) -> bytes:
    """Build a minimal Anthropic SSE stream for a completed assistant text reply."""
    msg_id = message_id or f"msg_{uuid.uuid4().hex[:12]}"
    events: list[dict[str, Any]] = [
        {
            "type": "message_start",
            "message": {
                "id": msg_id,
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": text},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": max(1, len(text.split()))},
        },
        {"type": "message_stop"},
    ]
    return b"".join(anthropic_sse_event(event) for event in events)

def _openai_stream_choice(payload: dict[str, Any]) -> dict[str, Any]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return {}
    choice = choices[0]
    return choice if isinstance(choice, dict) else {}

def openai_choice_finish_reason(payload: dict[str, Any]) -> str | None:
    """Return ``finish_reason`` from one OpenAI stream chunk, if present."""
    reason = _openai_stream_choice(payload).get("finish_reason")
    return str(reason) if isinstance(reason, str) and reason else None

def openai_choice_tool_call_deltas(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return ``tool_calls`` delta objects from one OpenAI chat completion chunk."""
    choice = _openai_stream_choice(payload)
    delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    tool_calls = delta.get("tool_calls") if isinstance(delta.get("tool_calls"), list) else []
    if not tool_calls and isinstance(message.get("tool_calls"), list):
        tool_calls = message["tool_calls"]
    return [tc for tc in tool_calls if isinstance(tc, dict)]

def anthropic_stop_reason_from_openai(finish_reason: str | None) -> str:
    if finish_reason == "tool_calls":
        return "tool_use"
    if finish_reason == "length":
        return "max_tokens"
    return "end_turn"

def openai_choice_delta_parts(payload: dict[str, Any]) -> tuple[str, str]:
    """Return ``(content, reasoning_content)`` from one OpenAI chat completion chunk."""
    choice = _openai_stream_choice(payload)
    if not choice:
        return "", ""
    delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    content = delta.get("content") if isinstance(delta.get("content"), str) else ""
    if not content and isinstance(message.get("content"), str):
        content = message["content"]
    reasoning = (
        delta.get("reasoning_content")
        if isinstance(delta.get("reasoning_content"), str)
        else ""
    )
    if not reasoning and isinstance(message.get("reasoning_content"), str):
        reasoning = message["reasoning_content"]
    return content or "", reasoning or ""

def openai_sse_chunks_to_text(raw: bytes) -> str:
    """Extract concatenated assistant text from an OpenAI SSE body."""
    parts: list[str] = []
    for line in raw.splitlines():
        if not line.startswith(b"data:"):
            continue
        data = line[5:].strip()
        if not data or data == b"[DONE]":
            continue
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        content, _reasoning = openai_choice_delta_parts(payload)
        if content:
            parts.append(content)
    return "".join(parts)

def now_ms() -> int:
    return int(time.time() * 1000)

# Consecutive OpenAI payloads without reasoning_content before closing Anthropic thinking.
# 2 avoids aborting on interleaved r→c→r mid-budget streams.
_REASONING_IDLE_FLUSH = 2

def parse_openai_sse_data_line(line: bytes) -> dict[str, Any] | None:
    return _parse_sse_data_line(line)

@dataclass
class _OpenAiAnthropicSsePipe:
    write: Callable[[bytes], bool]
    model: str
    merge_reasoning: bool
    log_reasoning: bool
    on_payload: Callable[[dict[str, Any]], None] | None = None
    msg_id: str = field(default_factory=lambda: f"msg_{uuid.uuid4().hex[:12]}")
    message_started: bool = False
    thinking_open: bool = False
    text_open: bool = False
    tool_use_open: bool = False
    block_index: int = -1
    reasoning_parts: list[str] = field(default_factory=list)
    output_chars: int = 0
    last_finish_reason: str | None = None
    active_tool_index: int | None = None
    tool_calls: dict[int, dict[str, Any]] = field(default_factory=dict)
    pending_text: list[str] = field(default_factory=list)
    # Consecutive payloads without reasoning_content while thinking is open.
    reasoning_idle_streak: int = 0

    def _emit(self, event: dict[str, Any]) -> bool:
        return self.write(anthropic_sse_event(event))

    def _start_message(self) -> bool:
        if self.message_started:
            return True
        ok = self._emit(
            {
                "type": "message_start",
                "message": {
                    "id": self.msg_id,
                    "type": "message",
                    "role": "assistant",
                    "model": self.model,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            }
        )
        self.message_started = ok or self.message_started
        return ok

    def _close_block(self) -> bool:
        if not (self.thinking_open or self.text_open or self.tool_use_open):
            return True
        ok = self._emit({"type": "content_block_stop", "index": self.block_index})
        self.thinking_open = self.text_open = self.tool_use_open = False
        self.active_tool_index = None
        return ok

    def _open_block(self, content_block: dict[str, Any]) -> bool:
        if not self._start_message() or not self._close_block():
            return False
        self.block_index += 1
        return self._emit(
            {
                "type": "content_block_start",
                "index": self.block_index,
                "content_block": content_block,
            }
        )

    def _ensure(self, block: dict[str, Any], flag: str) -> bool:
        if getattr(self, flag):
            return True
        if not self._open_block(block):
            return False
        setattr(self, flag, True)
        return True

    def _delta(self, delta: dict[str, Any]) -> bool:
        return self._emit(
            {
                "type": "content_block_delta",
                "index": self.block_index,
                "delta": delta,
            }
        )

    def _emit_thinking(self, text: str) -> bool:
        if not text:
            return True
        if not self._ensure({"type": "thinking", "thinking": ""}, "thinking_open"):
            return False
        return self._delta({"type": "thinking_delta", "thinking": text})

    def _emit_text(self, text: str) -> bool:
        if not text:
            return True
        if not self._ensure({"type": "text", "text": ""}, "text_open"):
            return False
        self.output_chars += len(text)
        return self._delta({"type": "text_delta", "text": text})

    def _flush_pending_text(self) -> bool:
        """Close thinking (via opening text) and emit buffered answer deltas."""
        if not self.pending_text:
            return True
        text = "".join(self.pending_text)
        self.pending_text.clear()
        return self._emit_text(text)

    def _emit_tool_json(self, partial_json: str) -> bool:
        return not partial_json or self._delta({"type": "input_json_delta", "partial_json": partial_json})

    def _handle_tool_deltas(self, tool_deltas: list[dict[str, Any]]) -> bool:
        for tc in sorted(tool_deltas, key=lambda item: int(item.get("index", 0))):
            idx = int(tc.get("index", 0))
            entry = self.tool_calls.setdefault(
                idx, {"id": "", "name": "", "arguments": "", "block_started": False}
            )
            if tc.get("id"):
                entry["id"] = str(tc["id"])
            fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
            if fn.get("name"):
                entry["name"] = str(fn["name"])
            args_part = fn.get("arguments") if isinstance(fn.get("arguments"), str) else ""
            if not entry["block_started"] and entry["id"] and entry["name"]:
                if self.active_tool_index not in (None, idx) and not self._close_block():
                    return False
                if not self._ensure(
                    {"type": "tool_use", "id": entry["id"], "name": entry["name"], "input": {}},
                    "tool_use_open",
                ):
                    return False
                entry["block_started"] = True
                self.active_tool_index = idx
            if entry["block_started"] and args_part:
                entry["arguments"] += args_part
                if not self._emit_tool_json(args_part):
                    return False
        return True

    def handle_payload(self, payload: dict[str, Any]) -> bool:
        if self.on_payload:
            self.on_payload(payload)
        finish = openai_choice_finish_reason(payload)
        if finish:
            self.last_finish_reason = finish
        content, reasoning = openai_choice_delta_parts(payload)
        if self.log_reasoning and reasoning:
            self.reasoning_parts.append(reasoning)
        if self.merge_reasoning and reasoning:
            content = f"{reasoning}{content}" if content else reasoning
            reasoning = ""
        had_reasoning = bool(reasoning)
        if had_reasoning:
            self.reasoning_idle_streak = 0
        elif self.thinking_open:
            self.reasoning_idle_streak += 1
        if reasoning and not self.log_reasoning and not self._emit_thinking(reasoning):
            return False
        if content:
            if self.thinking_open:
                # Keep thinking open; do not close on early/mixed content deltas.
                self.pending_text.append(content)
            elif not self._emit_text(content):
                return False
        tool_deltas = openai_choice_tool_call_deltas(payload)
        if tool_deltas:
            if not self._flush_pending_text():
                return False
            return self._handle_tool_deltas(tool_deltas)
        # Reasoning idle long enough: flush buffered answer (closes thinking).
        if (
            self.thinking_open
            and self.pending_text
            and self.reasoning_idle_streak >= _REASONING_IDLE_FLUSH
        ):
            return self._flush_pending_text()
        return True

    def finish(self) -> None:
        if not self._flush_pending_text():
            return
        if not self.message_started and not self._emit_text(""):
            return
        if not self._close_block():
            return
        stop_reason = anthropic_stop_reason_from_openai(self.last_finish_reason)
        for event in (
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": {"output_tokens": max(1, self.output_chars // 4 or 1)},
            },
            {"type": "message_stop"},
        ):
            if not self._emit(event):
                return

def pipe_openai_sse_as_anthropic(
    source,
    write: Callable[[bytes], bool],
    *,
    model: str,
    merge_reasoning: bool = False,
    log_reasoning: bool = False,
    on_payload: Callable[[dict[str, Any]], None] | None = None,
    on_reasoning: Callable[[str], None] | None = None,
    read_chunk: Callable[..., bytes] | None = None,
    chunk_size: int = 4 * 1024,
) -> None:
    """Translate OpenAI SSE deltas into Anthropic events."""
    read = read_chunk or (lambda src, size=chunk_size: src.read(size))
    pipe = _OpenAiAnthropicSsePipe(
        write=write,
        model=model,
        merge_reasoning=merge_reasoning,
        log_reasoning=log_reasoning,
        on_payload=on_payload,
    )
    buf = b""
    client_ok = True
    while client_ok:
        chunk = read(source, chunk_size)
        if not chunk:
            payload = parse_openai_sse_data_line(buf)
            if payload is not None:
                client_ok = pipe.handle_payload(payload)
            break
        buf += chunk
        while True:
            idx = buf.find(b"\n")
            if idx < 0:
                break
            line, buf = buf[:idx], buf[idx + 1 :]
            payload = parse_openai_sse_data_line(line)
            if payload is not None:
                client_ok = pipe.handle_payload(payload)
                if not client_ok:
                    break
    if log_reasoning and on_reasoning:
        on_reasoning("".join(pipe.reasoning_parts))
    pipe.finish()

import os
import re
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

import requests

from UnitTest_gen.core.io import log_message
from UnitTest_gen.core.config import AGENTIC_LLM_ROOT, PipelineConfig, get_config

_ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=\s*(.*)$")
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})

# Dummy BYOK key. Must not be a path segment (Copilot redacts the API key everywhere;
# "local" turned ``…/data/local/Foo.kt`` into ``…/data/******/Foo.kt``).
OFFLINE_PROVIDER_API_KEY = "testgen-offline"

@dataclass(frozen=True)
class AgentModelSpec:
    path: str
    alias: str

def local_llm_root() -> Path:
    return Path(get_config().local_llm_root).expanduser().resolve()

def _env_file(root: Path) -> Path:
    """``.env`` wins; the committed ``.env.example`` is a working fallback."""
    candidate = root / ".env"
    return candidate if candidate.is_file() else root / ".env.example"

@lru_cache(maxsize=8)
def _parse_env_file(path: str) -> tuple[tuple[str, str], ...]:
    rows: list[tuple[str, str]] = []
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return ()
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = _ENV_LINE.match(line)
        if match:
            rows.append((match.group(1), match.group(2).strip().strip('"').strip("'")))
    return tuple(rows)

def _env_with_process_overlay(root: Path) -> dict[str, str]:
    values = dict(_parse_env_file(str(_env_file(root))))
    for key in list(values):
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values

def load_local_llm_env() -> dict[str, str]:
    """Vendored ``.env`` values, with real process environment taking precedence."""
    return _env_with_process_overlay(local_llm_root())

def load_vendor_env(agent_env: str | None = None) -> dict[str, str]:
    """Read ``AgenticLLM/.env`` (shared for every CLI)."""
    _ = agent_env
    return _env_with_process_overlay(Path(AGENTIC_LLM_ROOT).expanduser().resolve())

def resolve_agent_model(config: PipelineConfig | None = None) -> AgentModelSpec:
    """Resolve GGUF path + API alias: AgenticLLM/.env with optional CLI/env overrides."""
    cfg = config if config is not None else get_config()
    shared = load_local_llm_env()
    claude_keys = load_vendor_env("claude")
    alias = (
        cfg.agent_model
        or claude_keys.get("ANTHROPIC_MODEL")
        or claude_keys.get("COPILOT_MODEL")
        or shared.get("COPILOT_MODEL")
        or shared.get("MODEL_ALIAS")
        or claude_keys.get("MODEL_ALIAS")
        or ""
    ).strip()
    raw_path = (cfg.agent_model_path or shared.get("MODEL_PATH") or "").strip()
    if not raw_path:
        raise RuntimeError(
            "No model path configured. Set MODEL_PATH in AgenticLLM/.env or pass --agent-model-path."
        )
    resolved = str(Path(raw_path).expanduser().resolve())
    if not Path(resolved).is_file():
        raise RuntimeError(f"Model file not found: {resolved}")
    if not alias:
        raise RuntimeError(
            "No model alias configured. Set ANTHROPIC_MODEL in AgenticLLM/.env or pass --agent-model."
        )
    return AgentModelSpec(path=resolved, alias=alias)

def provider_base_url(env: dict[str, str] | None = None) -> str:
    """OpenAI-compatible base URL for the shared llama stack."""
    config = get_config()
    # Prefer process/config upstream so Claude/Copilot always hit the same ports.
    upstream = os.environ.get("TESTGEN_LLAMA_UPSTREAM", "").strip()
    if upstream:
        return upstream.rstrip("/") + "/v1"
    env = env if env is not None else load_local_llm_env()
    host = env.get("LLAMA_HOST", "127.0.0.1")
    port = str(config.llama_port)
    return f"http://{host}:{port}/v1"

def is_local_url(url: str) -> bool:
    hostname = urlparse(url).hostname or ""
    return hostname in LOCAL_HOSTS

def health_url(env: dict[str, str] | None = None) -> str:
    base = provider_base_url(env).rstrip("/")
    return f"{base.removesuffix('/v1')}/health"

def is_server_healthy(timeout: float = 3.0) -> bool:
    try:
        return requests.get(health_url(), timeout=timeout).status_code == 200
    except requests.RequestException:
        return False

def _run_script(
    name: str,
    *args: str,
    timeout: int,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    script = local_llm_root() / "scripts" / name
    if not script.is_file():
        raise FileNotFoundError(f"Vendored local-LLM script is missing: {script}")
    env = os.environ.copy()
    if extra_env:
        env.update({k: v for k, v in extra_env.items() if v is not None})
    return subprocess.run(
        ["bash", str(script), *args],
        cwd=str(local_llm_root()),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )

def _wait_for_healthy(*, timeout: float, interval: float = 2.0) -> bool:
    """Poll ``/health`` until success or ``timeout`` seconds elapse."""
    import time

    deadline = time.monotonic() + max(1.0, float(timeout))
    while time.monotonic() < deadline:
        if is_server_healthy(timeout=min(5.0, interval)):
            return True
        time.sleep(interval)
    return False

def ensure_coding_server(
    startup_timeout: int | None = None,
    *,
    model_path: str = "",
    model_alias: str = "",
) -> bool:
    """Start the vendored llama-server when the local endpoint is not already healthy."""
    if is_server_healthy():
        log_message(f"✅ Local model server already healthy at {health_url()}", category="success")
        return True

    timeout = startup_timeout or get_config().server_startup_timeout
    overlay: dict[str, str] = {}
    if model_path.strip():
        overlay["MODEL_PATH"] = str(Path(model_path).expanduser().resolve())
    if model_alias.strip():
        overlay["MODEL_ALIAS"] = model_alias.strip()
        overlay["ANTHROPIC_MODEL"] = model_alias.strip()

    log_message(
        f"🚀 Starting vendored local llama-server (model load can take minutes; timeout {timeout}s)...",
        category="info",
    )
    if overlay.get("MODEL_PATH"):
        log_message(f"   model: {overlay['MODEL_PATH']}", category="info")
    if overlay.get("MODEL_ALIAS"):
        log_message(f"   alias: {overlay['MODEL_ALIAS']}", category="info")
    try:
        script_timeout = max(int(timeout) + 180, 720)
        result = _run_script("start-llama-server.sh", timeout=script_timeout, extra_env=overlay or None)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        log_message(f"❌ Could not start local llama-server: {exc}", category="error")
        print(f"❌ Could not start local llama-server: {exc}")
        return False

    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        log_message(
            f"❌ start-llama-server.sh failed (exit {result.returncode}):\n{detail}",
            category="error",
        )
        if detail:
            print(f"❌ start-llama-server.sh failed (exit {result.returncode}):\n{detail}")
        return False

    if _wait_for_healthy(timeout=float(timeout)):
        log_message(f"✅ Local model server healthy at {health_url()}", category="success")
        return True

    log_message(
        f"❌ Local model server not healthy at {health_url()} after {timeout}s.",
        category="error",
    )
    print(
        f"❌ Local model server not healthy at {health_url()} after {timeout}s.\n"
        f"   Check {local_llm_root() / 'logs' / 'llama-server.log'} or start manually:\n"
        f"   {local_llm_root() / 'scripts' / 'start-llama-server.sh'}"
    )
    return False

def stop_coding_server() -> None:
    try:
        _run_script("stop-llama-server.sh", timeout=90)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        log_message(f"⚠️ Could not stop local llama-server cleanly: {exc}", category="warning")

import json
import os
import socket
import sys
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from UnitTest_gen.core.io import log_block, log_message
from UnitTest_gen.core.config import env_flag, get_config

DEFAULT_PROXY_HOST = "127.0.0.1"
DEFAULT_PROXY_PORT = 8081
DEFAULT_UPSTREAM = "http://127.0.0.1:8080"
TEMP_HEADER = "X-Testgen-Temperature"
PRESENCE_HEADER = "X-Testgen-Presence-Penalty"
THINKING_HEADER = "X-Testgen-Enable-Thinking"
BUDGET_HEADER = "X-Testgen-Reasoning-Budget"
_STRIP_HEADERS = {
    TEMP_HEADER.lower(),
    PRESENCE_HEADER.lower(),
    THINKING_HEADER.lower(),
    BUDGET_HEADER.lower(),
}
_CLIENT_GONE = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)
# Small reads so SSE deltas flush to the agent ASAP (urllib can otherwise batch).
_STREAM_CHUNK = 4 * 1024

_PROXY_THREAD: threading.Thread | None = None
_PROXY_SERVER: ThreadingHTTPServer | None = None
_PROXY_LISTEN_PORT: int | None = None
_ACTIVE_SAMPLING_LOCK = threading.Lock()
_ACTIVE_SAMPLING: dict[str, Any] = {
    "temperature": None,
    "presence_penalty": None,
    "enable_thinking": None,
    "reasoning_budget": None,
}
_TIMINGS_LOCK = threading.Lock()
_TIMINGS: dict[str, float | int] = {
    "request_count": 0,
    "prompt_n": 0.0,
    "predicted_n": 0.0,
    "predicted_ms": 0.0,
}

@dataclass(frozen=True)
class SamplingTimings:
    """Aggregated llama.cpp ``timings`` across chat-completion requests in one agent run."""

    request_count: int = 0
    prompt_n: float = 0.0
    predicted_n: float = 0.0
    predicted_ms: float = 0.0

    @property
    def decode_tokens_per_second(self) -> float | None:
        if self.predicted_ms <= 0 or self.predicted_n <= 0:
            return None
        return self.predicted_n / self.predicted_ms * 1000.0

def set_active_sampling(
    *,
    temperature: float | None = None,
    presence_penalty: float | None = None,
    enable_thinking: bool | None = None,
    reasoning_budget: int | None = None,
) -> None:
    """Sampling overrides for Claude Code (no custom headers on Anthropic requests)."""
    with _ACTIVE_SAMPLING_LOCK:
        _ACTIVE_SAMPLING["temperature"] = temperature
        _ACTIVE_SAMPLING["presence_penalty"] = presence_penalty
        _ACTIVE_SAMPLING["enable_thinking"] = enable_thinking
        _ACTIVE_SAMPLING["reasoning_budget"] = reasoning_budget

def get_active_sampling() -> dict[str, Any]:
    with _ACTIVE_SAMPLING_LOCK:
        return dict(_ACTIVE_SAMPLING)

def reset_sampling_timings() -> None:
    """Clear decode/prefill aggregates before a Copilot/Claude/local-chat run."""
    with _TIMINGS_LOCK:
        _TIMINGS["request_count"] = 0
        _TIMINGS["prompt_n"] = 0.0
        _TIMINGS["predicted_n"] = 0.0
        _TIMINGS["predicted_ms"] = 0.0

def record_sampling_timings(payload: dict[str, Any] | None) -> None:
    """Accumulate llama ``timings`` from a final OpenAI chat-completion payload."""
    if not isinstance(payload, dict):
        return
    timings = payload.get("timings")
    if not isinstance(timings, dict):
        return
    prompt_n = timings.get("prompt_n")
    predicted_n = timings.get("predicted_n")
    predicted_ms = timings.get("predicted_ms")
    if prompt_n is None and predicted_n is None and predicted_ms is None:
        return
    with _TIMINGS_LOCK:
        _TIMINGS["request_count"] = int(_TIMINGS["request_count"]) + 1
        if prompt_n is not None:
            _TIMINGS["prompt_n"] = float(_TIMINGS["prompt_n"]) + float(prompt_n)
        if predicted_n is not None:
            _TIMINGS["predicted_n"] = float(_TIMINGS["predicted_n"]) + float(predicted_n)
        if predicted_ms is not None:
            _TIMINGS["predicted_ms"] = float(_TIMINGS["predicted_ms"]) + float(predicted_ms)

def get_sampling_timings() -> SamplingTimings:
    with _TIMINGS_LOCK:
        return SamplingTimings(
            request_count=int(_TIMINGS["request_count"]),
            prompt_n=float(_TIMINGS["prompt_n"]),
            predicted_n=float(_TIMINGS["predicted_n"]),
            predicted_ms=float(_TIMINGS["predicted_ms"]),
        )

def _pid_file() -> Path:
    root = os.environ.get("TESTGEN_LOCAL_LLM_ROOT", "").strip()
    if not root:
        try:
            from UnitTest_gen.core.config import get_config

            root = get_config().local_llm_root
        except Exception:
            root = str(Path(__file__).resolve().parents[1] / "AgenticLLM")
    return Path(root).expanduser().resolve() / "logs" / "sampling-proxy.pid"

def proxy_base_url(host: str = DEFAULT_PROXY_HOST, port: int = DEFAULT_PROXY_PORT) -> str:
    return f"http://{host}:{port}/v1"

def _upstream_root() -> str:
    return os.environ.get("TESTGEN_LLAMA_UPSTREAM", DEFAULT_UPSTREAM).rstrip("/")

def _parse_float_header(headers, header_name: str) -> float | None:
    raw = headers.get(header_name) or headers.get(header_name.lower())
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return float(str(raw).strip())
    except ValueError:
        return None

def _parse_temperature(headers) -> float | None:
    return _parse_float_header(headers, TEMP_HEADER)

def _parse_presence_penalty(headers) -> float | None:
    return _parse_float_header(headers, PRESENCE_HEADER)

def _inject_sampling_fields(
    body: bytes,
    *,
    temperature: float | None = None,
    presence_penalty: float | None = None,
) -> bytes:
    """Override OpenAI-compatible sampling fields used by llama-server."""
    if temperature is None and presence_penalty is None:
        return body

    def _apply(payload: dict[str, Any]) -> None:
        if temperature is not None:
            payload["temperature"] = temperature
        if presence_penalty is not None:
            payload["presence_penalty"] = presence_penalty

    return _mutate_json_body(body, _apply)

def _inject_prompt_cache_fields(body: bytes, backend: str | None = None) -> bytes:
    """Inject backend-gated prompt-cache flags. llama.cpp honours ``cache_prompt``;
    vLLM rejects unknown fields, so only emit for ``llama``.
    """
    name = (backend or "").strip().lower()
    if not name or name in {"off", "0", "false", "none", "vllm", "ollama"}:
        return body
    if name != "llama":
        return body

    def _apply(payload: dict[str, Any]) -> None:
        payload["cache_prompt"] = True

    return _mutate_json_body(body, _apply)

def _inject_enable_thinking(
    body: bytes,
    enable_thinking: bool | None,
    reasoning_budget: int | None = None,
) -> bytes:
    if enable_thinking is None:
        return body

    def _apply(payload: dict[str, Any]) -> None:
        kwargs = payload.get("chat_template_kwargs")
        if not isinstance(kwargs, dict):
            kwargs = {}
        kwargs["enable_thinking"] = enable_thinking
        payload["chat_template_kwargs"] = kwargs
        if not enable_thinking:
            payload["reasoning_budget_tokens"] = 0
            payload["thinking_budget_tokens"] = 0
        elif reasoning_budget is not None:
            payload["reasoning_budget_tokens"] = reasoning_budget
            payload["thinking_budget_tokens"] = reasoning_budget

    return _mutate_json_body(body, _apply)

def _prepare_upstream_body(
    body: bytes,
    *,
    temperature: float | None,
    presence_penalty: float | None,
    enable_thinking: bool | None,
    reasoning_budget: int | None,
) -> bytes:
    body = _inject_sampling_fields(body, temperature=temperature, presence_penalty=presence_penalty)
    body = _inject_enable_thinking(body, enable_thinking, reasoning_budget)
    return _inject_prompt_cache_fields(body, get_config().prompt_cache_backend)

def _parse_enable_thinking(headers) -> bool | None:
    raw = headers.get(THINKING_HEADER) or headers.get(THINKING_HEADER.lower())
    if raw is None or str(raw).strip() == "":
        return None
    value = str(raw).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return None

def _parse_reasoning_budget(headers) -> int | None:
    raw = headers.get(BUDGET_HEADER) or headers.get(BUDGET_HEADER.lower())
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return max(0, int(str(raw).strip()))
    except ValueError:
        return None

def print_reasoning_enabled() -> bool:
    """Whether the agent JSONL tee should print reasoning deltas on the TTY."""
    return env_flag("TESTGEN_PRINT_REASONING", False)

def print_tools_enabled() -> bool:
    """Whether the agent JSONL tee should print tool lifecycle/partial lines on the TTY."""
    return env_flag("TESTGEN_PRINT_TOOLS", False)

def reasoning_pipe_mode(enable_thinking: bool | None) -> str:
    """How to forward upstream completions when thinking may be present.

    Returns ``log`` (strip reasoning for the client + end-of-response log),
    ``passthrough``, or ``plain``.
    Never merges reasoning into ``content`` (avoids double-print with JSONL tees).
    """
    if enable_thinking is not True:
        return "plain"
    if print_reasoning_enabled():
        return "passthrough"
    return "log"

def _log_model_reasoning(text: str) -> None:
    cleaned = (text or "").strip()
    if cleaned:
        log_block("MODEL REASONING", cleaned, console=False)

def _safe_write(wfile, data: bytes) -> bool:
    """Write to the client socket; return False if the client already hung up."""
    try:
        wfile.write(data)
        flush = getattr(wfile, "flush", None)
        if callable(flush):
            flush()
        return True
    except _CLIENT_GONE:
        return False

def _read_some(source, chunk_size: int = _STREAM_CHUNK) -> bytes:
    """Read available bytes without waiting to fill ``chunk_size`` when possible."""
    fp = getattr(source, "fp", None)
    if fp is not None and hasattr(fp, "read1"):
        try:
            return fp.read1(chunk_size)
        except Exception:
            pass
    return source.read(chunk_size)

def _pipe_body(source, wfile, *, chunk_size: int = _STREAM_CHUNK) -> None:
    """Stream upstream bytes to the client without buffering the full body."""
    while True:
        chunk = _read_some(source, chunk_size)
        if not chunk:
            return
        if not _safe_write(wfile, chunk):
            return

def _read_all(source, chunk_size: int = _STREAM_CHUNK) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = _read_some(source, chunk_size)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)

def _pipe_sse_lines(
    source,
    wfile,
    *,
    on_line: Callable[[bytes], bytes | None],
    on_done: Callable[[], None] | None = None,
    chunk_size: int = _STREAM_CHUNK,
) -> None:
    """Stream SSE lines; ``on_line`` returns rewritten bytes or None to pass through."""
    buf = b""

    def _emit(line: bytes) -> bool:
        out = on_line(line)
        if out is None:
            out = line if line.endswith(b"\n") else line + b"\n"
        elif not out.endswith(b"\n"):
            out = out + b"\n"
        return _safe_write(wfile, out)

    while True:
        chunk = _read_some(source, chunk_size)
        if not chunk:
            if buf:
                _emit(buf)
            if on_done:
                on_done()
            return
        buf += chunk
        while True:
            idx = buf.find(b"\n")
            if idx < 0:
                break
            line, buf = buf[:idx], buf[idx + 1 :]
            if not _emit(line):
                if on_done:
                    on_done()
                return

def _pipe_sse_openai(
    source,
    wfile,
    *,
    mode: str,
    chunk_size: int = _STREAM_CHUNK,
) -> None:
    """Stream OpenAI SSE with merge/collect/passthrough reasoning handling."""
    reasoning_parts: list[str] = []
    in_think = False

    def on_line(line: bytes) -> bytes | None:
        nonlocal in_think
        if mode == "merge":
            return rewrite_sse_line_merge_reasoning(line)
        payload = _parse_sse_data_line(line)
        if payload is None:
            return None
        record_sampling_timings(payload)
        if mode == "collect":
            piece = extract_reasoning_from_openai_payload(payload)
            if piece:
                reasoning_parts.append(piece)
        in_think, extracted = _strip_think_from_payload(payload, in_think)
        if mode == "collect" and extracted:
            reasoning_parts.append(extracted)
        return b"data: " + json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def on_done() -> None:
        if mode == "collect":
            _log_model_reasoning("".join(reasoning_parts))

    _pipe_sse_lines(source, wfile, on_line=on_line, on_done=on_done, chunk_size=chunk_size)

def _pipe_json_openai(
    source,
    wfile,
    *,
    mode: str,
    chunk_size: int = _STREAM_CHUNK,
) -> None:
    body = _read_all(source, chunk_size)
    payload = _loads_json_dict(body)
    if payload:
        record_sampling_timings(payload)
    if mode == "merge":
        body = rewrite_json_body_merge_reasoning(body)
    elif mode == "collect":
        if payload:
            _log_model_reasoning(extract_reasoning_from_openai_payload(payload))
        body = rewrite_json_body_strip_reasoning(body)
    _safe_write(wfile, body)

def _pipe_openai_sse_as_anthropic(
    source,
    wfile,
    *,
    model: str,
    merge_reasoning: bool,
    log_reasoning: bool,
    chunk_size: int = _STREAM_CHUNK,
) -> None:
    """Translate OpenAI SSE deltas into Anthropic events (see anthropic_openai_bridge)."""
    pipe_openai_sse_as_anthropic(
        source,
        lambda data: _safe_write(wfile, data),
        model=model,
        merge_reasoning=merge_reasoning,
        log_reasoning=log_reasoning,
        on_payload=record_sampling_timings,
        on_reasoning=_log_model_reasoning,
        read_chunk=_read_some,
        chunk_size=chunk_size,
    )

class _ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self) -> None:
        super().setup()
        try:
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def finish(self) -> None:
        # Client disconnect during flush is common on Copilot retries; keep quiet.
        try:
            super().finish()
        except _CLIENT_GONE:
            pass

    def _reply(
        self,
        status: int,
        content_type: str,
        body: bytes = b"",
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> bool:
        """Send a response; return False when the client already disconnected."""
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            if body:
                self.send_header("Content-Length", str(len(body)))
            for key, value in (extra_headers or {}).items():
                self.send_header(key, value)
            self.send_header("Connection", "close")
            self.end_headers()
            return _safe_write(self.wfile, body) if body else True
        except _CLIENT_GONE:
            return False

    def _reply_upstream_error(self, exc: URLError, *, json_error: bool = False) -> None:
        if json_error:
            body = json.dumps({"type": "error", "error": {"message": str(exc)}}).encode("utf-8")
            self._reply(502, "application/json", body)
        else:
            self._reply(502, "text/plain", f"upstream error: {exc}".encode("utf-8"))

    def _reply_http_error(self, exc: HTTPError, *, read_body: bool = False) -> None:
        content_type = exc.headers.get("Content-Type", "application/json" if read_body else "text/plain")
        if read_body:
            body = exc.read() if hasattr(exc, "read") else b""
            self._reply(exc.code, content_type, body)
            return
        try:
            self.send_response(exc.code)
            self.send_header("Content-Type", content_type)
            self.send_header("Connection", "close")
            self.end_headers()
            _pipe_body(exc, self.wfile)
        except _CLIENT_GONE:
            return

    def _resolve_sampling(self) -> tuple[float | None, float | None, bool | None, int | None]:
        temperature = _parse_temperature(self.headers)
        presence_penalty = _parse_presence_penalty(self.headers)
        enable_thinking = _parse_enable_thinking(self.headers)
        reasoning_budget = _parse_reasoning_budget(self.headers)
        active = get_active_sampling()
        if temperature is None:
            temperature = active.get("temperature")
        if presence_penalty is None:
            presence_penalty = active.get("presence_penalty")
        if enable_thinking is None:
            enable_thinking = active.get("enable_thinking")
        if reasoning_budget is None:
            reasoning_budget = active.get("reasoning_budget")
        return temperature, presence_penalty, enable_thinking, reasoning_budget

    def _forward_openai(self, body: bytes, *, path: str | None = None) -> None:
        upstream = _upstream_root()
        target = f"{upstream}{path or self.path}"
        temperature, presence_penalty, enable_thinking, reasoning_budget = self._resolve_sampling()
        if self.command in {"POST", "PUT", "PATCH"}:
            body = _prepare_upstream_body(
                body,
                temperature=temperature,
                presence_penalty=presence_penalty,
                enable_thinking=enable_thinking,
                reasoning_budget=reasoning_budget,
            )

        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in {"host", "content-length", *_STRIP_HEADERS, "anthropic-version", "x-api-key"}
        }
        headers["Content-Type"] = "application/json"
        if body:
            headers["Content-Length"] = str(len(body))
        request = Request(target, data=body or None, headers=headers, method=self.command)
        pipe_mode = reasoning_pipe_mode(enable_thinking)
        try:
            with urlopen(request, timeout=600) as response:
                content_type = (response.headers.get("Content-Type") or "").lower()
                try:
                    self.send_response(response.status)
                    for key, value in response.headers.items():
                        if key.lower() in {"transfer-encoding", "connection", "content-length"}:
                            continue
                        self.send_header(key, value)
                    self.send_header("Connection", "close")
                    self.end_headers()
                except _CLIENT_GONE:
                    return
                is_sse = "text/event-stream" in content_type
                is_json = "application/json" in content_type
                if pipe_mode == "log":
                    if is_json and not is_sse:
                        _pipe_json_openai(response, self.wfile, mode="collect")
                    else:
                        _pipe_sse_openai(response, self.wfile, mode="collect")
                elif is_json and not is_sse:
                    _pipe_json_openai(response, self.wfile, mode="passthrough")
                else:
                    _pipe_sse_openai(response, self.wfile, mode="passthrough")
        except HTTPError as exc:
            self._reply_http_error(exc)
        except URLError as exc:
            self._reply_upstream_error(exc)
        except _CLIENT_GONE:
            return

    def _forward_anthropic_messages(self, body: bytes) -> None:
        """Translate Anthropic /v1/messages → OpenAI chat/completions → Anthropic response."""
        try:
            anthropic_payload = json.loads(body.decode("utf-8")) if body else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            anthropic_payload = {}
        if not isinstance(anthropic_payload, dict):
            anthropic_payload = {}
        model = str(anthropic_payload.get("model") or "local")
        want_stream = bool(anthropic_payload.get("stream"))
        openai_payload = anthropic_messages_to_openai(anthropic_payload)
        openai_payload["stream"] = want_stream
        temperature, presence_penalty, enable_thinking, reasoning_budget = self._resolve_sampling()
        pipe_mode = reasoning_pipe_mode(enable_thinking)
        log_reasoning = pipe_mode == "log"
        openai_body = json.dumps(openai_payload).encode("utf-8")
        openai_body = _prepare_upstream_body(
            openai_body,
            temperature=temperature,
            presence_penalty=presence_penalty,
            enable_thinking=enable_thinking,
            reasoning_budget=reasoning_budget,
        )

        upstream = _upstream_root()
        target = f"{upstream}/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Content-Length": str(len(openai_body)),
            "Authorization": self.headers.get("Authorization") or "Bearer local",
        }
        request = Request(target, data=openai_body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=600) as response:
                content_type = (response.headers.get("Content-Type") or "").lower()
                if want_stream or "text/event-stream" in content_type:
                    if not self._reply(
                        200,
                        "text/event-stream",
                        extra_headers={"Cache-Control": "no-cache"},
                    ):
                        return
                    _pipe_openai_sse_as_anthropic(
                        response,
                        self.wfile,
                        model=model,
                        merge_reasoning=False,
                        log_reasoning=log_reasoning,
                    )
                    return
                raw = response.read()
                openai_json = _loads_json_dict(raw) or {}
                record_sampling_timings(openai_json)
                if log_reasoning:
                    _log_model_reasoning(extract_reasoning_from_openai_payload(openai_json))
                out_body = json.dumps(openai_completion_to_anthropic(openai_json, model=model)).encode("utf-8")
                self._reply(200, "application/json", out_body)
        except HTTPError as exc:
            self._reply_http_error(exc, read_body=True)
        except URLError as exc:
            self._reply_upstream_error(exc, json_error=True)

    def _forward(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length > 0 else b""
        path = self.path.split("?", 1)[0]
        if self.command == "POST" and path.rstrip("/") == "/v1/messages":
            self._forward_anthropic_messages(body)
            return
        self._forward_openai(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path in {"/health", "/v1/health"}:
            self._reply(200, "application/json", b'{"status":"ok","proxy":true}')
            return
        self._forward()

    def do_POST(self) -> None:  # noqa: N802
        self._forward()

    def do_PUT(self) -> None:  # noqa: N802
        self._forward()

    def do_DELETE(self) -> None:  # noqa: N802
        self._forward()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._forward()

def is_proxy_port_open(host: str = DEFAULT_PROXY_HOST, port: int = DEFAULT_PROXY_PORT) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False

class _SamplingProxyServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address) -> None:  # noqa: ANN001
        exc = sys.exc_info()[1]
        if isinstance(exc, _CLIENT_GONE):
            return
        super().handle_error(request, client_address)

def start_sampling_proxy(
    *,
    host: str = DEFAULT_PROXY_HOST,
    port: int | None = None,
) -> str:
    """Start the in-process proxy if needed; return Copilot provider base URL."""
    global _PROXY_THREAD, _PROXY_SERVER, _PROXY_LISTEN_PORT
    listen_port = int(port or os.environ.get("TESTGEN_SAMPLING_PROXY_PORT", DEFAULT_PROXY_PORT))
    if is_proxy_port_open(host, listen_port):
        return proxy_base_url(host, listen_port)
    if _PROXY_SERVER is not None:
        if _PROXY_LISTEN_PORT == listen_port:
            return proxy_base_url(host, listen_port)
        stop_sampling_proxy()

    server = _SamplingProxyServer((host, listen_port), _ProxyHandler)
    thread = threading.Thread(target=server.serve_forever, name="testgen-sampling-proxy", daemon=True)
    thread.start()
    _PROXY_SERVER = server
    _PROXY_THREAD = thread
    _PROXY_LISTEN_PORT = listen_port
    try:
        pid_path = _pid_file()
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass
    log_message(
        f"🌡️ Sampling proxy listening on {proxy_base_url(host, listen_port)} → {_upstream_root()}",
        category="info",
    )
    return proxy_base_url(host, listen_port)

def stop_sampling_proxy() -> None:
    global _PROXY_THREAD, _PROXY_SERVER, _PROXY_LISTEN_PORT
    if _PROXY_SERVER is None:
        return
    try:
        _PROXY_SERVER.shutdown()
    except Exception:
        pass
    _PROXY_SERVER = None
    _PROXY_THREAD = None
    _PROXY_LISTEN_PORT = None
