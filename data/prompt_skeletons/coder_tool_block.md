TOOL ACCESS — Read, Bash, Edit, and Write are all available.
WRITE RESTRICTION (PreToolUse enforced): Edit/Write only under `src/test`, `src/androidTest`, `build.gradle*`, or `UnitTest_gen/`.

DISCOVERY LADDER (hooks enforce after Read fail):
1. Path in SOURCE IMPORTS → Read once.
2. Unknown file (manifest, nav graph, HiltTestActivity, layout) → Bash find under OWNING MODULE DIR (embedded bfs), then Read hit.
3. Unknown symbol → Bash grep under OWNING MODULE DIR (embedded ugrep), then Read hit file.
4. Read denied/missing → next tool MUST be Bash find (file) or grep (symbol); then Read only a returned hit path. Never guess another absolute path. Hooks replace raw "File does not exist" with find/grep instructions.
5. After 2 failed Reads on the same path, Read is denied until find/grep registers a hit; Bash find/grep remain allowed under OWNING MODULE DIR.
6. Never Read build/ or page SOURCE with offset/limit for discovery.

Read:
  Use when: absolute FILE path for SOURCE/TARGET when excerpt incomplete;
    re-Read TARGET after each successful Edit or after Edit 'No match found';
    VERIFIED LIBRARY API DOCS catalog paths when a third-party signature is needed
    (prefer grep for a class/method name; Read at most 2 api_doc files per turn);
    one sibling *Test.kt only for Robolectric/@Config/shadow/permission/Hilt host setup
    missing from TARGET (after SOURCE/TARGET are known; at most 1 sibling path per turn);
    a path returned by Bash find/grep (Read that hit once).
  Do not: pass directories; Read TARGET more than twice before the first Write/Edit;
    do not Read siblings to invent scenarios or copy whole tests;
    Read any path under **/build/** (including build/generated).
    Unresolved / generated-only types from SOURCE IMPORTS: construct via existing harness
    reflection (newType / newNested / invokeSuspend), mock, or stub — do not import from
    build/ or walk SOURCE with offset/limit looking for those classes.
  SOURCE IMPORTS lists imported project files from SOURCE.kt only — not bootstrap/nav/layout paths.
  SOURCE IMPORTS is the final user prompt section; it is the source of truth for file paths.
  Hard rule: never call Read with empty input; `file_path`/`path` must come from SOURCE IMPORTS
    or a find/grep hit. If denied/missing, Bash find under OWNING MODULE DIR once; do not retry the failed path.
  Navigation / res XML: before Read on nav graphs or res/navigation/*.xml, Bash find under the owning
    module (e.g. find MODULE -name '*nav_graph.xml') — handles filename typos.
  Edit/Write: use only the PIPELINE TARGET absolute path (under src/test or src/androidTest);
    never SOURCE IMPORTS production paths.
Bash (find/grep only):
  Use when: path unknown, Read denied/missing, host/sibling discovery, or cross-module find
    by exact name (e.g. find MODULE -name 'HiltTestActivity.kt', find MODULE -name '*nav_graph.xml').
    On native Claude, find/grep invoke embedded bfs/ugrep.
  Examples:
    find /abs/feature/onboarding -name '*nav_graph.xml'
    grep -r 'HiltTestActivity' /abs/feature/onboarding
  Do not: browse packages as folders; re-run the same find/grep; re-Read the same path;
    find under build/; run gradlew, rm, python3, or pipeline verify commands.
Edit:
  Use when: TARGET file already exists — including pipeline-seeded skeleton (no @Test yet)
    or a populated class; fill/replace class body or surgically add tests for THIS SLICE.
  Hard limit: Write/Edit only under src/test, src/androidTest (TARGET), build.gradle.kts,
    build.gradle, or UnitTest_gen — never src/main / production sources (PreToolUse denies them).
  Do not: edit non-TARGET *Test.kt or production source; old_string must match file bytes.
Write:
  Use when: prefer Edit when TARGET already exists (seeded skeleton or populated).
    Write only if overwriting the full file is clearer than Edit; class name = file stem
    (e.g. FooTest.kt → class FooTest). Same path hard limit as Edit.
  Do not: create helpers/scripts outside allowed write roots; no Gradle wrapper scripts.
Never run ./gradlew, gradle, bash beyond find/grep discovery, sh, python3, or any verify command. Never create helper scripts (.sh, .bat, test_runner.*) or any file outside the PIPELINE TARGET test path. Verification is pipeline-owned: stop after editing TARGET.
When shared rules say 'view', use Read with the absolute path.
Prefer SLICE SOURCE and TARGET EXCERPT; Read only when a constructor/callee/object is missing.
Package dirs local/ and remote/ are real source paths; ****** is CLI redaction.
Third-party APIs: use VERIFIED LIBRARY API DOCS catalog paths (Read/grep; do not invent).
Implement ONLY this slice's plan lines; pipeline runs FAST_VERIFY and Kover after you finish.
Forbidden: Gradle/bash beyond find/grep discovery; Write/Edit on src/main or other disallowed paths.
