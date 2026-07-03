#!/usr/bin/env python3
# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: tkinter GUI for the codebase RAG chatbot.
"""Small tkinter window for the UnitTest_gen RAG chatbot."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

_HELPER_DIR = Path(__file__).resolve().parent
if str(_HELPER_DIR) not in sys.path:
    sys.path.insert(0, str(_HELPER_DIR))

from codebase_chatbot import ChatSession, chat_once


def require_tkinter() -> None:
    try:
        import tkinter  # noqa: F401
    except ModuleNotFoundError:
        print(
            "❌ tkinter is not installed (required for the GUI).\n"
            "   Ubuntu/Debian: sudo apt install python3-tk\n"
            "   Or use the terminal chatbot: python codebase_chatbot.py",
            file=sys.stderr,
        )
        raise SystemExit(1)


def parse_gui_args(argv: list[str] | None = None):
    from codebase_chatbot import build_arg_parser

    argv = sys.argv[1:] if argv is None else list(argv)
    if not any(token in argv for token in ("--gui", "-h", "--help")):
        argv = ["--gui", *argv]
    return build_arg_parser().parse_args(argv)


def run_gui(session: ChatSession) -> int:
    require_tkinter()
    import tkinter as tk
    from tkinter import scrolledtext
    root = tk.Tk()
    root.title("UnitTest_gen Chatbot")
    root.geometry("760x560")
    root.minsize(480, 360)

    top_k = max(1, session.args.top_k)
    history: list[dict[str, str]] = []
    busy = threading.Event()

    status = tk.Label(
        root,
        text=session.status_lines[-1] if session.status_lines else "",
        anchor="w",
        relief=tk.SUNKEN,
        padx=6,
        pady=4,
    )
    status.pack(fill=tk.X, side=tk.BOTTOM)

    input_frame = tk.Frame(root, padx=8, pady=8)
    input_frame.pack(fill=tk.X, side=tk.BOTTOM)

    entry = tk.Entry(input_frame)
    entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

    send_btn = tk.Button(input_frame, text="Send", width=10)
    send_btn.pack(side=tk.RIGHT)

    transcript = scrolledtext.ScrolledText(
        root,
        wrap=tk.WORD,
        state=tk.DISABLED,
        padx=8,
        pady=8,
        font=("Segoe UI", 10),
    )
    transcript.pack(fill=tk.BOTH, expand=True)
    transcript.tag_configure("user", foreground="#1a5276")
    transcript.tag_configure("assistant", foreground="#1e8449")
    transcript.tag_configure("system", foreground="#566573")

    def append_line(text: str, tag: str = "assistant") -> None:
        transcript.configure(state=tk.NORMAL)
        transcript.insert(tk.END, text, tag)
        transcript.see(tk.END)
        transcript.configure(state=tk.DISABLED)

    def set_busy(is_busy: bool) -> None:
        send_btn.configure(state=tk.DISABLED if is_busy else tk.NORMAL)
        entry.configure(state=tk.DISABLED if is_busy else tk.NORMAL)

    def finish_answer(question: str, answer: str) -> None:
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": answer or ""})
        append_line(f"Assistant: {answer or '(no model response)'}\n\n", "assistant")
        busy.clear()
        set_busy(False)
        entry.focus_set()

    def worker(question: str) -> None:
        try:
            answer = chat_once(question, session.index, history, top_k=top_k)
        except Exception as exc:
            answer = f"Error: {exc}"
        root.after(0, lambda: finish_answer(question, answer))

    def send_question(_event=None) -> None:
        if busy.is_set():
            return
        question = entry.get().strip()
        if not question:
            return
        entry.delete(0, tk.END)
        append_line(f"You: {question}\n", "user")
        busy.set()
        set_busy(True)
        threading.Thread(target=worker, args=(question,), daemon=True).start()

    send_btn.configure(command=send_question)
    entry.bind("<Return>", send_question)
    root.protocol("WM_DELETE_WINDOW", root.destroy)

    append_line(
        f"Indexed {len(session.index)} files from {session.scan_root}.\n"
        "Ask about UnitTest_gen docs or source.\n\n",
        "system",
    )
    entry.focus_set()
    root.mainloop()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_gui_args(argv)
    require_tkinter()
    from codebase_chatbot import bootstrap_chat_session

    session = bootstrap_chat_session(args)
    if session is None:
        return 1
    try:
        return run_gui(session)
    finally:
        if session.server_manager:
            session.server_manager.stop_all_started_servers()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
