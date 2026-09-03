Use JUnit4 only. Stay offline: no network or instrumentation.
Robolectric is allowed: @RunWith(RobolectricTestRunner::class) + @Config when Android APIs are needed.
Prefer safe stubs/mocks to cover DELTA lines/branches when real construction would block compile-safe tests.
Write from plan + SOURCE + TARGET. Optionally view one sibling only for host setup; never copy whole tests.
Obey MOCKING LANE. Make minimal edits, then stop — Python runs FAST_VERIFY / Kover after you finish.
