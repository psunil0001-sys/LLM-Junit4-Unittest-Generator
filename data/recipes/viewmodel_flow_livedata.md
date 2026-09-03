---
id: viewmodel_flow_livedata
triggers: [viewmodel, coroutines_flow, hilt_viewmodel]
---

# ViewModel — Flow / StateFlow / LiveData unit tests

Use when SOURCE **is the ViewModel class file**. Fragment attach gaps on a different SOURCE file use the fragment recipes instead.

## MUST

- Construct VM with **every SOURCE constructor dep mocked** (types from SOURCE IMPORTS) — no Fragment host for logic gaps.
- `@HiltViewModel` SOURCE: plain JVM test — mock ctor deps directly; **no** `@HiltAndroidTest` / `HiltAndroidRule`.
- `runTest` for suspend/Flow; `@OptIn(ExperimentalCoroutinesApi::class)` on the test class when using `runTest`.
- When CUT uses `viewModelScope` + `Dispatchers.Main`: add `MainDispatcherRule` or inject `UnconfinedTestDispatcher` via ctor/test factory.
- `androidx.arch.core.executor.testing.InstantTaskExecutorRule` **only** when LiveData is under test.

## MUST NOT

- InstantTaskExecutorRule solely for coroutines with no LiveData.
- `runBlockingTest` (removed; use `runTest`).
- `@HiltAndroidTest` / `@BindValue` for ViewModel-only SOURCE files.
- FragmentScenario / Hilt attach when the gap is on the ViewModel class itself.

## Ordered write steps (1..N)

1. Imports + `MainDispatcherRule` / `InstantTaskExecutorRule` as needed.
2. Arrange: Cut VM + mocked use cases from SOURCE ctor.
3. Stub controlled flows (`flowOf`, `MutableStateFlow`, `coEvery`, …).
4. Act: public VM API in `runTest`.
5. Assert states / verify use case.

## Minimal sketch

```kotlin
@OptIn(ExperimentalCoroutinesApi::class)
class CutViewModelTest {
  @get:Rule val mainDispatcher = MainDispatcherRule()

  private val dep = mock<Dep>()
  private val vm = CutViewModel(dep)

  @Test fun publicApi_updatesState() = runTest {
    // stub dep, call vm public API, assert state
  }
}
```

## Related api_doc

- `data/api_doc/kotlinx_coroutines_test_1_7_3_api_index.json`
- `data/api_doc/arch_core_testing_2_2_api_index.json` (LiveData / InstantTaskExecutorRule)
