"""ADK BaseLlm backed by the official openai SDK (streaming + non-streaming)."""

from __future__ import annotations

import json
import re
from contextvars import ContextVar
from typing import Any, AsyncGenerator, Callable, Optional

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from openai import AsyncOpenAI
from pydantic import Field, PrivateAttr
from typing_extensions import override

from UnitTest_gen.core.config import get_config, uses_remote_vllm
from UnitTest_gen.core.llm import provider_base_url, resolve_agent_model

# Qwen / DeepSeek-style think blocks that may still land in ``content``.
_THINK_TAG_RE = re.compile(
    r"<think>(.*?)</think>|<thinking>(.*?)</thinking>",
    re.DOTALL | re.IGNORECASE,
)

# Live TTY tee: called with (kind, text) as OpenAI SSE tokens arrive.
# kind is "reasoning" or "content". Set by the ADK runner for one agent turn.
LiveTee = Callable[[str, str], None]
_live_tee: ContextVar[LiveTee | None] = ContextVar("adk_live_tee", default=None)


def set_live_tee(callback: LiveTee | None):
    """Install or clear the SSE live-tee callback for the current context."""
    return _live_tee.set(callback)


def reset_live_tee(token) -> None:
    _live_tee.reset(token)


def _tee(kind: str, text: str) -> None:
    if not text:
        return
    cb = _live_tee.get()
    if cb is not None:
        cb(kind, text)


def openai_client_kwargs(config=None) -> dict[str, Any]:
    """Build ``AsyncOpenAI`` / ``OpenAI`` constructor kwargs for llama or vLLM."""
    import httpx

    cfg = config if config is not None else get_config()
    base = provider_base_url().rstrip("/")
    if not base.endswith("/v1"):
        base = base.rstrip("/") + "/v1"
    if uses_remote_vllm(cfg):
        key = (cfg.vllm_api_key or "").strip() or "EMPTY"
    else:
        key = "testgen-offline"
    # OpenAI SDK default read timeout is 600s — too short for local Qwen at ~5 t/s
    # writing a large tool-call (Write) after a long tool history. Match agent timeout.
    read_s = float(max(600, int(getattr(cfg, "agent_timeout_seconds", 10800) or 10800)))
    return {
        "base_url": base,
        "api_key": key,
        "timeout": httpx.Timeout(connect=30.0, read=read_s, write=read_s, pool=60.0),
    }


def resolve_openai_model_name(config=None) -> str:
    return resolve_agent_model(config).alias


class OpenAIChatLlm(BaseLlm):
    """OpenAI-compatible Chat Completions adapter for ADK agents."""

    model: str = Field(default="")
    temperature: Optional[float] = None
    presence_penalty: Optional[float] = None
    enable_thinking: bool = False
    reasoning_budget: int = 0
    _client: Any = PrivateAttr(default=None)

    def model_post_init(self, __context: Any) -> None:
        if not self.model:
            object.__setattr__(self, "model", resolve_openai_model_name())
        if self._client is None:
            object.__setattr__(self, "_client", AsyncOpenAI(**openai_client_kwargs()))

    @override
    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        messages = _contents_to_openai_messages(llm_request)
        tools = _tools_from_request(llm_request)
        kwargs: dict[str, Any] = {
            "model": self.model or llm_request.model or resolve_openai_model_name(),
            "messages": messages,
        }
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self.presence_penalty is not None:
            kwargs["presence_penalty"] = self.presence_penalty
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        thinking_body = _thinking_extra_body(
            enable_thinking=bool(self.enable_thinking),
            reasoning_budget=int(self.reasoning_budget or 0),
        )
        if thinking_body:
            kwargs["extra_body"] = thinking_body

        if stream:
            async for response in _stream_chat_completions(self._client, kwargs):
                yield response
            return

        # --no-stream / TESTGEN_AGENT_STREAM=0: wait for full turn + TTY heartbeat.
        completion = await _create_with_heartbeat(self._client, kwargs)
        yield _completion_to_llm_response(completion)


async def _create_with_heartbeat(client: Any, kwargs: dict[str, Any]) -> Any:
    """Await non-streaming chat.completions.create; emit TTY heartbeats while waiting."""
    import asyncio

    from UnitTest_gen.core.io import log_message

    started = asyncio.get_running_loop().time()
    stop = asyncio.Event()

    async def _heartbeat() -> None:
        while True:
            try:
                await asyncio.wait_for(stop.wait(), timeout=30.0)
                return
            except asyncio.TimeoutError:
                elapsed = int(asyncio.get_running_loop().time() - started)
                log_message(
                    f"⏳ LLM generating (non-streaming)… {elapsed // 60}m{elapsed % 60:02d}s — "
                    "tokens print only when this turn finishes",
                    category="info",
                )

    beat = asyncio.create_task(_heartbeat())
    try:
        return await client.chat.completions.create(**kwargs)
    finally:
        stop.set()
        beat.cancel()
        try:
            await beat
        except asyncio.CancelledError:
            pass


async def _stream_chat_completions(
    client: Any, kwargs: dict[str, Any]
) -> AsyncGenerator[LlmResponse, None]:
    """Yield partial ADK responses as tokens arrive, then one final aggregate."""
    req = dict(kwargs)
    req["stream"] = True
    # Some OpenAI-compatible servers ignore this; harmless when unsupported.
    req["stream_options"] = {"include_usage": True}

    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    reasoning_acc = ""
    content_acc = ""
    # index -> {id, name, args_parts}
    function_calls: dict[int, dict[str, Any]] = {}
    usage_metadata = None
    finish_reason: str | None = None

    stream = await client.chat.completions.create(**req)
    async for chunk in stream:
        if getattr(chunk, "usage", None) is not None:
            u = chunk.usage
            usage_metadata = types.GenerateContentResponseUsageMetadata(
                prompt_token_count=getattr(u, "prompt_tokens", None),
                candidates_token_count=getattr(u, "completion_tokens", None),
                total_token_count=getattr(u, "total_tokens", None),
            )

        choice = chunk.choices[0] if chunk.choices else None
        if choice is None:
            continue
        if choice.finish_reason:
            finish_reason = str(choice.finish_reason)
        delta = choice.delta
        if delta is None:
            continue

        reasoning_delta = _delta_reasoning_text(delta)
        if reasoning_delta:
            reasoning_acc, emit = _coalesce_stream_piece(reasoning_acc, reasoning_delta)
            if emit:
                reasoning_parts.append(emit)
                # Tee at the HTTP chunk — do not wait for ADK event plumbing.
                _tee("reasoning", emit)
                yield LlmResponse(
                    content=types.Content(
                        role="model",
                        parts=[types.Part(text=emit, thought=True)],
                    ),
                    partial=True,
                )

        content_delta = delta.content
        if content_delta:
            content_acc, emit = _coalesce_stream_piece(content_acc, content_delta)
            if emit:
                text_parts.append(emit)
                _tee("content", emit)
                yield LlmResponse(
                    content=types.Content(
                        role="model",
                        parts=[types.Part.from_text(text=emit)],
                    ),
                    partial=True,
                )

        for tool_call in delta.tool_calls or ():
            index = int(getattr(tool_call, "index", None) or 0)
            slot = function_calls.setdefault(
                index, {"id": None, "name": "", "args_parts": []}
            )
            if getattr(tool_call, "id", None):
                slot["id"] = tool_call.id
            fn = getattr(tool_call, "function", None)
            if fn is not None:
                if getattr(fn, "name", None):
                    slot["name"] += fn.name
                if getattr(fn, "arguments", None):
                    slot["args_parts"].append(fn.arguments)
            # Intentionally do NOT yield partial function_call events.
            # Mid-stream tool partials make ADK/runner print [tool:stream] and a
            # newline that cuts the reasoning line; LiteLLM accumulates silently.

    # Final non-partial response (ADK expects this after streaming fragments).
    yield _assemble_streamed_response(
        text_parts=text_parts,
        reasoning_parts=reasoning_parts,
        function_calls=function_calls,
        usage_metadata=usage_metadata,
        finish_reason=finish_reason,
    )


# Thought restarts often arrive as a fresh long span without a leading newline.
_STREAM_RESTART_PREFIXES = (
    "Now I",
    "Now let",
    "Wait",
    "Okay",
    "Ok,",
    "Let me",
    "I need",
    "You're right",
    "You are right",
    "Actually",
    "Hmm",
    "Good.",
    "Sorry",
    "I made an error",
    "I see",
)


def _coalesce_stream_piece(previous: str, incoming: str) -> tuple[str, str]:
    """Normalize delta vs cumulative snapshot vs mid-stream thought restart.

    Returns ``(new_accumulated, text_to_emit)``.
    """
    if not incoming:
        return previous, ""
    if not previous:
        return incoming, incoming
    # Cumulative snapshot: server resends full text so far.
    if incoming.startswith(previous):
        return incoming, incoming[len(previous) :]
    # Shorter rewind / duplicate snapshot.
    if previous.startswith(incoming):
        return previous, ""
    # Model aborted and restarted thinking (common with Qwen); avoid "onAttachNow".
    stripped = incoming.lstrip()
    if any(stripped.startswith(prefix) for prefix in _STREAM_RESTART_PREFIXES):
        piece = incoming if incoming.startswith("\n") else "\n" + incoming
        return previous + piece, piece
    # Long non-continuing span: treat as snapshot replace/restart, not a smash-on delta.
    if len(incoming) >= 48:
        piece = incoming if incoming.startswith("\n") else "\n" + incoming
        return previous + piece, piece
    return previous + incoming, incoming


def _delta_reasoning_text(delta: Any) -> str:
    for attr in ("reasoning_content", "reasoning"):
        value = getattr(delta, attr, None)
        if value:
            return str(value)
    extra = getattr(delta, "model_extra", None)
    if isinstance(extra, dict):
        for key in ("reasoning_content", "reasoning"):
            value = extra.get(key)
            if value:
                return str(value)
    return ""


def _assemble_streamed_response(
    *,
    text_parts: list[str],
    reasoning_parts: list[str],
    function_calls: dict[int, dict[str, Any]],
    usage_metadata: Any,
    finish_reason: str | None,
) -> LlmResponse:
    content = "".join(text_parts)
    reasoning = "".join(reasoning_parts).strip()
    if not reasoning:
        reasoning, content = _extract_think_tags(content)
    else:
        _, content = _extract_think_tags(content)

    parts: list[types.Part] = []
    if reasoning.strip():
        parts.append(types.Part(text=reasoning.strip(), thought=True))
    if content:
        parts.append(types.Part.from_text(text=content))

    for index in sorted(function_calls):
        slot = function_calls[index]
        raw_args = "".join(slot["args_parts"]) or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
        except json.JSONDecodeError:
            args = {"_raw": raw_args}
        if not isinstance(args, dict):
            args = {"value": args}
        part = types.Part.from_function_call(name=slot["name"] or "", args=args)
        call_id = slot.get("id")
        if part.function_call is not None and call_id:
            part.function_call.id = call_id
        parts.append(part)

    if not parts:
        parts = [types.Part.from_text(text="")]

    response = LlmResponse(
        content=types.Content(role="model", parts=parts),
        partial=False,
        usage_metadata=usage_metadata,
    )
    # Best-effort finish_reason mapping (ADK optional).
    if finish_reason and hasattr(response, "finish_reason"):
        try:
            from google.genai.types import FinishReason

            mapped = {
                "stop": FinishReason.STOP,
                "tool_calls": FinishReason.STOP,
                "length": FinishReason.MAX_TOKENS,
            }.get(finish_reason.lower())
            if mapped is not None:
                response.finish_reason = mapped
        except Exception:  # noqa: BLE001
            pass
    return response


def _thinking_extra_body(*, enable_thinking: bool, reasoning_budget: int) -> dict[str, Any]:
    """llama.cpp / vLLM chat-template kwargs for hybrid reasoning models (Qwen3.x)."""
    body: dict[str, Any] = {
        "chat_template_kwargs": {"enable_thinking": bool(enable_thinking)},
    }
    if enable_thinking and reasoning_budget > 0:
        body["reasoning_budget_tokens"] = int(reasoning_budget)
        body["thinking_budget_tokens"] = int(reasoning_budget)
    elif not enable_thinking:
        # Cap thinking when explicitly off (server may still default thinking on).
        body["reasoning_budget_tokens"] = 0
        body["thinking_budget_tokens"] = 0
    return body


def _contents_to_openai_messages(llm_request: LlmRequest) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    system = ""
    if llm_request.config and getattr(llm_request.config, "system_instruction", None):
        system = _content_to_text(llm_request.config.system_instruction)
    if system.strip():
        messages.append({"role": "system", "content": system.strip()})

    for content in llm_request.contents or []:
        role = (content.role or "user").lower()
        if role == "model":
            role = "assistant"
        parts = list(content.parts or [])
        text_bits: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for part in parts:
            if part.function_response is not None:
                fr = part.function_response
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": getattr(fr, "id", None) or fr.name or "tool",
                        "content": _json_dumps(fr.response),
                    }
                )
                continue
            if part.function_call is not None:
                fc = part.function_call
                tool_calls.append(
                    {
                        "id": getattr(fc, "id", None) or f"call_{fc.name}",
                        "type": "function",
                        "function": {
                            "name": fc.name or "",
                            "arguments": _json_dumps(fc.args or {}),
                        },
                    }
                )
                continue
            if part.text:
                text_bits.append(part.text)
        if tool_calls:
            messages.append(
                {
                    "role": "assistant",
                    "content": "".join(text_bits) or None,
                    "tool_calls": tool_calls,
                }
            )
        elif text_bits:
            messages.append({"role": role if role in {"user", "assistant", "system"} else "user", "content": "".join(text_bits)})
    return messages


def _tools_from_request(llm_request: LlmRequest) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    config = llm_request.config
    if not config or not config.tools:
        return out
    for tool in config.tools:
        decls = getattr(tool, "function_declarations", None) or []
        for decl in decls:
            params = decl.parameters_json_schema
            if params is None and decl.parameters is not None:
                params = decl.parameters.model_dump(exclude_none=True) if hasattr(decl.parameters, "model_dump") else decl.parameters
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": decl.name,
                        "description": decl.description or "",
                        "parameters": params or {"type": "object", "properties": {}},
                    },
                }
            )
    return out


def _completion_to_llm_response(completion: Any) -> LlmResponse:
    choice = completion.choices[0] if completion.choices else None
    message = choice.message if choice else None
    parts: list[types.Part] = []
    if message is not None:
        reasoning, answer = _split_reasoning_and_content(message)
        if reasoning.strip():
            parts.append(types.Part(text=reasoning, thought=True))
        if answer:
            parts.append(types.Part.from_text(text=answer))
        for call in message.tool_calls or []:
            fn = call.function
            args: Any = {}
            raw = fn.arguments or "{}"
            try:
                args = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except json.JSONDecodeError:
                args = {"_raw": raw}
            if not isinstance(args, dict):
                args = {"value": args}
            part = types.Part.from_function_call(name=fn.name or "", args=args)
            if part.function_call is not None and call.id:
                part.function_call.id = call.id
            parts.append(part)
    if not parts:
        parts = [types.Part.from_text(text="")]
    usage = None
    if getattr(completion, "usage", None) is not None:
        u = completion.usage
        usage = types.GenerateContentResponseUsageMetadata(
            prompt_token_count=getattr(u, "prompt_tokens", None),
            candidates_token_count=getattr(u, "completion_tokens", None),
            total_token_count=getattr(u, "total_tokens", None),
        )
    return LlmResponse(
        content=types.Content(role="model", parts=parts),
        partial=False,
        usage_metadata=usage,
    )


def _split_reasoning_and_content(message: Any) -> tuple[str, str]:
    """Return ``(reasoning, answer)`` from an OpenAI-compatible chat message."""
    reasoning = _message_reasoning_field(message)
    content = "" if message.content is None else str(message.content)
    if not reasoning:
        tagged, content = _extract_think_tags(content)
        reasoning = tagged
    else:
        # Prefer structured field; strip duplicate think tags from answer if present.
        _, content = _extract_think_tags(content)
    return reasoning.strip(), content


def _message_reasoning_field(message: Any) -> str:
    for attr in ("reasoning_content", "reasoning"):
        value = getattr(message, attr, None)
        if value:
            return str(value)
    extra = getattr(message, "model_extra", None)
    if isinstance(extra, dict):
        for key in ("reasoning_content", "reasoning"):
            value = extra.get(key)
            if value:
                return str(value)
    return ""


def _extract_think_tags(content: str) -> tuple[str, str]:
    if not content:
        return "", ""
    chunks: list[str] = []

    def _collect(match: re.Match[str]) -> str:
        chunks.append(match.group(1) or match.group(2) or "")
        return ""

    cleaned = _THINK_TAG_RE.sub(_collect, content)
    return "\n".join(c.strip() for c in chunks if c.strip()), cleaned.strip()


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = getattr(content, "parts", None) or []
    return "".join(p.text or "" for p in parts if getattr(p, "text", None))


def _json_dumps(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value if value is not None else {}, ensure_ascii=False)
    except TypeError:
        return json.dumps({"result": str(value)}, ensure_ascii=False)
