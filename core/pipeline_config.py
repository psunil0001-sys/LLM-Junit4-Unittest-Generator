# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Central runtime flow-control settings for the test-generation pipeline.
"""Single source of truth for pipeline flow-control settings (env + CLI)."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, replace
from pathlib import Path

PACKAGE_DIR = str(Path(__file__).resolve().parents[1])
TRUE_VALUES = {"1", "true", "yes", "on"}

_ACTIVE_CONFIG: "PipelineConfig | None" = None


@dataclass(frozen=True)
class PipelineConfig:
    # Model / chat
    chat_base_url: str = "http://127.0.0.1:8080/v1"
    chat_model: str = "qwen3.5 MOE"
    enable_thinking: bool = False
    embedding_base_url: str = "http://127.0.0.1:8081/v1"
    embedding_model: str = "nomic-embed-text"
    embedding_max_chars: int = 2048
    embeddings_available: bool = True
    vector_cache_file: str = ""
    slot_save_path: str = ""
    llama_cache_file: str = "coding_session.bin"
    llama_server_url: str = "http://127.0.0.1:8080/slots/0"
    save_llama_slot_bin: bool = False
    print_prompts_in_terminal: bool = False
    model_reasoning_print: bool = False
    request_sampler_log: bool = True
    request_temperature: float = 0.1
    request_top_p: float = 0.95
    request_min_p: float = 0.0
    request_top_k: int = 20
    request_presence_penalty: float = 0.2
    request_repeat_penalty: float = 1.05
    request_seed: int = 42

    # Stream stuck detector
    model_stuck_detector_enabled: bool = True
    stream_stuck_retry_limit: int = 1
    stream_stuck_min_reasoning_chars: int = 18000
    stream_stuck_min_content_chars: int = 14000
    stream_stuck_no_final_reasoning_chars: int = 32000
    stream_stuck_tail_words: int = 240
    stream_stuck_window_words: int = 40
    stream_stuck_similarity: float = 0.96

    # Server launch
    llama_cpp_cwd: str = ""
    model_dir: str = ""
    coding_model_path: str = ""
    embedding_model_path: str = ""
    server_slot_save_path: str = ""
    server_log_dir: str = ""
    server_startup_timeout: int = 120
    server_use_terminal: bool = True
    terminal_command: str = ""
    auto_start_servers: bool = False
    coding_server_command: str = ""
    embedding_server_command: str = ""

    # Index / vector cache
    index_root: str = ""
    auto_build_vector_cache: bool = True

    # Repair loop
    max_repair_rounds: int = 10
    max_unchanged_error_repair_attempts: int = 2

    # Incremental coverage
    incremental_coverage_enabled: bool = True
    incremental_coverage_rounds: int = 5
    incremental_line_budget: int = 80
    incremental_safe_cap: int = 4
    coverage_buckets: tuple[str, ...] = ("safe", "attemptable")

    # Gradle MCP
    gradle_heartbeat_seconds: int = 30
    gradle_offline: bool = False

    # Semgrep
    semgrep_core_executable: str = ""
    semgrep_cache_dir: str = ""

    # Feature gates
    enable_guardrails: bool = True
    enable_memory_lessons: bool = False
    prompt_slices_enabled: bool = True


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


def env_flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).lower() in TRUE_VALUES


def thinking_enabled_from_env() -> bool:
    return env_flag("TESTGEN_ENABLE_THINKING", "0")


def load_config_from_env() -> PipelineConfig:
    home = Path.home()
    model_dir = Path(os.environ.get("TESTGEN_MODEL_DIR", str(home / "models")))
    return PipelineConfig(
        chat_base_url=os.environ.get("TESTGEN_CHAT_BASE_URL", "http://127.0.0.1:8080/v1"),
        chat_model=os.environ.get("TESTGEN_CHAT_MODEL", "qwen3.5 MOE"),
        enable_thinking=thinking_enabled_from_env(),
        embedding_base_url=os.environ.get(
            "TESTGEN_EMBEDDING_BASE_URL",
            os.environ.get("TESTGEN_OPENAI_BASE_URL", "http://127.0.0.1:8081/v1"),
        ),
        embedding_model=os.environ.get("TESTGEN_EMBEDDING_MODEL", "nomic-embed-text"),
        embedding_max_chars=_env_int("TESTGEN_EMBEDDING_MAX_CHARS", 2048),
        embeddings_available=env_flag("TESTGEN_ENABLE_EMBEDDINGS", "1"),
        vector_cache_file=os.environ.get("TESTGEN_VECTOR_CACHE_FILE", ""),
        slot_save_path=os.environ.get("TESTGEN_LLAMA_SLOT_SAVE_PATH", str(home / ".testgen" / "slots")),
        llama_cache_file=os.environ.get("TESTGEN_LLAMA_CACHE_FILE", "coding_session.bin"),
        llama_server_url=os.environ.get("TESTGEN_LLAMA_SERVER_URL", "http://127.0.0.1:8080/slots/0"),
        save_llama_slot_bin=env_flag("TESTGEN_SAVE_LLAMA_SLOT_BIN", "0"),
        request_sampler_log=env_flag("TESTGEN_LOG_REQUEST_SAMPLING", "1"),
        model_stuck_detector_enabled=env_flag("TESTGEN_ENABLE_STUCK_DETECTOR", "0"),
        llama_cpp_cwd=os.environ.get(
            "LLAMA_CPP_CWD",
            os.environ.get("TESTGEN_LLAMA_CPP_CWD", str(home / "llama.cpp")),
        ),
        model_dir=str(model_dir),
        coding_model_path=os.environ.get(
            "TESTGEN_CODING_MODEL_PATH",
            str(model_dir / "Qwen3.6-35B-A3B-UD-Q8_K_XL.gguf"),
        ),
        embedding_model_path=os.environ.get(
            "TESTGEN_EMBEDDING_MODEL_PATH",
            str(model_dir / "nomic-embed-text-v1.5.Q6_K.gguf"),
        ),
        server_slot_save_path=os.environ.get(
            "TESTGEN_LLAMA_SLOT_SAVE_PATH",
            str(model_dir / "server_slots"),
        ),
        server_log_dir=os.environ.get("TESTGEN_SERVER_LOG_DIR", os.path.join(PACKAGE_DIR, "log")),
        server_startup_timeout=_env_int("TESTGEN_SERVER_STARTUP_TIMEOUT", 120),
        server_use_terminal=env_flag("TESTGEN_SERVER_TERMINAL", "1"),
        terminal_command=os.environ.get("TESTGEN_TERMINAL_COMMAND", ""),
        auto_start_servers=env_flag("TESTGEN_AUTO_START_SERVERS", "0"),
        coding_server_command=os.environ.get("TESTGEN_CODING_SERVER_COMMAND", ""),
        embedding_server_command=os.environ.get("TESTGEN_EMBEDDING_SERVER_COMMAND", ""),
        index_root=os.environ.get("TESTGEN_INDEX_ROOT", ""),
        auto_build_vector_cache=env_flag("TESTGEN_AUTO_BUILD_VECTOR_CACHE", "1"),
        incremental_line_budget=max(1, _env_int("TESTGEN_INCREMENTAL_LINE_BUDGET", 80)),
        incremental_safe_cap=max(1, _env_int("TESTGEN_INCREMENTAL_SAFE_CAP", 2)),
        gradle_heartbeat_seconds=max(5, _env_int("TESTGEN_GRADLE_HEARTBEAT_SECONDS", 30)),
        semgrep_core_executable=os.environ.get("SEMGREP_CORE_EXECUTABLE", ""),
        semgrep_cache_dir=os.environ.get("TESTGEN_SEMGREP_CACHE_DIR", ""),
        enable_guardrails=env_flag("TESTGEN_ENABLE_GUARDRAILS", "1"),
        enable_memory_lessons=env_flag("TESTGEN_ENABLE_MEMORY_LESSONS", "0"),
        prompt_slices_enabled=env_flag("TESTGEN_PROMPT_SLICES", "1"),
    )


def apply_cli_args(config: PipelineConfig, args: argparse.Namespace) -> PipelineConfig:
    updates: dict = {}
    if getattr(args, "enable_stuck_detector", False):
        updates["model_stuck_detector_enabled"] = True
    if getattr(args, "enable_slot_bin_cache", False):
        updates["save_llama_slot_bin"] = True
    elif getattr(args, "disable_slot_bin_cache", False):
        updates["save_llama_slot_bin"] = False
    if getattr(args, "auto_start_servers", False):
        updates["auto_start_servers"] = True
    if getattr(args, "no_server_terminal", False):
        updates["server_use_terminal"] = False
    if getattr(args, "gradle_offline", False):
        updates["gradle_offline"] = True
    if getattr(args, "disable_incremental_coverage", False):
        updates["incremental_coverage_enabled"] = False
    if getattr(args, "incremental_coverage_rounds", None) is not None:
        updates["incremental_coverage_rounds"] = args.incremental_coverage_rounds
    if getattr(args, "coverage_buckets", None):
        updates["coverage_buckets"] = tuple(args.coverage_buckets)
    if getattr(args, "server_startup_timeout", None) is not None:
        updates["server_startup_timeout"] = args.server_startup_timeout
    if getattr(args, "index_root", None):
        updates["index_root"] = args.index_root
    if getattr(args, "coding_server_command", None):
        updates["coding_server_command"] = args.coding_server_command
    if getattr(args, "embedding_server_command", None):
        updates["embedding_server_command"] = args.embedding_server_command
    return replace(config, **updates) if updates else config


def set_active_config(config: PipelineConfig) -> None:
    global _ACTIVE_CONFIG
    _ACTIVE_CONFIG = config


def get_config() -> PipelineConfig:
    if _ACTIVE_CONFIG is None:
        set_active_config(load_config_from_env())
    return _ACTIVE_CONFIG
