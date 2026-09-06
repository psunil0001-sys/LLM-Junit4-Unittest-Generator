"""Pipeline config, TESTGEN_* env, and agent tool-policy constants."""

from __future__ import annotations

# Legacy permission kinds (kept for prompt/tool CSV compatibility).
CODER_ALLOWED_TOOLS = (
    "write,read,"
    "shell(file:*),shell(wc:*),shell(stat:*),shell(head:*)"
)
CODER_DENIED_TOOLS = (
    "shell(curl),shell(wget),shell(git push),shell(npm),shell(pip),"
    "shell(ssh),shell(scp),shell(rm),"
    "shell(bash:*),shell(sh:*),shell(python3:*),shell(perl:*),"
    "shell(xxd:*),shell(od:*),shell(diff:*)"
)

FIX_ALLOWED_TOOLS = CODER_ALLOWED_TOOLS
FIX_DENIED_TOOLS = CODER_DENIED_TOOLS

# ADK FunctionTools (plan, coder, fixer). Policy enforced in-process.
ADK_ALLOWED_TOOLS = (
    "Read",
    "Bash",
    "Edit",
    "Write",
    "Glob",
    "Grep",
)
ADK_DENIED_TOOLS = (
    "WebSearch",
    "WebFetch",
    "NotebookEdit",
)
# Back-compat aliases
CLAUDE_CLI_ALLOWED_TOOLS = ADK_ALLOWED_TOOLS
CLAUDE_CLI_DENIED_TOOLS = ADK_DENIED_TOOLS

# Availability names used to detect plan vs coder calls (legacy view labels).
PLAN_AVAILABLE_TOOLS = "view,edit,create"
PLAN_ALLOWED_TOOLS = "read,write"
PLAN_DENIED_TOOLS = "shell"
CODER_AVAILABLE_TOOLS = "view,edit,create,apply_patch"
FIX_AVAILABLE_TOOLS = CODER_AVAILABLE_TOOLS

# Runtime: plan agent may mutate only *.plan.md when this env is "plan".
AGENT_PHASE_ENV = "TESTGEN_AGENT_PHASE"
AGENT_PHASE_PLAN = "plan"

import argparse
import os
from dataclasses import dataclass, replace
from pathlib import Path

PACKAGE_DIR = str(Path(__file__).resolve().parents[1])
TRUE_VALUES = {"1", "true", "yes", "on"}

DEFAULT_AGENT_ALLOWED_TOOLS = CODER_ALLOWED_TOOLS
DEFAULT_AGENT_DENIED_TOOLS = CODER_DENIED_TOOLS

# Shared llama OpenAI-compatible server for every agent.
SHARED_LLAMA_PORT = 8080
AGENTIC_LLM_ROOT = os.path.join(PACKAGE_DIR, "AgenticLLM")

PIPELINE_PHASES = frozenset({"both", "plan", "coder"})
TEST_MODES = frozenset({"unit", "instrumented", "auto"})
LLM_BACKENDS = frozenset({"llama", "vllm"})

_ACTIVE_CONFIG: "PipelineConfig | None" = None

def normalize_agent_env(value: str | None) -> str:
    """ADK-only; any other label normalizes to adk."""
    _ = value
    return "adk"

def normalize_pipeline_phase(value: str | None) -> str:
    name = (value or "both").strip().lower()
    return name if name in PIPELINE_PHASES else "both"

def normalize_test_mode(value: str | None) -> str:
    name = (value or "auto").strip().lower()
    return name if name in TEST_MODES else "auto"

def normalize_llm_backend(value: str | None) -> str:
    name = (value or "llama").strip().lower()
    return name if name in LLM_BACKENDS else "llama"

def normalize_openai_compatible_root(url: str) -> str:
    """Strip trailing slash and optional ``/v1`` so proxy can append ``/v1/chat/completions``."""
    root = (url or "").strip().rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3].rstrip("/")
    return root

def uses_remote_vllm(config: "PipelineConfig | None" = None) -> bool:
    cfg = config if config is not None else get_config()
    return normalize_llm_backend(cfg.llm_backend) == "vllm" and bool(cfg.vllm_base_url.strip())

def run_unit_tests(config: "PipelineConfig | None" = None) -> bool:
    cfg = config if config is not None else get_config()
    return normalize_test_mode(cfg.test_mode) in {"unit", "auto"}

def run_instrumented_tests(config: "PipelineConfig | None" = None) -> bool:
    cfg = config if config is not None else get_config()
    return normalize_test_mode(cfg.test_mode) in {"instrumented", "auto"}

def run_plan_phase(config: "PipelineConfig | None" = None) -> bool:
    cfg = config if config is not None else get_config()
    return normalize_pipeline_phase(cfg.pipeline_phase) in {"both", "plan"}

def run_coder_phase(config: "PipelineConfig | None" = None) -> bool:
    cfg = config if config is not None else get_config()
    return normalize_pipeline_phase(cfg.pipeline_phase) in {"both", "coder"}

def derive_pipeline_phase(
    *,
    agent_env: str | None,
    plan_agent_env: str | None,
    coder_agent_env: str | None,
    env_fallback: str = "both",
) -> str:
    """Infer phase from legacy CLI flags retained for CLI compatibility.

    Prefer ``--pipeline-phase`` / ``TESTGEN_PIPELINE_PHASE``. Legacy:
    - ``--env`` → both
    - both ``--plan-env`` and ``--coder-env`` → both
    - only ``--plan-env`` → plan
    - only ``--coder-env`` → coder
    """
    if agent_env is not None:
        return "both"
    if plan_agent_env is not None and coder_agent_env is not None:
        return "both"
    if plan_agent_env is not None:
        return "plan"
    if coder_agent_env is not None:
        return "coder"
    return normalize_pipeline_phase(env_fallback)

def agent_env_display_name(_agent_env: str | None = None) -> str:
    """User-facing label for the agent runtime."""
    return "ADK"

def agent_env_log_tag(_agent_env: str | None = None) -> str:
    """Uppercase tag for log section headers."""
    return "ADK"

def export_agent_env_ports(config: "PipelineConfig") -> None:
    """Pin upstream env vars for local llama or remote vLLM."""
    os.environ["TESTGEN_AGENT_ENV"] = "adk"
    os.environ["TESTGEN_PIPELINE_PHASE"] = config.pipeline_phase
    os.environ["TESTGEN_LOCAL_LLM_ROOT"] = config.local_llm_root
    os.environ.pop("TESTGEN_SAMPLING_PROXY_PORT", None)
    os.environ["TESTGEN_PRINT_REASONING"] = "1" if config.print_reasoning_in_terminal else "0"
    os.environ["TESTGEN_PRINT_TOOLS"] = "1" if config.print_tools_in_terminal else "0"
    os.environ["TESTGEN_PLAN_TEMP"] = str(config.plan_temperature)
    os.environ["TESTGEN_GEN_TEMP"] = str(config.gen_temperature)
    os.environ["TESTGEN_FIX_TEMP"] = str(config.fix_temperature)
    os.environ["TESTGEN_PLAN_PRESENCE_PENALTY"] = str(config.plan_presence_penalty)
    os.environ["TESTGEN_GEN_PRESENCE_PENALTY"] = str(config.gen_presence_penalty)
    os.environ["TESTGEN_FIX_PRESENCE_PENALTY"] = str(config.fix_presence_penalty)
    os.environ["TESTGEN_PLAN_ENABLE_THINKING"] = "1" if config.plan_enable_thinking else "0"
    os.environ["TESTGEN_GEN_ENABLE_THINKING"] = "1" if config.gen_enable_thinking else "0"
    os.environ["TESTGEN_FIX_ENABLE_THINKING"] = "1" if config.fix_enable_thinking else "0"
    os.environ["TESTGEN_PLAN_THINKING_BUDGET"] = str(config.plan_thinking_budget)
    os.environ["TESTGEN_GEN_THINKING_BUDGET"] = str(config.gen_thinking_budget)
    os.environ["TESTGEN_FIX_THINKING_BUDGET"] = str(config.fix_thinking_budget)
    backend = normalize_llm_backend(config.llm_backend)
    os.environ["TESTGEN_LLM_BACKEND"] = backend
    if uses_remote_vllm(config):
        os.environ["TESTGEN_LLAMA_UPSTREAM"] = normalize_openai_compatible_root(config.vllm_base_url)
        key = (config.vllm_api_key or "").strip()
        if key:
            os.environ["TESTGEN_VLLM_API_KEY"] = key
        if (config.prompt_cache_backend or "").strip().lower() == "llama":
            os.environ["TESTGEN_PROMPT_CACHE"] = "vllm"
        else:
            os.environ["TESTGEN_PROMPT_CACHE"] = config.prompt_cache_backend
    else:
        os.environ["TESTGEN_LLAMA_UPSTREAM"] = f"http://127.0.0.1:{config.llama_port}"
        os.environ.pop("TESTGEN_VLLM_API_KEY", None)
        os.environ["TESTGEN_PROMPT_CACHE"] = config.prompt_cache_backend

@dataclass(frozen=True)
class PipelineConfig:
    """Resolved runtime settings; environment and CLI values override these defaults."""

    agent_env: str = "adk"
    # both = plan+coder; plan = plan only; coder = load saved plan + code.
    pipeline_phase: str = "both"
    local_llm_root: str = AGENTIC_LLM_ROOT
    llama_port: int = SHARED_LLAMA_PORT
    agent_model: str = ""
    agent_model_path: str = ""
    agent_allowed_tools: str = DEFAULT_AGENT_ALLOWED_TOOLS
    agent_denied_tools: str = DEFAULT_AGENT_DENIED_TOOLS
    agent_timeout_seconds: int = 10800  # 180 min
    agent_stream: bool = True
    auto_start_local_llm: bool = True
    server_startup_timeout: int = 600
    # llama = vendored llama.cpp; vllm = remote OpenAI-compatible endpoint (Cloudflare OK).
    llm_backend: str = "llama"
    vllm_base_url: str = ""
    vllm_api_key: str = ""
    # When False: still print agent title banner; body is "Prompt Processing..." (full prompt in log).
    print_prompts_in_terminal: bool = False
    # When False: model still thinks, but reasoning is log-only (not merged into agent TTY).
    print_reasoning_in_terminal: bool = True
    # When False: TTY shows minimal tool start/done status; full args/partials stay log-only.
    print_tools_in_terminal: bool = True

    gradle_offline: bool = False
    gradle_heartbeat_seconds: int = 30
    agent_heartbeat_seconds: int = 60
    gradle_timeout_seconds: int = 1800

    kover_acceptance_enabled: bool = True
    jacoco_acceptance_enabled: bool = True
    # unit | instrumented | hybrid — hybrid generates both; instrumented verify needs adb.
    test_mode: str = "auto"
    aosp_root: str = "/home/pathipatisunilkumar/AOSP"
    emulator_lunch: str = "sdk_car_x86_64-trunk_staging-userdebug"
    emulator_wipe_data: bool = True
    emulator_args: str = "-skin 1080x1920 -prop qemu.hw.mainkeys=0"
    emulator_boot_timeout_seconds: int = 600
    auto_start_emulator: bool = True
    prompt_context_char_budget: int = 48000  # ~16k tokens @ 3 chars/token; fits slice source + TARGET excerpts.

    # Per-phase temperature + presence_penalty (openai chat completions).
    #
    # Default (recommended):
    #   plan:  temp=0.30
    #   coder: temp=0.60
    #   fix:   temp=0.25
    plan_temperature: float = 0.3
    gen_temperature: float = 0.2
    fix_temperature: float = 0.1
    plan_presence_penalty: float = 0.0
    gen_presence_penalty: float = 0.0
    fix_presence_penalty: float = 0.0
    # Per-phase thinking and budgets are sent in chat_template_kwargs/extra_body
    # for both llama.cpp and vLLM.
    plan_enable_thinking: bool = True
    gen_enable_thinking: bool = True
    fix_enable_thinking: bool = True
    plan_thinking_budget: int = 256  # -1 = unlimited; 0 = disabled; >0 = max tokens to think before plan/coder/fix.
    gen_thinking_budget: int = 256
    fix_thinking_budget: int = 256
    fix_max_attempts: int = 5
    # Extra plan+coder cycles when planned testable lines remain in post-coder Kover (0 = no replan).
    review_replan_max: int = 1
    # llama | vllm | ollama | off — reserved; openai path does not send cache_prompt.
    prompt_cache_backend: str = "off"
    # Process-local RAM file cache (core/file_cache.py).
    file_cache_enabled: bool = True
    file_cache_max_bytes: int = 1073741824   # 1 GiB
    # Same (tool, path, error) repeats abort the CLI session (0 = disabled).
    stuck_repeat_threshold: int = 2

    enable_guardrails: bool = False
    enable_memory_lessons: bool = False

    # Markdown plan artifacts (overwrite on each plan run).
    plans_dir: str = str(Path(PACKAGE_DIR) / "data" / "plans")

    # Console ANSI section colors (codes live in logging_utils; file logs stay plain).
    enable_console_color: bool = True

    semgrep_core_executable: str = ""
    semgrep_cache_dir: str = ""

def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default

def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default

def env_flag(name: str, default: str | bool = "0") -> bool:
    fallback = "1" if default is True else "0" if default is False else default
    return os.environ.get(name, fallback).lower() in TRUE_VALUES

def _env_optional_bool(name: str) -> bool | None:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return None
    return raw in TRUE_VALUES

def _env_str(name: str, default: str = "") -> str:
    """Return env value when set; otherwise dataclass/default fallback."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw

def _env_optional_int(name: str, default: int | None) -> int | None:
    raw = os.environ.get(name)
    if raw is None:
        return default
    if not raw.strip():
        return None
    try:
        return int(raw)
    except ValueError:
        return default

def load_config_from_env() -> PipelineConfig:
    defaults = PipelineConfig()
    agent_env = normalize_agent_env(os.environ.get("TESTGEN_AGENT_ENV", defaults.agent_env))
    pipeline_phase = normalize_pipeline_phase(
        os.environ.get("TESTGEN_PIPELINE_PHASE", defaults.pipeline_phase)
    )
    # Shared LLM lifecycle always lives under AgenticLLM unless overridden.
    default_root = AGENTIC_LLM_ROOT
    return PipelineConfig(
        agent_env=agent_env,
        pipeline_phase=pipeline_phase,
        local_llm_root=os.environ.get("TESTGEN_LOCAL_LLM_ROOT", default_root),
        llama_port=max(1, _env_int("TESTGEN_LLAMA_PORT", defaults.llama_port)),
        agent_model=_env_str("TESTGEN_AGENT_MODEL", defaults.agent_model),
        agent_model_path=_env_str("TESTGEN_AGENT_MODEL_PATH", defaults.agent_model_path),
        agent_allowed_tools=os.environ.get("TESTGEN_AGENT_ALLOWED_TOOLS", defaults.agent_allowed_tools),
        agent_denied_tools=os.environ.get("TESTGEN_AGENT_DENIED_TOOLS", defaults.agent_denied_tools),
        agent_timeout_seconds=max(60, _env_int("TESTGEN_AGENT_TIMEOUT", defaults.agent_timeout_seconds)),
        agent_stream=env_flag("TESTGEN_AGENT_STREAM", defaults.agent_stream),
        auto_start_local_llm=env_flag("TESTGEN_AUTO_START_LOCAL_LLM", defaults.auto_start_local_llm),
        server_startup_timeout=max(30, _env_int("TESTGEN_SERVER_STARTUP_TIMEOUT", defaults.server_startup_timeout)),
        print_prompts_in_terminal=env_flag("TESTGEN_PRINT_PROMPTS", defaults.print_prompts_in_terminal),
        print_reasoning_in_terminal=env_flag(
            "TESTGEN_PRINT_REASONING", defaults.print_reasoning_in_terminal
        ),
        print_tools_in_terminal=env_flag(
            "TESTGEN_PRINT_TOOLS", defaults.print_tools_in_terminal
        ),
        gradle_offline=env_flag("TESTGEN_GRADLE_OFFLINE", defaults.gradle_offline),
        gradle_heartbeat_seconds=max(5, _env_int("TESTGEN_GRADLE_HEARTBEAT_SECONDS", defaults.gradle_heartbeat_seconds)),
        agent_heartbeat_seconds=max(5, _env_int("TESTGEN_AGENT_HEARTBEAT_SECONDS", defaults.agent_heartbeat_seconds)),
        gradle_timeout_seconds=max(120, _env_int("TESTGEN_GRADLE_TIMEOUT", defaults.gradle_timeout_seconds)),
        kover_acceptance_enabled=env_flag("TESTGEN_KOVER_ACCEPTANCE", defaults.kover_acceptance_enabled),
        jacoco_acceptance_enabled=env_flag("TESTGEN_JACOCO_ACCEPTANCE", defaults.jacoco_acceptance_enabled),
        test_mode=normalize_test_mode(os.environ.get("TESTGEN_TEST_MODE", defaults.test_mode)),
        aosp_root=_env_str("TESTGEN_AOSP_ROOT", defaults.aosp_root),
        emulator_lunch=_env_str("TESTGEN_EMULATOR_LUNCH", defaults.emulator_lunch),
        emulator_wipe_data=env_flag("TESTGEN_EMULATOR_WIPE_DATA", defaults.emulator_wipe_data),
        emulator_args=_env_str("TESTGEN_EMULATOR_ARGS", defaults.emulator_args),
        emulator_boot_timeout_seconds=max(
            30,
            _env_int("TESTGEN_EMULATOR_BOOT_TIMEOUT", defaults.emulator_boot_timeout_seconds),
        ),
        auto_start_emulator=env_flag("TESTGEN_AUTO_START_EMULATOR", defaults.auto_start_emulator),
        prompt_context_char_budget=max(
            512,
            _env_int("TESTGEN_PROMPT_CONTEXT_CHAR_BUDGET", defaults.prompt_context_char_budget),
        ),
        gen_temperature=_env_float("TESTGEN_GEN_TEMP", defaults.gen_temperature),
        fix_temperature=_env_float("TESTGEN_FIX_TEMP", defaults.fix_temperature),
        plan_temperature=_env_float("TESTGEN_PLAN_TEMP", defaults.plan_temperature),
        gen_presence_penalty=_env_float("TESTGEN_GEN_PRESENCE_PENALTY", defaults.gen_presence_penalty),
        fix_presence_penalty=_env_float("TESTGEN_FIX_PRESENCE_PENALTY", defaults.fix_presence_penalty),
        plan_presence_penalty=_env_float("TESTGEN_PLAN_PRESENCE_PENALTY", defaults.plan_presence_penalty),
        plan_enable_thinking=env_flag("TESTGEN_PLAN_ENABLE_THINKING", defaults.plan_enable_thinking),
        gen_enable_thinking=env_flag("TESTGEN_GEN_ENABLE_THINKING", defaults.gen_enable_thinking),
        fix_enable_thinking=env_flag("TESTGEN_FIX_ENABLE_THINKING", defaults.fix_enable_thinking),
        plan_thinking_budget=max(0, _env_int("TESTGEN_PLAN_THINKING_BUDGET", defaults.plan_thinking_budget)),
        gen_thinking_budget=max(0, _env_int("TESTGEN_GEN_THINKING_BUDGET", defaults.gen_thinking_budget)),
        fix_thinking_budget=max(0, _env_int("TESTGEN_FIX_THINKING_BUDGET", defaults.fix_thinking_budget)),
        fix_max_attempts=max(0, _env_int("TESTGEN_FIX_MAX_ATTEMPTS", defaults.fix_max_attempts)),
        review_replan_max=max(0, _env_int("TESTGEN_REVIEW_REPLAN_MAX", defaults.review_replan_max)),
        stuck_repeat_threshold=max(0, _env_int("TESTGEN_STUCK_REPEAT_THRESHOLD", defaults.stuck_repeat_threshold)),
        prompt_cache_backend=(
            os.environ.get("TESTGEN_PROMPT_CACHE", defaults.prompt_cache_backend) or "off"
        ).strip().lower()
        or "off",
        file_cache_enabled=env_flag("TESTGEN_FILE_CACHE", defaults.file_cache_enabled),
        file_cache_max_bytes=max(
            0,
            _env_int("TESTGEN_FILE_CACHE_MAX_BYTES", defaults.file_cache_max_bytes),
        ),
        enable_guardrails=env_flag("TESTGEN_ENABLE_GUARDRAILS", defaults.enable_guardrails),
        enable_memory_lessons=env_flag("TESTGEN_ENABLE_MEMORY_LESSONS", defaults.enable_memory_lessons),
        plans_dir=os.environ.get("TESTGEN_PLANS_DIR", defaults.plans_dir),
        enable_console_color=env_flag("TESTGEN_CONSOLE_COLOR", defaults.enable_console_color)
        and not env_flag("NO_COLOR", False)
        and not env_flag("TESTGEN_NO_COLOR", False),
        semgrep_core_executable=os.environ.get("SEMGREP_CORE_EXECUTABLE", ""),
        semgrep_cache_dir=os.environ.get("TESTGEN_SEMGREP_CACHE_DIR", ""),
        llm_backend=normalize_llm_backend(
            os.environ.get("TESTGEN_LLM_BACKEND", defaults.llm_backend)
        ),
        vllm_base_url=os.environ.get("TESTGEN_VLLM_BASE_URL", defaults.vllm_base_url).strip(),
        vllm_api_key=os.environ.get("TESTGEN_VLLM_API_KEY", defaults.vllm_api_key).strip(),
    )

def apply_cli_args(config: PipelineConfig, args: argparse.Namespace) -> PipelineConfig:
    updates: dict = {}
    if getattr(args, "gradle_offline", False):
        updates["gradle_offline"] = True
    if getattr(args, "no_auto_start_local_llm", False):
        updates["auto_start_local_llm"] = False
    if getattr(args, "disable_kover_acceptance", False):
        updates["kover_acceptance_enabled"] = False
    if getattr(args, "disable_jacoco_acceptance", False):
        updates["jacoco_acceptance_enabled"] = False
    if getattr(args, "test_mode", None):
        updates["test_mode"] = normalize_test_mode(args.test_mode)
    if getattr(args, "no_post_validation", False):
        updates["enable_guardrails"] = False
    if getattr(args, "print_prompts", False):
        updates["print_prompts_in_terminal"] = True
    if getattr(args, "print_reasoning", None) is True:
        updates["print_reasoning_in_terminal"] = True
    if getattr(args, "no_print_reasoning", False):
        updates["print_reasoning_in_terminal"] = False
    if getattr(args, "print_tools", None) is True:
        updates["print_tools_in_terminal"] = True
    if getattr(args, "no_print_tools", False):
        updates["print_tools_in_terminal"] = False
    if getattr(args, "stream", None) is True:
        updates["agent_stream"] = True
    if getattr(args, "no_stream", False):
        updates["agent_stream"] = False

    plan_set = getattr(args, "plan_agent_env", None)
    coder_set = getattr(args, "coder_agent_env", None)
    agent_set = getattr(args, "agent_env", None)
    phase_set = getattr(args, "pipeline_phase", None)
    if agent_set:
        updates["agent_env"] = normalize_agent_env(agent_set)
    if phase_set is not None:
        updates["pipeline_phase"] = normalize_pipeline_phase(phase_set)
    elif agent_set is not None or plan_set is not None or coder_set is not None:
        # Legacy --plan-env / --coder-env only select the pipeline phase.
        updates["pipeline_phase"] = derive_pipeline_phase(
            agent_env=agent_set,
            plan_agent_env=plan_set,
            coder_agent_env=coder_set,
            env_fallback=config.pipeline_phase,
        )

    if getattr(args, "local_llm_root", None):
        updates["local_llm_root"] = args.local_llm_root
    if getattr(args, "agent_model", None):
        updates["agent_model"] = args.agent_model
    if getattr(args, "agent_model_path", None):
        updates["agent_model_path"] = args.agent_model_path
    if getattr(args, "agent_timeout", None) is not None:
        updates["agent_timeout_seconds"] = args.agent_timeout
    if getattr(args, "server_startup_timeout", None) is not None:
        updates["server_startup_timeout"] = args.server_startup_timeout
    if getattr(args, "llm_backend", None):
        updates["llm_backend"] = normalize_llm_backend(args.llm_backend)
    if getattr(args, "vllm_base_url", None):
        updates["vllm_base_url"] = str(args.vllm_base_url).strip()
    if getattr(args, "vllm_api_key", None):
        updates["vllm_api_key"] = str(args.vllm_api_key).strip()
    return replace(config, **updates) if updates else config

def set_active_config(config: PipelineConfig) -> None:
    global _ACTIVE_CONFIG
    _ACTIVE_CONFIG = config
    export_agent_env_ports(config)

def get_config() -> PipelineConfig:
    if _ACTIVE_CONFIG is None:
        set_active_config(load_config_from_env())
    return _ACTIVE_CONFIG
