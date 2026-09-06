# Complex Android ViewModel instrumentation recipe

Use this recipe when the source is a ViewModel whose behavior depends on Hilt injection, Android context, LiveData, delegated fragment state, or other lifecycle-owned collaborators.

## Test layer

- Write the test under `src/androidTest`.
- Use `@RunWith(AndroidJUnit4::class)` and the module's existing Hilt test application when the source has Hilt annotations.
- Use `@HiltAndroidTest`, `HiltAndroidRule`, and `hiltRule.inject()` for Hilt graphs.
- Launch the smallest host activity available; do not recreate the production navigation flow unless the target behavior requires it.
- Use real lifecycle ownership for LiveData and lifecycle-aware collectors.

## Test shape

- Replace network, repository, and car/system collaborators with `@BindValue` fakes or test doubles.
- Exercise one uncovered method or branch per test.
- Assert emitted state, navigation effects, or persisted outcomes, not implementation details.
- Use `runTest` only for coroutine work and advance the test dispatcher deliberately.
- Keep Android-only setup in the test harness. Never modify `src/main`.

## Fallback

If the source's uncovered methods use only pure transformations and injected interfaces, prefer the unit recipe instead. Do not duplicate the same coverage lines in both `src/test` and `src/androidTest`.
