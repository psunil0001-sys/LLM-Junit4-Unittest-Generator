# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Standalone runtime for codebase_chatbot (no UnitTest_gen imports).
"""Standalone helpers for the UnitTest_gen doc/source RAG chatbot."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shlex
import signal
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import requests
from openai import OpenAI

DEFAULT_LLAMA_CPP_CWD = os.environ.get(
    "LLAMA_CPP_CWD",
    os.environ.get(
        "CHATBOT_SERVER_CWD",
        os.environ.get("TESTGEN_LLAMA_CPP_CWD", str(Path.home() / "llama.cpp")),
    ),
)
DEFAULT_CHATBOT_MODEL_PATH = Path(
    os.environ.get(
        "CHATBOT_MODEL_PATH",
        "/home/pathipatisunilkumar/models/llama-3.2-3b-instruct-q4_k_m.gguf",
    )
)
DEFAULT_CHATBOT_BASE_URL = "http://127.0.0.1:8082/v1"
DEFAULT_CHATBOT_EMBEDDING_BASE_URL = "http://127.0.0.1:8081/v1"
DEFAULT_MODEL_DIR = Path(
    os.environ.get("CHATBOT_MODEL_DIR")
    or os.environ.get("TESTGEN_MODEL_DIR")
    or str(Path.home() / "models")
)
DEFAULT_EMBEDDING_MODEL_PATH = Path(
    os.environ.get(
        "CHATBOT_EMBEDDING_MODEL_PATH",
        os.environ.get(
            "TESTGEN_EMBEDDING_MODEL_PATH",
            str(DEFAULT_MODEL_DIR / "nomic-embed-text-v1.5.Q6_K.gguf"),
        ),
    )
)
DEFAULT_EMBEDDING_MODEL = "nomic-embed-text"
DEFAULT_CHATBOT_MODEL = DEFAULT_CHATBOT_MODEL_PATH.name
DEFAULT_EMBEDDING_MAX_CHARS = int(
    os.environ.get("CHATBOT_EMBEDDING_MAX_CHARS")
    or os.environ.get("TESTGEN_EMBEDDING_MAX_CHARS", "1200")
)

SKIP_DIR_NAMES = frozenset(
    {"build", ".git", ".gradle", "__pycache__", ".cache", "log", "data", ".ruff_cache"}
)
INDEX_EXTENSIONS = frozenset({".md", ".puml", ".py"})
PROMPT_SNIPPET_MAX = 6000
KNOWLEDGE_BUNDLE_VERSION = 1
DEFAULT_KNOWLEDGE_BUNDLE_NAME = "knowledge.bundle.json"
DEFAULT_TOP_K = 8
MAX_HISTORY_MESSAGES = 6
CHAT_SERVER_CTX_SIZE = int(os.environ.get("CHATBOT_CTX_SIZE", "16384"))

SYSTEM_PROMPT = (
    "You are a helpful assistant for the UnitTest_gen Kotlin/Android test generator codebase. "
    "Use only the RETRIEVED CONTEXT and conversation history. "
    "Explain architecture, modules, debugging steps, and code navigation clearly. "
    "Cite file paths like kotlin/generator.py when referencing code or docs. "
    "If the retrieved context is insufficient, say what is missing instead of inventing details."
)

_RUNTIME_LOCK = threading.Lock()
_RUNTIME: "ChatRuntime | None" = None


@dataclass
class ScopedVectorCache:
    path: str

    def exists(self) -> bool:
        return os.path.exists(self.path)

    def load(self) -> dict:
        if not self.exists():
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            return {}

    def save(self, value: dict) -> None:
        cache_dir = os.path.dirname(self.path) or "."
        os.makedirs(cache_dir, exist_ok=True)
        descriptor, temporary_path = tempfile.mkstemp(suffix=".json", dir=cache_dir)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(value, handle, indent=4)
            os.replace(temporary_path, self.path)
        except Exception:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass
            raise

    def delete(self) -> bool:
        if not self.exists():
            return False
        os.remove(self.path)
        return True


@dataclass
class ChatRuntime:
    chat_base_url: str
    chat_model: str
    embedding_base_url: str
    embedding_model: str
    embedding_max_chars: int = DEFAULT_EMBEDDING_MAX_CHARS
    embeddings_available: bool = True
    embeddings_warning_shown: bool = False
    chat_client: OpenAI | None = None
    embedding_client: OpenAI | None = None

    def __post_init__(self) -> None:
        self.chat_client = OpenAI(base_url=self.chat_base_url, api_key="local")
        self.embedding_client = OpenAI(base_url=self.embedding_base_url, api_key="local")

    def embeddings_enabled(self) -> bool:
        return self.embeddings_available

    def is_model_server_available(self) -> bool:
        try:
            response = requests.get(f"{self.chat_base_url.rstrip('/')}/models", timeout=2)
            return response.status_code < 500
        except Exception:
            return False

    def is_embedding_server_available(self) -> bool:
        try:
            response = requests.get(f"{self.embedding_base_url.rstrip('/')}/models", timeout=2)
            return response.status_code < 500
        except Exception:
            return False

    def get_vector(self, text: str):
        if not self.embeddings_available:
            return None

        text_limit = min(len(text), self.embedding_max_chars)
        try:
            while text_limit >= 256:
                try:
                    response = self.embedding_client.embeddings.create(
                        input=[text[:text_limit]],
                        model=self.embedding_model,
                    )
                    return response.data[0].embedding
                except Exception as exc:
                    message = str(exc).lower()
                    if "too large to process" not in message and "batch size" not in message:
                        raise
                    text_limit //= 2

            response = self.embedding_client.embeddings.create(
                input=[text[:256]],
                model=self.embedding_model,
            )
            return response.data[0].embedding
        except Exception as exc:
            self.embeddings_available = False
            if not self.embeddings_warning_shown:
                print(
                    "Embedding collection unavailable; continuing with keyword overlap only. "
                    f"Reason: {exc}"
                )
                self.embeddings_warning_shown = True
            return None

    def sync_embeddings_available(self) -> None:
        self.embeddings_available = embeddings_requested() and self.is_embedding_server_available()


def get_runtime() -> ChatRuntime:
    global _RUNTIME
    with _RUNTIME_LOCK:
        if _RUNTIME is None:
            configure_chatbot_runtime()
        return _RUNTIME  # type: ignore[return-value]


def configure_chatbot_runtime() -> ChatRuntime:
    global _RUNTIME
    base_url = chatbot_base_url()
    model = (
        os.environ.get("CHATBOT_MODEL")
        or os.environ.get("TESTGEN_CHAT_MODEL")
        or DEFAULT_CHATBOT_MODEL
    )
    embedding_model = (
        os.environ.get("CHATBOT_EMBEDDING_MODEL")
        or os.environ.get("TESTGEN_EMBEDDING_MODEL")
        or DEFAULT_EMBEDDING_MODEL
    )
    runtime = ChatRuntime(
        chat_base_url=base_url,
        chat_model=model,
        embedding_base_url=chatbot_embedding_base_url(),
        embedding_model=embedding_model,
        embeddings_available=embeddings_requested(),
    )
    with _RUNTIME_LOCK:
        _RUNTIME = runtime
    return runtime


def sync_embeddings_config() -> None:
    get_runtime().sync_embeddings_available()


def embeddings_enabled() -> bool:
    return get_runtime().embeddings_enabled()


def get_vector(text: str):
    return get_runtime().get_vector(text)


def is_model_server_available() -> bool:
    return get_runtime().is_model_server_available()


def is_embedding_server_available() -> bool:
    return get_runtime().is_embedding_server_available()


def _bundle_root(chatbot_dir: Path | None = None) -> Path:
    """Resolve UnitTest_gen (or standalone bot bundle) root from script location."""
    chatbot_dir = (chatbot_dir or Path(__file__).resolve().parent).resolve()
    if (chatbot_dir / "doc").is_dir():
        return chatbot_dir
    bundle_parent = chatbot_dir.parent
    if (bundle_parent / "doc").is_dir():
        return bundle_parent
    if chatbot_dir.name == "chatbot" and bundle_parent.name == "helper":
        return bundle_parent.parent
    # Flat bot/ copy (scripts + knowledge.bundle.json, no doc/)
    if (chatbot_dir / "codebase_chatbot_lib.py").is_file():
        return chatbot_dir
    return bundle_parent


def unit_test_gen_root() -> Path:
    return _bundle_root()


def repo_root() -> Path:
    chatbot_dir = Path(__file__).resolve().parent
    if chatbot_dir.name == "chatbot" and chatbot_dir.parent.name == "helper":
        return chatbot_dir.parents[2]
    return _bundle_root().parent


def is_full_unit_test_gen_tree(path: Path) -> bool:
    """True when PATH looks like the full generator tree (doc + kotlin/core)."""
    return (path / "doc").is_dir() and (
        (path / "kotlin").is_dir() or (path / "core").is_dir()
    )


def chatbot_base_url() -> str:
    return (
        os.environ.get("CHATBOT_BASE_URL")
        or os.environ.get("TESTGEN_CHAT_BASE_URL")
        or DEFAULT_CHATBOT_BASE_URL
    )


def chatbot_embedding_base_url() -> str:
    return (
        os.environ.get("CHATBOT_EMBEDDING_BASE_URL")
        or os.environ.get("TESTGEN_EMBEDDING_BASE_URL")
        or DEFAULT_CHATBOT_EMBEDDING_BASE_URL
    )


def chatbot_model_path() -> str:
    return os.environ.get("CHATBOT_MODEL_PATH") or str(DEFAULT_CHATBOT_MODEL_PATH)


def chatbot_embedding_model_path() -> str:
    return os.environ.get("CHATBOT_EMBEDDING_MODEL_PATH") or str(DEFAULT_EMBEDDING_MODEL_PATH)


def embeddings_requested() -> bool:
    for name in ("CHATBOT_ENABLE_EMBEDDINGS", "TESTGEN_ENABLE_EMBEDDINGS"):
        value = os.environ.get(name)
        if value is not None:
            return value.lower() in {"1", "true", "yes"}
    return True


def build_chatbot_server_command() -> str:
    custom = (os.environ.get("CHATBOT_SERVER_COMMAND") or "").strip()
    if custom:
        return custom

    parsed = urlparse(chatbot_base_url())
    port = parsed.port or 8082
    host = parsed.hostname or "127.0.0.1"
    model = chatbot_model_path()
    return (
        "./build/bin/llama-server "
        f"-m {shlex.quote(model)} "
        f"--port {port} --host {shlex.quote(host)} "
        f"--ctx-size {CHAT_SERVER_CTX_SIZE} --batch-size 512"
    )


def build_chatbot_embedding_server_command() -> str:
    custom = (os.environ.get("CHATBOT_EMBEDDING_SERVER_COMMAND") or "").strip()
    if not custom:
        custom = (os.environ.get("TESTGEN_EMBEDDING_SERVER_COMMAND") or "").strip()
    if custom:
        return custom

    parsed = urlparse(chatbot_embedding_base_url())
    port = parsed.port or 8081
    host = parsed.hostname or "127.0.0.1"
    model = chatbot_embedding_model_path()
    return (
        "./build/bin/llama-server "
        f"-m {shlex.quote(model)} "
        f"--embedding --port {port} --host {shlex.quote(host)} "
        "--ctx-size 8192 --batch-size 2048 --ubatch-size 2048 "
        "--rope-scaling yarn --rope-freq-scale 0.75"
    )


def chat_server_unavailable_message() -> str:
    return (
        f"Chat server not reachable at {chatbot_base_url()}. "
        "Omit --no-manage-server to auto-start, or run:\n"
        f"  {build_chatbot_server_command()}"
    )


def embedding_server_unavailable_message() -> str:
    return (
        f"Embedding server not reachable at {chatbot_embedding_base_url()}. "
        "Omit --no-manage-server to auto-start, or run:\n"
        f"  {build_chatbot_embedding_server_command()}"
    )


def retrieval_status_line() -> str:
    runtime = get_runtime()
    if runtime.embeddings_enabled():
        return "Retrieval: embedding vectors + keyword overlap."
    env_disabled = os.environ.get("TESTGEN_ENABLE_EMBEDDINGS", "1").lower() not in {
        "1",
        "true",
        "yes",
    }
    if env_disabled:
        return "Retrieval: keyword overlap only (TESTGEN_ENABLE_EMBEDDINGS=0)."
    if not runtime.is_embedding_server_available():
        return (
            "Retrieval: keyword overlap only "
            f"(embedding server unreachable at {runtime.embedding_base_url})."
        )
    return "Retrieval: keyword overlap only (embeddings unavailable during indexing)."


def chat_completion_text(messages: list[dict[str, str]], *, temperature: float = 0.2) -> str:
    from openai import APIConnectionError

    runtime = get_runtime()
    try:
        response = runtime.chat_client.chat.completions.create(
            model=runtime.chat_model,
            messages=messages,
            temperature=temperature,
            stream=False,
        )
    except APIConnectionError as exc:
        raise ConnectionError(chat_server_unavailable_message()) from exc

    message = response.choices[0].message
    return (getattr(message, "content", None) or "").strip()


def file_md5(path: str) -> str:
    digest = hashlib.md5()
    with open(path, "rb") as handle:
        digest.update(handle.read())
    return digest.hexdigest()


def build_vector_index(
    scan_root: str,
    cache: ScopedVectorCache,
    *,
    file_filter: Callable[[str, str], bool],
    item_key_fn: Callable[[str, str], str],
    vector_fn: Callable[[str], object],
    vectors_enabled_fn: Callable[[], bool],
    write_cache: bool = True,
) -> dict:
    index = {}
    cached_values = cache.load()
    cache_updated = False
    if not os.path.exists(scan_root):
        return index

    for root, directories, files in os.walk(scan_root):
        directories[:] = [name for name in directories if name not in {"build", ".gradle", ".git"}]
        for file_name in files:
            path = os.path.join(root, file_name)
            if not file_filter(path, scan_root):
                continue
            with open(path, "r", encoding="utf-8") as handle:
                content = handle.read()
            item_key = item_key_fn(path, content)
            absolute_path = os.path.abspath(path)
            checksum = file_md5(path)
            cached = cached_values.get(absolute_path, cached_values.get(item_key, {}))
            current = cached.get("md5") == checksum
            has_vector = cached.get("vector") is not None
            if current and (has_vector or not vectors_enabled_fn()):
                vector = cached.get("vector")
            else:
                vector = vector_fn(content)
                if vector is not None or not vectors_enabled_fn():
                    cached_values[absolute_path] = {
                        "class_name": item_key,
                        "path": absolute_path,
                        "md5": checksum,
                        "vector": vector,
                    }
                    cache_updated = True
            index_key = item_key
            if index_key in index and os.path.abspath(index[index_key]["path"]) != absolute_path:
                index_key = f"{item_key}@{os.path.relpath(path, scan_root)}"
            index[index_key] = {"class_name": item_key, "path": path, "content": content, "vector": vector}
    if cache_updated and write_cache:
        cache.save(cached_values)
    return index


def tokenize(text: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z][a-z0-9_]{2,}", (text or "").lower()))


def cosine_similarity(v1, v2) -> float:
    if v1 is None or v2 is None:
        return 0.0
    dot_product = sum(a * b for a, b in zip(v1, v2))
    norm_v1 = math.sqrt(sum(a * a for a in v1))
    norm_v2 = math.sqrt(sum(b * b for b in v2))
    if norm_v1 == 0 or norm_v2 == 0:
        return 0.0
    return dot_product / (norm_v1 * norm_v2)


def default_index_root() -> Path:
    return unit_test_gen_root() / "doc"


def resolve_index_root(codebase: str | None = None) -> tuple[Path, bool]:
    """Return scan root and whether md/puml are allowed anywhere under it."""
    if not (codebase or "").strip():
        return default_index_root(), True
    path = Path(codebase).expanduser().resolve()
    if not path.is_dir():
        raise FileNotFoundError(f"Codebase path not found: {path}")
    docs_only = not is_full_unit_test_gen_tree(path)
    return path, docs_only


def chat_cache_path(index_root: Path | None = None) -> Path:
    cache_dir = Path(__file__).resolve().parent / ".cache"
    if index_root is None:
        return cache_dir / "codebase_chat_index.json"
    digest = hashlib.sha256(str(index_root.resolve()).encode("utf-8")).hexdigest()[:8]
    return cache_dir / f"codebase_chat_index_{digest}.json"


def is_indexable_chat_path(path: str, root: str, *, docs_only: bool = False) -> bool:
    normalized = os.path.normpath(os.path.abspath(path))
    root = os.path.abspath(root)
    if not normalized.startswith(root + os.sep) and normalized != root:
        return False
    parts = set(normalized.split(os.sep))
    if parts & SKIP_DIR_NAMES:
        return False
    if "/htmlreport/" in normalized.replace("\\", "/"):
        return False
    rel = os.path.relpath(normalized, root).replace("\\", "/")
    ext = os.path.splitext(normalized)[1].lower()
    if ext not in INDEX_EXTENSIONS:
        return False
    if ext in {".md", ".puml"}:
        return docs_only or rel.startswith("doc/")
    return True


def relative_item_key(path: str, root: str, _content: str) -> str:
    return os.path.relpath(os.path.abspath(path), os.path.abspath(root)).replace("\\", "/")


def prompt_snippet(content: str) -> str:
    text = (content or "").strip()
    if len(text) <= PROMPT_SNIPPET_MAX:
        return text
    return text[:PROMPT_SNIPPET_MAX].rstrip() + "\n... [truncated]"


def export_knowledge_bundle(
    index: dict,
    bundle_path: str | Path,
    *,
    embedding_model: str | None = None,
) -> Path:
    """Write a portable index (text snippets + vectors) with no source tree required."""
    path = Path(bundle_path).expanduser().resolve()
    model = (
        embedding_model
        or os.environ.get("CHATBOT_EMBEDDING_MODEL")
        or os.environ.get("TESTGEN_EMBEDDING_MODEL")
        or DEFAULT_EMBEDDING_MODEL
    )
    items = {}
    for key, item in index.items():
        rel = item.get("class_name") or key
        rel = str(rel).replace("\\", "/")
        items[rel] = {
            "class_name": rel,
            "path": rel,
            "content": item.get("content") or "",
            "vector": item.get("vector"),
            "md5": item.get("md5"),
        }
    payload = {
        "version": KNOWLEDGE_BUNDLE_VERSION,
        "embedding_model": model,
        "item_count": len(items),
        "items": items,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    return path


def load_knowledge_bundle(bundle_path: str | Path) -> dict:
    """Load a portable knowledge bundle into the in-memory index shape."""
    path = Path(bundle_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Knowledge bundle not found: {path}")
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if payload.get("version") != KNOWLEDGE_BUNDLE_VERSION:
        raise ValueError(
            f"Unsupported knowledge bundle version: {payload.get('version')!r} "
            f"(expected {KNOWLEDGE_BUNDLE_VERSION})"
        )
    items = payload.get("items") or {}
    index = {}
    for rel, item in items.items():
        rel_path = str(rel).replace("\\", "/")
        content = prompt_snippet(item.get("content") or "")
        index[rel_path] = {
            "class_name": item.get("class_name") or rel_path,
            "path": item.get("path") or rel_path,
            "content": content,
            "vector": item.get("vector"),
        }
    return index


def build_chat_index(
    root: str,
    cache: ScopedVectorCache,
    *,
    reindex: bool = False,
    docs_only: bool = False,
) -> dict:
    if reindex and cache.exists():
        cache.delete()
    index = build_vector_index(
        root,
        cache,
        file_filter=lambda path, scan_root: is_indexable_chat_path(
            path, scan_root, docs_only=docs_only
        ),
        item_key_fn=lambda path, content: relative_item_key(path, root, content),
        vector_fn=get_vector,
        vectors_enabled_fn=embeddings_enabled,
        write_cache=True,
    )
    for item in index.values():
        item["content"] = prompt_snippet(item.get("content") or "")
    return index


def retrieve_context(query: str, index: dict, top_k: int = DEFAULT_TOP_K) -> list[tuple[str, float, str]]:
    if not index or top_k <= 0:
        return []

    query_terms = tokenize(query)
    query_vector = get_vector(query) if embeddings_enabled() else None
    ranked: list[tuple[str, float, str]] = []
    scan_root = str(unit_test_gen_root())

    for item in index.values():
        rel_path = item.get("class_name") or relative_item_key(
            item["path"],
            scan_root,
            item.get("content", ""),
        )
        text = item.get("content") or ""
        score = 0.0
        if query_vector is not None and item.get("vector") is not None:
            score = cosine_similarity(query_vector, item["vector"])
        if query_terms:
            overlap = len(query_terms & tokenize(f"{rel_path}\n{text}"))
            score = max(score, float(overlap))
        if score > 0:
            ranked.append((rel_path, score, text))

    ranked.sort(key=lambda row: (-row[1], row[0]))
    return ranked[:top_k]


def format_retrieved_context(hits: list[tuple[str, float, str]]) -> str:
    if not hits:
        return "No matching documentation or source files were retrieved."
    blocks = []
    for rel_path, score, text in hits:
        blocks.append(f"### {rel_path} (score={score:.3f})\n```\n{text}\n```")
    return "\n\n".join(blocks)


def chat_once(
    question: str,
    index: dict,
    history: list[dict[str, str]],
    *,
    top_k: int = DEFAULT_TOP_K,
) -> str:
    hits = retrieve_context(question, index, top_k=top_k)
    context_block = format_retrieved_context(hits)
    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.append({"role": "user", "content": f"RETRIEVED CONTEXT:\n\n{context_block}"})
    messages.extend(history[-MAX_HISTORY_MESSAGES:])
    messages.append({"role": "user", "content": question})
    try:
        return chat_completion_text(messages, temperature=0.2)
    except ConnectionError as exc:
        return str(exc)


def _env_flag(name: str, default: str) -> bool:
    return os.environ.get(name, default).lower() in {"1", "true", "yes"}


def _server_log_dir() -> str:
    return os.path.abspath(
        os.path.expanduser(
            os.environ.get("CHATBOT_SERVER_LOG_DIR")
            or os.environ.get("TESTGEN_SERVER_LOG_DIR")
            or str(unit_test_gen_root() / "log")
        )
    )


def _resolve_cwd(path_value: str | None, project_root: str) -> str:
    if not path_value:
        return project_root
    path_value = os.path.expanduser(path_value)
    if os.path.isabs(path_value):
        return os.path.abspath(path_value)
    return os.path.abspath(os.path.join(project_root, path_value))


def _normalize_server_command(command: str) -> str:
    normalized = (command or "").strip()
    while normalized.endswith("\\"):
        normalized = normalized[:-1].rstrip()
    return normalized


@dataclass
class ManagedServer:
    name: str
    command: str
    cwd: str
    process: subprocess.Popen
    script_path: str
    pid_path: str
    log_path: str


class ChatbotServerManager:
    """Starts/stops chat (8082) and embedding (8081) llama-server processes for one session."""

    def __init__(
        self,
        project_root: str,
        *,
        command: str | None = None,
        embedding_command: str | None = None,
        cwd: str | None = None,
        embedding_cwd: str | None = None,
        startup_timeout_seconds: int | None = None,
        use_terminal: bool | None = None,
    ):
        self.project_root = os.path.abspath(os.path.expanduser(project_root))
        self.command = _normalize_server_command(command or build_chatbot_server_command())
        self.embedding_command = _normalize_server_command(
            embedding_command or build_chatbot_embedding_server_command()
        )
        self.cwd = _resolve_cwd(
            cwd
            or os.environ.get("CHATBOT_SERVER_CWD")
            or os.environ.get("TESTGEN_CODING_SERVER_CWD")
            or DEFAULT_LLAMA_CPP_CWD,
            self.project_root,
        )
        self.embedding_cwd = _resolve_cwd(
            embedding_cwd
            or os.environ.get("CHATBOT_EMBEDDING_SERVER_CWD")
            or os.environ.get("TESTGEN_EMBEDDING_SERVER_CWD")
            or self.cwd,
            self.project_root,
        )
        self.startup_timeout_seconds = startup_timeout_seconds or int(
            os.environ.get("CHATBOT_SERVER_STARTUP_TIMEOUT")
            or os.environ.get("TESTGEN_SERVER_STARTUP_TIMEOUT", "120")
        )
        if use_terminal is None:
            self.use_terminal = _env_flag("CHATBOT_SERVER_TERMINAL", os.environ.get("TESTGEN_SERVER_TERMINAL", "1"))
        else:
            self.use_terminal = use_terminal
        self.log_dir = _server_log_dir()
        self.server: ManagedServer | None = None
        self.embedding_server: ManagedServer | None = None

    def start_coding_server(self) -> None:
        if self.server:
            return
        if not self.command.strip():
            raise RuntimeError("Chatbot server command is not configured.")
        self.server = self._start("coding", self.command, self.cwd, "chatbot_server.log")

    def start_embedding_server(self) -> None:
        if self.embedding_server:
            return
        if not self.embedding_command.strip():
            raise RuntimeError("Chatbot embedding server command is not configured.")
        self.embedding_server = self._start(
            "embedding",
            self.embedding_command,
            self.embedding_cwd,
            "chatbot_embedding_server.log",
        )

    def stop_all_started_servers(self) -> None:
        if self.embedding_server:
            self._stop(self.embedding_server, "embedding")
            self.embedding_server = None
        if self.server:
            self._stop(self.server, "chat")
            self.server = None

    def wait_until(self, checker, *, label: str = "coding") -> bool:
        server = self.server if label == "coding" else self.embedding_server
        deadline = time.monotonic() + self.startup_timeout_seconds
        while time.monotonic() < deadline:
            if checker():
                print(f"✅ {label} server is reachable.")
                return True
            if server and server.process.poll() is not None:
                print(
                    f"❌ {label} server process exited before the endpoint became reachable. "
                    f"Log: {server.log_path}"
                )
                self._print_log_tail(server.log_path)
                return False
            time.sleep(2)
        if server:
            print(f"⚠️ {label} server did not become reachable before timeout. Log: {server.log_path}")
            self._print_log_tail(server.log_path)
        return False

    def _start(self, name: str, command: str, cwd: str, log_name: str) -> ManagedServer:
        if not os.path.isdir(cwd):
            raise RuntimeError(f"Chatbot server working directory does not exist: {cwd}")
        os.makedirs(self.log_dir, exist_ok=True)
        log_path = os.path.join(self.log_dir, log_name)

        if self.use_terminal:
            terminal_command = self._terminal_command()
            if not terminal_command:
                print(
                    "⚠️ No terminal emulator/display found. Starting server in background instead. "
                    "Set CHATBOT_SERVER_TERMINAL=0 to make this explicit."
                )
                script_path, pid_path = self._write_script(command, cwd, log_path, mirror_output=False)
                process = subprocess.Popen(["bash", script_path], start_new_session=True)
            else:
                script_path, pid_path = self._write_script(command, cwd, log_path, mirror_output=True)
                process = subprocess.Popen(terminal_command(script_path), start_new_session=True)
        else:
            script_path, pid_path = self._write_script(command, cwd, log_path, mirror_output=False)
            process = subprocess.Popen(["bash", script_path], start_new_session=True)

        print(
            f"🚀 Started {name} server in {'terminal' if self.use_terminal else 'background'}: {command}\n"
            f"   Log: {log_path}"
        )
        return ManagedServer(
            name=name,
            command=command,
            cwd=cwd,
            process=process,
            script_path=script_path,
            pid_path=pid_path,
            log_path=log_path,
        )

    def _write_script(self, command: str, cwd: str, log_path: str, mirror_output: bool) -> tuple[str, str]:
        fd, path = tempfile.mkstemp(prefix="chatbot_server_", suffix=".sh")
        pid_path = path + ".pid"
        with os.fdopen(fd, "w", encoding="utf-8") as script:
            script.write("#!/usr/bin/env bash\n")
            script.write("set -euo pipefail\n")
            script.write(f"cd {shlex.quote(cwd)}\n")
            script.write(f"mkdir -p {shlex.quote(os.path.dirname(log_path))}\n")
            script.write(f"rm -f {shlex.quote(pid_path)}\n")
            if mirror_output:
                script.write(f"exec > >(tee -a {shlex.quote(log_path)}) 2>&1\n")
            else:
                script.write(f"exec >> {shlex.quote(log_path)} 2>&1\n")
            script.write("echo\n")
            script.write("echo '================================================================================'\n")
            script.write("echo '[chatbot] starting server at '$(date -Is)\n")
            script.write(f"echo '[chatbot] cwd: {cwd}'\n")
            script.write("echo '[chatbot] command:'\n")
            script.write(f"printf '%s\\n' {shlex.quote(command.strip())}\n")
            script.write("echo '================================================================================'\n")
            script.write(f"setsid bash -lc {shlex.quote(command.strip())} &\n")
            script.write("server_pid=$!\n")
            script.write(f"printf '%s\\n' \"$server_pid\" > {shlex.quote(pid_path)}\n")
            script.write("wait \"$server_pid\"\n")
        os.chmod(path, 0o700)
        return path, pid_path

    def _terminal_command(self):
        if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
            return None

        custom_terminal = os.environ.get("CHATBOT_TERMINAL_COMMAND") or os.environ.get("TESTGEN_TERMINAL_COMMAND")
        if custom_terminal:
            return lambda script_path: shlex.split(custom_terminal.format(script=script_path))

        candidates = [
            ("gnome-terminal", lambda script_path: ["gnome-terminal", "--wait", "--", "bash", script_path]),
            ("konsole", lambda script_path: ["konsole", "-e", "bash", script_path]),
            ("xfce4-terminal", lambda script_path: ["xfce4-terminal", "-e", f"bash {shlex.quote(script_path)}"]),
            ("xterm", lambda script_path: ["xterm", "-e", "bash", script_path]),
            ("x-terminal-emulator", lambda script_path: ["x-terminal-emulator", "-e", "bash", script_path]),
        ]

        for executable, command_factory in candidates:
            if shutil.which(executable):
                return command_factory

        return None

    def _stop(self, server: ManagedServer, label: str) -> None:
        print(f"🛑 Stopping {label} server started by this run...")
        server_pid = self._read_pid(server.pid_path)
        if server_pid:
            self._terminate_process_group(server_pid, "chatbot llama-server")

        launcher_pid = server.process.pid
        if launcher_pid and server.process.poll() is None:
            self._terminate_process_group(launcher_pid, "chatbot launcher")

        try:
            server.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            if launcher_pid:
                self._kill_process_group(launcher_pid, "chatbot launcher")

        for path in (server.script_path, server.pid_path):
            try:
                os.remove(path)
            except OSError:
                pass

    def _read_pid(self, pid_path: str) -> int | None:
        try:
            with open(pid_path, "r", encoding="utf-8") as pid_file:
                value = pid_file.read().strip()
            return int(value) if value else None
        except (OSError, ValueError):
            return None

    def _terminate_process_group(self, pid: int, label: str) -> None:
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except Exception as exc:
            print(f"Could not terminate {label} process group cleanly: {exc}")
            return

        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                os.killpg(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.25)

        self._kill_process_group(pid, label)

    def _kill_process_group(self, pid: int, label: str) -> None:
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except Exception as exc:
            print(f"Could not kill {label} process group: {exc}")

    def _print_log_tail(self, log_path: str, max_lines: int = 40) -> None:
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as log_file:
                lines = log_file.readlines()
        except OSError:
            return

        tail = "".join(lines[-max_lines:]).strip()
        if tail:
            print("---- server log tail ----")
            print(tail)
            print("-------------------------")


def ensure_managed_servers(
    *,
    manage: bool,
    use_terminal: bool,
    project_root: str,
) -> ChatbotServerManager | None:
    """Auto-start chat and embedding llama-server when managed and unreachable."""
    if not manage:
        return None

    manager = create_chatbot_server_manager(project_root, use_terminal=use_terminal)
    runtime = get_runtime()
    try:
        if not is_model_server_available():
            manager.start_coding_server()
            if not manager.wait_until(is_model_server_available, label="coding"):
                manager.stop_all_started_servers()
                print(
                    f"❌ Auto-started chat server did not become reachable at "
                    f"{runtime.chat_base_url}."
                )
                return None

        if embeddings_requested() and not is_embedding_server_available():
            manager.start_embedding_server()
            if not manager.wait_until(is_embedding_server_available, label="embedding"):
                print(
                    f"⚠️ Embedding server did not become reachable at "
                    f"{runtime.embedding_base_url}; continuing with keyword overlap only."
                )
                print(embedding_server_unavailable_message())
        return manager
    except Exception as exc:
        manager.stop_all_started_servers()
        print(f"❌ Failed to auto-start chatbot servers: {exc}")
        return None


def create_chatbot_server_manager(
    project_root: str,
    *,
    use_terminal: bool = True,
    startup_timeout_seconds: int | None = None,
) -> ChatbotServerManager:
    return ChatbotServerManager(
        project_root,
        use_terminal=use_terminal,
        startup_timeout_seconds=startup_timeout_seconds,
    )


# Backward-compatible alias for tests that patch LocalServerManager.
LocalServerManager = ChatbotServerManager
