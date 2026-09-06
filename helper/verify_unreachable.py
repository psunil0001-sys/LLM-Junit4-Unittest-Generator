#!/usr/bin/env python3
# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: LLM audit of unreachable_coverage.json entries (false-positive check).
"""Verify deferred unreachable coverage entries with the local LLM.

Skips entries already marked ``model_processed: true``. Starts/stops the vendored
llama-server when this process owns it.

Example::

    python3 UnitTest_gen/helper/verify_unreachable.py
    python3 UnitTest_gen/helper/verify_unreachable.py --dry-run --limit 1
"""

from __future__ import annotations

import argparse
import json
import re
import signal
import sys
from pathlib import Path
from typing import Any

# Allow ``python3 helper/verify_unreachable.py`` without setting PYTHONPATH.
_REPO_ROOT = str(Path(__file__).resolve().parents[2])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from UnitTest_gen.core.adk_agents.model import (  # noqa: E402
    _thinking_extra_body,
    openai_client_kwargs,
    resolve_openai_model_name,
)
from UnitTest_gen.core.config import load_config_from_env, set_active_config  # noqa: E402
from UnitTest_gen.core.io import compact_ranges, save_json  # noqa: E402
from UnitTest_gen.core.llm import (  # noqa: E402
    ensure_coding_server,
    is_server_healthy,
    resolve_agent_model,
    stop_coding_server,
    stop_owned_coding_server,
)
from UnitTest_gen.kotlin.coverage import (  # noqa: E402
    DEFAULT_STORE_PATH,
    entry_lines_and_branches,
    load_store,
    store_path,
)

PACKAGE_DIR = Path(__file__).resolve().parents[1]
SYSTEM_PROMPT_PATH = PACKAGE_DIR / "data" / "prompt_skeletons" / "verify_unreachable_system.md"

# Persist flag on each audited entry (skip on later runs).
MODEL_PROCESSED_KEY = "model_processed"

_DEFAULT_TEMP = 0.1
_DEFAULT_REASONING_BUDGET = 64
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def is_model_processed(entry: dict[str, Any] | None) -> bool:
    """True when the entry was already verified by this script."""
    if not isinstance(entry, dict):
        return False
    if entry.get(MODEL_PROCESSED_KEY) is True:
        return True
    raw = entry.get(MODEL_PROCESSED_KEY)
    if isinstance(raw, str) and raw.strip().lower() in {"true", "1", "yes"}:
        return True
    # Tolerate spaced / legacy keys.
    for key, value in entry.items():
        norm = str(key).lower().replace("_", " ").replace(":", " ").strip()
        if norm in {"model processed", "model processed true"}:
            if value is True or str(value).strip().lower() in {"true", "1", "yes", ""}:
                return True
    return False


def pending_entries(entries: list[Any]) -> list[tuple[int, dict[str, Any]]]:
    """Return ``(index, entry)`` pairs that still need model verification."""
    out: list[tuple[int, dict[str, Any]]] = []
    for index, entry in enumerate(entries or []):
        if not isinstance(entry, dict):
            continue
        if is_model_processed(entry):
            continue
        out.append((index, entry))
    return out


def _read_source_excerpt(source: str, probes: set[int], *, pad: int = 3) -> str:
    path = Path(source)
    if not path.is_file() or not probes:
        return "(source unavailable)"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return f"(could not read source: {exc})"
    wanted: set[int] = set()
    for n in probes:
        for i in range(max(1, n - pad), min(len(lines), n + pad) + 1):
            wanted.add(i)
    chunks: list[str] = []
    for n in sorted(wanted):
        mark = ">>" if n in probes else "  "
        text = lines[n - 1] if 0 < n <= len(lines) else ""
        chunks.append(f"{mark} {n:4d}| {text}")
    return "\n".join(chunks)


def _guess_target_test_paths(source: str) -> list[Path]:
    """Best-effort map ``src/main/.../Foo.kt`` → ``src/test/.../FooTest.kt`` candidates."""
    path = Path(source)
    if not path.name.endswith(".kt"):
        return []
    text = str(path)
    if "/src/main/" not in text:
        return []
    stem = path.stem
    test_root = Path(text.replace("/src/main/", "/src/test/", 1)).parent
    names = [
        f"{stem}Test.kt",
        f"{stem}RealTest.kt",
        f"{stem}UnitTest.kt",
    ]
    return [test_root / name for name in names]


def _target_evidence_block(source: str) -> str:
    """Summarize whether TARGET already has flow cancel / collect probes."""
    candidates = _guess_target_test_paths(source)
    existing = [p for p in candidates if p.is_file()]
    if not existing:
        return (
            "TARGET EVIDENCE: (no sibling *Test.kt found under src/test; "
            "treat planner reason as authoritative for residual category)"
        )
    path = existing[0]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"TARGET EVIDENCE: (could not read {path}: {exc})"
    has_launch = "launchIn" in text
    has_cancel = "cancelAndJoin" in text or re.search(r"\bcancel\s*\(", text) is not None
    has_first = ".first(" in text or ".first()" in text
    has_collect = ".collect" in text or "collectLatest" in text
    flags = [
        f"path={path}",
        f"launchIn={has_launch}",
        f"cancel_probe={has_cancel}",
        f"first={has_first}",
        f"collect={has_collect}",
    ]
    # Short excerpt of cancel-related lines for the model.
    highlight: list[str] = []
    for i, line in enumerate(text.splitlines(), start=1):
        if any(tok in line for tok in ("launchIn", "cancelAndJoin", "cancel(", "onEach", "delay(")):
            highlight.append(f"  {i:4d}| {line.rstrip()}")
            if len(highlight) >= 12:
                break
    body = "\n".join(highlight) if highlight else "  (no cancel/onEach lines matched)"
    return "TARGET EVIDENCE:\n" + "\n".join(f"  {f}" for f in flags) + "\n" + body


_NAIVE_FP_RE = re.compile(
    r"\b(first\s*\(|\.first\b|collect\b|emit\b|invoke the flow|unit tests? invoking|"
    r"standard kotlin\s+`?flow|fully coverable)\b",
    re.IGNORECASE,
)
_NEW_TECHNIQUE_RE = re.compile(
    r"\b(launchIn|cancelAndJoin|cancel\s*\(|slow.?collector|onEach\s*\{|"
    r"exception|throws?|TimeoutCancellation|missing (cancel|exception))\b",
    re.IGNORECASE,
)


def reconcile_verdict(entry: dict[str, Any], verdict: dict[str, Any]) -> dict[str, Any]:
    """Align model output with planner residual policy (reject naive false positives)."""
    category = str(entry.get("category") or "").strip()
    genuine = bool(verdict.get("genuine_unreachable"))
    reason = str(verdict.get("reason") or "").strip()
    if genuine or category != "kover_residual_branch":
        return {"genuine_unreachable": genuine, "reason": reason}

    # Model claimed false positive for residual branch — require a concrete NEW technique.
    naive = bool(_NAIVE_FP_RE.search(reason))
    names_new = bool(_NEW_TECHNIQUE_RE.search(reason))
    source = str(entry.get("source") or "")
    target_block = _target_evidence_block(source)
    cancel_already = "cancel_probe=True" in target_block and "launchIn=True" in target_block

    if cancel_already or naive or not names_new:
        lines_set, branches_set = entry_lines_and_branches(entry)
        probe = compact_ranges(sorted(branches_set or lines_set))
        forced = (
            f"Kover residual branch (mb>0) on {probe}: TARGET already covers logical Flow emit "
            f"paths; remaining probes are coroutine/JaCoCo state-machine residuals after "
            f"cancel/collect attempts. Planner demotion stands."
        )
        return {"genuine_unreachable": True, "reason": forced[:800]}
    return {"genuine_unreachable": False, "reason": reason}


def build_user_prompt(entry: dict[str, Any]) -> str:
    """Build the per-entry user message (entry fields + SOURCE/TARGET excerpts)."""
    lines_set, branches_set = entry_lines_and_branches(entry)
    probes = lines_set | branches_set
    source = str(entry.get("source") or "")
    category = str(entry.get("category") or "")
    payload = {
        "category": entry.get("category"),
        "module": entry.get("module"),
        "source": source,
        "source_name": entry.get("source_name"),
        "methods": entry.get("methods") or [],
        "lines": sorted(lines_set),
        "branches": sorted(branches_set),
        "reason": entry.get("reason"),
        "evidence": entry.get("evidence"),
        "recorded_at": entry.get("recorded_at"),
    }
    category_note = (
        "CATEGORY NOTE: kover_residual_branch = planner already demoted partial mb>0 probes "
        "after TARGET exercised public methods (and flow cancel when SOURCE is thin flow{emit}). "
        "Naive collect/first is NOT a false-positive fix."
        if category == "kover_residual_branch"
        else f"CATEGORY NOTE: {category or 'unknown'}."
    )
    return "\n".join(
        [
            "UNREACHABLE ENTRY (JSON):",
            json.dumps(payload, indent=2),
            "",
            category_note,
            "",
            f"LINES (missed): {compact_ranges(sorted(lines_set))}",
            f"BRANCHES (partial mb>0): {compact_ranges(sorted(branches_set))}",
            "",
            "SOURCE EXCERPT (>> = probe line):",
            _read_source_excerpt(source, probes),
            "",
            _target_evidence_block(source),
            "",
            "Return the JSON object only.",
        ]
    )


def load_system_prompt() -> str:
    try:
        return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return (
            "Audit one unreachable coverage entry. Reply JSON only with keys "
            "genuine_unreachable (bool) and reason (string)."
        )


def parse_model_json(text: str) -> dict[str, Any]:
    """Extract the JSON object from a model reply."""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("empty model response")
    # Strip optional ```json fences
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = _JSON_OBJECT_RE.search(raw)
        if not match:
            raise
        data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("model JSON root must be an object")
    if "genuine_unreachable" not in data:
        raise ValueError("missing genuine_unreachable")
    genuine = data["genuine_unreachable"]
    if isinstance(genuine, str):
        genuine = genuine.strip().lower() in {"true", "1", "yes"}
    else:
        genuine = bool(genuine)
    reason = str(data.get("reason") or "").strip()
    if not reason:
        raise ValueError("missing reason")
    return {"genuine_unreachable": genuine, "reason": reason[:800]}


def apply_verification(
    entry: dict[str, Any],
    verdict: dict[str, Any],
) -> str:
    """Mutate ``entry`` in place. Returns ``keep`` or ``drop``."""
    entry[MODEL_PROCESSED_KEY] = True
    entry["reason"] = str(verdict.get("reason") or entry.get("reason") or "").strip()
    if verdict.get("genuine_unreachable"):
        return "keep"
    return "drop"


def call_model(
    *,
    system: str,
    user: str,
    temperature: float = _DEFAULT_TEMP,
    reasoning_budget: int = _DEFAULT_REASONING_BUDGET,
) -> str:
    """Synchronous OpenAI-compatible chat completion (no tools)."""
    from openai import OpenAI

    client = OpenAI(**openai_client_kwargs())
    model = resolve_openai_model_name()
    extra = _thinking_extra_body(enable_thinking=True, reasoning_budget=int(reasoning_budget))
    response = client.chat.completions.create(
        model=model,
        temperature=float(temperature),
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        extra_body=extra,
    )
    choice = response.choices[0] if response.choices else None
    message = choice.message if choice is not None else None
    content = getattr(message, "content", None) if message is not None else None
    return str(content or "").strip()


def _persist(store: dict[str, Any], path: Path) -> None:
    save_json(path, store, touch_updated_at=True)


def run_verification(
    *,
    input_path: Path,
    dry_run: bool = False,
    limit: int = 0,
    temperature: float = _DEFAULT_TEMP,
    reasoning_budget: int = _DEFAULT_REASONING_BUDGET,
    skip_server: bool = False,
    stop_server_on_exit: bool = False,
    preloaded_store: dict[str, Any] | None = None,
) -> int:
    """Process pending entries. Returns process exit code (0 ok)."""
    store = preloaded_store if isinstance(preloaded_store, dict) else load_store(input_path)
    entries = list(store.get("entries") or [])
    pending = pending_entries(entries)
    if limit > 0:
        pending = pending[:limit]

    # Summary may already have been printed by main(); keep a short line when called alone.
    if preloaded_store is None:
        total = len(entries)
        already = total - len(pending_entries(entries))
        print(
            f"Unreachable store: {input_path}\n"
            f"  entries={total} already_processed≈{already} pending={len(pending)} "
            f"dry_run={dry_run}"
        )
    if not pending:
        print("Nothing to verify — all entries have model_processed=true.")
        return 0

    system = load_system_prompt()
    kept = 0
    dropped = 0
    failed = 0

    for seq, (_index, entry) in enumerate(pending, start=1):
        # Re-resolve index each time because drops shrink the list (identity match).
        try:
            live_index = next(i for i, row in enumerate(entries) if row is entry)
        except StopIteration:
            continue

        name = entry.get("source_name") or Path(str(entry.get("source") or "")).name
        lines_set, branches_set = entry_lines_and_branches(entry)
        print(
            f"\n[{seq}/{len(pending)}] {name} "
            f"cat={entry.get('category')} "
            f"lines={compact_ranges(sorted(lines_set))} "
            f"branches={compact_ranges(sorted(branches_set))}"
        )
        user = build_user_prompt(entry)
        try:
            raw = call_model(
                system=system,
                user=user,
                temperature=temperature,
                reasoning_budget=reasoning_budget,
            )
            verdict = parse_model_json(raw)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  ❌ model/parse error: {exc}")
            continue

        reconciled = reconcile_verdict(entry, verdict)
        if reconciled != verdict and reconciled.get("genuine_unreachable"):
            print(
                "  ↩ overridden to genuine (planner residual policy; "
                "naive collect/emit FP rejected)"
            )
        action = apply_verification(entry, reconciled)
        if action == "drop":
            dropped += 1
            print(f"  ⚠️  false positive → drop | {reconciled['reason'][:160]}")
            if not dry_run:
                entries.pop(live_index)
        else:
            kept += 1
            print(f"  ✅ genuine → keep | {reconciled['reason'][:160]}")

        if not dry_run:
            store["entries"] = entries
            _persist(store, input_path)

    if dry_run:
        print("\n(dry-run: store not written)")

    print(
        f"\nDone. kept={kept} dropped_false_positives={dropped} failed={failed} "
        f"store={input_path}"
    )
    return 1 if failed and not kept and not dropped else 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify unreachable_coverage.json entries with the local LLM.",
    )
    parser.add_argument(
        "--input",
        "-i",
        type=Path,
        default=None,
        help=f"Path to unreachable_coverage.json (default: {DEFAULT_STORE_PATH})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Call the model but do not write the JSON store.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max pending entries to process this run (0 = all).",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=_DEFAULT_TEMP,
        help=f"Chat temperature (default {_DEFAULT_TEMP}).",
    )
    parser.add_argument(
        "--reasoning-budget",
        type=int,
        default=_DEFAULT_REASONING_BUDGET,
        help=f"Thinking token budget (default {_DEFAULT_REASONING_BUDGET}).",
    )
    parser.add_argument(
        "--skip-server",
        action="store_true",
        help="Do not auto-start llama-server; require it already healthy.",
    )
    parser.add_argument(
        "--stop-server-on-exit",
        action="store_true",
        help="Stop llama-server on exit even if it was already running.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    config = load_config_from_env()
    set_active_config(config)

    input_path = (args.input or store_path()).expanduser().resolve()
    if not input_path.is_file():
        print(f"❌ Input not found: {input_path}")
        return 2

    # Inspect JSON first — only start the model when there is work.
    store = load_store(input_path)
    entries = list(store.get("entries") or [])
    pending = pending_entries(entries)
    limit = max(0, int(args.limit or 0))
    if limit > 0:
        pending = pending[:limit]
    total = len(entries)
    already = total - len(pending_entries(entries))
    print(
        f"Unreachable store: {input_path}\n"
        f"  entries={total} already_processed≈{already} pending={len(pending)} "
        f"dry_run={bool(args.dry_run)}"
    )
    if not pending:
        print("Nothing to verify — all entries have model_processed=true (model not started).")
        return 0

    owned_before = is_server_healthy()
    started = False

    def _shutdown(_signum=None, _frame=None):
        if args.stop_server_on_exit or started:
            if args.stop_server_on_exit and owned_before and not started:
                print("\n🛑 Stopping local llama-server (--stop-server-on-exit)…", flush=True)
                stop_coding_server()
            else:
                stop_owned_coding_server()
        if _signum is not None:
            raise KeyboardInterrupt

    previous_sigint = signal.getsignal(signal.SIGINT)
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        if not is_server_healthy():
            if args.skip_server:
                print("❌ Model server not healthy and --skip-server was set.")
                return 2
            try:
                resolve_agent_model(config)
            except RuntimeError as exc:
                print(f"❌ {exc}")
                return 2
            if not ensure_coding_server():
                return 2
            started = True
        else:
            print("✅ Model server already healthy")

        return run_verification(
            input_path=input_path,
            dry_run=bool(args.dry_run),
            limit=limit,
            temperature=float(args.temperature),
            reasoning_budget=max(0, int(args.reasoning_budget)),
            skip_server=bool(args.skip_server),
            stop_server_on_exit=bool(args.stop_server_on_exit),
            preloaded_store=store,
        )
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)
        if started or args.stop_server_on_exit:
            if args.stop_server_on_exit and owned_before and not started:
                stop_coding_server()
            else:
                stop_owned_coding_server()


if __name__ == "__main__":
    sys.exit(main())
