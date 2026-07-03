# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Defines reusable Kotlin source-shape strategy contracts for prompts and validators.
"""Compact strategy contracts for repeated Kotlin/Android test source shapes."""

from __future__ import annotations

from dataclasses import dataclass

from UnitTest_gen.kotlin.guardrail_catalog import (
    SDK_STATIC_SEAM_PROMPT_RULES,
    repair_intent_for,
)


@dataclass(frozen=True)
class StrategyContract:
    id: str
    source_tags: frozenset[str]
    repair_tags: frozenset[str]
    prompt_rules: tuple[str, ...]
    retrieved_rule_tags: frozenset[str] = frozenset()
    fixture_template: str = ""
    validation_categories: tuple[str, ...] = ()
    stop_conditions: tuple[str, ...] = ()


def canonical_hilt_fragment_fixture_template() -> str:
    return (
        "Canonical attached Hilt Fragment fixture template:\n"
        "```kotlin\n"
        "private lateinit var activityController: ActivityController<HiltTestActivity>\n"
        "private lateinit var fragment: FragmentUnderTest\n"
        "private lateinit var toolbar: ToolbarController\n"
        "private lateinit var progressBarController: ProgressBarController\n"
        "private var carUiStaticMock: MockedStatic<CarUi>? = null\n\n"
        "private fun mockCarUi(activity: Activity): Pair<ToolbarController, ProgressBarController> {\n"
        "    toolbar = mock()\n"
        "    progressBarController = mock()\n"
        "    whenever(toolbar.progressBar).thenReturn(progressBarController)\n"
        "    carUiStaticMock = org.mockito.Mockito.mockStatic(CarUi::class.java)\n"
        "    carUiStaticMock!!.`when`<ToolbarController> { CarUi.requireToolbar(activity) }.thenReturn(toolbar)\n"
        "    return toolbar to progressBarController\n"
        "}\n\n"
        "@Before\n"
        "fun setUp() {\n"
        "    hiltRule.inject()\n"
        "    activityController = Robolectric.buildActivity(HiltTestActivity::class.java).create()\n"
        "    val activity = activityController.get()\n"
        "    activity.setTheme(androidx.appcompat.R.style.Theme_AppCompat)\n"
        "    mockCarUi(activity)\n"
        "    fragment = FragmentUnderTest()\n"
        "    fragment.arguments = Bundle().apply { /* put required args before attach */ }\n"
        "    activity.supportFragmentManager.beginTransaction()\n"
        "        .replace(android.R.id.content, fragment)\n"
        "        .commitNow()\n"
        "    activityController.start()\n"
        "    Navigation.setViewNavController(fragment.requireView(), navController)\n"
        "    activityController.resume()\n"
        "}\n"
        "```\n"
        "Keep the ActivityController assignment separate from activityController.get(); use get() only when an "
        "Activity/Context is needed. Set fragment arguments before commitNow; never call onCreateView/onViewCreated "
        "manually after attach. Install navigation after commitNow/start and before resume. Close carUiStaticMock in teardown."
    )


def main_dispatcher_rule_template() -> str:
    return (
        "Main dispatcher test scaffolding for viewModelScope/lifecycleScope coverage:\n"
        "```kotlin\n"
        "@get:Rule\n"
        "val mainDispatcherRule = MainDispatcherRule()\n\n"
        "class MainDispatcherRule(\n"
        "    private val dispatcher: TestDispatcher = StandardTestDispatcher(),\n"
        ") : TestWatcher() {\n"
        "    override fun starting(description: Description) {\n"
        "        Dispatchers.setMain(dispatcher)\n"
        "    }\n"
        "    override fun finished(description: Description) {\n"
        "        Dispatchers.resetMain()\n"
        "    }\n"
        "}\n"
        "```\n"
        "Use runTest { publicEntry(); advanceUntilIdle() } after MainDispatcherRule is installed."
    )


def fragment_ui_trigger_template() -> str:
    return (
        "Fragment UI/callback trigger scaffolding:\n"
        "- Click listeners: fragment.requireView().findViewById<View>(R.id.target).performClick() "
        "using verified module R.id names (never androidx.appcompat.R for feature layout ids); "
        "prefer binding.<field>.performClick() when ViewBinding is in scope.\n"
        "- Composite observer + click: postValue/setValue on the delegated ViewModel instance, "
        "advance main looper per MODULE SDK AND TEST STACK CONFIGURATION, then performClick() "
        "on the view that received the listener.\n"
        "- Checked change: fragment.requireView().findViewById<CheckBox>(R.id.target).isChecked = true\n"
        "- LiveData observe: postValue/setValue on the same ViewModel instance after lifecycle attach\n"
        "- registerForActivityResult/registerForAuthorizationResult: invoke the stored callback after Fragment attach with the required result payload"
    )


def delegated_viewmodel_emission_template() -> str:
    return (
        "Delegated ViewModel emission scaffolding (by viewModels()/activityViewModels()):\n"
        "- Do not @BindValue the delegated ViewModel type; attach the Fragment under the verified Hilt graph so the delegate resolves normally.\n"
        "- Drive observer/state through verified public ViewModel methods or mutable test-visible seams; do not use ViewModelProvider(activity).get() or getDeclaredField on private StateFlow/LiveData fields.\n"
        "- Post/set on the same LiveData/StateFlow instance the Fragment observes after commitNow/start/resume."
    )


ATTACHED_HILT_FRAGMENT_CONTRACT = StrategyContract(
    id="attached_hilt_fragment",
    source_tags=frozenset({"android_fragment", "hilt_fragment", "hilt_entrypoint"}),
    repair_tags=frozenset({"fixture_strategy", "hilt_graph", "dependency_error", "qualifier_mismatch"}),
    retrieved_rule_tags=frozenset(
        {
            "android_fragment",
            "hilt_fragment",
            "hilt_entrypoint",
            "hilt_graph",
            "fixture_strategy",
            "carui_toolbar",
            "carui_progress",
            "navigation_fragment",
        }
    ),
    prompt_rules=(
        "Use one attached-Hilt fixture lane only when the Hilt graph and module host are verified; otherwise use reduced non-attached public-contract tests or stop with the graph requirement.",
        "Attached @AndroidEntryPoint Fragment tests require @HiltAndroidTest, @get:Rule HiltAndroidRule, @RunWith(RobolectricTestRunner::class), @Config(application = HiltTestApplication::class), and the owning module's manifest-declared HiltTestActivity.",
        "Do not use local fake HiltTestActivity classes, reflection/private-field injection, manual ViewModelStore insertion, per-file @Module @InstallIn graph mutation, or partial non-Hilt lifecycle setup.",
        "Reuse the owning module's verified shared test Hilt module for duplicate binding keys instead of adding per-file @BindValue or @Provides replacements.",
        "Hilt MissingBinding is graph closure evidence; qualifier mismatch is a production DI contract conflict and must not be repaired with invented unqualified bindings.",
        "For CarUi, stub CarUi.requireToolbar with the exact host Activity and a named ProgressBarController mock; verify typed mocks, not property assignment.",
        "Set Fragment arguments before FragmentManager attachment and do not call onCreateView/onViewCreated manually; commitNow already runs lifecycle callbacks.",
        "For missed click/checkbox/observer/ActivityResult callback lines, trigger the real UI path with performClick(), isChecked, LiveData emission, or stored callback invocation after lifecycle attach.",
    ),
    fixture_template=canonical_hilt_fragment_fixture_template() + "\n\n" + fragment_ui_trigger_template() + "\n\n" + delegated_viewmodel_emission_template(),
    validation_categories=(
        "invalid_host_strategy",
        "invalid_hilt_lifecycle_setup",
        "invalid_hilt_manual_injection",
        "invalid_activity_controller_order",
        "invalid_global_hilt_graph_mutation",
        "duplicate_shared_hilt_test_binding",
        "invalid_carui_static_fixture",
        "invalid_property_assignment_verification",
    ),
    stop_conditions=("QUALIFIER_MISMATCH", "hilt_graph_unclosed"),
)


CARUI_BACK_LISTENER_CONTRACT = StrategyContract(
    id="carui_back_listener_fixture",
    source_tags=frozenset({"carui_back_listener", "carui_toolbar_fragment"}),
    repair_tags=frozenset({"fixture_strategy", "api_signature_mismatch", "mocking_strategy"}),
    retrieved_rule_tags=frozenset({"carui_back_listener", "carui_toolbar", "fixture_strategy", "mocking_strategy"}),
    prompt_rules=(
        "For ToolbarController.registerBackListener, use the verified Java callback shape used by the CarUi API; do not guess a Kotlin () -> Boolean callback type.",
        "If callback behavior is under test, capture the typed Java callback and invoke it; otherwise verify stable public UI/navigation behavior instead of broad matcher-only registration.",
    ),
    validation_categories=("invalid_carui_back_listener_fixture",),
)


NAV_DEEPLINK_VERIFICATION_CONTRACT = StrategyContract(
    id="nav_deeplink_verification",
    source_tags=frozenset({"nav_deeplink", "navigation_fragment"}),
    repair_tags=frozenset({"fixture_strategy", "api_signature_mismatch", "assertion_behavior"}),
    retrieved_rule_tags=frozenset({"nav_deeplink", "android_navigation", "navigation_fragment", "fixture_strategy"}),
    prompt_rules=(
        "When a Fragment emits NavDeepLinkRequest to another graph, install a mocked NavController on the real view and capture/verify the emitted URI.",
        "Use org.mockito.kotlin.argumentCaptor<NavDeepLinkRequest>() for Kotlin non-null navigate(...) verification; do not use Java ArgumentCaptor.forClass(...).capture().",
        "Use TestNavHostController only when the test installs the exact graph that owns the destination; do not force local graph ownership for cross-feature deep links.",
    ),
    validation_categories=("invalid_nav_deeplink_fixture", "invalid_nav_deeplink_java_argumentcaptor"),
)


STATIC_SINGLETON_GET_INSTANCE_CONTRACT = StrategyContract(
    id="static_singleton_get_instance",
    source_tags=frozenset({"static_singleton_get_instance", "companion_singleton_static_mock", "alert_dialog_helper"}),
    repair_tags=frozenset({"mocking_strategy", "mockito_misuse", "api_signature_mismatch", "fixture_strategy"}),
    retrieved_rule_tags=frozenset({"static_api", "mocking_strategy", "companion_singleton_static_mock", "alert_dialog_helper"}),
    prompt_rules=(
        "For Java static or Kotlin @JvmStatic getInstance() APIs, use a nullable MockedStatic<Type> field and close it in tearDown; never reflect singleton internals such as INSTANCE or instance.",
        "Stub verified static getInstance through the open MockedStatic receiver with an explicit return type: mockedStatic.`when`<ReturnType> { Type.getInstance(...) }.thenReturn(mock).",
        "For plain Kotlin companion getInstance() methods without @JvmStatic, do not use Mockito.mockStatic(Type::class.java); use the real singleton behavior or cover the public UI/callback path.",
        "Do not create or close MockedStatic inside a short setup block if production code will call it later during Fragment lifecycle or click callbacks.",
    ),
    validation_categories=(
        "invalid_singleton_reflection",
        "invalid_mockedstatic_missing_return_type",
    ),
)


HILT_ANDROID_ACTIVITY_CONTRACT = StrategyContract(
    id="hilt_android_activity",
    source_tags=frozenset({"android_activity", "hilt_android_activity"}),
    repair_tags=frozenset({"fixture_strategy", "lifecycle_error", "hilt_graph"}),
    retrieved_rule_tags=frozenset({"android_activity", "hilt_entrypoint", "fixture_strategy"}),
    prompt_rules=(
        "Drive Activity lifecycle only through Robolectric ActivityController; never call protected onCreate/onStart/onDestroy methods directly.",
        "Use the verified manifest theme and inspect layout-hosted Fragment/NavHost startup behavior before start/resume.",
        "Do not invent collaborator APIs or manually mutate a per-file Hilt graph. Use verified existing bindings and public collaborator methods only.",
    ),
    validation_categories=(
        "invalid_direct_activity_lifecycle_call",
        "invalid_hilt_activity_reflection",
        "invalid_fixed_buildconfig_branch",
        "invalid_project_interface_call",
    ),
)


HILT_ANDROID_APPLICATION_CONTRACT = StrategyContract(
    id="hilt_android_application",
    source_tags=frozenset({"android_application", "hilt_android_application"}),
    repair_tags=frozenset({"fixture_strategy", "assertion_behavior", "hilt_graph"}),
    retrieved_rule_tags=frozenset({"android_application", "hilt_entrypoint", "firebase", "logging"}),
    prompt_rules=(
        "Treat BuildConfig values as fixed for the active Gradle variant; do not generate contradictory true/false tests or mutate constants.",
        "Do not declare per-file Hilt modules for a @HiltAndroidApp Application. Test only a verified active-variant lifecycle path with the existing graph.",
        "If the Application graph or opposite BuildConfig branch cannot be controlled, report the seam instead of replacing assertions with comments or tautologies.",
    ),
    validation_categories=(
        "invalid_global_hilt_graph_mutation",
        "invalid_buildconfig_assertion",
        "invalid_trivial_assertion",
    ),
)


DAGGER_PROVIDER_LOGIC_CONTRACT = StrategyContract(
    id="dagger_provider_logic",
    source_tags=frozenset({"dagger_module", "dagger_provider_logic"}),
    repair_tags=frozenset({"api_signature_mismatch", "syntax_or_file_shape"}),
    retrieved_rule_tags=frozenset({"dagger_module", "mockito", "fixture_strategy"}),
    prompt_rules=(
        "Call the authored @Provides method directly with real values and Mockito-Kotlin mocks; do not build a Hilt component for pure selector logic.",
        "For non-marker interfaces, use mock<Interface>() unless every required member is implemented from verified source context.",
    ),
    validation_categories=("invalid_empty_interface_implementation",),
)


FRAGMENT_UNCONTROLLED_VIEWMODEL_IO_CONTRACT = StrategyContract(
    id="fragment_uncontrolled_viewmodel_io",
    source_tags=frozenset({"fragment_triggers_uncontrolled_viewmodel_io"}),
    repair_tags=frozenset({"coverage_strategy", "coroutine_error", "runtime_lifecycle"}),
    retrieved_rule_tags=frozenset(
        {"fragment_triggers_uncontrolled_viewmodel_io", "hardcoded_dispatcher_entry", "coverage_strategy"}
    ),
    prompt_rules=(
        "If a Fragment click path calls a delegated ViewModel method that launches hard-coded Dispatchers.IO, static SDK APIs, or real delay, do not use that click as a coverage target unless setup proves safe deterministic execution.",
        "For those paths, assert stable immediate UI/event behavior only; final coroutine state, SDK side effects, and delayed results require a ViewModel seam or should be reported as blocked.",
        "For click/checkbox/observer paths without IO detachment, trigger the real UI callback and assert collaborator/state effects instead of stopping at dialog-shown or pre-launch state.",
    ),
    stop_conditions=("fragment_path_triggers_uncontrolled_io", "needs_dispatcher_seam", "needs_static_wrapper"),
)


PLAIN_FRAGMENT_CONTRACT = StrategyContract(
    id="plain_fragment",
    source_tags=frozenset({"android_fragment", "plain_fragment", "dialog_fragment"}),
    repair_tags=frozenset({"fixture_strategy", "runtime_lifecycle"}),
    retrieved_rule_tags=frozenset({"plain_fragment", "dialog_fragment", "android_ui", "fixture_strategy"}),
    prompt_rules=(
        "Use FragmentScenario or a real Robolectric FragmentActivity/DialogFragment host only when the source has no @AndroidEntryPoint graph requirement.",
        "Exercise public lifecycle, click, dialog, and navigation behavior through visible views and callbacks; do not reflect private binding fields.",
    ),
    validation_categories=("invalid_fragment_fixture",),
)


DIRECT_CONSTRUCTOR_VIEWMODEL_CONTRACT = StrategyContract(
    id="direct_constructor_viewmodel",
    source_tags=frozenset({"viewmodel", "hilt_viewmodel"}),
    repair_tags=frozenset({"fixture_strategy", "api_signature_mismatch"}),
    retrieved_rule_tags=frozenset({"viewmodel", "hilt_viewmodel", "constructor_injection", "livedata"}),
    prompt_rules=(
        "Treat @HiltViewModel as directly constructible for unit tests unless the test intentionally exercises the Hilt graph.",
        "Construct the ViewModel first, then assign injected lateinit collaborators with direct statements before invoking methods under test.",
        "Do not assign injected lateinit fields inside apply/also/with receiver scopes because generated accessors can read the uninitialized field.",
        "For viewModelScope/lifecycleScope coroutine bodies on Main, install MainDispatcherRule and use runTest { method(); advanceUntilIdle() }.",
    ),
    fixture_template=main_dispatcher_rule_template(),
    validation_categories=("invalid_lateinit_injection_setup_order",),
)


HARDCODED_DISPATCHER_VIEWMODEL_CONTRACT = StrategyContract(
    id="hardcoded_dispatcher_viewmodel",
    source_tags=frozenset({"viewmodel", "hardcoded_dispatcher_entry"}),
    repair_tags=frozenset({"coroutine_error", "coverage_strategy", "assertion_behavior"}),
    retrieved_rule_tags=frozenset({"viewmodel", "coroutines_flow", "coroutine_error"}),
    prompt_rules=(
        "When production launches work on Dispatchers.IO or standalone CoroutineScope, runTest/advanceUntilIdle cannot prove final coroutine state without an injectable dispatcher seam.",
        "For viewModelScope/lifecycleScope work on Main without IO detachment, use MainDispatcherRule + runTest/advanceUntilIdle instead of deferring the opportunity.",
        "Prefer synchronous public methods, pure branch methods, immediate state setters, and collaborator interactions outside the hard-coded IO body.",
        "Do not add final-state assertions for uncontrolled delayed IO work; report the dispatcher seam requirement when those are the only remaining coverage gaps.",
    ),
    fixture_template=main_dispatcher_rule_template(),
    validation_categories=("invalid_hardcoded_dispatcher_final_state_assertion",),
    stop_conditions=("needs_dispatcher_seam", "coverage_blocked_path"),
)


COVERAGE_BLOCKED_PATH_CONTRACT = StrategyContract(
    id="coverage_blocked_path",
    source_tags=frozenset({"coverage_strategy"}),
    repair_tags=frozenset({"coverage_strategy", "coroutine_error"}),
    retrieved_rule_tags=frozenset({"coverage_strategy", "coroutine_error", "static_api"}),
    prompt_rules=(
        "Blocked coverage paths should not be sent as generation targets: private-only code, unsupported static/global APIs, unverified exception fixtures, and no dispatcher seam.",
        "Generate no-op or method-entry tests only when they execute listed missed lines; otherwise stop with a compact seam recommendation.",
    ),
    stop_conditions=("needs_dispatcher_seam", "needs_static_wrapper", "needs_verified_exception_fixture", "private_only_path"),
)


SDK_STATIC_OR_PLATFORM_SEAM_CONTRACT = StrategyContract(
    id="sdk_static_or_platform_seam",
    source_tags=frozenset({"sdk_static_seam", "static_api"}),
    repair_tags=frozenset({"coverage_strategy", "mocking_strategy", "runtime_lifecycle"}),
    retrieved_rule_tags=frozenset({"sdk_static_seam", "static_api", "coverage_strategy"}),
    prompt_rules=SDK_STATIC_SEAM_PROMPT_RULES,
    stop_conditions=("needs_static_wrapper", "needs_verified_sdk_fixture"),
)


OBSERVER_AND_CLICK_CONTRACT = StrategyContract(
    id="observer_and_click",
    source_tags=frozenset({"verified_observer_and_click", "delegated_viewmodel_fragment"}),
    repair_tags=frozenset({"fixture_strategy", "lifecycle_error", "coverage_strategy"}),
    retrieved_rule_tags=frozenset(
        {
            "verified_observer_and_click",
            "delegated_viewmodel_fragment",
            "livedata",
            "fixture_strategy",
            "coverage_strategy",
            "fixture_playbooks",
        }
    ),
    prompt_rules=(
        "Follow PLAYBOOK: verified_observer_and_click in FIXTURE EXECUTION PLAYBOOKS (attach -> emit observed state -> idleMainLooper -> performClick).",
        "Consult VIEWMODEL AND DIALOG CONTEXT mutability hints before postValue/setValue; use a public ViewModel method when the observed field is read-only.",
    ),
    validation_categories=("missing_coverage_trigger_observer_before_click",),
)


INCREMENTAL_DIALOG_CALLBACK_CONTRACT = StrategyContract(
    id="incremental_dialog_callback",
    source_tags=frozenset({"dialog_callback_chain", "alert_dialog_helper"}),
    repair_tags=frozenset({"fixture_strategy", "mocking_strategy", "coverage_strategy"}),
    retrieved_rule_tags=frozenset(
        {
            "dialog_callback_chain",
            "alert_dialog_helper",
            "android_dialog",
            "fixture_strategy",
            "coverage_strategy",
            "fixture_playbooks",
        }
    ),
    prompt_rules=(
        "Follow PLAYBOOK: verified_dialog_callback in FIXTURE EXECUTION PLAYBOOKS.",
        "Trigger dialog/callback coverage through the target class public entry path; do not call delegated getInstance() helpers directly from the test.",
    ),
    validation_categories=("missing_coverage_trigger_dialog",),
)


INJECT_FIELD_COVERAGE_CONTRACT = StrategyContract(
    id="inject_field_coverage",
    source_tags=frozenset({"inject_lateinit_field", "hilt_fragment"}),
    repair_tags=frozenset({"coverage_strategy", "fixture_strategy"}),
    retrieved_rule_tags=frozenset(
        {"inject_lateinit_field", "hilt_fragment", "coverage_strategy", "fixture_strategy", "fixture_playbooks"}
    ),
    prompt_rules=(
        "Follow FRAGMENT / VIEWMODEL LIFECYCLE PLAYBOOKS §@INJECT LATEINIT FIELD; do not use fragment.isAdded as coverage proof.",
    ),
    validation_categories=("invalid_inject_field_shallow_coverage",),
)


STRATEGY_CONTRACTS = (
    ATTACHED_HILT_FRAGMENT_CONTRACT,
    HILT_ANDROID_ACTIVITY_CONTRACT,
    HILT_ANDROID_APPLICATION_CONTRACT,
    DAGGER_PROVIDER_LOGIC_CONTRACT,
    CARUI_BACK_LISTENER_CONTRACT,
    NAV_DEEPLINK_VERIFICATION_CONTRACT,
    STATIC_SINGLETON_GET_INSTANCE_CONTRACT,
    FRAGMENT_UNCONTROLLED_VIEWMODEL_IO_CONTRACT,
    PLAIN_FRAGMENT_CONTRACT,
    DIRECT_CONSTRUCTOR_VIEWMODEL_CONTRACT,
    HARDCODED_DISPATCHER_VIEWMODEL_CONTRACT,
    COVERAGE_BLOCKED_PATH_CONTRACT,
    SDK_STATIC_OR_PLATFORM_SEAM_CONTRACT,
    OBSERVER_AND_CLICK_CONTRACT,
    INCREMENTAL_DIALOG_CALLBACK_CONTRACT,
    INJECT_FIELD_COVERAGE_CONTRACT,
)


def select_strategy_contracts(
    source_tags,
    repair_tags=None,
) -> tuple[StrategyContract, ...]:
    source_tag_set = {str(tag) for tag in (source_tags or [])}
    repair_tag_set = {str(tag) for tag in (repair_tags or [])}
    selected = []

    for contract in STRATEGY_CONTRACTS:
        source_match = bool(contract.source_tags & source_tag_set)
        repair_match = bool(contract.repair_tags & repair_tag_set)
        if source_match or repair_match:
            if contract.id == "attached_hilt_fragment" and "hilt_fragment" not in source_tag_set and not repair_match:
                continue
            if contract.id == "plain_fragment" and "hilt_fragment" in source_tag_set:
                continue
            if contract.id == "dagger_provider_logic" and "dagger_provider_logic" not in source_tag_set:
                continue
            if contract.id == "direct_constructor_viewmodel" and (
                "viewmodel" not in source_tag_set or "android_fragment" in source_tag_set
            ):
                continue
            if contract.id == "hardcoded_dispatcher_viewmodel" and not (
                ({"viewmodel", "hardcoded_dispatcher_entry"} <= source_tag_set and "android_fragment" not in source_tag_set)
                or repair_match
            ):
                continue
            if contract.id == "fragment_uncontrolled_viewmodel_io" and "android_fragment" not in source_tag_set and not repair_match:
                continue
            if contract.id == "sdk_static_or_platform_seam" and not (
                source_tag_set & {"sdk_static_seam", "static_api"} or repair_match
            ):
                continue
            selected.append(contract)

    return tuple(selected)


def contract_rule_tags(contracts) -> set[str]:
    tags: set[str] = set()
    for contract in contracts or ():
        tags.update(contract.retrieved_rule_tags)
    return tags


def format_contract_prompt_rules(contracts, include_templates: bool = False) -> str:
    lines = []
    for contract in contracts or ():
        title = contract.id.replace("_", " ").title()
        lines.append(f"{title} contract:")
        lines.extend(f"- {rule}" for rule in contract.prompt_rules)
        if include_templates and contract.fixture_template:
            lines.append(contract.fixture_template)
    return "\n".join(lines)


def filter_contracts_for_prompt(contracts, fixture_ids=None, source_categories=None, source_code: str = ""):
    """Drop playbook-backed contracts when matching fixture playbooks are already in the prompt."""
    from UnitTest_gen.kotlin import prompts as prompt_constants
    from UnitTest_gen.kotlin.kotlin_analysis import source_declares_android_fragment

    skip_ids = prompt_constants.contract_ids_covered_by_fixtures(fixture_ids)
    categories = {str(category) for category in (source_categories or ())}
    if "inject_lateinit_field" in categories and prompt_constants.format_fixture_playbooks(
        fixture_ids,
        source_categories=source_categories,
        source_code=source_code,
        is_android_fragment=source_declares_android_fragment(source_code or ""),
    ):
        skip_ids.add("inject_field_coverage")
    return tuple(contract for contract in (contracts or ()) if contract.id not in skip_ids)


def validation_repair_intent(category: str) -> str:
    return repair_intent_for(category)
