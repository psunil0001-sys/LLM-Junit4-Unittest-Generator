"""Post-agent YAML/AST guardrails for generated Kotlin tests."""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml

from UnitTest_gen.core import io as file_cache
from UnitTest_gen.core.io import DEFAULT_SKIP_DIRS, walk_source_roots
from UnitTest_gen.kotlin.analysis import (
    analyze_kotlin_code,
    classify_source,
    extract_junit4_test_blocks,
    has_apollo_response_extension_function,
    kotlin_declared_class_names,
    kotlin_member_extension_functions,
    kotlin_top_level_class_name,
    source_declares_android_fragment,
    source_declares_viewmodel_class,
    source_get_instance_types,
    source_uses_carui_toolbar_progress,
    suspend_function_names_from_ast,
    verify_project_exception_findings,
)
from UnitTest_gen.kotlin.project import (
    _ANDROID_R_REF_PATTERN,
    choose_robolectric_sdk_for_module,
    collect_module_test_dependency_versions,
    find_owning_module_dir,
    find_project_root_for_path,
    find_single_same_type_resource_match,
    is_android_platform_installed,
    load_verified_android_resource_index,
    lookup_verified_android_resource,
    missing_platform_in_range_reason,
    module_has_hilt_robolectric_fragment_support,
    module_has_shared_test_hilt_binding,
    owning_module_dir_for_output,
    parse_module_compile_sdk,
    parse_module_min_sdk,
    parse_module_namespace,
    parse_module_robolectric_version,
    project_robolectric_version,
)

PACKAGE_DIR = Path(__file__).resolve().parents[1]
_DEFAULT_REPAIR = "Repair the stable invalid pattern before running Gradle."

@lru_cache
def _catalog_repair_intents() -> dict:
    path = PACKAGE_DIR / "data" / "prompt_skeletons" / "validation_repair_intents.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
    return data if isinstance(data, dict) else {}

def catalog_repair_intent(category: str) -> str:
    return _catalog_repair_intents().get(category, _DEFAULT_REPAIR)

# Alias used by sections that imported catalog intent under this name
validation_repair_intent_catalog = catalog_repair_intent

def format_prefixed_repair_intent(prefix: str, detail: str | None = None) -> str:
    """Format ``prefix: <catalog repair intent for detail-or-prefix>``."""
    return f"{prefix}: {catalog_repair_intent(detail or prefix)}"

def validation_repair_intent(category: str, detail: str | None = None) -> str:
    """Issue string with stable category code (1-arg) or repair_prefix + detail (2-arg)."""
    if detail is not None:
        return format_prefixed_repair_intent(category, detail)
    return format_prefixed_repair_intent(category)

def find_kotlin_object_in_project(project_root: str | None, name: str) -> bool:
    """True when ``object name`` is declared under the project's ``src/main`` tree."""
    if not project_root or not name:
        return False
    object_pattern = re.compile(rf"(?m)^\s*object\s+{re.escape(name)}\b")
    for file_path in walk_source_roots(
        project_root,
        roots=("src/main",),
        skip_dirs=DEFAULT_SKIP_DIRS,
        extensions=(".kt",),
    ):
        text = file_cache.read_text(file_path, default="")
        if text and object_pattern.search(text):
            return True
    return False

RULES_DIR = PACKAGE_DIR / "data" / "validation_rules"
_RE_FLAGS = {"I": re.IGNORECASE, "M": re.MULTILINE, "S": re.DOTALL}

def _compile(pattern: str, flags: str = "") -> re.Pattern[str]:
    flag_bits = 0
    for char in flags:
        flag_bits |= _RE_FLAGS.get(char, 0)
    return re.compile(pattern, flag_bits)

def _field_text(name: str, test_code: str, source_code: str, ctx: dict[str, Any]) -> str:
    if name == "test_code":
        return test_code or ""
    if name == "source_code":
        return source_code or ""
    return str(ctx.get(name) or "")

def _regex_match(pattern: str | re.Pattern[str], text: str, flags: str = "") -> bool:
    if not pattern:
        return True
    compiled = pattern if isinstance(pattern, re.Pattern) else _compile(str(pattern), flags)
    return bool(compiled.search(text))

def _ctx_matches(when: dict[str, Any], ctx: dict[str, Any]) -> bool:
    ctx_rules = when.get("ctx") or {}
    if not isinstance(ctx_rules, dict):
        return True
    for key, expected in ctx_rules.items():
        if key.endswith("_true"):
            base = key[: -len("_true")]
            if not ctx.get(base):
                return False
            continue
        if key.endswith("_false"):
            base = key[: -len("_false")]
            if ctx.get(base):
                return False
            continue
        if key.endswith("_any"):
            base = key[: -len("_any")]
            values = expected if isinstance(expected, (list, tuple, set)) else (expected,)
            if ctx.get(base) not in values:
                return False
            continue
        if ctx.get(key) != expected:
            return False
    return True

def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]

def _when_matches(when: dict[str, Any], *, test_code: str, source_code: str, ctx: dict[str, Any]) -> bool:
    if not isinstance(when, dict):
        return False

    test = _field_text("test_code", test_code, source_code, ctx)
    source = _field_text("source_code", test_code, source_code, ctx)
    flags = when.get("flags", "")

    for pattern in _as_list(when.get("test_not")):
        if _regex_match(pattern, test, flags):
            return False
    for pattern in _as_list(when.get("source_not")):
        if _regex_match(pattern, source, flags):
            return False
    for token in _as_list(when.get("test_not_contains")):
        if token in test:
            return False
    for token in _as_list(when.get("source_not_any")):
        if token in source:
            return False
    for token in _as_list(when.get("source_not_contains")):
        if token in source:
            return False

    all_patterns = _as_list(when.get("test")) + _as_list(when.get("test_all"))
    if all_patterns and not all(_regex_match(p, test, flags) for p in all_patterns):
        return False
    any_patterns = _as_list(when.get("test_any"))
    if any_patterns and not any(_regex_match(p, test, flags) for p in any_patterns):
        return False

    required_source = _as_list(when.get("source")) or []
    if required_source and not any(_regex_match(p, source, flags) for p in required_source):
        return False

    for token in _as_list(when.get("test_contains")):
        if token not in test:
            return False
    contains_any = _as_list(when.get("test_contains_any"))
    if contains_any and not any(token in test for token in contains_any):
        return False
    for token in _as_list(when.get("source_all")):
        if token not in source:
            return False
    for token in _as_list(when.get("source_contains")):
        if token not in source:
            return False
    source_any = _as_list(when.get("source_any"))
    if source_any and not any(
        (token in source if isinstance(token, str) else _regex_match(token, source, flags))
        for token in source_any
    ):
        return False

    return _ctx_matches(when, ctx)

def _render_message(rule: dict[str, Any]) -> str:
    if rule.get("message"):
        return str(rule["message"])
    repair_key = rule.get("repair")
    if not repair_key:
        return ""
    prefix = str(rule.get("repair_prefix") or "").strip()
    detail = str(rule.get("repair_detail") or repair_key)
    if prefix:
        return validation_repair_intent(prefix, detail)
    return validation_repair_intent(detail)

@lru_cache
def load_rules(name: str) -> tuple[dict[str, Any], ...]:
    path = RULES_DIR / name
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rules = data.get("rules") if isinstance(data, dict) else data
    if not isinstance(rules, list):
        return ()
    return tuple(rule for rule in rules if isinstance(rule, dict))

def run_pattern_rules(
    test_code: str,
    rules: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    source_code: str = "",
    ctx: dict[str, Any] | None = None,
) -> list[str]:
    """Evaluate declarative regex/substring rules; return issue messages."""
    issues: list[str] = []
    context = dict(ctx or {})
    for rule in rules:
        when = rule.get("when") or {}
        if not _when_matches(when, test_code=test_code or "", source_code=source_code or "", ctx=context):
            continue
        message = _render_message(rule)
        if message:
            issues.append(message)
        if rule.get("stop"):
            break
    return issues

def validation_module_context(output_file_path: str) -> tuple[str | None, str]:
    """Return ``(project_root, module_dir)`` for a generated test path.

    When no Gradle project root is found, ``project_root`` is ``None`` and
    ``module_dir`` falls back to the output file's parent directory.
    """
    project_root = find_project_root_for_path(output_file_path)
    if not project_root:
        return None, os.path.dirname(output_file_path)
    return project_root, find_owning_module_dir(project_root, output_file_path)

_UNIT_ONLY_COLLECTORS = frozenset({
    "collect_robolectric_sdk_validation_issues",
    "collect_activity_validation_issues",
    "collect_lifecycle_validation_issues",
    "collect_fragment_validation_issues",
})

def _is_instrumented_test_path(output_file_path: str) -> bool:
    return "/androidTest/" in output_file_path.replace("\\", "/")

def collect_validation_rule_issues(
    test_code: str,
    output_file_path: str,
    source_code: str,
    test_report,
    *,
    existing_test_code: str = "",
) -> list[str]:
    """Run every registered validation rule and return merged issue strings."""
    from UnitTest_gen.kotlin.validate import VALIDATION_RULES
    from UnitTest_gen.kotlin.validate import collect_validation_issues as collect_coroutines_validation_issues
    from UnitTest_gen.kotlin.validate import collect_mocking_lane_validation_issues

    existing_test_aware = {
        collect_mocking_lane_validation_issues,
        collect_coroutines_validation_issues,
    }
    issues: list[str] = []
    for collect_rule_issues in VALIDATION_RULES:
        if collect_rule_issues in existing_test_aware:
            collected = collect_rule_issues(
                test_code,
                output_file_path,
                source_code,
                test_report,
                existing_test_code=existing_test_code,
            )
        elif _is_instrumented_test_path(output_file_path) and collect_rule_issues.__name__ in _UNIT_ONLY_COLLECTORS:
            collected = []
        else:
            collected = collect_rule_issues(test_code, output_file_path, source_code, test_report)
        issues.extend(collected or [])
    return issues

MockingLane = Literal["mockito", "mockk", "open"]

WHENEVER_IMPORT_MESSAGE = (
    "Kotlin tests that use whenever() must import org.mockito.kotlin.whenever "
    "(or import org.mockito.kotlin.*). "
    "Use Mockito-Kotlin `whenever` for normal Kotlin mock stubbing."
)
_BARE_WHENEVER_CALL = re.compile(r"(?<![.\w])whenever\s*\(")
_MOCKITO_KOTLIN_STAR_IMPORT = re.compile(r"(?m)^\s*import\s+org\.mockito\.kotlin\.\*\s*$")

def has_mockito_kotlin_whenever(test_code: str) -> bool:
    """True when TARGET imports Mockito-Kotlin whenever (explicit or star)."""
    code = test_code or ""
    if re.search(r"(?m)^import org\.mockito\.kotlin\.whenever\s*$", code):
        return True
    return bool(_MOCKITO_KOTLIN_STAR_IMPORT.search(code))

def uses_bare_mockito_kotlin_whenever(test_code: str) -> bool:
    """Top-level whenever(...) calls — excludes Mockito-Kotlin extension `.whenever(...)`."""
    return bool(_BARE_WHENEVER_CALL.search(test_code or ""))

def mockito_whenever_import_issue(test_code: str) -> str | None:
    if uses_bare_mockito_kotlin_whenever(test_code) and not has_mockito_kotlin_whenever(test_code):
        return WHENEVER_IMPORT_MESSAGE
    return None

VOID_NAVIGATION_STUB_PATTERN = re.compile(
    r"(?ms)^[ \t]*(?:(?:org\.mockito\.)?Mockito)\.(?:`when`|when)\s*"
    r"\(\s*(?:\n\s*)?"
    r"(?P<receiver>[A-Za-z_][A-Za-z0-9_]*)\.navigate\s*"
    r"\(\s*(?:(?:org\.mockito\.)?Mockito)\.any\s*"
    r"\(\s*NavDeepLinkRequest::class\.java\s*\)\s*\)"
    r"\s*(?:\n\s*)?\)\s*\.\s*thenAnswer\s*\{\s*null\s*\}\s*\n?",
)
_JAVA_SDK_GETINSTANCE_ALLOW = frozenset(
    {
        "FirebaseAnalytics",
        "FirebaseInstallations",
        "FirebaseCrashlytics",
        "FirebaseAuth",
        "FirebaseMessaging",
        "FirebaseApp",
    }
)

def _yaml(name: str, test_code: str, source_code: str = "", ctx: dict | None = None) -> list[str]:
    return run_pattern_rules(test_code, load_rules(name), source_code=source_code, ctx=ctx or {})

def test_uses_mockk(test_code: str) -> bool:
    return bool(re.search(r"\bio\.mockk\b|import\s+io\.mockk|\bmockkObject\b|\bmockkStatic\b|\bcoEvery\b", test_code or ""))

def test_uses_mockito(test_code: str) -> bool:
    return bool(
        re.search(
            r"\borg\.mockito\b|import\s+org\.mockito|\bwhenever\s*\(|\bmockStatic\s*\(|\bMockedStatic\b",
            test_code or "",
        )
    )

def resolve_mocking_lane(
    *,
    source_code: str = "",
    existing_test_code: str = "",
    output_file_path: str = "",
    opportunity_fixture: str = "",
    detected_seams=(),
) -> MockingLane:
    del source_code, output_file_path, opportunity_fixture, detected_seams
    existing = existing_test_code or ""
    if not existing.strip():
        return "open"
    uses_mockk = test_uses_mockk(existing)
    uses_mockito = test_uses_mockito(existing)
    if uses_mockk and not uses_mockito:
        return "mockk"
    if uses_mockito and not uses_mockk:
        return "mockito"
    return "open"

@lru_cache
def _mocking_lane_prompts() -> dict[str, str]:
    path = PACKAGE_DIR / "data" / "validation_rules" / "mocking_lane.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(k): str(v).strip() for k, v in (data.get("lanes") or {}).items()}

def mocking_lane_prompt_block(lane: MockingLane) -> str:
    prompts = _mocking_lane_prompts()
    return prompts.get(lane, prompts.get("open", ""))

def collect_mocking_lane_validation_issues(
    test_code: str,
    output_file_path: str,
    source_code: str,
    test_report,
    *,
    existing_test_code: str = "",
) -> list[str]:
    del test_report
    uses_mockk = test_uses_mockk(test_code)
    uses_mockito = test_uses_mockito(test_code)
    if uses_mockk and uses_mockito:
        return [validation_repair_intent("mixed_mocking_frameworks")]
    lane = resolve_mocking_lane(
        source_code=source_code, existing_test_code=existing_test_code, output_file_path=output_file_path
    )
    if lane == "mockk" and uses_mockito:
        return [validation_repair_intent("mixed_mocking_frameworks")]
    if lane == "mockito" and uses_mockk:
        return [validation_repair_intent("nonstandard_mock_framework")]
    return []

def has_invalid_void_navigation_stubbing(test_code: str) -> bool:
    return bool(VOID_NAVIGATION_STUB_PATTERN.search(test_code or ""))

def has_invalid_java_nav_deeplink_captor(test_code: str) -> bool:
    code = test_code or ""
    names = {
        match.group("name")
        for match in re.finditer(
            r"\b(?:val|var)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
            r"ArgumentCaptor\.forClass\s*\(\s*NavDeepLinkRequest::class\.java\s*\)",
            code,
        )
    }
    return bool(names) and any(
        re.search(rf"\.\s*navigate\s*\(\s*{re.escape(name)}\.capture\s*\(\s*\)\s*\)", code) for name in names
    )

def mocked_static_return_types(test_code: str) -> dict[str, str]:
    return {
        match.group("name"): match.group("type").split(".")[-1]
        for match in re.finditer(
            r"\b(?:private\s+)?(?:var|val)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*:\s*(?:[A-Za-z_][A-Za-z0-9_.$]*\.)?MockedStatic\s*<\s*(?P<type>[A-Za-z_][A-Za-z0-9_.$]*)\s*>",
            test_code or "",
        )
    }

def has_mocked_static_when_missing_return_type(test_code: str) -> bool:
    static_types = mocked_static_return_types(test_code)
    if not static_types:
        return False
    pattern = re.compile(r"\b(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:!!|\?)?\s*\.\s*`when`\s*(?!<)\{")
    return any(match.group("name") in static_types for match in pattern.finditer(test_code or ""))

def has_invalid_singleton_reflection(test_code: str, source_code: str = "") -> bool:
    del source_code
    return bool(
        re.search(r"::class\.java\.getDeclaredField\s*\(\s*\"(?:instance|INSTANCE)\"\s*\)", test_code or "")
    )

def has_invalid_companion_getinstance_static_mock(test_code: str, source_code: str = "") -> bool:
    helpers = source_get_instance_types(source_code) - _JAVA_SDK_GETINSTANCE_ALLOW
    test = test_code or ""
    return any(
        f"mockStatic({helper}::class.java)" in test or f"MockedStatic<{helper}>" in test for helper in helpers
    )

def has_static_stub_eq_mock(test_code: str) -> bool:
    return bool(re.search(r"\beq\s*\(\s*mock\s*(?:<[^>\n]*>)?\s*\(", test_code or ""))

def collect_nested_stub_validation_issues(
    test_code: str, output_file_path: str, source_code: str, test_report
) -> list[str]:
    del output_file_path, source_code
    mock_var_names = set(test_report.tests.mock_variables)
    for receiver, middle, leaf in re.findall(
        r"\b(?:whenever|Mockito\.`when`|Mockito\.when)\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)",
        test_code,
    ):
        if receiver in mock_var_names or receiver.lower().startswith("mock"):
            return [
                "Nested mock property stubbing is unsafe when an intermediate property is not stubbed. "
                f"Stub `{receiver}.{middle}` first or use a real value object before stubbing/asserting `{middle}.{leaf}`."
            ]
    return []

def collect_primitive_matcher_validation_issues(
    test_code: str, output_file_path: str, source_code: str, test_report
) -> list[str]:
    del output_file_path, source_code, test_report
    return _yaml("primitive_matcher.yaml", test_code)

def collect_static_and_navigation_validation_issues(
    test_code: str, output_file_path: str, source_code: str, test_report
) -> list[str]:
    issues: list[str] = []
    if has_invalid_singleton_reflection(test_code, source_code):
        issues.append(validation_repair_intent("invalid_singleton_reflection"))
    if has_invalid_companion_getinstance_static_mock(test_code, source_code):
        issues.append(validation_repair_intent("invalid_alertdialoghelper_static_mock"))
    if has_mocked_static_when_missing_return_type(test_code):
        issues.append(validation_repair_intent("invalid_mockedstatic_missing_return_type"))
    if has_static_stub_eq_mock(test_code):
        issues.append(validation_repair_intent("invalid_static_stub_eq_mock"))
    if has_invalid_void_navigation_stubbing(test_code):
        issues.append(validation_repair_intent("invalid_void_navigation_stubbing"))
    if has_invalid_java_nav_deeplink_captor(test_code):
        issues.append(validation_repair_intent("invalid_nav_deeplink_java_argumentcaptor"))
    issues.extend(_yaml("mockk_patterns.yaml", test_code, source_code))
    if test_report.tests.property_assignment_verification_lines:
        issues.append(validation_repair_intent("invalid_property_assignment_verification"))
    return issues

def collect_mockito_import_and_api_validation_issues(
    test_code: str, output_file_path: str, source_code: str, test_report
) -> list[str]:
    issues: list[str] = []
    whenever_issue = mockito_whenever_import_issue(test_code)
    if whenever_issue:
        issues.append(whenever_issue)
    issues.extend(_yaml("mockito_api_patterns.yaml", test_code, source_code))
    kotlin_any_verify = set(re.findall(r"(?m)^\s*import\s+org\.mockito\.kotlin\.(any|verify)\b", test_code))
    mockito_any_verify = set(re.findall(r"(?m)^\s*import\s+org\.mockito\.Mockito\.(any|verify)\b", test_code))
    if kotlin_any_verify & mockito_any_verify:
        issues.append(
            "mixed_mockito_imports: Do not import the same any/verify symbol from both "
            "org.mockito.kotlin and org.mockito.Mockito. Pick Mockito-Kotlin for normal mocks; "
            "use fully qualified org.mockito.Mockito only for static mocking or doThrow."
        )
    kotlin_object_names = set(re.findall(r"(?m)^\s*object\s+([A-Za-z_][A-Za-z0-9_]*)\b", source_code or ""))
    project_root, _module_dir = validation_module_context(output_file_path)
    for mocked_static_name in re.findall(r"\bmockStatic\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)::class\.java\s*\)", test_code):
        if mocked_static_name in kotlin_object_names:
            continue
        if find_kotlin_object_in_project(project_root, mocked_static_name):
            kotlin_object_names.add(mocked_static_name)
    for object_name in sorted(kotlin_object_names):
        if f"mockStatic({object_name}::class.java)" in test_code:
            issues.append(
                f"{object_name} is a Kotlin object declared in project sources. "
                "Do not use Mockito mockStatic unless a verified @JvmStatic Java API exists. "
                "Prefer real Robolectric/ApplicationProvider behavior, a source-visible dependency seam, or a different public branch."
            )
    mocked_static_type_names = {
        name.rsplit(".", 1)[-1]
        for name in re.findall(
            r"\bmockStatic\s*\(\s*([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)::class\.java\s*\)",
            test_code,
        )
    }
    for match in re.finditer(
        r"whenever\s*\(\s*([A-Z][A-Za-z0-9_]*)\.[A-Za-z0-9_]+\s*\([^)]*\bany(?:<[^>]+>)?\s*\(", test_code
    ):
        if match.group(1) not in mocked_static_type_names:
            issues.append(
                "Generated tests should not stub Kotlin object/framework helper calls as whenever(Object.method(any())). "
                "Mockito evaluates the real call during stubbing and Kotlin non-null parameters receive null from matchers. "
                "Use real Robolectric framework state, open mockStatic(Type::class.java) before stubbing that Type, "
                "or a public branch that does not require object stubbing."
            )
            break
    lines = test_code.splitlines()
    test_bodies = [
        "\n".join(lines[item.span.start_line - 1 : item.span.end_line]) for item in test_report.tests.test_functions
    ]
    if any(
        (
            "ShadowAlertDialog" in body
            or "AlertDialogHelper" in body
            or "getLatestDialog" in body
            or "getLatestAlertDialog" in body
        )
        and (
            "ShadowApplication.getInstance().getNextStartedActivity" in body
            or "ShadowApplication.getInstance().peekNextStartedActivity" in body
        )
        for body in test_bodies
    ):
        issues.append(
            "Dialog positive-button tests that assert context.startActivity should use an Activity "
            "(or Fragment.requireActivity) context, idle ShadowLooper after the button click, and assert "
            "shadowOf(activity).nextStartedActivity. Do not use ApplicationProvider + ShadowApplication "
            "for dialog startActivity paths."
        )
    return issues

def collect_apollo_mock_stub_validation_issues(
    test_code: str, output_file_path: str, source_code: str, test_report
) -> list[str]:
    del output_file_path, source_code, test_report
    return _yaml("apollo_mock_stub.yaml", test_code)

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

def static_policy_validation_issues(
    test_code: str,
    output_file_path: str = "",
    source_code: str = "",
    *,
    existing_test_code: str = "",
) -> list[str]:
    from UnitTest_gen.kotlin.validate import resolve_mocking_lane, test_uses_mockk

    lane = resolve_mocking_lane(
        source_code=source_code,
        existing_test_code=existing_test_code,
        output_file_path=output_file_path,
    )
    mockk_lane = lane == "mockk" and test_uses_mockk(test_code)
    if lane == "open":
        expected = "mockk" if test_uses_mockk(test_code) else "mockito"
    else:
        expected = "mockk" if lane == "mockk" else "mockito"
    report = analyze_kotlin_code(test_code or "", expected_mocking_framework=expected)
    project_root = _project_root_for_output(output_file_path)
    if project_root and os.path.isfile(os.path.join(project_root, "gradlew")):
        report = verify_project_exception_findings(report, project_root, output_file_path)
    issues: list[str] = []
    if report.frameworks.mockk and lane == "mockito" and test_uses_mockk(test_code):
        issues.append(validation_repair_intent("invalid_mocking_framework"))
    elif (
        lane != "open"
        and report.confirmed_findings("mocking_framework_mismatch")
        and not mockk_lane
    ):
        issues.append(validation_repair_intent("invalid_mocking_framework"))
    if report.confirmed_findings("detached_coroutine_scope"):
        issues.append(validation_repair_intent("invalid_detached_coroutine_scope"))
    for finding in report.confirmed_findings("speculative_sdk_exception_constructor"):
        issues.append(
            "invalid_speculative_sdk_exception_constructor: Use only a project-verified SDK exception "
            f"constructor/member shape. Evidence line {finding.span.start_line}: {finding.evidence[:240]}"
        )
    return issues

_STUB_DO_PREFIX = re.compile(r"\bdo(?:Throw|Return|Answer|Nothing)\s*\(")
_MOCKK_STUB_PREFIX = re.compile(r"\bcoEvery\b|\bevery\s*\{")

def _test_function_uses_run_test(owner, lines: list[str]) -> bool:
    """True when the @Test owner runs under runTest/runBlocking (block or expression body)."""
    start = max(0, owner.span.start_line - 1)
    end = min(len(lines), owner.span.end_line)
    block = "\n".join(lines[start:end])
    if "runTest" in block or "runBlocking" in block:
        return True
    header = "\n".join(lines[start : min(len(lines), start + 3)])
    return bool(re.search(r"=\s*run(?:Test|Blocking)\b", header))

def _is_suspend_mock_stub_reference(lines: list[str], line_no: int, call_name: str) -> bool:
    """Suspend method name used as a mock stub target, not a real coroutine invocation."""
    idx = line_no - 1
    if idx < 0 or idx >= len(lines):
        return False
    line = lines[idx]
    escaped = re.escape(call_name)
    if re.search(rf"(?<![.\w])whenever\s*\([^)]*\b{escaped}\s*\(", line):
        return True
    if re.search(rf"\.whenever\s*\([^)]*\)\s*\.\s*{escaped}\s*\(", line):
        return True
    if _STUB_DO_PREFIX.search(line) and re.search(rf"\b{escaped}\s*\(", line):
        return True
    if _MOCKK_STUB_PREFIX.search(line) and re.search(rf"\b{escaped}\s*\(", line):
        return True
    if idx > 0:
        prev = lines[idx - 1]
        if re.search(r"\.whenever\s*\(", prev) and re.search(rf"\b{escaped}\s*\(", line):
            return True
        if _STUB_DO_PREFIX.search(prev) and re.search(rf"\b{escaped}\s*\(", line):
            return True
    return False

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
        if _test_function_uses_run_test(owner, lines):
            continue
        if _is_suspend_mock_stub_reference(lines, call.span.start_line, call.name):
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
            text = file_cache.read_text(path, default="")
            if text:
                names.update(suspend_function_names_from_ast(text))
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
        source = file_cache.read_text(declaration, default="")
        if not source:
            continue
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

def collect_coroutines_validation_issues(
    test_code: str,
    output_file_path: str,
    source_code: str,
    test_report,
    *,
    existing_test_code: str = "",
) -> list[str]:
    """Static policy, suspend call context, and project interface checks."""
    issues: list[str] = []
    issues.extend(
        static_policy_validation_issues(
            test_code,
            output_file_path,
            source_code=source_code,
            existing_test_code=existing_test_code,
        )
    )
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
                    issues.append(validation_repair_intent("invalid_lateinit_injection_setup_order"))
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
                        validation_repair_intent("invalid_hardcoded_dispatcher_final_state_assertion")
                    )
                    break

    return issues

def _iter_assertthrows_block_bodies(test_code: str):
    """Yield inner bodies of assertThrows { ... } lambdas."""
    index = 0
    opener = re.compile(r"assertThrows\s*\([^)]*\)\s*\{")
    while index < len(test_code):
        match = opener.search(test_code, index)
        if not match:
            break
        brace = match.end() - 1
        depth = 0
        for pos in range(brace, len(test_code)):
            char = test_code[pos]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    yield test_code[brace + 1 : pos]
                    index = pos + 1
                    break
        else:
            break

def collect_suspend_assert_validation_issues(
    test_code: str, output_file_path: str, source_code: str, test_report
) -> list[str]:
    """Reject assertThrows on suspend functions outside runTest/runBlocking."""
    del output_file_path, test_report
    issues: list[str] = []
    suspend_function_names = suspend_function_names_from_ast(source_code)
    for function_name in suspend_function_names:
        for body in _iter_assertthrows_block_bodies(test_code or ""):
            if "runBlocking" in body or "runTest" in body:
                continue
            if re.search(rf"\.{re.escape(function_name)}\s*\(", body):
                issues.append(validation_repair_intent("invalid_suspend_assert"))
                break
    return issues

def _module_has_shared_test_hilt_graph(output_file_path: str) -> bool:
    module_dir = owning_module_dir_for_output(output_file_path)
    if not module_dir:
        return False
    test_java_dir = Path(module_dir) / "src" / "test" / "java"
    if not test_java_dir.exists():
        return False
    for test_file in test_java_dir.rglob("*.kt"):
        content = file_cache.read_text(test_file, default="")
        if not content:
            continue
        if "@Module" in content and "@InstallIn" in content and (
            "TestModule" in content or "Test" in test_file.name
        ):
            return True
    return False

def _fragment_context(source_code: str, output_file_path: str, test_code: str) -> dict:
    source = source_code or ""
    lower_source = source.lower()
    is_fragment = source_declares_android_fragment(source)
    return {
        "is_hilt_fragment": "@AndroidEntryPoint" in source and is_fragment,
        "source_is_hilt_fragment": "@AndroidEntryPoint" in source and is_fragment,
        "source_is_fragment": is_fragment,
        "source_has_find_nav": "findNavController" in source,
        "source_has_delegated_viewmodel": any(
            token in lower_source for token in ("activityviewmodels", "by viewmodels", "viewmodels(")
        ),
        "source_has_setmenuitems": "setmenuitems" in lower_source,
        "source_declares_viewmodel": source_declares_viewmodel_class(source),
        "allows_bindvalue_viewmodel_fallback": "bindvalue_viewmodel_fallback" in (test_code or ""),
    }

def _fragment_fixture_initialized(test_code: str) -> bool:
    if not re.search(r"\blateinit\s+var\s+fragment\b", test_code):
        return True
    if re.search(r"@Test[\s\S]*?\bval\s+fragment\s*=", test_code):
        return True
    if re.search(r"@Before\b[\s\S]*?\bfragment\s*=", test_code):
        return True
    return len(re.findall(r"\bfragment\b", test_code)) == 1

def _sets_fragment_arguments_after_attach(test_code: str) -> bool:
    tokens = re.finditer(
        r"\bval\s+fragment\s*=|(?<!\.)\bfragment\s*=(?!=)|\bfragment\s*\.\s*arguments\s*=|"
        r"\.commitNow(?:AllowingStateLoss)?\s*\(|\bactivityController\s*\.\s*(?:start|resume)\s*\(",
        test_code,
    )
    attached = False
    for token in tokens:
        text = token.group(0)
        if "fragment.arguments" in re.sub(r"\s+", "", text):
            if attached:
                return True
        elif re.match(r"\bval\s+fragment\s*=|(?<!\.)\bfragment\s*=", text):
            attached = False
        else:
            attached = True
    return False

def collect_fragment_validation_issues(test_code: str, output_file_path: str, source_code: str, test_report) -> list[str]:
    issues: list[str] = []
    ctx = _fragment_context(source_code, output_file_path, test_code)
    source = source_code or ""
    attaches = bool(
        "supportFragmentManager" in test_code
        or ".commitNow()" in test_code
        or ".commitNowAllowingStateLoss()" in test_code
        or "fragment.requireView()" in test_code
        or "Navigation.setViewNavController" in test_code
        or test_report.tests.direct_lifecycle_call_names
    )
    if ctx["source_is_hilt_fragment"] and attaches:
        if test_report.tests.has_local_hilt_test_activity:
            issues.append(validation_repair_intent("invalid_host_strategy"))
        missing = [
            part
            for part, ok in (
                ("@HiltAndroidTest", "@HiltAndroidTest" in test_code),
                ("HiltAndroidRule", "HiltAndroidRule" in test_code),
                ("@Config(application = HiltTestApplication::class)", "HiltTestApplication" in test_code),
            )
            if not ok
        ]
        if missing:
            issues.append(
                "invalid_hilt_lifecycle_setup: attached @AndroidEntryPoint Fragment tests require "
                + ", ".join(missing)
                + ". "
                + catalog_repair_intent("invalid_hilt_lifecycle_setup")
            )
        if "getDeclaredField(" in test_code or "isAccessible = true" in test_code:
            issues.append(validation_repair_intent("invalid_hilt_manual_injection"))
    issues.extend(_yaml("fragment_patterns.yaml", test_code, source, ctx))
    if (
        "@AndroidEntryPoint" in source
        and "findNavController" in source
        and "Robolectric.buildActivity(HiltTestActivity::class.java)" in test_code
        and "Navigation.setViewNavController" in test_code
    ):
        first_resume = test_code.find(".resume()")
        nav_install = test_code.find("Navigation.setViewNavController")
        if first_resume != -1 and nav_install != -1 and first_resume < nav_install:
            issues.append(
                "Robolectric Hilt Fragment navigation tests should install TestNavHostController before resuming the host. "
                "Use order: buildActivity(...).theme(Theme_AppCompat).create(), attach Fragment with commitNow(), "
                "activityController.start(), Navigation.setViewNavController(fragment.requireView(), navController), then activityController.resume()."
            )
        first_start = test_code.find(".start()")
        if (
            "fragment.requireView()" in test_code
            and first_start == -1
            and re.search(r"\.commitNow\(\)[\s\S]{0,500}Navigation\.setViewNavController", test_code)
        ):
            issues.append(
                "Robolectric Hilt Fragment navigation tests should call activityController.start() after commitNow() "
                "and before fragment.requireView()/Navigation.setViewNavController so the Fragment view is created "
                "without triggering onResume navigation too early."
            )
        if (
            "onViewCreated" in source
            and re.search(r"\.observe\s*\(\s*viewLifecycleOwner", source)
            and re.search(r"MutableLiveData\s*\(\s*AccountState\.(?:User|Owner|Orphan|Error)", test_code)
            and re.search(r"\.commitNow\(\)[\s\S]{0,700}Navigation\.setViewNavController", test_code)
        ):
            issues.append(
                "The test pre-seeds LiveData with a navigation AccountState before attaching the Fragment. "
                "commitNow() runs onViewCreated synchronously, so the observer can call findNavController() "
                "before Navigation.setViewNavController is installed. Use an initially empty observable or "
                "drive the same real ViewModel's public state setter only after commitNow(), activityController.start(), "
                "and Navigation.setViewNavController(fragment.requireView(), navController)."
            )
    return issues

def collect_fragment_viewmodel_quality_issues(
    test_code: str, output_file_path: str, source_code: str, test_report
) -> list[str]:
    del test_report
    if not (source_code or "").strip() or not (test_code or "").strip():
        return []
    ctx = _fragment_context(source_code, output_file_path, test_code)
    issues: list[str] = []
    source = source_code or ""
    test = test_code or ""
    if ctx["source_is_fragment"]:
        detached = re.search(
            r"\bval\s+\w+\s*=\s*[A-Za-z_][A-Za-z0-9_]*Fragment\s*\(\s*\)"
            r"[\s\S]{0,500}\.\s*on(?:Resume|Start|Pause|Stop)\s*\(",
            test,
        )
        attached = any(
            token in test
            for token in ("commitNow", "supportFragmentManager", "activityController", "Robolectric.buildActivity")
        )
        if detached and not attached and "isadded" in source.lower():
            issues.append(validation_repair_intent("invalid_fragment_detached_lifecycle_probe"))
    return issues

def collect_lifecycle_validation_issues(
    test_code: str, output_file_path: str, source_code: str, test_report
) -> list[str]:
    issues: list[str] = []
    ctx = _fragment_context(source_code, output_file_path, test_code)
    source = source_code or ""
    fragment_attach = re.search(
        r"supportFragmentManager\s*\.\s*beginTransaction\(\).*?\.add\(\s*[A-Za-z_][A-Za-z0-9_]*\s*,",
        test_code,
        flags=re.DOTALL,
    )
    direct_lifecycle = re.search(r"\.\s*on(?:CreateView|ViewCreated)\s*\(", test_code)
    if ctx["is_hilt_fragment"] and (fragment_attach or direct_lifecycle):
        full_hilt_supported, missing_hilt_support = module_has_hilt_robolectric_fragment_support(
            output_file_path, source
        )
        has_hilt_setup = (
            "@HiltAndroidTest" in test_code and "HiltAndroidRule" in test_code and "HiltTestApplication" in test_code
        )
        if direct_lifecycle and not full_hilt_supported:
            pass  # covered by yaml + below
        if not full_hilt_supported:
            issues.append(
                "@AndroidEntryPoint fragment lifecycle testing is not supported by the owning module Gradle setup. "
                f"Missing prerequisites: {', '.join(missing_hilt_support) if missing_hilt_support else 'unknown'}. "
                "Use direct public-contract tests with observable assertions or add the missing dependencies first."
            )
        elif not has_hilt_setup:
            issues.append(
                "@AndroidEntryPoint fragments cannot be attached to a plain Robolectric FragmentActivity/Application. "
                "Use @HiltAndroidTest + HiltAndroidRule + @Config(application = HiltTestApplication::class). "
                "When full Hilt setup is unavailable, test only public logic that does not trigger Hilt onAttach injection."
            )
        if any(token in source for token in ("HiltViewModel", "activityViewModels", "viewModels(")):
            if "@BindValue" not in test_code and "@TestInstallIn" not in test_code and not _module_has_shared_test_hilt_graph(output_file_path):
                issues.append(validation_repair_intent("hilt_graph_unverified"))
    if ctx["is_hilt_fragment"]:
        if "getDeclaredField(" in test_code or "isAccessible = true" in test_code:
            issues.append(validation_repair_intent("invalid_hilt_manual_injection"))
        if _sets_fragment_arguments_after_attach(test_code):
            issues.append(validation_repair_intent("invalid_fragment_arguments_after_attach"))
        if re.search(r"\blateinit\s+var\s+fragment\b", test_code) and not _fragment_fixture_initialized(test_code):
            issues.append(
                "The generated test declares a lateinit fragment fixture without deterministic initialization. "
                "Initialize it in @Before before every test, use local val fragment inside @Test methods, "
                "or remove tests that rely on it."
            )
        if (
            "activityViewModels" in source
            and re.search(r"\bviewModel\.[A-Za-z0-9_]+\.postValue\s*\(", test_code)
            and not re.search(r"supportFragmentManager|FragmentScenario|launchFragmentInHiltContainer", test_code)
        ):
            issues.append(
                "Posting to a mocked/standalone activityViewModels ViewModel without an attached fragment does not exercise fragment observers. "
                "Use verified Hilt lifecycle attachment, or replace the observer scenario with a valid public-contract test."
            )
    return issues

_INJECT_LATEINIT = re.compile(
    r"@Inject\b(?:[^\n]*\n){0,3}\s*lateinit\s+var\s+([A-Za-z_][A-Za-z0-9_]*)\b"
)
_TYPE_CONST = re.compile(r"^[A-Z][A-Za-z0-9_]*\.[A-Z][A-Z0-9_]*$")
_APPAUTH_MARKERS = (
    "net.openid.appauth.AuthState",
    "AuthState",
    "createTokenRefreshRequest",
    "TokenResponse.Builder",
    "AuthorizationServiceConfiguration",
)
_APOLLO_API_PACKAGE = r"(?:[A-Za-z_][A-Za-z0-9_]*\.)+journeylog\.api"
_BIND_VALUE_TYPE = re.compile(
    r"@BindValue\b(?:[^\n]*\n){0,4}[^\n]*\b(?:lateinit\s+)?(?:var|val)\s+[A-Za-z_][A-Za-z0-9_]*\s*:\s*([A-Za-z_][A-Za-z0-9_.]*)"
)

def collect_dotted_test_name_validation_issues(
    test_code: str, output_file_path: str, source_code: str, test_report
) -> list[str]:
    del output_file_path, source_code, test_report
    return _yaml("dotted_test_name.yaml", test_code, "")

def collect_activity_validation_issues(
    test_code: str, output_file_path: str, source_code: str, test_report
) -> list[str]:
    del output_file_path, test_report
    source = source_code or ""
    test = test_code or ""
    categories = classify_source(source).categories
    ctx = {f"cat_{cat}": True for cat in categories}
    issues = _yaml("activity_patterns.yaml", test, source, ctx)
    if "hilt_android_activity" in categories:
        for field in _INJECT_LATEINIT.findall(source):
            if re.search(rf"\.create\s*\([^)]*\)[\s\S]{{0,1200}}?\.{re.escape(field)}\s*=", test):
                issues.append(
                    "invalid_hilt_activity_assign_after_create: "
                    f"Do not assign .{field} after Robolectric.create() on an @AndroidEntryPoint Activity. "
                    "Inject collaborators via @BindValue/@TestInstallIn (or the production graph) before create()."
                )
                break
    issues.extend(_yaml("buildconfig_assertions.yaml", test, source))
    issues.extend(_yaml("hilt_service_worker.yaml", test, source))
    issues.extend(_yaml("permissions_patterns.yaml", test, source))
    return issues

def collect_assertions_validation_issues(
    test_code: str, output_file_path: str, source_code: str, test_report
) -> list[str]:
    issues: list[str] = []
    del output_file_path, test_report
    suspicious, vacuous = [], []
    for match in re.finditer(
        r"assertEquals\(\s*\"([^\"]+)\"\s*,\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\)", test_code
    ):
        literal = match.group(1)
        if len(literal) <= 1:
            continue
        prefix = test_code[: match.start()]
        if not re.search(rf"(?:=|\(|,|\bto)\s*\"{re.escape(literal)}\"", prefix) and (
            f'"{literal}"' not in source_code and f"'{literal}'" not in source_code
        ):
            suspicious.append(f'assertEquals("{literal}", {match.group(2)})')
    for match in re.finditer(
        r"assertEquals\(\s*([A-Za-z_][A-Za-z0-9_.]*)\s*,\s*\"([^\"]+)\"\s*\)", test_code
    ):
        symbol, literal = match.group(1), match.group(2)
        if len(literal) > 1 and _TYPE_CONST.fullmatch(symbol):
            vacuous.append(f'assertEquals({symbol}, "{literal}")')
    if suspicious:
        issues.append(
            "Suspicious expected string literal not backed by arranged input or source constants: "
            + "; ".join(suspicious[:4])
            + ". Derive expected values from test setup or verified source behavior."
        )
    if vacuous:
        issues.append(
            "Vacuous assertion compares a source constant/property to a hard-coded literal without exercising behavior: "
            + "; ".join(vacuous[:4])
            + ". Assert a captured output, visible view state, emitted state, or collaborator interaction instead."
        )
    issues.extend(_yaml("drawable_assertion.yaml", test_code, source_code))
    return issues

def collect_android_resource_validation_issues(
    test_code: str, output_file_path: str, source_code: str, test_report
) -> list[str]:
    issues: list[str] = []
    del source_code, test_report
    project_root, module_dir = validation_module_context(output_file_path)
    if not project_root or not test_code:
        return issues
    index = load_verified_android_resource_index(module_dir)
    if not index:
        return issues
    qualified_import = re.search(r"(?m)^\s*import\s+([A-Za-z0-9_.]+)\.R\s*$", test_code)
    module_namespace = parse_module_namespace(module_dir)
    if qualified_import and module_namespace and qualified_import.group(1) != module_namespace:
        return issues
    seen: set[str] = set()
    for match in _ANDROID_R_REF_PATTERN.finditer(test_code):
        if match.group("prefix"):
            continue
        resource_type, name = match.group("type"), match.group("name")
        token = f"R.{resource_type}.{name}"
        if token in seen or lookup_verified_android_resource(index, resource_type, name):
            seen.add(token)
            continue
        seen.add(token)
        single_match = find_single_same_type_resource_match(index, resource_type, name)
        if single_match:
            issues.append(
                "unverified_android_resource: "
                f"{token} is not declared. Use verified `R.{resource_type}.{single_match.name}` "
                f"from {single_match.declaration_path}."
            )
            continue
        same_type = sorted(candidate for (rtype, candidate) in index if rtype == resource_type and candidate != name)
        if len(same_type) > 1:
            options = ", ".join(f"R.{resource_type}.{candidate}" for candidate in same_type[:6])
            issues.append(
                "unverified_android_resource: "
                f"{token} is not declared and multiple same-type resources exist ({options}). "
                "Repair must choose the exact verified declaration; do not guess."
            )
        else:
            issues.append(
                "unverified_android_resource: "
                f"{token} is not declared in verified project resources. "
                "Remove or replace this test trigger instead of inventing a resource name."
            )
    return issues

def collect_member_extension_validation_issues(
    test_code: str, output_file_path: str, source_code: str, test_report
) -> list[str]:
    del output_file_path, test_report
    for name, (owner, receiver) in kotlin_member_extension_functions(source_code or "").items():
        for call in re.finditer(rf"\b[A-Za-z_][A-Za-z0-9_]*\s*\.\s*{re.escape(name)}\s*\(", test_code or ""):
            prefix = test_code[: call.start()]
            with_starts = list(re.finditer(rf"\bwith\s*\(\s*{re.escape(owner)}\s*\)\s*\{{", prefix))
            if with_starts and prefix[with_starts[-1].start() :].count("{") > prefix[with_starts[-1].start() :].count("}"):
                continue
            return [
                f"invalid_member_extension_receiver: `{name}` is a `{receiver}` member extension inside `{owner}`. "
                f"Call it as `with({owner}) {{ receiver.{name}(...) }}`."
            ]
    return []

def collect_carui_validation_issues(
    test_code: str, output_file_path: str, source_code: str, test_report
) -> list[str]:
    del output_file_path, test_report
    return _yaml(
        "carui_patterns.yaml",
        test_code,
        source_code,
        {"source_uses_carui_toolbar_progress": source_uses_carui_toolbar_progress(source_code or "")},
    )

def collect_junit_declaration_validation_issues(
    test_code: str, output_file_path: str, source_code: str, test_report
) -> list[str]:
    del output_file_path
    issues = _yaml("junit_patterns.yaml", test_code, source_code)
    if any(function.visibility == "private" for function in test_report.tests.test_functions):
        issues.append("Generated tests should keep @Test methods public/internal to JUnit.")
    if test_report.tests.malformed_test_declaration_lines or any(
        function.has_extra_parameter_list or function.parameter_list_count > 1
        for function in test_report.tests.test_functions
    ):
        issues.append(
            "JUnit4 test declarations should have exactly one parameter list. "
            "Use:\n@Test\nfun `short summary`() { ... }\n"
            "not fun `short summary`()()."
        )
    if re.search(r"registerFor(?:Activity|Authorization)Result", source_code or "") and re.search(
        r"registerFor(?:Activity|Authorization)Result", test_code
    ):
        bypasses = (
            re.search(r"registerForAuthorizationResult\s*\([\s\S]{0,120}?capture\s*\(", test_code)
            or (
                "argumentCaptor" in test_code
                and re.search(r"\.firstValue\s*\?\s*\.\s*invoke\s*\(|\.firstValue\s*\.\s*invoke\s*\(", test_code)
            )
        )
        if bypasses and not re.search(r"\bActivityResult\s*\(", test_code):
            issues.append(validation_repair_intent("invalid_activity_result_bypass"))
    return issues

def collect_robolectric_sdk_validation_issues(
    test_code: str, output_file_path: str, source_code: str, test_report
) -> list[str]:
    issues: list[str] = []
    project_root, module_dir = validation_module_context(output_file_path)
    module_min_sdk = parse_module_min_sdk(module_dir)
    module_compile_sdk = parse_module_compile_sdk(module_dir)
    selected_sdk = choose_robolectric_sdk_for_module(module_min_sdk, module_compile_sdk) if module_min_sdk else None
    ctx: dict = {}
    robo_version = parse_module_robolectric_version(module_dir) if module_dir else None
    if not robo_version and project_root:
        robo_version = project_robolectric_version(project_root)
    if robo_version:
        try:
            ctx["robolectric_major_at_least_4"] = int(robo_version.split(".", 1)[0]) >= 4
        except ValueError:
            ctx["robolectric_major_at_least_4"] = False
    issues.extend(_yaml("robolectric_deprecated.yaml", test_code, source_code, ctx))
    if module_min_sdk is not None:
        for config_sdk in test_report.tests.config_sdks:
            if config_sdk < module_min_sdk:
                issues.append(
                    f"Robolectric @Config(sdk = [{config_sdk}]) is below the owning module minSdk {module_min_sdk}. "
                    f"Use @Config(sdk = [{selected_sdk or module_min_sdk}]) when explicit SDK config is needed, or omit @Config."
                )
            elif module_compile_sdk is not None and config_sdk > module_compile_sdk:
                issues.append(
                    f"Robolectric @Config(sdk = [{config_sdk}]) is above the owning module compileSdk {module_compile_sdk}. "
                    f"Use @Config(sdk = [{selected_sdk or module_compile_sdk}]) when explicit SDK config is needed, or omit @Config."
                )
            elif selected_sdk is not None and config_sdk > selected_sdk and not is_android_platform_installed(config_sdk):
                issues.append(
                    f"Robolectric @Config(sdk = [{config_sdk}]) targets an Android SDK platform that is not installed locally. "
                    f"Use installed SDK {selected_sdk}, or install android-{config_sdk}."
                )
        if selected_sdk is None:
            issues.append(missing_platform_in_range_reason(module_min_sdk, module_compile_sdk))
    stack_versions = collect_module_test_dependency_versions(module_dir, project_root or "") if module_dir else {}
    issues.extend(
        _yaml(
            "mockito_kotlin_carui.yaml",
            test_code,
            source_code,
            {"stack_has_mockito_kotlin": bool(stack_versions.get("mockito_kotlin"))},
        )
    )
    if ctx.get("robolectric_major_at_least_4") and issues and issues[-1].startswith("deprecated_robolectric_api"):
        issues[-1] += (
            " For ApplicationProvider contexts use org.robolectric.shadows.ShadowApplication.getInstance().getNextStartedActivity()."
        )
    return issues

def collect_appauth_validation_issues(
    test_code: str, output_file_path: str, source_code: str, test_report
) -> list[str]:
    del output_file_path, test_report
    source = source_code or ""
    if not any(marker in source for marker in _APPAUTH_MARKERS):
        return []
    issues = _yaml("appauth_patterns.yaml", test_code, source)
    if (
        re.search(r"\.update\s*\(\s*any<\s*AuthorizationResponse\s*>", test_code)
        and (
            "refreshToken" in source
            or "TokenResponse.Builder" in source
            or re.search(r"createTokenRefreshRequest\s*\(", source)
        )
    ):
            issues.append(
                "AppAuth refreshToken calls AuthState.update(TokenResponse, AuthorizationException?). "
                "Use verify(mockAuthState).update(any<TokenResponse>(), isNull()) (or isNull<AuthorizationException>()), "
                "not AuthorizationResponse."
            )
    if "AuthorizationServiceDiscovery(" in test_code:
        missing = [
            field
            for field in (
                "issuer", "authorization_endpoint", "token_endpoint", "jwks_uri",
                "response_types_supported", "subject_types_supported", "id_token_signing_alg_values_supported",
            )
            if field not in test_code
        ]
        if missing:
            issues.append(
                "AppAuth AuthorizationServiceDiscovery test JSON must include mandatory OIDC fields: "
                + ", ".join(missing)
                + "."
            )
    appauth_storage = (
        "storage.getAuthStateJson()" in test_code
        or "KEY_AUTH_STATE_JSON" in test_code
        or '"auth_storage"' in test_code
    )
    if appauth_storage and (
        re.search(
            r"(?:every|coEvery)\s*\{[^}]*getAuthStateJson\s*\(\s*\)[^}]*\}\s*returns(?!\s*null\b)[^\n;]*"
            r"(?:access_token|id_token|refresh_token)",
            test_code,
            re.DOTALL,
        )
        or re.search(
            r'setAuthStateJson\s*\(\s*"(?:[^"\\]|\\.)*(?:access_token|id_token|refresh_token)',
            test_code,
        )
    ):
        issues.append(
            "Raw access_token/id_token/refresh_token JSON is not a verified AuthState serialized shape. "
            "Use AuthState().jsonSerializeString() only for empty unauthorized state, or inject a mocked AuthState with public getter stubs for authorized token/user-claim tests."
        )
    return issues

def _bind_value_type_names(test_code: str, test_report) -> set[str]:
    names: set[str] = set()
    for property_info in test_report.properties:
        if "BindValue" not in property_info.annotations:
            continue
        type_name = (property_info.type_text or "").split(".")[-1].split("<", 1)[0].strip()
        if type_name:
            names.add(type_name)
    for match in _BIND_VALUE_TYPE.finditer(test_code or ""):
        names.add(match.group(1).split(".")[-1])
    return names

def collect_hilt_validation_issues(
    test_code: str, output_file_path: str, source_code: str, test_report
) -> list[str]:
    del source_code
    issues: list[str] = []
    if test_report.tests.has_hilt_module_install_in:
        issues.append(validation_repair_intent("invalid_global_hilt_graph_mutation"))
    for type_name in sorted(_bind_value_type_names(test_code, test_report)):
        if module_has_shared_test_hilt_binding(output_file_path, type_name):
            issues.append(validation_repair_intent("duplicate_shared_hilt_test_binding"))
            break
    return issues

def collect_apollo_validation_issues(
    test_code: str, output_file_path: str, source_code: str, test_report
) -> list[str]:
    del output_file_path, test_report
    issues = run_pattern_rules(test_code, load_rules("apollo_patterns.yaml"), source_code=source_code)
    if re.search(
        rf"(?m)^\s*import\s+{_APOLLO_API_PACKAGE}\.(?:type\.)?\*\s*$", test_code
    ) and has_apollo_response_extension_function(source_code):
        issues.append(
            "Wildcard Apollo generated imports make response tests depend on compile-time generated types. "
            "Use project model imports directly and reference operation response receivers through Class.forName strings."
        )
    if re.search(
        rf"(?m)^\s*import\s+{_APOLLO_API_PACKAGE}\.type\.[A-Za-z_][A-Za-z0-9_]*\s*$", test_code
    ) and re.search(rf"{_APOLLO_API_PACKAGE}\.type", source_code):
        issues.append(
            "Apollo generated api.type imports make tests depend on compile-time generated classes. "
            "Use Class.forName strings and Java reflection for generated input return types."
        )
    return issues

def collect_reflection_validation_issues(
    test_code: str, output_file_path: str, source_code: str, test_report
) -> list[str]:
    del output_file_path, test_report
    issues = run_pattern_rules(
        test_code, load_rules("apollo_reflection_patterns.yaml"), source_code=source_code
    )
    if not re.search(rf"{_APOLLO_API_PACKAGE}\.type", source_code):
        issues = [
            item
            for item in issues
            if "input mapper is called directly" not in item
            and "cast to a compile-time api.type" not in item
        ]
    if has_apollo_response_extension_function(source_code):
        fake = [
            n for n in kotlin_declared_class_names(test_code) if n.endswith(("Mutation", "Query"))
        ]
        if fake:
            issues.append(
                "Source extension functions need verified Apollo generated receiver classes. "
                "Use verified generated classes through reflection/Mockito, or target a reflected nullable/simple branch."
            )
    return issues

def collect_instrumented_harness_validation_issues(
    test_code: str, output_file_path: str, source_code: str, test_report
) -> list[str]:
    del test_report
    if "/androidTest/" not in (output_file_path or "").replace("\\", "/"):
        return []
    return _yaml("instrumented_hilt.yaml", test_code or "", source_code or "")

def _source_singleton_delegation_helpers(source_code: str) -> set[str]:
    """Types reached via getInstance() inside the source under test."""
    return source_get_instance_types(source_code)

def _source_has_dialog_callback_chain(source_code: str) -> bool:
    markers = (
        "onPositiveAction",
        "onNegativeAction",
        "onUserAction",
        "showAlert(",
        "FullScreenDialogFragment.newInstance",
        "setButton(",
        "AlertDialog",
        "DialogFragment",
    )
    return any(marker in (source_code or "") for marker in markers) or bool(
        _source_singleton_delegation_helpers(source_code)
    )

def _block_exercises_target_class(block: str, target_class: str) -> bool:
    if target_class and target_class in block:
        return True
    if re.search(r"\b(?:fragment|sut|subject|target)\s*[=:.]", block):
        return True
    if re.search(r"\b(?:fragment|sut|subject|target)\s*\.", block):
        return True
    return False

def _block_calls_helper_get_instance(block: str, helper: str) -> bool:
    return bool(re.search(rf"\b{re.escape(helper)}\.getInstance\s*\(\s*\)", block))

def _lifecycle_state_after_stop_is_wrong(block: str) -> bool:
    if not re.search(r"(?:activityController|controller|host|scenario)\s*\.\s*stop\s*\(|\bonStop\s*\(", block):
        return False
    stop_at = None
    for match in re.finditer(
        r"(?:activityController|controller|host|scenario)\s*\.\s*stop\s*\(|\bonStop\s*\(",
        block,
    ):
        stop_at = match.start()
    if stop_at is None:
        return False
    tail = block[stop_at:]
    return bool(
        re.search(
            r"assertEquals\s*\(\s*Lifecycle\.State\.(?:STARTED|RESUMED)\s*,\s*\w+\.lifecycle\.currentState",
            tail,
        )
        or re.search(
            r"assertSame\s*\(\s*Lifecycle\.State\.(?:STARTED|RESUMED)\s*,\s*\w+\.lifecycle\.currentState",
            tail,
        )
    )

def collect_coverage_orchestration_validation_issues(
    test_code: str, output_file_path: str, source_code: str, test_report
) -> list[str]:
    """Reject tests that bypass the target class entry path or assert impossible lifecycle states."""
    if "/androidTest/" in (output_file_path or "").replace("\\", "/"):
        return []
    issues: list[str] = []
    source_code = source_code or ""
    if not source_code.strip():
        return issues

    target_class = kotlin_top_level_class_name(source_code, "")
    helpers = _source_singleton_delegation_helpers(source_code)
    dialog_chain = _source_has_dialog_callback_chain(source_code)

    for block in extract_junit4_test_blocks(test_code):
        if dialog_chain and helpers:
            for helper in sorted(helpers):
                if helper == target_class:
                    continue
                if _block_calls_helper_get_instance(block, helper) and not _block_exercises_target_class(
                    block, target_class
                ):
                    issues.append(validation_repair_intent("missing_coverage_trigger_dialog"))
                    break

        if source_declares_android_fragment(source_code) and _lifecycle_state_after_stop_is_wrong(block):
            issues.append(validation_repair_intent("invalid_fragment_lifecycle_state_assertion"))

    issues.extend(
        run_pattern_rules(
            test_code,
            load_rules("coverage_triggers.yaml"),
            source_code=source_code,
        )
    )
    return issues


_GENERATED_IMPORT = re.compile(r"(?m)^\s*import\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)\s*$")


@lru_cache(maxsize=64)
def _module_kt_java_indexes(module_dir: str) -> tuple[frozenset[str], frozenset[str]] | None:
    gen = Path(module_dir) / "build" / "generated"
    if not gen.is_dir():
        return None

    def collect(root: Path) -> frozenset[str]:
        found: set[str] = set()
        if not root.is_dir():
            return frozenset()
        for path in root.rglob("*"):
            if path.suffix in {".kt", ".java"}:
                found.add(path.as_posix())
        return frozenset(found)

    return collect(gen), collect(Path(module_dir) / "src")


def _fqcn_file_in(files: frozenset[str], fqcn: str) -> bool:
    rel = fqcn.replace(".", "/")
    return any(posix.endswith(rel + ".kt") or posix.endswith(rel + ".java") for posix in files)


def collect_generated_import_validation_issues(
    test_code: str,
    output_file_path: str,
    source_code: str = "",
    test_report=None,
) -> list[str]:
    """Flag imports whose only matching .kt/.java lives under build/generated."""
    _ = source_code, test_report
    module = owning_module_dir_for_output(output_file_path)
    if not module:
        return []
    indexes = _module_kt_java_indexes(module)
    if indexes is None:
        return []
    generated_files, src_files = indexes
    issues: list[str] = []
    seen: set[str] = set()
    for match in _GENERATED_IMPORT.finditer(test_code or ""):
        fqcn = match.group(1)
        if fqcn in seen:
            continue
        seen.add(fqcn)
        if not _fqcn_file_in(generated_files, fqcn):
            continue
        if _fqcn_file_in(src_files, fqcn):
            continue
        issues.append(
            f"generated_only_import: do not import {fqcn}; type lives only under build/generated"
        )
    return issues


VALIDATION_RULES = [
    collect_activity_validation_issues,
    collect_android_resource_validation_issues,
    collect_hilt_validation_issues,
    collect_fragment_validation_issues,
    collect_coroutines_validation_issues,
    collect_mocking_lane_validation_issues,
    collect_nested_stub_validation_issues,
    collect_viewmodel_validation_issues,
    collect_primitive_matcher_validation_issues,
    collect_carui_validation_issues,
    collect_static_and_navigation_validation_issues,
    collect_junit_declaration_validation_issues,
    collect_member_extension_validation_issues,
    collect_robolectric_sdk_validation_issues,
    collect_lifecycle_validation_issues,
    collect_dotted_test_name_validation_issues,
    collect_mockito_import_and_api_validation_issues,
    collect_suspend_assert_validation_issues,
    collect_appauth_validation_issues,
    collect_assertions_validation_issues,
    collect_apollo_validation_issues,
    collect_apollo_mock_stub_validation_issues,
    collect_reflection_validation_issues,
    collect_coverage_orchestration_validation_issues,
    collect_fragment_viewmodel_quality_issues,
    collect_instrumented_harness_validation_issues,
    collect_generated_import_validation_issues,
]

__all__ = ["VALIDATION_RULES"]
