"""Run one ADK agent turn (planner / coder / fixer) and collect text + tool traces."""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import time
import uuid
from collections.abc import Coroutine
from typing import Any, Literal, TypeVar

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from UnitTest_gen.core.adk_agents.agents import make_coder, make_fixer, make_planner
from UnitTest_gen.core.adk_agents.model import openai_client_kwargs, resolve_openai_model_name
from UnitTest_gen.core.config import AGENT_PHASE_ENV, AGENT_PHASE_PLAN, get_config
from UnitTest_gen.core.hooks import (
    DISCOVERY_ENV,
    OWNING_MODULE_ENV,
    READ_ONCE_ENV,
    SOURCE_IMPORTS_ENV,
    write_discovery_sidecar,
    write_read_once_sidecar,
    write_source_import_sidecar,
)
from UnitTest_gen.core.io import log_block, log_message
from UnitTest_gen.core.llm import is_server_healthy

AgentPhase = Literal["plan", "gen", "fix"]
_T = TypeVar("_T")


def _run_coro_sync(coro: Coroutine[Any, Any, _T]) -> _T:
    """Run ``coro`` even when the caller already has a running asyncio loop.

    ``kotlin/generator.async_main`` owns the outer loop, so ``asyncio.run()``
    alone raises RuntimeError. Fall back to a dedicated worker thread.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def run_adk_agent(
    prompt: str,
    *,
    cwd: str,
    system_prompt: str | None = None,
    timeout: int | None = None,
    title: str = "ADK PROMPT",
    require_healthy_server: bool = True,
    temperature: float | None = None,
    presence_penalty: float | None = None,
    enable_thinking: bool | None = None,
    reasoning_budget: int | None = None,
    phase: AgentPhase = "gen",
    owning_module_dir: str = "",
) -> "AgentResult":
    """Synchronous entry used by the Kotlin pipeline."""
    from UnitTest_gen.core.agent import (
        AgentResult,
        AgentUnavailableError,
        AgentUsage,
        format_agent_session_line,
        log_agent_traces,
        log_prompt_metrics,
        print_prompt_to_terminal,
    )

    config = get_config()
    if require_healthy_server and not is_server_healthy():
        raise AgentUnavailableError(
            "Model server is not healthy. Start AgenticLLM/scripts/start-llama-server.sh "
            "or check TESTGEN_VLLM_BASE_URL."
        )

    thinking_on, thinking_budget = _resolve_thinking(
        phase=phase,
        config=config,
        enable_thinking=enable_thinking,
        reasoning_budget=reasoning_budget,
    )
    temperature, presence_penalty = _resolve_generation_parameters(
        phase=phase,
        config=config,
        temperature=temperature,
        presence_penalty=presence_penalty,
    )

    limit = timeout or config.agent_timeout_seconds
    log_text = prompt
    if system_prompt and system_prompt.strip():
        log_text = f"SYSTEM (append):\n{system_prompt.strip()}\n\nUSER:\n{prompt}"

    env_backup = _install_sidecars(log_text, cwd=cwd, phase=phase, owning_module_dir=owning_module_dir)
    print_prompt_to_terminal(title, log_text)
    log_block(title, log_text, category="context", console=False)
    log_prompt_metrics(title, log_text)

    model = resolve_openai_model_name(config)
    endpoint = openai_client_kwargs(config)["base_url"]
    agent_label = {"plan": "ADK planner", "gen": "ADK coder", "fix": "ADK fixer"}.get(phase, "ADK agent")
    stream_on = bool(getattr(config, "agent_stream", True))
    log_message(
        format_agent_session_line(
            agent_label=agent_label,
            endpoint=str(endpoint),
            model=model,
            stream=stream_on,
            available="Read,Bash,Edit,Write,Glob,Grep",
            denied="WebSearch,WebFetch,NotebookEdit,bash-network-commands",
            temperature=temperature,
            presence_penalty=presence_penalty,
            enable_thinking=thinking_on,
            reasoning_budget=thinking_budget if thinking_on else 0,
        ),
        category="info",
    )

    started = time.monotonic()
    timed_out = False
    exit_code = 0
    stdout = ""
    stderr = ""
    tool_log_parts: list[str] = []
    reasoning_parts: list[str] = []
    try:
        stdout, tool_log_parts, reasoning_parts = _run_coro_sync(
            _run_async(
                prompt=prompt,
                system_prompt=system_prompt,
                phase=phase,
                temperature=temperature,
                presence_penalty=presence_penalty,
                enable_thinking=thinking_on,
                reasoning_budget=thinking_budget,
                timeout=limit,
            )
        )
    except asyncio.TimeoutError:
        timed_out = True
        exit_code = 124
        stderr = "agent run exceeded its timeout"
    except Exception as exc:  # noqa: BLE001
        exit_code = 1
        stderr = str(exc)
        log_message(f"⚠️ ADK agent error: {exc}", category="warning")
    finally:
        _restore_env(env_backup)

    duration = time.monotonic() - started
    result = AgentResult(
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        duration_seconds=duration,
        timed_out=timed_out,
        usage=AgentUsage(duration_seconds=duration) if duration else None,
        reasoning_log="".join(p for p in reasoning_parts if p).strip(),
        tool_log="\n".join(tool_log_parts),
    )
    log_agent_traces(result)
    log_block("ADK RESPONSE", result.stdout or "(no output)", category="code", console=False)
    if result.timed_out:
        log_message("⏱️ ADK agent timed out.", category="warning")
    elif result.exit_code != 0:
        log_message(f"⚠️ ADK agent exited {result.exit_code}: {result.stderr.strip()[:300]}", category="warning")
    else:
        log_message(
            f"🤖 {agent_label} finished in {result.duration_seconds / 60:.1f} min (exit {result.exit_code}).",
            category="success",
        )
    return result


def _resolve_thinking(
    *,
    phase: AgentPhase,
    config: Any,
    enable_thinking: bool | None,
    reasoning_budget: int | None,
) -> tuple[bool, int]:
    if enable_thinking is None:
        enable_thinking = bool(getattr(config, f"{phase}_enable_thinking", False))
    if reasoning_budget is None:
        reasoning_budget = int(getattr(config, f"{phase}_thinking_budget", 0) or 0)
    if not enable_thinking:
        return False, 0
    return True, max(0, int(reasoning_budget))


def _resolve_generation_parameters(
    *,
    phase: AgentPhase,
    config: Any,
    temperature: float | None,
    presence_penalty: float | None,
) -> tuple[float | None, float | None]:
    if temperature is None:
        temperature = float(getattr(config, f"{phase}_temperature"))
    if presence_penalty is None:
        presence_penalty = float(getattr(config, f"{phase}_presence_penalty"))
    return temperature, presence_penalty


async def _run_async(
    *,
    prompt: str,
    system_prompt: str | None,
    phase: AgentPhase,
    temperature: float | None,
    presence_penalty: float | None,
    enable_thinking: bool,
    reasoning_budget: int,
    timeout: int,
) -> tuple[str, list[str], list[str]]:
    from google.adk.agents.run_config import RunConfig
    from google.adk.agents._streaming_mode import StreamingMode

    agent_kwargs = {
        "temperature": temperature,
        "presence_penalty": presence_penalty,
        "enable_thinking": enable_thinking,
        "reasoning_budget": reasoning_budget,
    }
    if phase == "plan":
        agent = make_planner(**agent_kwargs)
    elif phase == "fix":
        agent = make_fixer(**agent_kwargs)
    else:
        agent = make_coder(**agent_kwargs)

    if system_prompt and system_prompt.strip():
        agent.instruction = f"{agent.instruction}\n\n{system_prompt.strip()}"

    app_name = f"testgen_{phase}"
    session_id = f"s_{uuid.uuid4().hex[:12]}"
    user_id = "pipeline"
    sessions = InMemorySessionService()
    await sessions.create_session(app_name=app_name, user_id=user_id, session_id=session_id)
    runner = Runner(agent=agent, app_name=app_name, session_service=sessions)

    stream_on = bool(getattr(get_config(), "agent_stream", True))
    run_config = RunConfig(
        streaming_mode=StreamingMode.SSE if stream_on else StreamingMode.NONE,
    )

    from UnitTest_gen.core.adk_agents.model import reset_live_tee, set_live_tee

    texts: list[str] = []
    reasoning_log: list[str] = []
    tool_log: list[str] = []
    # Match tool responses back to call args (ADK may omit call id on response).
    pending_by_id: dict[str, tuple[str, dict]] = {}
    pending_by_name: dict[str, list[dict]] = {}
    # Text already printed via OpenAI SSE live-tee (avoid ADK event reprints).
    live_teed = False
    need_newline_before_tool = False

    def _on_live_tee(kind: str, text: str) -> None:
        nonlocal live_teed, need_newline_before_tool
        if not text:
            return
        live_teed = True
        need_newline_before_tool = True
        if kind == "reasoning":
            reasoning_log.append(text)
            _emit_reasoning_chunk(text)
        else:
            texts.append(text)
            _emit_model_chunk(text)

    tee_token = set_live_tee(_on_live_tee if stream_on else None)

    async def _consume() -> None:
        nonlocal need_newline_before_tool
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=types.Content(role="user", parts=[types.Part.from_text(text=prompt)]),
            run_config=run_config,
        ):
            if not event.content or not event.content.parts:
                continue
            partial = bool(getattr(event, "partial", False))
            for part in event.content.parts:
                if part.function_call is not None:
                    # Ignore mid-stream tool partials (model no longer yields them).
                    if partial:
                        continue
                    fc = part.function_call
                    name = str(fc.name or "tool")
                    args = dict(fc.args or {}) if isinstance(fc.args, dict) else {}
                    call_id = str(getattr(fc, "id", None) or "")
                    start_line = _format_tool_start(name, args)
                    tool_log.append(start_line)
                    if need_newline_before_tool:
                        print(flush=True)
                        need_newline_before_tool = False
                    _emit_tool_line(start_line, ok=None, category="tool_start")
                    if call_id:
                        pending_by_id[call_id] = (name, args)
                    pending_by_name.setdefault(name, []).append(args)
                if part.function_response is not None:
                    fr = part.function_response
                    name = str(fr.name or "tool")
                    call_id = str(getattr(fr, "id", None) or "")
                    args: dict = {}
                    if call_id and call_id in pending_by_id:
                        _, args = pending_by_id.pop(call_id)
                    elif pending_by_name.get(name):
                        args = pending_by_name[name].pop(0)
                    body = _tool_response_text(fr.response)
                    ok = _tool_response_ok(body)
                    done_line = _format_tool_done(name, args, ok=ok, body=body)
                    tool_log.append(done_line)
                    _emit_tool_line(done_line, ok=ok)
                if part.text:
                    # Live-tee already printed SSE tokens; only fill gaps from
                    # non-streaming turns or ADK finals that never teed.
                    if live_teed and stream_on:
                        continue
                    is_thought = bool(getattr(part, "thought", None))
                    if partial:
                        continue
                    if is_thought:
                        reasoning_log.append(part.text)
                        _emit_reasoning_chunk(part.text)
                        need_newline_before_tool = True
                    else:
                        texts.append(part.text)
                        _emit_model_chunk(part.text)
                        need_newline_before_tool = True

    try:
        await asyncio.wait_for(_consume(), timeout=max(1, int(timeout)))
    finally:
        reset_live_tee(tee_token)
        if stream_on and (live_teed or texts or reasoning_log):
            print(flush=True)
        await _close_agent_llm_client(agent)
    return "".join(texts).strip(), tool_log, reasoning_log


async def _close_agent_llm_client(agent: Any) -> None:
    """Close AsyncOpenAI before the worker loop exits (avoids 'Event loop is closed')."""
    model = getattr(agent, "model", None) or getattr(agent, "_llm", None)
    client = getattr(model, "_client", None) if model is not None else None
    if client is None:
        return
    close = getattr(client, "close", None)
    if close is None:
        return
    try:
        result = close()
        if asyncio.iscoroutine(result):
            await result
    except Exception:  # noqa: BLE001
        pass


def _emit_reasoning_chunk(text: str) -> None:
    """Print reasoning to TTY when enabled; file logging is via MODEL REASONING block."""
    from UnitTest_gen.core.io import paint
    from UnitTest_gen.core.llm import print_reasoning_enabled

    if not text or not print_reasoning_enabled():
        return
    print(paint(text, "reasoning"), end="", flush=True)


def _emit_model_chunk(text: str) -> None:
    """Print model answer content in bright white on the TTY (live chunks)."""
    from UnitTest_gen.core.io import paint

    if not text:
        return
    print(paint(text, "model"), end="", flush=True)


def _emit_tool_line(line: str, *, ok: bool | None, category: str | None = None) -> None:
    from UnitTest_gen.core.config import get_config
    from UnitTest_gen.core.llm import print_tools_enabled

    if category is None:
        category = "success" if ok is True else "err" if ok is False else "info"
    # Always keep tool lines in the pipeline log; gate TTY separately.
    # Streaming runs always show tools live so the terminal isn't silent during tool turns.
    show_tty = print_tools_enabled() or bool(getattr(get_config(), "agent_stream", True))
    log_message(line, category=category, console=show_tty)


def _format_tool_start(name: str, args: dict) -> str:
    return f"[tool:start] {name} {_format_tool_args(name, args)}"


def _format_tool_done(name: str, args: dict, *, ok: bool, body: str) -> str:
    status = "ok" if ok else "fail"
    detail = _format_tool_args(name, args)
    if ok:
        return f"[tool:done] {name} {status} {detail}".rstrip()
    # Keep failure reason short for the TTY; full body is still in tool_log via detail+snippet.
    snippet = body.strip().replace("\n", " ")
    if len(snippet) > 180:
        snippet = snippet[:177] + "..."
    return f"[tool:done] {name} {status} {detail} error={snippet!r}".rstrip()


def _format_tool_args(name: str, args: dict) -> str:
    if not args:
        return ""
    tool = (name or "").strip()
    if tool == "Read":
        return f"file_path={_q(args.get('file_path') or args.get('path') or '')}"
    if tool == "Write":
        content = args.get("content")
        extra = f" bytes={len(content)}" if isinstance(content, str) else ""
        return f"file_path={_q(args.get('file_path') or args.get('path') or '')}{extra}"
    if tool == "Edit":
        return f"file_path={_q(args.get('file_path') or args.get('path') or '')}"
    if tool == "Bash":
        desc = args.get("description")
        bits = [f"command={_q(args.get('command') or args.get('cmd') or '')}"]
        if desc:
            bits.append(f"description={_q(desc)}")
        return " ".join(bits)
    if tool == "Glob":
        return f"pattern={_q(args.get('pattern') or '')} path={_q(args.get('path') or '')}"
    if tool == "Grep":
        return (
            f"pattern={_q(args.get('pattern') or '')} "
            f"path={_q(args.get('path') or '')} "
            f"glob={_q(args.get('glob') or '')}"
        ).rstrip()
    # Generic fallback: compact key=value pairs (skip huge blobs).
    parts: list[str] = []
    for key, value in args.items():
        text = value if isinstance(value, str) else str(value)
        if len(text) > 200:
            text = text[:197] + "..."
        parts.append(f"{key}={_q(text)}")
    return " ".join(parts)


def _q(value: object) -> str:
    text = str(value or "")
    if not text:
        return '""'
    if any(ch.isspace() for ch in text) or '"' in text:
        return repr(text)
    return text


def _tool_response_text(response: object) -> str:
    if response is None:
        return ""
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        for key in ("result", "output", "error", "message"):
            if key in response and response[key] is not None:
                return str(response[key])
        return str(response)
    return str(response)


def _tool_response_ok(body: str) -> bool:
    text = (body or "").strip()
    if not text:
        return True
    lowered = text.lower()
    return not (
        lowered.startswith("error:")
        or lowered.startswith("error ")
        or "file does not exist" in lowered
        or "no match found" in lowered
        or "denied" in lowered[:80]
    )


def _install_sidecars(
    log_text: str,
    *,
    cwd: str,
    phase: AgentPhase,
    owning_module_dir: str,
) -> dict[str, str | None]:
    keys = (
        SOURCE_IMPORTS_ENV,
        READ_ONCE_ENV,
        DISCOVERY_ENV,
        OWNING_MODULE_ENV,
        AGENT_PHASE_ENV,
        "TESTGEN_AGENT_CWD",
    )
    backup = {k: os.environ.get(k) for k in keys}
    os.environ[SOURCE_IMPORTS_ENV] = str(write_source_import_sidecar(log_text))
    os.environ[READ_ONCE_ENV] = str(write_read_once_sidecar())
    os.environ[DISCOVERY_ENV] = str(write_discovery_sidecar())
    os.environ["TESTGEN_AGENT_CWD"] = str(cwd)
    if owning_module_dir.strip():
        from pathlib import Path

        os.environ[OWNING_MODULE_ENV] = str(Path(owning_module_dir).resolve())
    if phase == "plan":
        os.environ[AGENT_PHASE_ENV] = AGENT_PHASE_PLAN
    else:
        os.environ.pop(AGENT_PHASE_ENV, None)
    return backup


def _restore_env(backup: dict[str, str | None]) -> None:
    for key, value in backup.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
