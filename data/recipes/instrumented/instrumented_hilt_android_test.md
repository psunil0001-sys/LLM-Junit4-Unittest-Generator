---
id: instrumented_hilt_android_test
triggers: [android_foreground_service, android_work_manager, instrumented]
---

# Hilt @HiltAndroidTest — on-device androidTest harness

## MUST (non-skippable)

- Output path: `src/androidTest/java/.../<Cut>InstrumentedTest.kt` (mirror source package).
- `@HiltAndroidTest` + `@RunWith(AndroidJUnit4::class)` — **never** `@RunWith(RobolectricTestRunner::class)`.
- `@get:Rule(order = 0) val hiltRule = HiltAndroidRule(this)`; call `hiltRule.inject()` in `@Before`.
- Shared runner when the module uses `:testsupport`: `com.HiltTestRunner` + androidTest `HiltTestApplication` manifest (`tools:replace`). Prefer shared hosts (`HiltContainerHostActivity`, `HiltToolbarHostActivity`, `PlainHiltActivity`) over per-module copies.
- Use `ActivityScenario.launch(...)` for lifecycle — not `Robolectric.buildActivity`. For fragments see `instrumented_hilt_fragment_test`; for FGS see `instrumented_foreground_service_test`.
- `@BindValue` / `@TestInstallIn` for every SOURCE `@Inject` dependency before launch.
- Gradle verify (when device present): `connected*AndroidTest` + JaCoCo (`instrumentedCoverageReport`).
- Close **instrumented-only** gaps (FGS, WorkManager constraints, system services) — not lines already green in Kover unit reports.

## MUST NOT

- Write tests under `src/test` or use Robolectric shadows for this pass.
- Duplicate unit/Robolectric scenarios already covered by Kover (same method + branch intent).
- `.create()` on Robolectric `ActivityController` in androidTest.
- Skip `@HiltAndroidTest` while using `@AndroidEntryPoint` CUT on device.
- Assign `@Inject` fields on the Activity after launch — bind via Hilt test modules first.

## When to use

- Hybrid instrumented pass after unit pass, or `test_mode=instrumented`.
- SOURCE needs real Android framework: FGS, WorkManager, notifications, hardware, multi-process.

## Ordered write steps (1..N)

1. File + imports: androidTest package; `@HiltAndroidTest`, `@RunWith(AndroidJUnit4::class)`, `HiltAndroidRule`, `ActivityScenario`, `@BindValue`, MockK/Mockito as needed.
2. Rules: `@get:Rule(order = 0) val hiltRule = HiltAndroidRule(this)`; prefer `ActivityScenario` only — avoid deprecated `ActivityTestRule`.
3. Arrange: `@BindValue` for each SOURCE `@Inject`; `hiltRule.inject()` in `@Before`.
4. Act: `ActivityScenario.launch(Cut::class.java)` (or `FragmentScenario` + nav host); trigger the delta behavior on device.
5. Assert: observable side effects (UI, service start, WorkManager enqueue verify with `WorkManagerTestInitHelper` when needed).
6. Gradle: pipeline runs `connected*AndroidTest` + JaCoCo when adb device available; otherwise generate-only with warning.

## Forbidden

- Robolectric runner/config in androidTest; src/test output; inventing non-Hilt Activity launch;
  duplicating closed Kover unit scenarios; verify tasks without device in hybrid warn-only mode.

## Minimal sketch

```kotlin
@HiltAndroidTest
@RunWith(AndroidJUnit4::class)
class CutInstrumentedTest {
  @get:Rule(order = 0)
  val hiltRule = HiltAndroidRule(this)

  @BindValue @JvmField
  val dep: Dep = mockk()

  @Before
  fun setUp() {
    hiltRule.inject()
  }

  @Test
  fun launches_onDevice() {
    ActivityScenario.launch(CutActivity::class.java).use { scenario ->
      scenario.onActivity { /* assert delta behavior */ }
    }
  }
}
```
