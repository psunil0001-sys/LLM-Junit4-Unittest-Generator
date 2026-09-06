"""OpenAI-compatible model server helpers (llama.cpp / remote vLLM)."""

from __future__ import annotations

import atexit
import os
import re
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

import requests

from UnitTest_gen.core.config import AGENTIC_LLM_ROOT, PipelineConfig, get_config
from UnitTest_gen.core.io import log_message

_ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=\s*(.*)$")
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})
OFFLINE_PROVIDER_API_KEY = "testgen-offline"

# True when this process started llama-server (stop on interrupt / exit).
_owned_server = False


@dataclass(frozen=True)
class AgentModelSpec:
    path: str
    alias: str


def local_llm_root() -> Path:
    return Path(get_config().local_llm_root).expanduser().resolve()


def _env_file(root: Path) -> Path:
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
    return _env_with_process_overlay(local_llm_root())


def load_vendor_env(agent_env: str | None = None) -> dict[str, str]:
    _ = agent_env
    return _env_with_process_overlay(Path(AGENTIC_LLM_ROOT).expanduser().resolve())


def resolve_agent_model(config: PipelineConfig | None = None) -> AgentModelSpec:
    """Resolve API alias (+ local GGUF path for llama). Remote vLLM needs alias only."""
    from UnitTest_gen.core.config import uses_remote_vllm

    cfg = config if config is not None else get_config()
    shared = load_local_llm_env()
    keys = load_vendor_env()
    alias = (
        cfg.agent_model
        or keys.get("ANTHROPIC_MODEL")
        or keys.get("MODEL_ALIAS")
        or shared.get("MODEL_ALIAS")
        or shared.get("COPILOT_MODEL")
        or ""
    ).strip()
    if not alias:
        raise RuntimeError(
            "No model alias configured. Set MODEL_ALIAS / ANTHROPIC_MODEL in AgenticLLM/.env "
            "or pass --agent-model."
        )
    if uses_remote_vllm(cfg):
        return AgentModelSpec(path="", alias=alias)
    raw_path = (cfg.agent_model_path or shared.get("MODEL_PATH") or "").strip()
    if not raw_path:
        raise RuntimeError(
            "No model path configured. Set MODEL_PATH in AgenticLLM/.env or pass --agent-model-path."
        )
    resolved = str(Path(raw_path).expanduser().resolve())
    if not Path(resolved).is_file():
        raise RuntimeError(f"Model file not found: {resolved}")
    return AgentModelSpec(path=resolved, alias=alias)


def provider_base_url(env: dict[str, str] | None = None) -> str:
    """OpenAI-compatible base URL (…/v1) for llama or remote vLLM."""
    config = get_config()
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


def _upstream_api_key() -> str:
    key = (os.environ.get("TESTGEN_VLLM_API_KEY") or "").strip()
    if key:
        return key
    return (get_config().vllm_api_key or "").strip()


def _upstream_auth_headers() -> dict[str, str]:
    key = _upstream_api_key()
    return {"Authorization": f"Bearer {key}"} if key else {}


def is_server_healthy(timeout: float = 3.0) -> bool:
    """True when local llama ``/health`` or remote ``/v1/models`` responds 200."""
    headers = _upstream_auth_headers() or None
    for url in (health_url(), f"{provider_base_url().rstrip('/')}/models"):
        try:
            if requests.get(url, timeout=timeout, headers=headers).status_code == 200:
                return True
        except requests.RequestException:
            continue
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
    global _owned_server

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
        f"🚀 Starting local llama-server (timeout {timeout}s)...",
        category="info",
    )
    try:
        script_timeout = max(int(timeout) + 180, 720)
        result = _run_script("start-llama-server.sh", timeout=script_timeout, extra_env=overlay or None)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        log_message(f"❌ Could not start local llama-server: {exc}", category="error")
        print(f"❌ Could not start local llama-server: {exc}")
        return False

    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        log_message(f"❌ start-llama-server.sh failed (exit {result.returncode}):\n{detail}", category="error")
        return False

    if _wait_for_healthy(timeout=float(timeout)):
        _owned_server = True
        log_message(f"✅ Local model server healthy at {health_url()}", category="success")
        return True

    log_message(f"❌ Local model server not healthy at {health_url()} after {timeout}s.", category="error")
    print(
        f"❌ Local model server not healthy at {health_url()} after {timeout}s.\n"
        f"   Check {local_llm_root() / 'logs' / 'llama-server.log'}"
    )
    return False


def coding_server_owned() -> bool:
    """True when this process started the local llama-server."""
    return bool(_owned_server)


def stop_coding_server() -> None:
    """Stop llama-server via the vendored script (idempotent)."""
    global _owned_server
    try:
        _run_script("stop-llama-server.sh", timeout=90)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        log_message(f"⚠️ Could not stop local llama-server cleanly: {exc}", category="warning")
    finally:
        _owned_server = False


def stop_owned_coding_server() -> None:
    """Stop llama-server only if this process started it."""
    if not _owned_server:
        return
    log_message("🛑 Stopping local llama-server started by this process…", category="info")
    print("\n🛑 Stopping local llama-server…", flush=True)
    stop_coding_server()


def _atexit_stop_owned_server() -> None:
    if _owned_server:
        try:
            stop_coding_server()
        except Exception:  # noqa: BLE001
            pass


atexit.register(_atexit_stop_owned_server)

def print_reasoning_enabled() -> bool:
    from UnitTest_gen.core.config import get_config

    return bool(get_config().print_reasoning_in_terminal)


def print_tools_enabled() -> bool:
    from UnitTest_gen.core.config import get_config

    return bool(get_config().print_tools_in_terminal)
