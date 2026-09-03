---
id: instrumented_work_manager_test
triggers: [android_work_manager, hilt_worker, worker_service]
---

# WorkManager — on-device androidTest with TestDriver

## MUST (non-skippable)

- Output: `src/androidTest/.../<Cut>InstrumentedTest.kt`.
- `@HiltAndroidTest` when CUT or Worker uses Hilt; `@RunWith(AndroidJUnit4::class)`.
- Initialize with `WorkManagerTestInitHelper.initializeTestWorkManager(context)` in `@Before`.
- Enqueue via real `WorkManager.getInstance(context)`; drive constraints with `TestDriver` when plan targets constraint branches.
- Use `TestListenableWorkerBuilder` only when verifying worker logic in isolation on device.

## MUST NOT

- Robolectric `WorkManagerTestInitHelper` in JVM unit style inside androidTest without real WorkManager instance.
- Duplicate unit scenarios already green in Kover for the same enqueue path.

## Ordered write steps

1. `@HiltAndroidTest` + rules when Worker is `@HiltWorker`.
2. `@Before`: application context + `WorkManagerTestInitHelper.initializeTestWorkManager`.
3. Act: enqueue work matching delta; optionally `testDriver.setAllConstraintsMet(request.id)`.
4. Assert: `WorkInfo` state or side effects observable on device.
