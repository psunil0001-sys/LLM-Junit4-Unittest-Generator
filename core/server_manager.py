# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Starts, monitors, and stops local model server processes.
import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from UnitTest_gen.core.pipeline_config import env_flag, get_config


PACKAGE_DIR = str(Path(__file__).resolve().parents[1])
DEFAULT_SERVER_LOG_DIR = os.path.join(PACKAGE_DIR, "log")
DEFAULT_LLAMA_CPP_CWD = os.environ.get(
    "LLAMA_CPP_CWD",
    os.environ.get("TESTGEN_LLAMA_CPP_CWD", str(Path.home() / "llama.cpp"))
)
DEFAULT_MODEL_DIR = Path(os.environ.get("TESTGEN_MODEL_DIR", str(Path.home() / "models")))
DEFAULT_CODING_MODEL_PATH = os.environ.get(
    "TESTGEN_CODING_MODEL_PATH",
    str(DEFAULT_MODEL_DIR / "Qwen3.6-35B-A3B-UD-Q8_K_XL.gguf"),
)
DEFAULT_EMBEDDING_MODEL_PATH = os.environ.get(
    "TESTGEN_EMBEDDING_MODEL_PATH",
    str(DEFAULT_MODEL_DIR / "nomic-embed-text-v1.5.Q6_K.gguf"),
)
DEFAULT_SLOT_SAVE_PATH = os.environ.get(
    "TESTGEN_LLAMA_SLOT_SAVE_PATH",
    str(DEFAULT_MODEL_DIR / "server_slots"),
)
# Sampler flags in the server command are startup defaults. The generator sends
# request-level overrides for temperature/top_p/min_p/top_k/presence_penalty/
# repeat_penalty on every chat call. --threads-batch 12 \\
# TESTGEN_ENABLE_THINKING controls both request-side thinking fields and the
# default managed llama-server reasoning flags.

#----------------command for qwen3.6 a35b model------------------------------#

def build_default_coding_server_command(enable_thinking: bool | None = None) -> str:
    use_thinking = get_config().enable_thinking if enable_thinking is None else bool(enable_thinking)
    lines = [
        "./build/bin/llama-server",
        f"   -m {shlex.quote(DEFAULT_CODING_MODEL_PATH)}",
        "   --ctx-size 65536",
        "   --n-predict 24576",
        "   -fitt 768",
        "   --flash-attn on",
        "   --parallel 1",
        "   --no-mmap",
        "   --mlock",
        "   --no-warmup",
        "   --threads 12",
        "   --cont-batching",
        "   --timeout 300",
        "   --port 8080",
        "   --host 127.0.0.1",
        "   --jinja",
        "   --metrics",
    ]
    if use_thinking:
        lines.extend([
            "   --reasoning on",
            "   --reasoning-format deepseek",
        ])
    lines.extend([
        "   --spec-type draft-mtp",
        "   --spec-draft-n-max 2",
        f"   --slot-save-path {shlex.quote(DEFAULT_SLOT_SAVE_PATH)}",
    ])
    return " \\\n".join(lines)


DEFAULT_CODING_SERVER_COMMAND = build_default_coding_server_command()


#----------------command for qwen3 coder 30b model------------------------------#

# DEFAULT_CODING_SERVER_COMMAND = f"""./build/bin/llama-server \\
#    -m {shlex.quote(DEFAULT_CODING_MODEL_PATH)} \\
#    --ctx-size 24576 \\
#    --n-predict 8192 \\
#    --no-mmap \\
#    --flash-attn on \\
#    --parallel 1 \\
#    --n-gpu-layers auto \\
#    --threads 12 \\
#    --cont-batching \\
#    --timeout 300 \\
#    --port 8080 \\
#    --host 127.0.0.1 \\
#    --jinja \\
#    --metrics \\
#    --slot-save-path {shlex.quote(DEFAULT_SLOT_SAVE_PATH)}"""

DEFAULT_EMBEDDING_SERVER_COMMAND = f"""./build/bin/llama-server \\
  -m {shlex.quote(DEFAULT_EMBEDDING_MODEL_PATH)} \\
  --embedding \\
  --ctx-size 8192 \\
  --batch-size 2048 \\
  --ubatch-size 2048 \\
  --rope-scaling yarn \\
  --rope-freq-scale 0.75 \\
  --port 8081 \\
  --host 127.0.0.1"""


def resolve_cwd(path_value: str | None, project_root: str) -> str:
    if not path_value:
        return project_root

    path_value = os.path.expanduser(path_value)
    if os.path.isabs(path_value):
        return os.path.abspath(path_value)

    return os.path.abspath(os.path.join(project_root, path_value))


def normalize_server_command(command: str) -> str:
    normalized = (command or "").strip()
    while normalized.endswith("\\"):
        normalized = normalized[:-1].rstrip()
    return normalized


def ensure_slot_save_path(command: str) -> None:
    normalized = re.sub(r"\\\s*\n", " ", command or "")
    try:
        shell_words = shlex.split(normalized)
    except ValueError:
        return

    if "--slot-save-path" not in shell_words:
        return

    index = shell_words.index("--slot-save-path")
    if index + 1 >= len(shell_words):
        return

    os.makedirs(os.path.expanduser(shell_words[index + 1]), exist_ok=True)


@dataclass
class ManagedServer:
    name: str
    command: str
    cwd: str
    process: subprocess.Popen
    script_path: str
    pid_path: str
    log_path: str


class LocalServerManager:
    """
    Starts optional llama.cpp servers for this generator run.

    The manager only stops servers that it launched. If a server was already
    running before the generator started, it is treated as external state and
    left untouched.
    """

    def __init__(
        self,
        project_root: str,
        coding_command: str | None = None,
        coding_cwd: str | None = None,
        embedding_command: str | None = None,
        embedding_cwd: str | None = None,
        startup_timeout_seconds: int | None = None,
        use_terminal: bool | None = None,
    ):
        self.project_root = os.path.abspath(os.path.expanduser(project_root))
        self.coding_command = (
            coding_command
            or os.environ.get("TESTGEN_CODING_SERVER_COMMAND")
            or DEFAULT_CODING_SERVER_COMMAND
        )
        self.coding_command = normalize_server_command(self.coding_command)
        self.embedding_command = (
            embedding_command
            or os.environ.get("TESTGEN_EMBEDDING_SERVER_COMMAND")
            or DEFAULT_EMBEDDING_SERVER_COMMAND
        )
        self.embedding_command = normalize_server_command(self.embedding_command)
        self.coding_cwd = resolve_cwd(
            coding_cwd or os.environ.get("TESTGEN_CODING_SERVER_CWD") or DEFAULT_LLAMA_CPP_CWD,
            self.project_root,
        )
        self.embedding_cwd = resolve_cwd(
            embedding_cwd or os.environ.get("TESTGEN_EMBEDDING_SERVER_CWD") or DEFAULT_LLAMA_CPP_CWD,
            self.project_root,
        )
        self.startup_timeout_seconds = startup_timeout_seconds or int(
            os.environ.get("TESTGEN_SERVER_STARTUP_TIMEOUT", "120")
        )
        self.use_terminal = env_flag("TESTGEN_SERVER_TERMINAL", "1") if use_terminal is None else use_terminal
        self.log_dir = os.path.abspath(
            os.path.expanduser(os.environ.get("TESTGEN_SERVER_LOG_DIR", DEFAULT_SERVER_LOG_DIR))
        )
        self.coding_server: ManagedServer | None = None
        self.embedding_server: ManagedServer | None = None

    def has_coding_command(self) -> bool:
        return bool(self.coding_command and self.coding_command.strip())

    def has_embedding_command(self) -> bool:
        return bool(self.embedding_command and self.embedding_command.strip())

    def start_coding_server(self):
        if self.coding_server:
            return
        if not self.has_coding_command():
            raise RuntimeError(
                "Coding server command is not configured. Set TESTGEN_CODING_SERVER_COMMAND "
                "or pass --coding-server-command."
            )
        self.coding_server = self._start("coding", self.coding_command, self.coding_cwd)

    def start_embedding_server(self):
        if self.embedding_server:
            return
        if not self.has_embedding_command():
            raise RuntimeError(
                "Embedding server command is not configured. Set TESTGEN_EMBEDDING_SERVER_COMMAND "
                "or pass --embedding-server-command."
            )
        self.embedding_server = self._start("embedding", self.embedding_command, self.embedding_cwd)

    def stop_embedding_server_if_started(self):
        if self.embedding_server:
            self._stop(self.embedding_server)
            self.embedding_server = None

    def stop_coding_server_if_started(self):
        if self.coding_server:
            self._stop(self.coding_server)
            self.coding_server = None

    def stop_all_started_servers(self):
        self.stop_embedding_server_if_started()
        self.stop_coding_server_if_started()

    def wait_until(self, label: str, checker) -> bool:
        server = self._server_for_label(label)
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

    def _start(self, name: str, command: str, cwd: str) -> ManagedServer:
        if not os.path.isdir(cwd):
            raise RuntimeError(f"{name} server working directory does not exist: {cwd}")
        ensure_slot_save_path(command)
        os.makedirs(self.log_dir, exist_ok=True)
        log_path = os.path.join(self.log_dir, f"{name}_server.log")

        if self.use_terminal:
            terminal_command = self._terminal_command()
            if not terminal_command:
                print(
                    "⚠️ No terminal emulator/display found. Starting server in background instead. "
                    "Set TESTGEN_SERVER_TERMINAL=0 to make this explicit."
                )
                script_path, pid_path = self._write_script(name, command, cwd, log_path, mirror_output=False)
                process = subprocess.Popen(["bash", script_path], start_new_session=True)
            else:
                script_path, pid_path = self._write_script(name, command, cwd, log_path, mirror_output=True)
                process = subprocess.Popen(terminal_command(script_path), start_new_session=True)
        else:
            script_path, pid_path = self._write_script(name, command, cwd, log_path, mirror_output=False)
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

    def _write_script(self, name: str, command: str, cwd: str, log_path: str, mirror_output: bool) -> tuple[str, str]:
        fd, path = tempfile.mkstemp(prefix=f"testgen_{name}_server_", suffix=".sh")
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
            script.write(f"echo '[testgen] starting {name} server at '$(date -Is)\n")
            script.write(f"echo '[testgen] cwd: {cwd}'\n")
            script.write("echo '[testgen] command:'\n")
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

        custom_terminal = os.environ.get("TESTGEN_TERMINAL_COMMAND")
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

    def _stop(self, server: ManagedServer):
        print(f"🛑 Stopping {server.name} server started by this run...")
        server_pid = self._read_pid(server.pid_path)
        if server_pid:
            self._terminate_process_group(
                server_pid,
                f"{server.name} llama-server",
            )

        launcher_pid = server.process.pid
        if launcher_pid and server.process.poll() is None:
            self._terminate_process_group(
                launcher_pid,
                f"{server.name} launcher",
            )

        try:
            server.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            if launcher_pid:
                self._kill_process_group(launcher_pid, f"{server.name} launcher")

        try:
            os.remove(server.script_path)
        except OSError:
            pass

        try:
            os.remove(server.pid_path)
        except OSError:
            pass

    def _read_pid(self, pid_path: str) -> int | None:
        try:
            with open(pid_path, "r", encoding="utf-8") as pid_file:
                value = pid_file.read().strip()
            return int(value) if value else None
        except (OSError, ValueError):
            return None

    def _terminate_process_group(self, pid: int, label: str):
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

    def _kill_process_group(self, pid: int, label: str):
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except Exception as exc:
            print(f"Could not kill {label} process group: {exc}")

    def _server_for_label(self, label: str) -> ManagedServer | None:
        if label == "coding":
            return self.coding_server
        if label == "embedding":
            return self.embedding_server
        return None

    def _print_log_tail(self, log_path: str, max_lines: int = 40):
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
