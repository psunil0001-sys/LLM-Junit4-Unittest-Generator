---
id: instrumented_hilt_fragment_test
triggers: [hilt_fragment, android_fragment, android_navigation, android_ui]
---

# Hilt Fragment — on-device ActivityScenario androidTest

## MUST (non-skippable)

- Output: `src/androidTest/java/.../<Cut>InstrumentedTest.kt` mirroring source package.
- `@HiltAndroidTest` + `@RunWith(AndroidJUnit4::class)` — never Robolectric.
- `@get:Rule(order = 0) val hiltRule = HiltAndroidRule(this)`; `hiltRule.inject()` in `@Before`.
- `@BindValue` / `@TestInstallIn` for every SOURCE `@Inject` field the fragment (or its `@HiltViewModel`) reads.
- **Shared harness (`:testsupport`)** — prefer these over per-module copies:
  - Runner: `com.HiltTestRunner`
  - Toolbar-only host: `HiltToolbarHostActivity`
  - `HostDrawerActivity` + drawer host: `HiltContainerHostActivity` (`fragmentContainerId` for `commitNow`)
  - Non-delegate host (negative `onAttach`): `PlainHiltActivity`
  - Theme: `@style/HiltHostTheme` (from testsupport; parent `AppTheme`)
- **Tier A** (attach / layout / shared ViewModels): `ActivityScenario.launch(HiltContainerHostActivity::class.java)` or `HiltToolbarHostActivity` then
  `supportFragmentManager.commitNow { add(activity.fragmentContainerId /* or android.R.id.content */, CutFragment(), tag) }`.
- **Tier B** (real NavHost / deep-link destinations): module-local `HiltNavHostActivity` + `hilt_nav_test_graph` when the CUT needs graph destinations. If only `findNavController().navigate/popBackStack` is required and no graph destinations are under test, Tier A + `Navigation.setViewNavController(fragment.requireView(), mockk(relaxed = true))` is enough.
- Consumer androidTest manifest: `android:name="dagger.hilt.android.testing.HiltTestApplication"` with `tools:replace`; activities merge from `:testsupport` (plus any module-local NavHost activity).
- Assert public surface: Espresso `onView(...)` for layout `@+id/*`; collaborator effects via `verify { @BindValue mock }`. Prefer not reading private fragment fields; when CarUi adapters expose no Espresso-friendly children, assert via adapter public API on the main thread inside `onActivity`.

## Module harness (source-conditioned)

Write **only** files INSTRUMENTED HARNESS PATHS lists. Reuse `:testsupport` hosts; do not invent a second runner.

Always for `@AndroidEntryPoint` Fragment (or CarUi toolbar fragments):

- `build.gradle.kts` `defaultConfig.testInstrumentationRunner = "com.HiltTestRunner"`
- androidTest `AndroidManifest.xml`:
  `android:name="dagger.hilt.android.testing.HiltTestApplication"` (+ optional `android:theme="@style/HiltHostTheme"`).
- Deps: `hilt-android-testing:2.49`, `espresso-core`, `androidx.test.ext:junit`, `androidx.test:runner`, `mockk-android`. Apply `gradle/testgen-coverage.gradle` (pulls `:testsupport` + META-INF excludes).

Only if SOURCE needs it:

- `findNavController` / `NavDeepLinkRequest` with real destinations: module `HiltNavHostActivity` + `hilt_nav_test_activity.xml` (`@+id/nav_host_fragment`). Activity **must** `findViewById` the NavHost after `setContentView`.
- Library modules: androidTest layouts/ids are on ``com.<module>.test.R``, **not** production `R`. Using production `R.layout.hilt_nav_test_activity` → `Unresolved reference` at compile.
- `hilt_nav_test_graph` startDestination = CUT; stub dest per URI when SOURCE navigates to undeclared deep links.
- `onAttach` throws unless `HostDrawerActivity`: `PlainHiltActivity`.
- `@BindValue` replacing `@Binds` types: `@UninstallModules(...)` **and** BindValue every remaining bind in those modules.
- Platform / vehicle-property helpers used from SOURCE (`CarManager.getDistance*`, `LocationUtil.getCurrentLocation`, similar): **`mockkObject` / `mockkStatic` on instrumented** — do not call real `CarPropertyManager` / GPS in androidTest; AAOS emulators often lack the property or return null and crash.
- Map / WebView / heavy native UI: stub location + repo flows so the fragment reaches RESUMED; assert toolbar/loading/zoom controls that are layout ids.
- `Firebase.crashlytics` / `Firebase.analytics`: `mockkStatic(FirebaseApp::class)` in `@Before`. **Do not** `FirebaseApp.initializeApp` in library androidTest.
- `AlertDialogBuilder` + dialog buttons: on AAOS emulators CarUi dialogs often never take
  window focus — do **not** rely on Espresso's default root (times out with
  `RootViewWithoutFocusException`). Walk `WindowManagerGlobal.mViews` and `performClick`
  the matching `TextView` on the main thread (see the module dialog-root helper pattern).

Never `@Config(application = HiltTestApplication::class)` on the **test class** — that is Robolectric/`src/test` only.

## Full coverage checklist (instrumented lane)

For each Hilt/`Fragment` CUT, aim to close these paths when present in SOURCE:

| SOURCE path | Host | Assert |
|---|---|---|
| `onAttach` success (`HostDrawerActivity`) | `HiltContainerHostActivity` | fragment added / `isAdded` |
| `onAttach` failure (non-delegate) | `PlainHiltActivity` | Use `commitNow` + `assertThrows(IllegalArgumentException)` (Hilt inject runs in `super.onAttach` — bare `fragment.onAttach(activity)` NPEs). When SOURCE checks the delegate **after** `super.onAttach` and later uses null-delegate drawer/`!!` paths: inside the catch, `commitNow { remove(...) }` all FM fragments and `activity.finish()` before `ActivityScenario.close()`, and optionally `every { } just Runs` as a safety net. |
| `onDetach` / `onDestroyView` | Tier A | remove fragment; `!isAdded`, `view == null` |
| Layout inflation / observers / adapters | Tier A | Espresso on public `@+id/*`; month/list adapters update |
| Shared `activityViewModels` seed | Tier A | set StateFlow/LiveData **before** `commitNow` |
| `@HiltViewModel` via `activityViewModels()` (not only shared plain VMs) | Tier A (`HiltContainerHostActivity` / any `@AndroidEntryPoint` host) | Host creates the VM; `@BindValue` must satisfy that VM’s inject graph. Do not assume `by viewModels()` fragment scope. |
| EditText / `TextWatcher` / button clicks | Tier A | Mutate and click **inside** `onActivity` (main thread). Assert error/char-count labels on public `@+id/*`. |
| `findNavController` / dialog open | Tier B or ViewNavController | click; navigate/pop verified or dialog title |
| Dialog positive/negative callbacks | Tier B | `verify { repo.method(...) }` on `@BindValue` |
| `@Inject` / use-case side effects | Tier A/B | `verify { repo... }` / prefs |
| `android:visibility="gone"` controls | Tier A/B | make `VISIBLE` then click, or document skip |

## MUST NOT

- Write under `src/test`; use Robolectric shadows; duplicate unit/Kover scenarios for same branch intent.
- Call private fragment methods as the primary assertion strategy.
- Use `FragmentScenario.launchInContainer` / `launchFragmentInContainer` — lacks a Hilt/CarUi host.
- Assign `@Inject` fields after launch without Hilt bindings.
- Anonymous-object-implement interfaces that reference AppAuth types; use `mockk(relaxed = true)` instead.
- Hit real vehicle property / network / GPS APIs when a stub exists — prefer `mockkObject` + `@BindValue` Flows.
- Launch a plain non-CarUi activity when SOURCE calls `CarUi.requireToolbar`.
- Use production nav graph alone when SOURCE navigates to deep links not declared in module graph — provide `hilt_nav_test_graph` with stub destinations (or ViewNavController when only pop/navigate call sites matter).

## Ordered write steps

1. Ensure `:testsupport` harness is on the classpath (`testgen-coverage.gradle`); set shared `HiltTestRunner` FQCN; thin consumer manifest.
2. Imports: `@HiltAndroidTest`, `HiltAndroidRule`, `UninstallModules` when replacing `@Binds`, `ActivityScenario`, `commitNow`, `@BindValue`, `AndroidJUnit4`, Espresso, mockk.
3. Rules + `@Before` inject (+ Firebase / CarManager / LocationUtil stubs when SOURCE uses them).
4. Arrange mocks/fakes with `@BindValue`; seed shared ViewModels before attach when SOURCE collects flows in `onViewCreated`/`onCreateView`. When SOURCE uses `activityViewModels()` for a `@HiltViewModel`, the host Activity must be `@AndroidEntryPoint` (prefer `HiltContainerHostActivity`) so Hilt can create that VM.
5. Act: Tier A → launch shared host, `commitNow` add fragment; Tier B → NavHost activity; drive to `RESUMED`; invoke public UI paths from plan. EditText/`TextWatcher` mutations and clicks must run on the main thread (`onActivity`).
6. Assert Espresso / `verify { }` / lifecycle flags on the main thread (`onActivity`).

## Minimal sketch

**Class header (when SOURCE injects prefs + trips repo)**

```kotlin
@HiltAndroidTest
@RunWith(AndroidJUnit4::class)
@UninstallModules(PreferencesBindsModule::class, DataBindsModule::class)
class CutInstrumentedTest {
  @get:Rule(order = 0) val hiltRule = HiltAndroidRule(this)
  @BindValue @JvmField val userPreferencesRepository: UserPreferencesRepository = mockk(relaxed = true)
  @BindValue @JvmField val repository: CutRepository = mockk(relaxed = true)
}
```

**Tier A — attach / layout (delegate host)**

```kotlin
@Test
fun fragment_reaches_delta_onDevice() {
  ActivityScenario.launch(HiltContainerHostActivity::class.java).use { scenario ->
    scenario.onActivity { activity ->
      activity.supportFragmentManager.commitNow {
        add(activity.fragmentContainerId, CutFragment(), "cut")
      }
    }
    onView(withId(R.id.somePublicView)).check(matches(isDisplayed()))
  }
}
```

**findNavController without full graph**

```kotlin
scenario.onActivity { activity ->
  val f = activity.supportFragmentManager.findFragmentByTag("cut")!!
  Navigation.setViewNavController(f.requireView(), mockk(relaxed = true))
}
```

**Negative onAttach**

```kotlin
@Test
fun onAttach_rejects_non_delegate_host() {
  // Skip this test when SOURCE: super.onAttach then throw, plus onCreateView uses delegate!!.
  ActivityScenario.launch(PlainHiltActivity::class.java).use { scenario ->
    assertThrows(IllegalArgumentException::class.java) {
      scenario.onActivity { activity ->
        activity.supportFragmentManager.commitNow {
          add(android.R.id.content, CutFragment(), "cut")
        }
      }
    }
  }
}
```
