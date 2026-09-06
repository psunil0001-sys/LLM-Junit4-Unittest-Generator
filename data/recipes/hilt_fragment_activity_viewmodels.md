---
id: hilt_fragment_activity_viewmodels
triggers: [delegated_viewmodel_fragment, hilt_fragment]
---

# Hilt Fragment `viewModels()` / `activityViewModels()`

## MUST

- SOURCE is an **`@AndroidEntryPoint` Fragment** with `by viewModels()` / `activityViewModels()`.
- Attach Cut using the **Hilt Fragment lifecycle** approach only (`hilt_fragment_lifecycle`): `@HiltAndroidTest` + `HiltAndroidRule` + `HiltTestApplication` + module `@AndroidEntryPoint` host (main or `src/test` `HiltHostActivity`) + `commitNow()`.
- `@BindValue` / `@TestInstallIn` for SOURCE **collaborators** (repos/use cases) — **never** the delegated ViewModel type.
- Host bootstrap: theme on `get()` **before** `setup()` / `create()` (both OK when theme is set first).
- `activityViewModels()`: attach under the **same** host Activity so the Activity-scoped VM is shared. Seed the Activity `ViewModelStore` **before** `commitNow` when the test needs a non-default StateFlow/LiveData:

```kotlin
val controller = Robolectric.buildActivity(HiltHostActivity::class.java)
val activity = controller.get()
activity.setTheme(androidx.appcompat.R.style.Theme_AppCompat)
controller.setup()
val shared = ViewModelProvider(activity)[SharedVm::class.java]
// shared.send… / set… public APIs
activity.supportFragmentManager.beginTransaction()
    .add(android.R.id.content, Cut(), "cut")
    .commitNow()
ShadowLooper.idleMainLooper()
```

- After `commitNow()`, call `ShadowLooper.idleMainLooper()` before asserting views / bindings.
- Act only **public** entry points after attach; private methods only via those Acts.

## MUST NOT

- `FragmentScenario.launchFragmentInContainer` (host is not `@AndroidEntryPoint`).
- `@BindValue` of the delegated ViewModel type; `fragment.viewModel = mock` on a delegated `val`.
- Invent a one-off host Activity inside the test file; invent `launchFragmentInHiltContainer` unless it already exists in the module.
- Plan a plain/non-Hilt attach path in the same approach.
- `buildActivity(...).setup().get()` — always `get()` → `setTheme` → `setup()`.

## Ordered write steps (1..N)

1. Imports / runner + Hilt annotations; import module `HiltHostActivity` when using the test host.
2. Arrange: `hiltRule.inject()`; `@BindValue` SOURCE deps (not the VM type).
3. Stub MOCKING LANE on binds before attach.
4. Act: host `get()` → `setTheme` → `setup()` then `commitNow()` add Cut; `ShadowLooper.idleMainLooper()`; drive public UI / public VM APIs.
5. Assert: public UI / verify collaborator — not private `_binding`.

## Related recipes

- Primary attach: `hilt_fragment_lifecycle.md`
- VM-class logic (when SOURCE is the ViewModel file): `viewmodel_flow_livedata.md`

## Related api_doc

- `data/api_doc/hilt_android_testing_2_49_api_index.json`
- `data/api_doc/robolectric_4_13_api_index.json`
