---
id: flow_builder_cancellation_branch
triggers: [coroutines_flow, kover_partial_branch_flow_emit]
---

# Flow builder — cancel / slow-collector / exception branches (companion)

Use when Kover reports **partial-branch** (`mb>0`) on lines that are `emit(...)` inside
`flow { ... }`, **after** TARGET already covers the happy path with `.first()` / `.single()`.

`.first()` cancels after the first value, but often does **not** hit coroutine state-machine
probes for mid-body cancel, post-emit cleanup, or exception continuations. Explicit
`launchIn` + `cancelAndJoin` (with a suspending upstream or slow collector) does.

## WHEN

- SOURCE returns `Flow` via `flow { emit(dep.suspendCall(...)) }` (thin repo / data wrappers).
- DELTA is **partial-branch only** on those `emit` lines (lines already covered; `mb>0`).
- Happy-path tests already call the public API and assert the emitted value.

## WHEN NOT

- Real Kotlin `if` / `&&` / `||` short-circuit arms → use normal complementary-arm tests.
- Missed **lines** (never executed) → plan a public path that reaches them first.
- After cancel + exception + slow-collector tests exist and Kover still shows `mb>0` →
  `not_testable` / `kover_residual_branch` (do not invent more cancel variants).
- Do **not** use instrumented tests for these probes — same bytecode on device.

## MUST

- `@OptIn(ExperimentalCoroutinesApi::class)` when using `runTest` / `runCurrent` / `advanceUntilIdle`.
- Prefer `runTest` + `launchIn(this)` (TestScope), not a raw `GlobalScope`.
- Pair cancel with **real suspension** so cancel lands inside `flow { emit(...) }`:
  - Upstream: `coEvery { dep.foo(any()) } coAnswers { delay(10_000); value }` then
    `flow.onEach { }.launchIn(this)` → `runCurrent()` → `job.cancelAndJoin()`.
  - And/or slow collector: emit returns immediately, `onEach { delay(10_000) }.launchIn(this)`
    → `runCurrent()` → `cancelAndJoin()`.
- Also collect once with upstream **throw** (`coEvery { ... } throws boom` + `flow.first()` in
  try/catch) to hit exception continuations.
- Keep existing happy-path `.first()` tests; add these as **extra** `@Test` methods.

## MUST NOT

- Replace mapping/assertion tests with cancel-only tests.
- Expect 100% branch on every `emit` line — one residual probe per line is common; demote then.
- Use this for ViewModel UI state flows unless the gap line is literally `flow { emit(...) }`.

## Ordered write steps (1..N)

1. Imports: `launchIn`, `onEach`, `cancelAndJoin`, `delay`, `runCurrent`, `advanceUntilIdle`,
   `ExperimentalCoroutinesApi`.
2. Stub deps with `coAnswers { delay(...); value }` for each public Flow method in the DELTA.
3. For each Flow: `val job = repo.method(...).onEach { }.launchIn(this)` → `runCurrent()` →
   `job.cancelAndJoin()`.
4. Repeat with immediate stub + `onEach { delay(...) }` cancel (post-emit cleanup).
5. Repeat with `throws` + `first()` catching the exception (exception continuation).
6. Re-run Kover; leave remaining `mb>0` as residual.

## Minimal sketch

```kotlin
@OptIn(ExperimentalCoroutinesApi::class)
@Test
fun flowEmit_cancelWhileUpstreamSuspended() = runTest {
  coEvery { dataSource.fetch(any()) } coAnswers {
    delay(10_000)
    response
  }
  val job = repo.fetch(input).onEach { }.launchIn(this)
  runCurrent()
  job.cancelAndJoin()
  advanceUntilIdle()
}

@OptIn(ExperimentalCoroutinesApi::class)
@Test
fun flowEmit_cancelSlowCollectorAfterEmit() = runTest {
  coEvery { dataSource.fetch(any()) } returns response
  val job = repo.fetch(input).onEach { delay(10_000) }.launchIn(this)
  runCurrent()
  job.cancelAndJoin()
  advanceUntilIdle()
}

@Test
fun flowEmit_exceptionContinuation() = runTest {
  coEvery { dataSource.fetch(any()) } throws RuntimeException("boom")
  try {
    repo.fetch(input).first()
  } catch (_: RuntimeException) {
  }
}
```

## Related api_doc

- `data/api_doc/kotlinx_coroutines_test_1_7_3_api_index.json`

## Evidence

On `TripsRepoImpl` emit lines, happy-path-only was ~25% branch (cb=1/4). After cancel +
slow-collector + exception tests: ~75% branch (cb=3/4). Remaining `mb=1` per line → residual.
