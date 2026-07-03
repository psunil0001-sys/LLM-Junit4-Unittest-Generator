# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Validates generated Fragment test fixtures.
"""Fragment-specific validation rules."""

from __future__ import annotations

import re

from UnitTest_gen.kotlin.kotlin_analysis import source_declares_android_fragment
from UnitTest_gen.kotlin.strategy_contracts import validation_repair_intent


def _fragment_fixture_initialized(test_code: str) -> bool:
    """True when a class-level lateinit fragment is assigned in @Before or tests use local val fragment."""
    if not re.search(r"\blateinit\s+var\s+fragment\b", test_code):
        return True
    if re.search(r"@Test[\s\S]*?\bval\s+fragment\s*=", test_code):
        return True
    if re.search(r"@Before\b[\s\S]*?\bfragment\s*=", test_code):
        return True
    if len(re.findall(r"\bfragment\b", test_code)) == 1:
        return True
    return False


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


def collect_validation_issues(test_code: str, output_file_path: str, source_code: str, test_report) -> list[str]:
    """Hilt fragment host strategy and navigation ordering checks."""
    issues: list[str] = []

    source_is_hilt_fragment = (
        "@AndroidEntryPoint" in (source_code or "")
        and source_declares_android_fragment(source_code or "")
    )
    attaches_fragment_lifecycle = bool(
        "supportFragmentManager" in test_code
        or ".commitNow()" in test_code
        or ".commitNowAllowingStateLoss()" in test_code
        or "fragment.requireView()" in test_code
        or "Navigation.setViewNavController" in test_code
        or test_report.tests.direct_lifecycle_call_names
    )
    if source_is_hilt_fragment and attaches_fragment_lifecycle:
        if test_report.tests.has_local_hilt_test_activity:
            issues.append(
                "invalid_host_strategy: " + validation_repair_intent("invalid_host_strategy")
            )
        missing_hilt_parts = []
        if "@HiltAndroidTest" not in test_code:
            missing_hilt_parts.append("@HiltAndroidTest")
        if "HiltAndroidRule" not in test_code:
            missing_hilt_parts.append("HiltAndroidRule")
        if "HiltTestApplication" not in test_code:
            missing_hilt_parts.append("@Config(application = HiltTestApplication::class)")
        if missing_hilt_parts:
            issues.append(
                "invalid_hilt_lifecycle_setup: attached @AndroidEntryPoint Fragment tests require "
                + ", ".join(missing_hilt_parts)
                + ". "
                + validation_repair_intent("invalid_hilt_lifecycle_setup")
            )
        if "getDeclaredField(" in test_code or "isAccessible = true" in test_code:
            issues.append(
                "invalid_hilt_manual_injection: " + validation_repair_intent("invalid_hilt_manual_injection")
            )
        if (
            "Robolectric.buildActivity(" in test_code
            and "supportFragmentManager" in test_code
            and not re.search(r"Robolectric\.buildActivity\([^)]*\)\.create\(\)", test_code)
            and not re.search(
                r"\bactivityController\s*=\s*Robolectric\.buildActivity\([^)]*\)[\s\S]{0,350}\bactivityController\.create\(\)",
                test_code,
            )
        ):
            issues.append(
                "invalid_activity_controller_order: " + validation_repair_intent("invalid_activity_controller_order")
            )

    if (
        "@AndroidEntryPoint" in (source_code or "")
        and "Robolectric.buildActivity(HiltTestActivity::class.java)" in test_code
        and re.search(r"Robolectric\.buildActivity\(HiltTestActivity::class\.java\)\.create\(\)\.resume\(", test_code)
        and ".setTheme(" not in test_code
    ):
        issues.append(
            "Robolectric HiltTestActivity setup should set an AppCompat theme before resume. "
            "Use `activityController = Robolectric.buildActivity(HiltTestActivity::class.java).create(); "
            "activityController.get().setTheme(androidx.appcompat.R.style.Theme_AppCompat); attach the Fragment; "
            "activityController.start(); install any NavController; activityController.resume()`."
        )

    if (
        "@AndroidEntryPoint" in (source_code or "")
        and "findNavController" in (source_code or "")
        and "Robolectric.buildActivity(HiltTestActivity::class.java)" in test_code
        and "Navigation.setViewNavController" in test_code
    ):
        first_resume = test_code.find(".resume()")
        nav_install = test_code.find("Navigation.setViewNavController")
        if first_resume != -1 and nav_install != -1 and first_resume < nav_install:
            issues.append(
                "Robolectric Hilt Fragment navigation tests should install TestNavHostController before resuming the host. "
                "Use order: create ActivityController, set Theme_AppCompat, attach Fragment with commitNow(), "
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
            "onViewCreated" in source_code
            and re.search(r"\.observe\s*\(\s*viewLifecycleOwner", source_code)
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


def collect_lifecycle_validation_issues(
    test_code: str, output_file_path: str, source_code: str, test_report
) -> list[str]:
    """Fragment lifecycle attachment and Hilt host checks."""
    issues: list[str] = []

    is_android_entrypoint_fragment = (
        "@AndroidEntryPoint" in (source_code or "") and source_declares_android_fragment(source_code or "")
    )
    fragment_lifecycle_attach = re.search(
        r"supportFragmentManager\s*\.\s*beginTransaction\(\).*?\.add\(\s*[A-Za-z_][A-Za-z0-9_]*\s*,",
        test_code,
        flags=re.DOTALL,
    )
    fragment_direct_lifecycle_call = re.search(r"\.\s*on(?:CreateView|ViewCreated)\s*\(", test_code)

    if is_android_entrypoint_fragment and (fragment_lifecycle_attach or fragment_direct_lifecycle_call):
        from UnitTest_gen.kotlin.project_context import module_has_hilt_robolectric_fragment_support

        full_hilt_supported, missing_hilt_support = module_has_hilt_robolectric_fragment_support(
            output_file_path,
            source_code,
        )
        has_hilt_test_setup = (
            "@HiltAndroidTest" in test_code
            and "HiltAndroidRule" in test_code
            and "HiltTestApplication" in test_code
        )
        if fragment_direct_lifecycle_call:
            issues.append(
                "@AndroidEntryPoint fragment lifecycle tests should launch a verified Hilt host activity and attach the fragment with supportFragmentManager.commitNow(). "
                "Use full Hilt/Robolectric attachment when supported, or generate direct public-contract tests with observable assertions."
            )
        if not full_hilt_supported:
            issues.append(
                "@AndroidEntryPoint fragment lifecycle testing is not supported by the owning module Gradle setup. "
                f"Missing prerequisites: {', '.join(missing_hilt_support) if missing_hilt_support else 'unknown'}. "
                "Use direct public-contract tests with observable assertions or add the missing dependencies first."
            )
        elif not has_hilt_test_setup:
            issues.append(
                "@AndroidEntryPoint fragments cannot be attached to a plain Robolectric FragmentActivity/Application. "
                "Use @HiltAndroidTest + HiltAndroidRule + @Config(application = HiltTestApplication::class). "
                "When full Hilt setup is unavailable, test only public logic that does not trigger Hilt onAttach injection."
            )
        if (
            "findNavController" in (source_code or "")
            and "NavDeepLinkRequest" not in (source_code or "")
            and "TestNavHostController" not in test_code
        ):
            issues.append(
                "Fragment lifecycle tests for source that calls findNavController() should install a real TestNavHostController "
                "on the fragment view after attachment with Navigation.setViewNavController. "
                "For Robolectric ActivityController hosts, call activityController.start() after commitNow() before fragment.requireView()."
            )
        if "HiltViewModel" in (source_code or "") or "activityViewModels" in (source_code or ""):
            has_hilt_test_bindings = "@BindValue" in test_code or "@TestInstallIn" in test_code
            if not has_hilt_test_bindings:
                issues.append(
                    "hilt_graph_unverified: attached @AndroidEntryPoint Fragment lifecycle tests with Hilt ViewModels "
                    "require a verified closed graph. Do not invent bindings; reduce to non-attached public-contract "
                    "tests or stop with the dependency/binding requirement."
                )
            if re.search(r"@BindValue\s+lateinit\s+var\s+\w+\s*:\s*\w*ViewModel\b", test_code):
                issues.append(
                    "@BindValue ViewModel fields do not replace a Fragment property obtained through viewModels()/activityViewModels(). "
                    "The delegate uses ViewModelProvider/Hilt factory. Use the real delegated ViewModel and drive public state methods, "
                    "or satisfy the Hilt graph that creates the ViewModel."
                )
        if re.search(r"\b(getPrivate|readPrivate|binding)\s*<[^>]*Binding\b|Fragment[A-Za-z0-9_]*Binding", test_code):
            issues.append(
                "@AndroidEntryPoint Fragment tests should assert visible public view behavior from the attached root view. "
                "Do not access private binding fields or generated binding classes from the test."
            )
    if "@AndroidEntryPoint" in (source_code or "") and "EmptyRobolectricApplication" in test_code:
        issues.append(
            "EmptyRobolectricApplication does not satisfy Hilt GeneratedComponent requirements for @AndroidEntryPoint fragments. "
            "Use HiltTestApplication with HiltAndroidRule, or avoid attaching the fragment lifecycle."
        )

    if is_android_entrypoint_fragment and re.search(r"whenever\s*\([^)]*findNavController\s*\(", test_code):
        if "NavDeepLinkRequest" in (source_code or ""):
            issues.append(
                "invalid_nav_deeplink_fixture: " + validation_repair_intent("invalid_nav_deeplink_fixture")
            )
        else:
            issues.append(
                "Mockito cannot stub the AndroidX fragment.findNavController() extension on a real Fragment. "
                "Use TestNavHostController with Navigation.setViewNavController after real fragment attachment. "
                "For Robolectric ActivityController hosts, call activityController.start() after commitNow() before fragment.requireView()."
            )

    if is_android_entrypoint_fragment:
        if re.search(r"\blaunchFragmentInContainer\s*<|\bFragmentScenario\.launchInContainer\b", test_code):
            issues.append(
                "@AndroidEntryPoint fragment lifecycle tests should not use plain launchFragmentInContainer/FragmentScenario.launchInContainer. "
                "Those hosts use EmptyFragmentActivity rather than an @AndroidEntryPoint host activity. "
                "Launch/create a verified Hilt host activity with ActivityScenario/Robolectric, create the fragment manually, attach it with supportFragmentManager.commitNow(), "
                "call activityController.start() before fragment.requireView() when using Robolectric ActivityController, then install TestNavHostController after attachment."
            )

        if re.search(r"\bactivityClass\s*=", test_code):
            issues.append(
                "The generated test is trying to pass `activityClass = ...` into launchFragmentInContainer/FragmentScenario, but that is not a verified local API here. "
                "Replace this with the module's manifest-declared HiltTestActivity and the actual fragment class under test. "
                "For Robolectric, use: val activityController = Robolectric.buildActivity(HiltTestActivity::class.java).create(); "
                "activityController.get().setTheme(androidx.appcompat.R.style.Theme_AppCompat); attach TargetFragment with commitNow(); "
                "activityController.start(); install any NavController; then activityController.resume()."
            )

        if re.search(r"\bgetDeclaredMethod\s*\(", test_code) or "fragment::class.java" in test_code:
            issues.append(
                "invalid_hilt_manual_injection: " + validation_repair_intent("invalid_hilt_manual_injection")
            )
        if re.search(r"\.\s*on(?:CreateView|ViewCreated)\s*\(", test_code) and (
            ".commitNow()" in test_code
            or "supportFragmentManager" in test_code
            or "Robolectric.buildActivity" in test_code
        ):
            issues.append(
                "invalid_fragment_lifecycle_reentry: commitNow()/FragmentManager attachment already invokes Fragment lifecycle. "
                "Do not call onCreateView/onViewCreated manually after attachment; create a fresh Fragment with arguments before attach "
                "and drive only public UI/callback behavior."
            )
        if _sets_fragment_arguments_after_attach(test_code):
            issues.append(
                "invalid_fragment_arguments_after_attach: Fragment arguments must be set before FragmentManager commit/attach. "
                "Use a fixture helper that creates a fresh Fragment, assigns arguments, attaches once, installs navigation, "
                "and then starts/resumes the host."
            )
        if re.search(r"\blateinit\s+var\s+fragment\b", test_code) and not _fragment_fixture_initialized(test_code):
            issues.append(
                "The generated test declares a lateinit fragment fixture without deterministic initialization. "
                "Initialize it in @Before before every test, use local val fragment inside @Test methods, "
                "or remove tests that rely on it."
            )
        if (
            "activityViewModels" in source_code
            and re.search(r"\bviewModel\.[A-Za-z0-9_]+\.postValue\s*\(", test_code)
            and not re.search(r"supportFragmentManager|FragmentScenario|launchFragmentInHiltContainer", test_code)
        ):
            issues.append(
                "Posting to a mocked/standalone activityViewModels ViewModel without an attached fragment does not exercise fragment observers. "
                "Use verified Hilt lifecycle attachment, or replace the observer scenario with a valid public-contract test."
            )

    return issues
