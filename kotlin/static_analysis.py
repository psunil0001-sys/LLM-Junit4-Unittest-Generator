# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Produces strict Tree-sitter and Semgrep analysis reports for Kotlin sources.
"""Authoritative Kotlin source/test static analysis pipeline."""

from __future__ import annotations

import hashlib
import os
import re
from importlib.metadata import PackageNotFoundError, version
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from UnitTest_gen.core.logging_utils import get_pipeline_log_dir, log_message
from UnitTest_gen.core.semgrep_runner import (
    SemgrepParseError,
    SemgrepUnavailableError,
    require_semgrep_executable,
    scan_text_with_semgrep,
)
from UnitTest_gen.kotlin.kotlin_analysis import (
    annotation_names,
    ast_text,
    declaration_name,
    direct_child,
    direct_children,
    kotlin_imports,
    parse_kotlin_ast,
    walk_ast,
)


REPORT_SCHEMA_VERSION = "1.1"
SEMGREP_RULE_CONFIG = os.path.join(os.path.dirname(__file__), "semgrep_rules", "kotlin-testgen.yml")
SEMGREP_EXCEPTION_RULE_CONFIG = os.path.join(
    os.path.dirname(__file__), "semgrep_rules", "kotlin-exception-constructors.yml"
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class FindingDisposition(str, Enum):
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    DISMISSED = "dismissed"


class SourceSpan(StrictModel):
    start_line: int = Field(ge=1)
    start_column: int = Field(ge=1)
    end_line: int = Field(ge=1)
    end_column: int = Field(ge=1)


class KotlinFunctionInfo(StrictModel):
    name: str
    visibility: Literal["public", "internal", "protected", "private"]
    annotations: tuple[str, ...]
    modifiers: tuple[str, ...]
    is_suspend: bool
    span: SourceSpan


class KotlinDeclarationInfo(StrictModel):
    kind: Literal["class", "object", "interface", "function", "property", "typealias"]
    name: str
    visibility: Literal["public", "internal", "protected", "private"]
    annotations: tuple[str, ...]
    modifiers: tuple[str, ...]
    super_types: tuple[str, ...] = ()
    span: SourceSpan


class KotlinPropertyInfo(StrictModel):
    name: str
    visibility: Literal["public", "internal", "protected", "private"]
    annotations: tuple[str, ...]
    modifiers: tuple[str, ...]
    type_text: str = ""
    is_lateinit: bool = False
    is_delegated: bool = False
    delegate_call: str = ""
    span: SourceSpan


class CoroutineUsage(StrictModel):
    kind: Literal[
        "viewmodel_scope_launch",
        "global_scope",
        "standalone_scope",
        "with_context",
        "delay",
    ]
    dispatcher: Literal["IO", "Default", "Main", "Unspecified"]
    function_name: str
    span: SourceSpan
    evidence: str


class CallbackUsage(StrictModel):
    kind: str
    function_name: str
    called_identifiers: tuple[str, ...]
    span: SourceSpan


class KotlinCallUsage(StrictModel):
    name: str
    function_name: str
    span: SourceSpan
    evidence: str


class KotlinTestFunctionInfo(StrictModel):
    name: str
    annotations: tuple[str, ...]
    visibility: Literal["public", "internal", "protected", "private"]
    span: SourceSpan
    parameter_list_count: int = 0
    has_extra_parameter_list: bool = False


class KotlinTestMetadata(StrictModel):
    annotations: tuple[str, ...] = ()
    fields: tuple[KotlinPropertyInfo, ...] = ()
    setup_teardown_functions: tuple[KotlinFunctionInfo, ...] = ()
    helper_functions: tuple[KotlinFunctionInfo, ...] = ()
    test_functions: tuple[KotlinTestFunctionInfo, ...] = ()
    config_sdks: tuple[int, ...] = ()
    mock_variables: tuple[str, ...] = ()
    mocked_static_targets: tuple[str, ...] = ()
    direct_lifecycle_call_names: tuple[str, ...] = ()
    property_assignment_verification_lines: tuple[int, ...] = ()
    malformed_test_declaration_lines: tuple[int, ...] = ()
    has_hilt_module_install_in: bool = False
    has_local_hilt_test_activity: bool = False


class FrameworkUsage(StrictModel):
    junit4: bool = False
    junit5: bool = False
    mockito: bool = False
    mockk: bool = False
    robolectric: bool = False
    hilt: bool = False
    coroutine_test: bool = False


class StaticFinding(StrictModel):
    rule_id: str
    category: str
    severity: str
    disposition: FindingDisposition
    span: SourceSpan
    evidence: str
    message: str
    verification: str = ""


class KotlinStaticAnalysisReport(StrictModel):
    schema_version: str
    source_path: str
    source_hash: str
    tree_sitter_language: str
    semgrep_version: str
    status: Literal["complete"]
    imports: tuple[str, ...]
    declarations: tuple[KotlinDeclarationInfo, ...]
    properties: tuple[KotlinPropertyInfo, ...]
    functions: tuple[KotlinFunctionInfo, ...]
    coroutine_usages: tuple[CoroutineUsage, ...]
    callbacks: tuple[CallbackUsage, ...]
    calls: tuple[KotlinCallUsage, ...]
    frameworks: FrameworkUsage
    tests: KotlinTestMetadata
    findings: tuple[StaticFinding, ...]
    validation_errors: tuple[str, ...] = ()

    def function_for_line(self, line_no: int, public_only: bool = False) -> KotlinFunctionInfo | None:
        candidates = [
            item
            for item in self.functions
            if item.span.start_line <= line_no <= item.span.end_line
            and (not public_only or item.visibility != "private")
        ]
        return min(candidates, key=lambda item: item.span.end_line - item.span.start_line) if candidates else None

    def confirmed_findings(self, *categories: str) -> tuple[StaticFinding, ...]:
        allowed = set(categories)
        return tuple(
            finding
            for finding in self.findings
            if finding.disposition == FindingDisposition.CONFIRMED
            and (not allowed or finding.category in allowed)
        )


_REPORT_CACHE: dict[tuple[str, str], KotlinStaticAnalysisReport] = {}
_PREFLIGHT_VERSION = ""


def analyze_kotlin_code(
    source_code: str,
    source_path: str = "",
    *,
    persist: bool = False,
    expected_mocking_framework: str = "mockito",
) -> KotlinStaticAnalysisReport:
    source_hash = hashlib.sha256((source_code or "").encode("utf-8")).hexdigest()
    cache_key = (source_hash, expected_mocking_framework)
    cached = _REPORT_CACHE.get(cache_key)
    if cached:
        resolved = cached.model_copy(update={"source_path": source_path}) if source_path and cached.source_path != source_path else cached
        if persist and source_path:
            persist_static_analysis_report(resolved)
        return resolved

    imports = tuple(kotlin_imports(source_code or ""))
    declarations = tuple(_extract_declarations(source_code or ""))
    properties = tuple(_extract_properties(source_code or ""))
    functions = tuple(_extract_functions(source_code or ""))
    coroutine_usages = tuple(_extract_coroutine_usages(source_code or "", functions))
    callbacks = tuple(_extract_callbacks(source_code or "", functions))
    calls = tuple(_extract_calls(source_code or "", functions))
    frameworks = _detect_frameworks(imports, source_code or "")
    tests = _extract_test_metadata(source_code or "", functions, properties, calls)
    semgrep_runtime_version = require_semgrep_preflight()
    policy_relevant = bool(
        frameworks.mockk
        or any(
            token in (source_code or "")
            for token in (
                "Dispatchers.IO",
                "Dispatchers.Default",
                "GlobalScope",
                "CoroutineScope(",
                "mockk(",
                "coEvery",
                "coVerify",
                "mockkObject",
                "mockkStatic",
            )
        )
    )
    rule_configs = [SEMGREP_RULE_CONFIG] if policy_relevant else []
    is_test_candidate = bool(
        frameworks.junit4
        or frameworks.junit5
        or frameworks.mockito
        or frameworks.mockk
        or "/src/test/" in (source_path or "").replace("\\", "/")
    )
    if is_test_candidate and any(
        identifier.endswith("Exception") or (identifier.endswith("Error") and identifier != "Error")
        for identifier in _identifier_texts(source_code or "")
    ):
        rule_configs.append(SEMGREP_EXCEPTION_RULE_CONFIG)
    scans = ()
    if rule_configs:
        scan_jobs = 1 if len(rule_configs) > 1 or len((source_code or "").splitlines()) > 500 else None
        try:
            scans = (
                scan_text_with_semgrep(
                    source_code or "",
                    tuple(rule_configs),
                    ".kt",
                    jobs=scan_jobs,
                    source_path=source_path,
                ),
            )
        except SemgrepUnavailableError as exc:
            message = str(exc).lower()
            if not isinstance(exc, SemgrepParseError) and not any(
                marker in message
                for marker in ("reported analysis errors:", "syntax error", "was unexpected")
            ):
                raise
            log_message(
                "⚠️ Semgrep could not parse this Kotlin snippet; continuing with tree-sitter analysis only. "
                f"{exc}",
                category="warning",
            )
    findings = tuple(
        _normalize_finding(item, source_code or "", imports, frameworks, expected_mocking_framework)
        for scan in scans
        for item in scan.findings
    )
    validation_errors = tuple(
        f"invalid_kotlin_syntax: malformed backtick function declaration at line {line_no}"
        for line_no, line in enumerate((source_code or "").splitlines(), start=1)
        if re.search(r"\bfun\s+`[^`]*\([^`]*\)`\s*\{", line)
    )
    report = KotlinStaticAnalysisReport(
        schema_version=REPORT_SCHEMA_VERSION,
        source_path=source_path,
        source_hash=source_hash,
        tree_sitter_language="kotlin",
        semgrep_version=("/".join(dict.fromkeys(scan.version for scan in scans)) if scans else semgrep_runtime_version),
        status="complete",
        imports=imports,
        declarations=declarations,
        properties=properties,
        functions=functions,
        coroutine_usages=coroutine_usages,
        callbacks=callbacks,
        calls=calls,
        frameworks=frameworks,
        tests=tests,
        findings=findings,
        validation_errors=validation_errors,
    )
    _REPORT_CACHE[cache_key] = report
    if persist and source_path:
        persist_static_analysis_report(report)
    return report


def analyze_kotlin_test_code(test_code: str, source_code: str = "") -> KotlinStaticAnalysisReport:
    del source_code
    return analyze_kotlin_code(test_code or "", expected_mocking_framework="mockito")


def persist_static_analysis_report(report: KotlinStaticAnalysisReport) -> str:
    os.makedirs(get_pipeline_log_dir(), exist_ok=True)
    stem = Path(report.source_path or "KotlinSource.kt").stem
    path = os.path.join(get_pipeline_log_dir(), f"{stem}.static-analysis.json")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(report.model_dump_json(indent=2) + "\n")
    log_message(f"🔎 Wrote strict static analysis report: {path}", category="context", console=False)
    return path


def verify_project_exception_findings(
    report: KotlinStaticAnalysisReport,
    project_root: str,
    exclude_path: str = "",
) -> KotlinStaticAnalysisReport:
    if not project_root or not os.path.isdir(project_root):
        return report
    updated = []
    for finding in report.findings:
        if (
            finding.category != "speculative_sdk_exception_constructor"
            or finding.disposition != FindingDisposition.CANDIDATE
        ):
            updated.append(finding)
            continue
        exception_name = finding.evidence.split("(", 1)[0].strip().split(".")[-1]
        nested_shape = ".Subcode" in finding.evidence or f"{exception_name}." in finding.evidence
        verified_usage = _project_has_exception_shape(
            project_root,
            exception_name,
            nested_shape,
            exclude_path,
        )
        if verified_usage:
            updated.append(
                finding.model_copy(
                    update={
                        "disposition": FindingDisposition.DISMISSED,
                        "verification": "A matching project declaration or established constructor/member usage was found.",
                    }
                )
            )
        elif nested_shape:
            updated.append(
                finding.model_copy(
                    update={
                        "disposition": FindingDisposition.CONFIRMED,
                        "verification": "No project declaration or established usage verifies this nested SDK constructor/member shape.",
                    }
                )
            )
        else:
            updated.append(finding)
    return report.model_copy(update={"findings": tuple(updated)})


def require_semgrep_preflight() -> str:
    global _PREFLIGHT_VERSION
    if _PREFLIGHT_VERSION:
        return _PREFLIGHT_VERSION
    require_semgrep_executable()
    for config_path in (SEMGREP_RULE_CONFIG, SEMGREP_EXCEPTION_RULE_CONFIG):
        if not os.path.isfile(config_path):
            raise SemgrepUnavailableError(f"Mandatory local Semgrep rules are missing: {config_path}")
    scan = scan_text_with_semgrep(
        "class SemgrepPreflight",
        SEMGREP_RULE_CONFIG,
        ".kt",
        jobs=1,
    )
    try:
        package_version = version("semgrep")
    except PackageNotFoundError:
        package_version = "package-unknown"
    _PREFLIGHT_VERSION = f"package={package_version}, core={scan.version}"
    return _PREFLIGHT_VERSION


def _span(node) -> SourceSpan:
    return SourceSpan(
        start_line=node.start_point[0] + 1,
        start_column=node.start_point[1] + 1,
        end_line=node.end_point[0] + 1,
        end_column=node.end_point[1] + 1,
    )


def _identifier_texts(source_code: str) -> set[str]:
    source_bytes = source_code.encode("utf-8")
    return {
        ast_text(source_bytes, node)
        for node in walk_ast(parse_kotlin_ast(source_code))
        if node.type == "identifier"
    }


def _modifier_texts(source_bytes: bytes, node) -> tuple[str, ...]:
    modifiers_node = direct_child(node, "modifiers")
    return tuple(
        ast_text(source_bytes, child)
        for child in (modifiers_node.children if modifiers_node else ())
        if ast_text(source_bytes, child).strip() and child.type != "annotation"
    )


def _declaration_visibility(source_bytes: bytes, node) -> tuple[str, tuple[str, ...]]:
    texts = _modifier_texts(source_bytes, node)
    for visibility in ("private", "protected", "internal", "public"):
        if visibility in texts:
            return visibility, texts
    return "public", texts


def _variable_name(source_bytes: bytes, node) -> str:
    variable = direct_child(node, "variable_declaration")
    if not variable:
        return declaration_name(source_bytes, node)
    identifier = direct_child(variable, "identifier")
    return ast_text(source_bytes, identifier) if identifier else ""


def _property_type_text(source_bytes: bytes, node) -> str:
    variable = direct_child(node, "variable_declaration")
    if not variable:
        return ""
    type_node = direct_child(variable, "type")
    return ast_text(source_bytes, type_node).removeprefix(":").strip() if type_node else ""


def _super_types(source_bytes: bytes, node) -> tuple[str, ...]:
    specs = direct_child(node, "delegation_specifiers")
    if not specs:
        return ()
    values = []
    for spec in direct_children(specs, "delegation_specifier"):
        user_type = next((child for child in walk_ast(spec) if child.type == "user_type"), None)
        if user_type:
            values.append(ast_text(source_bytes, user_type).split("<", 1)[0].strip())
    return tuple(dict.fromkeys(value for value in values if value))


def _declaration_kind(node) -> str:
    return {
        "class_declaration": "class",
        "object_declaration": "object",
        "interface_declaration": "interface",
        "function_declaration": "function",
        "property_declaration": "property",
        "typealias_declaration": "typealias",
    }.get(node.type, "")


def _extract_declarations(source_code: str) -> list[KotlinDeclarationInfo]:
    source_bytes = source_code.encode("utf-8")
    root = parse_kotlin_ast(source_code)
    declarations = []
    for node in walk_ast(root):
        kind = _declaration_kind(node)
        if not kind:
            continue
        name = _variable_name(source_bytes, node) if kind == "property" else declaration_name(source_bytes, node)
        if not name:
            continue
        visibility, modifiers = _declaration_visibility(source_bytes, node)
        declarations.append(
            KotlinDeclarationInfo(
                kind=kind,
                name=name,
                visibility=visibility,
                annotations=tuple(sorted(annotation_names(source_bytes, node))),
                modifiers=modifiers,
                super_types=_super_types(source_bytes, node) if kind in {"class", "object", "interface"} else (),
                span=_span(node),
            )
        )
    return declarations


def _extract_properties(source_code: str) -> list[KotlinPropertyInfo]:
    source_bytes = source_code.encode("utf-8")
    root = parse_kotlin_ast(source_code)
    properties = []
    for node in walk_ast(root):
        if node.type != "property_declaration":
            continue
        name = _variable_name(source_bytes, node)
        if not name:
            continue
        visibility, modifiers = _declaration_visibility(source_bytes, node)
        delegate = direct_child(node, "property_delegate")
        delegate_call = ast_text(source_bytes, delegate).removeprefix("by").strip() if delegate else ""
        properties.append(
            KotlinPropertyInfo(
                name=name,
                visibility=visibility,
                annotations=tuple(sorted(annotation_names(source_bytes, node))),
                modifiers=modifiers,
                type_text=_property_type_text(source_bytes, node),
                is_lateinit="lateinit" in modifiers,
                is_delegated=delegate is not None,
                delegate_call=delegate_call,
                span=_span(node),
            )
        )
    return properties


def _extract_functions(source_code: str) -> list[KotlinFunctionInfo]:
    source_bytes = source_code.encode("utf-8")
    root = parse_kotlin_ast(source_code)
    functions = []
    for node in walk_ast(root):
        if node.type != "function_declaration":
            continue
        name = declaration_name(source_bytes, node)
        if not name:
            continue
        visibility, modifiers = _declaration_visibility(source_bytes, node)
        functions.append(
            KotlinFunctionInfo(
                name=name,
                visibility=visibility,
                annotations=tuple(sorted(annotation_names(source_bytes, node))),
                modifiers=modifiers,
                is_suspend="suspend" in modifiers,
                span=_span(node),
            )
        )
    return functions


def _annotation_texts(source_code: str) -> tuple[str, ...]:
    source_bytes = source_code.encode("utf-8")
    return tuple(
        dict.fromkeys(
            ast_text(source_bytes, node).strip()
            for node in walk_ast(parse_kotlin_ast(source_code))
            if node.type == "annotation"
        )
    )


def _config_sdks(annotation_texts: tuple[str, ...]) -> tuple[int, ...]:
    values: list[int] = []
    for text in annotation_texts:
        if not text.startswith("@Config") or "sdk" not in text:
            continue
        digits = ""
        in_sdk_value = False
        for char in text.split("sdk", 1)[1]:
            if char.isdigit():
                digits += char
                in_sdk_value = True
            elif in_sdk_value:
                break
        if digits:
            values.append(int(digits))
    return tuple(values)


def _extract_test_metadata(
    source_code: str,
    functions: tuple[KotlinFunctionInfo, ...],
    properties: tuple[KotlinPropertyInfo, ...],
    calls: tuple[KotlinCallUsage, ...],
) -> KotlinTestMetadata:
    source_bytes = source_code.encode("utf-8")
    root = parse_kotlin_ast(source_code)
    annotations = _annotation_texts(source_code)
    test_functions = []
    setup_teardown = []
    helpers = []
    for function in functions:
        annotation_set = set(function.annotations)
        parameter_lists = 0
        has_extra_parameter_list = False
        for node in walk_ast(root):
            if node.type == "function_declaration" and _span(node) == function.span:
                parameter_lists = sum(1 for child in node.children if child.type == "function_value_parameters")
                text = ast_text(source_bytes, node)
                has_extra_parameter_list = ")(" in text.split("{", 1)[0]
                break
        if "Test" in annotation_set:
            test_functions.append(
                KotlinTestFunctionInfo(
                    name=function.name,
                    annotations=function.annotations,
                    visibility=function.visibility,
                    span=function.span,
                    parameter_list_count=parameter_lists,
                    has_extra_parameter_list=has_extra_parameter_list,
                )
            )
        elif annotation_set & {"Before", "After", "BeforeEach", "AfterEach"}:
            setup_teardown.append(function)
        else:
            helpers.append(function)

    mock_variables = tuple(
        sorted(
            property_info.name
            for property_info in properties
            if any(call.name == "mock" and property_info.span.start_line == call.span.start_line for call in calls)
        )
    )
    mocked_static_targets = []
    for call in calls:
        if call.name != "mockStatic":
            continue
        target = call.evidence.split("(", 1)[1].split("::class", 1)[0].strip() if "(" in call.evidence else ""
        if target:
            mocked_static_targets.append(target)
    lifecycle_names = tuple(
        call.name
        for call in calls
        if call.name in {"onAttach", "onCreate", "onCreateView", "onViewCreated", "onStart", "onResume"}
    )
    property_assignment_lines = []
    malformed_test_declaration_lines = []
    for node in walk_ast(root):
        if node.type == "ERROR":
            text = ast_text(source_bytes, node)
            if "@Test" in text and ")(" in text:
                malformed_test_declaration_lines.append(node.start_point[0] + 1)
        if node.type == "assignment":
            text = ast_text(source_bytes, node)
            if text.strip().startswith("verify(") or "verify(" in text.split("=", 1)[0]:
                property_assignment_lines.append(node.start_point[0] + 1)

    return KotlinTestMetadata(
        annotations=annotations,
        fields=tuple(properties),
        setup_teardown_functions=tuple(setup_teardown),
        helper_functions=tuple(helpers),
        test_functions=tuple(test_functions),
        config_sdks=_config_sdks(annotations),
        mock_variables=mock_variables,
        mocked_static_targets=tuple(dict.fromkeys(mocked_static_targets)),
        direct_lifecycle_call_names=tuple(dict.fromkeys(lifecycle_names)),
        property_assignment_verification_lines=tuple(sorted(set(property_assignment_lines))),
        malformed_test_declaration_lines=tuple(sorted(set(malformed_test_declaration_lines))),
        has_hilt_module_install_in=any(text.startswith("@Module") for text in annotations)
        and any(text.startswith("@InstallIn") for text in annotations),
        has_local_hilt_test_activity=any(
            declaration.name == "HiltTestActivity" and declaration.kind == "class"
            for declaration in _extract_declarations(source_code)
        ),
    )


def _owner_function_for_span(functions: tuple[KotlinFunctionInfo, ...], line_no: int) -> KotlinFunctionInfo | None:
    candidates = [item for item in functions if item.span.start_line <= line_no <= item.span.end_line]
    if not candidates:
        return None
    return min(candidates, key=lambda item: item.span.end_line - item.span.start_line)


def _owner_for_span(functions: tuple[KotlinFunctionInfo, ...], line_no: int) -> str:
    owner = _owner_function_for_span(functions, line_no)
    return owner.name if owner is not None else "<top-level>"


def _dispatcher(text: str) -> str:
    if "Dispatchers.IO" in text:
        return "IO"
    if "Dispatchers.Default" in text:
        return "Default"
    if "Dispatchers.Main" in text:
        return "Main"
    return "Unspecified"


def _extract_coroutine_usages(source_code: str, functions: tuple[KotlinFunctionInfo, ...]) -> list[CoroutineUsage]:
    source_bytes = source_code.encode("utf-8")
    root = parse_kotlin_ast(source_code)
    usages: list[CoroutineUsage] = []
    seen: set[tuple[str, int, int]] = set()
    for node in walk_ast(root):
        if node.type != "call_expression":
            continue
        text = ast_text(source_bytes, node)
        kind = ""
        if text.startswith("viewModelScope.launch"):
            kind = "viewmodel_scope_launch"
        elif text.startswith("GlobalScope."):
            kind = "global_scope"
        elif text.startswith("CoroutineScope(") and (".launch" in text or ".async" in text):
            kind = "standalone_scope"
        elif text.startswith("withContext("):
            kind = "with_context"
        elif text.startswith("delay("):
            kind = "delay"
        if not kind:
            continue
        key = (kind, node.start_point[0], node.start_point[1])
        if key in seen:
            continue
        seen.add(key)
        usages.append(
            CoroutineUsage(
                kind=kind,
                dispatcher=_dispatcher(text),
                function_name=_owner_for_span(functions, node.start_point[0] + 1),
                span=_span(node),
                evidence=text.splitlines()[0][:500],
            )
        )
    return usages


def _extract_callbacks(source_code: str, functions: tuple[KotlinFunctionInfo, ...]) -> list[CallbackUsage]:
    source_bytes = source_code.encode("utf-8")
    root = parse_kotlin_ast(source_code)
    callbacks = []
    for node in walk_ast(root):
        if node.type != "call_expression":
            continue
        callback_names = {
            "setMenuItems",
            "setOnClickListener",
            "setOnLongClickListener",
            "registerBackListener",
            "observeForever",
            "observe",
            "onEach",
            "collectLatest",
            "collect",
            "postDelayed",
            "registerForActivityResult",
            "setFragmentResultListener",
        }
        callee_identifiers = [
            ast_text(source_bytes, child)
            for child in walk_ast(node.children[0])
            if child.type == "identifier"
        ]
        kind = next((name for name in callee_identifiers if name in callback_names), "")
        if not kind:
            kind = next((name for name in callee_identifiers if "Callback" in name), "")
        if not kind:
            continue
        identifiers = tuple(
            sorted(
                {
                    ast_text(source_bytes, child)
                    for child in walk_ast(node)
                    if child.type == "identifier"
                }
            )
        )
        callbacks.append(
            CallbackUsage(
                kind=kind,
                function_name=_owner_for_span(functions, node.start_point[0] + 1),
                called_identifiers=identifiers,
                span=_span(node),
            )
        )
    return [
        callback
        for callback in callbacks
        if not any(
            other is not callback
            and other.kind == callback.kind
            and other.function_name == callback.function_name
            and (other.span.start_line, other.span.start_column)
            <= (callback.span.start_line, callback.span.start_column)
            and (callback.span.end_line, callback.span.end_column)
            <= (other.span.end_line, other.span.end_column)
            and other.span != callback.span
            for other in callbacks
        )
    ]


def _extract_calls(source_code: str, functions: tuple[KotlinFunctionInfo, ...]) -> list[KotlinCallUsage]:
    source_bytes = source_code.encode("utf-8")
    root = parse_kotlin_ast(source_code)
    calls = []
    for node in walk_ast(root):
        if node.type != "call_expression" or not node.children:
            continue
        callee = node.children[0]
        identifiers = [
            ast_text(source_bytes, child)
            for child in walk_ast(callee)
            if child.type == "identifier"
        ]
        if not identifiers:
            continue
        calls.append(
            KotlinCallUsage(
                name=identifiers[-1],
                function_name=_owner_for_span(functions, node.start_point[0] + 1),
                span=_span(node),
                evidence=ast_text(source_bytes, node).splitlines()[0][:500],
            )
        )
    return calls


def _detect_frameworks(imports: tuple[str, ...], source_code: str) -> FrameworkUsage:
    joined = "\n".join(imports)
    return FrameworkUsage(
        junit4="org.junit." in joined and "org.junit.jupiter" not in joined,
        junit5="org.junit.jupiter" in joined,
        mockito="org.mockito" in joined or "Mockito." in source_code or "whenever(" in source_code,
        mockk="io.mockk" in joined,
        robolectric="org.robolectric" in joined,
        hilt="dagger.hilt" in joined or "@Hilt" in source_code,
        coroutine_test="kotlinx.coroutines.test" in joined,
    )


def _normalize_finding(
    raw: dict,
    source_code: str,
    imports: tuple[str, ...],
    frameworks: FrameworkUsage,
    expected_mocking_framework: str,
) -> StaticFinding:
    raw_rule_id = str(raw.get("check_id") or "unknown")
    rule_id = next(
        (rule for rule in (
            "kotlin.hardcoded-dispatcher",
            "kotlin.detached-coroutine-scope",
            "kotlin.mockk-api",
            "kotlin.sdk-exception-constructor-candidate",
        ) if raw_rule_id.endswith(rule)),
        raw_rule_id,
    )
    extra = raw.get("extra") or {}
    start = raw.get("start") or {}
    end = raw.get("end") or start
    start_line = int(start.get("line") or 1)
    end_line = int(end.get("line") or start_line)
    lines = source_code.splitlines()
    start_offset = start.get("offset")
    end_offset = end.get("offset")
    if isinstance(start_offset, int) and isinstance(end_offset, int):
        evidence = source_code[start_offset:end_offset].strip()
    else:
        evidence = "\n".join(lines[max(0, start_line - 1):min(len(lines), end_line)]).strip()
    category = {
        "kotlin.hardcoded-dispatcher": "hardcoded_dispatcher",
        "kotlin.detached-coroutine-scope": "detached_coroutine_scope",
        "kotlin.mockk-api": "mocking_framework_mismatch",
        "kotlin.sdk-exception-constructor-candidate": "speculative_sdk_exception_constructor",
    }.get(rule_id, "static_policy")
    disposition = FindingDisposition.CONFIRMED
    verification = "Matched a local deterministic Semgrep policy."
    if category == "mocking_framework_mismatch":
        if expected_mocking_framework != "mockito":
            disposition = FindingDisposition.DISMISSED
            verification = "The configured project mocking framework is not Mockito."
        else:
            verification = "The generator/project lane is Mockito-only; MockK APIs are incompatible."
    elif category == "speculative_sdk_exception_constructor":
        exception_name = evidence.split("(", 1)[0].strip().split(".")[-1]
        line_evidence = "\n".join(lines[max(0, start_line - 1):min(len(lines), end_line)]).strip()
        standard = exception_name in {
            "Exception", "RuntimeException", "IllegalArgumentException", "IllegalStateException",
            "NullPointerException", "UnsupportedOperationException", "IOException", "AssertionError",
        }
        declared_here = any(f"class {exception_name}" in line for line in lines)
        nested_shape = ".Subcode" in line_evidence or f"{exception_name}." in line_evidence
        imported = next((path for path in imports if path.endswith("." + exception_name)), "")
        if standard or declared_here:
            disposition = FindingDisposition.DISMISSED
            verification = "The exception is standard or declared in the analyzed source."
        else:
            disposition = FindingDisposition.CANDIDATE
            verification = (
                "External nested exception shape requires project verification."
                if nested_shape and imported
                else "No verified constructor declaration/usage was available; do not block until project context confirms it."
            )
    return StaticFinding(
        rule_id=rule_id,
        category=category,
        severity=str(extra.get("severity") or "WARNING"),
        disposition=disposition,
        span=SourceSpan(
            start_line=start_line,
            start_column=int(start.get("col") or 1),
            end_line=end_line,
            end_column=int(end.get("col") or start.get("col") or 1),
        ),
        evidence=evidence,
        message=str(extra.get("message") or "Semgrep policy finding"),
        verification=verification,
    )


@lru_cache(maxsize=256)
def _project_has_exception_shape(
    project_root: str,
    exception_name: str,
    nested_shape: bool,
    exclude_path: str,
) -> bool:
    excluded = os.path.abspath(exclude_path) if exclude_path else ""
    declaration_markers = (f"class {exception_name}", f"interface {exception_name}", f"enum class {exception_name}")
    usage_markers = (f"{exception_name}.Subcode", f"{exception_name}(") if nested_shape else (f"{exception_name}(",)
    for root, directories, files in os.walk(project_root):
        directories[:] = [name for name in directories if name not in {"build", ".gradle", ".git", ".cache"}]
        for file_name in files:
            if not file_name.endswith((".kt", ".java")):
                continue
            path = os.path.abspath(os.path.join(root, file_name))
            if excluded and path == excluded:
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                    text = handle.read()
            except OSError:
                continue
            if any(marker in text for marker in declaration_markers) or any(marker in text for marker in usage_markers):
                return True
    return False


__all__ = [
    "CallbackUsage",
    "CoroutineUsage",
    "FindingDisposition",
    "FrameworkUsage",
    "KotlinDeclarationInfo",
    "KotlinFunctionInfo",
    "KotlinCallUsage",
    "KotlinPropertyInfo",
    "KotlinStaticAnalysisReport",
    "KotlinTestFunctionInfo",
    "KotlinTestMetadata",
    "SemgrepParseError",
    "SemgrepUnavailableError",
    "StaticFinding",
    "analyze_kotlin_code",
    "analyze_kotlin_test_code",
    "persist_static_analysis_report",
    "require_semgrep_preflight",
    "verify_project_exception_findings",
]
