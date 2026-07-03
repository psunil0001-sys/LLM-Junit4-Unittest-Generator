# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Canonical guardrail rule text shared by prompts, contracts, and validators.
"""Single source of truth for guardrail prompt bullets and validation repair intents.

Themes (~12 groups):
  core, mockito, robolectric_sdk, activity_buildconfig, fragment_hilt, viewmodel_coroutine,
  navigation, carui, apollo, appauth, resources_imports, static_singleton,
  incremental_orchestration, coverage_strategy

Orchestration-only codes (planner/Kover gates, no static validator):
  missing_coverage_trigger_click, missing_coverage_trigger_observer_before_click,
  missing_coverage_trigger_observer, missing_coverage_trigger_dialog,
  missing_coverage_trigger_coroutine, missing_coverage_trigger_menu,
  missing_coverage_trigger_activity_result, missing_coverage_trigger_exception,
  missing_coverage_trigger_countdown, missing_coverage_observation_toolbar,
  invalid_inject_field_shallow_coverage, invalid_fragment_fixture
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GuardrailRule:
    id: str
    theme: str
    generation_bullets: tuple[str, ...]
    repair_bullets: tuple[str, ...]
    validator_codes: frozenset[str]
    prompt_targets: frozenset[str]


ORCHESTRATION_ONLY_CODES = frozenset(
    {
        "missing_coverage_trigger_click",
        "missing_coverage_trigger_observer_before_click",
        "missing_coverage_trigger_observer",
        "missing_coverage_trigger_coroutine",
        "missing_coverage_trigger_menu",
        "missing_coverage_trigger_activity_result",
        "missing_coverage_trigger_exception",
        "missing_coverage_trigger_countdown",
        "missing_coverage_observation_toolbar",
        "invalid_inject_field_shallow_coverage",
        "invalid_fragment_fixture",
        "duplicate_incremental_candidate",
    }
)


GUARDRAIL_RULES: tuple[GuardrailRule, ...] = (
    GuardrailRule(
        id="core_junit_package",
        theme="core",
        generation_bullets=(
            "Start every generated test file with package <same_as_source_under_test> as the first statement, before imports.",
            "Test file package declaration must match the target test directory path.",
            "Use JVM-safe Kotlin backtick test names; do not embed dots inside backtick names.",
            "Keep @Test methods public or internal; exactly one parameter list per test function.",
        ),
        repair_bullets=(
            "Add the package declaration matching the test file directory.",
            "Rename backtick test methods to JVM-safe camelCase words without embedded dots.",
        ),
        validator_codes=frozenset(),
        prompt_targets=frozenset({"core"}),
    ),
    GuardrailRule(
        id="mockito_matchers",
        theme="mockito",
        generation_bullets=(
            "Use typed Mockito-Kotlin matchers (any<Int>(), any<Boolean>(), eq(...)) on overload-sensitive calls; do not import or call primitive helpers such as anyInt().",
            "Do not nest stubbing through mock.inner.property without stubbing the intermediate property first.",
            "Verify method calls or resulting public behavior; do not use Mockito property-assignment verification.",
            "Kotlin tests using whenever() must import org.mockito.kotlin.whenever.",
            "For Unit/void methods use doThrow/doAnswer(...).`when`(mock).method(...); use whenever only for value-returning calls.",
            "Do not stub Kotlin object/framework helper calls with whenever(Object.method(any())) when matchers invoke real non-null Kotlin parameters.",
            "Static mocking uses org.mockito.Mockito.mockStatic(Type::class.java), not MockedStatic.mockStatic(...).",
            "This project uses Mockito-Kotlin from the pinned test stack; do not generate io.mockk APIs.",
        ),
        repair_bullets=(
            "Use typed Mockito-Kotlin primitive matchers such as any<Int>(), any<Boolean>(), or any<Long>(); do not import or call primitive helper matchers such as anyInt().",
            "Verify method calls or resulting public behavior; do not use Mockito property-assignment verification.",
            "Generated tests must use Mockito-Kotlin from the pinned test stack; do not generate io.mockk APIs.",
            "Use org.mockito.Mockito.mockStatic(Type::class.java), not MockedStatic.mockStatic(...).",
            "Add explicit MockedStatic.when return type: mockedStatic.`when`<ReturnType> { Type.staticCall(...) }.",
        ),
        validator_codes=frozenset(
            {
                "invalid_property_assignment_verification",
                "invalid_mockedstatic_missing_return_type",
                "invalid_mockedstatic_factory",
                "nonstandard_mock_framework",
            }
        ),
        prompt_targets=frozenset({"kotlin_android", "repair"}),
    ),
    GuardrailRule(
        id="robolectric_sdk",
        theme="robolectric_sdk",
        generation_bullets=(
            "Use @Config(sdk = [...]) only for verified SDK-specific branches, with SDK >= module minSdk and within compileSdk bounds.",
            "Do not call suspend functions inside @Before; wrap suspend work in runTest inside @Test methods.",
            "This project uses Robolectric 4.x; use org.robolectric.shadows.ShadowAlertDialog and org.robolectric.shadows.ShadowLooper for dialog/looper work.",
            "For activeNetwork/getNetworkCapabilities code, simulate no network with shadowOf(connectivityManager).setDefaultNetworkActive(false); do not use setActiveNetworkInfo(null) or obsolete ShadowNetworkInfo.newInstance overloads.",
            "Robolectric Drawable ConstantState does not expose a stable resourceId; assert stable UI structure/state instead.",
        ),
        repair_bullets=(
            "This project uses Robolectric 4.x; replace removed 3.x APIs with org.robolectric.shadows.ShadowAlertDialog and org.robolectric.shadows.ShadowLooper.",
            "Replace setActiveNetworkInfo(null) with setDefaultNetworkActive(false) when production reads activeNetwork/getNetworkCapabilities; do not guess ShadowNetworkInfo factory signatures.",
            "Align @Config(sdk = [...]) with module minSdk/compileSdk or omit @Config when not required.",
        ),
        validator_codes=frozenset({"deprecated_robolectric_api"}),
        prompt_targets=frozenset({"kotlin_android", "repair"}),
    ),
    GuardrailRule(
        id="activity_buildconfig",
        theme="activity_buildconfig",
        generation_bullets=(
            "Drive Activity lifecycle only through Robolectric ActivityController; never call protected onCreate/onStart/onDestroy directly.",
            "BuildConfig flags are fixed by the active Gradle variant; test only the active branch unless a production seam exists.",
            "Do not reflect or replace Hilt-injected or private Activity fields; cover public lifecycle/UI behavior or report the missing seam.",
        ),
        repair_bullets=(
            "Test only the active BuildConfig branch; an opposite branch requires a production seam or different Gradle variant.",
            "Do not reflect or replace Hilt-injected/private Activity fields; cover public lifecycle/UI behavior or report the missing seam.",
            "BuildConfig flags are fixed by the active Gradle variant; do not generate contradictory true/false tests.",
        ),
        validator_codes=frozenset(
            {
                "invalid_fixed_buildconfig_branch",
                "invalid_hilt_activity_reflection",
                "invalid_buildconfig_assertion",
                "invalid_trivial_assertion",
                "invalid_direct_activity_lifecycle_call",
            }
        ),
        prompt_targets=frozenset({"kotlin_android", "repair", "blueprint_fragment"}),
    ),
    GuardrailRule(
        id="fragment_hilt",
        theme="fragment_hilt",
        generation_bullets=(
            "Attached @AndroidEntryPoint Fragment tests use manifest-declared HiltTestActivity; do not declare local/fake hosts.",
            "ActivityController order: create -> stub CarUi -> set Fragment arguments -> commitNow -> start -> install NavController -> resume.",
            "Do not reflect, manually assign, or @BindValue injected Fragment fields; close the graph or reduce to non-attached coverage.",
            "Per-file tests must not mutate the global Hilt graph; reuse the owning module's shared test Hilt binding for duplicate keys.",
            "When merging into an existing Fragment test class, prefer local `val fragment = ...` inside each @Test when nearby tests use that pattern.",
            "commitNow()/FragmentManager attachment already invokes Fragment lifecycle; do not call onCreateView/onViewCreated manually afterward.",
            "Set Fragment arguments before FragmentManager commit/attach.",
        ),
        repair_bullets=(
            "Import the manifest-declared HiltTestActivity from the owning module; do not declare a local/fake host.",
            "Use complete Hilt test setup for attached @AndroidEntryPoint Fragment lifecycle tests, or reduce to non-attached public-contract coverage.",
            "Remove reflection/manual field injection from attached Hilt Fragment tests; close the graph or reduce strategy.",
            "Create the ActivityController before reading Activity/supportFragmentManager, then attach/start/install navigation/resume in order.",
            "Per-file tests must not mutate the global Hilt graph; stop or use a shared verified test module outside this generator.",
            "Remove the per-file binding and use the owning module's shared test Hilt binding for this key.",
        ),
        validator_codes=frozenset(
            {
                "invalid_host_strategy",
                "invalid_hilt_lifecycle_setup",
                "invalid_hilt_manual_injection",
                "invalid_activity_controller_order",
                "invalid_global_hilt_graph_mutation",
                "duplicate_shared_hilt_test_binding",
                "invalid_stateflow_reflection",
                "invalid_delegated_viewmodel_provider",
                "invalid_fragment_detached_lifecycle_probe",
            }
        ),
        prompt_targets=frozenset({"kotlin_android", "repair", "blueprint_fragment", "incremental"}),
    ),
    GuardrailRule(
        id="viewmodel_coroutine",
        theme="viewmodel_coroutine",
        generation_bullets=(
            "Construct direct-constructor ViewModels first, then assign @Inject lateinit collaborators with direct statements before invoking methods.",
            "Do not assign injected lateinit collaborators inside apply/also/with/chained receiver scopes.",
            "When production launches on Dispatchers.IO without an injectable seam, do not assert final coroutine completion state.",
            "For viewModelScope/lifecycleScope on Main, install MainDispatcherRule and use runTest { advanceUntilIdle() }.",
        ),
        repair_bullets=(
            "Construct the ViewModel first, then assign injected lateinit collaborators with direct statements.",
            "Avoid final-state assertions for hard-coded Dispatchers.IO work without a dispatcher seam.",
        ),
        validator_codes=frozenset(
            {
                "invalid_lateinit_injection_setup_order",
                "invalid_hardcoded_dispatcher_final_state_assertion",
            }
        ),
        prompt_targets=frozenset({"kotlin_android", "repair", "incremental"}),
    ),
    GuardrailRule(
        id="navigation",
        theme="navigation",
        generation_bullets=(
            "Cross-graph NavDeepLinkRequest / URI capture: install a mocked NavController on the fragment view and verify/capture the emitted URI.",
            "Same-graph plain navigation: use TestNavHostController with an installed graph via Navigation.setViewNavController.",
            "Do not stub NavController.navigate(NavDeepLinkRequest) with Mockito.when; trigger the event and verify(navController).navigate(captor.capture()).",
            "Do not stub findNavController(); install a controller on the real Fragment view.",
            "For NavDeepLinkRequest verification use org.mockito.kotlin.argumentCaptor<NavDeepLinkRequest>(), not Java ArgumentCaptor.forClass(...).capture().",
        ),
        repair_bullets=(
            "For cross-graph NavDeepLinkRequest behavior, install a mocked NavController and capture the URI unless the exact graph is installed.",
            "Do not stub NavController.navigate(NavDeepLinkRequest) with Mockito.when/thenAnswer; trigger the event and verify with a Kotlin captor.",
            "Use org.mockito.kotlin.argumentCaptor<NavDeepLinkRequest>() instead of Java ArgumentCaptor.forClass(...).capture().",
        ),
        validator_codes=frozenset(
            {
                "invalid_nav_deeplink_fixture",
                "invalid_void_navigation_stubbing",
                "invalid_nav_deeplink_java_argumentcaptor",
            }
        ),
        prompt_targets=frozenset({"kotlin_android", "repair", "blueprint_navigation", "incremental"}),
    ),
    GuardrailRule(
        id="carui",
        theme="carui",
        generation_bullets=(
            "Stub CarUi.requireToolbar with the exact host Activity instance and typed ToolbarController/ProgressBarController mocks.",
            "If source reads toolbar.progressBar, stub progressBar before attach/resume and verify the ProgressBarController mock directly.",
            "CarUI ProgressBarController uses setIndeterminate(boolean), not setIsIndeterminate.",
            "For ToolbarController.registerBackListener, use the verified Java callback shape from the CarUi API; do not guess a Kotlin () -> Boolean callback.",
        ),
        repair_bullets=(
            "Stub CarUi.requireToolbar with the exact host Activity and typed ToolbarController/ProgressBarController mocks.",
            "Use the verified CarUi back-listener callback shape; do not capture or stub it as a guessed Kotlin () -> Boolean callback.",
            "CarUI ProgressBarController uses setIndeterminate(boolean), not setIsIndeterminate; or assert public UI behavior instead.",
        ),
        validator_codes=frozenset(
            {
                "invalid_carui_static_fixture",
                "invalid_carui_progress_fixture",
                "invalid_carui_progress_verification",
                "invalid_carui_back_listener_fixture",
            }
        ),
        prompt_targets=frozenset({"kotlin_android", "repair", "blueprint_fragment", "incremental"}),
    ),
    GuardrailRule(
        id="appauth",
        theme="appauth",
        generation_bullets=(
            "AppAuth refreshToken success paths call AuthState.update(TokenResponse, null); verify with verify(mock).update(any<TokenResponse>(), isNull()), not AuthorizationResponse.",
            "Use typed matchers for AuthState.update overloads; untyped any() causes overload ambiguity.",
            "Build real AppAuth request/config/token value objects; do not stub Kotlin property getters on AppAuth types.",
            "Do not use AuthState.Builder or reflect idToken; inject a mock AuthState and stub public getters for authorized branches.",
        ),
        repair_bullets=(
            "AppAuth refreshToken calls AuthState.update(TokenResponse, AuthorizationException?); use verify(mockAuthState).update(any<TokenResponse>(), isNull()), not AuthorizationResponse.",
            "Use typed matchers for AuthState.update overloads (TokenResponse for refresh, AuthorizationResponse for login exchange).",
        ),
        validator_codes=frozenset(),
        prompt_targets=frozenset({"kotlin_android", "incremental"}),
    ),
    GuardrailRule(
        id="static_singleton",
        theme="static_singleton",
        generation_bullets=(
            "MockedStatic only for verified Java static or @JvmStatic APIs; close in tearDown after exercised code completes.",
            "Do not reflect singleton internals (INSTANCE, instance fields); use verified public behavior or MockedStatic for Java static APIs.",
            "AlertDialogHelper.getInstance is a plain Kotlin companion method; do not mockStatic it—drive onUserAction or Robolectric dialog buttons.",
            "Do not close MockedStatic inside @Before before @Test methods run.",
        ),
        repair_bullets=(
            "Do not reflect singleton internals; use verified public behavior, or a MockedStatic<Type> only for Java static/@JvmStatic APIs.",
            "AlertDialogHelper.getInstance is a plain Kotlin companion method, not a verified Java static/@JvmStatic API; do not mockStatic it.",
        ),
        validator_codes=frozenset(
            {
                "invalid_singleton_reflection",
                "invalid_alertdialoghelper_static_mock",
            }
        ),
        prompt_targets=frozenset({"kotlin_android", "repair", "incremental"}),
    ),
    GuardrailRule(
        id="resources_imports",
        theme="resources_imports",
        generation_bullets=(
            "Use verified module R.id or binding.<field>; never androidx.appcompat.R for feature layout ids.",
            "Verify Android resources against dependency context; do not invent R.id or layout names.",
        ),
        repair_bullets=(
            "Replace unverified Android resource references with verified module R.id or binding fields from dependency context.",
        ),
        validator_codes=frozenset({"unverified_android_resource"}),
        prompt_targets=frozenset({"kotlin_android", "incremental"}),
    ),
    GuardrailRule(
        id="coverage_strategy",
        theme="coverage_strategy",
        generation_bullets=(
            "Blocked coverage opportunities (private-only, no dispatcher seam, unsupported static/SDK, unverified exception fixtures) must not be sent as generation targets.",
            "For blocked opportunities, never generate tests; emit a compact seam recommendation only.",
            "Selected executable targets must execute at least one listed missed line, branch, or method; calling a public method alone is insufficient.",
            "Selected executable targets must run through the target class public entry path; bypassing via delegated singleton helpers does not count as covering source lines inside the target method.",
            "Static SDK/global platform calls are coverage seams unless verified context proves mockable deterministic local JVM execution.",
            "Do not invent SDK constructors, static wrappers, or final-state assertions for blocked static paths.",
        ),
        repair_bullets=(
            "Stop generation for blocked paths and report the seam requirement instead of no-op or method-entry tests.",
        ),
        validator_codes=frozenset(),
        prompt_targets=frozenset({"coverage_strategy", "incremental"}),
    ),
    GuardrailRule(
        id="incremental_orchestration",
        theme="incremental_orchestration",
        generation_bullets=(
            "Generate tests ONLY for selected targets from COVERAGE OPPORTUNITY PLAN; do not target alternatives or blocked items.",
            "Follow fixture playbooks step-by-step; static validators reject skipped fixture steps.",
            "Do not regenerate tests semantically identical to existing bodies when Kover still shows the same branch.",
            "For branch_probe when the missed line is a when-case arm, drive that exact case label; never substitute fall-through advice.",
            "For cast-safe-call branches (e.g. `(activity as? Activity)?.let`), drive a non-null receiver through verified public setup such as attach/resume, not a null-receiver recipe.",
            "Cover missed lines through the target class public entry path (UI click, menu action, lifecycle host, public method); do not call delegated singleton helpers directly from the test.",
            "Dialog/callback gaps require triggering the real callback through the target class path, then ShadowAlertDialog or verified callback storage; helper-only calls do not cover source lines inside the target method.",
            "Fragment lifecycle tests: after activityController.stop() or onStop(), expect Lifecycle.State.CREATED (or lower), not STARTED/RESUMED.",
        ),
        repair_bullets=(
            "Replay the required playbook steps in order before changing assertions.",
            "Route dialog/callback coverage through the target class public entry; do not invoke delegated getInstance() helpers alone.",
            "After stop/onStop lifecycle transitions, assert CREATED (or destroyed) lifecycle state, not STARTED or RESUMED.",
        ),
        validator_codes=frozenset(
            {
                "duplicate_incremental_candidate",
                "missing_coverage_trigger_dialog",
                "missing_coverage_trigger_click",
                "missing_coverage_trigger_menu",
                "missing_coverage_trigger_observer",
                "missing_coverage_trigger_observer_before_click",
                "missing_coverage_trigger_exception",
                "missing_coverage_trigger_countdown",
                "invalid_fragment_lifecycle_state_assertion",
            }
        ),
        prompt_targets=frozenset({"incremental", "playbook_orchestration"}),
    ),
    GuardrailRule(
        id="apollo_reflection",
        theme="apollo",
        generation_bullets=(
            "If source uses dataAssertNoErrors(), ApolloResponse data = null throws ApolloException before nullable mapper safe-calls.",
            "Use mockApollo reflection helpers for unverified generated Apollo types; stub getters through verified method names only.",
        ),
        repair_bullets=(
            "For Apollo dataAssertNoErrors failures, treat data = null as a thrown ApolloException unless a verified non-null data fixture exists.",
        ),
        validator_codes=frozenset(),
        prompt_targets=frozenset({"blueprint_apollo", "repair"}),
    ),
)


def _bullets_for_targets(targets: frozenset[str] | set[str], kind: str) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    target_set = set(targets)
    for rule in GUARDRAIL_RULES:
        if not (rule.prompt_targets & target_set):
            continue
        source = rule.generation_bullets if kind == "generation" else rule.repair_bullets
        for bullet in source:
            if bullet not in seen:
                seen.add(bullet)
                ordered.append(bullet)
    return ordered


def render_guardrails(*targets: str, kind: str = "generation") -> str:
    """Render deduplicated guardrail bullets for prompt_targets."""
    if not targets:
        return ""
    bullets = _bullets_for_targets(set(targets), kind)
    if not bullets:
        return ""
    return "\n".join(f"    - {bullet}" for bullet in bullets)


def _build_repair_intents() -> dict[str, str]:
    intents: dict[str, str] = {}
    for rule in GUARDRAIL_RULES:
        if not rule.validator_codes:
            continue
        repair_text = rule.repair_bullets[0] if rule.repair_bullets else ""
        for code in rule.validator_codes:
            if code not in intents and repair_text:
                intents[code] = repair_text
    # Explicit overrides where one code needs a specific message
    overrides = {
        "missing_coverage_trigger_menu": (
            "Capture the exact typed menu listener, invoke it once, and assert the resulting public behavior."
        ),
        "missing_coverage_trigger_activity_result": (
            "Dispatch a concrete ActivityResult through the verified registry/helper callback; do not reflect launcher fields."
        ),
        "missing_coverage_trigger_exception": (
            "Configure a verified collaborator with thenThrow/doThrow before invoking the public entry and assert its fallback."
        ),
        "missing_coverage_trigger_countdown": (
            "Advance the verified looper/clock or invoke the captured onFinish callback before asserting final state."
        ),
        "invalid_global_hilt_graph_mutation": (
            "Per-file tests must not mutate the global Hilt graph; stop or use a shared verified test module outside this generator."
        ),
        "duplicate_shared_hilt_test_binding": (
            "Remove the per-file binding and use the owning module's shared test Hilt binding for this key."
        ),
        "invalid_host_strategy": (
            "Import the manifest-declared HiltTestActivity from the owning module; do not declare a local/fake host."
        ),
        "invalid_hilt_lifecycle_setup": (
            "Use complete Hilt test setup for attached @AndroidEntryPoint Fragment lifecycle tests, "
            "or reduce to non-attached public-contract coverage."
        ),
        "invalid_hilt_manual_injection": (
            "Remove reflection/manual field injection from attached Hilt Fragment tests; close the graph or reduce strategy."
        ),
        "invalid_activity_controller_order": (
            "Create the ActivityController before reading Activity/supportFragmentManager, "
            "then attach/start/install navigation/resume in order."
        ),
        "invalid_carui_static_fixture": (
            "Stub CarUi.requireToolbar with the exact host Activity and typed ToolbarController/ProgressBarController mocks."
        ),
        "invalid_carui_back_listener_fixture": (
            "Use the verified CarUi back-listener callback shape; "
            "do not capture or stub it as a guessed Kotlin () -> Boolean callback."
        ),
        "invalid_nav_deeplink_fixture": (
            "Replace TestNavHostController(...) with navController = mock(); after commitNow/start call "
            "Navigation.setViewNavController(fragment.requireView(), navController); verify navigation with "
            "argumentCaptor<NavDeepLinkRequest>(). Use TestNavHostController only when .setGraph(...) installs the exact destination graph."
        ),
        "invalid_singleton_reflection": (
            "Do not reflect singleton internals; use verified public behavior, "
            "or a MockedStatic<Type> only for Java static/@JvmStatic APIs."
        ),
        "invalid_mockedstatic_missing_return_type": (
            "Add the explicit MockedStatic.when return type: mockedStatic.`when`<ReturnType> { Type.staticCall(...) }."
        ),
        "invalid_alertdialoghelper_static_mock": (
            "AlertDialogHelper.getInstance is a plain Kotlin companion method, not a verified Java static/@JvmStatic API; "
            "do not mockStatic it."
        ),
        "missing_coverage_trigger_dialog": (
            "Trigger dialog/callback coverage through the target class public entry path "
            "(toolbar/menu click, performClick, lifecycle host); do not call delegated getInstance() helpers directly from the test."
        ),
        "invalid_fragment_lifecycle_state_assertion": (
            "After activityController.stop() or onStop(), Fragment lifecycle is CREATED or lower; "
            "do not assert STARTED or RESUMED."
        ),
        "invalid_property_assignment_verification": (
            "Verify method calls or resulting public behavior; do not use Mockito property-assignment verification."
        ),
        "invalid_lateinit_injection_setup_order": (
            "Construct the ViewModel first, then assign injected lateinit collaborators with direct statements."
        ),
        "invalid_hardcoded_dispatcher_final_state_assertion": (
            "Avoid final-state assertions for hard-coded Dispatchers.IO work without a dispatcher seam."
        ),
        "invalid_hilt_activity_reflection": (
            "Do not reflect or replace Hilt-injected/private Activity fields; "
            "cover public lifecycle/UI behavior or report the missing seam."
        ),
        "invalid_fixed_buildconfig_branch": (
            "Test only the active BuildConfig branch; an opposite branch requires a production seam or different Gradle variant."
        ),
        "deprecated_robolectric_api": (
            "This project uses Robolectric 4.x; replace removed 3.x APIs with "
            "verified current shadows. For activeNetwork/getNetworkCapabilities, use "
            "shadowOf(connectivityManager).setDefaultNetworkActive(false), not setActiveNetworkInfo(null) "
            "or obsolete ShadowNetworkInfo.newInstance overloads."
        ),
        "nonstandard_mock_framework": (
            "Generated tests must use Mockito-Kotlin from the pinned test stack; do not generate io.mockk APIs."
        ),
        "invalid_carui_progress_verification": (
            "CarUI ProgressBarController uses setIndeterminate(boolean), not setIsIndeterminate; "
            "or assert public UI behavior instead."
        ),
        "invalid_void_navigation_stubbing": (
            "Do not stub NavController.navigate(NavDeepLinkRequest) with Mockito.when/thenAnswer. "
            "Trigger the event, then verify(navController).navigate(captor.capture()) using a Mockito-Kotlin captor."
        ),
        "invalid_nav_deeplink_java_argumentcaptor": (
            "Do not use org.mockito.ArgumentCaptor.forClass(...).capture() inside NavController.navigate(NavDeepLinkRequest) verification. "
            "Use org.mockito.kotlin.argumentCaptor<NavDeepLinkRequest>() instead."
        ),
        "invalid_mockedstatic_factory": (
            "Use org.mockito.Mockito.mockStatic(Type::class.java), not MockedStatic.mockStatic(...)."
        ),
        "invalid_retrofit_error_body": (
            "Response.error(code, null) can throw while Mockito stubbing is unfinished. "
            "Use a concrete okhttp3.ResponseBody, for example ResponseBody.create(null, \"error\")."
        ),
        "invalid_stateflow_reflection": (
            "Do not read private StateFlow/LiveData fields via reflection; drive public ViewModel methods "
            "or verified mutable test seams after Fragment attach."
        ),
        "invalid_delegated_viewmodel_provider": (
            "Do not obtain delegated ViewModels with ViewModelProvider(activity).get(); attach the Fragment "
            "under the verified Hilt graph and drive public state methods or verified emissions."
        ),
        "invalid_fragment_detached_lifecycle_probe": (
            "Branch gaps on isAdded or toolbar setup require attached Hilt/Robolectric lifecycle; "
            "do not call onResume/onStart on a detached Fragment instance."
        ),
        "duplicate_incremental_candidate": (
            "Generate a distinct supplemental test body with the required fixture trigger; "
            "do not repeat an existing test that already failed to move Kover."
        ),
        "missing_coverage_trigger_coroutine": (
            "Provide flowOf/emit with the required item and advance the test scheduler under runTest."
        ),
        "missing_coverage_trigger_click": (
            "Attach the Fragment and performClick() on the registered view that owns the listener."
        ),
        "missing_coverage_trigger_observer_before_click": (
            "Post/set the required observable value, then performClick() on the registered view."
        ),
    }
    intents.update(overrides)
    return intents


VALIDATION_REPAIR_INTENTS: dict[str, str] = _build_repair_intents()


def repair_intent_for(category: str) -> str:
    return VALIDATION_REPAIR_INTENTS.get(category, "Repair the stable invalid pattern before running Gradle.")


def repair_bullets_for_codes(*codes: str) -> list[str]:
    seen: set[str] = set()
    bullets: list[str] = []
    for code in codes:
        text = VALIDATION_REPAIR_INTENTS.get(code, "")
        if text and text not in seen:
            seen.add(text)
            bullets.append(text)
    return bullets


def validation_codes_from_issues(issues: list[str]) -> list[str]:
    codes: list[str] = []
    for issue in issues or []:
        if ":" in issue:
            code = issue.split(":", 1)[0].strip()
            if code:
                codes.append(code)
    return codes


def enrich_validation_error_block(issues: list[str]) -> str:
    """Format validation issues plus catalog repair bullets for model repair prompts."""
    lines = [f"- {issue}" for issue in (issues or [])]
    bullets = repair_bullets_for_codes(*validation_codes_from_issues(issues))
    if bullets:
        lines.extend(["", "Repair bullets:"])
        lines.extend(f"- {bullet}" for bullet in bullets)
    return "\n".join(lines)


COVERAGE_BLOCKED_PROMPT_RULES: tuple[str, ...] = tuple(
    bullet
    for rule in GUARDRAIL_RULES
    if rule.id == "coverage_strategy"
    for bullet in rule.generation_bullets
)


SDK_STATIC_SEAM_PROMPT_RULES: tuple[str, ...] = (
    "Static SDK/global platform calls are coverage seams unless verified context proves they are mockable and deterministic in local JVM tests.",
    "Do not invent SDK constructors, static wrappers, or final-state assertions for these paths; cover stable public behavior or report the static wrapper requirement.",
)


NAV_DEEPLINK_PROMPT_RULES: tuple[str, ...] = tuple(
    bullet
    for rule in GUARDRAIL_RULES
    if rule.id == "navigation"
    for bullet in rule.generation_bullets
    if "NavDeepLinkRequest" in bullet or "TestNavHostController" in bullet or "mocked NavController" in bullet
)


ORCHESTRATION_GATE_LABEL = "Orchestration gate (planner/Kover/static)"


def orchestration_gate_text(code: str, description: str) -> str:
    return f"{ORCHESTRATION_GATE_LABEL}: {description} (nearest static category: {code})."
