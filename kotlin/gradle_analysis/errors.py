# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Gradle/Kotlin/JUnit failure parsing and causal repair planning (errors).
"""Gradle/Kotlin/JUnit failure parsing and causal repair planning — errors submodule."""

# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Parses and groups Gradle, Kotlin compiler, and JUnit failures.
import json
import re
from dataclasses import dataclass, field



@dataclass
class ErrorEntry:
    group_key: str
    category: str
    symbols: list[str]
    files: list[str]
    lines: list[int]
    evidence: list[str]


@dataclass
class RepairRootCause:
    category: str
    primary_key: str
    primary_evidence: list[str]
    cascade_entries: list[ErrorEntry] = field(default_factory=list)
    peer_entries: list[ErrorEntry] = field(default_factory=list)
    confidence: float = 0.5
    recommended_action: str = ""


@dataclass(frozen=True)
class HiltBindingKey:
    qualifier: str | None
    type_name: str

    @property
    def display(self) -> str:
        return f"{self.qualifier} {self.type_name}" if self.qualifier else self.type_name


@dataclass(frozen=True)
class HiltBindingIssue:
    classification: str
    requested_key: HiltBindingKey | None
    available_keys: tuple[HiltBindingKey, ...] = ()
    cascade_lines: tuple[str, ...] = ()
    action: str = ""

    @property
    def is_qualifier_mismatch(self) -> bool:
        return self.classification == "QUALIFIER_MISMATCH"


ROOT_CAUSE_CATEGORY_PRIORITY = {
    "syntax_or_file_shape": 10,
    "missing_import_or_dependency": 20,
    "unresolved_reference": 30,
    "api_signature_mismatch": 40,
    "type_mismatch": 50,
    "mockito_misuse": 55,
    "fixture_strategy": 60,
    "mocking_strategy": 70,
    "runtime_lifecycle": 80,
    "assertion_behavior": 90,
    "coverage_strategy": 100,
    "unknown": 110,
}


def _normalize_hilt_type(type_name: str) -> str:
    return re.sub(r"\s+", "", (type_name or "").strip())


def _parse_hilt_qualifier(raw_qualifier: str | None) -> str | None:
    if not raw_qualifier:
        return None
    named = re.search(r"@(?:javax\.inject\.)?Named\(\"([^\"]+)\"\)", raw_qualifier)
    if named:
        return f'@Named("{named.group(1)}")'
    return raw_qualifier.strip()


def classify_hilt_missing_binding_issue(gradle_output: str) -> HiltBindingIssue | None:
    """
    Classify Dagger/Hilt MissingBinding output by binding key, not by text only.
    A same-type qualifier mismatch is a production DI contract issue for a
    per-file test generator, so repair must stop instead of inventing bindings.
    """
    if not gradle_output or "[Dagger/MissingBinding]" not in gradle_output:
        return None

    requested_match = re.search(
        r"\[Dagger/MissingBinding]\s+(?P<key>(?:@[^\s]+\s+)?[A-Za-z0-9_.$<>?]+)\s+cannot be provided",
        gradle_output,
    )
    requested_key = None
    if requested_match:
        raw_key = requested_match.group("key").strip()
        qualifier_match = re.match(r"(?P<qualifier>@[^\s]+)\s+(?P<type>[A-Za-z0-9_.$<>?]+)$", raw_key)
        if qualifier_match:
            requested_key = HiltBindingKey(
                _parse_hilt_qualifier(qualifier_match.group("qualifier")),
                _normalize_hilt_type(qualifier_match.group("type")),
            )
        else:
            requested_key = HiltBindingKey(None, _normalize_hilt_type(raw_key))

    available_keys: list[HiltBindingKey] = []
    for match in re.finditer(
        r"(?P<qualifier>@(?:javax\.inject\.)?Named\(\"[^\"]+\"\)\s+)?(?P<type>[A-Za-z0-9_.$<>?]+)\s+is provided at:",
        gradle_output,
    ):
        key = HiltBindingKey(
            _parse_hilt_qualifier(match.group("qualifier")),
            _normalize_hilt_type(match.group("type")),
        )
        if key not in available_keys:
            available_keys.append(key)

    cascade_lines = tuple(
        dict.fromkeys(
            line.strip()
            for line in gradle_output.splitlines()
            if "DaggerDefault_HiltComponents_" in line or "cannot find symbol" in line
        )
    )

    if requested_key and any(
        available.type_name == requested_key.type_name
        and available.qualifier != requested_key.qualifier
        for available in available_keys
    ):
        return HiltBindingIssue(
            classification="QUALIFIER_MISMATCH",
            requested_key=requested_key,
            available_keys=tuple(available_keys),
            cascade_lines=cascade_lines,
            action=(
                "Stop generated-test repair. The Hilt graph requests one binding key but production provides "
                "the same type with a different qualifier; this is a production DI contract issue or requires "
                "a deliberate graph-wide test binding outside per-file generation."
            ),
        )

    if requested_key:
        return HiltBindingIssue(
            classification="MISSING_BINDING",
            requested_key=requested_key,
            available_keys=tuple(available_keys),
            cascade_lines=cascade_lines,
            action="Resolve verified dependency/binding visibility before generated-test repair.",
        )

    return None


def format_hilt_binding_issue(issue: HiltBindingIssue) -> str:
    lines = [
        f"classification: {issue.classification}",
        f"requested_key: {issue.requested_key.display if issue.requested_key else 'unknown'}",
    ]
    if issue.available_keys:
        lines.append("available_similar_keys:")
        lines.extend(f"- {key.display}" for key in issue.available_keys)
    if issue.cascade_lines:
        lines.append("cascade_errors:")
        lines.extend(f"- {line}" for line in issue.cascade_lines[:8])
    lines.extend(
        [
            "fixture_required: true",
            "fallback: defer_test_and_report_graph_not_closed",
            f"action: {issue.action}",
            "blocked_repairs: @BindValue, @Provides, @TestInstallIn, local fake HiltTestActivity, reflection injection, removing Hilt from attached lifecycle tests",
        ]
    )
    return "\n".join(lines)

def extract_first_gradle_error(gradle_output: str) -> str:
    if not gradle_output:
        return "No Gradle output captured."

    patterns = [
        r"(?s)e: .*?(?=\ne: |\nFAILURE: |\nBUILD FAILED|\Z)",
        r"(?s)Cannot locate tasks .*?(?=\n\n|\nFAILURE: |\nBUILD FAILED|\Z)",
        r"(?s)Manifest merger failed .*?(?=\n\n|\nFAILURE: |\nBUILD FAILED|\Z)",
        r"(?s)requires a placeholder substitution.*?(?=\n\n|\nFAILURE: |\nBUILD FAILED|\Z)",
        r"(?s)> Task .*? FAILED.*?(?=\n> Task |\nFAILURE: |\nBUILD FAILED|\Z)",
        r"(?s)FAILURE: Build failed with an exception\..*?(?=\nBUILD FAILED|\Z)",
        r"(?s)There were failing tests\..*?(?=\nBUILD FAILED|\Z)",
        r"(?s)\w+Test\s*>.*?FAILED.*?(?=\n\w+Test\s*>|\nFAILURE: |\nBUILD FAILED|\Z)",
    ]

    for pattern in patterns:
        match = re.search(pattern, gradle_output)
        if match:
            return match.group(0).strip()[-6000:]

    return gradle_output[-6000:]


APOLLO_GENERATED_INPUT_SYMBOLS = {
    "CreateTripInput",
    "MergeTripsInput",
    "TripsInput",
    "DemergeTripInput",
    "UpdateTripCategoryInput",
    "DeleteTripInput",
    "DeleteTripHistoryInput",
    "DeleteMergedTripInput",
    "UpdateTripNoteInput",
}

GRADLE_ERROR_NOISE_PATTERNS = (
    r"^> Task .* FAILED$",
    r"^FAILURE: Build failed",
    r"^BUILD FAILED",
    r"^-+$",
    r"^w: ",
    r"^OpenJDK .* warning:",
    r"^Deprecated Gradle features",
    r"^You can use '--warning-mode",
    r"^For more on this",
    r"^\d+ actionable tasks",
    r"^=+$",
    r"^ALL ERROR GROUPS",
    r"^\d+\.\s+(?:Exception|Runtime exception|Kotlin compiler error|Unresolved reference|Suspend function|Argument type mismatch|Type mismatch|JUnit|Gradle failure):",
    r"^Evidence lines:",
    r"^File:",
    r"^Lines:",
)


def _classify_kotlin_compiler_line(line: str) -> str | None:
    unresolved = re.search(r"Unresolved reference[:\s]+'?([A-Za-z_][A-Za-z0-9_]*)'?", line)
    null_for_non_null = re.search(r"Null can not be a value of a non-null type\s+(.+)$", line)
    type_mismatch = re.search(r"Type mismatch:\s*inferred type is\s+(.+?)\s+but\s+(.+?)\s+was expected", line)
    assignment_type_mismatch = re.search(
        r"Assignment type mismatch:\s*actual type is\s+'?(.+?)'?,\s*but\s+'?(.+?)'?\s+was expected\.?",
        line,
    )
    argument_type_mismatch = re.search(
        r"Argument type mismatch:\s*actual type is\s+'?(.+?)'?,\s*but\s+'?(.+?)'?\s+was expected",
        line,
    )
    syntax_error = re.search(r"Syntax error:\s*(.+)$", line)
    annotation_target = re.search(
        r"This annotation is not applicable to target\s+'?([^'.]+)'?\.?\s+Applicable targets:\s*(.+)$",
        line,
    )
    unknown_named_parameter = re.search(
        r"(?:Cannot find a parameter with this name:|No parameter with name)\s+'?([A-Za-z_][A-Za-z0-9_]*)'?(?:\s+found)?\.?",
        line,
    )
    suspend_call = re.search(
        r"Suspend function\s+'([^']+)'\s+(?:should be called|can only be called from a coroutine)",
        line,
    )
    ksp_unresolved_type = re.search(
        r"\[ksp]\s+.*?\bbecause\s+'?([A-Za-z_][A-Za-z0-9_]*)'?\s+could not be resolved\.?",
        line,
    )
    ksp_error_return_type = re.search(
        r"=>\s+type\s+\(ERROR return type\):\s+([A-Za-z_][A-Za-z0-9_]*)",
        line,
    )

    if (
        "expected type Method!" in line
        or "but Method! was expected" in line
        or re.search(r"literal does not conform to the expected type Method!", line)
    ):
        return "Mockito reflection stubbing target is Method"
    if "Class '<anonymous>' is not abstract and does not implement abstract members" in line:
        return "Anonymous interface implementation missing abstract members"
    if ksp_unresolved_type:
        return f"KSP unresolved type: {ksp_unresolved_type.group(1)}"
    if ksp_error_return_type:
        return f"KSP unresolved type: {ksp_error_return_type.group(1)}"
    if "[Dagger/MissingBinding]" in line or "cannot be provided without an @Provides-annotated method" in line:
        return "Hilt graph unclosed: missing binding"
    if "[Dagger/DuplicateBindings]" in line:
        duplicate_type = re.search(r"\[Dagger/DuplicateBindings]\s+([A-Za-z0-9_.$]+)\s+is bound multiple times", line)
        return (
            f"Hilt Dagger duplicate binding: {duplicate_type.group(1).split('.')[-1]}"
            if duplicate_type
            else "Hilt Dagger duplicate binding"
        )
    if "Unresolved reference. None of the following candidates is applicable because of a receiver type mismatch" in line:
        return "Receiver type mismatch unresolved reference"
    if unresolved and (
        unresolved.group(1) in APOLLO_GENERATED_INPUT_SYMBOLS
        or unresolved.group(1).endswith("Input")
    ):
        return "Apollo generated api.type compile-time reference"
    if unresolved:
        return f"Unresolved reference: {unresolved.group(1)}"
    if "Cannot access class '" in line and ".journetlog.api.type." in line:
        return "Apollo generated api.type compile-time reference"
    if "Destructuring declaration initializer" in line and "component" in line:
        return "Invalid destructuring: initializer has no component functions"
    if null_for_non_null:
        return f"Null for non-null Kotlin value: {null_for_non_null.group(1).strip()}"
    if assignment_type_mismatch:
        return f"Assignment type mismatch: {assignment_type_mismatch.group(1).strip()} -> {assignment_type_mismatch.group(2).strip()}"
    if argument_type_mismatch:
        return f"Argument type mismatch: {argument_type_mismatch.group(1).strip()} -> {argument_type_mismatch.group(2).strip()}"
    if syntax_error:
        return f"Kotlin syntax error: {syntax_error.group(1).strip()}"
    if annotation_target:
        return f"Annotation target mismatch: {annotation_target.group(1).strip()}"
    if unknown_named_parameter:
        return f"Unknown parameter: {unknown_named_parameter.group(1)}"
    if type_mismatch and type_mismatch.group(1).strip() == "Any?" and type_mismatch.group(2).strip() == "Any":
        return "Nullable Any passed where non-null Any expected"
    if type_mismatch:
        return f"Type mismatch: {type_mismatch.group(1).strip()} -> {type_mismatch.group(2).strip()}"
    if "Type mismatch" in line:
        return "Type mismatch: unknown"
    if suspend_call:
        return f"Suspend function outside coroutine: {suspend_call.group(1)}"
    if re.search(r"None of the following functions can be called with the arguments supplied", line):
        return "No matching overload: arguments supplied"
    if re.search(r"Too many arguments", line):
        return "Too many arguments"
    if re.search(r"Overload resolution ambiguity", line):
        return "Overload resolution ambiguity"
    return None


def _finalize_gradle_error_groups(groups: dict, gradle_output: str, compiler_candidate_lines: list[str]) -> dict:
    mockk_coroutine_keys = [
        key
        for key in list(groups)
        if key in {"Unresolved reference: coEvery", "Unresolved reference: coVerify"}
        or key.startswith("Suspend function outside coroutine:")
    ]
    if any(key in groups for key in {"Unresolved reference: coEvery", "Unresolved reference: coVerify"}) and len(mockk_coroutine_keys) > 1:
        merged_lines = []
        for key in mockk_coroutine_keys:
            merged_lines.extend(groups.pop(key, []))
        groups["MockK coroutine DSL for suspend calls"] = merged_lines

    if len(groups) > 1:
        groups.pop("Kotlin compilation failed", None)
        for group_name in list(groups):
            if group_name.startswith("Gradle task failure:"):
                groups.pop(group_name, None)

    if compiler_candidate_lines and "Receiver type mismatch unresolved reference" in groups:
        groups["Receiver type mismatch unresolved reference"].extend(compiler_candidate_lines)

    if any(group_name.startswith("JUnit test failure:") for group_name in groups):
        for group_name, group_lines in list(groups.items()):
            if group_name.startswith("JUnit test failure:"):
                continue
            group_text = "\n".join(group_lines)
            is_summary_stacktrace_fragment = (
                group_name == "JUnit AssertionError"
                or group_name.startswith(("Exception:", "Runtime exception:"))
            ) and re.search(r"\bat\s+[A-Za-z0-9_.$]+(?:\.java|\.kt):\d+\b", group_text)
            if is_summary_stacktrace_fragment:
                groups.pop(group_name, None)

    if not groups and ("EXIT_CODE: 1" in gradle_output or "BUILD FAILED" in gradle_output):
        focused = extract_first_gradle_error(gradle_output)
        groups["Gradle failure: ungrouped"] = [line.strip() for line in focused.splitlines() if line.strip()][:80]

    for group_name, group_lines in list(groups.items()):
        groups[group_name] = list(dict.fromkeys(group_lines))
    return groups


_GRADLE_ERROR_PATTERNS = [
    (r"Unresolved reference[:\s]+'?([A-Za-z_][A-Za-z0-9_]*)'?", "Unresolved reference: {0}"),
    (r"No value passed for parameter '([^']+)'", "No value passed for parameter: {0}"),
    (r"Suspend function '([^']+)' (?:should be called|can only be called from a coroutine)", "Suspend function outside coroutine: {0}"),
    (r"Not enough information to infer type variable ([A-Za-z_][A-Za-z0-9_]*)", "Type inference failure: {0}"),
    (r"Cannot find a parameter with this name: ([A-Za-z_][A-Za-z0-9_]*)", "Unknown parameter: {0}"),
    (r"No parameter with name '([^']+)' found\.?", "Unknown parameter: {0}"),
    (r"Cannot access '([^']+)'", "Cannot access: {0}"),
    (r"\[ksp]\s+.*?\bbecause\s+'?([A-Za-z_][A-Za-z0-9_]*)'?\s+could not be resolved\.?", "KSP unresolved type: {0}"),
    (r"=>\s+type\s+\(ERROR return type\):\s+([A-Za-z_][A-Za-z0-9_]*)", "KSP unresolved type: {0}"),
    (r"(.+Test) > (.+) FAILED", "JUnit test failure: {1}"),
    (r"Cannot locate tasks that match '([^']+)'", "Missing Gradle task: {0}"),
    (r"no value for <([^>]+)> is provided", "Missing manifest placeholder: {0}"),
    (r"Execution failed for task '([^']+)'", "Gradle task failure: {0}"),
]


def _classify_gradle_error_key(line: str, *, is_kotlin_compiler_error: bool) -> str:
    if is_kotlin_compiler_error:
        compiler_key = _classify_kotlin_compiler_line(line)
        if compiler_key:
            return compiler_key

    unresolved = re.search(r"Unresolved reference[:\s]+'?([A-Za-z_][A-Za-z0-9_]*)'?", line)
    null_for_non_null = re.search(r"Null can not be a value of a non-null type\s+(.+)$", line)
    type_mismatch = re.search(r"Type mismatch:\s*inferred type is\s+(.+?)\s+but\s+(.+?)\s+was expected", line)
    assignment_type_mismatch = re.search(
        r"Assignment type mismatch:\s*actual type is\s+'?(.+?)'?,\s*but\s+'?(.+?)'?\s+was expected\.?",
        line,
    )
    argument_type_mismatch = re.search(
        r"Argument type mismatch:\s*actual type is\s+'?(.+?)'?,\s*but\s+'?(.+?)'?\s+was expected",
        line,
    )
    syntax_error = re.search(r"Syntax error:\s*(.+)$", line)
    annotation_target = re.search(
        r"This annotation is not applicable to target\s+'?([^'.]+)'?\.?\s+Applicable targets:\s*(.+)$",
        line,
    )

    if "[Dagger/MissingBinding]" in line or "cannot be provided without an @Provides-annotated method" in line:
        return "Hilt graph unclosed: missing binding"
    if "[Dagger/DuplicateBindings]" in line:
        duplicate_type = re.search(r"\[Dagger/DuplicateBindings]\s+([A-Za-z0-9_.$]+)\s+is bound multiple times", line)
        if duplicate_type:
            return f"Hilt Dagger duplicate binding: {duplicate_type.group(1).split('.')[-1]}"
        return "Hilt Dagger duplicate binding"
    if (
        "expected type Method!" in line
        or "but Method! was expected" in line
        or re.search(r"literal does not conform to the expected type Method!", line)
    ):
        return "Mockito reflection stubbing target is Method"
    if unresolved and (
        unresolved.group(1) in APOLLO_GENERATED_INPUT_SYMBOLS or unresolved.group(1).endswith("Input")
    ):
        return "Apollo generated api.type compile-time reference"
    if "Cannot access class '" in line and ".journetlog.api.type." in line:
        return "Apollo generated api.type compile-time reference"
    if "Destructuring declaration initializer" in line and "component" in line:
        return "Invalid destructuring: initializer has no component functions"
    if null_for_non_null:
        return f"Null for non-null Kotlin value: {null_for_non_null.group(1).strip()}"
    if assignment_type_mismatch:
        return f"Assignment type mismatch: {assignment_type_mismatch.group(1).strip()} -> {assignment_type_mismatch.group(2).strip()}"
    if argument_type_mismatch:
        return f"Argument type mismatch: {argument_type_mismatch.group(1).strip()} -> {argument_type_mismatch.group(2).strip()}"
    if syntax_error:
        return f"Kotlin syntax error: {syntax_error.group(1).strip()}"
    if annotation_target:
        return f"Annotation target mismatch: {annotation_target.group(1).strip()}"
    if type_mismatch and type_mismatch.group(1).strip() == "Any?" and type_mismatch.group(2).strip() == "Any":
        return "Nullable Any passed where non-null Any expected"
    if type_mismatch:
        return f"Type mismatch: {type_mismatch.group(1).strip()} -> {type_mismatch.group(2).strip()}"
    if "Type mismatch" in line:
        return "Type mismatch: unknown"
    if re.search(r"None of the following functions can be called with the arguments supplied", line):
        return "No matching overload: arguments supplied"
    if re.search(r"Too many arguments", line):
        return "Too many arguments"
    if re.search(r"Overload resolution ambiguity", line):
        return "Overload resolution ambiguity"
    if re.search(r"InvalidTestClassError", line):
        return "JUnit InvalidTestClassError"
    if re.search(r"AssertionError", line):
        return "JUnit AssertionError"

    for pattern, template in _GRADLE_ERROR_PATTERNS:
        match = re.search(pattern, line)
        if match:
            return template.format(*match.groups())

    caused_by = re.search(r"Caused by:\s+([A-Za-z0-9_.$]+)", line)
    if caused_by:
        return f"Runtime exception: {caused_by.group(1).split('.')[-1]}"

    exception_line = re.search(r"\b([A-Za-z0-9_.$]+Exception)\b", line)
    if exception_line and not is_kotlin_compiler_error:
        return f"Exception: {exception_line.group(1).split('.')[-1]}"

    if line.startswith("e: "):
        return "Kotlin compiler error: unclassified"
    if "Compilation error" in line:
        return "Kotlin compilation failed"
    return "Unclassified Gradle/JUnit failure"


def group_gradle_errors(gradle_output: str):
    groups = {}

    compiler_candidate_lines = [
        raw_line.strip()
        for raw_line in gradle_output.splitlines()
        if re.match(r"fun\s+[A-Za-z0-9_.]+\.(setMain|resetMain)\(", raw_line.strip())
    ]
    pre_grouped_lines = set()

    for duplicate_block_match in re.finditer(
        r"(?ms)(/[^\n]+\.java:\d+:\s+error:\s+\[Dagger/DuplicateBindings\].*?)(?=\n/[^\n]+\.java:\d+:\s+error:|\n\[Incubating]|\n> Compilation failed|\Z)",
        gradle_output,
    ):
        duplicate_block = duplicate_block_match.group(1)
        first_line = duplicate_block.splitlines()[0].strip()
        duplicate_key = _classify_kotlin_compiler_line(first_line) or "Hilt Dagger duplicate binding"
        for block_line in duplicate_block.splitlines():
            stripped_block_line = block_line.strip()
            if stripped_block_line:
                groups.setdefault(duplicate_key, []).append(stripped_block_line)
                pre_grouped_lines.add(stripped_block_line)

    for raw_line in gradle_output.splitlines():
        line = raw_line.strip()
        if line in pre_grouped_lines:
            continue
        if not line or any(re.search(pattern, line) for pattern in GRADLE_ERROR_NOISE_PATTERNS):
            continue

        is_compiler_warning = line.startswith("w: ")
        is_kotlin_compiler_error = line.startswith("e: ")
        is_junit_failure_selector = re.search(r"\b[A-Za-z0-9_.]+Test\s*>\s*.+\s+FAILED$", line) is not None

        is_relevant = (
            is_kotlin_compiler_error
            or is_junit_failure_selector
            or "InvalidTestClassError" in line
            or "AssertionError" in line
            or ("Exception" in line and not is_compiler_warning and not is_kotlin_compiler_error)
            or "Caused by:" in line
            or "Cannot locate tasks" in line
            or "Manifest merger failed" in line
            or "requires a placeholder substitution" in line
            or "Execution failed for task" in line
            or "Compilation error" in line
            or "[Dagger/MissingBinding]" in line
            or "[Dagger/DuplicateBindings]" in line
            or "cannot be provided without an @Provides-annotated method" in line
            or "could not be resolved" in line
            or "ERROR return type" in line
        )
        if not is_relevant:
            continue

        key = _classify_gradle_error_key(line, is_kotlin_compiler_error=is_kotlin_compiler_error)
        groups.setdefault(key, []).append(line)

    return _finalize_gradle_error_groups(groups, gradle_output, compiler_candidate_lines)


def extract_error_line_numbers(error_text: str):
    numbers = []
    numbers.extend(int(n) for n in re.findall(r"\.kt:(\d+):\d+", error_text))
    numbers.extend(int(n) for n in re.findall(r"\.kt:(\d+)(?![:\d])", error_text))
    numbers.extend(int(n) for n in re.findall(r"\((\d+),\s*\d+\)", error_text, re.IGNORECASE))
    numbers.extend(int(n) for n in re.findall(r"\bline\s+(\d+)\b", error_text, re.IGNORECASE))
    seen = []
    for n in numbers:
        if n > 0 and n not in seen:
            seen.append(n)
    return seen


def extract_error_symbols(error_text: str):
    candidates = []
    patterns = [
        r"Unresolved reference: ([A-Za-z_][A-Za-z0-9_]*)",
        r"Cannot access '([^']+)'",
        r"No value passed for parameter '([^']+)'",
        r"Cannot find a parameter with this name: ([A-Za-z_][A-Za-z0-9_]*)",
        r"Required:\s*([A-Za-z_][A-Za-z0-9_<>?.]*)",
        r"Found:\s*([A-Za-z_][A-Za-z0-9_<>?.]*)",
        r"Type mismatch:\s*inferred type is ([A-Za-z_][A-Za-z0-9_<>?.]*)",
        r"but ([A-Za-z_][A-Za-z0-9_<>?.]*) was expected",
        r"Cannot locate tasks that match '([^']+)'",
        r"task '([^']+)' not found",
        r"no value for <([^>]+)> is provided",
    ]
    for pattern in patterns:
        candidates.extend(re.findall(pattern, error_text, re.DOTALL))

    ignored = {
        "FAILURE", "BUILD", "FAILED", "Exception", "Gradle", "Kotlin", "Task",
        "Required", "Found", "String", "Int", "Long", "Double", "Float", "Boolean",
        "Unit", "List", "Map", "Set", "Any", "Nothing", "Null", "Error", "Compilation",
        "COMMAND", "EXIT_CODE", "Unresolved", "Too", "Build", "Common", "JUnit",
        "AssertionError", "UncaughtExceptionsBeforeTest", "Runtime", "RELATED", "GROUP",
        "Execution", "There",
    }
    result = []
    for candidate in candidates:
        clean = str(candidate).strip().replace("?", "").split(".")[-1]
        clean = re.sub(r"<.*$", "", clean)
        clean = re.sub(r"[^A-Za-z0-9_]", "", clean)
        if clean and clean not in ignored and re.match(r"[A-Za-z_]", clean) and clean not in result:
            result.append(clean)
    return result[:12]


def extract_unresolved_nested_receiver_symbols(error_text: str) -> list[str]:
    """
    Detect unresolved nested member calls such as SomeSdkType.Inner.Value in
    generated test setup. The unresolved symbol may be only Inner, but repair
    needs verified context for SomeSdkType before inventing nested API names.
    """
    unresolved_symbols = set(re.findall(r"Unresolved reference[:\s]+'?([A-Za-z_][A-Za-z0-9_]*)'?", error_text))
    receivers = []
    for match in re.finditer(
        r"\b([A-Z][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)(?:\.[A-Za-z_][A-Za-z0-9_]*)+",
        error_text,
    ):
        receiver, member = match.group(1), match.group(2)
        if unresolved_symbols and member not in unresolved_symbols:
            continue
        if receiver not in receivers:
            receivers.append(receiver)
    return receivers[:6]


def summarize_all_error_groups(groups: dict) -> str:
    output = []
    for index, (group_key, group_lines) in enumerate(groups.items(), 1):
        block = "\n".join(group_lines)
        files = sorted(set(re.findall(r"(/[^\s:]+\.kt)", block)))
        file_line_locations = sorted(
            {
                (path, int(line_no))
                for path, line_no in re.findall(r"(/[^\s:]+\.kt):(\d+):\d+", block)
            },
            key=lambda item: (item[0], item[1]),
        )
        line_numbers = sorted({line_no for _, line_no in file_line_locations})
        xml_test_methods = []
        for match in re.finditer(r"XML test method:\s*(.+)", block):
            method_name = match.group(1).strip()
            if method_name and method_name not in xml_test_methods:
                xml_test_methods.append(method_name)

        selector_tests = []
        for match in re.finditer(r"\b([A-Za-z0-9_.]+Test)\s*>\s*(.+?)\s+FAILED\b", block):
            selector = f"{match.group(1).split('.')[-1]} > {match.group(2).strip()}"
            if selector not in selector_tests:
                selector_tests.append(selector)

        output.append(f"{index}. {group_key}")
        if xml_test_methods:
            output.append(f"   Affected tests: {len(xml_test_methods)}")
            output.append(f"   Test methods: {', '.join(xml_test_methods[:8])}")
        elif selector_tests:
            output.append(f"   Affected tests: {len(selector_tests)}")
            output.append(f"   Test methods: {', '.join(selector_tests[:8])}")
        evidence_count = len(file_line_locations) if file_line_locations else len(group_lines)
        output.append(f"   Evidence lines: {evidence_count}")
        if file_line_locations and len(group_lines) != evidence_count:
            output.append(f"   Compiler messages: {len(group_lines)}")
        if files:
            output.append(f"   File: {files[0]}")
        if line_numbers:
            shown_lines = ", ".join(str(n) for n in line_numbers[:10])
            remaining_count = len(line_numbers) - 10
            if remaining_count > 0:
                shown_lines += f" ... (+{remaining_count} more)"
            output.append(f"   Lines: {shown_lines}")
    return "\n".join(output)


def classify_error_group_root_cause(group_key: str, group_lines: list[str]) -> str:
    combined = f"{group_key}\n" + "\n".join(group_lines)
    lower = combined.lower()

    if group_key.startswith("Unresolved reference") or group_key.startswith("KSP unresolved type") or group_key == "Apollo generated api.type compile-time reference":
        return "unresolved_reference"
    if (
        group_key.startswith("Hilt graph unclosed")
        or group_key.startswith("Hilt Dagger missing binding")
        or group_key.startswith("Missing Gradle task")
        or "cannot be provided without an @Provides" in combined
    ):
        return "missing_import_or_dependency"
    if (
        group_key.startswith("Unknown parameter")
        or group_key.startswith("No value passed")
        or group_key.startswith("No matching overload")
        or group_key.startswith("Too many arguments")
        or group_key.startswith("Overload resolution ambiguity")
        or group_key.startswith("Cannot access")
        or group_key.startswith("Suspend function outside coroutine")
    ):
        return "api_signature_mismatch"
    if (
        group_key.startswith("Type mismatch")
        or group_key.startswith("Argument type mismatch")
        or group_key.startswith("Assignment type mismatch")
        or group_key.startswith("Null for non-null")
        or group_key.startswith("Nullable Any")
        or group_key.startswith("Type inference failure")
        or group_key == "Receiver type mismatch unresolved reference"
    ):
        return "type_mismatch"
    if (
        group_key.startswith("Kotlin syntax error")
        or group_key.startswith("Annotation target mismatch")
        or group_key.startswith("Invalid destructuring")
        or group_key.startswith("Kotlin compiler error")
        or group_key.startswith("Kotlin compilation failed")
    ):
        return "syntax_or_file_shape"
    if any(
        marker in lower
        for marker in (
            "any(...) must not be null",
            "capture(...) must not be null",
            "invaliduseofmatchers",
            "unfinishedverificationexception",
            "unfinishedstubbingexception",
            "misplaced or misused argument matcher",
            "missing method call for verify",
        )
    ):
        return "mockito_misuse"
    if any(marker in lower for marker in ("robolectric", "fragment", "activity", "navcontroller", "hilt androidentrypoint", "generatedcomponent")):
        return "fixture_strategy"
    if any(marker in lower for marker in ("mockito", "mockk", "notamockexception", "invaliduseofmatchers", "mockedstatic")):
        return "mocking_strategy"
    if group_key.startswith("JUnit AssertionError") or "assertionerror" in lower or "wanted but not invoked" in lower:
        return "assertion_behavior"
    if (
        group_key.startswith("JUnit test failure")
        or group_key.startswith("Runtime exception")
        or group_key.startswith("Exception:")
        or "uncaughtexceptionsbeforetest" in lower
        or "failed" in lower
    ):
        return "runtime_lifecycle"
    return "unknown"


def _entry_from_group(group_key: str, group_lines: list[str]) -> ErrorEntry:
    block = "\n".join(group_lines)
    files = sorted(set(re.findall(r"(/[^\s:]+\.kt)", block)))
    return ErrorEntry(
        group_key=group_key,
        category=classify_error_group_root_cause(group_key, group_lines),
        symbols=extract_error_symbols(f"{group_key}\n{block}"),
        files=files,
        lines=extract_error_line_numbers(block),
        evidence=list(group_lines),
    )


def _is_type_like_symbol(symbol: str) -> bool:
    if not symbol:
        return False
    return (
        symbol[0].isupper()
        or symbol.endswith(("State", "Request", "Response", "Details", "Detail", "Device", "Event", "Result", "Input"))
    )


def _entry_has_type_like_symbol(entry: ErrorEntry) -> bool:
    return any(_is_type_like_symbol(symbol) for symbol in entry.symbols)


def _shares_file_and_line(left: ErrorEntry, right: ErrorEntry, radius: int = 0) -> bool:
    common_files = set(left.files) & set(right.files)
    if not common_files:
        return False
    if not left.lines or not right.lines:
        return False
    return any(abs(a - b) <= radius for a in left.lines for b in right.lines)


def _is_cascade_candidate(entry: ErrorEntry) -> bool:
    if entry.category in {"syntax_or_file_shape", "type_mismatch"} and (
        entry.group_key.startswith("Kotlin compiler error")
        or entry.group_key.startswith("Type inference failure")
        or entry.group_key == "Receiver type mismatch unresolved reference"
        or entry.group_key.startswith("No matching overload")
        or entry.group_key.startswith("Overload resolution ambiguity")
    ):
        return True
    if entry.category == "unresolved_reference" and entry.symbols and all(not _is_type_like_symbol(symbol) for symbol in entry.symbols):
        return True
    return False


def _entry_priority(entry: ErrorEntry) -> int:
    category_priority = ROOT_CAUSE_CATEGORY_PRIORITY.get(entry.category, ROOT_CAUSE_CATEGORY_PRIORITY["unknown"])
    if entry.category == "unresolved_reference":
        return category_priority if _entry_has_type_like_symbol(entry) else category_priority + 5
    return category_priority


def _recommended_action_for_category(category: str) -> str:
    actions = {
        "unresolved_reference": "Verify imports, type names, enum/data classes, and generated/test dependencies; update all related usages together.",
        "missing_import_or_dependency": "Resolve the owning dependency or module visibility first, then rerun before editing symptoms.",
        "api_signature_mismatch": "Verify constructor/function signatures and parameter names, then update call sites consistently.",
        "type_mismatch": "Repair the source expression type, matcher type, or return value shape before changing assertions.",
        "syntax_or_file_shape": "Fix Kotlin file structure, imports, annotations, and malformed code before semantic repair.",
        "mockito_misuse": "Repair invalid Mockito stubbing, matcher, or verification state before interpreting later runtime failures.",
        "fixture_strategy": "Repair test fixture, host, lifecycle, or framework setup order before assertions.",
        "mocking_strategy": "Repair mockability, stubbing, matcher, or verification strategy instead of changing expected values.",
        "runtime_lifecycle": "Use exact JUnit/XML failure context and repair lifecycle/setup path first.",
        "assertion_behavior": "Verify the public behavior path and expected outcome before changing assertions.",
    }
    return actions.get(category, "Use exact evidence and verified context to repair the primary failure first.")


def _confidence_for_cause(category: str, primary_entries: list[ErrorEntry], cascades: list[ErrorEntry]) -> float:
    base = {
        "missing_import_or_dependency": 0.9,
        "syntax_or_file_shape": 0.82,
        "unresolved_reference": 0.86,
        "api_signature_mismatch": 0.82,
        "type_mismatch": 0.75,
        "mockito_misuse": 0.84,
        "fixture_strategy": 0.78,
        "mocking_strategy": 0.78,
        "runtime_lifecycle": 0.7,
        "assertion_behavior": 0.68,
        "unknown": 0.45,
    }.get(category, 0.5)
    if cascades:
        base += 0.04
    if primary_entries and all(entry.files for entry in primary_entries):
        base += 0.02
    return min(base, 0.95)


def build_causal_repair_plan(groups: dict) -> list[RepairRootCause]:
    entries = [_entry_from_group(key, lines) for key, lines in groups.items()]
    primary_entries = []
    cascade_map: dict[int, list[ErrorEntry]] = {}

    candidate_primary_entries = [
        entry
        for entry in entries
        if not _is_cascade_candidate(entry)
    ]

    for entry in entries:
        if not _is_cascade_candidate(entry):
            primary_entries.append(entry)
            continue

        radius = 0 if entry.category == "unresolved_reference" else 3
        linked_primary_index = None
        for index, primary in enumerate(candidate_primary_entries):
            if primary is entry:
                continue
            if primary.category == "unresolved_reference" and not _entry_has_type_like_symbol(primary):
                continue
            if _shares_file_and_line(entry, primary, radius=radius):
                linked_primary_index = entries.index(primary)
                break

        if linked_primary_index is None:
            primary_entries.append(entry)
        else:
            cascade_map.setdefault(linked_primary_index, []).append(entry)

    buckets: dict[str, list[ErrorEntry]] = {}
    for entry in primary_entries:
        buckets.setdefault(entry.category, []).append(entry)

    causes = []
    for category, category_entries in buckets.items():
        ordered_entries = sorted(category_entries, key=lambda item: (_entry_priority(item), item.group_key))
        primary = ordered_entries[0]
        peer_entries = ordered_entries[1:]
        primary_index = entries.index(primary)
        cascades = cascade_map.get(primary_index, [])
        primary_key = f"{category}: {', '.join(primary.symbols[:4]) or primary.group_key}"

        if category == "unresolved_reference":
            # Keep independent unresolved symbols together because one import or type correction often resolves many of them.
            peer_entries = ordered_entries[1:]
            cascades = []
            for entry in ordered_entries:
                entry_index = entries.index(entry)
                cascades.extend(cascade_map.get(entry_index, []))
            all_symbols = []
            for entry in ordered_entries:
                for symbol in entry.symbols:
                    if symbol not in all_symbols:
                        all_symbols.append(symbol)
            primary_key = f"unresolved_reference: {', '.join(all_symbols[:8]) or primary.group_key}"

        causes.append(
            RepairRootCause(
                category=category,
                primary_key=primary_key,
                primary_evidence=primary.evidence,
                cascade_entries=cascades,
                peer_entries=peer_entries,
                confidence=_confidence_for_cause(category, ordered_entries, cascades),
                recommended_action=_recommended_action_for_category(category),
            )
        )

    return sorted(
        causes,
        key=lambda cause: (
            _entry_priority(ErrorEntry(cause.primary_key, cause.category, [], [], [], [])),
            -cause.confidence,
            cause.primary_key,
        ),
    )


def _format_entry(entry: ErrorEntry, label: str) -> str:
    details = [f"{label}: {entry.group_key}"]
    if entry.symbols:
        details.append(f"Symbols: {', '.join(entry.symbols[:8])}")
    if entry.files:
        details.append(f"File: {entry.files[0]}")
    if entry.lines:
        details.append(f"Lines: {', '.join(str(line) for line in entry.lines[:12])}")
    details.append("Evidence:")
    details.extend(entry.evidence)
    return "\n".join(details)


def format_causal_repair_block(cause: RepairRootCause) -> str:
    parts = [
        "ROOT CAUSE REPAIR BATCH",
        "",
        "PRIMARY ROOT CAUSE",
        f"Category: {cause.category}",
        f"Primary key: {cause.primary_key}",
        f"Confidence: {cause.confidence:.2f}",
        f"Recommended action: {cause.recommended_action}",
        "",
        "Primary evidence:",
        *cause.primary_evidence,
    ]

    if cause.cascade_entries:
        parts.extend(["", "EXPECTED CASCADE ERRORS TO DISAPPEAR"])
        for entry in cause.cascade_entries:
            parts.extend(["", _format_entry(entry, "Cascade")])

    if cause.peer_entries:
        parts.extend(["", "PEER ERRORS IN SAME CATEGORY"])
        for entry in cause.peer_entries:
            parts.extend(["", _format_entry(entry, "Peer")])

    return "\n".join(parts)


def summarize_causal_repair_plan(causes: list[RepairRootCause]) -> str:
    output = []
    for index, cause in enumerate(causes, 1):
        cascade_count = len(cause.cascade_entries)
        peer_count = len(cause.peer_entries)
        output.append(f"{index}. {cause.primary_key}")
        output.append(f"   Category: {cause.category}")
        output.append(f"   Confidence: {cause.confidence:.2f}")
        output.append(f"   Peers in category: {peer_count}")
        output.append(f"   Expected cascades: {cascade_count}")
        output.append(f"   Action: {cause.recommended_action}")
    return "\n".join(output)


def causal_repair_fingerprint(cause: RepairRootCause) -> str:
    block = format_causal_repair_block(cause)
    files = sorted(set(re.findall(r"(/[^\s:]+\.kt)", block)))
    line_numbers = extract_error_line_numbers(block)
    symbols = extract_error_symbols(block)
    return json.dumps(
        {
            "category": cause.category,
            "primary": cause.primary_key,
            "symbols": symbols,
            "files": files,
            "lines": line_numbers,
        },
        sort_keys=True,
    )


def repair_location_fingerprint(group_key: str, group_lines: list[str]) -> str:
    """
    Identify the concrete error location for loop detection. The same root
    cause at the same file/line gets two repair attempts, then the repair loop
    moves on to other errors.
    """
    error_text = "\n".join(group_lines)
    files = sorted(set(re.findall(r"(/[^\s:]+\.kt)", error_text)))
    line_numbers = extract_error_line_numbers(error_text)

    if files or line_numbers:
        location_payload = {
            "group_key": group_key,
            "files": files,
            "lines": line_numbers,
        }
        return json.dumps(location_payload, sort_keys=True)

    from UnitTest_gen.kotlin.gradle_analysis.junit import error_group_fingerprint

    return f"{group_key}:{error_group_fingerprint(group_lines)}"
