TOOL ACCESS — Read, Bash, Edit, Write, Glob, and Grep are available.
WebSearch/WebFetch are denied (local-only). NotebookEdit is denied.

WRITE RESTRICTION (PreToolUse enforced): Edit/Write under `src/test`, `src/androidTest`
(any harness file: kt, xml, res), `build.gradle*`, or `UnitTest_gen/`. Never `src/main`
or production sources.

GOAL:
Implement THIS SLICE's DELTA `@Test` methods in PIPELINE TARGET so compile+test and
the Python Kover / JaCoCo gates can pass. Invent any missing harness THIS SOURCE needs.

HARNESS INVENTION:
Invent only the harness THIS SOURCE requires (see INSTRUMENTED HARNESS PATHS) — not every host type.
When a listed file is missing, Write it: HiltTestRunner, AndroidManifest.xml (HiltTestApplication + @style/AppTheme),
Hilt/plain hosts, nav/layout XML (`*nav_graph.xml`), stub fragments, and test dependencies in the owning
module `build.gradle(.kts)` (espresso-core:3.7.0, androidx.test:runner:1.6.2, androidx.test.ext:junit:1.2.1,
hilt-android-testing:2.49, mockk-android; testInstrumentationRunner = module HiltTestRunner).
Do not invent a second runner or host pair if one already exists in the module.
Recipes and VERIFIED LIBRARY API DOCS are patterns — adapt names to THIS SOURCE.
If SOURCE or `build/generated` contradicts a recipe/doc, follow evidence.

SIBLINGS:
If this prompt lists sibling *Test.kt paths: Read at most one for host/runner/`@Config`
patterns; do not copy whole suites. If none listed: do not Glob/Bash the repo looking
for `*Test.kt`. Invent harness from SOURCE + recipes + generated sources + docs already
in this prompt. All new `@Test`s go in PIPELINE TARGET.

DISCOVERY:
Read existing files freely (SOURCE, TARGET, docs, `**/build/generated/**` for signatures).
A missed Read is a hint — Read another real file next; do not freeze on Glob/Grep.
Do not Read `build/intermediates` junk. Never page files with offset/limit.
Do not import generated FQCNs whose only `.kt`/`.java` lives under `build/generated` —
invent test-side stubs/wrappers from what you Read (newType / newNested / invokeSuspend,
mock, or stub).

Read:
  Use when: absolute FILE path for SOURCE/TARGET when excerpt incomplete;
    re-Read TARGET after each successful Edit or after Edit 'No match found';
    optional api_doc cheat-sheets; `**/build/generated/**` signatures;
    a path returned by Glob/Grep/Bash find.
  Do not: pass directories; Read `build/intermediates`.
  Hard rule: never call Read with empty input.
  Prefer Glob/Grep/Bash under the owning module to find types imported by SOURCE —
  do not invent filenames.
Glob / Grep:
  Use to discover hosts, manifests, nav graphs (`*nav_graph.xml`) under the owning module.
  Do not hunt `*Test.kt` unless siblings are listed in this prompt.
Bash:
  Any local command allowed (`ls`, `cat`, `pwd`, `python3`, find/grep, `./gradlew` for
  THIS MODULE's unit/androidTest compile+test — same command as PIPELINE VERIFY below).
  When PIPELINE VERIFY includes an AOSP EMULATOR section: run `adb devices` only.
  If a device is present, run the listed verify Gradle command. If none, run the listed
  compile-only command — never launch emulator / envsetup / lunch / AVD.
  Never network commands: `curl`, `wget`, `ssh`, `pip`, `npm`, `git clone|fetch|pull|push`.
Edit:
  Use when TARGET or harness files already exist — add tests for ONLY this slice or repair harness.
Write:
  Use when TARGET is missing or empty — one Write creates the full JUnit4 class at PIPELINE TARGET
  (package, imports, class name = file stem, annotations/rules from SOURCE + recipe, @Test for this slice).
  Also Write missing harness files under test roots / gradle. Prefer Write for first creation; then Edit.
