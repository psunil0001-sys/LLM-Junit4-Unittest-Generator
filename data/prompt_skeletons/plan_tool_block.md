TOOL ACCESS — Read, Bash, Edit, Write, Glob, and Grep are available.
WebSearch/WebFetch are denied (local-only). NotebookEdit is denied.

WRITE RESTRICTION (PreToolUse enforced): Edit/Write `*.plan.md` under `data/plans/` AND
test-root harness files (`src/test`, `src/androidTest`, xml/kt hosts, gradle test deps).
Never `src/main` or production sources.

GOAL:
Classify every DELTA line as testable or not_testable, then persist an execute-ready blueprint.
Optional: Write missing harness the coder will need. Chat ack is PLAN_SAVED — Python owns coverage gates.

HARNESS INVENTION (optional this phase):
You may Write missing androidTest/unit harness files listed in INSTRUMENTED HARNESS PATHS
(and gradle test deps) if the plan requires them. Write only SOURCE-required hosts (HiltTestRunner,
manifest with HiltTestApplication + AppTheme, Tier A/B/plain as listed) — not a kitchen-sink dump.
Still REQUIRED: one Write of PLAN OUTPUT FILE (`*.plan.md`) with YAML frontmatter + Markdown blueprint.

SIBLINGS:
If this prompt lists sibling *Test.kt paths: Read at most one for host/runner/`@Config` patterns;
do not copy whole suites. If none listed: do not Glob/Bash the repo looking for `*Test.kt`.

DISCOVERY:
Read existing files freely (SOURCE, TARGET, docs, recipes, `**/build/generated/**` for signatures).
A missed Read is a hint — Read another real file next; do not freeze on Glob/Grep.
Do not Read `build/intermediates` junk. Never page files with offset/limit.
Do not import generated FQCNs whose only `.kt`/`.java` lives under `build/generated` —
plan with mock, stub, or reflection harness (Apollo: newType / newNested / invokeSuspend).

Read:
  Use when: absolute paths to an existing FILE (SOURCE, TARGET, find/grep hit,
    VERIFIED LIBRARY API DOCS, VERIFIED TEST RECIPES, or `**/build/generated/**`).
  Read the PIPELINE TARGET *Test.kt once when the excerpt is incomplete.
  Do not: pass a directory; Read `build/intermediates`.
  Hard rule: never call Read with empty input.
  Prefer Glob/Grep/Bash under the owning module to find types imported by SOURCE —
  do not invent filenames.
Glob / Grep:
  Use to discover hosts, manifests, nav graphs under the owning module.
  Do not hunt `*Test.kt` unless siblings are listed in this prompt.
Bash:
  Any local command allowed (`ls`, `cat`, `pwd`, `python3`, find/grep). Prefer find/grep when
  Glob/Grep is not enough. Examples:
    ls -la /abs/feature/onboarding
    find /abs/feature/onboarding -name '*nav_graph.xml'
    grep -r 'HiltTestActivity' /abs/feature/onboarding
    find /abs/feature/onboarding/build/generated -name '*.kt'
  Optional: `./gradlew` for THIS MODULE's unit/androidTest compile+test.
  Never network commands: `curl`, `wget`, `ssh`, `pip`, `npm`, `git clone|fetch|pull|push`.
  Do not: re-run the same find/grep.
  On "Wasted call", change tool or path — do not retry.
Write:
  REQUIRED: one Write creates the absolute PLAN OUTPUT FILE (`*.plan.md`) with full frontmatter + body.
  The PLAN OUTPUT FILE does not exist yet — expected. Write creates it (never Edit it first).
  OPTIONAL: Write missing harness under src/test or src/androidTest and test deps in build.gradle(.kts)
    derived from SOURCE shape. Do not Write production src/main.
Edit:
  Use to amend a `*.plan.md` already written, or to amend harness files that already exist.
Read SOURCE, PIPELINE TARGET, and VERIFIED LIBRARY API DOCS as needed
to classify residual/short-circuit vs new testable work.
Forbidden: Write/Edit on src/main. WebSearch/WebFetch are denied.
After tools and Write: chat answer is a short ack only — `PLAN_SAVED`.
