---
id: instrumented_foreground_service_test
triggers: [android_foreground_service]
---

# Foreground Service — on-device androidTest

## MUST (non-skippable)

- Output: `src/androidTest/.../<Cut>InstrumentedTest.kt`.
- `@RunWith(AndroidJUnit4::class)`; add `@HiltAndroidTest` when service uses Hilt entry points.
- Start service via `context.startForegroundService(intent)` / `ActivityScenario` host that triggers service start.
- Assert notification channel, foreground promotion, or lifecycle callbacks on real framework — not shadows.

## MUST NOT

- Robolectric service shadows for FGS delta lines assigned to instrumented layer.
- Duplicate unit/Robolectric coverage for the same start/stop branch.

## Ordered write steps

1. Grant runtime permissions in `@Before` when SOURCE requires them (use `GrantPermissionRule` on API 28+).
2. Arrange Hilt bindings when service injects dependencies.
3. Act: launch host or start service intent from plan scenario.
4. Assert: `Service` state, notification posted, or callback side effect on device.
