"""Extract/validate/merge generated Kotlin tests; repair tickets; source projection."""

from __future__ import annotations

import re

_TOP_LEVEL_CLASS_RE = re.compile(
    r"(?m)^\s*(?:public\s+)?(?:class|object)\s+([A-Za-z_][A-Za-z0-9_]*)[^{]*\{"
)

def _scan_balanced_body_span(kotlin_code: str, start: int) -> tuple[int, int] | None:
    depth = 1
    index = start
    quote = None
    escaped = False
    line_comment = False
    block_comment_depth = 0

    while index < len(kotlin_code):
        char = kotlin_code[index]
        next_char = kotlin_code[index + 1] if index + 1 < len(kotlin_code) else ""

        if line_comment:
            if char == "\n":
                line_comment = False
            index += 1
            continue

        if block_comment_depth:
            if char == "/" and next_char == "*":
                block_comment_depth += 1
                index += 2
                continue
            if char == "*" and next_char == "/":
                block_comment_depth -= 1
                index += 2
                continue
            index += 1
            continue

        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif quote == '"""' and kotlin_code.startswith('"""', index):
                quote = None
                index += 3
                continue
            elif char == quote:
                quote = None
            index += 1
            continue

        if char == "/" and next_char == "/":
            line_comment = True
            index += 2
            continue

        if char == "/" and next_char == "*":
            block_comment_depth = 1
            index += 2
            continue

        if kotlin_code.startswith('"""', index):
            quote = '"""'
            index += 3
            continue

        if char in ("\"", "'"):
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return start, index

        index += 1

    return None

def iter_top_level_classes(kotlin_code: str) -> list[tuple[str, str, int]]:
    classes: list[tuple[str, str, int]] = []
    for match in _TOP_LEVEL_CLASS_RE.finditer(kotlin_code):
        span = _scan_balanced_body_span(kotlin_code, match.end())
        if not span:
            continue
        classes.append((match.group(1), match.group(0), span[1]))
    return classes

def top_level_test_class_names(kotlin_code: str) -> list[str]:
    return [name for name, _, _ in iter_top_level_classes(kotlin_code)]

import re
from dataclasses import dataclass
from pathlib import Path

from UnitTest_gen.core import io as file_cache

_TEST_ROOT_MARKERS = (
    "src/test/java/",
    "src/test/kotlin/",
    "src/androidTest/java/",
    "src/androidTest/kotlin/",
)
_JUNIT_TEST_RE = re.compile(r"@Test\b")

@dataclass(frozen=True)
class TestFileState:
    path: str
    exists: bool
    has_content: bool

@dataclass
class TestSnapshot:
    existed: bool
    contents: str

    @classmethod
    def read(cls, path: str) -> "TestSnapshot":
        target = Path(path)
        if not target.is_file():
            return cls(False, "")
        return cls(True, file_cache.read_text(path, default=""))

    @classmethod
    def refresh_last_green(cls, path: str) -> "TestSnapshot":
        state = read_test_file_state(path)
        if not state.has_content:
            return cls(state.exists, "")
        text = file_cache.read_text(path, default="")
        if not text and not Path(path).is_file():
            return cls(False, "")
        return cls(True, text)

    def write(self, path: str) -> None:
        target = Path(path)
        if self.existed or self.contents.strip():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(self.contents, encoding="utf-8")
            file_cache.invalidate(path)
        elif target.exists():
            target.unlink()
            file_cache.invalidate(path)

def read_test_file_state(path: str) -> TestFileState:
    """Return whether TARGET exists and has non-whitespace content."""
    target = Path(path)
    if not target.is_file():
        return TestFileState(path=str(target), exists=False, has_content=False)
    text = file_cache.read_text(path, default="")
    return TestFileState(path=str(target), exists=True, has_content=bool(text.strip()))

def text_has_junit_tests(text: str) -> bool:
    """True when Kotlin test source contains at least one ``@Test`` annotation."""
    return bool(_JUNIT_TEST_RE.search(text or ""))

def target_has_junit_tests(path: str) -> bool:
    """True when TARGET path exists and already contains ``@Test`` methods."""
    if not Path(path).is_file():
        return False
    return text_has_junit_tests(file_cache.read_text(path, default=""))

def package_from_test_path(test_path: str) -> str | None:
    """Infer Kotlin package from a standard ``src/test/java|kotlin/...`` layout."""
    normalized = Path(test_path).as_posix()
    for marker in _TEST_ROOT_MARKERS:
        idx = normalized.find(marker)
        if idx < 0:
            continue
        rel = normalized[idx + len(marker) :]
        parts = [part for part in rel.split("/") if part]
        if len(parts) < 2:
            return None
        return ".".join(parts[:-1])
    return None

def class_name_from_test_path(test_path: str) -> str:
    return Path(test_path).stem or "GeneratedTest"

def build_test_skeleton(test_path: str) -> str:
    """Minimal compile-safe JUnit4 shell: package + empty test class matching file stem."""
    class_name = class_name_from_test_path(test_path)
    package = package_from_test_path(test_path)
    if package:
        return f"package {package}\n\nclass {class_name} {{\n}}\n"
    return f"class {class_name} {{\n}}\n"

_HILT_INSTRUMENTED_TAGS = frozenset({
    "hilt_fragment",
    "hilt_entrypoint",
    "hilt_activity",
    "hilt_android_activity",
    "hilt_service",
    "hilt_worker",
})

def _needs_hilt_instrumented(categories: frozenset[str] | set[str] | None, source_code: str) -> bool:
    tags = frozenset(categories or ())
    if tags & _HILT_INSTRUMENTED_TAGS:
        return True
    hay = source_code or ""
    return "@AndroidEntryPoint" in hay or "@HiltAndroidApp" in hay or "@HiltWorker" in hay

def build_instrumented_skeleton(
    test_path: str,
    *,
    categories: frozenset[str] | set[str] | None = None,
    source_code: str = "",
) -> str:
    """Minimal androidTest harness shell with optional Hilt boilerplate."""
    class_name = class_name_from_test_path(test_path)
    package = package_from_test_path(test_path) or ""
    header = f"package {package}\n\n" if package else ""
    if _needs_hilt_instrumented(categories, source_code):
        return (
            f"{header}"
            "import androidx.test.ext.junit.runners.AndroidJUnit4\n"
            "import dagger.hilt.android.testing.HiltAndroidRule\n"
            "import dagger.hilt.android.testing.HiltAndroidTest\n"
            "import org.junit.Rule\n"
            "import org.junit.runner.RunWith\n\n"
            "@HiltAndroidTest\n"
            "@RunWith(AndroidJUnit4::class)\n"
            f"class {class_name} {{\n"
            "    @get:Rule\n"
            "    val hiltRule = HiltAndroidRule(this)\n"
            "}\n"
        )
    return (
        f"{header}"
        "import androidx.test.ext.junit.runners.AndroidJUnit4\n"
        "import org.junit.runner.RunWith\n\n"
        "@RunWith(AndroidJUnit4::class)\n"
        f"class {class_name} {{\n}}\n"
    )

def seed_test_skeleton_if_needed(
    path: str,
    *,
    state: TestFileState | None = None,
    test_layer=None,
    categories: frozenset[str] | set[str] | None = None,
    source_code: str = "",
) -> bool:
    """Write a minimal skeleton when TARGET is missing or whitespace-only.

    Never replaces a file that already contains ``@Test`` (even if state is stale).
    Returns True when a skeleton was written (vendor-neutral — Copilot and Claude both
    supplement via edit on the seeded class).
    """
    current = state or read_test_file_state(path)
    if current.has_content:
        return False
    if target_has_junit_tests(path):
        return False
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    from UnitTest_gen.kotlin.project import is_instrumented_test_path
    from UnitTest_gen.kotlin.layer import TestLayer

    layer = test_layer if test_layer is not None else TestLayer.UNIT
    if layer == TestLayer.INSTRUMENTED or is_instrumented_test_path(path):
        skeleton = build_instrumented_skeleton(
            path, categories=categories, source_code=source_code,
        )
    else:
        skeleton = build_test_skeleton(path)
    target.write_text(skeleton, encoding="utf-8")
    file_cache.invalidate(path)
    return True

from pathlib import Path

from UnitTest_gen.kotlin.analysis import source_referenced_kotlin_object_names
from UnitTest_gen.kotlin.analysis import KotlinFunctionInfo, KotlinStaticAnalysisReport, analyze_kotlin_code

SLICE_SOURCE_CHAR_BUDGET = 12_000
TARGET_EXCERPT_CHAR_BUDGET = 8_000
HEADER_LINE_CAP = 80
HUGE_METHOD_LINES = 80
HUGE_METHOD_RADIUS = 8
MAX_SAME_FILE_CALLEES = 4
_TRUNCATION_FOOTER = "... truncated; view SOURCE only for names not listed above"

def _numbered_span(lines: list[str], start: int, end: int) -> str:
    start = max(1, start)
    end = min(len(lines), end)
    if start > end:
        return ""
    header = f"--- lines {start}-{end} ---"
    body = "\n".join(f"{idx:>5}| {lines[idx - 1]}" for idx in range(start, end + 1))
    return f"{header}\n{body}"

def _merge_windows(anchors: list[int], *, start: int, end: int, radius: int) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for line_no in sorted(anchors):
        lo = max(start, line_no - radius)
        hi = min(end, line_no + radius)
        if spans and lo <= spans[-1][1] + 1:
            spans[-1] = (spans[-1][0], max(spans[-1][1], hi))
        else:
            spans.append((lo, hi))
    return spans

def _class_shape_line(report: KotlinStaticAnalysisReport | None) -> str:
    if report is None or not report.class_shapes:
        return ""
    shape = report.class_shapes[0]
    params = ", ".join(
        f"{param.name}:{param.type_text or '?'}" for param in shape.constructor_params
    ) or "(none)"
    return f"class_shape: {shape.name}({params})"

def _functions_for_slice(
    report: KotlinStaticAnalysisReport,
    *,
    method_names: set[str],
    delta_lines: set[int],
) -> list[KotlinFunctionInfo]:
    chosen: dict[tuple[str, int], KotlinFunctionInfo] = {}
    for line_no in sorted(delta_lines):
        fn = report.function_for_line(line_no, public_only=False)
        if fn is not None:
            chosen[(fn.name, fn.span.start_line)] = fn
    if method_names:
        for fn in report.functions:
            if fn.name in method_names:
                chosen[(fn.name, fn.span.start_line)] = fn
    return sorted(chosen.values(), key=lambda item: item.span.start_line)

def _same_file_callees(
    report: KotlinStaticAnalysisReport,
    slice_fns: list[KotlinFunctionInfo],
) -> list[KotlinFunctionInfo]:
    owners = {fn.name for fn in slice_fns}
    slice_keys = {(fn.name, fn.span.start_line) for fn in slice_fns}
    by_name: dict[str, list[KotlinFunctionInfo]] = {}
    for fn in report.functions:
        by_name.setdefault(fn.name, []).append(fn)
    extras: dict[tuple[str, int], KotlinFunctionInfo] = {}
    for call in report.calls:
        if call.function_name not in owners:
            continue
        for fn in by_name.get(call.name, ()):
            key = (fn.name, fn.span.start_line)
            if key in slice_keys or key in extras:
                continue
            extras[key] = fn
            if len(extras) >= MAX_SAME_FILE_CALLEES:
                return sorted(extras.values(), key=lambda item: item.span.start_line)
    return sorted(extras.values(), key=lambda item: item.span.start_line)

def _method_blocks(
    lines: list[str],
    fn: KotlinFunctionInfo,
    delta_lines: set[int],
) -> list[str]:
    start = fn.span.start_line
    end = fn.span.end_line
    span_len = end - start + 1
    if span_len <= HUGE_METHOD_LINES:
        block = _numbered_span(lines, start, end)
        return [block] if block else []
    anchors = [
        line_no
        for line_no in sorted(delta_lines)
        if start <= line_no <= end
    ]
    if not anchors:
        mid = (start + end) // 2
        anchors = [mid]
    windows = _merge_windows(anchors, start=start, end=end, radius=HUGE_METHOD_RADIUS)
    return [block for lo, hi in windows if (block := _numbered_span(lines, lo, hi))]

def _try_append(chunks: list[str], used: int, block: str, budget: int) -> tuple[int, bool]:
    if not block:
        return used, True
    extra = len(block) + (1 if chunks else 0)
    if used + extra > budget:
        return used, False
    chunks.append(block)
    return used + extra, True

def format_slice_source_projection(
    source_path: str,
    source_code: str,
    *,
    method_names: set[str] | frozenset[str] | tuple[str, ...] = (),
    delta_lines: set[int] | frozenset[int] | tuple[int, ...] = (),
    analysis_report: KotlinStaticAnalysisReport | None = None,
    char_budget: int = SLICE_SOURCE_CHAR_BUDGET,
    include_callees: bool = True,
) -> str:
    """Numbered header + slice methods (+ same-file callees) for one SOURCE file."""
    path = str(Path(source_path).resolve()) if source_path else ""
    lines = (source_code or "").splitlines()
    names = {str(name).strip() for name in method_names if str(name).strip()}
    deltas = set()
    for raw in delta_lines:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            deltas.add(value)

    title = f"SLICE SOURCE (absolute path: {path or '(none)'})"
    if not lines:
        return f"{title}\n(no source available)"

    report = analysis_report
    if report is None and (source_code or "").strip():
        try:
            report = analyze_kotlin_code(source_code, source_path)
        except Exception:
            report = None

    chunks: list[str] = [title]
    used = len(title)
    shape = _class_shape_line(report)
    if shape:
        used, ok = _try_append(chunks, used, shape, char_budget)
        if not ok:
            return "\n".join(chunks) + "\n" + _TRUNCATION_FOOTER

    first_fn_line = min((fn.span.start_line for fn in report.functions), default=len(lines) + 1) if report else len(lines) + 1
    header_end = min(HEADER_LINE_CAP, max(0, first_fn_line - 1), len(lines))
    if header_end >= 1:
        header = _numbered_span(lines, 1, header_end)
        used, ok = _try_append(chunks, used, header, char_budget)
        if not ok:
            return "\n".join(chunks) + "\n" + _TRUNCATION_FOOTER

    slice_fns: list[KotlinFunctionInfo] = []
    leftover: list[str] = []
    if report is not None:
        slice_fns = _functions_for_slice(report, method_names=names, delta_lines=deltas)
        slice_fns.sort(
            key=lambda fn: (
                -sum(1 for line_no in deltas if fn.span.start_line <= line_no <= fn.span.end_line),
                fn.span.start_line,
            )
        )
        ranked = list(slice_fns)
        kept: list[KotlinFunctionInfo] = []
        for index, fn in enumerate(ranked):
            blocks = _method_blocks(lines, fn, deltas)
            label = f"method {fn.name}"
            tentative = "\n".join([label, *blocks]) if blocks else label
            next_used, ok = _try_append(chunks, used, tentative, char_budget)
            if not ok:
                leftover.extend(item.name for item in ranked[index:])
                leftover = list(dict.fromkeys(leftover))
                break
            used = next_used
            kept.append(fn)
        slice_fns = kept

        if include_callees and report is not None:
            for fn in _same_file_callees(report, slice_fns):
                blocks = _method_blocks(lines, fn, deltas)
                tentative = "\n".join([f"callee {fn.name}", *blocks]) if blocks else f"callee {fn.name}"
                used, ok = _try_append(chunks, used, tentative, char_budget)
                if not ok:
                    leftover.append(fn.name)
                    break

    projected = "\n".join(chunks)
    if report is not None:
        objects = source_referenced_kotlin_object_names(source_code, source_path, report)
        listed = [name for name in sorted(objects) if name in projected]
        if listed:
            seam_lines = [
                "EXTERNAL SEAMS (Kotlin object — view that type if you need its API):",
                ", ".join(f"{name}" for name in listed),
            ]
            seam = "\n".join(seam_lines)
            used, ok = _try_append(chunks, used, seam, char_budget)
            if not ok:
                leftover.extend(listed)

    if leftover:
        footer = "remaining methods: " + ", ".join(leftover) + " — view SOURCE if needed"
        used, ok = _try_append(chunks, used, footer, char_budget)
        if not ok:
            chunks.append(_TRUNCATION_FOOTER)
    elif used >= char_budget:
        chunks.append(_TRUNCATION_FOOTER)

    return "\n".join(chunks)

def is_skeleton_test(text: str) -> bool:
    """True when TARGET has no @Test methods yet (blank or pipeline-seeded empty class)."""
    stripped = (text or "").strip()
    if not stripped:
        return True
    if "@Test" in stripped:
        return False
    return "class " in stripped and stripped.count("fun ") == 0

def format_target_excerpt(
    existing_test_code: str,
    test_path: str = "",
    *,
    char_budget: int = TARGET_EXCERPT_CHAR_BUDGET,
) -> str:
    """Head/tail excerpt of TARGET so later slices see fixtures without a full-file read."""
    path = str(Path(test_path).resolve()) if test_path else "(none)"
    header = f"TARGET EXCERPT (absolute path: {path})"
    stripped = (existing_test_code or "").strip()
    if not stripped:
        return (
            f"{header}\n"
            "TARGET is empty or missing — pipeline seeds a minimal skeleton before coder; "
            "after seed the file exists: use edit (not create)."
        )
    if is_skeleton_test(existing_test_code):
        budget = max(120, char_budget)
        body = stripped if len(stripped) <= budget else stripped[: budget - 20] + "\n..."
        return (
            f"{header}\n"
            "TARGET STATUS: pipeline-seeded skeleton (no @Test yet). "
            "File already exists — use edit/apply_patch (or Edit) to fill the class; "
            "do not create (fails: Path already exists).\n"
            f"{body}\n"
        )
    text = existing_test_code
    budget = max(120, char_budget)
    if len(text) <= budget:
        return f"{header}\n{text.rstrip()}\n"
    head = budget // 2
    tail = budget - head - 40
    excerpt = text[:head] + "\n... truncated ...\n" + text[-max(tail, 0) :]
    return f"{header}\n{excerpt.rstrip()}\n"

import re
from dataclasses import dataclass
from functools import lru_cache

from UnitTest_gen.kotlin.validate import catalog_repair_intent as validation_repair_intent
from UnitTest_gen.kotlin.validate import _catalog_repair_intents

VALIDATION_REPAIR_INTENTS = _catalog_repair_intents()  # snapshot for key lookup; YAML is cached

_CODE_PREFIX_RE = re.compile(r"^([a-z][a-z0-9_]*)\s*:\s*(.*)$", re.DOTALL)
_GRADLE_FINGERPRINTS = (
    ("unresolved_reference", re.compile(r"Unresolved reference", re.I)),
    ("type_mismatch", re.compile(r"Type mismatch|Type inference failed", re.I)),
    ("junit_assertion", re.compile(r"AssertionError|ComparisonFailure|expected:|but was:", re.I)),
    ("class_not_found", re.compile(r"ClassNotFoundException|NoClassDefFoundError", re.I)),
    ("mock_verification", re.compile(r"Wanted but not invoked|UnnecessaryStubbing|NeverWantedButInvoked", re.I)),
)
_APOLLO_GRADLE_HINTS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "apollo_http_exception_import",
        re.compile(r"Unresolved reference:\s*ApolloHttpException", re.I),
        "Import com.apollographql.apollo3.exception.ApolloHttpException when asserting that type, "
        "or prefer transport.enqueueThrow(apolloHttp(status)) and assert mapped domain exceptions "
        "(UserAuthException, ClientSideException, …) per Apollo harness recipe.",
    ),
    (
        "apollo_api_error_type",
        re.compile(r"List<kotlin\.Error.*apollo3\.api\.Error", re.I),
        "Use com.apollographql.apollo3.api.Error (fully qualified) for GraphQL errors, "
        "or avoid hand-built GraphQL error tests — use QueueNetworkTransport.enqueueThrow / enqueueData.",
    ),
    (
        "apollo_error_ctor",
        re.compile(r"Cannot find a parameter with this name:\s*message.*Error", re.I),
        "Do not construct kotlin.Error or guessed Apollo Error(message=…) ctors. "
        "Read ApolloTestHarness.kt and apollo_3_8_2_api_index.json; prefer enqueueThrow(apolloHttp(n)).",
    ),
    (
        "apollo_operation_mock",
        re.compile(r"mock\s*\(\s*Operation::class", re.I),
        "Do not mock Operation or hand-roll ApolloResponse for execute() tests. "
        "Use QueueNetworkTransport.enqueueData / enqueueThrow(apolloHttp(n)) from the project harness.",
    ),
    (
        "apollo_parse_exception_args",
        re.compile(r"ApolloParseException\([^)]*String[^)]*String", re.I),
        "ApolloParseException(message, cause) expects Throwable? as the second argument, not String. "
        "Use ApolloParseException() or enqueueThrow(ApolloParseException()) via the harness.",
    ),
)

def _apollo_gradle_tickets(gradle_output: str) -> list[RepairTicket]:
    text = gradle_output or ""
    if not text.strip():
        return []
    if not re.search(r"apollo|ApolloHttpException|apollo3", text, re.I):
        return []
    tickets: list[RepairTicket] = []
    seen: set[str] = set()
    for code, pattern, hint in _APOLLO_GRADLE_HINTS:
        if not pattern.search(text) or code in seen:
            continue
        seen.add(code)
        tickets.append(
            RepairTicket(
                code=code,
                message=pattern.search(text).group(0).strip(),
                source="gradle",
                hint=hint,
            )
        )
    return tickets

@dataclass(frozen=True)
class RepairTicket:
    code: str
    message: str
    severity: str = "error"
    source: str = "static"  # static | gradle | plan
    lines: tuple[int, ...] = ()
    hint: str = ""

    def format_line(self) -> str:
        parts = [f"[{self.source}/{self.code}] {self.message.strip()}"]
        if self.hint and self.hint.strip() and self.hint.strip() not in self.message:
            parts.append(f"hint: {self.hint.strip()}")
        if self.lines:
            parts.append(f"lines: {list(self.lines)}")
        return " | ".join(parts)

@lru_cache
def _known_codes() -> frozenset[str]:
    return frozenset(VALIDATION_REPAIR_INTENTS.keys())

def parse_validation_issue(issue: str) -> RepairTicket:
    """Map a static-validation string to a RepairTicket with a stable code."""
    text = (issue or "").strip()
    if not text:
        return RepairTicket(code="empty_issue", message="(empty)", source="static")
    match = _CODE_PREFIX_RE.match(text)
    if match and match.group(1) in _known_codes():
        code = match.group(1)
        message = match.group(2).strip() or text
        return RepairTicket(
            code=code,
            message=message,
            source="static",
            hint=validation_repair_intent(code),
        )
    # Heuristic: first snake_case token before colon even if not in catalog.
    if match:
        return RepairTicket(
            code=match.group(1),
            message=match.group(2).strip() or text,
            source="static",
            hint=validation_repair_intent(match.group(1)),
        )
    return RepairTicket(code="static_issue", message=text, source="static")

def tickets_from_validation_issues(issues: list[str] | tuple[str, ...] | None) -> list[RepairTicket]:
    return [parse_validation_issue(item) for item in (issues or ()) if str(item).strip()]

def tickets_from_gradle_output(gradle_output: str) -> list[RepairTicket]:
    text = (gradle_output or "").strip()
    if not text:
        return []
    tickets = _apollo_gradle_tickets(text)
    codes: list[str] = []
    for code, pattern in _GRADLE_FINGERPRINTS:
        if pattern.search(text):
            codes.append(code)
    if not codes:
        codes = ["gradle_failure"]
    snippet = text if len(text) <= 800 else text[:400] + "\n...\n" + text[-350:]
    generic_hint = "Fix the compile/test failure in TARGET without changing plan scope."
    if tickets:
        tickets.append(
            RepairTicket(
                code="gradle_failure",
                message=snippet,
                source="gradle",
                hint=generic_hint,
            )
        )
        return tickets
    return [
        RepairTicket(
            code=code,
            message=snippet if i == 0 else f"Also matched fingerprint: {code}",
            source="gradle",
            hint=generic_hint,
        )
        for i, code in enumerate(codes)
    ]

def build_repair_tickets(
    *,
    validation_issues: list[str] | tuple[str, ...] | None = None,
    gradle_output: str = "",
    plan_issues: list[str] | tuple[str, ...] | None = None,
) -> list[RepairTicket]:
    tickets = tickets_from_validation_issues(validation_issues)
    if gradle_output.strip():
        tickets.extend(tickets_from_gradle_output(gradle_output))
    for issue in plan_issues or ():
        text = str(issue).strip()
        if not text:
            continue
        match = _CODE_PREFIX_RE.match(text)
        if match:
            tickets.append(
                RepairTicket(
                    code=match.group(1),
                    message=match.group(2).strip() or text,
                    source="plan",
                    hint=match.group(2).strip(),
                )
            )
        else:
            tickets.append(RepairTicket(code="plan_issue", message=text, source="plan"))
    return tickets

def format_tickets_for_prompt(tickets: list[RepairTicket] | tuple[RepairTicket, ...] | None) -> str:
    rows = list(tickets or ())
    if not rows:
        return "(none)"
    return "\n".join(f"- {ticket.format_line()}" for ticket in rows)

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from UnitTest_gen.core.gradle import filter_gradle_errors, summarize_gradle_output

_FIX_ERROR_CHAR_CAP = 12_000
_JUNIT_CHAR_CAP = 4_000
_MAX_JUNIT_FAILURES = 5
_MSG_SLICE = 800
_STACK_BODY_LINES = 8
_STACK_KT = re.compile(r"\bat\s+\S+\((\w+\.kt):(\d+)\)")
_NO_MATCH_RE = re.compile(r"no\s+match\s+found", re.IGNORECASE)

def count_edit_no_match(text: str) -> int:
    """Count Copilot edit failures that report 'No match found'."""
    if not text:
        return 0
    return len(_NO_MATCH_RE.findall(text))

def _junit_xml_failures(project_root: str, test_path: str, *, max_failures: int = _MAX_JUNIT_FAILURES) -> str:
    """Pull failure messages from module test-results XML for the target *Test.kt class."""
    if not project_root or not test_path:
        return ""
    try:
        from UnitTest_gen.kotlin.project import find_owning_module_dir
    except ImportError:
        return ""

    module_dir = find_owning_module_dir(project_root, test_path)
    if not module_dir:
        return ""
    results_root = Path(module_dir) / "build" / "test-results"
    if not results_root.is_dir():
        return ""

    class_stem = Path(test_path).stem
    rows: list[str] = []
    for xml_path in sorted(results_root.rglob("TEST-*.xml")):
        if class_stem not in xml_path.name:
            continue
        try:
            root = ET.parse(xml_path).getroot()
        except (OSError, ET.ParseError):
            continue
        for case in root.iter("testcase"):
            failure = case.find("failure")
            if failure is None:
                failure = case.find("error")
            if failure is None:
                continue
            name = case.attrib.get("name") or "?"
            msg = (failure.attrib.get("message") or "").strip()
            body = (failure.text or "").strip()
            stack_hit = ""
            stack_preview: list[str] = []
            for line in body.splitlines():
                stripped = line.strip()
                if stripped and len(stack_preview) < _STACK_BODY_LINES:
                    stack_preview.append(stripped)
                match = _STACK_KT.search(line)
                if match and not stack_hit:
                    stack_hit = f"{match.group(1)}:{match.group(2)}"
            bit = f"FAILED: {name}"
            if msg:
                bit += f" — {msg[:_MSG_SLICE]}"
            if stack_hit:
                bit += f" (at {stack_hit})"
            if stack_preview:
                bit += "\n  " + "\n  ".join(stack_preview)
            rows.append(bit)
            if len(rows) >= max_failures:
                break
        if len(rows) >= max_failures:
            break
    if not rows:
        return ""
    return "\n".join(rows)[:_JUNIT_CHAR_CAP]

def format_fix_error_context(
    gradle_output: str,
    *,
    project_root: str = "",
    test_path: str = "",
    max_chars: int = _FIX_ERROR_CHAR_CAP,
) -> str:
    """Verbatim compile/test errors for the fix prompt (not a TTY summary)."""
    sections: list[str] = []

    junit = _junit_xml_failures(project_root, test_path)
    if junit.strip():
        sections.append("JUNIT FAILURES\n" + junit.strip())

    summary = summarize_gradle_output(gradle_output or "")
    if summary.strip():
        # Keep only status-ish lines from the compact summary (not full Errors: dump).
        status_lines = [
            line
            for line in summary.splitlines()
            if line.startswith(("COMMAND:", "EXIT_CODE:", "BUILD ", "Tasks:"))
        ]
        if status_lines:
            sections.append("STATUS\n" + "\n".join(status_lines))

    verbatim = filter_gradle_errors(gradle_output or "", max_chars=max_chars)
    if verbatim.strip():
        sections.append(verbatim.strip())

    if not sections:
        return "(empty)"
    return "\n\n".join(sections)[:max_chars]

from collections import Counter

from UnitTest_gen.core.config import get_config
from UnitTest_gen.core.io import log_message
from UnitTest_gen.kotlin.analysis import analyze_kotlin_test_code

def validate_generated_test_code(
    test_code: str,
    output_file_path: str,
    source_code: str = "",
    *,
    opportunity_plan=None,
    existing_test_code: str = "",
):
    """Fast local guardrails before Gradle."""
    issues = []
    if not output_file_path.endswith(".kt"):
        issues.append("Generated test output must be a .kt file.")
    top_lines = [line.strip() for line in test_code.splitlines()[:5]]
    if not any(line.startswith("package ") for line in top_lines):
        issues.append("Generated test is missing a package declaration near the top of the file.")

    from UnitTest_gen.kotlin.prompts import FORBIDDEN_LOCAL_JVM_TEST_SYMBOLS
    from UnitTest_gen.kotlin.project import is_instrumented_test_path

    instrumented = is_instrumented_test_path(output_file_path)
    if not instrumented:
        for forbidden in FORBIDDEN_LOCAL_JVM_TEST_SYMBOLS:
            if forbidden in test_code:
                issues.append(f"Forbidden import or symbol for local JUnit4 JVM tests: {forbidden}")
        if "androidx.test.ext.junit.runners.AndroidJUnit4" in test_code:
            issues.append(
                "unit_forbidden_android_junit4: AndroidJUnit4 belongs in androidTest, not src/test"
            )
    elif "RobolectricTestRunner" in test_code:
        issues.append(
            "instrumented_forbidden_robolectric: @RunWith(RobolectricTestRunner) not allowed in androidTest"
        )

    if not get_config().enable_guardrails:
        return issues

    test_report = analyze_kotlin_test_code(test_code or "", source_code or "")
    issues.extend(test_report.validation_errors)
    from UnitTest_gen.kotlin.validate import collect_validation_rule_issues

    issues.extend(
        collect_validation_rule_issues(
            test_code, output_file_path, source_code, test_report, existing_test_code=existing_test_code,
        )
    )
    del opportunity_plan
    return reconcile_validation_issues(
        test_code, source_code, output_file_path, _dedupe_validation_issues(issues),
    )

def reconcile_validation_issues(
    test_code: str,
    source_code: str,
    output_file_path: str,
    issues: list[str],
) -> list[str]:
    """Drop validation issues the current TARGET file already satisfies (feedback from file state)."""
    if not issues:
        return []

    from UnitTest_gen.kotlin.validate import invalid_suspend_call_context_issues
    from UnitTest_gen.kotlin.validate import (
        WHENEVER_IMPORT_MESSAGE,
        has_mockito_kotlin_whenever,
        mockito_whenever_import_issue,
        uses_bare_mockito_kotlin_whenever,
    )

    suspend_still_open = bool(
        invalid_suspend_call_context_issues(test_code, source_code, output_file_path)
    )
    whenever_still_open = mockito_whenever_import_issue(test_code)

    kept: list[str] = []
    cleared: list[str] = []
    for issue in issues:
        if WHENEVER_IMPORT_MESSAGE in issue or "must import org.mockito.kotlin.whenever" in issue:
            if not whenever_still_open:
                if uses_bare_mockito_kotlin_whenever(test_code) and has_mockito_kotlin_whenever(test_code):
                    cleared.append("whenever import satisfied by TARGET imports")
                else:
                    cleared.append("whenever import rule no longer applies")
                continue
        if issue.startswith("invalid_suspend_call_context:") and not suspend_still_open:
            cleared.append("suspend call context satisfied in TARGET")
            continue
        kept.append(issue)

    if cleared:
        log_message(
            "✅ Post-validation cleared stale issue(s) from current TARGET: " + "; ".join(cleared),
            category="info",
        )
    return kept

def _dedupe_validation_issues(issues: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for issue in issues or []:
        if issue not in seen:
            seen.add(issue)
            ordered.append(issue)
    return ordered

def validation_issues_added(
    base_test_code: str, candidate_test_code: str, output_file_path: str, source_code: str = "",
) -> list[str]:
    """Return only validation issues introduced by the candidate."""
    base_counts = Counter(validate_generated_test_code(base_test_code, output_file_path, source_code=source_code))
    candidate_counts = Counter(
        validate_generated_test_code(candidate_test_code, output_file_path, source_code=source_code)
    )
    return [issue for issue, count in candidate_counts.items() for _ in range(max(0, count - base_counts.get(issue, 0)))]
