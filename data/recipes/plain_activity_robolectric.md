---
id: plain_activity_robolectric
triggers: [android_activity]
---

# Plain Activity Robolectric (non-Hilt)

## MUST

- `@RunWith(RobolectricTestRunner::class)` and `@Config(sdk=[...])` when SDK/resources matter.
- Activity bootstrap order (AppCompat / `setContentView` in `onCreate`):

```kotlin
val controller = Robolectric.buildActivity(Cut::class.java)
val activity = controller.get()
activity.setTheme(androidx.appcompat.R.style.Theme_AppCompat)
controller.setup() // or .create() / .start() / .resume() as needed
```

- Theme **before** `setup()` / `create()` when `onCreate` inflates AppCompat layouts; ActivityController has no `.theme()`.
- Context for dialog/UI builders: `ApplicationProvider.getApplicationContext()` or the built Activity — not `mockk<Context>(relaxed=true)` into a real builder.
- Act only **public** lifecycle / APIs (`create`/`start`/`resume` via controller, public methods).

## MUST NOT

- `@HiltAndroidTest` / `HiltAndroidRule` / `HiltTestApplication` on a type that is not `@AndroidEntryPoint`.
- `ActivityScenario.launch` / `AndroidJUnit4` (instrumentation).
- Direct `activity.onCreate(...)` / `onStart` / `onResume` on the Activity instance.
- `setTheme` **after** `create()` when `onCreate` calls `setContentView` (too late → Theme.AppCompat crash).
- Service / Receiver construction in this playbook (use `worker_service_receiver`).

## Ordered write steps (1..N)

1. Imports / `@RunWith(RobolectricTestRunner::class)` / `@Config`.
2. Arrange: `buildActivity` → `get()` → `setTheme(Theme_AppCompat)` before lifecycle.
3. Stub collaborators / shadows for THIS branch.
4. Act: `controller.setup()` / `create()` / `start()` / `resume()` or one public Cut API.
5. Assert public state, started intents (`shadowOf(activity).nextStartedActivity`), or collaborator verify.

## Minimal sketch

```kotlin
@RunWith(RobolectricTestRunner::class)
class CutActivityTest {
  @Test fun onCreate_runs() {
    val controller = Robolectric.buildActivity(Cut::class.java)
    val activity = controller.get()
    activity.setTheme(androidx.appcompat.R.style.Theme_AppCompat)
    controller.setup()
    // assert public state
  }
}
```

## Related api_doc

- `data/api_doc/robolectric_4_13_api_index.json`
