---
id: plain_fragment_activity_viewmodels
triggers: [delegated_viewmodel_fragment, android_fragment]
---

# Plain Fragment `viewModels()` / `activityViewModels()` (non-Hilt)

## MUST

- SOURCE is a **non-`@AndroidEntryPoint`** Fragment with `by viewModels()` / `activityViewModels()`.
- Harness: `@RunWith(RobolectricTestRunner::class)` + `@Config(sdk=[...])` when needed — **no** `@HiltAndroidTest` / `HiltAndroidRule` / `HiltTestApplication`.
- Attach Cut with **one** of:
  1. `FragmentScenario.launchFragmentInContainer(themeResId = androidx.appcompat.R.style.Theme_AppCompat) { Cut() }` (preferred for fragment-scoped `viewModels()`), or
  2. Robolectric host Activity + `supportFragmentManager` `commitNow()` add Cut (required for shared `activityViewModels()` on a real Activity `ViewModelStore`).
- Activity-scoped VM: seed/stub via the **host Activity** `ViewModelProvider` / test `ViewModelProvider.Factory` — never `@BindValue` / Hilt modules.

```kotlin
// activityViewModels() — seed the Activity store BEFORE commitNow add Cut
val controller = Robolectric.buildActivity(AppCompatActivity::class.java)
val activity = controller.get()
activity.setTheme(androidx.appcompat.R.style.Theme_AppCompat)
controller.setup()
val shared = ViewModelProvider(activity)[SharedVm::class.java]
// shared.set… / send… public APIs to arrange StateFlow/LiveData
activity.supportFragmentManager.beginTransaction()
    .add(android.R.id.content, Cut(), "cut")
    .commitNow()
ShadowLooper.idleMainLooper()
```

- After `commitNow()`, call `ShadowLooper.idleMainLooper()` before view assertions.
- Act only **public** entry points (`onCreateView`, `onViewCreated`, public APIs). Private helpers are reached only through those Acts.
- When the uncovered logic lives on the ViewModel **class file**, prefer `viewmodel_flow_livedata` against that SOURCE instead of Fragment UI attach.

## MUST NOT

- `@HiltAndroidTest`, `HiltAndroidRule`, `HiltTestApplication`, `@BindValue`, `@TestInstallIn`, invent `HiltHostActivity`.
- `fragment.viewModel = mock` on a delegated `val`.
- Call, reflect, or spy on **private** methods (`getTripData`, `_binding`, …) as Act.
- Invent a second harness (do not also plan a Hilt attach path).
- `buildActivity(...).setup().get()` — always `get()` → `setTheme` → `setup()`.

## Ordered write steps (1..N)

1. Imports / `@RunWith(RobolectricTestRunner::class)` / `@Config` (no Hilt annotations).
2. Arrange: launch FragmentScenario with AppCompat theme **or** `buildActivity` → `get()` → `setTheme` → `setup()` + `commitNow` add Cut; install VM factory / seed Activity ViewModelStore for `activityViewModels()`.
3. Stub MOCKING LANE seams for THIS scenario only.
4. Act: one public lifecycle / API call that reaches the listed Kover lines; `ShadowLooper.idleMainLooper()` after attach.
5. Assert: public UI / ViewModel public state / collaborator verify — not private fields.

## Prefer not_testable when

- Gap is private-only and no public lifecycle/API path reaches it with verified stubs.
- ViewBinding / generated types unresolved and no JVM inflate seam.

## Related api_doc

- `data/api_doc/robolectric_4_13_api_index.json`
