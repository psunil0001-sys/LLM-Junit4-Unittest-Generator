# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Generated Kotlin test validation and normalization helpers.
"""Generated Kotlin test validation and normalization helpers."""

from __future__ import annotations

import os
import re
from collections import Counter

from tree_sitter import Language, Parser
import tree_sitter_kotlin as tskotlin

from UnitTest_gen.core.logging_utils import log_message
from UnitTest_gen.core.memory_store import add_generation_lesson
from UnitTest_gen.core.pipeline_config import get_config
from UnitTest_gen.kotlin.kotlin_analysis import (
    kotlin_package_name,
    source_declares_android_fragment,
    source_declares_viewmodel_class,
    source_rule_categories,
)
from UnitTest_gen.kotlin.project_context import (
    apply_verified_android_resource_corrections,
    module_has_hilt_robolectric_fragment_support,
)
from UnitTest_gen.kotlin.static_analysis import analyze_kotlin_code, analyze_kotlin_test_code
from UnitTest_gen.kotlin.test_code_utils.extract import extract_nullable_boolean_functions
from UnitTest_gen.kotlin.validation_rules.mockk import VOID_NAVIGATION_STUB_PATTERN, mocked_static_return_types
AST_PARSER = Parser(Language(tskotlin.language()))


def _append_validation_rule_issues(issues, test_code, output_file_path, source_code, test_report) -> None:
    try:
        from UnitTest_gen.kotlin.validation_rules import VALIDATION_RULES
    except ImportError:
        return
    for collect_rule_issues in VALIDATION_RULES:
        try:
            collected = collect_rule_issues(test_code, output_file_path, source_code, test_report)
        except TypeError:
            collected = collect_rule_issues(test_code, output_file_path, source_code)
        issues.extend(collected or [])


def normalize_kotlin_test_code(test_code: str, source_code: str = "", output_file_path: str = "") -> str:
    """
    Mechanical cleanups for common Kotlin/JUnit generation slips. These are
    intentionally narrow and compile-safety focused.
    """
    normalized = test_code

    top_lines = [line.strip() for line in normalized.splitlines()[:5]]
    if not any(line.startswith("package ") for line in top_lines):
        package_name = kotlin_package_name(source_code or "")
        if not package_name and output_file_path:
            normalized_path = os.path.normpath(output_file_path).replace("\\", "/")
            for marker in ("/src/test/java/", "/src/test/kotlin/"):
                if marker in normalized_path:
                    rel_dir = os.path.dirname(normalized_path.split(marker, 1)[1])
                    if rel_dir and rel_dir != ".":
                        package_name = rel_dir.replace("/", ".")
                    break
        if package_name:
            normalized = f"package {package_name}\n\n{normalized.lstrip()}"

    def has_import(import_line: str) -> bool:
        return re.search(rf"^{re.escape(import_line)}\s*$", normalized, re.MULTILINE) is not None

    def add_missing_imports(import_lines: list[str]) -> None:
        nonlocal normalized
        existing_imports = set(re.findall(r"^import\s+.+$", normalized, re.MULTILINE))
        missing_imports = [
            import_line
            for import_line in import_lines
            if import_line not in existing_imports
        ]
        if not missing_imports:
            return

        package_match = re.search(r"^\s*(package\s+[^\n]+\n)", normalized, re.MULTILINE)
        imports_text = "\n".join(missing_imports) + "\n"
        if package_match:
            insert_at = package_match.end()
            following_newline = "\n" if not normalized[insert_at:].startswith("\n") else ""
            normalized = normalized[:insert_at] + following_newline + imports_text + normalized[insert_at:]
        else:
            normalized = imports_text + normalized

    def inferred_module_root_package() -> str:
        package_match = re.search(r"^\s*package\s+([A-Za-z_][A-Za-z0-9_.]*)", normalized, re.MULTILINE)
        if not package_match:
            return ""
        package_name = package_match.group(1)
        for segment in (".presentation.", ".data.", ".viewmodel.", ".adapter."):
            if segment in package_name:
                return package_name.split(segment, 1)[0]
        return package_name.rsplit(".", 1)[0] if "." in package_name else package_name

    canonical_imports = []
    module_root_package = inferred_module_root_package()
    if "ActivityController<" in normalized:
        canonical_imports.append("import org.robolectric.android.controller.ActivityController")
    if "Robolectric." in normalized:
        canonical_imports.append("import org.robolectric.Robolectric")
    if re.search(r"\bActivity\b", normalized) and "android.app.Activity" not in normalized:
        canonical_imports.append("import android.app.Activity")
    if "commitNow(" in normalized:
        canonical_imports.append("import androidx.fragment.app.commitNow")
    if "Navigation.setViewNavController" in normalized:
        canonical_imports.append("import androidx.navigation.Navigation")
    if re.search(r"\bNavController\b", normalized):
        canonical_imports.append("import androidx.navigation.NavController")
    if "NavDeepLinkRequest" in normalized:
        canonical_imports.append("import androidx.navigation.NavDeepLinkRequest")
    if "CarUi." in normalized or "MockedStatic<CarUi>" in normalized:
        canonical_imports.append("import com.android.car.ui.core.CarUi")
    if "ToolbarController" in normalized:
        canonical_imports.append("import com.android.car.ui.toolbar.ToolbarController")
    if "ProgressBarController" in normalized:
        canonical_imports.append("import com.android.car.ui.toolbar.ProgressBarController")
    if "MockedStatic<" in normalized:
        canonical_imports.append("import org.mockito.MockedStatic")
    if "HiltTestActivity" in normalized and module_root_package:
        canonical_imports.append(f"import {module_root_package}.HiltTestActivity")
    if canonical_imports:
        add_missing_imports(canonical_imports)

    if re.search(r"\bwhenever\s*\(", normalized):
        normalized = normalized.replace("import org.mockito.Mockito.*\n", "")
        normalized = normalized.replace("import org.mockito.Mockito.whenever\n", "")
        normalized = normalized.replace("import org.mockito.kotlin.mockStatic\n", "import org.mockito.Mockito.mockStatic\n")

        required_imports = [
            "import org.mockito.kotlin.whenever",
        ]

        if re.search(r"\bverify\s*\(", normalized) and "import org.mockito.kotlin.verify" not in normalized:
            required_imports.append("import org.mockito.kotlin.verify")

        if re.search(r"\bmock\s*\(", normalized) and "import org.mockito.kotlin.mock" not in normalized:
            required_imports.append("import org.mockito.kotlin.mock")

        add_missing_imports(required_imports)

    # Keep one Mockito surface after supplemental code is merged into an
    # existing Mockito-Kotlin test class.
    for symbol in ("any", "verify"):
        if has_import(f"import org.mockito.kotlin.{symbol}"):
            normalized = re.sub(
                rf"(?m)^\s*import\s+org\.mockito\.Mockito\.{symbol}\s*\n",
                "",
                normalized,
            )

    # Java Mockito verifies setters as methods; Kotlin property assignment on
    # verify(...) only mutates the mock and leaves matcher state invalid.
    carui_setters = {
        "isVisible": "setVisible",
        "progress": "setProgress",
        "isIndeterminate": "setIndeterminate",
    }
    for property_name, setter_name in carui_setters.items():
        normalized = re.sub(
            rf"((?:org\.mockito\.Mockito\.)?verify\s*\([^\n]+?\))\s*\.\s*{property_name}\s*=\s*([^\n]+)",
            rf"\1.{setter_name}(\2)",
            normalized,
        )

    primitive_matcher_replacements = {
        "anyBoolean": "any<Boolean>",
        "anyByte": "any<Byte>",
        "anyChar": "any<Char>",
        "anyDouble": "any<Double>",
        "anyFloat": "any<Float>",
        "anyInt": "any<Int>",
        "anyLong": "any<Long>",
        "anyShort": "any<Short>",
    }
    for helper_name, typed_matcher in primitive_matcher_replacements.items():
        normalized = re.sub(
            rf"(?m)^import\s+org\.mockito\.kotlin\.{helper_name}\s*\n",
            "",
            normalized,
        )
        normalized = re.sub(rf"\b{helper_name}\s*\(\s*\)", f"{typed_matcher}()", normalized)

    if re.search(r"\bany\s*<", normalized) and not has_import("import org.mockito.kotlin.any"):
        add_missing_imports(["import org.mockito.kotlin.any"])
    if re.search(r"\bany\s*\(\s*\)", normalized) and not has_import("import org.mockito.kotlin.any"):
        add_missing_imports(["import org.mockito.kotlin.any"])
    if re.search(r"\beq\s*\(", normalized) and not has_import("import org.mockito.kotlin.eq"):
        add_missing_imports(["import org.mockito.kotlin.eq"])
    if re.search(r"\brunTest\s*\{|\brunTest\s*\(", normalized) and not has_import("import kotlinx.coroutines.test.runTest"):
        add_missing_imports(["import kotlinx.coroutines.test.runTest"])

    flow_imports = []
    if re.search(r"\bflow\s*(?:<[^>]+>)?\s*\{", normalized) and not has_import("import kotlinx.coroutines.flow.flow"):
        flow_imports.append("import kotlinx.coroutines.flow.flow")
    if re.search(r"\.toList\s*\(", normalized) and not has_import("import kotlinx.coroutines.flow.toList"):
        flow_imports.append("import kotlinx.coroutines.flow.toList")
    if re.search(r"\bcatch\s*\{", normalized) and not has_import("import kotlinx.coroutines.flow.catch"):
        flow_imports.append("import kotlinx.coroutines.flow.catch")
    if re.search(r"\bmap\s*\{", normalized) and not has_import("import kotlinx.coroutines.flow.map"):
        flow_imports.append("import kotlinx.coroutines.flow.map")
    if re.search(r"\bonStart\s*\{", normalized) and not has_import("import kotlinx.coroutines.flow.onStart"):
        flow_imports.append("import kotlinx.coroutines.flow.onStart")

    if flow_imports:
        add_missing_imports(flow_imports)

    if re.search(r"\bassertThrows\s*\(", normalized) and not has_import("import org.junit.Assert.assertThrows"):
        add_missing_imports(["import org.junit.Assert.assertThrows"])

    normalized = VOID_NAVIGATION_STUB_PATTERN.sub("", normalized)
    if "ArgumentCaptor.forClass(NavDeepLinkRequest::class.java)" in normalized:
        normalized = re.sub(
            r"\b(?:val|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
            r"ArgumentCaptor\.forClass\s*\(\s*NavDeepLinkRequest::class\.java\s*\)",
            r"val \1 = argumentCaptor<NavDeepLinkRequest>()",
            normalized,
        )
        normalized = re.sub(
            r"(?m)^\s*import\s+org\.mockito\.ArgumentCaptor\s*\n",
            "",
            normalized,
        )
        if not has_import("import org.mockito.kotlin.argumentCaptor"):
            add_missing_imports(["import org.mockito.kotlin.argumentCaptor"])
    # ponytail: cross-graph NavDeepLinkRequest sources use mocked NavController, not bare TestNavHostController
    if (
        "NavDeepLinkRequest" in (source_code or "")
        and "TestNavHostController" in normalized
        and ".setGraph(" not in normalized
    ):
        normalized = re.sub(
            r"(?:androidx\.navigation\.testing\.)?TestNavHostController\s*\([^)]*\)",
            "mock()",
            normalized,
        )
        normalized = re.sub(
            r"(?m)^\s*import\s+androidx\.navigation\.testing\.TestNavHostController\s*\n",
            "",
            normalized,
        )
        if re.search(r"\bmock\s*\(", normalized) and not has_import("import org.mockito.kotlin.mock"):
            add_missing_imports(["import org.mockito.kotlin.mock"])
    if "ArgumentCaptor" in normalized and not has_import("import org.mockito.ArgumentCaptor"):
        add_missing_imports(["import org.mockito.ArgumentCaptor"])
    if re.search(r"(?<!\.)\bverify\s*\(", normalized) and not has_import("import org.mockito.kotlin.verify"):
        add_missing_imports(["import org.mockito.kotlin.verify"])

    static_return_types = mocked_static_return_types(normalized)
    if static_return_types:
        def add_static_when_return_type(match: re.Match) -> str:
            receiver = match.group("name")
            return_type = static_return_types.get(receiver)
            return match.group(0) if not return_type else match.group(0).replace("`when`", f"`when`<{return_type}>")

        normalized = re.sub(
            r"\b(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:!!|\?)?\s*\.\s*`when`\s*(?!<)\{",
            add_static_when_return_type,
            normalized,
        )

    if re.search(r"\bResponse\.error(?:<[^>\n]+>)?\s*\(\s*[^,\n]+,\s*null\s*\)", normalized):
        normalized = re.sub(
            r"\bResponse\.error(\s*(?:<[^>\n]+>)?\s*\(\s*[^,\n]+,\s*)null(\s*\))",
            lambda match: f'Response.error{match.group(1)}ResponseBody.create(null, "error"){match.group(2)}',
            normalized,
        )
        if not has_import("import okhttp3.ResponseBody"):
            add_missing_imports(["import okhttp3.ResponseBody"])

    normalized = re.sub(
        r"ShadowApplication\.getInstance\(\)\.nextStartedActivity\s*<\s*Intent\s*>\s*\(\)\s+as\s+Intent",
        "org.robolectric.Shadows.shadowOf(context as android.app.Application).nextStartedActivity",
        normalized,
    )
    normalized = re.sub(
        r"ShadowApplication\.getInstance\(\)\.nextStartedActivity\s*<\s*Intent\s*>\s*\(\)",
        "org.robolectric.Shadows.shadowOf(context as android.app.Application).nextStartedActivity",
        normalized,
    )

    normalized = re.sub(
        r"\.thenReturn\(\s*([A-Za-z_][A-Za-z0-9_]*)\.toMutableList\(\)\s*,\s*([A-Za-z_][A-Za-z0-9_]*)\.toMutableList\(\)\s*\)",
        r".thenReturn(\1.toMutableList()).thenReturn(\2.toMutableList())",
        normalized,
    )

    nullable_boolean_functions = extract_nullable_boolean_functions(source_code)
    for function_name in nullable_boolean_functions:
        call_pattern = rf"([A-Za-z_][A-Za-z0-9_]*\.{re.escape(function_name)}\s*\([^)]*\))"
        normalized = re.sub(
            rf"assertTrue\(\s*{call_pattern}\s*\)",
            r"assertEquals(true, \1)",
            normalized,
        )
        normalized = re.sub(
            rf"assertFalse\(\s*{call_pattern}\s*\)",
            r"assertEquals(false, \1)",
            normalized,
        )

    if output_file_path:
        normalized = apply_verified_android_resource_corrections(normalized, output_file_path)

    return normalized

def extract_junit4_test_blocks(test_code: str) -> list[str]:
    report = analyze_kotlin_code(test_code or "")
    lines = (test_code or "").splitlines()
    return [
        "\n".join(lines[function.span.start_line - 1:function.span.end_line])
        for function in report.functions
        if "Test" in function.annotations
    ]

def fragment_requires_meaningful_lifecycle_coverage(source_code: str) -> bool:
    if not source_declares_android_fragment(source_code or ""):
        return False

    markers = (
        "@AndroidEntryPoint",
        "findNavController",
        "viewModels()",
        "activityViewModels()",
        "requireContext()",
        "requireActivity()",
        "onViewCreated(",
        "onResume(",
        "AlertDialogHelper",
        "FullScreenDialogFragment",
        "CarUi.",
    )
    return any(marker in (source_code or "") for marker in markers)

def test_block_has_fragment_behavioral_signal(block: str) -> bool:
    signal_patterns = (
        r"FragmentScenario",
        r"launchFragmentInContainer",
        r"supportFragmentManager",
        r"\.onFragment\s*\{",
        r"@HiltAndroidTest",
        r"HiltAndroidRule",
        r"HiltTestApplication",
        r"TestNavHostController",
        r"Navigation\.setViewNavController",
        r"\.performClick\s*\(",
        r"\bverify\s*\(",
        r"\bassertEquals\s*\(",
        r"\bassertSame\s*\(",
        r"\bassertNotEquals\s*\(",
        r"\bassertNull\s*\(",
        r"\bcurrentDestination\b",
        r"\bvisibility\b",
        r"\btext\.toString\s*\(",
    )
    return any(re.search(pattern, block) for pattern in signal_patterns)

def test_block_is_trivial_fragment_pattern(block: str) -> bool:
    if not re.search(r"val\s+fragment\s*=\s*[A-Za-z_][A-Za-z0-9_<>]*\s*\(", block):
        return False

    if test_block_has_fragment_behavioral_signal(block):
        return False

    trivial_assertions = (
        r"assertNotNull\s*\(\s*fragment",
        r"assertNull\s*\(\s*fragment\.arguments",
        r"assertTrue\s*\(\s*\"[^\"]*\"\s*,\s*fragment\s*!=\s*null\s*\)",
        r"assertEquals\s*\([^,]+::class\.java\s*,\s*fragment::class\.java\s*\)",
    )
    return any(re.search(pattern, block) for pattern in trivial_assertions)

def viewmodel_requires_coroutine_test_support(source_code: str) -> bool:
    report = analyze_kotlin_code(source_code or "")
    return bool(
        report.coroutine_usages
        or any(function.is_suspend for function in report.functions)
        or any(path.startswith("kotlinx.coroutines") for path in report.imports)
    )

def collect_generation_quality_issues(test_code: str, output_file_path: str, source_code: str = "") -> list[str]:
    issues: list[str] = []
    source_code = source_code or ""
    test_blocks = extract_junit4_test_blocks(test_code)
    is_fragment_source = source_declares_android_fragment(source_code)
    is_android_entrypoint_fragment = "@AndroidEntryPoint" in source_code and is_fragment_source
    is_viewmodel_source = source_declares_viewmodel_class(source_code)

    if is_fragment_source and test_blocks:
        trivial_fragment_blocks = [block for block in test_blocks if test_block_is_trivial_fragment_pattern(block)]
        if trivial_fragment_blocks and len(trivial_fragment_blocks) == len(test_blocks):
            issues.append(
                "Fragment tests should verify observable behavior such as attached UI state, navigation, dialog callbacks, or collaborator interaction; constructor-only assertions are too weak."
            )

        if fragment_requires_meaningful_lifecycle_coverage(source_code):
            full_hilt_supported = False
            if is_android_entrypoint_fragment:
                full_hilt_supported, _ = module_has_hilt_robolectric_fragment_support(
                    output_file_path,
                    source_code,
                )

            has_attachment_signal = any(
                test_block_has_fragment_behavioral_signal(block)
                for block in test_blocks
            )
            if (not is_android_entrypoint_fragment or full_hilt_supported) and not has_attachment_signal:
                issues.append(
                    "This fragment source has verified lifecycle-facing behavior. Generated tests should include at least one attached or otherwise observable behavioral scenario instead of only detached construction checks."
                )

        if any("assertDoesNotThrow" in block for block in test_blocks):
            issues.append(
                "Fragment tests should prefer direct observable assertions over custom assertDoesNotThrow wrappers around inert code paths."
            )

    from UnitTest_gen.kotlin.validation_rules.coroutines import static_policy_validation_issues

    issues.extend(static_policy_validation_issues(test_code, output_file_path))

    if is_viewmodel_source:
        if "LiveData" in source_code and "InstantTaskExecutorRule" not in test_code:
            issues.append(
                "ViewModel tests that observe LiveData should include InstantTaskExecutorRule."
            )

        if viewmodel_requires_coroutine_test_support(source_code) and not re.search(
            r"\brunTest\s*\(|\brunBlocking\s*\(|Dispatchers\.setMain",
            test_code,
        ):
            issues.append(
                "ViewModel tests that exercise coroutine or StateFlow behavior should use runTest/runBlocking with deterministic dispatcher setup."
            )

        meaningful_viewmodel_signal = any(
            re.search(
                r"\bverify\s*\(|\bassertEquals\s*\(|\bassertTrue\s*\(|\bassertFalse\s*\(|\buiState\b|\bstubState\b|\.value\b",
                block,
            )
            for block in test_blocks
        )
        if test_blocks and not meaningful_viewmodel_signal:
            issues.append(
                "ViewModel tests should assert public state transitions, emitted values, or collaborator interaction instead of relying on construction-only coverage."
            )

    return issues

def remember_successful_generation_if_high_quality(
    class_name: str,
    test_code: str,
    output_file_path: str,
    source_code: str = "",
) -> None:
    quality_issues = collect_generation_quality_issues(
        test_code=test_code,
        output_file_path=output_file_path,
        source_code=source_code,
    )
    if quality_issues:
        log_message(
            "🧠 Skipping AI memory save because the generated test did not meet fragment/ViewModel quality gates.",
            category="warning",
        )
        for issue in quality_issues:
            log_message(f"   - {issue}", category="warning")
        return

    add_generation_lesson(
        class_name,
        test_code,
        source_categories=source_rule_categories(source_code),
        source_code=source_code,
    )













def validate_generated_test_code(
    test_code: str,
    output_file_path: str,
    source_code: str = "",
    *,
    opportunity_plan=None,
    existing_test_code: str = "",
):
    """
    Fast local guardrails before Gradle. This catches the common model mistakes
    that waste repair cycles or create files outside the requested Kotlin/JUnit4 lane.
    """
    issues = []
    if not get_config().enable_guardrails:
        return []

    test_report = analyze_kotlin_test_code(test_code or "", source_code or "")
    issues.extend(test_report.validation_errors)

    if not output_file_path.endswith(".kt"):
        issues.append("Generated test output must be a .kt file.")

    forbidden_imports = [
        "org.junit.jupiter",
        "androidx.test.ext.junit.runners.AndroidJUnit4",
    ]

    for forbidden in forbidden_imports:
        if forbidden in test_code:
            issues.append(f"Forbidden import or symbol for local JUnit4 JVM tests: {forbidden}")

    top_lines = [line.strip() for line in test_code.splitlines()[:5]]
    if not any(line.startswith("package ") for line in top_lines):
        issues.append("Generated test is missing a package declaration near the top of the file.")

    _append_validation_rule_issues(issues, test_code, output_file_path, source_code, test_report)

    if opportunity_plan is not None:
        from UnitTest_gen.kotlin.incremental_coverage import collect_incremental_orchestration_issues

        issues.extend(
            collect_incremental_orchestration_issues(
                test_code,
                opportunity_plan,
                existing_test_code=existing_test_code,
            )
        )

    return issues

def validation_issues_added(
    base_test_code: str,
    candidate_test_code: str,
    output_file_path: str,
    source_code: str = "",
) -> list[str]:
    """Return only validation issues introduced by the candidate."""
    base_counts = Counter(validate_generated_test_code(base_test_code, output_file_path, source_code=source_code))
    candidate_counts = Counter(validate_generated_test_code(candidate_test_code, output_file_path, source_code=source_code))
    added_issues = []
    for issue, count in candidate_counts.items():
        added_issues.extend([issue] * max(0, count - base_counts.get(issue, 0)))
    return added_issues
