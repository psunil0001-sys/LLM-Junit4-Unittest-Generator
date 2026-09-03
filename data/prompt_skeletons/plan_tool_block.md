TOOL ACCESS — Read, Bash, Edit, and Write are all available.
WRITE RESTRICTION (PreToolUse enforced): Edit/Write only on `*.plan.md` under `data/plans/`.

DISCOVERY LADDER (hooks enforce after Read fail):
1. Path in SOURCE IMPORTS → Read once.
2. Unknown file (manifest, nav graph, HiltTestActivity, layout) → Bash find under OWNING MODULE DIR (embedded bfs), then Read hit.
3. Unknown symbol → Bash grep under OWNING MODULE DIR (embedded ugrep), then Read hit file.
4. Read denied/missing → next tool MUST be Bash find (file) or grep (symbol); then Read only a returned hit path. Never guess another absolute path. Hooks replace raw "File does not exist" with find/grep instructions.
5. After 2 failed Reads on the same path, Read is denied until find/grep registers a hit; Bash find/grep remain allowed under OWNING MODULE DIR.
6. Never Read build/ or page SOURCE with offset/limit for discovery.

Read:
  Use when: absolute path to an existing FILE (SOURCE, TARGET, a path from Bash find/grep,
    VERIFIED LIBRARY API DOCS catalog paths, or VERIFIED TEST RECIPES catalog paths).
  Do not: pass a directory; invent package dirs under the wrong module;
    treat ****** path segments as CLI redaction (not a missing directory);
    Read any path under **/build/** (including build/generated).
    If SOURCE IMPORTS says unresolved / no project file (generated types): do not invent
    a build/ path; classify and plan with mock, stub, or reflection harness already in
    recipe/TARGET (Apollo: newType / newNested / invokeSuspend — never direct generated imports).
  SOURCE IMPORTS lists imported project files from SOURCE.kt only — not bootstrap/nav/layout paths.
  SOURCE IMPORTS is the final user prompt section; it is the source of truth for file paths.
  Hard rule: never call Read with empty input; `file_path`/`path` must come from SOURCE IMPORTS. If denied/missing, Bash find under OWNING MODULE DIR once.
  At most one Read per path. Do not re-Read a recipe when Ordered write / MUST is already
  inlined in VERIFIED TEST RECIPES. On "Wasted call", change tool or path — do not retry.
Bash (find/grep only):
  Use when: host / sibling discovery or cross-module find by exact name
    (e.g. find MODULE -name 'HostActivity.kt', find MODULE -name '*nav_graph.xml').
    On native Claude, find/grep invoke embedded bfs/ugrep — use find/grep, not raw bfs/ugrep.
  Examples:
    find /abs/feature/onboarding -name '*nav_graph.xml'
    grep -r 'HiltTestActivity' /abs/feature/onboarding
  Do not: browse packages as folders; re-run the same find/grep; re-Read the same path;
    find under build/; run gradlew, rm, python3, or pipeline verify commands.
Write:
  REQUIRED: one Write call creates the absolute PLAN OUTPUT FILE (`*.plan.md`) with the full
    YAML frontmatter + Markdown blueprint (pipeline reads this file only — not chat).
  The PLAN OUTPUT FILE does not exist yet — expected. Write creates it: never Read, find/grep, or
    Edit it first, and never pass an empty old_string to Edit to fake a create.
  Do not: Write SOURCE, *Test.kt, recipes, api_doc, build/, or any non-`*.plan.md` path.
    Path must match PLAN OUTPUT FILE exactly (or another `*.plan.md` under data/plans/).
Edit:
  Use only to amend a `*.plan.md` already written in this session. Creating it is Write.
Read SOURCE, PIPELINE TARGET, and VERIFIED LIBRARY API DOCS as needed
to classify residual/short-circuit vs new testable work (at most one Read per path).
Prefer embedded SLICE SOURCE / TARGET EXCERPT / Kover / inlined recipe MUST when they
already answer the question; for third-party APIs Read/grep listed VERIFIED LIBRARY API DOCS
paths — do not invent signatures.
Recipe MUST / Ordered write steps are already in the user message — do not skip them.
Follow the PRIMARY recipe Ordered write path only; companions are stubs, not a second harness.
Act via public entry points only; private gaps without a public path → not_testable.
Optional Read of a listed recipe path is only for the sketch, never a substitute.
Use absolute paths from SOURCE FILE / imports; local/ and remote/ are real package dirs.
For partial-branch-only deltas (Missed lines empty, Partial branch lines non-empty):
Read the PIPELINE TARGET *Test.kt once before deciding testable vs not_testable.
After tools and Write: chat answer is a short ack only — `PLAN_SAVED` (optionally plus the
PLAN OUTPUT path). Do not paste the plan body, YAML, or --- into the answer.
Forbidden: Write/Edit on non-`*.plan.md`; Gradle/bash beyond find/grep discovery — pipeline-owned.
