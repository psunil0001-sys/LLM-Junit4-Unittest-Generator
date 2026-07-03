# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Exposes project file, search, and Gradle operations through MCP stdio tools.
import os
import re
import subprocess
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("gradle-test-tools")


def _allowed_root() -> str:
    root = os.environ.get("MCP_ALLOWED_ROOT") or os.getcwd()
    return os.path.realpath(os.path.abspath(os.path.expanduser(root)))


def _safe_abs(path: str) -> str:
    """
    Resolve a path and ensure the MCP tool cannot read/write outside the
    configured project root. The client sets MCP_ALLOWED_ROOT before startup.
    """
    resolved = os.path.realpath(os.path.abspath(os.path.expanduser(path)))
    root = _allowed_root()

    if resolved != root and not resolved.startswith(root + os.sep):
        raise ValueError(f"Path is outside allowed root: {resolved}")

    return resolved


_IGNORED_PROJECT_DIRS = {".git", ".gradle", "build", ".idea", ".kotlin", "out"}


def _iter_project_source_files(project_root: str, extensions: tuple[str, ...]):
    for root, dirs, files in os.walk(project_root):
        dirs[:] = [directory for directory in dirs if directory not in _IGNORED_PROJECT_DIRS]
        for file_name in files:
            if file_name.endswith(extensions):
                yield os.path.join(root, file_name)


def filter_gradle_errors(output: str) -> str:
    useful = []
    lines = output.splitlines()

    keywords = [
        "e: ",
        "w: ",
        "* What went wrong:",
        "* Try:",
        "> A failure occurred",
        "> Manifest merger failed",
        "> There were failing tests",
        "Caused by:",
        "Unresolved reference",
        "Type mismatch",
        "Cannot access",
        "Overload resolution ambiguity",
        "No value passed for parameter",
        "Too many arguments",
        "None of the following functions",
        "Only safe",
        "Null can not",
        "Expecting",
        "Conflicting declarations",
        "Redeclaration",
        "Cannot infer",
        "Not enough information to infer",
        "Argument type mismatch",
        "Return type mismatch",
        "Unresolved supertypes",
        "Cannot locate tasks",
        "task '",
        "Manifest merger failed",
        "requires a placeholder substitution",
        "Compilation error",
        "There were failing tests",
        "Execution failed for task",
        "FAILED",
        "FAILURE:",
        "BUILD FAILED",
    ]

    context_after_match = 0
    for line in lines:
        if any(k in line for k in keywords):
            useful.append(line)
            if (
                "None of the following functions" in line
                or "Overload resolution ambiguity" in line
                or "Type mismatch" in line
            ):
                context_after_match = 8
            continue

        if context_after_match > 0:
            useful.append(line)
            context_after_match -= 1

    if useful:
        tail = lines[-160:]
        merged = []
        seen = set()

        for line in useful[-300:] + ["", "---- GRADLE OUTPUT TAIL ----"] + tail:
            key = line.strip()
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            merged.append(line)

        return "\n".join(merged)[-30000:]

    return output[-12000:]


@mcp.tool()
def run_gradle(
    project_root: str,
    offline: bool = False,
    tasks: list[str] | None = None,
    gradle_args: list[str] | None = None,
    rerun_tasks: bool = False,
) -> str:
    """
    Run local Gradle tasks from the allowed project root.

    MCP itself does not use internet or open a network server.
    If offline=True, Gradle runs with --offline and uses cached dependencies only.
    """
    project_root = _safe_abs(project_root)
    selected_tasks = tasks or [":common:api:testDevDebugUnitTest", ":common:api:koverXmlReportDevDebug"]

    if not isinstance(selected_tasks, list) or not selected_tasks:
        return "Invalid Gradle task list."

    for task in selected_tasks:
        if not isinstance(task, str) or not task.strip() or any(c.isspace() for c in task):
            return f"Invalid Gradle task: {task!r}"

    extra_args: list[str] = []
    if gradle_args:
        if not isinstance(gradle_args, list):
            return "Invalid Gradle args list."
        for arg in gradle_args:
            if not isinstance(arg, str) or not arg.strip():
                return f"Invalid Gradle arg: {arg!r}"
            extra_args.append(arg.strip())

    command = ["./gradlew"] + selected_tasks + extra_args + ["--no-daemon"]

    if offline:
        command.append("--offline")
    if rerun_tasks:
        command.append("--rerun-tasks")

    header = (
        f"COMMAND: {' '.join(command)}\n"
    )

    try:
        result = subprocess.run(
            command,
            cwd=project_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=900,
        )
        output = result.stdout or ""
        header += f"EXIT_CODE: {result.returncode}\n"
    except subprocess.TimeoutExpired as exc:
        # Capture both stdout and stderr from timeout exception
        output = (exc.stdout or b"") + (exc.stderr or b"")
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        header += "TIMEOUT: Command exceeded 900 second limit\n"
        header += "EXIT_CODE: TIMEOUT\n"
        return header + "Gradle command timed out after 900 seconds.\n" + filter_gradle_errors(output)

    if result.returncode == 0:
        tail = "\n".join(output.splitlines()[-80:])
        return header + "BUILD SUCCESSFUL\n" + tail

    return header + filter_gradle_errors(output)


@mcp.tool()
def read_file(path: str) -> str:
    path = _safe_abs(path)

    with open(path, "r", encoding="utf-8") as f:
        return f.read()


@mcp.tool()
def read_file_range(path: str, start_line: int, end_line: int) -> str:
    """
    Read a small line range with line numbers.
    Useful for repairing the exact failing area instead of reading the whole file.
    """
    path = _safe_abs(path)

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    if not lines:
        return ""

    start = max(1, int(start_line))
    end = min(len(lines), int(end_line))

    if start > end:
        return ""

    return "".join(
        f"{line_no}: {lines[line_no - 1]}"
        for line_no in range(start, end + 1)
    )


@mcp.tool()
def write_file(path: str, content: str) -> str:
    path = _safe_abs(path)

    os.makedirs(os.path.dirname(path), exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

    return f"written: {path}"


@mcp.tool()
def delete_file(path: str) -> str:
    path = _safe_abs(path)

    if not os.path.exists(path):
        return f"not found: {path}"

    if not os.path.isfile(path):
        return f"not a file: {path}"

    os.remove(path)
    return f"deleted: {path}"


@mcp.tool()
def patch_file(path: str, old_text: str, new_text: str) -> str:
    """
    Replace only the first exact occurrence of old_text with new_text.
    """
    path = _safe_abs(path)

    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    if old_text not in content:
        return "old_text not found"

    updated = content.replace(old_text, new_text, 1)

    with open(path, "w", encoding="utf-8") as f:
        f.write(updated)

    return f"patched: {path}"


@mcp.tool()
def search_in_project(project_root: str, query: str, max_results: int = 100) -> str:
    """
    Simple local project search for Kotlin/Java/Gradle/XML/properties files.
    Skips build/.gradle/.git directories.
    """
    project_root = _safe_abs(project_root)
    matches = []

    for path in _iter_project_source_files(
        project_root,
        (".kt", ".kts", ".java", ".xml", ".gradle", ".properties"),
    ):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line_no, line in enumerate(f, 1):
                    if query in line:
                        matches.append(f"{path}:{line_no}: {line.strip()}")
                        if len(matches) >= max_results:
                            return "\n".join(matches)
        except Exception:
            continue

    return "\n".join(matches)


@mcp.tool()
def find_kotlin_declaration(project_root: str, symbol: str, max_results: int = 50) -> str:
    """
    Find likely Kotlin/Java declarations for a symbol.
    This helps avoid guessing types, constructors, methods, enum entries, and fields.
    """
    project_root = _safe_abs(project_root)
    symbol = symbol.strip()

    if not symbol:
        return ""

    symbol_re = re.escape(symbol)
    declaration_patterns = [
        re.compile(rf"\b(?:data\s+|sealed\s+)?class\s+{symbol_re}\b"),
        re.compile(rf"\binterface\s+{symbol_re}\b"),
        re.compile(rf"\bobject\s+{symbol_re}\b"),
        re.compile(rf"\benum\s+class\s+{symbol_re}\b"),
        re.compile(rf"\btypealias\s+{symbol_re}\b"),
        re.compile(rf"\bfun\s+{symbol_re}\b"),
        re.compile(rf"\b(?:const\s+)?(?:val|var)\s+{symbol_re}\b"),
        re.compile(rf"@JvmField\s+(?:val|var)\s+{symbol_re}\b"),
        re.compile(rf"\bpublic\s+(?:class|interface)\s+{symbol_re}\b"),
    ]

    matches = []

    for path in _iter_project_source_files(project_root, (".kt", ".kts", ".java")):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line_no, line in enumerate(f, 1):
                    stripped = line.strip()
                    if any(pattern.search(stripped) for pattern in declaration_patterns):
                        matches.append(f"{path}:{line_no}: {stripped}")
                        if len(matches) >= max_results:
                            return "\n".join(matches)
        except Exception:
            continue

    return "\n".join(matches)


@mcp.tool()
def read_kotlin_file_around_symbol(project_root: str, symbol: str, context_lines: int = 80, max_results: int = 5) -> str:
    """
    Find occurrences of a symbol and return surrounding file ranges.
    This provides verified local context for repairs.
    """
    project_root = _safe_abs(project_root)
    symbol = symbol.strip()

    if not symbol:
        return ""

    blocks = []
    found = 0

    for path in _iter_project_source_files(project_root, (".kt", ".kts", ".java")):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception:
            continue
        for index, line in enumerate(lines, 1):
            if symbol in line:
                start = max(1, index - context_lines)
                end = min(len(lines), index + context_lines)
                block = [f"### {path}:{start}-{end} around symbol {symbol}\n"]
                block.extend(
                    f"{line_no}: {lines[line_no - 1]}"
                    for line_no in range(start, end + 1)
                )
                blocks.append("".join(block))
                found += 1
                break
        if found >= max_results:
            return "\n\n".join(blocks)

    return "\n\n".join(blocks)


if __name__ == "__main__":
    # Local stdio transport. No network server is opened.
    mcp.run(transport="stdio")
