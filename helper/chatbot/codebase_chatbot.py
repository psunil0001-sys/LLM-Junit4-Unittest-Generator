#!/usr/bin/env python3
# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: RAG REPL CLI for UnitTest_gen docs and Python source.
"""Terminal REPL chatbot over UnitTest_gen docs and Python source."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

_HELPER_DIR = Path(__file__).resolve().parent
if str(_HELPER_DIR) not in sys.path:
    sys.path.insert(0, str(_HELPER_DIR))

import codebase_chatbot_lib as lib

# Re-exports for tests and callers importing UnitTest_gen.helper.chatbot.codebase_chatbot
DEFAULT_TOP_K = lib.DEFAULT_TOP_K
SYSTEM_PROMPT = lib.SYSTEM_PROMPT
ScopedVectorCache = lib.ScopedVectorCache
LocalServerManager = lib.ChatbotServerManager
build_chatbot_server_command = lib.build_chatbot_server_command
build_chatbot_embedding_server_command = lib.build_chatbot_embedding_server_command
build_chat_index = lib.build_chat_index
chat_completion_text = lib.chat_completion_text
chat_once = lib.chat_once
configure_chatbot_runtime = lib.configure_chatbot_runtime
create_chatbot_server_manager = lib.create_chatbot_server_manager
ensure_managed_servers = lib.ensure_managed_servers
embeddings_enabled = lib.embeddings_enabled
get_vector = lib.get_vector
is_embedding_server_available = lib.is_embedding_server_available
is_indexable_chat_path = lib.is_indexable_chat_path
is_model_server_available = lib.is_model_server_available
retrieve_context = lib.retrieve_context
unit_test_gen_root = lib.unit_test_gen_root
_retrieval_status_line = lib.retrieval_status_line
_chat_server_unavailable_message = lib.chat_server_unavailable_message
_sync_embeddings_config = lib.sync_embeddings_config


@dataclass
class ChatSession:
    args: argparse.Namespace
    index: dict
    scan_root: Path
    cache_path: str
    status_lines: list[str]
    server_manager: lib.ChatbotServerManager | None = None


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="RAG chatbot over UnitTest_gen documentation and Python source",
    )
    parser.add_argument(
        "--codebase",
        default="",
        metavar="PATH",
        help="Directory to index (.md/.puml/.py). Default: UnitTest_gen/doc only",
    )
    parser.add_argument(
        "--reindex",
        action="store_true",
        help="Rebuild the vector/keyword index cache from scratch",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=DEFAULT_TOP_K,
        help=f"Number of context files to retrieve per question (default {DEFAULT_TOP_K})",
    )
    parser.add_argument(
        "--no-manage-server",
        action="store_true",
        help="Do not auto-start/stop the chatbot llama-server (default: manage server)",
    )
    parser.add_argument(
        "-nst",
        "--no-server-terminal",
        action="store_true",
        help="Start managed server in background instead of a terminal window",
    )
    parser.add_argument(
        "--knowledge-bundle",
        default="",
        metavar="FILE",
        help="Load pre-built knowledge.bundle.json (no doc/ or source tree needed)",
    )
    parser.add_argument(
        "--export-knowledge-bundle",
        default="",
        metavar="FILE",
        help="After indexing, export portable bundle and exit (build on your machine only)",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Open a tkinter chat window instead of the terminal REPL",
    )
    return parser


def bootstrap_chat_session(args: argparse.Namespace) -> ChatSession | None:
    configure_chatbot_runtime()
    runtime = lib.get_runtime()

    server_manager = ensure_managed_servers(
        manage=not args.no_manage_server,
        use_terminal=not args.no_server_terminal,
        project_root=str(lib.repo_root()),
    )
    if server_manager is None and not args.no_manage_server and not is_model_server_available():
        return None

    _sync_embeddings_config()

    bundle_path = (args.knowledge_bundle or "").strip()
    export_path = (args.export_knowledge_bundle or "").strip()

    if bundle_path and export_path:
        print("❌ Use either --knowledge-bundle or --export-knowledge-bundle, not both.")
        if server_manager:
            server_manager.stop_all_started_servers()
        return None

    status_lines = [
        f"Chat model: {runtime.chat_model} @ {runtime.chat_base_url}",
        f"Embeddings: {runtime.embedding_model} @ {runtime.embedding_base_url}",
    ]
    if not is_model_server_available():
        status_lines.append(_chat_server_unavailable_message())

    scan_root: Path
    cache_path = ""

    if bundle_path:
        try:
            index = lib.load_knowledge_bundle(bundle_path)
        except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
            print(f"❌ {exc}")
            if server_manager:
                server_manager.stop_all_started_servers()
            return None
        scan_root = Path(bundle_path).resolve().parent
        status_lines.append(f"Loaded knowledge bundle: {bundle_path}")
        status_lines.append(f"Ready: {len(index)} items in bundle.")
    else:
        try:
            scan_root, docs_only = lib.resolve_index_root(args.codebase or None)
        except FileNotFoundError as exc:
            print(f"❌ {exc}")
            if server_manager:
                server_manager.stop_all_started_servers()
            return None

        cache = ScopedVectorCache(str(lib.chat_cache_path(scan_root)))
        cache_path = cache.path
        status_lines.append(f"Indexing {scan_root} ...")
        index = build_chat_index(
            str(scan_root),
            cache,
            reindex=args.reindex,
            docs_only=docs_only,
        )
        status_lines.append(f"Ready: {len(index)} files indexed. Cache: {cache.path}")
        if not index:
            status_lines.append(
                f"⚠️  No indexable files under {scan_root}. "
                f"Check --codebase or copy doc/ next to chatbot/ (expected: {lib.default_index_root()})."
            )

    if export_path:
        try:
            out = lib.export_knowledge_bundle(index, export_path)
            status_lines.append(f"Exported knowledge bundle: {out}")
        except OSError as exc:
            print(f"❌ Failed to export knowledge bundle: {exc}")
            if server_manager:
                server_manager.stop_all_started_servers()
            return None

    status_lines.append(_retrieval_status_line())

    for line in status_lines:
        print(line)

    return ChatSession(
        args=args,
        index=index,
        scan_root=scan_root,
        cache_path=cache_path,
        status_lines=status_lines,
        server_manager=server_manager,
    )


def run_repl(session: ChatSession) -> int:
    history: list[dict[str, str]] = []
    top_k = max(1, session.args.top_k)
    print("Ask questions about UnitTest_gen (exit/quit to leave).\n")
    while True:
        try:
            question = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not question:
            continue
        if question.lower() in {"exit", "quit", ":q"}:
            break

        print("\nassistant> ", end="", flush=True)
        answer = chat_once(question, session.index, history, top_k=top_k)
        print(answer or "(no model response)")
        print()
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": answer or ""})
    return 0


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    args = build_arg_parser().parse_args(argv)
    if args.gui:
        from codebase_chatbot_gui import require_tkinter

        require_tkinter()
    session = bootstrap_chat_session(args)
    if session is None:
        return 1
    try:
        if (args.export_knowledge_bundle or "").strip():
            return 0
        if args.gui:
            from codebase_chatbot_gui import run_gui

            return run_gui(session)
        return run_repl(session)
    finally:
        if session.server_manager:
            session.server_manager.stop_all_started_servers()


if __name__ == "__main__":
    raise SystemExit(main())
