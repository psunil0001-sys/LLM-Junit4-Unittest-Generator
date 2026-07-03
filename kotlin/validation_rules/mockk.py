# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Validates Mockito and MockK usage patterns.
"""MockK/Mockito pattern validation rules."""

from __future__ import annotations

import os
import re
from pathlib import Path

from UnitTest_gen.kotlin.strategy_contracts import validation_repair_intent

VOID_NAVIGATION_STUB_PATTERN = re.compile(
    r"(?ms)^[ \t]*(?:(?:org\.mockito\.)?Mockito)\.(?:`when`|when)\s*"
    r"\(\s*(?:\n\s*)?"
    r"(?P<receiver>[A-Za-z_][A-Za-z0-9_]*)\.navigate\s*"
    r"\(\s*(?:(?:org\.mockito\.)?Mockito)\.any\s*"
    r"\(\s*NavDeepLinkRequest::class\.java\s*\)\s*\)"
    r"\s*(?:\n\s*)?\)\s*\.\s*thenAnswer\s*\{\s*null\s*\}\s*\n?",
)


def has_invalid_void_navigation_stubbing(test_code: str) -> bool:
    return bool(VOID_NAVIGATION_STUB_PATTERN.search(test_code or ""))


def has_invalid_java_nav_deeplink_captor(test_code: str) -> bool:
    code = test_code or ""
    java_captor_names = {
        match.group("name")
        for match in re.finditer(
            r"\b(?:val|var)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
            r"ArgumentCaptor\.forClass\s*\(\s*NavDeepLinkRequest::class\.java\s*\)",
            code,
        )
    }
    if not java_captor_names:
        return False
    return any(
        re.search(
            rf"\.\s*navigate\s*\(\s*{re.escape(name)}\.capture\s*\(\s*\)\s*\)",
            code,
        )
        for name in java_captor_names
    )


def mocked_static_return_types(test_code: str) -> dict[str, str]:
    return {
        match.group("name"): match.group("type").split(".")[-1]
        for match in re.finditer(
            r"\b(?:private\s+)?(?:var|val)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*:\s*MockedStatic\s*<\s*(?P<type>[A-Za-z_][A-Za-z0-9_.$]*)\s*>",
            test_code or "",
        )
    }


def has_mocked_static_when_missing_return_type(test_code: str) -> bool:
    static_types = mocked_static_return_types(test_code)
    if not static_types:
        return False
    pattern = re.compile(r"\b(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:!!|\?)?\s*\.\s*`when`\s*(?!<)\{")
    return any(match.group("name") in static_types for match in pattern.finditer(test_code or ""))


def has_invalid_singleton_reflection(test_code: str) -> bool:
    return bool(
        re.search(
            r"AlertDialogHelper::class\.java\.getDeclaredField\s*\(\s*\"(?:instance|INSTANCE)\"\s*\)",
            test_code or "",
        )
        or re.search(
            r"getDeclaredField\s*\(\s*\"(?:instance|INSTANCE)\"\s*\)[\s\S]{0,240}AlertDialogHelper",
            test_code or "",
        )
    )


def has_invalid_alertdialoghelper_static_mock(test_code: str) -> bool:
    return bool(
        "mockStatic(AlertDialogHelper::class.java)" in (test_code or "")
        or "MockedStatic<AlertDialogHelper>" in (test_code or "")
    )


def _static_mock_validation_issues(test_code: str) -> list[str]:
    rules = (
        (has_invalid_singleton_reflection, "invalid_singleton_reflection"),
        (has_invalid_alertdialoghelper_static_mock, "invalid_alertdialoghelper_static_mock"),
        (has_mocked_static_when_missing_return_type, "invalid_mockedstatic_missing_return_type"),
    )
    return [
        f"{code}: {validation_repair_intent(code)}"
        for predicate, code in rules
        if predicate(test_code)
    ]


def collect_nested_stub_validation_issues(
    test_code: str, output_file_path: str, source_code: str, test_report
) -> list[str]:
    issues: list[str] = []
    mock_var_names = set(test_report.tests.mock_variables)
    for receiver, middle, leaf in re.findall(
        r"\b(?:whenever|Mockito\.`when`|Mockito\.when)\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)",
        test_code,
    ):
        if receiver in mock_var_names or receiver.lower().startswith("mock"):
            issues.append(
                "Nested mock property stubbing is unsafe when an intermediate property is not stubbed. "
                f"Stub `{receiver}.{middle}` first or use a real value object before stubbing/asserting `{middle}.{leaf}`."
            )
            break
    return issues


def collect_primitive_matcher_validation_issues(
    test_code: str, output_file_path: str, source_code: str, test_report
) -> list[str]:
    issues: list[str] = []
    if re.search(
        r"\b(anyBoolean|anyByte|anyChar|anyDouble|anyFloat|anyInt|anyLong|anyShort)\s*\(",
        test_code,
    ) or re.search(
        r"import\s+org\.mockito\.kotlin\.(anyBoolean|anyByte|anyChar|anyDouble|anyFloat|anyInt|anyLong|anyShort)\b",
        test_code,
    ):
        issues.append(
            "Use typed Mockito-Kotlin primitive matchers such as any<Int>(), any<Boolean>(), or any<Long>(); "
            "do not import or call primitive helper matchers such as anyInt()."
        )
    return issues


def collect_static_and_navigation_validation_issues(
    test_code: str, output_file_path: str, source_code: str, test_report
) -> list[str]:
    issues: list[str] = []
    issues.extend(_static_mock_validation_issues(test_code))

    if has_invalid_void_navigation_stubbing(test_code):
        issues.append(
            "invalid_void_navigation_stubbing: Do not stub NavController.navigate(NavDeepLinkRequest) with "
            "Mockito.when/thenAnswer. It is a Unit call, and Mockito.any(...) passes null to a non-null Kotlin "
            "parameter. Trigger the event, then verify(navController).navigate(captor.capture()) using a Mockito-Kotlin captor."
        )

    if has_invalid_java_nav_deeplink_captor(test_code):
        issues.append(
            "invalid_nav_deeplink_java_argumentcaptor: Do not use org.mockito.ArgumentCaptor.forClass(...).capture() "
            "inside NavController.navigate(NavDeepLinkRequest) verification. Java captor capture() returns a null "
            "matcher sentinel and Kotlin rejects it for the non-null NavDeepLinkRequest parameter. Use "
            "org.mockito.kotlin.argumentCaptor<NavDeepLinkRequest>() instead."
        )

    if "MockedStatic.mockStatic" in test_code:
        issues.append(
            "invalid_mockedstatic_factory: use org.mockito.Mockito.mockStatic(Type::class.java), not MockedStatic.mockStatic(...)."
        )

    if test_report.tests.property_assignment_verification_lines:
        issues.append(
            "invalid_property_assignment_verification: "
            + validation_repair_intent("invalid_property_assignment_verification")
        )

    return issues


def collect_mockito_import_and_api_validation_issues(
    test_code: str, output_file_path: str, source_code: str, test_report
) -> list[str]:
    issues: list[str] = []

    if (
        re.search(r"(?m)^\s*import\s+org\.mockito\.kotlin\.(?:any|verify)\b", test_code)
        and re.search(r"(?m)^\s*import\s+org\.mockito\.Mockito\.(?:any|verify)\b", test_code)
    ):
        issues.append(
            "mixed_mockito_imports: Do not import both org.mockito.kotlin.any/verify and "
            "org.mockito.Mockito.any/verify in the same Kotlin test. Pick Mockito-Kotlin for normal mocks; "
            "use fully qualified org.mockito.Mockito only for static mocking or doThrow."
        )

    if re.search(r"\bwhenever\s*\(", test_code) and "import org.mockito.kotlin.whenever" not in test_code:
        issues.append(
            "Kotlin tests that use whenever() must import org.mockito.kotlin.whenever. "
            "Use Mockito-Kotlin `whenever` for normal Kotlin mock stubbing."
        )

    if re.search(r"\bResponse\.error(?:<[^>\n]+>)?\s*\(\s*[^,\n]+,\s*null\s*\)", test_code):
        issues.append(
            "invalid_retrofit_error_body: Response.error(code, null) can throw while Mockito stubbing is unfinished. "
            "Use a concrete okhttp3.ResponseBody, for example ResponseBody.create(null, \"error\")."
        )

    if "import org.mockito.kotlin.mockStatic" in test_code:
        issues.append(
            "Static mocking uses import org.mockito.Mockito.mockStatic "
            "and keep MockedStatic open until the exercised code completes."
        )

    kotlin_object_names = set(re.findall(r"(?m)^\s*object\s+([A-Za-z_][A-Za-z0-9_]*)\b", source_code or ""))
    for mocked_static_name in re.findall(r"\bmockStatic\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)::class\.java\s*\)", test_code):
        if mocked_static_name in kotlin_object_names:
            continue

        normalized_output_path = os.path.abspath(output_file_path)
        test_marker = f"{os.sep}src{os.sep}test{os.sep}"
        search_roots = []
        if test_marker in normalized_output_path:
            module_root = normalized_output_path.split(test_marker, 1)[0]
            module_source_root = os.path.join(module_root, "src", "main")
            if os.path.isdir(module_source_root):
                search_roots.append(module_source_root)

        current = Path(normalized_output_path).resolve()
        for parent in [current.parent, *current.parents]:
            if (parent / "settings.gradle.kts").exists() or (parent / "settings.gradle").exists():
                for dir_path, dir_names, _ in os.walk(parent):
                    dir_names[:] = [
                        name
                        for name in dir_names
                        if name not in {"build", ".gradle", ".git", "generated", "ksp", ".idea"}
                    ]
                    if dir_path.endswith(os.path.join("src", "main")) and dir_path not in search_roots:
                        search_roots.append(dir_path)
                break

        object_pattern = re.compile(rf"(?m)^\s*object\s+{re.escape(mocked_static_name)}\b")
        found_object = False
        for search_root in search_roots:
            if not os.path.isdir(search_root):
                continue
            for dir_path, dir_names, file_names in os.walk(search_root):
                dir_names[:] = [
                    name
                    for name in dir_names
                    if name not in {"build", ".gradle", ".git", "generated", "ksp"}
                ]
                for file_name in file_names:
                    if not file_name.endswith(".kt"):
                        continue
                    try:
                        with open(os.path.join(dir_path, file_name), "r", encoding="utf-8") as source_file:
                            if object_pattern.search(source_file.read()):
                                kotlin_object_names.add(mocked_static_name)
                                found_object = True
                                break
                    except (OSError, UnicodeDecodeError):
                        continue
                if found_object:
                    break
            if found_object:
                break

    for object_name in sorted(kotlin_object_names):
        if f"mockStatic({object_name}::class.java)" in test_code:
            issues.append(
                f"{object_name} is a Kotlin object declared in project sources. "
                "Do not use Mockito mockStatic unless a verified @JvmStatic Java API exists. "
                "Prefer real Robolectric/ApplicationProvider behavior, a source-visible dependency seam, or a different public branch."
            )

    if re.search(r"whenever\s*\(\s*[A-Z][A-Za-z0-9_]*\.[A-Za-z0-9_]+\s*\([^)]*\bany(?:<[^>]+>)?\s*\(", test_code):
        issues.append(
            "Generated tests should not stub Kotlin object/framework helper calls as whenever(Object.method(any())). "
            "Mockito evaluates the real call during stubbing and Kotlin non-null parameters receive null from matchers. "
            "Use real Robolectric framework state or a public branch that does not require object stubbing."
        )

    if "CarPropertyEventValue" in test_code:
        issues.append(
            "android.car CarPropertyManager.getProperty returns android.car.hardware.CarPropertyValue<T>. "
            "Use android.car.hardware.CarPropertyValue<T> for getProperty return values."
        )

    if re.search(r"ShadowApplication\.getInstance\(\)\.nextStartedActivity\s*<", test_code):
        issues.append(
            "Robolectric ShadowApplication nextStartedActivity is not a generic Kotlin function. "
            "Use org.robolectric.Shadows.shadowOf(application).nextStartedActivity with an ApplicationProvider Application context."
        )

    if (
        "showConnectivityIssueDialog(context)" in test_code
        and "ShadowAlertDialog" in test_code
        and "nextStartedActivity" in test_code
    ):
        issues.append(
            "Dialog positive-button tests that assert context.startActivity should use a real Robolectric Activity context. "
            "Build an Activity with Robolectric.buildActivity(Activity::class.java).setup().get(), pass it to the source, "
            "and assert org.robolectric.Shadows.shadowOf(activity).nextStartedActivity."
        )

    if re.search(r"\.thenReturn\([^)]*\.toMutableList\(\)\s*,\s*[^)]*\.toMutableList\(\)\)", test_code):
        issues.append(
            "Mockito-Kotlin multi-value thenReturn can cause Kotlin type inference failures for Android generic list APIs. "
            "Use chained thenReturn(firstList).thenReturn(secondList) with typed queryIntentActivities matchers."
        )

    if re.search(r"(?:`when`|Mockito\.`when`|org\.mockito\.Mockito\.`when`)\s*\([^)]*\.logEvent\s*\(", test_code):
        issues.append(
            "FirebaseAnalytics.logEvent is a Unit/void method. "
            "Use org.mockito.Mockito.doThrow(exception).`when`(mockAnalytics).logEvent(eq(\"event_name\"), any<Bundle>()) "
            "or doAnswer for selected exception branches; do not use Mockito.when/logEvent(...).thenThrow."
        )

    if re.search(r"verify\s*\([^)]*\)\.logEvent\s*\(\s*\"[^\"]+\"\s*,\s*any(?:<[^>]+>)?\s*\(", test_code):
        issues.append(
            "Mockito verification with a matcher argument should use matchers for all parameters. "
            "Use verify(mockAnalytics).logEvent(eq(\"event_name\"), any<Bundle>())."
        )

    if (
        "MockedStatic<FirebaseAnalytics>" in test_code
        and re.search(r"`when`\s*\(\s*FirebaseAnalytics\.getInstance\s*\(", test_code)
    ):
        issues.append(
            "When a MockedStatic<FirebaseAnalytics> is already open, restub FirebaseAnalytics.getInstance through that MockedStatic receiver. "
            "Use firebaseAnalyticsStaticMock?.`when`<FirebaseAnalytics> { FirebaseAnalytics.getInstance(context) }?.thenThrow(...) or thenReturn(...)."
        )

    return issues


def collect_apollo_mock_stub_validation_issues(
    test_code: str, output_file_path: str, source_code: str, test_report
) -> list[str]:
    issues: list[str] = []
    method_object_stubbing = re.search(
        r"(?:`when`|Mockito\.`when`|org\.mockito\.Mockito\.`when`)\s*\(\s*"
        r"[A-Za-z_][A-Za-z0-9_]*Class\.getMethod\s*\(",
        test_code,
    )
    if method_object_stubbing:
        issues.append(
            "Mockito reflection stubbing is targeting the Method object instead of the mock result. "
            "Use mockApollo(...) or stub clazz.getMethod(getter).invoke(instance), then thenReturn(value)."
        )
    return issues


def collect_validation_issues(test_code: str, output_file_path: str, source_code: str, test_report) -> list[str]:
    """Aggregate all MockK/Mockito validation issues (for standalone use)."""
    issues: list[str] = []
    for collector in (
        collect_nested_stub_validation_issues,
        collect_primitive_matcher_validation_issues,
        collect_static_and_navigation_validation_issues,
        collect_mockito_import_and_api_validation_issues,
        collect_apollo_mock_stub_validation_issues,
    ):
        issues.extend(collector(test_code, output_file_path, source_code, test_report))
    return issues
