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
  Use when: absolute FILE path when excerpt incomplete; re-Read TARGET after Edit / No match;
    VERIFIED LIBRARY API DOCS catalog paths for third-party signatures
    (prefer grep for a class/method; at most 2 api_doc files per turn);
    one sibling *Test.kt only for Robolectric/Hilt host setup missing from TARGET
    (at most 1 sibling path per turn; do not copy whole tests);
    a path returned by Bash find/grep (Read that hit once).
  Do not: directories; Read TARGET more than twice before the first Edit;
    do not Read siblings to invent scenarios;
    Read any path under **/build/** (including build/generated).
    Unresolved / generated-only types: construct via harness reflection (newType /
    newNested / invokeSuspend), mock, or stub — do not import from build/ or walk SOURCE
    with offset/limit looking for those classes.
  If denied/missing, Bash find under OWNING MODULE DIR once; do not retry the failed path.
  Navigation / res XML: before Read on nav graphs or res/navigation/*.xml, Bash find under the owning
    module (e.g. find MODULE -name '*nav_graph.xml') — handles filename typos.
Bash (find/grep only):
  Use when: path unknown, Read denied/missing, or cross-module find by exact name
    (e.g. find MODULE -name 'HiltTestActivity.kt', find MODULE -name '*nav_graph.xml').
  Examples:
    find /abs/feature/onboarding -name '*nav_graph.xml'
    grep -r 'SymbolName' /abs/feature/onboarding
  Do not: re-run the same find/grep; re-Read the same path;
    find under build/; run gradlew, rm, python3, or pipeline verify commands.
Edit:
  Use when: repair TARGET for THIS SLICE validation/Gradle failures only.
  Hard limit: Write/Edit only under src/test, src/androidTest (TARGET), build.gradle.kts,
    build.gradle, or UnitTest_gen — never src/main / production sources (PreToolUse denies them).
  Do not: edit non-TARGET / production; old_string must match file bytes.
Write:
  Use when: TARGET must be rewritten to fix the failure (prefer Edit when possible).
  Do not: helpers outside allowed write roots. Same path hard limit as Edit.
Never run ./gradlew, gradle, bash beyond find/grep discovery, sh, python3, or any verify command. Never create helper scripts (.sh, .bat, test_runner.*) or any file outside the PIPELINE TARGET test path. Verification is pipeline-owned: stop after editing TARGET.
When shared rules say 'view', use Read with the absolute path.
Third-party APIs: use VERIFIED LIBRARY API DOCS catalog paths (Read/grep; do not invent).
Prefer verified AppAuth AuthorizationResponse/TokenResponse builders for OAuth fixtures.
Pipeline re-runs FAST_VERIFY after you finish.
Forbidden: Gradle/bash beyond find/grep discovery; Write/Edit on src/main or other disallowed paths.
