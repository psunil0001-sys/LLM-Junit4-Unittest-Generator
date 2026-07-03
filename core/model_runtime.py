# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Manages model clients, streaming responses, embeddings, and runtime state.
import difflib
import json
import os
import re
import threading
from pathlib import Path

import requests
from openai import OpenAI

from UnitTest_gen.core.logging_utils import log_block, log_message
from UnitTest_gen.core.pipeline_config import PipelineConfig, get_config
from UnitTest_gen.core.vector_cache import ScopedVectorCache
from UnitTest_gen.core.vector_index import file_md5


PACKAGE_DIR = str(Path(__file__).resolve().parents[1])
CHAT_BASE_URL = os.environ.get("TESTGEN_CHAT_BASE_URL", "http://127.0.0.1:8080/v1")
CHAT_MODEL = os.environ.get("TESTGEN_CHAT_MODEL", "qwen3.5 MOE")
ENABLE_THINKING = False
EMBEDDING_BASE_URL = os.environ.get(
    "TESTGEN_EMBEDDING_BASE_URL",
    os.environ.get("TESTGEN_OPENAI_BASE_URL", "http://127.0.0.1:8081/v1"),
)

EMBEDDING_MODEL = os.environ.get("TESTGEN_EMBEDDING_MODEL", "nomic-embed-text")
EMBEDDING_MAX_CHARS = int(os.environ.get("TESTGEN_EMBEDDING_MAX_CHARS", "2048"))
client = OpenAI(base_url=CHAT_BASE_URL, api_key="local")
embedding_client = OpenAI(base_url=EMBEDDING_BASE_URL, api_key="local")

# Thread-safe global state management
_state_lock = threading.Lock()
EMBEDDINGS_AVAILABLE = os.environ.get("TESTGEN_ENABLE_EMBEDDINGS", "1").lower() in {"1", "true", "yes"}
EMBEDDINGS_WARNING_SHOWN = False
_EXPLICIT_CACHE_FILE = os.environ.get("TESTGEN_VECTOR_CACHE_FILE")
_ACTIVE_VECTOR_CACHE = ScopedVectorCache.for_source("source", PACKAGE_DIR, _EXPLICIT_CACHE_FILE)
CACHE_FILE = _ACTIVE_VECTOR_CACHE.path

SLOT_SAVE_PATH = os.environ.get(
    "TESTGEN_LLAMA_SLOT_SAVE_PATH",
    str(Path.home() / ".testgen" / "slots")
)
LLAMA_CACHE_FILE = os.environ.get("TESTGEN_LLAMA_CACHE_FILE", "coding_session.bin")
LLAMA_SERVER_URL = os.environ.get("TESTGEN_LLAMA_SERVER_URL", "http://127.0.0.1:8080/slots/0")
SAVE_LLAMA_SLOT_BIN = os.environ.get("TESTGEN_SAVE_LLAMA_SLOT_BIN", "0").lower() in {"1", "true", "yes", "on"}
# Set to True to print every generation and repair prompt sent to the model.
PRINT_PROMPTS_IN_TERMINAL = False
MODEL_REASONING_PRINT = False
REASONING_FALLBACK_DETECTOR = None
STREAM_RETRY_INSTRUCTION_BUILDER = None
MODEL_STUCK_DETECTOR_ENABLED = True
STREAM_STUCK_RETRY_LIMIT = 1
STREAM_STUCK_MIN_REASONING_CHARS = 18000
STREAM_STUCK_MIN_CONTENT_CHARS = 14000
STREAM_STUCK_NO_FINAL_REASONING_CHARS = 32000
STREAM_STUCK_TAIL_WORDS = 240
STREAM_STUCK_WINDOW_WORDS = 40
STREAM_STUCK_SIMILARITY = 0.96
REQUEST_SAMPLER_LOG = os.environ.get("TESTGEN_LOG_REQUEST_SAMPLING", "1").lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def thinking_enabled() -> bool:
    return bool(ENABLE_THINKING)


def apply_pipeline_config(config: PipelineConfig) -> None:
    """Sync module-level runtime globals from the active pipeline config."""
    global CHAT_BASE_URL, CHAT_MODEL, ENABLE_THINKING, EMBEDDING_BASE_URL
    global EMBEDDING_MODEL, EMBEDDING_MAX_CHARS, client, embedding_client
    global EMBEDDINGS_AVAILABLE, _EXPLICIT_CACHE_FILE, SLOT_SAVE_PATH, LLAMA_CACHE_FILE
    global LLAMA_SERVER_URL, SAVE_LLAMA_SLOT_BIN, PRINT_PROMPTS_IN_TERMINAL, MODEL_REASONING_PRINT
    global MODEL_STUCK_DETECTOR_ENABLED, STREAM_STUCK_RETRY_LIMIT, STREAM_STUCK_MIN_REASONING_CHARS
    global STREAM_STUCK_MIN_CONTENT_CHARS, STREAM_STUCK_NO_FINAL_REASONING_CHARS
    global STREAM_STUCK_TAIL_WORDS, STREAM_STUCK_WINDOW_WORDS, STREAM_STUCK_SIMILARITY, REQUEST_SAMPLER_LOG

    CHAT_BASE_URL = config.chat_base_url
    CHAT_MODEL = config.chat_model
    ENABLE_THINKING = config.enable_thinking
    EMBEDDING_BASE_URL = config.embedding_base_url
    EMBEDDING_MODEL = config.embedding_model
    EMBEDDING_MAX_CHARS = config.embedding_max_chars
    client = OpenAI(base_url=CHAT_BASE_URL, api_key="local")
    embedding_client = OpenAI(base_url=EMBEDDING_BASE_URL, api_key="local")
    EMBEDDINGS_AVAILABLE = config.embeddings_available
    _EXPLICIT_CACHE_FILE = config.vector_cache_file or None
    SLOT_SAVE_PATH = config.slot_save_path
    LLAMA_CACHE_FILE = config.llama_cache_file
    LLAMA_SERVER_URL = config.llama_server_url
    SAVE_LLAMA_SLOT_BIN = config.save_llama_slot_bin
    PRINT_PROMPTS_IN_TERMINAL = config.print_prompts_in_terminal
    MODEL_REASONING_PRINT = config.model_reasoning_print
    MODEL_STUCK_DETECTOR_ENABLED = config.model_stuck_detector_enabled
    STREAM_STUCK_RETRY_LIMIT = config.stream_stuck_retry_limit
    STREAM_STUCK_MIN_REASONING_CHARS = config.stream_stuck_min_reasoning_chars
    STREAM_STUCK_MIN_CONTENT_CHARS = config.stream_stuck_min_content_chars
    STREAM_STUCK_NO_FINAL_REASONING_CHARS = config.stream_stuck_no_final_reasoning_chars
    STREAM_STUCK_TAIL_WORDS = config.stream_stuck_tail_words
    STREAM_STUCK_WINDOW_WORDS = config.stream_stuck_window_words
    STREAM_STUCK_SIMILARITY = config.stream_stuck_similarity
    REQUEST_SAMPLER_LOG = config.request_sampler_log


def final_output_instruction() -> str:
    if thinking_enabled():
        return "Keep reasoning, analysis, explanations, and strategy in the thinking stream only."
    return "Do not include reasoning, analysis, explanations, or strategy in the final output."


def strip_thinking_fields(extra_body: dict) -> dict:
    sanitized = dict(extra_body or {})
    if thinking_enabled():
        return sanitized

    sanitized.pop("thinking_budget_tokens", None)
    chat_template_kwargs = sanitized.get("chat_template_kwargs")
    if isinstance(chat_template_kwargs, dict):
        chat_template_kwargs = dict(chat_template_kwargs)
        chat_template_kwargs.pop("enable_thinking", None)
        if chat_template_kwargs:
            sanitized["chat_template_kwargs"] = chat_template_kwargs
        else:
            sanitized.pop("chat_template_kwargs", None)
    return sanitized


def set_save_llama_slot_bin(enabled: bool):
    global SAVE_LLAMA_SLOT_BIN
    SAVE_LLAMA_SLOT_BIN = bool(enabled)
    try:
        from dataclasses import replace

        from UnitTest_gen.core.pipeline_config import get_config, set_active_config

        set_active_config(replace(get_config(), save_llama_slot_bin=bool(enabled)))
    except RuntimeError:
        pass


def set_model_stuck_detector_enabled(enabled: bool):
    global MODEL_STUCK_DETECTOR_ENABLED
    MODEL_STUCK_DETECTOR_ENABLED = bool(enabled)
    try:
        from dataclasses import replace

        from UnitTest_gen.core.pipeline_config import get_config, set_active_config

        set_active_config(replace(get_config(), model_stuck_detector_enabled=bool(enabled)))
    except RuntimeError:
        pass


def set_reasoning_fallback_detector(detector):
    global REASONING_FALLBACK_DETECTOR
    REASONING_FALLBACK_DETECTOR = detector


def set_stream_retry_instruction_builder(builder):
    global STREAM_RETRY_INSTRUCTION_BUILDER
    STREAM_RETRY_INSTRUCTION_BUILDER = builder


def configure_vector_cache_scope(source_path: str | None) -> str:
    global CACHE_FILE, _ACTIVE_VECTOR_CACHE
    _ACTIVE_VECTOR_CACHE = ScopedVectorCache.for_source(source_path, PACKAGE_DIR, _EXPLICIT_CACHE_FILE)
    CACHE_FILE = _ACTIVE_VECTOR_CACHE.path
    return CACHE_FILE


def get_vector_cache_file() -> str:
    return CACHE_FILE


def get_vector_cache() -> ScopedVectorCache:
    return _ACTIVE_VECTOR_CACHE






def get_file_md5(file_path):
    return file_md5(file_path)


def load_cache():
    cache = _ACTIVE_VECTOR_CACHE.load()
    if cache:
        print(f"Loaded project vector cache from {_ACTIVE_VECTOR_CACHE.path} ({len(cache)} entries).")
    return cache


def save_cache(cache):
    """Safely save cache with atomic file operations to prevent corruption."""
    try:
        _ACTIVE_VECTOR_CACHE.save(cache)
        print(f"Saved project vector cache to {_ACTIVE_VECTOR_CACHE.path} ({len(cache)} entries).")
    except Exception as e:
        print(f"Failed to save tracking cache: {str(e)}")


def embeddings_enabled():
    return EMBEDDINGS_AVAILABLE


def get_vector(text):
    global EMBEDDINGS_AVAILABLE, EMBEDDINGS_WARNING_SHOWN

    with _state_lock:
        if not EMBEDDINGS_AVAILABLE:
            return None

    text_limit = min(len(text), EMBEDDING_MAX_CHARS)
    try:
        while text_limit >= 256:
            try:
                response = embedding_client.embeddings.create(
                    input=[text[:text_limit]],
                    model=EMBEDDING_MODEL,
                )
                return response.data[0].embedding
            except Exception as exc:
                message = str(exc).lower()
                if "too large to process" not in message and "batch size" not in message:
                    raise
                text_limit //= 2

        response = embedding_client.embeddings.create(
            input=[text[:256]],
            model=EMBEDDING_MODEL,
        )
        return response.data[0].embedding
    except Exception as e:
        with _state_lock:
            EMBEDDINGS_AVAILABLE = False
            if not EMBEDDINGS_WARNING_SHOWN:
                print(
                    "Embedding collection unavailable; continuing with structural dependency context only. "
                    f"Reason: {str(e)}"
                )
                EMBEDDINGS_WARNING_SHOWN = True
        return None


def is_model_server_available() -> bool:
    try:
        response = requests.get(f"{CHAT_BASE_URL.rstrip('/')}/models", timeout=2)
        return response.status_code < 500
    except Exception:
        return False


def is_embedding_server_available() -> bool:
    try:
        response = requests.get(f"{EMBEDDING_BASE_URL.rstrip('/')}/models", timeout=2)
        return response.status_code < 500
    except Exception:
        return False


def load_llama_kv_cache():
    if not SAVE_LLAMA_SLOT_BIN:
        print("Skipping llama-server binary KV cache restore; TESTGEN_SAVE_LLAMA_SLOT_BIN is disabled.")
        return False

    full_path = os.path.join(SLOT_SAVE_PATH, LLAMA_CACHE_FILE)

    if os.path.exists(full_path):
        try:
            print("Loading binary KV cache into llama-server...")
            response = requests.post(
                f"{LLAMA_SERVER_URL}?action=restore",
                json={"filename": LLAMA_CACHE_FILE},
                timeout=330,
            )

            if response.status_code == 200:
                print("KV cache successfully restored.")
                return True

            print(f"Server rejected cache load: {response.text}")
        except Exception as e:
            print(f"Fresh KV cache initialization required: {str(e)}")

    return False


def save_llama_kv_cache():
    if not SAVE_LLAMA_SLOT_BIN:
        print("Skipping llama-server binary KV cache save; TESTGEN_SAVE_LLAMA_SLOT_BIN is disabled.")
        return False

    try:
        print("Saving binary KV cache from llama-server to disk...")
        response = requests.post(
            f"{LLAMA_SERVER_URL}?action=save",
            json={"filename": LLAMA_CACHE_FILE},
            timeout=330,
        )

        if response.status_code == 200:
            print(f"Binary KV cache saved to {SLOT_SAVE_PATH}")
            return True

        print(f"Server failed to save KV cache: {response.text}")
    except Exception as e:
        print(f"Failed to save KV cache: {str(e)}")

    return False


class TerminalPromptColor:
    RESET = "\033[0m"
    TITLE = "\033[1;38;5;213m"
    SYSTEM = "\033[38;5;81m"
    USER = "\033[38;5;222m"
    BORDER = "\033[38;5;99m"


def print_prompt_to_terminal(title: str, system_prompt: str, user_prompt: str) -> None:
    if not PRINT_PROMPTS_IN_TERMINAL:
        return

    border = "=" * 120
    print(
        f"\n{TerminalPromptColor.BORDER}{border}{TerminalPromptColor.RESET}\n"
        f"{TerminalPromptColor.TITLE}{title}{TerminalPromptColor.RESET}\n"
        f"{TerminalPromptColor.BORDER}{border}{TerminalPromptColor.RESET}",
        flush=True,
    )
    print(
        f"{TerminalPromptColor.SYSTEM}\n--- SYSTEM PROMPT SENT TO MODEL ---\n"
        f"{system_prompt}{TerminalPromptColor.RESET}",
        flush=True,
    )
    print(
        f"{TerminalPromptColor.USER}\n--- USER PROMPT SENT TO MODEL ---\n"
        f"{user_prompt}{TerminalPromptColor.RESET}",
        flush=True,
    )
    print(f"{TerminalPromptColor.BORDER}{border}{TerminalPromptColor.RESET}\n", flush=True)


class ModelStreamStuckError(RuntimeError):
    pass


def normalize_stream_words(text):
    return re.findall(r"[a-zA-Z0-9_.$]+", text.lower())


def repeated_tail_similarity(text, tail_words=STREAM_STUCK_TAIL_WORDS, window_words=STREAM_STUCK_WINDOW_WORDS):
    words = normalize_stream_words(text)
    if len(words) < window_words * 3:
        return 0.0

    tail = words[-tail_words:]
    current = " ".join(tail[-window_words:])
    previous_windows = [
        " ".join(tail[i:i + window_words])
        for i in range(0, max(0, len(tail) - window_words), window_words)
    ]

    if not current or not previous_windows:
        return 0.0

    return max(
        difflib.SequenceMatcher(None, current, previous).ratio()
        for previous in previous_windows
        if previous
    )


def detect_stream_stuck(reasoning_text, content_text):
    if not MODEL_STUCK_DETECTOR_ENABLED:
        return None

    reasoning_len = len(reasoning_text)
    content_len = len(content_text)

    if reasoning_len >= STREAM_STUCK_MIN_REASONING_CHARS:
        score = repeated_tail_similarity(reasoning_text)
        if score >= STREAM_STUCK_SIMILARITY:
            return (
                "repeated reasoning",
                f"reasoning_chars={reasoning_len}, content_chars={content_len}, similarity={score:.2f}",
            )

    if content_len >= STREAM_STUCK_MIN_CONTENT_CHARS:
        score = repeated_tail_similarity(content_text)
        if score >= STREAM_STUCK_SIMILARITY:
            return (
                "repeated final content",
                f"reasoning_chars={reasoning_len}, content_chars={content_len}, similarity={score:.2f}",
            )

    if reasoning_len >= STREAM_STUCK_NO_FINAL_REASONING_CHARS and content_len == 0:
        return (
            "reasoning produced no final content",
            f"reasoning_chars={reasoning_len}, content_chars=0",
        )

    return None


def build_stuck_retry_messages(messages, reason):
    if STREAM_RETRY_INSTRUCTION_BUILDER:
        retry_instruction = STREAM_RETRY_INSTRUCTION_BUILDER(reason)
    else:
        retry_instruction = (
            "The previous stream was stopped because it appeared stuck: "
            f"{reason}. Return the requested final output now. "
            f"Return only the final artifact in the requested format. {final_output_instruction()}"
        )

    return list(messages) + [{"role": "user", "content": retry_instruction}]


def estimate_stream_tokens(text: str) -> int:
    """
    llama-server does not expose separate streamed reasoning/content usage.
    Use a conservative byte-based estimate so logs still show per-block cost.
    """
    if not text:
        return 0
    return max(1, round(len(text.encode("utf-8")) / 4))


def estimate_messages_tokens(messages) -> tuple[int, int]:
    parts: list[str] = []
    for message in messages or []:
        role = str((message or {}).get("role", "") or "")
        content = (message or {}).get("content", "")
        if isinstance(content, list):
            content = json.dumps(content, ensure_ascii=False)
        else:
            content = str(content or "")
        parts.append(f"{role}\n{content}")
    text = "\n\n".join(parts)
    return estimate_stream_tokens(text), len(text)


def stream_chat_completion_once(messages, temperature=None, extra_body=None):
    try:
        config = get_config()
    except RuntimeError:
        config = None
    request_temperature = (
        temperature
        if temperature is not None
        else (config.request_temperature if config else 0.1)
    )
    request_top_p = config.request_top_p if config else 0.95
    merged_extra_body = {
        "min_p": config.request_min_p if config else 0.0,
        "top_k": config.request_top_k if config else 20,
        "presence_penalty": config.request_presence_penalty if config else 0.2,
        "repeat_penalty": config.request_repeat_penalty if config else 1.05,
        "seed": config.request_seed if config else 42,
    }
    if thinking_enabled():
        merged_extra_body.update(
            {
                "chat_template_kwargs": {
                    "enable_thinking": True,
                },
                "thinking_budget_tokens": 16384,
            }
        )

    if extra_body:
        merged_extra_body.update(extra_body)
    if not thinking_enabled():
        merged_extra_body = strip_thinking_fields(merged_extra_body)

    if "temperature" in merged_extra_body:
        request_temperature = merged_extra_body.pop("temperature")
    if "top_p" in merged_extra_body:
        request_top_p = merged_extra_body.pop("top_p")

    if REQUEST_SAMPLER_LOG:
        request_sampler = {
            "temperature": request_temperature,
            "top_p": request_top_p,
            **{
                key: merged_extra_body.get(key)
                for key in [
                    "min_p",
                    "top_k",
                    "presence_penalty",
                    "repeat_penalty",
                    "chat_template_kwargs",
                    "thinking_budget_tokens",
                    "seed",
                ]
                if key in merged_extra_body
            },
        }
        log_block(
            "CHAT COMPLETION REQUEST SAMPLING PARAMS",
            json.dumps(request_sampler, indent=2),
            category="context",
            console=True,
        )

    prompt_tokens, prompt_chars = estimate_messages_tokens(messages)
    log_message(
        "🧮 Prompt token estimate: "
        f"~{prompt_tokens} tokens ({prompt_chars} chars).",
        category="context",
        console=True,
    )

    response = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=messages,
        temperature=request_temperature,
        top_p=request_top_p,
        stream=True,
        extra_body=merged_extra_body,
    )

    raw_chunks = []
    reasoning_chunks = []
    last_stuck_check_reasoning_len = 0
    last_stuck_check_content_len = 0

    thinking_shown = False
    for chunk in response:
        try:
            if not chunk.choices or len(chunk.choices) == 0:
                continue

            delta = chunk.choices[0].delta

            reasoning = (
                getattr(delta, "reasoning_content", None)
                or getattr(delta, "reasoning", None)
                or getattr(delta, "thinking", None)
            )

            content = getattr(delta, "content", None)
            
            if reasoning:
                reasoning_text = str(reasoning)
                reasoning_chunks.append(reasoning_text)
                log_message(reasoning_text, category="reasoning", end="", console=MODEL_REASONING_PRINT)
                if MODEL_REASONING_PRINT:
                    pass
                elif not thinking_shown:
                    log_message("Thinking...", category="reasoning")
                    thinking_shown = True

            if content:
                log_message(content, category="code", end="")
                raw_chunks.append(content)

            reasoning_text_so_far = "".join(reasoning_chunks)
            content_text_so_far = "".join(raw_chunks)
            should_check_stuck = (
                len(reasoning_text_so_far) - last_stuck_check_reasoning_len >= 2000
                or len(content_text_so_far) - last_stuck_check_content_len >= 2000
            )

            if should_check_stuck:
                last_stuck_check_reasoning_len = len(reasoning_text_so_far)
                last_stuck_check_content_len = len(content_text_so_far)
                stuck = detect_stream_stuck(reasoning_text_so_far, content_text_so_far)
                if stuck:
                    stuck_reason, stuck_details = stuck
                    log_message(
                        f"\n⚠️ Model stream appears stuck ({stuck_reason}; {stuck_details}).",
                        category="warning",
                    )
                    log_block(
                        "STUCK STREAM REASONING TAIL",
                        reasoning_text_so_far[-4000:] or "No reasoning captured.",
                        category="reasoning",
                        console=False,
                    )
                    log_block(
                        "STUCK STREAM CONTENT TAIL",
                        content_text_so_far[-4000:] or "No final content captured.",
                        category="code",
                        console=False,
                    )
                    raise ModelStreamStuckError(f"{stuck_reason}; {stuck_details}")

        except ModelStreamStuckError:
            raise
        except Exception:
            continue

    log_message("")
    final_text = "".join(raw_chunks)
    reasoning_text = "".join(reasoning_chunks)
    if reasoning_text.strip():
        log_block("MODEL REASONING", reasoning_text, category="reasoning", console=False)
        log_message(
            "🧮 Reasoning/thinking token estimate: "
            f"~{estimate_stream_tokens(reasoning_text)} tokens "
            f"({len(reasoning_text)} chars).",
            category="reasoning",
        )

    if final_text.strip():
        log_block("MODEL FINAL CONTENT", final_text, category="code", console=False)
        log_message(
            "🧮 Final content token estimate: "
            f"~{estimate_stream_tokens(final_text)} tokens "
            f"({len(final_text)} chars).",
            category="code",
        )
        return final_text

    if REASONING_FALLBACK_DETECTOR and REASONING_FALLBACK_DETECTOR(reasoning_text):
        log_message("⚠️ Model returned usable content in reasoning stream; using it as fallback output.", category="warning")
        log_block("MODEL REASONING FALLBACK CONTENT", reasoning_text, category="reasoning", console=False)
        return reasoning_text

    if reasoning_text.strip():
        log_message("⚠️ Model returned reasoning but no final content.", category="warning")
        log_block("MODEL REASONING WITHOUT FINAL CONTENT", reasoning_text, category="reasoning", console=False)

    return final_text


def stream_chat_completion(messages, temperature=0.1, extra_body=None):
    """
    Streams model output. If the model gets repetitive or reasons for a very
    long time without final output, stop that stream and retry once with a
    stricter final-output-only instruction.
    """
    active_messages = messages
    active_extra_body = strip_thinking_fields(extra_body or {})

    for attempt in range(STREAM_STUCK_RETRY_LIMIT + 1):
        try:
            return stream_chat_completion_once(
                active_messages,
                temperature=temperature,
                extra_body=active_extra_body,
            )
        except ModelStreamStuckError as exc:
            if attempt >= STREAM_STUCK_RETRY_LIMIT:
                log_message("⚠️ Model stream stayed stuck after retry limit.", category="warning")
                return ""

            log_message("🔁 Retrying model stream with stricter final-output instruction.", category="warning")
            active_messages = build_stuck_retry_messages(active_messages, str(exc))
            retry_extra_body = {
                "repeat_penalty": 1.15,
                "presence_penalty": 0.15,
            }
            if thinking_enabled():
                retry_extra_body["thinking_budget_tokens"] = 6144
            active_extra_body.update(retry_extra_body)
            active_extra_body = strip_thinking_fields(active_extra_body)

    return ""
