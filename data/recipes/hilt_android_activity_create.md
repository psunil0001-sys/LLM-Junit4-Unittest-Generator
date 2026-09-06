---
id: hilt_android_activity_create
triggers: [hilt_android_activity]
---

# Hilt @AndroidEntryPoint Activity — Robolectric `.create()`

## MUST (non-skippable)

- `@HiltAndroidTest` + `HiltAndroidRule` + `@Config(application = HiltTestApplication::class)`.
- `hiltRule.inject()` in `@Before` **before** any `.create()`.
- `@BindValue` / `@TestInstallIn` for **every SOURCE `@Inject` dependency**
  (types from SOURCE imports (Glob/Read)) **before** create.
- If SOURCE uses `CarUi` / `requireToolbar`: `mockkStatic(CarUi::class)` (or mockStatic) + stub `requireToolbar(any())` before create; tear down in `@After`.
- If SOURCE uses `setContentView` / AppCompat: set theme on the **Activity** before `create()`:

```kotlin
val controller = Robolectric.buildActivity(Cut::class.java)
val activity = controller.get()
activity.setTheme(androidx.appcompat.R.style.Theme_AppCompat)
controller.create()
```

  Prefer `Theme_AppCompat` (or a known AppCompat descendant). Project `R.style.AppTheme` only if it extends AppCompat.

## MUST NOT

- `.create()` then `activity.<injectField> = mock`.
- Claim `hiltRule.inject()` binds Activity `@Inject` fields (it injects the **test** class; Activity is injected at create from the graph).
- Claim Hilt N/A while planning `.create()`.
- Rely on a second recipe view for CarUi — apply CarUi static stub from **this** playbook.
- `ActivityController.setTheme(...)` / `buildActivity(...).setTheme(...).create()` — **does not exist** on Robolectric 4.13.
- `@Config(theme=...)` — **no such parameter** on Robolectric 4.13 `@Config`.
- `activity.setTheme(...)` **after** `.create()` when `onCreate` calls `setContentView` (too late → Theme.AppCompat crash).

## When to use

- SOURCE is `@AndroidEntryPoint` Activity; delta lines need real `onCreate` via `.create()`.

## Ordered write steps (1..N)

1. Imports / runner: `@HiltAndroidTest`, `@RunWith(RobolectricTestRunner::class)`,
   `@Config(application = HiltTestApplication::class)`, `HiltAndroidRule`, `@BindValue`, MOCKING LANE, CarUi static mock if needed.
2. Arrange: `@get:Rule val hiltRule = HiltAndroidRule(this)`; `@BindValue` for each SOURCE `@Inject`; `mockkStatic(CarUi::class)` if CarUi;
   call `hiltRule.inject()` in `@Before` before create.
3. Stub: lane stubs on `@BindValue` deps + `CarUi.requireToolbar(any())` **before** create.
4. Act (theme **before** create):
   `buildActivity` → `get()` → `activity.setTheme(Theme_AppCompat)` → `create()` —
   no inject-field assign after create; no `ActivityController.setTheme`; no `@Config(theme=...)`.
5. Assert: onCreate observable (public view / collaborator verify).

## Forbidden

- create-then-assign inject fields; create without Hilt harness; invent Hilt N/A;
  controller `.setTheme` chain; `@Config(theme=...)`; theme after create for AppCompat `setContentView`.

## Minimal sketch

```kotlin
@HiltAndroidTest
@RunWith(RobolectricTestRunner::class)
@Config(application = HiltTestApplication::class)
class CutActivityTest {
  @get:Rule var hiltRule = HiltAndroidRule(this)
  @BindValue @JvmField
  val dep: Dep = mockk()

  @Before fun setUp() {
    hiltRule.inject()
  }

  @Test fun onCreate_runs() {
    val controller = Robolectric.buildActivity(Cut::class.java)
    val activity = controller.get()
    activity.setTheme(androidx.appcompat.R.style.Theme_AppCompat)
    controller.create()
    // assert — no activity.dep = ...
  }
}
```

## Prefer not_testable when

- Module cannot host `@HiltAndroidTest` on the JVM unit classpath.

## Related api_doc

- `data/api_doc/hilt_android_testing_2_49_api_index.json`
- `data/api_doc/robolectric_4_13_api_index.json`
- `data/api_doc/car_ui_lib_2_6_0_api_index.json` (signatures only; order is above)
