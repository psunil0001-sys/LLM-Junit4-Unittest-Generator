---
id: instrumented_foreground_service_test
triggers: [android_foreground_service]
---

# Foreground Service — on-device androidTest

## MUST (non-skippable)

- Output: `src/androidTest/.../<Cut>InstrumentedTest.kt`.
- `@RunWith(AndroidJUnit4::class)`; add `@HiltAndroidTest` when service uses `@AndroidEntryPoint`.
- Shared runner: `com.HiltTestRunner` + consumer androidTest `HiltTestApplication` manifest (same as fragment recipe).
- Start service via `context.startForegroundService(Intent(context, CutService::class.java))` (or `startService` when not FGS).
- Assert observable service state on the real framework: companion `isRunning` flags, `stopSelf` / `onDestroy`, notification channel creation, or `@BindValue` collaborator calls (`verify { repo... }` / `saveLog`).
- `@BindValue` / `@UninstallModules` for every SOURCE `@Inject` dependency the service reads before start.
- Prefer stubbing work gates (e.g. `countPendingTrips() == 0`, no-network branch) so start→work→`stopSelf` completes without real network I/O.

## MUST NOT

- Robolectric service shadows for FGS delta lines assigned to instrumented layer.
- Duplicate unit/Robolectric coverage for the same start/stop branch.
- Call production sync/upload managers against live backends from androidTest — stub repositories / gate conditions.
- Skip `@HiltAndroidTest` when the service is `@AndroidEntryPoint`.

## Ordered write steps

1. Point `testInstrumentationRunner` at shared `HiltTestRunner`; thin androidTest manifest with `HiltTestApplication`.
2. Grant runtime permissions in `@Before` only when the platform can grant them (AAOS emulators often reject `GrantPermissionRule` for `POST_NOTIFICATIONS` — omit the rule and rely on `startForeground` which does not require the post-notifications runtime grant for FGS).
3. Arrange Hilt `@BindValue` mocks; uninstall `@Binds` modules the service’s inject graph uses.
4. Act: prefer `context.startService(Intent(...))` for library androidTest when CUT calls `startForeground` itself — `startForegroundService` races with Hilt `onCreate` inject and often throws `ForegroundServiceDidNotStartInTimeException` if tearDown/`stopService` runs mid-handshake. Use `startForegroundService` only when asserting that entry path specifically.
5. Assert: running flag, `verify { userPreferencesRepository.saveLog(...) }`, destroy cleanup (`isRunning == false` after stop).
6. Tear down: `stopService` if still running; `unmockkAll()`.

## Minimal sketch

```kotlin
@HiltAndroidTest
@RunWith(AndroidJUnit4::class)
@UninstallModules(PreferencesBindsModule::class, /* service bind modules */)
class CutServiceInstrumentedTest {
  @get:Rule(order = 0) val hiltRule = HiltAndroidRule(this)
  @BindValue @JvmField val userPreferencesRepository: UserPreferencesRepository = mockk(relaxed = true)
  @BindValue @JvmField val tripRepository: TripRepository = mockk(relaxed = true)

  @Before fun setUp() {
    hiltRule.inject()
    every { tripRepository.countPendingTrips() } returns 0
  }

  @Test fun starts_and_stops_when_no_pending_work() {
    val ctx = ApplicationProvider.getApplicationContext<Context>()
    ctx.startForegroundService(Intent(ctx, CutService::class.java))
    // poll until !CutService.isRunning or timeout
    verify(atLeast = 1) { userPreferencesRepository.saveLog(any(), any()) }
  }
}
```
