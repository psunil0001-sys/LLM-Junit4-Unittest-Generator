---
id: hilt_fragment_lifecycle
triggers: [hilt_fragment, closed_hilt_graph]
---

# Hilt @AndroidEntryPoint Fragment — Robolectric attach

## MUST

- `@HiltAndroidTest` + `HiltAndroidRule` + `@Config(application = HiltTestApplication::class)` + `hiltRule.inject()` before attach.
- `@BindValue` / `@TestInstallIn` for SOURCE collaborators (repos/use cases from SOURCE imports (Glob/Read)), **not** the `by viewModels()` / `activityViewModels()` ViewModel type (see companion `hilt_fragment_activity_viewmodels` when SOURCE uses those).
- Attach Cut to an `@AndroidEntryPoint` Activity **in the same module**:
  1. Prefer a **main** host when present (`LogActivity`, `SettingsHomeActivity`, …).
  2. Else use the module’s **`src/test` `HiltHostActivity`** (must be declared in `src/test/AndroidManifest.xml`).

```kotlin
val controller = Robolectric.buildActivity(HiltHostActivity::class.java) // or main HostActivity::class.java
val activity = controller.get()
activity.setTheme(androidx.appcompat.R.style.Theme_AppCompat)
controller.setup() // or .create() — both OK when theme is set on get() first
activity.supportFragmentManager.beginTransaction()
    .add(android.R.id.content, Cut(), "cut")
    .commitNow()
ShadowLooper.idleMainLooper()
```

- Theme: `activity.setTheme(Theme_AppCompat)` **before** `create()` / `setup()`.
- After `commitNow()`, call `ShadowLooper.idleMainLooper()` before asserting views / bindings.
- Act only **public** lifecycle / APIs after attach. CarUi / Nav / permissions stubs come from companion recipes when those tags are selected — do not invent a second attach strategy.
- If SOURCE `onAttach` requires `HostDrawerActivity` (throws `IllegalArgumentException("Activity must be HostDrawerActivity")`), the module **`src/test` `HiltHostActivity` MUST implement `HostDrawerActivity`** (DrawerLayout / NavigationView / FragmentContainerView + no-op `navigateTo*`). Creating/upgrading that **module-level** host + declaring it in `src/test/AndroidManifest.xml` is required; inventing a host **inside** `*Test.kt` is forbidden.
- When `@BindValue` replaces a type also `@Binds`-bound (e.g. `AuthRepository` via `ApiModule.BindsApiModule`), `@UninstallModules` that binds module and `@BindValue` **every** type it exposed (`AuthRepository`, `JourneyApi`, `AuthSource`).

```kotlin
@AndroidEntryPoint
class HiltHostActivity : AppCompatActivity(), HostDrawerActivity {
    // onCreate: create DrawerLayout / NavigationView / FragmentContainerView; setContentView(container)
    override fun getDrawerLayout() = drawerLayout
    override fun getNavigationView() = navigationView
    override fun getFragmentContainerView() = fragmentContainerView
    override fun navigateToLogBookPage() = Unit
    override fun navigateToManagedTripPage() = Unit
    override fun navigateToSettingsPage() = Unit
    override fun navigateToExportPage() = Unit
}
```

## MUST NOT

- `FragmentScenario.launchFragmentInContainer` — host is not `@AndroidEntryPoint`.
- Invent a one-off host Activity class inside the generated test file; use the module main host or existing `src/test` `HiltHostActivity` only (upgrade that host for Delegate when needed).
- Invent `launchFragmentInHiltContainer` unless that helper already exists in the module.
- Assign `@Inject` fields after attach; `@BindValue` the delegated ViewModel class.
- `@Config(theme=...)` / `ActivityController.setTheme` — neither exists on Robolectric 4.13.

## Ordered write steps (1..N)

1. Imports / runner + Hilt annotations (import module `HiltHostActivity` when using the test host).
2. Arrange: `hiltRule.inject()`; `@BindValue` SOURCE deps; `@UninstallModules` conflicting `@Binds` modules.
3. Stub MOCKING LANE on binds before attach (CarUi, DeviceModelImpl, …).
4. Act: host Activity `get()` → `setTheme` → `setup()` then `commitNow()` add Cut; `ShadowLooper.idleMainLooper()`.
5. Assert: public UI / verify collaborator — not private `_binding`.

## Prefer not_testable when

- Closed graph + no JVM stub seam.
- UI attach required and the module has **neither** a main `@AndroidEntryPoint` Activity **nor** a manifest-declared `src/test` `HiltHostActivity` (and, when SOURCE needs Delegate, that activity does not implement `HostDrawerActivity`).

## Related api_doc

- `data/api_doc/hilt_android_testing_2_49_api_index.json`
- `data/api_doc/robolectric_4_13_api_index.json`
