"""AOSP emulator lifecycle for instrumented Gradle connected tests."""

from __future__ import annotations

import atexit
import os
import re
import shlex
import shutil
import subprocess
import time
from contextlib import AbstractContextManager, contextmanager, nullcontext
from pathlib import Path
from typing import Iterator

from UnitTest_gen.core.config import get_config
from UnitTest_gen.core.io import log_message

_DEVICE_LINE = re.compile(r"^\s*\S+\s+device\s*$", re.MULTILINE)
# Tasks that execute on-device work (connected androidTest / report wrappers that depend on it).
# jacocoAndroidTestReport alone only rebuilds XML from existing .ec — no emulator required.
_DEVICE_TASK = re.compile(
    r"(?:^|:)(?:connected\w*AndroidTest|instrumentedCoverageReport\w*|create\w*CoverageReport)$"
)

_owned_proc: subprocess.Popen | None = None


def reset_emulator_state_for_tests() -> None:
    global _owned_proc
    _owned_proc = None


def gradle_needs_device(tasks: list[str] | None) -> bool:
    for task in tasks or []:
        name = str(task).strip().split()[0]
        if _DEVICE_TASK.search(name):
            return True
    return False


def adb_executable() -> str:
    which = shutil.which("adb")
    if which:
        return which
    root = Path(get_config().aosp_root).expanduser()
    for path in sorted(root.glob("out/host/*/bin/adb")):
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return "adb"


def device_available(*, timeout: float = 5.0) -> bool:
    try:
        result = subprocess.run(
            [adb_executable(), "devices"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    if result.returncode != 0:
        return False
    return bool(_DEVICE_LINE.search(result.stdout or ""))


def aosp_ready() -> bool:
    cfg = get_config()
    if not cfg.auto_start_emulator:
        return False
    root = Path(cfg.aosp_root).expanduser()
    return (root / "build" / "envsetup.sh").is_file()


def instrumented_device_planned() -> bool:
    """True when a device is up or this process can start the AOSP emulator."""
    return device_available() or aosp_ready()


def _emulator_cli_parts() -> list[str]:
    cfg = get_config()
    parts = ["emulator"]
    if cfg.emulator_wipe_data:
        parts.append("-wipe-data")
    extra = cfg.emulator_args.strip()
    if extra:
        parts.extend(shlex.split(extra))
    return parts


def _launch_command() -> str:
    cfg = get_config()
    root = str(Path(cfg.aosp_root).expanduser())
    emu = " ".join(shlex.quote(p) for p in _emulator_cli_parts())
    return (
        f"cd {shlex.quote(root)} && "
        "source build/envsetup.sh >/dev/null && "
        f"lunch {shlex.quote(cfg.emulator_lunch)} >/dev/null && "
        f"exec {emu}"
    )


def format_emulator_prompt_block(*, compile_command: str = "") -> str:
    """Agent guidance: check adb; never launch; compile-only if no device."""
    compile_line = (compile_command or "").strip() or (
        "./gradlew :<module>:compileDevDebugAndroidTestKotlin  "
        "(resolve the real compile*AndroidTestKotlin task for THIS MODULE)"
    )
    return "\n".join(
        [
            "AOSP EMULATOR (androidTest verify)",
            "Python owns emulator start/stop for this tool run — do NOT launch an emulator.",
            "Forbidden: source/envsetup, lunch, `emulator`, AVD/SDK paths, wipe-data, background `&` launches.",
            "Before PIPELINE VERIFY:",
            "1. Run `adb devices`.",
            "2. If any serial is already in `device` state: run the full PIPELINE VERIFY Gradle "
            "command (connected androidTest / instrumentedCoverageReport as listed above).",
            "3. If no device is listed: do NOT wait for or start an emulator. Run compile-only:",
            f"   {compile_line}",
            "   Then stop so Python can continue (device gates stay for the pipeline).",
        ]
    )


def _wait_for_boot(timeout: int) -> bool:
    deadline = time.monotonic() + max(1, timeout)
    adb = adb_executable()
    while time.monotonic() < deadline:
        if not device_available(timeout=8.0):
            time.sleep(3)
            continue
        try:
            boot = subprocess.run(
                [adb, "shell", "getprop", "sys.boot_completed"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            time.sleep(3)
            continue
        if (boot.stdout or "").strip() == "1":
            return True
        time.sleep(3)
    return False


def ensure_running() -> bool:
    """Start the AOSP emulator if needed. True when a device is in ``device`` state.

    Once started by this process, the emulator stays up until ``stop_owned()``
    (generator exit / atexit) — not after each Gradle call.
    """
    global _owned_proc
    if device_available():
        return True
    if _owned_proc is not None and _owned_proc.poll() is None:
        # Still booting from an earlier ensure_running in this process.
        cfg = get_config()
        if _wait_for_boot(cfg.emulator_boot_timeout_seconds):
            return True
        stop_owned()
        return False
    if not aosp_ready():
        log_message(
            "⚠️ Instrumented Gradle needs a device, but AOSP emulator is not configured "
            f"(missing {get_config().aosp_root}/build/envsetup.sh).",
            category="warning",
        )
        return False
    cfg = get_config()
    log_message(
        f"📱 Starting AOSP emulator ({cfg.emulator_lunch}"
        f"{' -wipe-data' if cfg.emulator_wipe_data else ''})…",
        category="info",
    )
    try:
        proc = subprocess.Popen(
            ["bash", "-lc", _launch_command()],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        log_message(f"⚠️ Failed to launch emulator: {exc}", category="warning")
        return False
    _owned_proc = proc
    if not _wait_for_boot(cfg.emulator_boot_timeout_seconds):
        log_message(
            f"⚠️ Emulator did not boot within {cfg.emulator_boot_timeout_seconds}s.",
            category="warning",
        )
        stop_owned()
        return False
    log_message(
        "📱 AOSP emulator booted — kept running until this tool exits.",
        category="info",
    )
    return True


def stop_owned() -> None:
    """Shut down an emulator started by this process (if any)."""
    global _owned_proc
    proc = _owned_proc
    _owned_proc = None
    if proc is None:
        return
    log_message("📱 Stopping AOSP emulator started by this tool run.", category="info")
    try:
        subprocess.run(
            [adb_executable(), "emu", "kill"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    try:
        proc.poll()
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
    except OSError:
        pass


def _atexit_stop() -> None:
    if _owned_proc is not None:
        stop_owned()


atexit.register(_atexit_stop)


@contextmanager
def emulator_session() -> Iterator[None]:
    """Compatibility no-op: emulator stays up for the whole tool process.

    Prefer ``ensure_running()`` before device Gradle and ``stop_owned()`` on
    generator exit. This context manager does not stop the emulator.
    """
    yield


def emulator_session_if_needed(tasks: list[str] | None) -> AbstractContextManager[None]:
    """Return a no-op session when tasks need a device (lifecycle is process-scoped).

    ``run_gradle`` still calls ``ensure_running()`` for device tasks. The owned
    emulator is stopped only via ``stop_owned()`` / atexit at tool exit.
    """
    if gradle_needs_device(tasks) and get_config().auto_start_emulator:
        return emulator_session()
    return nullcontext()
