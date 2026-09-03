## 8. Test Data
- Input arguments: choose minimal values that reach selected lines
- Bundle keys: only keys read by selected lines
- Fake data: complete escaped JSON string literals or JSONObject builders when nested
- Expected output: observable behavior caused by selected lines

## 9. Expected Assertions
- View assertions: assert only visible state changed by selected lines
- State assertions: assert state emitted by selected lines
- ViewModel assertions: assert calls/state needed by selected lines
- Repository assertions: assert collaborator interaction only when observable output is unavailable
- Navigation assertions: assert navigation only when selected lines trigger it
- Error assertions: assert error state only when selected lines produce it
- Do not verify Timber/Log unless the gap under test is logging itself

## 10. Risks and Assumptions
- Assumption: coder follows existing target-file testing style
- Risk: generated fallback sections may need target-specific fixture names
- Possible compile issue: missing project-specific helper imports
- Flaky test risk: avoid timing and real async delays

## 11. Missing Information
- Missing dependency: none identified by fallback renderer
- Missing module: none identified by fallback renderer
- Missing theme: use existing target-file pattern
- Missing Gradle dependency: none

## 12. Generation Rules
- Use JUnit4 only; never JUnit5, MockitoExtension, or @ExtendWith
- Obey frontmatter mocking_lane: one framework; all stubs/verifies in that syntax only
- Suspend CUT or suspend seams: call inside kotlinx.coroutines.test.runTest { }
- CUT construction: object/static call vs constructor inject vs Hilt — no vague inject
- Singleton/object under test: call Type.method(...) directly; do not invent DI
- JSON fixtures: complete, escaped string literals; prefer builders when complex
- Do not verify Timber/Log unless the selected gap is logging itself
- Use Robolectric / FragmentScenario / Hilt only when Sections 2–5 require them
- InstantTaskExecutorRule only for LiveData
- Do not mock Fragment/Activity lifecycle directly
- Edit ONLY the PIPELINE TARGET *Test.kt file
- If TARGET already exists: merge — keep every existing `@Test`, import, helper, 
runner, and class structure; add only new `@Test` methods (plus imports/helpers 
those new tests need). Do not overwrite, rewrite, rename, or delete prior tests.
- If TARGET does not exist yet: create it once with Sections 2–5 infrastructure, 
then add this plan's `@Test` methods
- Cover only the Kover lines listed for each TC; do not invent already-covered lines

## 13. Deferred Coverage
| Kover Lines | Methods | Tag | Category | Reason |
|---|---|---|---|---|
