# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Gradle/Kotlin/JUnit failure parsing and causal repair planning (junit).
"""Gradle/Kotlin/JUnit failure parsing and causal repair planning — junit submodule."""

# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Parses and groups Gradle, Kotlin compiler, and JUnit failures.
import hashlib
import re

def is_compile_repair_group(group_key: str, group_lines: list[str]) -> bool:
    runtime_markers = (
        "JUnit",
        "Assertion",
        "Exception:",
        "Runtime exception:",
        "Gradle task failure:",
        "Missing Gradle task:",
        "Manifest",
    )
    if any(marker in group_key for marker in runtime_markers):
        return False

    compile_key_markers = (
        "Unresolved reference",
        "No value passed",
        "Suspend function outside coroutine",
        "Type inference failure",
        "Unknown parameter",
        "Cannot access",
        "Apollo generated api.type compile-time reference",
        "Invalid destructuring",
        "Null for non-null Kotlin value",
        "Nullable Any passed",
        "Type mismatch",
        "Argument type mismatch",
        "No matching overload",
        "Too many arguments",
        "Overload resolution ambiguity",
        "Mockito reflection stubbing target",
        "Anonymous interface implementation missing abstract members",
        "Kotlin compiler error",
        "Kotlin compilation failed",
    )
    if any(marker in group_key for marker in compile_key_markers):
        return True

    return any(
        line.startswith("e: ") or "Compilation error" in line
        for line in group_lines
    )


def should_try_batch_compile_repair(groups: dict, max_evidence_lines: int = 20) -> bool:
    if not groups:
        return False

    total_evidence_lines = sum(len(lines) for lines in groups.values())
    if total_evidence_lines > max_evidence_lines:
        return False

    return all(
        is_compile_repair_group(group_key, group_lines)
        for group_key, group_lines in groups.items()
    )


def is_junit_or_runtime_failure_group(group_key: str, focused_error_block: str) -> bool:
    combined = f"{group_key}\n{focused_error_block}"
    markers = [
        "JUnit test failure",
        "Gradle/JUnit failure",
        "Unclassified Gradle/JUnit failure",
        "JUnit AssertionError",
        "Runtime exception",
        "Exception:",
        "UncaughtExceptionsBeforeTest",
        "FAILED",
        "AssertionError",
    ]
    return any(marker in combined for marker in markers) and "> Task" not in combined


def extract_failed_test_selectors(focused_error_block: str):
    selectors = []
    for match in re.finditer(r"([A-Za-z0-9_.]+Test)\s*>\s*(.+?)\s+FAILED", focused_error_block):
        class_name = match.group(1).strip()
        method_name = match.group(2).strip()
        selectors.append((class_name, method_name))
    return selectors


def test_case_matches_selector(testcase, selectors):
    if not selectors:
        return True

    testcase_class = testcase.attrib.get("classname", "")
    testcase_name = testcase.attrib.get("name", "")

    for class_name, method_name in selectors:
        class_matches = testcase_class == class_name or testcase_class.endswith("." + class_name.split(".")[-1])
        method_matches = testcase_name == method_name or method_name in testcase_name or testcase_name in method_name
        if class_matches and method_matches:
            return True

    return False


def normalize_junit_failure_message(message: str, max_len: int = 140) -> str:
    normalized = (message or "").replace("\n", " ")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    normalized = re.sub(r"#\d+", "#N", normalized)
    normalized = re.sub(r"\([^)]*\.kt:\d+\)", "(FILE.kt:LINE)", normalized)
    normalized = re.sub(r"\bat\s+[^ ]+\.kt:\d+", "at FILE.kt:LINE", normalized)

    if len(normalized) > max_len:
        normalized = normalized[:max_len].rstrip() + "..."

    return normalized or "no failure message"


def classify_junit_failure_for_group(failure_type: str, failure_message: str) -> str:
    combined = f"{failure_type}\n{failure_message}"

    if "PackageParserException" in combined and "Requires newer sdk version" in combined:
        return "Robolectric SDK config below manifest requirements"

    if (
        "GeneratedComponent" in combined
        and "GeneratedComponentManager" in combined
        and "component holder" in combined
    ):
        return "Hilt AndroidEntryPoint fragment attached without Hilt test application"

    if "[Dagger/MissingBinding]" in combined or "cannot be provided without an @Provides-annotated method" in combined:
        return "Hilt graph unclosed: missing binding"

    if "AuthorizationServiceDiscovery$MissingArgumentException" in combined:
        return "AppAuth discovery JSON missing mandatory fields"

    if "MissingMethodInvocationException" in combined:
        return "Mockito cannot stub final or non-mock AppAuth value property"

    if "Expected an exception" in combined and "to be thrown, but was completed successfully" in combined:
        return "JUnit expected exception swallowed by source"

    if "UncaughtExceptionsBeforeTest" in combined or (
        "Dispatchers.IO" in combined and ("AssertionError" in combined or "expected" in combined)
    ):
        return "JUnit coroutine strategy failure: hard-coded dispatcher or uncaught background exception"

    if "authResponse.request" in combined and "null" in combined:
        return "AppAuth AuthorizationResponse mock missing real request"

    if "refreshRequest.configuration" in combined and "null" in combined:
        return "AppAuth TokenRequest mock missing real configuration"

    if "ActivityNotFoundException" in combined and (
        "AuthorizationService" in combined
        or "getAuthorizationRequestIntent" in combined
        or "getLoginIntent" in combined
    ):
        return "AppAuth browser authorization intent has no Robolectric resolver"

    if "ClassNotFoundException" in combined:
        if ".journetlog.api." in combined and "$" in combined:
            return "JUnit ClassNotFoundException: Apollo generated class name"
        return "JUnit ClassNotFoundException"

    if "InvocationTargetException" in combined and "Expected: an instance of" in combined:
        return "JUnit expected exception wrapped by reflection InvocationTargetException"

    if "expected" in combined and "to be thrown, but nothing was thrown" in combined:
        return "JUnit expected exception not thrown"

    if "AssertionError" in combined:
        return "JUnit assertion failure: " + normalize_junit_failure_message(failure_message)

    clean_type = failure_type.split(".")[-1] if failure_type else "unknown"
    return f"JUnit failure: {clean_type}: {normalize_junit_failure_message(failure_message)}"


def trim_stacktrace(text: str, max_lines: int = 80) -> str:
    lines = (text or "").splitlines()
    if len(lines) <= max_lines:
        return "\n".join(lines)

    head = lines[:60]
    caused_by = [
        line
        for line in lines[60:]
        if "Caused by:" in line or line.strip().startswith("at ") and ".kt:" in line
    ][:20]
    return "\n".join(head + ["... stacktrace trimmed ..."] + caused_by)


def error_group_fingerprint(group_lines):
    normalized = [re.sub(r":\d+:\d+", ":LINE:COL", line.strip()) for line in group_lines if line.strip()]
    return hashlib.md5("\n".join(normalized).encode("utf-8")).hexdigest()


def is_gradle_success(gradle_output: str) -> bool:
    output = gradle_output or ""
    return "EXIT_CODE: 0" in output and "BUILD SUCCESSFUL" in output and "BUILD FAILED" not in output


def text_fingerprint(text):
    return hashlib.md5((text or "").encode("utf-8")).hexdigest()
