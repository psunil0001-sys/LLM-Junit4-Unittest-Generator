---
id: hilt_worker_dowork
triggers: [hilt_worker]
---

# @HiltWorker / CoroutineWorker — unit `doWork()`

## MUST (non-skippable)

- `@RunWith(RobolectricTestRunner::class)` and Context via
  `ApplicationProvider.getApplicationContext<Context>()`.
- `WorkerParameters` is a **mock**, not a real instance:
  `mock<WorkerParameters>()` (mockito) or `mockk<WorkerParameters>()` (MockK).
  Stub `inputData` / `runAttemptCount` only if `doWork` reads them.
- Construct via **@AssistedInject** constructor:
  `Cut(context, params, …mockedInjectDeps)` — mock every non-`@Assisted` SOURCE
  constructor param (types from SOURCE imports (Glob/Read)).
- `runTest { worker.doWork() }` (or suspend entry).
- Assert `ListenableWorker.Result.success()` / `.retry()` / `.failure()`.

## MUST NOT

- `HiltWorkerFactory` / full WorkManager enqueue as the only unit plan.
- Rely on 2-arg `Worker(context, params)` when AssistedInject deps are required.
- `Robolectric.buildWorkerParams()` — **does not exist**.
- `androidx.work.testing` / `TestListenableWorkerBuilder` / `TestWorkerBuilder` —
  needs `androidx.work:work-testing`, which is not on this test classpath; for
  `@HiltWorker` the builder still needs a custom `WorkerFactory`.
- `WorkerParameters.Factory` / `object : WorkerParameters` — class is **final**.
- `androidx.work.Result` — no such type; use nested `ListenableWorker.Result`.

## Ordered write steps (1..N)

1. Imports / runner: Robolectric, `ApplicationProvider`, MOCKING LANE,
   `WorkerParameters`, `ListenableWorker.Result`, `runTest`.
2. Arrange: `val params = mock<WorkerParameters>()` (or `mockk`); construct CUT
   via AssistedInject ctor with mocked inject deps from SOURCE.
3. Stub branch inputs for THIS scenario (and `params.inputData` only if used).
4. Act: `runTest { worker.doWork() }`.
5. Assert: `ListenableWorker.Result` + verify collaborators.

## Forbidden

- HiltWorkerFactory; TestListenableWorkerBuilder; work-testing; buildWorkerParams;
  subclassing WorkerParameters; `androidx.work.Result`.

## Minimal sketch

```kotlin
@RunWith(RobolectricTestRunner::class)
class CutWorkerTest {
  private val context: Context = ApplicationProvider.getApplicationContext()
  private val params = mock<WorkerParameters>()
  private val dep = mock<Dep>()

  @Test fun doWork_returnsSuccess() = runTest {
    val worker = Cut(context, params, dep)
    assertEquals(ListenableWorker.Result.success(), worker.doWork())
  }
}
```

## Related api_doc

- `data/api_doc/hilt_worker_testing.json`
