TOOL ACCESS — Read, Bash, Edit, Write, Glob, and Grep are available.
WebSearch/WebFetch are denied (local-only). NotebookEdit is denied.

WRITE RESTRICTION (PreToolUse enforced): Edit/Write under `src/test`, `src/androidTest`
(TARGET and harness: kt, xml, res), `build.gradle*`, or `UnitTest_gen/`. Never `src/main`
or production sources.

GOAL:
Clear the REPAIR TICKETS for THIS SLICE. You may `./gradlew` compile+test THIS TARGET;
Python still runs Kover / JaCoCo after you stop.

HARNESS INVENTION:
Repair or invent missing harness if Gradle/validation failed because hosts/manifest/nav/deps are missing.
When missing, Write only files listed in INSTRUMENTED HARNESS PATHS: HiltTestRunner, AndroidManifest.xml,
hosts, nav/layout XML (`*nav_graph.xml`), and gradle test deps (espresso-core:3.7.0).
TARGET remains the primary *Test.kt / *InstrumentedTest.kt for THIS SLICE; harness files are first-class Write/Edit targets.
Recipes and VERIFIED LIBRARY API DOCS are patterns — if SOURCE or `build/generated` disagrees, follow evidence.

SIBLINGS:
If this prompt lists sibling *Test.kt paths: Read at most one for host/runner/`@Config`
patterns; do not copy whole suites. If none listed: do not Glob/Bash looking for `*Test.kt`.
All new `@Test`s go in PIPELINE TARGET.

DISCOVERY:
Read existing files freely (SOURCE, TARGET, docs, `**/build/generated/**` for signatures).
A missed Read is a hint — Read another real file next; do not freeze on Glob/Grep.
Do not Read `build/intermediates` junk. Never page files with offset/limit.
Do not import generated FQCNs whose only `.kt`/`.java` lives under `build/generated` —
invent test-side stubs/wrappers from what you Read (newType / newNested / invokeSuspend,
mock, or stub).

Read:
  Use when: excerpt incomplete; re-Read TARGET after Edit / No match;
    optional api_doc cheat-sheets; `**/build/generated/**` signatures;
    a path returned by Glob/Grep/Bash find.
  Do not: directories; Read `build/intermediates`.
Bash:
  Any local command allowed (`ls`, `cat`, `pwd`, `python3`, find/grep, `./gradlew` for
  THIS MODULE's unit/androidTest compile+test — PIPELINE VERIFY below).
  When PIPELINE VERIFY includes an AOSP EMULATOR section: run `adb devices` only.
  If a device is present, run the listed verify Gradle command. If none, run the listed
  compile-only command — never launch emulator / envsetup / lunch / AVD.
  Never network commands: `curl`, `wget`, `ssh`, `pip`, `npm`, `git clone|fetch|pull|push`.
  Never Kover/JaCoCo XML reports (Python owns those gates).
Edit:
  Use when: repair TARGET and/or existing harness for THIS SLICE validation/Gradle failures.
Write:
  Use when TARGET or a missing harness file must be created or fully rewritten.
Forbidden: Write/Edit on src/main or production sources. WebSearch/WebFetch are denied.
