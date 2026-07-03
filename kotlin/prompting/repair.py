# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Kotlin compile/runtime repair prompt streaming.
"""Kotlin repair prompt streaming."""

import re

from UnitTest_gen.core.logging_utils import log_block, log_message, log_section
from UnitTest_gen.core.model_runtime import (
    print_prompt_to_terminal,
    stream_chat_completion,
)
from UnitTest_gen.core.prompt_assembly import PromptBuildSpec, PromptSection, build_prompt_bundle, format_template
from UnitTest_gen.core.pipeline_config import get_config
from UnitTest_gen.kotlin import prompt_constants
from UnitTest_gen.kotlin.gradle_analysis.errors import extract_error_line_numbers
from UnitTest_gen.kotlin.kotlin_analysis import classify_repair, classify_source, source_uses_carui_toolbar_progress
from UnitTest_gen.kotlin.project_context import collect_nearby_test_pattern_context
from UnitTest_gen.kotlin.prompting.rules import (
    _meaningful_context,
    _prompt_title,
    build_full_file_repair_rule_tail,
    build_patch_repair_rule_tail,
    build_source_strategy_context,
    should_include_repair_source_risks,
)
from UnitTest_gen.kotlin.prompt_slices import slice_for_patch_repair
from UnitTest_gen.kotlin.test_code_utils.extract import extract_json_patches, extract_kotlin_code


def _repair_categories(focused_error_block: str, source_code: str, source_categories=None):
    source_profile = classify_source(source_code)
    profile = classify_repair(source_profile, focused_error_block)
    return set(source_categories or source_profile.categories), profile.categories


def _guide_allowed(lower_block: str, repair_categories: set[str], fragment_keys: set[str], hilt_keys: set[str]) -> bool:
    if any(key in lower_block for key in fragment_keys) and not (repair_categories & {"android_fragment", "hilt_fragment", "navigation_fragment", "fixture_strategy", "lifecycle_error"}):
        return False
    if any(key in lower_block for key in hilt_keys) and not (repair_categories & {"hilt_entrypoint", "hilt_fragment", "hilt_graph", "fixture_strategy", "dependency_error"}):
        return False
    return True


def build_compiler_repair_diagnostic_guide(focused_error_block: str, *, repair_categories: set[str] | None = None, source_code: str = "") -> str:
    """
    Convert broad compiler messages into generic repair heuristics. These rules
    are deliberately language/framework level, not project-specific guardrails.
    """
    lower_block = focused_error_block.lower()
    bullets = []
    repair_categories = set(repair_categories or ())
    if source_code and not repair_categories:
        _, repair_categories = _repair_categories(focused_error_block, source_code)

    if "invalid_nav_deeplink_fixture" in lower_block:
        bullets.extend(
            [
                "- Replace TestNavHostController(...) with navController = mock(); keep the field typed as NavController.",
                "- After commitNow() and activityController.start(), call Navigation.setViewNavController(fragment.requireView(), navController).",
                "- Verify NavDeepLinkRequest navigation with argumentCaptor<NavDeepLinkRequest>() and verify(navController).navigate(captor.capture()).",
            ]
        )

    if "unfinishedstubbingexception" in lower_block or "unfinished stubbing" in lower_block:
        bullets.extend(
            [
                "- For Mockito UnfinishedStubbingException, first inspect expressions built inside thenReturn(...), thenThrow(...), or whenever(...); an exception there can leave Mockito stubbing unfinished.",
                "- If the generated test uses Response.error(code, null), replace null with a concrete okhttp3.ResponseBody such as ResponseBody.create(null, \"error\") and add import okhttp3.ResponseBody.",
                "- Only debate suspend/Unit stubbing after the thenReturn argument itself is known to be safe and type-correct.",
            ]
        )

    if "capture(...) must not be null" in lower_block or "unfinishedverificationexception" in lower_block:
        bullets.extend(
            [
                "- For Mockito verification failures with `capture(...) must not be null`, first replace Java `ArgumentCaptor.forClass(...).capture()` used in a Kotlin non-null method call with `org.mockito.kotlin.argumentCaptor<T>()`.",
                "- For NavController.navigate(NavDeepLinkRequest), use `val captor = argumentCaptor<NavDeepLinkRequest>()` and then `verify(navController).navigate(captor.capture())`; assert `captor.firstValue` or `captor.allValues`.",
                "- Treat a later UnfinishedVerificationException as likely downstream contamination until the earlier null captor or matcher misuse is fixed.",
            ]
        )

    if "cannot infer type for type parameter" in lower_block:
        bullets.extend(
            [
                "- For Kotlin type-inference errors, inspect the highlighted expression first, not the nearest return value.",
                "- If the failing expression is a Mockito stub of an overloaded method, type every matcher from the method signature before changing returned data.",
                "- Examples of generic matcher fixes: any<Type>(), eq(value), nullable<Type>(), argumentCaptor<Type>().capture(), or a typed local variable.",
                "- Add collection type arguments only after overload/matcher types are already explicit.",
            ]
        )
        if "mockstatic" in lower_block or "mockito" in lower_block:
            bullets.extend(
                [
                    "- For MockedStatic inference failures, use the MockedStatic receiver directly: mockedStatic.`when`<ReturnType> { StaticType.method(exactArg) }.thenReturn(value).",
                    "- Use exact verified arguments inside static mock lambdas; prefer real Robolectric values when they are deterministic.",
                ]
            )
        if ".`when` {" in focused_error_block or ".`when`<" in focused_error_block:
            bullets.extend(
                [
                    "- If the highlighted receiver is `MockedStatic<SomeKotlinObject>`, verify whether `SomeKotlinObject` is declared as Kotlin `object` in project sources.",
                    "- For ordinary Kotlin `object` helpers, remove Mockito `mockStatic/MockedStatic` and prefer real behavior, a verified public seam, or a different branch.",
                    "- Use Mockito static mocking only when the compiled API exposes a verified Java static or `@JvmStatic` method.",
                ]
            )

    if "overload resolution ambiguity" in lower_block or "overloaded" in lower_block:
        bullets.extend(
            [
                "- Resolve overload ambiguity by selecting the exact overload with typed arguments and typed matchers.",
                "- Prefer fixing the call site signature over adding casts around the result.",
            ]
        )

    if "unresolved reference" in lower_block:
        bullets.extend(
            [
                "- For unresolved references, use verified declarations/imports from context or project search before inventing nested names.",
                "- If a symbol looks like Outer.Inner but is unresolved, verify whether Inner is actually a top-level class that needs its own import.",
                "- If a generated test constructs an SDK/API type with an unresolved nested member such as Outer.Inner.Value, verify Outer constructors/usages first; when the nested API shape is unverified, remove or replace that branch test instead of guessing enum/class names.",
                "- If an unresolved symbol is a test-framework helper or shadow method, use a helper verified in the local dependency version or replace it with a simpler verified JUnit4/Mockito/Robolectric pattern.",
            ]
        )
        if "runuithreadtasksincludingdelayedtasks" in lower_block:
            bullets.extend(
                [
                    "- For Robolectric delayed main-looper tasks in this repo, use `org.robolectric.shadows.ShadowLooper`.",
                    "- Replace `shadowOf(fragment.requireActivity().mainLooper).runUiThreadTasksIncludingDelayedTasks()` with `ShadowLooper.runUiThreadTasksIncludingDelayedTasks()` and add `import org.robolectric.shadows.ShadowLooper`.",
                    "- Do not debate the `shadowOf(Looper)` return type for this error; the verified local pattern is the static `ShadowLooper.runUiThreadTasksIncludingDelayedTasks()` call.",
                ]
            )
        if "mockito" in lower_block:
            bullets.extend(
                [
                    "- If `Mockito.`when`` is unresolved, either import `org.mockito.Mockito` or replace normal mock stubbing with Mockito-Kotlin `whenever`; keep one Mockito style in the file.",
                    "- If the unresolved Mockito call is part of static mocking, prefer `val mocked = org.mockito.Mockito.mockStatic(Type::class.java)` plus `mocked.`when`<ReturnType> { Type.method(exactArg) }`, and close it only after the exercised code runs.",
                    "- For app utility methods that can run under Robolectric, use the real returned value through ApplicationProvider/Robolectric.",
                ]
            )

    if "argument type mismatch" in lower_block or "type mismatch" in lower_block:
        bullets.extend(
            [
                "- For argument/type mismatch errors, repair the parameter type at the highlighted call, then align captors, mocks, and literals to that signature.",
                "- For Kotlin Mockito captors, prefer captor.capture() when using org.mockito.kotlin.argumentCaptor<T>().",
            ]
        )

    if "suspend function outside coroutine" in lower_block or "can only be called from a coroutine" in lower_block:
        bullets.extend(
            [
                "- For suspend collaborator stubbing or verification, call the suspend method from runTest/runBlocking or another verified coroutine scope.",
                "- Keep the public method under test unchanged; wrap only the Mockito stubbing/verification call that invokes the suspend collaborator.",
                "- If the source launches hard-coded Dispatchers.IO, use timeout/atLeast verification only for stable collaborator calls and avoid fragile final-state assertions.",
            ]
        )

    if "unfinishedstubbingexception" in lower_block or "unfinished stubbing" in lower_block:
        bullets.extend(
            [
                "- For Mockito unfinished stubbing around static mocks, make the static lambda call concrete and side-effect free; prefer exact verified arguments over broad matchers.",
                "- If the returned mock is lateinit/@Mock, verify it is initialized before thenReturn; under non-Mockito runners, create mocks explicitly or open Mockito annotations.",
            ]
        )

    if "invaliduseofmatchersexception" in lower_block or "invalid use of argument matchers" in lower_block:
        bullets.extend(
            [
                "- For Mockito matcher misuse, use matchers for every argument in that invocation, such as eq(\"event_name\") plus any<Bundle>().",
                "- For Unit/void methods such as FirebaseAnalytics.logEvent, stub thrown exceptions with org.mockito.Mockito.doThrow(exception).`when`(mock).method(eq(...), any<Type>()) instead of `when`(...).thenThrow(...).",
                "- Repair both the stubbing line and the matching verify line when the same method mixes a raw value with any()/eq().",
            ]
        )

    if "checked exception is invalid for this method" in lower_block:
        bullets.extend(
            [
                "- For Mockito checked-exception failures, the stubbed method signature does not allow that checked exception; do not keep changing constructor arguments.",
                "- Replace the invalid checked-exception branch with a verified unchecked exception path, a stable collaborator interaction test, or remove the speculative branch test.",
                "- For suspend collaborator methods, keep stubbing/verifying inside runTest/runBlocking and use only exceptions verified to be throwable by the method.",
            ]
        )

    if "toomanyactualinvocations" in lower_block or "too many actual invocations" in lower_block:
        bullets.extend(
            [
                "- For Mockito TooManyActualInvocations, do not blindly change times(1) to the observed call count.",
                "- Prefer verifying a stable public effect, clearing setup invocations before the action under test, or using atLeastOnce() only when repeated calls are valid source behavior.",
                "- If extra calls come from dialog/fragment setup outside the test intent, narrow the assertion to the behavior under test or remove the strict count assertion.",
            ]
        )

    if "comparisonfailure" in lower_block or "assertionerror" in lower_block:
        bullets.extend(
            [
                "- For assertion failures, keep the assertion connected to exercised behavior; do not replace the actual expression with the expected literal or source constant.",
                "- For Fragment navigation, assert the captured NavDeepLinkRequest/TestNavHostController destination produced by the Fragment, not assertEquals(sourceConstant, hard-coded literal).",
            ]
        )

    if "parameter specified as non-null is null" in lower_block:
        bullets.append(
            "- A Mockito matcher returned null during stubbing of a Kotlin non-null parameter. Do not stub Kotlin object/framework helpers as whenever(Object.method(any())); use real Robolectric framework state or a branch that avoids object stubbing."
        )

    if "[dagger/duplicatebindings]" in lower_block or "is bound multiple times" in lower_block:
        bullets.extend(
            [
                "- For Dagger DuplicateBindings, identify the duplicated type and both binding sources before editing the test.",
                "- Remove the duplicate @BindValue/@Provides from the test when production Hilt already provides the same unqualified type.",
                "- If the test truly needs to replace a production binding, use a verified @UninstallModules/@TestInstallIn replacement for the exact owning module; do not add @BindValue on top of an existing production binding.",
                "- Treat later generated-component cannot-find-symbol errors as fallout from the duplicate binding until the duplicate is removed.",
            ]
        )

    if "wantedbutnotinvoked" in lower_block or "wanted but not invoked" in lower_block:
        bullets.extend(
            [
                "- For Mockito WantedButNotInvoked, inspect the source branch condition before changing verification; a missing call usually means setup did not reach the branch.",
                "- Do not blindly change a positive verify(...) to verify(..., never()). Keep the intended interaction when the source branch should call it.",
                "- For BuildConfig-gated branches, align the test with the actual Gradle variant value or drive the callback/public path that reaches the expected interaction.",
                "- For Flow/Lifecycle assertions in Fragment tests, attach and start/resume the Fragment with a real lifecycle owner before changing MutableStateFlow values or observer expectations.",
            ]
        )

    if "static mocking is already registered" in lower_block:
        bullets.extend(
            [
                "- A Java static mock is already open on the current thread; reuse the existing MockedStatic field instead of calling mockStatic for the same class again.",
                "- Restub static methods through the open MockedStatic receiver, for example firebaseAnalyticsStaticMock?.`when`<FirebaseAnalytics> { FirebaseAnalytics.getInstance(context) }?.thenThrow(exception).",
                "- Keep MockedStatic fields nullable and close every opened static mock in tearDown in reverse setup order.",
            ]
        )

    if "lateinit property" in lower_block and "has not been initialized" in lower_block:
        bullets.extend(
            [
                "- For uninitialized @Mock fields, check the test runner. RobolectricTestRunner does not initialize Mockito @Mock fields by itself.",
                "- Initialize mocks with MockitoAnnotations.openMocks(this) in setup/teardown or replace @Mock fields with explicit Mockito-Kotlin mock() assignments.",
            ]
        )

    if "suspend function" in lower_block or "coroutine" in lower_block:
        bullets.extend(
            [
                "- For coroutine errors, use runTest/test dispatchers for suspend APIs and prefer virtual-time or early observable assertions when the source uses hard-coded dispatchers or long delays.",
            ]
        )

    if "assertionerror" in lower_block or "assertion failed" in lower_block:
        bullets.extend(
            [
                "- For assertion failures, compare the assertion against actual source timing/state. Preserve only assertions that the source can deterministically satisfy.",
                "- For launched asynchronous work with hard-coded long delays, assert an early observable effect or remove the completion assertion.",
            ]
        )

    if not bullets:
        bullets.append(
            "- Repair the exact highlighted expression first, using compiler line/column data and verified signatures from context."
        )

    return "\n".join(dict.fromkeys(bullets))

def format_error_location_context(current_test_code: str, focused_error_block: str, radius: int = 8) -> str:
    """
    Give the repair model compiler-provided line numbers with exact generated
    test excerpts. This avoids brittle manual line counting over the full file.
    """
    locations_by_line = {}
    ordered_lines = []

    for match in re.finditer(r"([^:\s]+\.kt):(\d+):(\d+)\s+(.+)", focused_error_block):
        line_no = int(match.group(2))
        column_no = int(match.group(3))
        message = match.group(4).strip()
        if line_no not in locations_by_line:
            locations_by_line[line_no] = []
            ordered_lines.append(line_no)
        entry = (column_no, message)
        if entry not in locations_by_line[line_no]:
            locations_by_line[line_no].append(entry)

    if not ordered_lines:
        line_numbers = extract_error_line_numbers(focused_error_block)
        for line_no in line_numbers:
            if line_no not in locations_by_line:
                locations_by_line[line_no] = [(1, "Compiler/JUnit error location")]
                ordered_lines.append(line_no)

    if not ordered_lines:
        return "Compiler line location was unavailable in the focused error block."

    lines = current_test_code.splitlines()
    sections = []

    for line_no in ordered_lines[:12]:
        column_messages = locations_by_line[line_no]
        start = max(1, line_no - radius)
        end = min(len(lines), line_no + radius)
        excerpt = []

        for current_no in range(start, end + 1):
            marker = ">>" if current_no == line_no else "  "
            text = lines[current_no - 1] if current_no - 1 < len(lines) else ""
            excerpt.append(f"{marker} {current_no:4}: {text}")
            if current_no == line_no:
                for column_no, _ in column_messages[:4]:
                    if column_no > 0:
                        caret_prefix = " " * (8 + max(0, column_no - 1))
                        excerpt.append(f"{caret_prefix}^ compiler column {column_no}")

        message_lines = [
            f"- column {column_no}: {message}"
            for column_no, message in column_messages[:6]
        ]

        sections.append(
            "\n".join(
                [
                    f"Compiler messages at generated test line {line_no}:",
                    *message_lines,
                    "",
                    "Line-numbered generated-test excerpt:",
                    *excerpt,
                ]
            )
        )

    return "\n\n".join(sections)

def _extract_imports(kotlin_code: str) -> set[str]:
    return {
        match.group(1).strip()
        for match in re.finditer(r"^import\s+(.+)$", kotlin_code, flags=re.MULTILINE)
    }

def _function_block_contains(code: str, annotation: str, pattern: str) -> bool:
    annotation_index = code.find(annotation)
    if annotation_index < 0:
        return False

    next_test_index = code.find("@Test", annotation_index + len(annotation))
    block = code[annotation_index:] if next_test_index < 0 else code[annotation_index:next_test_index]
    return pattern in block

def build_generated_test_static_diagnostics(
    current_test_code: str,
    focused_error_block: str,
    source_code: str = "",
    repair_categories: set[str] | None = None,
) -> str:
    """
    Detect generated-test smells that commonly hide behind narrow compiler
    messages. The output is intentionally small and repair-oriented.
    """
    repair_categories = set(repair_categories or ())
    if source_code and not repair_categories:
        _, repair_categories = _repair_categories(focused_error_block, source_code)
    lower_error = focused_error_block.lower()
    imports = _extract_imports(current_test_code)
    bullets = []

    if "invalid_nav_deeplink_fixture" in lower_error or (
        "NavDeepLinkRequest" in (source_code or "")
        and "TestNavHostController" in current_test_code
        and ".setGraph(" not in current_test_code
    ):
        bullets.extend(
            [
                "invalid_nav_deeplink_fixture: replace TestNavHostController(...) with navController = mock(); do not keep TestNavHostController without .setGraph(...).",
                "Install navigation after attach: Navigation.setViewNavController(fragment.requireView(), navController) following commitNow() and activityController.start().",
                "Capture cross-graph navigation with argumentCaptor<NavDeepLinkRequest>() and verify(navController).navigate(captor.capture()).",
            ]
        )

    uses_unqualified_mockito = bool(re.search(r"(?<![\w.])Mockito\.", current_test_code))
    has_mockito_import = "org.mockito.Mockito" in imports or "org.mockito.Mockito.*" in imports
    if uses_unqualified_mockito and not has_mockito_import:
        bullets.append(
            "Unqualified `Mockito.` needs `import org.mockito.Mockito`; for normal mocks, prefer Mockito-Kotlin `whenever`, or use fully qualified `org.mockito.Mockito`."
        )

    has_mock_static = "mockStatic(" in current_test_code or "mockStatic<" in current_test_code
    closes_static_in_before = has_mock_static and _function_block_contains(
        current_test_code,
        "@Before",
        ".close()",
    )
    if closes_static_in_before:
        bullets.append(
            "A MockedStatic is closed inside setup before @Test methods run; keep it open until tearDown/use-block completion, or remove the static mock."
        )

    if has_mock_static and re.search(r"mockStatic\([^)]+\).*?any<", current_test_code, flags=re.DOTALL):
        bullets.append(
            "Static mock setup uses broad Kotlin matchers; MockedStatic lambdas compile more reliably with exact verified arguments and explicit return type."
        )

    static_app_utility = has_mock_static and re.search(
        r"(getAppVersion|getPackageInfo|packageManager|getSystemService)",
        current_test_code,
    )
    if static_app_utility:
        bullets.append(
            "The test static-mocks an app/framework utility that can usually run under Robolectric; use ApplicationProvider/Robolectric real values and assert against the helper result."
        )

    if (
        ("unfinishedstubbingexception" in lower_error or "unfinished stubbing" in lower_error)
        and re.search(r"\bResponse\.error(?:<[^>\n]+>)?\s*\(\s*[^,\n]+,\s*null\s*\)", current_test_code)
    ):
        bullets.append(
            "Retrofit Response.error(code, null) can throw while Mockito is still recording a stub. Replace null with ResponseBody.create(null, \"error\") and import okhttp3.ResponseBody."
        )

    if (
        "@AndroidEntryPoint" in source_code
        and "GeneratedComponent" in focused_error_block
        and "GeneratedComponentManager" in focused_error_block
    ):
        bullets.append(
            "@AndroidEntryPoint fragment lifecycle attachment requires Hilt test setup: use @HiltAndroidTest, HiltAndroidRule, and @Config(application = HiltTestApplication::class), or remove lifecycle attachment tests and keep only direct public-method tests that do not trigger onAttach."
        )
        bullets.append(
            "Plain FragmentActivity, android.app.Application, and EmptyRobolectricApplication do not satisfy Hilt GeneratedComponent requirements for @AndroidEntryPoint fragments."
        )

    if "@AndroidEntryPoint" in source_code and (
        "activityClass" in focused_error_block
        or "No value passed for parameter 'instantiate'" in focused_error_block
        or "launchFragmentInContainer" in current_test_code
    ):
        bullets.append(
            "For @AndroidEntryPoint Fragment lifecycle tests, replace launchFragmentInContainer/FragmentScenario.launchInContainer with a Hilt host Activity launched directly through ActivityScenario or Robolectric."
        )
        bullets.append(
            "Use the module's manifest-declared HiltTestActivity and replace TargetFragment with the actual fragment class under test. "
            "For Robolectric, use: val activityController = Robolectric.buildActivity(HiltTestActivity::class.java).create(); "
            "activityController.get().setTheme(androidx.appcompat.R.style.Theme_AppCompat); attach TargetFragment with commitNow(); "
            "activityController.start(); install Navigation.setViewNavController(fragment.requireView(), navController); then activityController.resume()."
        )
        bullets.append(
            "Do not add `activityClass = ...` to launchFragmentInContainer; that named parameter is not available in the local Fragment testing API and the default FragmentScenario host is not @AndroidEntryPoint."
        )

    if "HiltAndroidRunner" in current_test_code:
        bullets.append(
            "Local JVM Hilt/Robolectric fragment tests should use @RunWith(RobolectricTestRunner::class); keep @HiltAndroidTest, HiltAndroidRule, and HiltTestApplication for Hilt setup."
        )

    if "fragmentmanager has not been attached to a host" in lower_error:
        bullets.append(
            "invalid_fragment_host_lifecycle: rewrite the setup fixture shape instead of patching only the failing line. "
            "Use buildActivity(...).create(), set Theme_AppCompat, attach the Fragment with supportFragmentManager.commitNow(), "
            "call start(), install Navigation.setViewNavController(...), then resume only if needed."
        )
        if "@AndroidEntryPoint" in source_code:
            bullets.append(
                "For @AndroidEntryPoint Fragment fixture repair, keep @HiltAndroidTest, HiltAndroidRule, "
                "@Config(application = HiltTestApplication::class), and the manifest-declared module HiltTestActivity. "
                "Do not switch to a local fake Activity, private-field reflection injection, or partial non-Hilt host."
            )

    if (
        "Theme.AppCompat theme" in focused_error_block
        and "Robolectric.buildActivity(HiltTestActivity::class.java)" in current_test_code
    ):
        bullets.append(
            "The Robolectric-built HiltTestActivity is an AppCompatActivity and needs a theme before resume. "
            "For navigation tests, use order: create ActivityController, "
            "`activityController.get().setTheme(androidx.appcompat.R.style.Theme_AppCompat)`, attach the Fragment with commitNow(), "
            "call `activityController.start()`, install NavController with Navigation.setViewNavController(fragment.requireView(), navController) "
            "(mock() for NavDeepLinkRequest sources; TestNavHostController only when .setGraph(...) is installed), then `activityController.resume()`."
        )

    if source_uses_carui_toolbar_progress(source_code) and (
        "getprogressbar" in lower_error
        or "toolbar.progressbar" in lower_error
        or "progressbar" in focused_error_block.lower()
        or ("ToolbarController" in current_test_code and "CarUi.requireToolbar" in current_test_code)
    ):
        bullets.append(
            "Source reads toolbar.progressBar from the ToolbarController returned by CarUi.requireToolbar(...). If ToolbarController is mocked, also mock ProgressBarController and stub `whenever(toolbar.progressBar).thenReturn(progressBarController)` before fragment attach/resume."
        )
        bullets.append(
            "Verify the ProgressBarController mock directly. Do not verify or assign through a chained `toolbar.progressBar` access because an unstubbed mocked property returns null."
        )

    if (
        "@AndroidEntryPoint" in source_code
        and "findNavController" in source_code
        and "Robolectric.buildActivity(HiltTestActivity::class.java)" in current_test_code
        and "Navigation.setViewNavController" in current_test_code
    ):
        first_resume = current_test_code.find(".resume()")
        nav_install = current_test_code.find("Navigation.setViewNavController")
        if first_resume != -1 and nav_install != -1 and first_resume < nav_install:
            bullets.append(
                "The host Activity resumes before the Fragment view has a TestNavHostController. "
                "Move ActivityController.resume() after fragment commitNow(), activityController.start(), and Navigation.setViewNavController(...)."
            )

    if "wantedbutnotinvoked" in lower_error or "wanted but not invoked" in lower_error:
        bullets.append(
            "Mockito WantedButNotInvoked means the expected interaction was not reached; inspect the source branch condition and test setup before changing verification."
        )
        if "BuildConfig" in source_code or "BuildConfig" in current_test_code or "BuildConfig" in focused_error_block:
            bullets.append(
                "For BuildConfig-gated behavior, match the test expectation to the active Gradle variant value or drive the callback/public path that executes the expected branch."
            )
        if (
            "MutableStateFlow" in source_code
            or "StateFlow" in source_code
            or "Flow" in source_code
            or "MutableStateFlow" in current_test_code
            or "StateFlow" in current_test_code
        ):
            bullets.append(
                "For Flow/Lifecycle Fragment assertions, attach the Fragment and move its lifecycle to started/resumed before changing MutableStateFlow values or observer expectations."
            )
        if re.search(r"verify\s*\([^)]*\)\.", current_test_code):
            bullets.append(
                "Keep positive verify(...) assertions when the source branch should call the collaborator; use never() only for a source-verified no-call branch."
            )

    if (
        "parameter specified as non-null is null" in lower_error
        and re.search(r"whenever\s*\([^)]*\bany(?:<[^>]+>)?\s*\(", current_test_code)
    ):
        bullets.append(
            "The test stubs a call with any() where Kotlin requires a non-null parameter; Mockito matchers pass null during stubbing if the real helper is evaluated."
        )
        bullets.append(
            "Do not repair this by changing any() to another matcher. Remove the Kotlin object/framework helper stub and use real Robolectric framework state, source-visible seams, or a public branch that avoids the helper."
        )

    if "toomanyactualinvocations" in lower_error or "too many actual invocations" in lower_error:
        bullets.append(
            "Do not repair TooManyActualInvocations by increasing times(n) to the observed count; that preserves incidental setup calls instead of the behavior under test."
        )

    if re.search(r"assertEquals\(\s*[A-Za-z_][A-Za-z0-9_.]*\s*,\s*\"[^\"]+\"\s*\)", current_test_code):
        bullets.append(
            "The test contains source-constant-to-literal assertions that can become vacuous after repair. Keep assertions tied to captured navigation, visible view state, emitted state, or collaborator calls."
        )

    if "@AndroidEntryPoint" in source_code and "findNavController" in source_code:
        if re.search(r"whenever\s*\([^)]*findNavController\s*\(", current_test_code):
            bullets.append(
                "The test stubs fragment.findNavController(), which is an AndroidX extension on a real Fragment; attach the fragment, call activityController.start() when using Robolectric ActivityController, and install TestNavHostController with Navigation.setViewNavController instead."
            )
        if re.search(r"\.\s*on(?:CreateView|ViewCreated)\s*\(", current_test_code):
            bullets.append(
                "The test calls fragment lifecycle methods directly; full Hilt/Robolectric fragment tests should launch the Hilt host Activity and attach the Fragment with supportFragmentManager.commitNow() so requireContext, viewLifecycleOwner, and findNavController have real owners."
            )
        if re.search(r"\b(getPrivate|readPrivate|binding)\s*<[^>]*Binding\b|Fragment[A-Za-z0-9_]*Binding", current_test_code):
            bullets.append(
                "The test reaches private binding internals; assert visible behavior through the attached root view or public effects instead of private binding fields/generated binding classes."
            )
        if re.search(r"\bgetDeclaredMethod\s*\(", current_test_code) or "fragment::class.java" in current_test_code:
            bullets.append(
                "The test is trying to cover private Fragment UI/lifecycle methods by reflection. Repair it into visible behavior through full verified Hilt/Robolectric attachment, or stop with the invalid generated test intact."
            )
        if "lateinit property fragment has not been initialized" in lower_error or (
            re.search(r"\blateinit\s+var\s+fragment\b", current_test_code)
            and not re.search(r"(?m)^\s*(?:private\s+)?fun\s+setUp\s*\(|^\s*@Before\b[\s\S]{0,500}\bfragment\s*=", current_test_code)
        ):
            bullets.append(
                "The generated Fragment fixture is not deterministically initialized. Initialize the fixture in @Before before each test, or replace unattached-fragment scenarios with valid public-contract coverage."
            )
        if "activityViewModels" in source_code and re.search(r"\bviewModel\.[A-Za-z0-9_]+\.postValue\s*\(", current_test_code):
            bullets.append(
                "Posting values into a standalone mocked activityViewModels dependency does not exercise Fragment observers. Use verified Hilt lifecycle attachment, or replace observer scenarios with valid public-contract coverage."
            )
        if "activityViewModels" in source_code and re.search(r"@BindValue\s+lateinit\s+var\s+\w+\s*:\s*\w*ViewModel\b", current_test_code):
            bullets.append(
                "@BindValue ViewModel fields do not replace the Fragment's by activityViewModels()/viewModels() delegate. The attached Fragment obtains its ViewModel from ViewModelProvider/Hilt factory; repair by using the real delegated ViewModel public methods or by completing the Hilt graph."
            )
        if (
            re.search(r"\.observe\s*\(\s*viewLifecycleOwner", source_code)
            and re.search(r"MutableLiveData\s*\(\s*AccountState\.(?:User|Owner|Orphan|Error)", current_test_code)
            and re.search(r"\.commitNow\(\)[\s\S]{0,700}Navigation\.setViewNavController", current_test_code)
        ):
            bullets.append(
                "The test seeds LiveData with a navigation AccountState before attach. commitNow() runs onViewCreated synchronously, so observer navigation happens before Navigation.setViewNavController is installed. Use an initially empty observable/state, install the NavController after the view exists, then emit through the same real ViewModel instance or verified public setter."
            )

    if "@AndroidEntryPoint" in source_code and "[Dagger/MissingBinding]" in focused_error_block:
        bullets.append(
            "hilt_graph_unclosed: Dagger/MissingBinding in a Hilt Fragment test means the test component graph is incomplete. "
            "Resolve direct dependency/binding visibility for the exact missing type first, or switch to reduced non-attached public-contract tests."
        )
        bullets.append(
            "Do not bypass hilt_graph_unclosed with private-field reflection, local fake HiltTestActivity, manual ViewModelStore insertion, or partial attached lifecycle setup."
        )

    if "[Dagger/DuplicateBindings]" in focused_error_block or "is bound multiple times" in focused_error_block:
        bullets.append(
            "Dagger/DuplicateBindings means the generated test added a binding for a type already provided by production Hilt; remove the duplicate test binding unless the exact production module is replaced."
        )
        duplicate_type_match = re.search(
            r"\[Dagger/DuplicateBindings]\s+([A-Za-z0-9_.$]+)\s+is bound multiple times",
            focused_error_block,
        )
        if duplicate_type_match:
            duplicate_simple_name = duplicate_type_match.group(1).split(".")[-1]
            if "@BindValue lateinit var" in current_test_code and duplicate_simple_name in current_test_code:
                bullets.append(
                    f"The current test appears to bind {duplicate_simple_name}; remove that @BindValue field and related mock setup when production Hilt already binds {duplicate_simple_name}."
                )
        bullets.append(
            "Generated Hilt component cannot-find-symbol errors after DuplicateBindings are secondary fallout; repair the duplicate binding first."
        )

    if (
        "todo(" in source_code.lower()
        and "unsupportedoperationexception" in current_test_code.lower()
        and ("exception" in lower_error or "junit" in lower_error or "runtime" in lower_error)
    ):
        bullets.append(
            "Source uses Kotlin TODO(...), which throws NotImplementedError; replace UnsupportedOperationException expectations for that path."
        )

    if not bullets:
        return "No static generated-test smells detected for this error block."

    return "\n".join(f"- {bullet}" for bullet in dict.fromkeys(bullets))


def _build_focused_repair_prompt_bundle(
    *,
    class_name,
    source_code,
    current_test_code,
    focused_error_block,
    verified_context,
    group_key,
    repair_scope_text,
    repair_system_prompt,
    final_rule_tail,
    error_location_context="",
    compiler_repair_guide="",
    generated_test_diagnostics="",
    source_specific_strategy="",
    nearby_test_patterns="",
    repair_source_risk_context="",
    memory_context="",
):
    ticks = chr(96) * 3
    repair_intro = format_template(prompt_constants.REPAIR_INTRO_TEMPLATE, repair_scope_text=repair_scope_text)
    prompt_test_code = current_test_code
    prompt_source_code = source_code
    if repair_system_prompt == prompt_constants.PATCH_REPAIR_SYSTEM_PROMPT and get_config().prompt_slices_enabled:
        slices = slice_for_patch_repair(
            source_code,
            current_test_code,
            focused_error_block=focused_error_block,
        )
        prompt_test_code = slices.test_slice
        prompt_source_code = slices.source_slice
        if slices.omitted_summary:
            prompt_test_code = (
                f"// Scoped for patch repair; full file is patched on disk.\n"
                f"// {slices.omitted_summary}\n\n"
                f"{prompt_test_code}"
            )
    return build_prompt_bundle(
        PromptBuildSpec(
            repair_system_prompt,
            [
                PromptSection("", repair_intro.strip()),
                PromptSection(_prompt_title(prompt_constants.REPAIR_USER_SECTION_TITLES, "class_name"), class_name),
                PromptSection(_prompt_title(prompt_constants.REPAIR_USER_SECTION_TITLES, "focused_error"), f"{ticks}text\n{focused_error_block}\n{ticks}"),
                PromptSection(_prompt_title(prompt_constants.REPAIR_USER_SECTION_TITLES, "error_locations"), f"{ticks}text\n{error_location_context}\n{ticks}"),
                PromptSection(_prompt_title(prompt_constants.REPAIR_USER_SECTION_TITLES, "compiler_guide"), compiler_repair_guide),
                PromptSection(_prompt_title(prompt_constants.REPAIR_USER_SECTION_TITLES, "generated_diagnostics"), _meaningful_context(generated_test_diagnostics)),
                PromptSection(_prompt_title(prompt_constants.REPAIR_USER_SECTION_TITLES, "source_strategy"), source_specific_strategy),
                PromptSection(_prompt_title(prompt_constants.REPAIR_USER_SECTION_TITLES, "nearby_patterns"), _meaningful_context(nearby_test_patterns)),
                PromptSection(_prompt_title(prompt_constants.REPAIR_USER_SECTION_TITLES, "verified_context"), f"{ticks}text\n{verified_context}\n{ticks}" if verified_context else ""),
                PromptSection(_prompt_title(prompt_constants.REPAIR_USER_SECTION_TITLES, "source_risks"), f"{ticks}text\n{repair_source_risk_context}\n{ticks}" if repair_source_risk_context else ""),
                PromptSection(_prompt_title(prompt_constants.REPAIR_USER_SECTION_TITLES, "current_test"), f"{ticks}kotlin\n{prompt_test_code}\n{ticks}"),
                PromptSection(_prompt_title(prompt_constants.REPAIR_USER_SECTION_TITLES, "source_code"), f"{ticks}kotlin\n{prompt_source_code}\n{ticks}"),
                PromptSection("", memory_context),
                PromptSection("", final_rule_tail),
            ],
        )
    )


def repair_focused_error_patch_streaming(
    class_name,
    source_code,
    current_test_code,
    focused_error_block,
    verified_context,
    group_key,
    source_risk_context="",
    output_file_path=None,
    source_categories=None,
    memory_context="",
):
    ticks = chr(96) * 3
    final_rule_tail = build_patch_repair_rule_tail(
        ticks,
        source_code=source_code,
        current_test_code=current_test_code,
        source_categories=source_categories,
        error_context=focused_error_block,
    )
    error_location_context = format_error_location_context(
        current_test_code,
        focused_error_block,
    )
    source_categories, repair_categories = _repair_categories(focused_error_block, source_code, source_categories)
    compiler_repair_guide = build_compiler_repair_diagnostic_guide(focused_error_block, repair_categories=repair_categories, source_code=source_code)
    generated_test_diagnostics = build_generated_test_static_diagnostics(
        current_test_code=current_test_code,
        focused_error_block=focused_error_block,
        source_code=source_code,
        repair_categories=repair_categories,
    )
    source_specific_strategy = build_source_strategy_context(class_name, source_code, output_file_path)
    include_nearby = any(
        token in f"{focused_error_block}\n{group_key}".lower()
        for token in ("fixture", "lifecycle", "fragment", "activity", "robolectric", "hilt", "host", "navcontroller")
    )
    nearby_test_patterns = ""
    if include_nearby:
        nearby_test_patterns = collect_nearby_test_pattern_context(
            output_file_path=output_file_path,
            current_test_code=current_test_code,
            class_name=class_name,
            source_categories=source_categories,
            require_category_overlap=True,
        )
    repair_source_risk_context = source_risk_context if should_include_repair_source_risks(focused_error_block, group_key) else ""
    is_batch_compile_repair = group_key == "Batch Kotlin compile repair"
    is_causal_repair = "ROOT CAUSE REPAIR BATCH" in focused_error_block
    repair_scope_text = (
        "Fix this batch of Kotlin compile errors in one minimal JSON patch set.\n"
        "All listed errors are compile-time issues from the same generated test file.\n"
        "Repair imports, types, constructor arguments, helper signatures, overload selections, and unresolved symbols only where needed.\n"
        "Keep runtime/JUnit behavior, assertions, and existing passing tests unchanged."
        if is_batch_compile_repair
        else (
            "Fix this causal root-cause repair batch.\n"
            "Repair the PRIMARY ROOT CAUSE first. Do not patch cascade symptoms directly unless the same edit is required to complete the primary fix.\n"
            "Use PEER ERRORS IN SAME CATEGORY to make the fix consistent across all affected imports, types, signatures, or setup calls.\n"
            "After the primary fix, expected cascade errors should disappear on the next Gradle run.\n"
            f"Selected root cause: {group_key}"
        )
        if is_causal_repair
        else (
            "Fix this GROUP of related compiler/test errors.\n"
            "They share the same root cause:\n"
            f"{group_key}"
        )
    )

    repair_intro = format_template(prompt_constants.REPAIR_INTRO_TEMPLATE, repair_scope_text=repair_scope_text)
    repair_system_prompt = prompt_constants.PATCH_REPAIR_SYSTEM_PROMPT
    prompt_bundle = build_prompt_bundle(
        PromptBuildSpec(
            repair_system_prompt,
            [
            PromptSection("", repair_intro.strip()),
            PromptSection(_prompt_title(prompt_constants.REPAIR_USER_SECTION_TITLES, "class_name"), class_name),
            PromptSection(_prompt_title(prompt_constants.REPAIR_USER_SECTION_TITLES, "focused_error"), f"{ticks}text\n{focused_error_block}\n{ticks}"),
            PromptSection(_prompt_title(prompt_constants.REPAIR_USER_SECTION_TITLES, "error_locations"), f"{ticks}text\n{error_location_context}\n{ticks}"),
            PromptSection(_prompt_title(prompt_constants.REPAIR_USER_SECTION_TITLES, "compiler_guide"), compiler_repair_guide),
            PromptSection(_prompt_title(prompt_constants.REPAIR_USER_SECTION_TITLES, "generated_diagnostics"), _meaningful_context(generated_test_diagnostics)),
            PromptSection(_prompt_title(prompt_constants.REPAIR_USER_SECTION_TITLES, "source_strategy"), source_specific_strategy),
            PromptSection(_prompt_title(prompt_constants.REPAIR_USER_SECTION_TITLES, "nearby_patterns"), _meaningful_context(nearby_test_patterns)),
            PromptSection(_prompt_title(prompt_constants.REPAIR_USER_SECTION_TITLES, "verified_context"), f"{ticks}text\n{verified_context}\n{ticks}" if verified_context else ""),
            PromptSection(_prompt_title(prompt_constants.REPAIR_USER_SECTION_TITLES, "source_risks"), f"{ticks}text\n{repair_source_risk_context}\n{ticks}" if repair_source_risk_context else ""),
            PromptSection(_prompt_title(prompt_constants.REPAIR_USER_SECTION_TITLES, "current_test"), f"{ticks}kotlin\n{current_test_code}\n{ticks}"),
            PromptSection(_prompt_title(prompt_constants.REPAIR_USER_SECTION_TITLES, "source_code"), f"{ticks}kotlin\n{source_code}\n{ticks}"),
            PromptSection("", memory_context),
            PromptSection("", final_rule_tail),
            ],
        )
    )
    repair_prompt = prompt_bundle.user_prompt

    log_section(f"PATCH REPAIR REQUEST: {group_key}", category="fix")
    log_block("PATCH REPAIR PROMPT", repair_prompt, category="fix", console=False)
    print_prompt_to_terminal(
        title=f"FINAL PATCH REPAIR PROMPT SENT TO MODEL: {class_name}",
        system_prompt=prompt_bundle.system_prompt,
        user_prompt=repair_prompt,
    )
    log_message("\n🔧 Streaming focused patch repair output:\n", category="fix")

    raw_output = stream_chat_completion(
        messages=list(prompt_bundle.messages),
        temperature=0.06 if is_batch_compile_repair else 0.09,
        extra_body={
            "thinking_budget_tokens": 4096,
            "presence_penalty": 0.1,
            "repeat_penalty": 1.1,
        },
    )

    return extract_json_patches(raw_output)

def repair_focused_error_block_streaming(
    class_name,
    source_code,
    current_test_code,
    focused_error_block,
    verified_context,
    group_key,
    source_risk_context="",
    output_file_path=None,
    source_categories=None,
    memory_context="",
):
    ticks = chr(96) * 3
    final_rule_tail = build_full_file_repair_rule_tail(
        ticks,
        source_code=source_code,
        current_test_code=current_test_code,
        source_categories=source_categories,
        error_context=focused_error_block,
    )
    error_location_context = format_error_location_context(
        current_test_code,
        focused_error_block,
    )
    source_categories, repair_categories = _repair_categories(focused_error_block, source_code, source_categories)
    compiler_repair_guide = build_compiler_repair_diagnostic_guide(focused_error_block, repair_categories=repair_categories, source_code=source_code)
    generated_test_diagnostics = build_generated_test_static_diagnostics(
        current_test_code=current_test_code,
        focused_error_block=focused_error_block,
        source_code=source_code,
        repair_categories=repair_categories,
    )
    source_specific_strategy = build_source_strategy_context(class_name, source_code, output_file_path)
    include_nearby = any(
        token in f"{focused_error_block}\n{group_key}".lower()
        for token in ("fixture", "lifecycle", "fragment", "activity", "robolectric", "hilt", "host", "navcontroller")
    )
    nearby_test_patterns = ""
    if include_nearby:
        nearby_test_patterns = collect_nearby_test_pattern_context(
            output_file_path=output_file_path,
            current_test_code=current_test_code,
            class_name=class_name,
            source_categories=source_categories,
            require_category_overlap=True,
        )
    repair_source_risk_context = source_risk_context if should_include_repair_source_risks(focused_error_block, group_key) else ""

    if "ROOT CAUSE REPAIR BATCH" in focused_error_block:
        repair_scope_text = (
            "Fix this causal root-cause repair batch by rewriting only what is needed.\n"
            "Repair the PRIMARY ROOT CAUSE first. Do not patch cascade symptoms directly unless the same edit is required to complete the primary fix.\n"
            "Use PEER ERRORS IN SAME CATEGORY to keep imports, types, signatures, and setup calls consistent.\n"
            "After the primary fix, expected cascade errors should disappear on the next Gradle run.\n"
            f"Selected root cause: {group_key}"
        )
    else:
        repair_scope_text = (
            "Fix this GROUP of related compiler/test errors.\n"
            "They share the same root cause:\n"
            f"{group_key}"
        )
    repair_intro = format_template(prompt_constants.REPAIR_INTRO_TEMPLATE, repair_scope_text=repair_scope_text)
    repair_system_prompt = prompt_constants.FULL_FILE_REPAIR_SYSTEM_PROMPT
    prompt_bundle = build_prompt_bundle(
        PromptBuildSpec(
            repair_system_prompt,
            [
            PromptSection("", repair_intro.strip()),
            PromptSection(_prompt_title(prompt_constants.REPAIR_USER_SECTION_TITLES, "class_name"), class_name),
            PromptSection(_prompt_title(prompt_constants.REPAIR_USER_SECTION_TITLES, "focused_error"), f"{ticks}text\n{focused_error_block}\n{ticks}"),
            PromptSection(_prompt_title(prompt_constants.REPAIR_USER_SECTION_TITLES, "error_locations"), f"{ticks}text\n{error_location_context}\n{ticks}"),
            PromptSection(_prompt_title(prompt_constants.REPAIR_USER_SECTION_TITLES, "compiler_guide"), compiler_repair_guide),
            PromptSection(_prompt_title(prompt_constants.REPAIR_USER_SECTION_TITLES, "generated_diagnostics"), _meaningful_context(generated_test_diagnostics)),
            PromptSection(_prompt_title(prompt_constants.REPAIR_USER_SECTION_TITLES, "source_strategy"), source_specific_strategy),
            PromptSection(_prompt_title(prompt_constants.REPAIR_USER_SECTION_TITLES, "nearby_patterns"), _meaningful_context(nearby_test_patterns)),
            PromptSection(_prompt_title(prompt_constants.REPAIR_USER_SECTION_TITLES, "verified_context"), f"{ticks}text\n{verified_context}\n{ticks}" if verified_context else ""),
            PromptSection(_prompt_title(prompt_constants.REPAIR_USER_SECTION_TITLES, "source_risks"), f"{ticks}text\n{repair_source_risk_context}\n{ticks}" if repair_source_risk_context else ""),
            PromptSection(_prompt_title(prompt_constants.REPAIR_USER_SECTION_TITLES, "current_test"), f"{ticks}kotlin\n{current_test_code}\n{ticks}"),
            PromptSection(_prompt_title(prompt_constants.REPAIR_USER_SECTION_TITLES, "source_code"), f"{ticks}kotlin\n{source_code}\n{ticks}"),
            PromptSection("", memory_context),
            PromptSection("", final_rule_tail),
            ],
        )
    )
    repair_prompt = prompt_bundle.user_prompt

    log_section(f"FULL-FILE REPAIR REQUEST: {group_key}", category="fix")
    log_block("FULL-FILE REPAIR PROMPT", repair_prompt, category="fix", console=False)
    print_prompt_to_terminal(
        title=f"FINAL FULL-FILE REPAIR PROMPT SENT TO MODEL: {class_name}",
        system_prompt=prompt_bundle.system_prompt,
        user_prompt=repair_prompt,
    )
    log_message("\n🔧 Streaming focused repair output:\n", category="fix")

    raw_output = stream_chat_completion(
        messages=list(prompt_bundle.messages),
        temperature=0.1,
        extra_body={
            "thinking_budget_tokens": 4096,
            "presence_penalty": 0.1,
            "repeat_penalty": 1.1,
        },
        
    )

    return extract_kotlin_code(raw_output)
