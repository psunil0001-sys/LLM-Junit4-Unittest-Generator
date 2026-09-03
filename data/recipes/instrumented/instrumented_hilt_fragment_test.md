---
id: instrumented_hilt_fragment_test
triggers: [hilt_fragment, android_fragment, android_navigation, android_ui]
---

# Hilt Fragment — on-device ActivityScenario androidTest

## MUST (non-skippable)

- Output: `src/androidTest/java/.../<Cut>InstrumentedTest.kt` mirroring source package.
- `@HiltAndroidTest` + `@RunWith(AndroidJUnit4::class)` — never Robolectric.
- `@get:Rule(order = 0) val hiltRule = HiltAndroidRule(this)`; `hiltRule.inject()` in `@Before`.
- `@BindValue` / `@TestInstallIn` for every SOURCE `@Inject` field the fragment reads.
- **Tier A** (attach / layout-only): `ActivityScenario.launch(HiltTestActivity::class.java)` then `supportFragmentManager.commitNow { add(android.R.id.content, CutFragment(), tag) }`.
- **Tier B** (navigation / `findNavController`): `ActivityScenario.launch(HiltNavTestActivity::class.java)` — fragment arrives via `hilt_nav_test_graph` startDestination (no `commitNow`).
- Use module androidTest hosts declared in manifest (`HiltTestActivity`, `HiltNavTestActivity`, `PlainHiltActivity` when CUT requires `MainActivityDelegate`).
- Assert public surface only: Espresso `onView(...)` for UI; collaborator effects via `verify { @BindValue mock }` (not `fragment.privateMember`).

## Full coverage checklist (instrumented lane)

For each Hilt `Fragment` CUT, aim to close these paths when present in SOURCE:

| SOURCE path | Host | Assert |
|---|---|---|
| `onAttach` success (`MainActivityDelegate`) | Tier A | `fragment.isAdded` |
| `onAttach` failure (non-delegate) | `PlainHiltActivity` | `assertThrows(IllegalArgumentException)` |
| `onDetach` / `onDestroyView` | Tier A | remove fragment; `!isAdded`, `view == null` |
| Layout inflation / `setupViews` UI deltas | Tier A or B | Espresso on public `@+id/*` from layout XML |
| `findNavController` / dialog open | Tier B | click public button; Espresso dialog title string |
| Dialog positive/negative callbacks | Tier B | `verify { repo.method(...) }` on `@BindValue` mockk |
| Deep-link navigation after dialog | Tier B | bootstrap `hilt_nav_test_graph` + `StubHomeFragment` per URI in SOURCE |
| `@Inject` collaborator side effects | Tier B | `verify { authRepository... }` / `userPreferencesRepository...` |
| `android:visibility="gone"` controls | Tier B | document skip OR `activity.findViewById` set `VISIBLE` then Espresso click |

Defer only when environment blocks execution (document in test name or `@Ignore`): `CarUi.requireToolbar`, Firebase on emulators without Play Services.

## MUST NOT

- Write under `src/test`; use Robolectric shadows; duplicate unit/Kover scenarios for same branch intent.
- Call private fragment methods or read private fields (`_binding`, `binding`, `toolbar`, `mainActivityDelegate`, …).
- Use `FragmentScenario.launchInContainer` / `launchFragmentInContainer` — lacks a Hilt host.
- Assign `@Inject` fields after launch without Hilt bindings.
- Anonymous-object-implement interfaces that reference AppAuth types (e.g. `handleLoginAttempt(AuthorizationResponse)`); use `mockk(relaxed = true)` or Mockito mock instead.
- Assume CarUi toolbar (`CarUi.requireToolbar`) needs an automotive/Car UI host — attach/lifecycle tests may run on AAOS emulator only; prefer Espresso on layout ids for public UI when toolbar setup is environment-sensitive.
- Do not assert `skip` click paths when layout sets `android:visibility="gone"` unless the test makes the control visible first.
- Launch `HiltTestActivity` + `commitNow` when SOURCE calls `findNavController()` or plan tags include `android_navigation` — use **Tier B** `HiltNavTestActivity` instead.
- Use production nav graph alone when SOURCE navigates to deep links not declared in module graph — bootstrap provides `hilt_nav_test_graph` with stub destinations.

## Ordered write steps

1. Imports: `@HiltAndroidTest`, `HiltAndroidRule`, `ActivityScenario`, `commitNow` (Tier A only), `@BindValue`, `AndroidJUnit4`, Espresso, `io.mockk.verify` as needed.
2. Rules + `@Before` inject.
3. Arrange mocks/fakes with `@BindValue` (mockk/Mockito for AppAuth-typed collaborators).
4. Act: Tier A → launch `HiltTestActivity`, `commitNow` add fragment; Tier B → launch `HiltNavTestActivity` only; drive lifecycle to `RESUMED`; invoke public UI/navigation paths from plan.
5. Assert observable UI via Espresso, `verify { }` on `@BindValue` mocks, or lifecycle flags on test fields.

## Minimal sketch

**Tier A — attach / layout**

```kotlin
@HiltAndroidTest
@RunWith(AndroidJUnit4::class)
class CutInstrumentedTest {
  @get:Rule(order = 0) val hiltRule = HiltAndroidRule(this)
  @BindValue @JvmField val dep: Dep = mockk(relaxed = true)

  @Before fun setUp() { hiltRule.inject() }

  @Test
  fun fragment_reaches_delta_onDevice() {
    ActivityScenario.launch(HiltTestActivity::class.java).use { scenario ->
      scenario.onActivity { activity ->
        activity.supportFragmentManager.commitNow {
          add(android.R.id.content, CutFragment(), "cut")
        }
      }
      onView(withId(R.id.somePublicView)).check(matches(isDisplayed()))
    }
  }
}
```

**Tier B — dialog + collaborator verify**

```kotlin
@Test
fun consent_sets_analytics_preference() {
  val title = targetContext.getString(core.R.string.share_app_usage_title)
  val consent = targetContext.getString(core.R.string.consent)
  ActivityScenario.launch(HiltNavTestActivity::class.java).use {
    onView(withId(R.id.next)).perform(click())
    onView(withText(title)).check(matches(isDisplayed()))
    onView(withText(consent)).perform(click())
    verify { userPreferencesRepository.setAnalyticsConsent(true) }
  }
}
```

**Negative onAttach**

```kotlin
@Test
fun onAttach_rejects_non_delegate_host() {
  ActivityScenario.launch(PlainHiltActivity::class.java).use { scenario ->
    assertThrows(IllegalArgumentException::class.java) {
      scenario.onActivity { it.supportFragmentManager.commitNow { add(...) } }
    }
  }
}
```
