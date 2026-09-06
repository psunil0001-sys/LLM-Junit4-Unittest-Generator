---
id: hilt_service_full_harness
triggers: [hilt_service]
---

# Hilt @AndroidEntryPoint Service — full Robolectric + Hilt test harness

## MUST (non-skippable)

- `@HiltAndroidTest` + `HiltAndroidRule`
- `@RunWith(RobolectricTestRunner::class)`
- `@Config(application = HiltTestApplication::class)`
- `hiltRule.inject()` in `@Before` (before starting the Service)
- `@BindValue` / `@TestInstallIn` for **every** Hilt `@Inject` field used by the Service logic:
  - types come from `SOURCE imports (Glob/Read)`
  - include any qualifiers (example: `@ApplicationScope` for `CoroutineScope`)

- Act: start the Service via a path that runs Hilt `onCreate` inject **before** `onStartCommand`:
  1. Prefer `context.startService(Intent(context, Cut::class.java))` when BindValue mocks observe `onStartCommand` effects.
  2. **Proven gap (library Robolectric):** `startService` can return a `ComponentName` without invoking Hilt inject / `onStartCommand`. Then use:
     `Robolectric.buildService(Cut::class.java, intent).create().startCommand(0, 0)` under `@Config(application = HiltTestApplication::class)`.
- Module must include `testImplementation("com.google.dagger:hilt-android-testing:…")` + `kspTest` compiler; prefer_not_testable for Hilt Service only after that classpath is confirmed missing.
- When `@BindValue` replaces types also bound by a `@Binds` module (e.g. `ApiModule.BindsApiModule`, `JLRepoModule`), `@UninstallModules` that module and `@BindValue` **all** of its bound types (AuthRepository **and** JourneyApi / AuthSource). See `BootCompleteForegroundServiceTest` for a multi-module `@UninstallModules` pattern.

- For suspend work launched in `onStartCommand` (via injected `CoroutineScope`):
  - use `kotlinx.coroutines.test.runTest { }` in each test method
  - ensure the injected `CoroutineScope` runs synchronously under the test dispatcher
  - then assert/verify repository or object-seam calls after the coroutine work completes

## MUST NOT

- Starting the Service without Hilt harness annotations/rules
- Inventing a non-Hilt Service subclass or editing `src/main` to expose test seams

## Ordered write steps (1..N)

1. Imports / runner + Hilt annotations.
2. Arrange:
   - `@get:Rule val hiltRule = HiltAndroidRule(this)`
   - `@BindValue` fields for each SOURCE `@Inject` dependency (including qualifiers).
   - `@UninstallModules` for `@Binds` modules that conflict with those `@BindValue`s.
   - `@Before` call: `hiltRule.inject()`.
3. Stub:
   - configure mockk lane stubs on bound dependencies for this branch.
4. Act:
   - `val intent = Intent(context, Cut::class.java).apply { action = "SOURCE_ACTION"; putExtra("key", value) }`
   - Try `context.startService(intent)` first.
   - **Fallback:** `Robolectric.buildService(Cut::class.java, intent).create().startCommand(0, 0)` when inject/onStartCommand does not run.
   - Wrap the lifecycle trigger in `runTest { }` if the Service launches suspend work.
5. Assert:
   - verify collaborators (publicly observable effects, and/or static/object seam calls you stub).

## Related api_doc
- `data/api_doc/hilt_android_testing_2_49_api_index.json`
- `data/api_doc/robolectric_4_13_api_index.json`

