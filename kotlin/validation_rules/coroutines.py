# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Validates coroutine and suspend test patterns.
"""Coroutine, dispatcher, and suspend-call validation rules."""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

from UnitTest_gen.kotlin.kotlin_analysis import source_declares_viewmodel_class, suspend_function_names_from_ast
from UnitTest_gen.kotlin.project_context import find_project_root_for_path
from UnitTest_gen.kotlin.static_analysis import analyze_kotlin_code, verify_project_exception_findings
from UnitTest_gen.kotlin.strategy_contracts import validation_repair_intent


def extract_junit4_test_blocks(test_code: str) -> list[str]:
    report = analyze_kotlin_code(test_code or "")
    lines = (test_code or "").splitlines()
    return [
        "\n".join(lines[function.span.start_line - 1:function.span.end_line])
        for function in report.functions
        if "Test" in function.annotations
    ]


def hard_coded_dispatcher_public_methods(source_code: str) -> set[str]:
    report = analyze_kotlin_code(source_code or "")
    return {
        function.name
        for function in report.functions
        if function.visibility != "private"
        and any(
            finding.span.start_line <= function.span.end_line
            and finding.span.end_line >= function.span.start_line
            for finding in report.confirmed_findings("hardcoded_dispatcher", "detached_coroutine_scope")
        )
    }


def static_policy_validation_issues(test_code: str, output_file_path: str = "") -> list[str]:
    report = analyze_kotlin_code(test_code or "", expected_mocking_framework="mockito")
    project_root = _project_root_for_output(output_file_path)
    if project_root and os.path.isfile(os.path.join(project_root, "gradlew")):
        report = verify_project_exception_findings(report, project_root, output_file_path)
    issues: list[str] = []
    if report.frameworks.mockk or report.confirmed_findings("mocking_framework_mismatch"):
        issues.append(
            "invalid_mocking_framework: Generated tests must use the configured Mockito-Kotlin lane; "
            "remove MockK imports/APIs and use verified Mockito-Kotlin suspend/test patterns."
        )
    if report.confirmed_findings("detached_coroutine_scope"):
        issues.append(
            "invalid_detached_coroutine_scope: Generated tests must not launch GlobalScope or standalone "
            "CoroutineScope work that escapes the test scheduler."
        )
    for finding in report.confirmed_findings("speculative_sdk_exception_constructor"):
        issues.append(
            "invalid_speculative_sdk_exception_constructor: Use only a project-verified SDK exception "
            f"constructor/member shape. Evidence line {finding.span.start_line}: {finding.evidence[:240]}"
        )
    return issues


def invalid_suspend_call_context_issues(
    test_code: str,
    source_code: str,
    output_file_path: str = "",
) -> list[str]:
    source_report = analyze_kotlin_code(source_code or "")
    suspend_names = {function.name for function in source_report.functions if function.is_suspend}
    project_root = _project_root_for_output(output_file_path)
    source_call_names = {call.name for call in source_report.calls}
    if project_root:
        suspend_names.update(_project_suspend_function_names(project_root) & source_call_names)
    if not suspend_names:
        return []
    test_report = analyze_kotlin_code(test_code or "", expected_mocking_framework="mockito")
    test_functions = {function.name: function for function in test_report.functions if "Test" in function.annotations}
    lines = (test_code or "").splitlines()
    invalid = []
    for call in test_report.calls:
        if call.name not in suspend_names or call.function_name not in test_functions:
            continue
        owner = test_functions[call.function_name]
        block = "\n".join(lines[owner.span.start_line - 1:owner.span.end_line])
        if "runTest" in block or "runBlocking" in block:
            continue
        invalid.append((call.name, call.span.start_line))
    if not invalid:
        return []
    details = ", ".join(f"{name} at line {line}" for name, line in sorted(set(invalid)))
    return [
        "invalid_suspend_call_context: Suspend production calls used in test setup/stubbing must execute "
        f"inside runTest or runBlocking, or the unsafe coverage test must be removed. Found: {details}."
    ]


def _project_root_for_output(output_file_path: str) -> str:
    current = os.path.abspath(os.path.dirname(output_file_path)) if output_file_path else ""
    while current and current != os.path.dirname(current):
        if os.path.isfile(os.path.join(current, "gradlew")):
            return current
        current = os.path.dirname(current)
    return ""


@lru_cache(maxsize=8)
def _project_suspend_function_names(project_root: str) -> frozenset[str]:
    names: set[str] = set()
    for root, directories, files in os.walk(project_root):
        directories[:] = [name for name in directories if name not in {"build", ".gradle", ".git", ".cache"}]
        for file_name in files:
            if not file_name.endswith(".kt"):
                continue
            path = os.path.join(root, file_name)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                    names.update(suspend_function_names_from_ast(handle.read()))
            except OSError:
                continue
    return frozenset(names)


def injected_lateinit_property_names(source_code: str) -> set[str]:
    report = analyze_kotlin_code(source_code or "")
    return {
        property_info.name
        for property_info in report.properties
        if property_info.is_lateinit and "Inject" in property_info.annotations
    }


def invalid_project_interface_issues(test_code: str, output_file_path: str) -> list[str]:
    project_root = find_project_root_for_path(output_file_path)
    if not project_root:
        return []
    issues = []
    mock_vars = re.findall(
        r"\b(?:val|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*([A-Za-z_][A-Za-z0-9_]*))?\s*=\s*mock<([A-Za-z_][A-Za-z0-9_]*)>",
        test_code or "",
    )
    for variable, declared_type, mock_type in mock_vars:
        interface_name = declared_type or mock_type
        declaration = next(
            (
                path
                for path in Path(project_root).rglob(f"{interface_name}.kt")
                if not ({"build", "test", "androidTest", "generated"} & set(path.parts))
            ),
            None,
        )
        if declaration is None:
            continue
        source = declaration.read_text(encoding="utf-8", errors="replace")
        if not re.search(rf"\binterface\s+{re.escape(interface_name)}\b", source):
            continue
        declared_methods = set(re.findall(r"\bfun\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", source))
        for called_method in set(re.findall(rf"\b{re.escape(variable)}\.([A-Za-z_][A-Za-z0-9_]*)\s*\(", test_code)):
            if called_method not in declared_methods:
                issues.append(
                    "invalid_project_interface_call: "
                    f"`{interface_name}` does not declare `{called_method}()`. Use only verified interface methods."
                )
    return issues


def collect_validation_issues(test_code: str, output_file_path: str, source_code: str, test_report) -> list[str]:
    """Static policy, suspend call context, and project interface checks."""
    issues: list[str] = []
    issues.extend(static_policy_validation_issues(test_code, output_file_path))
    issues.extend(invalid_suspend_call_context_issues(test_code, source_code, output_file_path))
    issues.extend(invalid_project_interface_issues(test_code, output_file_path))
    return issues


def collect_viewmodel_validation_issues(
    test_code: str, output_file_path: str, source_code: str, test_report
) -> list[str]:
    """ViewModel lateinit injection order and hard-coded dispatcher assertions."""
    issues: list[str] = []

    if source_declares_viewmodel_class(source_code):
        injected_lateinit_names = injected_lateinit_property_names(source_code)
        if injected_lateinit_names:
            apply_blocks = re.finditer(
                r"\b[A-Za-z_][A-Za-z0-9_]*\s*\([^)]*\)\s*\.\s*apply\s*\{(?P<body>[\s\S]{0,1200}?)\}",
                test_code,
            )
            for apply_block in apply_blocks:
                body = apply_block.group("body")
                assigned_names = [
                    name
                    for name in sorted(injected_lateinit_names)
                    if re.search(rf"\b(?:this\.)?{re.escape(name)}\s*=", body)
                ]
                if assigned_names:
                    issues.append(
                        "invalid_lateinit_injection_setup_order: Direct-constructor ViewModel tests should not assign "
                        f"injected lateinit collaborator(s) inside apply/also/with receiver scopes: {', '.join(assigned_names)}. "
                        "Construct the ViewModel first, then assign injected lateinit collaborators with direct statements before invoking methods under test."
                    )
                    break

        io_methods = hard_coded_dispatcher_public_methods(source_code)
        if io_methods:
            for block in extract_junit4_test_blocks(test_code):
                calls_io_method = any(
                    re.search(rf"\b[A-Za-z_][A-Za-z0-9_]*\.{re.escape(method)}\s*\(", block) for method in io_methods
                )
                asserts_final_state = bool(
                    re.search(r"\badvanceUntilIdle\s*\(", block)
                    and re.search(
                        r"\bassert(?:Equals|True|False|Same|Null|NotNull)\s*\([\s\S]{0,220}\.[A-Za-z_][A-Za-z0-9_]*\.value",
                        block,
                    )
                )
                if calls_io_method and asserts_final_state:
                    issues.append(
                        "invalid_hardcoded_dispatcher_final_state_assertion: "
                        + validation_repair_intent("invalid_hardcoded_dispatcher_final_state_assertion")
                    )
                    break

    return issues


def collect_suspend_assert_validation_issues(
    test_code: str, output_file_path: str, source_code: str, test_report
) -> list[str]:
    """Reject assertThrows on suspend functions outside runTest/runBlocking."""
    issues: list[str] = []
    suspend_function_names = suspend_function_names_from_ast(source_code)
    for function_name in suspend_function_names:
        if re.search(
            rf"assertThrows\s*\([^)]*\)\s*\{{(?:(?!\n\s*@Test).)*\.{re.escape(function_name)}\s*\(",
            test_code,
            flags=re.DOTALL,
        ):
            issues.append(
                f"assertThrows cannot directly call suspend function {function_name} inside a normal lambda. "
                "Use runTest and assert stable observable state/interactions, or assert exceptions only when the source rethrows."
            )
    return issues
