"""ADK agent facade (planner / coder / fixer) and shared result types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from UnitTest_gen.core.config import (
    agent_env_display_name,
    get_config,
)
from UnitTest_gen.core.io import log_block, log_message


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
    """Return ``(endpoint, model)`` without starting any proxy."""
    config = get_config()
    model = config.agent_model or ""
    if not model:
        try:
            from UnitTest_gen.core.llm import resolve_agent_model

            model = resolve_agent_model(config).alias
        except RuntimeError:
            model = ""
    from UnitTest_gen.core.llm import provider_base_url

    endpoint = provider_base_url().removesuffix("/v1")
    return endpoint, model


def coder_tool_surface() -> tuple[str, str]:
    """Return ``(available, denied)`` tool CSVs for preamble logging."""
    return (
        "Read,Bash,Edit,Write,Glob,Grep",
        "WebSearch,WebFetch,NotebookEdit",
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
    from UnitTest_gen.core.io import log_section

    agent = agent_env_display_name()
    batch_note = f" batch={batch_n}/{batch_total}" if batch_total else ""
    log_section(f"CODER SLICE PREAMBLE tag={slice_tag}{batch_note}", category="gen")
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
        log_message(
            f"  Python FAST_VERIFY (fallback if agent did not report BUILD SUCCESSFUL): {gradle_command}",
            category="info",
        )


class AgentOfflineError(RuntimeError):
    """Raised when the agent runtime cannot be proven safe for this pipeline."""


class AgentUnavailableError(RuntimeError):
    """Raised when the model server or ADK runtime is unavailable."""


@dataclass(frozen=True)
class AgentUsage:
    prompt_tokens: float | None = None
    completion_tokens: float | None = None
    cached_tokens: float | None = None
    duration_seconds: float | None = None
    decode_tokens_per_second: float | None = None

    @property
    def tokens_per_second(self) -> float | None:
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
    # Always persist reasoning to the pipeline log; TTY already printed live when enabled.
    if result.reasoning_log.strip():
        log_block("MODEL REASONING", _cap_trace(result.reasoning_log), category="reasoning", console=False)
    if result.tool_log.strip():
        log_block("AGENT TOOL TRACE", _cap_trace(result.tool_log), category="dim", console=False)


def prompt_size_metrics(prompt: str) -> tuple[int, int]:
    chars = len(prompt or "")
    if chars <= 0:
        return 0, 0
    return chars, max(1, int(round(chars / 3.0)))


def log_prompt_metrics(title: str, prompt: str) -> None:
    from UnitTest_gen.core.io import prompt_kind_from_title

    chars, est_tokens = prompt_size_metrics(prompt)
    kind = prompt_kind_from_title(title)
    log_message(
        f"📏 Prompt size ({kind}): {chars:,} chars (~{est_tokens:,} tokens est.)",
        category="info",
    )


def print_prompt_to_terminal(title: str, prompt: str) -> None:
    from UnitTest_gen.core.io import paint, prompt_kind_from_title

    kind = prompt_kind_from_title(title)
    border = "=" * 120
    header = paint(f"\n{border}\n{title}\n{border}", kind)
    if get_config().print_prompts_in_terminal:
        body = paint(prompt or "", "dim")
    else:
        body = paint("Prompt Processing...", "dim")
    footer = paint(border, kind)
    print(f"{header}\n{body}\n{footer}\n", flush=True)


AgentPhase = Literal["plan", "gen", "fix"]


def run_agent(
    prompt: str,
    *,
    cwd: str,
    system_prompt: str | None = None,
    allowed_tools: str | None = None,
    denied_tools: str | None = None,
    available_tools: str | None = None,
    excluded_tools: str | None = None,
    timeout: int | None = None,
    title: str = "ADK PROMPT",
    require_healthy_server: bool = True,
    stream: bool | None = None,
    temperature: float | None = None,
    presence_penalty: float | None = None,
    enable_thinking: bool | None = None,
    reasoning_budget: int | None = None,
    phase: AgentPhase = "gen",
    owning_module_dir: str = "",
) -> AgentResult:
    """Run planner / coder / fixer via Google ADK + openai SDK."""
    _ = (
        allowed_tools,
        denied_tools,
        available_tools,
        excluded_tools,
    )
    from dataclasses import replace

    from UnitTest_gen.core.adk_agents.runner import run_adk_agent
    from UnitTest_gen.core.config import get_config, set_active_config

    if stream is not None:
        set_active_config(replace(get_config(), agent_stream=bool(stream)))

    return run_adk_agent(
        prompt,
        cwd=cwd,
        system_prompt=system_prompt,
        timeout=timeout,
        title=title,
        require_healthy_server=require_healthy_server,
        temperature=temperature,
        presence_penalty=presence_penalty,
        enable_thinking=enable_thinking,
        reasoning_budget=reasoning_budget,
        phase=phase,
        owning_module_dir=owning_module_dir,
    )


# Back-compat name used by older call sites / tests during migration.
run_claude = run_agent
