---
id: carui_toolbar_host
triggers: [carui_toolbar, carui_progress, carui_back_listener]
---

# CarUi toolbar host — static stub + capture

## MUST

- Static mock `CarUi` before production `requireToolbar`.
- Stub returns a capturing/fake `ToolbarController` before the setup path runs.
- Layouts that inflate `CarUiRecyclerViewImpl` need AAOS OEM API types on the **unit-test classpath**. Those types are compile-only / platform-provided and are **not** shipped inside `car-ui-lib` AAR for JVM tests. Copy minimal stubs from an existing module test (e.g. `feature/settings/.../RecyclerViewAttributesOEMV1.kt`, `LayoutStyleOEMV1.kt` under `src/test/java/com/android/car/ui/plugin/oemapis/recyclerview/`) — do **not** invent package layout or LayoutInflater factory hacks that change ViewBinding types.

## MUST NOT

- Assert car-ui private internals; skip static stub.
- Guess OEM stub packages — copy from a proven module test in the same repo.

## Ordered write steps (1..N)

1. Imports + static mock API for MOCKING LANE.
2. Arrange host Cut + capturing toolbar.
3. Stub `CarUi.requireToolbar(any())` before act.
4. Act: attach / setupToolbar path.
5. Assert: public effect (nav mode, menu click → side effect).

## Minimal sketch

```kotlin
mockkStatic(CarUi::class)
val toolbar = mockk<ToolbarController>(relaxed = true)
every { CarUi.requireToolbar(any()) } returns toolbar
// attach Cut, then verify toolbar / public side effect
```

## Related api_doc

- `data/api_doc/car_ui_lib_2_6_0_api_index.json`
