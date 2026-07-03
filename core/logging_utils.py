# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Records pipeline events and archives generated diagnostic artifacts.
import os
import sys
import zipfile
from datetime import datetime
from pathlib import Path


class LogColor:
    RESET = "\033[0m"
    DIM = "\033[2m"
    INFO = "\033[38;5;51m"
    SUCCESS = "\033[38;5;46m"
    WARNING = "\033[38;5;220m"
    ERROR = "\033[38;5;196m"
    REASONING = "\033[38;5;50m"
    CODE = "\033[38;5;118m"
    CONTEXT = "\033[38;5;208m"
    GRADLE = "\033[38;5;87m"
    FIX = "\033[38;5;202m"


class PipelineLogger:
    def __init__(self, log_path: str, use_color: bool = True):
        self.log_path = os.path.abspath(log_path)
        self.use_color = use_color and sys.stdout.isatty()
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        with open(self.log_path, "w", encoding="utf-8") as f:
            f.write(f"# Test generation log\nStarted: {datetime.now().isoformat()}\n\n")

    def _color(self, category: str) -> str:
        return {
            "info": LogColor.INFO,
            "success": LogColor.SUCCESS,
            "warning": LogColor.WARNING,
            "error": LogColor.ERROR,
            "reasoning": LogColor.REASONING,
            "code": LogColor.CODE,
            "context": LogColor.CONTEXT,
            "gradle": LogColor.GRADLE,
            "fix": LogColor.FIX,
        }.get(category, "")

    def log(self, message: str = "", category: str = "info", end: str = "\n", console: bool = True):
        text = "" if message is None else str(message)
        if console:
            if self.use_color:
                color = self._color(category)
                if color:
                    print(f"{color}{text}{LogColor.RESET}", end=end, flush=True)
                else:
                    print(text, end=end, flush=True)
            else:
                print(text, end=end, flush=True)

        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(text + end)

    def section(self, title: str, category: str = "info", console: bool = True):
        border = "=" * 96
        self.log(f"\n{border}\n{title}\n{border}", category=category, console=console)

    def block(self, title: str, content: str, category: str = "info", console: bool = False):
        self.section(title, category=category, console=console)
        self.log(content or "", category=category, console=console)


ACTIVE_LOGGER = None


def set_active_logger(logger):
    global ACTIVE_LOGGER
    ACTIVE_LOGGER = logger


def log_message(message: str = "", category: str = "info", end: str = "\n", console: bool = True):
    if ACTIVE_LOGGER:
        ACTIVE_LOGGER.log(message, category=category, end=end, console=console)
    else:
        print(message, end=end, flush=True)


def log_section(title: str, category: str = "info", console: bool = True):
    if ACTIVE_LOGGER:
        ACTIVE_LOGGER.section(title, category=category, console=console)
    else:
        if console:
            print(f"\n{title}", flush=True)


def log_block(title: str, content: str, category: str = "info", console: bool = False):
    if ACTIVE_LOGGER:
        ACTIVE_LOGGER.block(title, content, category=category, console=console)
    elif console:
        print(content, flush=True)


def get_pipeline_log_dir() -> str:
    package_dir = str(Path(__file__).resolve().parents[1])
    return os.path.join(package_dir, "log")


def archive_generated_side_files(output_file_path: str, source_file_path: str):
    test_dir = os.path.dirname(output_file_path)
    test_name = os.path.basename(output_file_path)
    pipeline_log_dir = get_pipeline_log_dir()
    os.makedirs(pipeline_log_dir, exist_ok=True)

    candidates = []
    if os.path.isdir(test_dir):
        for file_name in os.listdir(test_dir):
            if not file_name.startswith(test_name + "."):
                continue

            path = os.path.join(test_dir, file_name)
            if os.path.isfile(path) and not path.endswith(".kt"):
                candidates.append(path)

    if not candidates:
        return None

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    source_stem = os.path.basename(source_file_path).replace(".kt", "")
    zip_path = os.path.join(pipeline_log_dir, f"{source_stem}.generated-artifacts-{timestamp}.zip")

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(candidates):
            archive.write(path, arcname=os.path.basename(path))

    for path in candidates:
        try:
            os.remove(path)
        except OSError as exc:
            log_message(f"⚠️ Could not remove archived side file {path}: {exc}", category="warning")

    log_message(f"📦 Archived {len(candidates)} generated side file(s) to {zip_path}", category="success")
    return zip_path
