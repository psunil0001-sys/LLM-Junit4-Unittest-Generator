# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Defines authoritative Kotlin, Android, framework, and repair rule content.
# Prompt blueprints and generation rule constants

GENERATION_SYSTEM_PROMPT = (
    "You are an expert Android Kotlin test engineer. "
    "Generate JUnit4 Kotlin tests only: use org.junit.*, @RunWith when needed, and never use JUnit5/Jupiter. "
    "Every complete test file must begin with package <same_as_source_under_test> before imports. "
    "Choose the smallest verified compile-safe test strategy, then return only the requested final code. "
    "Treat the final rule block at the end of the user prompt as the highest-priority instruction."
)

INCREMENTAL_SYSTEM_PROMPT = (
    "You are an expert Android Kotlin test engineer. "
    "Generate JUnit4 Kotlin tests only: use org.junit.*, @RunWith when needed, and never use JUnit5/Jupiter. "
    "Supplemental output must keep or include package <same_as_source_under_test> before imports. "
    "Generate only supplemental tests for verified Kover coverage gaps. "
    "Treat the final rule block at the end of the user prompt as highest priority."
)

PATCH_REPAIR_SYSTEM_PROMPT = (
    "Return only valid JSON with old_text/new_text or patches[]. "
    "For batch compile repair, return minimal patches that clear all listed compiler errors. "
    "Do not explain, compare alternatives, or include prose outside JSON. "
    "Use verified context. Follow the final rule block at the end of the user prompt."
)

FULL_FILE_REPAIR_SYSTEM_PROMPT = (
    "Fix the focused Kotlin/Gradle failure using verified context. "
    "Return JUnit4 Kotlin test code only; remove JUnit5/Jupiter imports, annotations, and assertions. "
    "Keep package <same_as_source_under_test> as the first statement before imports. "
    "Follow the final rule block at the end of the user prompt. "
    "Do not explain or compare alternatives in the final response. "
    "Return only corrected Kotlin test code."
)

REPAIR_INTRO_TEMPLATE = """
You are an expert Android Kotlin test engineer.
Choose the first concrete fix supported by the highlighted compiler/JUnit error.
Use ONLY verified context.
Use compiler-provided line numbers and excerpts from EXACT GENERATED TEST ERROR LOCATIONS as the source of truth.
When the highlighted error is inside setup/mocking code, inspect the whole setup block and prefer a stable setup rewrite over import-only patching.
Do not compare alternative framework strategies when the highlighted line has a direct compile fix.

Task:
{repair_scope_text}

Goal:
Fix the current Gradle/Kotlin/JUnit failure with minimal changes while preserving useful coverage.
"""

GENERATION_USER_SECTION_TITLES = {
    "target_source": "TARGET CLASS TO TEST (PUBLIC API ONLY)",
    "dependency_context": "DEPENDENCY CONTEXT STRUCTURES",
    "source_strategy": "SOURCE-SPECIFIC TEST STRATEGY",
    "sdk_guidance": "MODULE SDK TEST CONFIGURATION",
    "nearby_patterns": "NEARBY EXISTING TEST PATTERNS",
    "source_risks": "STATIC SOURCE BUG-HUNTING TARGETS",
}

INCREMENTAL_USER_SECTION_TITLES = {
    "target_source": "TARGET CLASS TO TEST (PUBLIC API ONLY)",
    "existing_test": "EXISTING TEST FILE TO EXTEND WITHOUT DUPLICATION",
    "coverage_context": "KOVER COVERAGE GAP CONTEXT",
    "coverage_strategy": "DETERMINISTIC COVERAGE STRATEGY",
    "dependency_context": "DEPENDENCY CONTEXT STRUCTURES",
    "source_strategy": "SOURCE-SPECIFIC TEST STRATEGY",
    "sdk_guidance": "MODULE SDK TEST CONFIGURATION",
    "nearby_patterns": "NEARBY EXISTING TEST PATTERNS",
    "source_risks": "GAP-RELEVANT STATIC BUG-HUNTING TARGETS",
}

REPAIR_USER_SECTION_TITLES = {
    "class_name": "CLASS UNDER TEST",
    "focused_error": "FOCUSED GRADLE/KOTLIN ERROR BLOCK",
    "error_locations": "EXACT GENERATED TEST ERROR LOCATIONS",
    "compiler_guide": "COMPILER REPAIR DIAGNOSTIC GUIDE",
    "generated_diagnostics": "STATIC GENERATED-TEST REPAIR DIAGNOSTICS",
    "source_strategy": "SOURCE-SPECIFIC TEST STRATEGY",
    "nearby_patterns": "NEARBY EXISTING TEST PATTERNS",
    "verified_context": "VERIFIED SOURCE / DEPENDENCY CONTEXT FROM MCP TOOLS",
    "source_risks": "STATIC SOURCE BUG-HUNTING TARGETS",
    "current_test": "CURRENT GENERATED TEST CODE",
    "source_code": "SOURCE CODE UNDER TEST",
}

ANDROID_BLUEPRINTS = """
    --- CRITICAL ANDROID ARCHITECTURE BLUEPRINTS ---

    1. FRAGMENT TESTING:
    - Use @RunWith(RobolectricTestRunner::class) for local JVM tests that execute Android framework, Fragment lifecycle, resources, views, Context/Activity, or navigation behavior.
    - Use FragmentScenario / launchFragmentInContainer only for plain non-Hilt Fragments when the default empty host satisfies all requirements: no @AndroidEntryPoint, no Hilt injection, no custom Activity, no Activity-level setup, and standard lifecycle/view assertions only.
    - For instrumented tests, use FragmentScenario or ActivityScenario only when the selected host satisfies all production requirements (DI, theme, window flags, navigation, Activity APIs).
    - For custom host, library Fragment, requireContext(), requireActivity(), View, lifecycle, or navigation behavior, create a real Robolectric FragmentActivity/AppCompatActivity and attach the Fragment through supportFragmentManager.beginTransaction().replace(android.R.id.content, fragment).commitNow() before exercising lifecycle-dependent code.
    - For DialogFragment behavior, attach with fragment.show(activity.supportFragmentManager, tag) and executePendingTransactions() so dialog/window behavior is initialized.
    - If production code calls findNavController() after the view is created, install TestNavHostController with Navigation.setViewNavController(fragment.requireView(), navController) before the event or observable emission that triggers navigation.
    - If onViewCreated() itself can navigate from an immediate LiveData/Flow emission, do not seed a navigation state before attachment. Create/attach the host and Fragment, install the controller as early as the verified lifecycle permits, then emit through the same real observable/ViewModel/public setter.
    - If navigation occurs directly inside onViewCreated() before a controller can be installed, do not generate a speculative lifecycle test; use a verified host/navigation setup or a direct public-contract test that avoids that lifecycle path.
    - When a source navigates by NavDeepLinkRequest to another feature graph, use a mocked NavController installed on the view and capture/verify the deep-link URI; do not require the local TestNavHostController graph to own that destination.
    - For NavDeepLinkRequest verification, capture the request sent to NavController.navigate(...) with org.mockito.kotlin.argumentCaptor<NavDeepLinkRequest>() and assert the URI/route query values produced by the source. Do not use Java ArgumentCaptor.forClass(...).capture() for Kotlin non-null navigate parameters. Use TestNavHostController only when the exact destination graph is installed in the test.
    - Do not stub NavController.navigate(...) with Mockito.when; trigger the UI/event and verify/capture the navigate call.
    - Do not stub findNavController() itself; install a controller on the real Fragment view.
    - Manual field assignment is allowed only for direct method tests that do not attach an @AndroidEntryPoint Fragment and do not trigger onAttach(), onCreate(), or onViewCreated(). Never combine manual injection, reflection, or private-field writes with attached Hilt lifecycle tests.
    - For complex Fragments (Hilt, Navigation, delegated ViewModels, dialogs, BuildConfig gates, static UI helpers, async handlers), generate a compact portfolio of high-confidence tests that share one verified fixture. If multiple missed lines are reachable from the same setup, cover them in one test or a small table-style group. Prefer 2–4 tests only when each test covers a distinct public behavior path. Do not generate broad speculative observer matrices.
    - If a Fragment path cannot be covered because of missing graph bindings, unsupported runtime APIs, unsafe lifecycle setup, or no public execution path, skip that target and report the blocked source/method/lines with the required fix.

    2. HILT / DEPENDENCY INJECTION:
    - For pure unit tests of constructor-injected ViewModels, UseCases, Repositories, Mappers, Validators, Helpers, or plain classes, instantiate directly with fakes/mocks. Do not start Hilt unless DI integration is under test.
    - For Hilt component/UI integration tests, use @HiltAndroidTest and @get:Rule(order = 0) val hiltRule = HiltAndroidRule(this). Call hiltRule.inject() in @Before before using @Inject fields or Hilt-dependent UI.
    - For local JVM lifecycle tests of @AndroidEntryPoint Fragments, use @RunWith(RobolectricTestRunner::class), @HiltAndroidTest, HiltAndroidRule, and HiltTestApplication configured through @Config(application = HiltTestApplication::class) or verified robolectric.properties.
    - HiltAndroidRunner is for instrumentation; do not use it as the local JVM/Robolectric runner.
    - Plain FragmentActivity/Application and EmptyRobolectricApplication do not satisfy an attached @AndroidEntryPoint Fragment's Hilt GeneratedComponent requirement.
    - Use full Hilt/Robolectric Fragment attachment only when module support, generated test components, host Activity, and all required bindings are verified. Otherwise test direct public contracts that avoid Hilt lifecycle injection.
    - Before generating full local Hilt lifecycle tests, verify hilt-android-testing, test-source KSP/KAPT Hilt compiler support, Robolectric, AndroidX test core, Fragment testing support, and navigation-testing when needed.
    - For local Hilt Fragment attachment, use a manifest-declared top-level @AndroidEntryPoint HiltTestActivity in src/test/AndroidManifest.xml with a matching src/test/java/.../HiltTestActivity.kt. Import and launch that declared host; do not invent an undeclared local, nested, or fake Activity.
    - For instrumented Hilt UI tests, use a verified custom AndroidJUnitRunner returning HiltTestApplication. For local Robolectric Hilt tests, use HiltTestApplication through @Config or robolectric.properties.
    - Attached @AndroidEntryPoint Fragment fixture contract: use the fixed script-owned fixture shape with ActivityController<HiltTestActivity>, assignment from Robolectric.buildActivity(...).create(), activityController.get() only when an Activity/Context is needed, named ProgressBarController, exact CarUi.requireToolbar(activity), set Fragment arguments before commitNow(), commitNow(), start(), install Navigation.setViewNavController(...), then resume() only if required. Do not call onCreateView/onViewCreated manually after commitNow().
    - When the owning module has a shared test Hilt binding for a missing key, use that shared test graph binding and do not generate per-file @BindValue, @Provides, @TestInstallIn, or @Module/@InstallIn for the same key.
    - Recommended controlled Robolectric order: build ActivityController; configure required theme before create; call create(); attach Fragment with commitNow(); install NavController before start/resume when initial observer emissions may navigate; call start(); call resume() only if source behavior requires RESUMED state. Do not compress lifecycle calls when intermediate state/order matters.
    - Hilt MissingBinding means graph closure is unverified. Resolve the exact missing dependency or binding through verified classpath-visible production/test bindings first; otherwise use reduced non-attached public-contract coverage or stop with the dependency requirement.
    - If MissingBinding requests an unqualified type while production provides the same type with @Named or another qualifier, treat it as a production DI contract conflict. Do not invent an unqualified @BindValue, @Provides, @TestInstallIn, or per-file module binding.
    - Never repair an unclosed Hilt graph by declaring a local fake HiltTestActivity, private-field reflection injection, manual ViewModelStore insertion, or partial non-Hilt attached lifecycle setup.
    - @BindValue and @TestInstallIn may coexist only for different binding keys. For the same key, use one replacement strategy and never create duplicate unqualified bindings.
    - Do not add @BindValue for a type already provided unqualified by production unless the owning production module is correctly removed/replaced with @UninstallModules plus a test binding, or a verified @TestInstallIn replacement.
    - Prefer @TestInstallIn for a replacement shared across tests. Use @UninstallModules plus a local test module or @BindValue for per-test replacement when justified.
    - @UninstallModules removes only verified production @InstallIn modules; do not attempt to uninstall a @TestInstallIn module.
    - For by viewModels()/by activityViewModels(), @BindValue lateinit var viewModel does not replace the delegated ViewModelProvider/Hilt factory result. Drive the real delegated instance through public APIs or repair the Hilt graph that creates it.
    - For delegated ViewModels, prefer stable lifecycle/UI/navigation assertions. Generate observer-state tests only if the same real instance is reachable and controllable after a view/lifecycle owner/controller exists.
    - On MissingBinding, treat the Hilt graph as unclosed until the exact missing key has a verified dependency or binding path; fall back to direct public-contract coverage when closure is not verified. On DuplicateBindings, remove/replace the conflicting binding; never retain duplicate test/prod bindings.

    3. VIEWMODELS, LIVEDATA, FLOW, COROUTINES:
    - Add InstantTaskExecutorRule only when the tested code uses LiveData, postValue(), MediatorLiveData, Transformations, or Architecture Components executors; it is not required for StateFlow-only or pure Kotlin ViewModels.
    - For direct-constructor ViewModel tests with @Inject lateinit collaborators, construct the ViewModel first, then assign those collaborators with separate direct statements before invoking methods under test.
    - Do not assign injected lateinit ViewModel collaborators inside apply/also/with/chained receiver scopes; those scopes can read the uninitialized property through the accessor before assignment.
    - For suspend functions, repositories, UseCases, and coroutine ViewModels, use kotlinx.coroutines.test.runTest.
    - When source uses Dispatchers.Main/viewModelScope, install a TestDispatcher with Dispatchers.setMain in setup and resetMain in teardown. Use StandardTestDispatcher for controlled scheduling; use UnconfinedTestDispatcher only when eager execution is deliberately required.
    - Use runCurrent(), advanceTimeBy(), or advanceUntilIdle() only when the source scheduling requires them; keep virtual time deterministic.
    - Drive LiveData/Flow deterministically through MutableLiveData, MutableStateFlow, SharedFlow, fake repositories, or controllable producers. Assert external state, emissions, interaction, or error mapping; do not merely mock an observable.
    - For Flow outputs, assert a required first item, finite emission sequence, or collected final state. For Flow inputs, prefer an injected fake/test producer over timing-dependent real implementations.
    - Import only Flow helpers actually used: flow, flowOf, first, toList, catch, map, onStart.
    - For SavedStateHandle, create it with verified source keys/values.
    - Assert public state transitions, events, LiveData/StateFlow values, and collaborator calls rather than constructor-only/no-throw tests.
    - kotlinx.coroutines.test controls Dispatchers.Main through Dispatchers.setMain/resetMain.
    - Do not generate Dispatchers.setIO(), Dispatchers.resetIO(), or equivalent unsupported dispatcher replacement APIs.
    - When production code explicitly launches on Dispatchers.IO or CoroutineScope(Dispatchers.IO) without an injectable dispatcher/seam, do not generate completion-state tests that rely on runTest, runCurrent, or advanceUntilIdle.
    - Defer that cluster unless a verified source-level dispatcher seam exists.
    - For blocked ViewModel/coroutine paths, do not generate no-op method-entry tests. Report the method/lines, seam reason, and the test approach that becomes valid after the seam is added.

    4. PERMISSIONS, ACTIVITY RESULTS, ANDROID CONTEXT:
    - Use ApplicationProvider.getApplicationContext<Context>() for generic Context and ApplicationProvider.getApplicationContext<Application>() when Application shadows/started-Activity state are needed.
    - ActivityScenario.launch(...) returns a scenario, not a Context; use ApplicationProvider or the Activity instance from scenario callbacks.
    - Grant permissions with project-compatible Robolectric shadows, e.g. shadowOf(application).grantPermissions(Manifest.permission.X); do not rely on PackageManager.addPermission unless verified in this repo.
    - Cover both granted and denied/request-launch branches for permission helpers.
    - For Fragment permission extensions that call requireContext()/requireActivity(), attach the Fragment to a Robolectric Activity first.
    - For ActivityResultContracts.RequestMultiplePermissions, use a verified synchronous Robolectric delivery path when available; otherwise directly invoke the stored/generated callback/lambda and assert an observable effect.

    5. ROBOLECTRIC UI, RESOURCES, SYSTEM SERVICES, CALLBACKS:
    - For context.getString/getColor, layouts, Views, visibility, padding/margins, click listeners, and Material widgets, prefer real Robolectric resources/views over deep mocks of LayoutInflater, View, TextView, ContextCompat, or framework APIs.
    - Android library modules that perform resource tests need android.testOptions.unitTests.isIncludeAndroidResources = true. Add @Config(packageName = module namespace) only when verified manifest/resource resolution requires it.
    - If resource enablement reveals an AppAuth manifest placeholder error and the module uses that placeholder, configure manifestPlaceholders["appAuthRedirectScheme"] in defaultConfig.
    - Configure required AppCompat/Material theme before creating widgets that require it.
    - For real Robolectric Context, configure framework behavior with shadows and real fixtures; reserve Mockito Context stubs for explicitly mocked Contexts.
    - For code that reads ConnectivityManager.activeNetwork/getNetworkCapabilities, simulate no network with shadowOf(connectivityManager).setDefaultNetworkActive(false). Do not call setActiveNetworkInfo(null), which makes Robolectric activeNetwork dereference null, or use obsolete ShadowNetworkInfo.newInstance overloads.
    - Use Mockito primarily for project-owned collaborators, network/service boundaries, and explicit mock Contexts. For real app Context, prefer shadowOf(packageManager), real ResolveInfo/ActivityInfo/PackageInfo, and Robolectric system-service shadows.
    - For network branches, prefer an injected app abstraction; otherwise mock/fixture ConnectivityManager and NetworkCapabilities. For Settings.Global branches, use Settings.Global.putInt(contentResolver, key, value) when source reads it through ContentResolver.
    - For Android 13+ PackageManager query paths, use the verified ResolveInfoFlags overload with typed matchers; use Int flags only for verified pre-33 paths.
    - Use Mockito-Kotlin typed matchers: any<T>(), eq(...), argumentCaptor(). If one argument uses a matcher, use matchers for all arguments in that invocation.
    - For generic Android list stubs, prefer chained thenReturn(first.toMutableList()).thenReturn(second.toMutableList()) over vararg multi-return when inference is fragile.
    - Use Java static mocks only for verified Java static/@JvmStatic APIs, scope MockedStatic tightly, close it after exercising code, and keep static-mock assertions on one deterministic thread. Do not static-mock ordinary Kotlin object helpers.
    - For CarUi.requireToolbar in Fragment fixtures, open the static mock with org.mockito.Mockito.mockStatic, stub CarUi.requireToolbar(activity) with the exact host Activity instance, and close the MockedStatic in teardown. Do not use MockedStatic.mockStatic or any()/any<Activity>() inside the static lambda.
    - If source reads toolbar.progressBar, create a named typed ProgressBarController mock, stub toolbar.progressBar to return it before attach/resume, and verify that ProgressBarController directly. Do not use thenReturn(mock()), chained toolbar.progressBar verification, Android ProgressBar, or Mockito property-assignment verification.
    - For ToolbarController.registerBackListener, use the verified CarUi callback type exposed by the API, such as a Java Supplier<Boolean> when required by the local dependency. Capture and invoke the typed callback only when testing callback behavior; otherwise verify stable public UI/navigation behavior instead of broad matcher-only registration. Do not guess a Kotlin () -> Boolean callback shape.
    - If a Fragment click path calls a delegated ViewModel method that launches hard-coded Dispatchers.IO, uses static SDK/platform calls, or waits on real delay, treat that path as controlled-risk. Assert only immediate stable UI/event behavior unless the ViewModel exposes a dispatcher/static seam.
    - When an Android framework, static SDK, environment, or fixture limitation prevents deterministic execution, skip that coverage item and report whether it is a dependency, environment, test setup, or source-code issue.
    - Prefer real ApplicationProvider/Robolectric behavior or a source-visible seam over mocking Kotlin object utilities.
    - For delayed main-looper work, use the Robolectric API verified in this repo. Where this repo supports it, import org.robolectric.shadows.ShadowLooper and call ShadowLooper.runUiThreadTasksIncludingDelayedTasks(); do not generate shadowOf(looper).runUiThreadTasksIncludingDelayedTasks() unless verified locally.
    - When exact delay matters, use PAUSED looper mode and advance time with ShadowLooper.idleFor(...) if available in the repo version.
    - For started-Activity assertions, use Shadows.shadowOf(application).nextStartedActivity or Shadows.shadowOf(activity).nextStartedActivity; nextStartedActivity is a Kotlin property/accessor, not a generic function.
    - For dialogs/callbacks that call startActivity(), use a real Robolectric Activity context and assert the started intent.
    - For callback/lambda coverage, showing a dialog is insufficient: trigger the real button/callback path and assert collaborator, state, or navigation effects.
    - Use @Config(sdk = [...]) only for verified SDK-specific source branches, with SDK >= minSdk and within the module's verified build/manifest limits.
    - For ColorDrawable assertions, compare resolved values such as Color.TRANSPARENT.
    - Use only shadow APIs verified in the local dependency version.

    6. TEST PRIORITY:
    - Test behavior owners first: ViewModel → UseCase/Interactor → RepositoryImpl → DataSource/Storage → Validator/Mapper/Utils → UI (Fragment/Activity/Dialog/Adapter) → DI modules/Constants/Navigation objects.
    - Cover private/internal behavior through public APIs.
    - Passive State/UiState/Result/DTO/Item/Status/enum/simple sealed models are usually indirect coverage; test directly only when they contain branching, validation, transformation, or custom behavior.
    """

APOLLO_MAPPER_BLUEPRINTS = """
    --- CRITICAL APOLLO MAPPER TESTING BLUEPRINTS ---

    1. DEFAULT SAFETY MODE:
    - Prefer compact compile-safe mapper suites with strong observable assertions over exhaustive tests that assume unverified generated Apollo APIs.
    - Use project-owned api.model DTOs/models as normal Kotlin fixtures only when package, constructors, and non-null requirements are verified.
    - Default to reflection mode for Apollo generated operation receivers, nested response/body/list classes, and api.type input return types when generated source details or test-classpath availability are uncertain.
    - If generated Apollo source, constructors, package names, and test classpath are verified, direct typed construction is allowed; do not force reflection merely to hide a known valid API.
    - When Apollo test data builders are enabled and verified, prefer them for generated response construction.
    - Escape nested-class separators in generated Kotlin string literals: use "\\$" in Class.forName("...\\$Body").

    2. REFLECTION MODE:
    - Reference unverified generated classes only through verified Class.forName strings, raw org.mockito.Mockito.mock(clazz), and reflected Any? values.
    - Do not infer generated package names, nested class names, constructor signatures, getter names, or operation-child layout. Verify from generated source/bytecode; otherwise cover a nullable/simple branch.
    - For ApiExt.kt top-level functions, resolve `<verified.generated.api.package>.ApiExtKt` from source/generated files when there is no verified @file:JvmName/@file:JvmMultifileClass override. Otherwise use the verified facade.
    - Verify Mockito can mock the generated Kotlin/final class before reflection mocking. Reflection does not bypass final-class mocking limitations. If unavailable, use verified direct construction/data builders/project fakes or a safe nullable/simple branch.
    - Keep reflection helpers fail-fast: allow ClassNotFoundException, NoSuchMethodException, IllegalAccessException, and unexpected reflection errors to surface.
    - Use getMethod() for verified public JVM-visible methods/getters. Use getDeclaredMethod() only for verified declared targets with deliberate accessibility handling.
    - Verify Java getter shape: boolean values may expose isXxx(), and not all generated Kotlin properties have a getXxx() method.

    3. REQUIRED REFLECTION HELPERS:
    private fun mockApollo(className: String, getters: Map<String, Any?>): Pair<Class<*>, Any> {
        val clazz = Class.forName(className)
        val instance = org.mockito.Mockito.mock(clazz)
        getters.forEach { (getter, value) ->
            org.mockito.Mockito.`when`(clazz.getMethod(getter).invoke(instance)).thenReturn(value)
        }
        return clazz to instance
    }

    private fun invokeApiExt(
        methodName: String,
        parameterTypes: Array<Class<*>>,
        args: Array<Any?>
    ): Any? = try {
        Class.forName("<verified.generated.api.package>.ApiExtKt")
            .getDeclaredMethod(methodName, *parameterTypes)
            .invoke(null, *args)
    } catch (e: java.lang.reflect.InvocationTargetException) {
        throw e.targetException
    }

    private fun readApollo(value: Any, getterName: String): Any? =
        value::class.java.getMethod(getterName).invoke(value)

    4. REFLECTION EXECUTION RULES:
    - Call invokeApiExt with exact verified receiver/argument arrays, e.g. invokeApiExt("toCustomResponse", arrayOf(receiverClass), arrayOf(receiver)).
    - For overloads, include every verified JVM parameter type; the extension receiver is the first JVM parameter.
    - For response mappers, mock verified receiver/body/list-item types using mockApollo, then invoke the extension through the verified facade.
    - mockApollo returns Pair<Class<*>, Any>; when nesting a mock in a getter map, pass .second only: "getBody" to mockApollo("...\\$Body", mapOf(...)).second.
    - Stub getters only through verified method names and the mockApollo pattern.
    - Keep reflected input mapper results as Any? and inspect verified getters with readApollo.
    - Assert values from verified generated getter/property names only.
    - If generated return details are inaccessible, assert non-null invocation only when that is still a meaningful public contract; otherwise cover a verified nullable/simple mapper branch.
    - Assert externally meaningful mapping: mapped fields, null/default behavior, error/body mapping, and list conversion; do not assert incidental generated implementation details.

    5. EXAMPLES:
    - Receiver: val receiverClass = Class.forName("<verified.generated.api.package>.UpdateTripCategoryMutation\\$UpdateTripCategory")
    - Response: val result = invokeApiExt("toCustomResponse", arrayOf(receiverClass), arrayOf(receiver)) as CustomResponseRemote
    - Input: val result = invokeApiExt("toCreateTripInputGQL", arrayOf(CreateTripInputRemote::class.java), arrayOf(input)) ?: error("mapper returned null")
    - Input assertion: assertEquals("VIN123", readApollo(result, "getVin"))
    - Use nullable-safe DTO assertions, e.g. result.body?.message.
    - If source uses dataAssertNoErrors(), ApolloResponse data = null throws ApolloException before nullable mapper safe-calls; assert that thrown behavior or use a verified non-null data fixture with selected nullable fields.

    6. COMPILE-SAFE APOLLO CONSTRAINTS:
    - Use verified type names inside Class.forName strings and escape nested classes with \\$.
    - Create project-model fixtures matching verified non-null requirements.
    - Do not import/instantiate an Apollo generated type unless generated source/test classpath availability is verified.
    - For large mappers, cover representative success, null/empty, list conversion, and error/default branches rather than speculative exhaustive generated-type shapes.
    """

FRAMEWORK_TESTING_BLUEPRINTS = """
    --- GENERIC FRAMEWORK TESTING BLUEPRINTS ---
    Apply only when source/dependency context proves the framework is present.

    1. ROOM:
    - For DAO/database behavior, use Room.inMemoryDatabaseBuilder(ApplicationProvider context, Database::class.java).allowMainThreadQueries() when Room test dependencies are verified; close the database in teardown.
    - Test public DAO insert/upsert/query/delete behavior; exclude generated Room implementation classes.
    - For Flow DAO methods, collect with first()/toList() under runTest.

    2. HILT / DAGGER MODULES:
    - @Binds interface modules are compile-time DI declarations. Do not generate reflection-only tests solely for their signatures unless a coverage policy explicitly requires it.
    - For @Provides methods, instantiate the authored module/object and call provider methods with real/mocked parameters where possible.
    - Test Hilt injection/binding behavior only in verified Hilt component tests. Exclude generated Dagger/Hilt factories from Kover rather than testing generated code.
    - Use @HiltAndroidTest only for verified component/UI injection scenarios; use local JVM tests for plain provider logic.

    3. RETROFIT / OKHTTP / AUTHENTICATOR / INTERCEPTOR:
    - For OkHttp Interceptor/Authenticator, build real Request/Response objects with dummy URLs and mock Chain/Route only as needed.
    - Verify headers, token refresh, response/error branches, and null-authentication behavior.
    - For Retrofit API interfaces, use contract/reflection annotation checks only when annotations are verified, or mock the interface at its boundary.

    4. APOLLO GRAPHQL:
    - Follow APOLLO_MAPPER_BLUEPRINTS for generated responses/inputs.
    - For ApolloClient wrappers, mock the client execution boundary or isolate mapper/error branches.
    - Test ApolloHttpException, ApolloNetworkException, ApolloParseException, and generic ApolloException only when source explicitly handles them.

    5. ANDROID CAR / CAR UI:
    - Use Robolectric when code calls android.util.Log, Context/resources, Views, or Car UI widgets.
    - For verified Java static CarUi APIs such as CarUi.requireToolbar(activity), use MockedStatic with the exact Activity instance and close it in teardown. For navigation-focused Fragments, mock CarUi.requireToolbar instead of forcing an unsupported real toolbar host.
    - If source reads toolbar.progressBar, mock ToolbarController and the returned ProgressBarController; stub progressBar before attach/resume and verify the returned controller directly.
    - For android.car constants/simple permission arrays, use plain JUnit assertions.
    - Mock CarPropertyManager/service boundaries and verify public mapping/error behavior. CarPropertyManager.getProperty returns CarPropertyValue<T>; use that concrete wrapper.
    - For overloaded CarPropertyManager.setProperty, use typed Mockito-Kotlin matchers matching the verified source overload.

    6. WORKMANAGER / SERVICES / RECEIVERS:
    - Prefer extracted helper logic.
    - For Worker tests, use WorkManager test APIs only when dependencies are verified; otherwise test verified constructor/helper paths with mocked Context/WorkerParameters.
    - For BroadcastReceiver, use a Robolectric/ApplicationProvider Context and Intent; assert started service/work effects through observable collaborators.

    7. SHARED PREFERENCES / JSON / APPAUTH:
    - Use ApplicationProvider SharedPreferences and clear them between tests.
    - Use real JSONObject/JSONArray; cover missing keys, empty arrays, malformed values, and round-trip behavior where public APIs permit.
    - For simple unauthorized/empty AuthState storage paths, use real AuthState().jsonSerializeString().
    - For authorized AuthState branches, prefer an injected fake/public seam. Use reflection/private-field replacement only as a last resort when no seam exists and the field/type is verified; never invent private members.
    - For AppAuth discovery, use complete valid OIDC JSON when that path is tested: issuer, authorization_endpoint, token_endpoint, jwks_uri, response_types_supported, subject_types_supported, id_token_signing_alg_values_supported.
    - For token exchange/refresh, prefer real builder-created AuthorizationRequest, AuthorizationResponse, TokenRequest, AuthorizationServiceConfiguration, AuthorizationServiceDiscovery, and compatible TokenResponse/AuthState fixtures when constructors/contracts are verified.
    - If source catches AppAuth exceptions, assert stable fallback behavior: clear storage, no persist, null result, or no-crash.
    - Under Robolectric, browser authorization may throw ActivityNotFoundException when no browser is registered; test a verified no-browser branch or assert the local exception while verifying prior observable work.
    - Use typed Mockito-Kotlin matchers for overloaded AppAuth methods, e.g. update(any<TokenResponse?>(), any<AuthorizationException?>()).
    - If AppAuth resource enablement needs it, configure appAuthRedirectScheme only in the affected module.

    8. FIREBASE / TIMBER / LOGGING:
    - Mock Firebase wrappers/helpers or verify calls at the wrapper boundary.
    - Treat logging as a side effect; assert it only through a source-exposed wrapper/helper contract.
    - When Android Log is unavailable in local JVM, use Robolectric or wrap/mock the logging dependency.
    """

CORE_GENERATION_RULES = """
    Core rules:
    - Start every generated test file with package <same_as_source_under_test> as the first statement (before imports).
    - Generate JUnit4 Kotlin tests only; do not import JUnit5 assertions/runners.
    - Maximize meaningful public-API line/branch coverage, including success, null/error, state, and edge paths.
    - Use static source bug-hunting targets as test design input; keep valid bug-revealing assertions.
    - Verified source/dependency context is authoritative for APIs, constructors, resources, imports, fields, enum values, packages, nullability, Gradle dependencies, and SDK bounds.
    - Choose the smallest reliable style: plain JUnit for pure Kotlin; Mockito at dependency boundaries; Robolectric for Android resources/views/Log; coroutine-test for suspend/Flow; Hilt only for verified DI integration.
    - Prefer deterministic collaborators, test dispatchers, fake clocks, controlled producers, and mocked I/O boundaries.
    - For uncertainty, assert the nearest observable public contract conservatively; never invent APIs/types/classes/packages/resources.
    - Use JVM-safe test method names, including inside Kotlin backticks; use words such as bodyTripList instead of body.tripList.
    - Private methods are coverage consequences, not direct test targets.
    - Do not call, reflect on, spy on, or otherwise invoke private methods directly.
    - A private-method coverage gap is eligible only when one selected public method reaches it through deterministic, verified setup.
    - If that public path requires uncontrolled Dispatchers.IO, real delays, static construction, framework behavior, or unavailable declarations, defer that gap.
    """

TEST_QUALITY_RULES = """
    Test quality rules:
    - Each test must verify a public contract: mapping, branch, error/null path, state change, dependency interaction, visible UI effect, navigation, or verified bug risk.
    - Derive expected values from arranged inputs, source constants, or verified library behavior.
    - Use precise value/field assertions, interaction verification, or exception assertions as the main signal.
    - Prefer 3–4 strong scenario/table-style tests that cover success, null/error, boundaries, and representative variants over many weak tests.
    - One strong test may cover several related simple functions if assertions remain specific and failure diagnosis stays clear.
    - Prefer compact compile-safe coverage when APIs are generated or uncertain.
    - Do not replace lifecycle/navigation/observer/state-transition/collaborator tests with constructor-only or assert-not-null tests when a stronger verified public path exists.
    - Do not claim behavior is covered elsewhere unless current repository context shows a compiling test for it.
    """

KOTLIN_ANDROID_TEST_RULES = """
    Kotlin/Android rules:
    - Member extensions inside an object/class require both receivers: with(Helper) { receiver.ext(args) }. Use verified top-level extension imports for top-level declarations.
    - Example: object JLUtils { fun Context.getIntentActivities(i: Intent) } => with(JLUtils) { context.getIntentActivities(i) }.
    - Mockito-Kotlin is the default normal mocking style: mock<T>(), whenever, verify, typed any<T>(), eq, argumentCaptor, doAnswer/doThrow.
    - For Unit/void methods, use doThrow/doAnswer(...).`when`(mock).method(...); use whenever/Mockito.`when` only for value-returning calls.
    - Keep one normal mocking style per file. Java MockedStatic may coexist only for verified Java static/@JvmStatic calls.
    - Mockito @Mock fields are not initialized by Robolectric alone; use MockitoAnnotations.openMocks(this) or create mocks with mock().
    - For suspend calls that catch exceptions internally, assert stable observable result/interaction after runTest. Use assertThrows only when source visibly rethrows.
    - In JUnit4, verify no-throw behavior by direct invocation or try/fail. Do not use JUnit5 assertDoesNotThrow.
    - On matcher inference errors, inspect the highlighted call first; choose typed matchers for the exact overload before adding casts/changing return types.
    - On unresolved references, use verified source/search declarations; verify whether Outer.Inner is actually a top-level class.
    - For hard-coded dispatcher work with long real delays, assert a small observable effect or use deterministic scheduler control; do not wait in real time.
    - For static Java API restubbing, use the already-open MockedStatic receiver; do not open competing static mocks for the same class in one lifecycle.
    - Do not stub Kotlin object/framework helper calls with whenever(Object.method(any())) when matchers invoke real non-null Kotlin parameters. Use real Robolectric state, a verified seam, or another public branch.
    - Keep Mockito imports/style consistent: Mockito.`when` requires org.mockito.Mockito; with Mockito-Kotlin prefer whenever for normal mocks and MockedStatic.when for static mocks.
    - Nullable Boolean assertions use assertEquals(true/false, value) or assertNull.
    """

REPAIR_RULES = """
    Repair rules:
    - Fix the reported root-cause group in the generated test file; preserve production code and unrelated valid tests.
    - For batch Kotlin compile failures, resolve every listed error with minimal import/type/signature/helper changes while preserving test intent.
    - For a precise compiler/API/import error, apply the verified direct fix before changing strategy.
    - Use source/search/declarations as authority; replace unresolved symbols with verified alternatives.
    - Preserve valid coverage and edge-case intent. For runtime/JUnit failures, repair setup first, then align expectations with verified behavior or verified bug-hunting intent.
    - On Mockito WantedButNotInvoked, inspect source branch conditions and lifecycle/callback reachability; do not weaken positive verify(...) to never().
    - On TooManyActualInvocations, do not blindly change times(1) to the observed count. Clear setup interactions before action, verify a stable effect, or use atLeastOnce() only when repeated calls are valid source behavior.
    - On assertion failure, do not replace an exercised expression with an expected literal/source constant. Keep the assertion tied to captured navigation, visible view state, emitted state, or collaborator interaction.
    - For BuildConfig gates, align the test with the actual variant or drive a verified callback/public path.
    - For Fragment Flow/Lifecycle failures, attach/start/resume with a real lifecycle owner before changing StateFlow/observer state; prevent pre-controller navigation emissions.
    - For delegated ViewModel failures, do not assume @BindValue replaces the provider result; drive the real instance or repair its Hilt graph.
    - For CarUi progress failures, stub toolbar.progressBar before attach/resume and verify the returned ProgressBarController.
    - For Apollo dataAssertNoErrors failures, treat data = null as a thrown ApolloException unless a verified non-null data fixture exists.
    - For complex Fragment repair loops, reduce to fewer high-confidence lifecycle/public-contract tests instead of adding speculative scenarios.
    - With limited context, simplify to the nearest compile-valid observable public contract.
    - Keep imports complete, changes minimal, and valid existing test structure intact.
    """


REQUIRED_DEPENDENCIES = {
    "libs.junit": 'testImplementation(libs.junit)',
    "org.mockito.kotlin:mockito-kotlin": 'testImplementation("org.mockito.kotlin:mockito-kotlin:5.2.1")',
    "org.mockito:mockito-inline": 'testImplementation("org.mockito:mockito-inline:5.2.0")',
    "org.robolectric:robolectric": 'testImplementation("org.robolectric:robolectric:4.13")',
    "org.jetbrains.kotlinx:kotlinx-coroutines-test": 'testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.10.2")',
    "org.jetbrains.kotlin:kotlin-test": 'testImplementation("org.jetbrains.kotlin:kotlin-test")',
    "androidx.test:core": 'testImplementation("androidx.test:core:1.5.0")',
    "androidx.fragment:fragment-testing": 'testImplementation("androidx.fragment:fragment-testing:1.6.2")',
    "androidx.navigation:navigation-testing": 'testImplementation("androidx.navigation:navigation-testing:2.8.9")',
    "com.google.dagger:hilt-android-testing": 'testImplementation("com.google.dagger:hilt-android-testing:<project-hilt-version>")',
    "androidx.arch.core:core-testing": 'testImplementation("androidx.arch.core:core-testing:2.2.0")',
    "org.json:json": 'testImplementation("org.json:json:20230227")',
    "org.jetbrains.kotlinx:kotlinx-coroutines-play-services": 'implementation("org.jetbrains.kotlinx:kotlinx-coroutines-play-services:1.7.3")',
    "kotlin:test": 'testImplementation(kotlin("test"))',
}

SAFE_KOVER_EXCLUDE_CLASSES = [
    "*$*$inlined$*",
    "*BuildConfig*",
    "*$map$*",
    "*$stateIn$*",
    "*_MembersInjector",
    "*_HiltModules*",
    "*_Factory",
    "*.Hilt_*",
    "*.HiltWrapper_*",
    "*_Factory*",
    "*_Provides*Factory*",
    "*.*Binding",
    "*.*BindingImpl",
    "hilt_aggregated_deps.**",
    "*_Impl",
    "*_Impl*"
]

UNSAFE_KOVER_EXCLUDE_CLASSES = [
    "*$1",
    "*$2",
    "*$3",
]

ORCHESTRATION_ONLY_RAG_TAGS = frozenset({
    "fixture_playbooks", "coverage_strategy", "verified_observer_and_click",
    "verified_observer_emission", "verified_ui_click", "verified_dialog_callback",
    "verified_menu_callback", "verified_activity_result_callback",
    "verified_coroutine_completion", "attached_hilt_fragment", "dialog_callback_chain",
    "inject_lateinit_field", "fragment_triggers_uncontrolled_viewmodel_io",
    "delegated_viewmodel_fragment", "navigation_fragment", "fixture_strategy",
})

VIEWMODEL_EXCLUSIVE_RAG_TAGS = frozenset({"viewmodel", "hilt_viewmodel", "livedata", "coroutines_flow"})
FRAGMENT_EXCLUSIVE_RAG_TAGS = frozenset({
    "android_fragment", "hilt_fragment", "plain_fragment", "dialog_fragment",
    "navigation_fragment", "delegated_viewmodel_fragment",
})

ORCHESTRATION_RULE_SOURCES = frozenset({
    "FRAGMENT_VIEWMODEL_PLAYBOOKS",
    "INCREMENTAL_COVERAGE_RULES",
    "INCREMENTAL_EXECUTION_PLAYBOOKS",
})

FIXTURE_PLAYBOOK_UI_SOURCE_CATEGORIES = frozenset({
    "android_fragment", "hilt_fragment", "delegated_viewmodel_fragment",
    "verified_observer_and_click", "verified_ui_click", "dialog_callback_chain",
})

RULE_SOURCE_NAMES = (
    "ANDROID_BLUEPRINTS",
    "APOLLO_MAPPER_BLUEPRINTS",
    "FRAMEWORK_TESTING_BLUEPRINTS",
    "CORE_GENERATION_RULES",
    "TEST_QUALITY_RULES",
    "KOTLIN_ANDROID_TEST_RULES",
    "REPAIR_RULES",
    "FIXTURE_PLAYBOOK_VERIFIED_UI_CLICK",
    "FIXTURE_PLAYBOOK_VERIFIED_OBSERVER_AND_CLICK",
    "FIXTURE_PLAYBOOK_VERIFIED_OBSERVER_EMISSION",
    "FIXTURE_PLAYBOOK_VERIFIED_DIALOG_CALLBACK",
    "FIXTURE_PLAYBOOK_VERIFIED_MENU_CALLBACK",
    "FIXTURE_PLAYBOOK_VERIFIED_ACTIVITY_RESULT_CALLBACK",
    "FIXTURE_PLAYBOOK_CONTROLLED_EXCEPTION_PATH",
    "FIXTURE_PLAYBOOK_CONTROLLED_COUNTDOWN_CALLBACK",
    "FIXTURE_PLAYBOOK_VERIFIED_COROUTINE_COMPLETION",
    "FIXTURE_PLAYBOOK_ATTACHED_HILT_FRAGMENT",
    "FIXTURE_PLAYBOOK_PUBLIC_METHOD",
    "FIXTURE_PLAYBOOK_VIEWMODEL_PUBLIC_METHOD",
    "FIXTURE_PLAYBOOK_VIEWMODEL_SYNC_PUBLIC",
    "FIXTURE_PLAYBOOK_BRANCH_PROBE",
    "FRAGMENT_VIEWMODEL_PLAYBOOKS",
    "INCREMENTAL_COVERAGE_RULES",
    "INCREMENTAL_EXECUTION_PLAYBOOKS",
)
