---
id: worker_service_receiver
triggers: [worker_service]
---

# Service / BroadcastReceiver / non-Hilt Worker

**Redirect:** if SOURCE is `@AndroidEntryPoint` Service → use `hilt_service_full_harness.md`, not this recipe.

## MUST

- One scenario / branch per TC; Robolectric Context + Intent.
- Mock WorkManager / Kotlin `object` seams used by SOURCE.
- Service: `Robolectric.buildService(Cut::class.java)` (or construct + `onStartCommand`).
- Receiver: `Cut().onReceive(context, intent)`.
- Non-Hilt `Worker` / `CoroutineWorker`: mock `WorkerParameters` the same way as
  `hilt_worker_dowork.md` (`mock<WorkerParameters>()` / `mockk<WorkerParameters>()`).

## MUST NOT

- One mega-test for the entire production start/stop loop.
- `buildWorkerParams`, `androidx.work.testing`, `TestListenableWorkerBuilder`.
- Hilt Service harness on `@AndroidEntryPoint` Service (use `hilt_service_full_harness`).

## Ordered write steps (1..N)

1. Imports + Robolectric.
2. Arrange construct Service/Receiver; mock SOURCE singletons.
3. Stub flags for THIS branch.
4. Act: onReceive / onStartCommand (or `doWork` for Worker).
5. Assert Result / verify enqueue / collaborator.

## Related recipes

- `hilt_worker_dowork.md`, `hilt_service_full_harness.md`, `android_object_singleton.md`
