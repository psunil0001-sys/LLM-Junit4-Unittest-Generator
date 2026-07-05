# Local Kotlin Unit Test Generation

## Goal

`UnitTest_gen` generates Kotlin/JUnit4 tests for Android Gradle modules using a local OpenAI-compatible `llama.cpp` server, verifies the result with Gradle/Kover through MCP, and repairs only generated test code until the selected Gradle tasks pass.

The current baseline is intentionally single-agent and classification-driven:

```text
source classifier -> tailored RAG/context -> focused prompt modules -> validation -> Gradle -> repair loop
```

This is simpler and more deterministic than the previous planner/coder/reviewer/fixer split on single-worker local hardware.

## Key Files

| File | Purpose |
|---|---|
| `AI_Unittestgenerator.py` | Public CLI entry point. |
| `kotlin/generator.py` | Kotlin/Android pipeline orchestration. |
| `kotlin/prompts.py` / `kotlin/guardrail_catalog.py` | Generation rules and the shared prompt/validation guardrail catalog. |
| `core/rag_context.py` | Reusable deterministic RAG ranking for rules, memory, dependencies, and docs. |
| `core/prompt_assembly.py` | Generic prompt-rule parsing, retrieval, budgeting, and section assembly. |
| `core/vector_cache.py` / `core/vector_index.py` | Source-scoped vector-cache persistence and generic index lifecycle. |
| `core/model_runtime.py` | Chat/embedding clients, prompt printing, stuck detection, and slot-cache helpers. |
| `core/server_manager.py` | Optional Python-managed `llama-server` startup/shutdown. |
| `core/mcp_tools.py` | Generic MCP stdio wrapper for file tools and project search. |
| `kotlin/mcp_tool_adapter.py` | Kotlin/Gradle MCP adapter for Gradle heartbeat and Kotlin declaration lookup. |
| `kotlin/gradle_analysis/` | Gradle/JUnit parsing, causal grouping, and fingerprints. |
| `kotlin/coverage_analysis.py` | Kover XML gap classification, missed instruction lines, branch lines, and method counters. |
| `kotlin/incremental_coverage.py` | Shared Kover bucket planning, CLI-prioritized safe/attemptable/blocked selection, strategy text, and bounded attempts. |
| `kotlin/project_context.py` | Module discovery, project indexing, Fragment context, and verified Android resource lookup. |
| `kotlin/static_analysis.py` | Tree-sitter extraction, strict Pydantic models, and Semgrep policy findings. |
| `kotlin/test_code_utils/` | Generated-code extraction, merge, normalization, and optional local validation. |
| `kotlin/repair_flow.py` | Ownership-aware Gradle/JUnit repair orchestration. |
| `core/memory_store.py` | Stateless persisted lesson add/retrieve APIs for `ai_test_memory.json`. |
| `core/logging_utils.py` | Per-source logs and generated side-file archival. |
| `core/pipeline_config.py` | Shared environment flags, CLI overrides, and active runtime config. |
| `helper/dashboard/blocked_coverage_kover_report.py` | Path-based Kover XML discovery, gap dashboards, and HTML report orchestration. |
| `helper/dashboard/kover_coverage_summary.py` | Interactive coverage-summary HTML from discovered module Kover XML reports. |
| `helper/chatbot/codebase_chatbot.py` | RAG REPL over UnitTest_gen docs and Python source. |

## Reusable Core Services

`core/` contains mechanics that can be reused by other generators:

| Core service | Reusable responsibility |
|---|---|
| RAG ranking | Rank any context item by tags, lexical terms, priority, phase, and budget. |
| Prompt assembly | Parse rule text, retrieve relevant rules, deduplicate sections, and build model messages. |
| Vector cache/index | Keep source-scoped cache files safe for parallel runs and reuse MD5/vector metadata. |
| Memory store | Load, sanitize, retrieve, and save successful lessons without sending the full JSON to the model. |
| Model runtime | Stream OpenAI-compatible chat responses, call embeddings, print prompts, and detect stuck streams. |
| MCP client | Generic allowed-root file/search MCP calls; Kotlin-specific Gradle logic stays in `kotlin/`. |
| Logging/server management | Per-source logs, artifact archival, and optional local `llama-server` process management. |

## Runtime Setup

Build `llama.cpp` with CUDA:

```bash
cd /home/pathipatisunilkumar/llama.cpp
cmake -B build -DGGML_CUDA=ON -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release -j
```

The generator expects:

```bash
TESTGEN_CHAT_BASE_URL=http://127.0.0.1:8080/v1
TESTGEN_EMBEDDING_BASE_URL=http://127.0.0.1:8081/v1
```

Endpoint variables:

| Variable | Meaning |
|---|---|
| `TESTGEN_CHAT_BASE_URL` | OpenAI-compatible chat endpoint used for generation and repair. |
| `TESTGEN_CHAT_MODEL` | Chat model name sent to the OpenAI-compatible endpoint. |
| `TESTGEN_EMBEDDING_BASE_URL` | Separate embedding endpoint used only while building vector cache. |
| `TESTGEN_ENABLE_THINKING` | Default `0`. When `1`, request payloads include thinking fields and the default managed coding server adds reasoning flags. |
| `TESTGEN_ENABLE_GUARDRAILS` | Default `1`. Set to `0` to skip deterministic generated-test validation before Gradle. Prompt guardrail text remains classification-driven in either mode. |
| `TESTGEN_ENABLE_MEMORY_LESSONS` | Default `0`. Set to `1` to retrieve bounded classification-matched lessons into generation and repair prompts. |

Server command variables used by `--auto-start-servers`:

```bash
# Enable Python-managed server startup for this generator run.
export TESTGEN_AUTO_START_SERVERS=1

# Directory where the coding llama-server command is executed.
export TESTGEN_CODING_SERVER_CWD=/home/pathipatisunilkumar/llama.cpp

# Default model directory and coding model path used by server_manager.py.
export TESTGEN_MODEL_DIR=/home/pathipatisunilkumar/models
export TESTGEN_CODING_MODEL_PATH=/home/pathipatisunilkumar/models/Qwen3.6-35B-A3B-UD-Q8_K_XL.gguf

# Full command used to start the Qwen coding server on port 8080.
export TESTGEN_CODING_SERVER_COMMAND='<coding server command shown below>'

# Directory where the embedding llama-server command is executed.
export TESTGEN_EMBEDDING_SERVER_CWD=/home/pathipatisunilkumar/llama.cpp

# Full command used to start the embedding server on port 8081.
export TESTGEN_EMBEDDING_SERVER_COMMAND='<embedding server command shown below>'

# Maximum seconds to wait for auto-started /v1/models endpoints.
export TESTGEN_SERVER_STARTUP_TIMEOUT=120

# Llama slot save directory; server_manager.py creates it before startup.
export TESTGEN_LLAMA_SLOT_SAVE_PATH=/home/pathipatisunilkumar/models/server_slots

# Set to 0 to start servers as background processes instead of terminal windows.
export TESTGEN_SERVER_TERMINAL=1
```

Default non-thinking coding server, run from `/home/pathipatisunilkumar/llama.cpp`:

```bash
export TESTGEN_ENABLE_THINKING=0

./build/bin/llama-server \
  -m /home/pathipatisunilkumar/models/Qwen3.6-35B-A3B-UD-Q8_K_XL.gguf \
  --ctx-size 65536 \
  --n-predict 24576 \
  -fitt 768 \
  --flash-attn on \
  --parallel 1 \
  --no-mmap \
  --mlock \
  --no-warmup \
  --threads 12 \
  --cont-batching \
  --timeout 300 \
  --port 8080 \
  --host 127.0.0.1 \
  --jinja \
  --metrics \
  --spec-type draft-mtp \
  --spec-draft-n-max 2 \
  --slot-save-path /home/pathipatisunilkumar/models/server_slots/
```

Thinking-capable models can opt in:

```bash
export TESTGEN_ENABLE_THINKING=1
# server_manager.py adds:
#   --reasoning on
#   --reasoning-format deepseek
```

Keep `--embedding` off the coding server. In this `llama.cpp` build, that flag makes the server embedding-only and breaks chat/generation requests.

`server_manager.py` normalizes dangling trailing `\` characters before launch. If the command contains `--slot-save-path`, it creates the directory before starting `llama-server`.

Coding server flags:

| Flag | Meaning |
|---|---|
| `-m` | Qwen GGUF model used for Kotlin generation and repair. |
| `--ctx-size 65536` | Long context for source, dependency context, logs, memory, and repair prompts. |
| `--n-predict 24576` | Allows large test files and full-file repairs. |
| `-fitt 768` | Local llama.cpp tuning flag used by this tested runtime. |
| `--flash-attn on` | Improves long-context speed and memory use. |
| `--parallel 1` | Keeps one active request slot for deterministic single-worker runs. |
| `--no-mmap` | Loads model into memory instead of mmap. |
| `--mlock` | Locks model memory to reduce paging. |
| `--no-warmup` | Starts faster for iterative local runs. |
| `--threads 12` | CPU threads used for server-side work. |
| `--cont-batching` | Enables continuous batching in server mode. |
| `--timeout 300` | Gives long generations time to finish. |
| `--port 8080`, `--host 127.0.0.1` | Local chat server address. |
| `--jinja` | Uses the model chat template. |
| `--metrics` | Exposes llama.cpp runtime metrics. |
| `--reasoning on` | Added only when `TESTGEN_ENABLE_THINKING=1`; streams reasoning separately when supported. |
| `--reasoning-format deepseek` | Added only when thinking is enabled; emits DeepSeek-compatible reasoning. |
| `--spec-type`, `--spec-draft-n-max` | Optional speculative decoding settings for speed. |
| `--slot-save-path` | Location for optional llama slot `.bin` cache. |

Request-level sampler overrides:

| Variable | Default | Meaning |
|---|---:|---|
| `TESTGEN_REQUEST_TEMPERATURE` | `0.1` | Default request temperature when a prompt call does not pass one explicitly. |
| `TESTGEN_REQUEST_TOP_P` | `0.95` | Request-level nucleus sampling value. |
| `TESTGEN_REQUEST_MIN_P` | `0.05` | Request-level llama.cpp `min_p`. |
| `TESTGEN_REQUEST_TOP_K` | `20` | Request-level llama.cpp `top_k`. |
| `TESTGEN_REQUEST_PRESENCE_PENALTY` | `0.0` | Request-level presence penalty. |
| `TESTGEN_REQUEST_REPEAT_PENALTY` | `1.05` | Request-level repeat penalty. |
| `TESTGEN_LOG_REQUEST_SAMPLING` | `0` | Set to `1` to print the sampler payload sent with each OpenAI-compatible chat request. |
| `TESTGEN_GRADLE_HEARTBEAT_SECONDS` | `30` | Optional heartbeat interval while Gradle is still running through MCP. Minimum effective value is 5 seconds. |

Embedding server, only needed when building real semantic vectors:

```bash
./build/bin/llama-server \
  -m /home/pathipatisunilkumar/models/nomic-embed-text-v1.5.Q6_K.gguf \
  --embedding \
  --ctx-size 8192 \
  --batch-size 2048 \
  --ubatch-size 2048 \
  --rope-scaling yarn \
  --rope-freq-scale 0.75 \
  --port 8081 \
  --host 127.0.0.1
```

Embedding server flags:

| Flag | Meaning |
|---|---|
| `-m` | Embedding-capable GGUF model. |
| `--embedding` | Enables embedding endpoint mode. |
| `--ctx-size 8192` | Context window for embedding input chunks. |
| `--batch-size`, `--ubatch-size` | Batch sizes large enough for source snippets. Increase if the server rejects inputs as too large. |
| `--rope-scaling`, `--rope-freq-scale` | Long-context settings for the embedding model. |
| `--port 8081`, `--host 127.0.0.1` | Local embedding server address. |

With `--auto-start-servers`, `server_manager.py` can start these servers from Python. Auto-started servers are launched in their own process groups with PID files, so cleanup stops the real `llama-server` on success, failure, Ctrl+C, or exception. Servers that were already running before the script started are left running.

## Memlock for `--mlock`

Permanent setup:

```bash
sudo nano /etc/security/limits.conf
```

Add:

```text
pathipatisunilkumar    soft    memlock    unlimited
pathipatisunilkumar    hard    memlock    unlimited
```

Ensure `/etc/pam.d/common-session` contains:

```text
session required pam_limits.so
```

Reboot and verify:

```bash
ulimit -l
```

Temporary current-shell setup:

```bash
sudo prlimit --pid $$ --memlock=unlimited
ulimit -l unlimited
```

## Main Command

Single source file:

```bash
python3 UnitTest_gen/AI_Unittestgenerator.py \
  -p common/api/src/main/java/com/example/app/api/ApiExt.kt \
  -R /path/to/project \
  -mcp UnitTest_gen/kotlin/mcp_gradle_server.py \
  --gradle-task :common:api:testDevDebugUnitTest \
  --gradle-task :common:api:koverHtmlReportDevDebug \
  --gradle-task :common:api:koverXmlReportDevDebug \
  --auto-start-servers
```

Folder:

```bash
python3 UnitTest_gen/AI_Unittestgenerator.py \
  -F common/api/src/main/java/com/example/app/api \
  -R /path/to/project \
  -mcp UnitTest_gen/kotlin/mcp_gradle_server.py \
  --gradle-task :common:api:testDevDebugUnitTest
```

Useful flags:

| Flag | Use |
|---|---|
| `--index-root <path>` | Build one temporary dependency/vector cache for a module/project path and reuse it for all queued files in this run. |
| `--disable-incremental-coverage` | Ignore existing tests and generate a full test file. |
| `--coverage-buckets safe,attemptable` | Select one or more incremental buckets. Allowed values are `safe`, `attemptable`, and `blocked`; default is `safe,attemptable`. |
| `--gradle-offline` | Run Gradle through MCP with offline mode. |
| `--skip-model-preflight` | Skip quick `/models` check. |
| `--enable-stuck-detector` | Enable stream repetition detection. |
| `--enable-slot-bin-cache` | Restore/save llama slot `.bin` for this run. |
| `--disable-slot-bin-cache` | Disable slot `.bin` even when env is enabled. |
| `--no-server-terminal` | Start auto-managed servers in background instead of terminal windows. |

## Generation Flow

1. Resolve target path and owning source root.
2. Retrieve bounded `ai_test_memory.json` lessons by source classification.
3. Start MCP with `MCP_ALLOWED_ROOT`.
4. Build a temporary source-scoped `vector_cache_<source>_<hash>.json`.
5. Classify the source by shape and domain: Fragment, Hilt, ViewModel, Flow, Apollo, Android UI/navigation, Car UI, storage/Auth, network, Room, worker/service, Firebase/logging, and complex Fragment markers.
6. Gather Tree-sitter/Semgrep/Pydantic static-analysis context, up to three structural/semantic dependency examples, Apollo generated signatures, classification-matched memory lessons, and source risk signals.
7. Rank retrieved dependency context, memory, and nearby test examples using source classification instead of sending unrelated context.
8. Assemble a context-first, rules-last prompt from only the relevant rule modules.
9. Add bounded verified Android resource context from active-variant `R.txt`, source resources, layouts, generated bindings, and ViewBinding fields referenced by the selected trigger.
10. Stream Kotlin output from the local model. Reasoning is requested only when `TESTGEN_ENABLE_THINKING=1`.
11. Extract and normalize the test code. When `TESTGEN_ENABLE_GUARDRAILS=1`, validate it locally before Gradle.
12. Run Gradle/Kover through MCP with heartbeat logs while the build is still running.
13. On failure, group compiler/runtime/JUnit errors, filter to failures owned by the generated test, collect exact line/JUnit XML/source/resource context, and repair with JSON patch first or full-file fallback.
14. Normalize repairs and, when enabled, validate them before rerunning Gradle.
15. Keep a generated or merged test only after Gradle passes and Kover proves a relevant improvement; otherwise restore/delete it.
16. Save memory only after final Gradle and Kover acceptance.
17. Archive side logs and delete temporary vector cache.

Model reasoning is stored in the per-source log as `MODEL REASONING` only when the server streams reasoning content. With `TESTGEN_ENABLE_THINKING=0`, the client omits `chat_template_kwargs.enable_thinking` and `thinking_budget_tokens`. Interactive terminals show one blinking `Processing prompt... MM:SS elapsed` line until the first model output, then one blinking `Thinking... MM:SS elapsed` line while hidden reasoning streams; both timers stop before final output/Gradle logs. Redirected output receives plain status lines.

## Existing Tests and Kover

When a matching test file already exists, the default mode is incremental coverage:

1. Run a fresh Gradle/Kover baseline when the source or test is newer than the selected report.
2. Match Kover by module, package, source filename, and task context, then map missed lines/branches to the narrowest AST declaration and public-entry path.
3. Classify the full safe, attemptable, alternative, and blocked opportunity inventory from Kover plus Tree-sitter/static-planner evidence. Callback spans cover actual lambda/object bodies; unknown callbacks without a verified trigger are blocked rather than false-safe.
4. Read `--coverage-buckets` from the active `PipelineConfig`. The Kover context prints the selected values. Default selection is `safe,attemptable`; `blocked` is sent only when explicitly selected.
5. Select the largest compatible enabled fixture group using strict priority `safe` then `attemptable` then `blocked` (`TESTGEN_INCREMENTAL_SAFE_CAP`, effective default `2`; `TESTGEN_INCREMENTAL_LINE_BUDGET`, default `80`). Only one phase/fixture group is sent in a model request.
6. Build trigger recipes from the selected opportunities only. Branch-only gaps complement existing call shapes rather than repeating already-covered inputs.
7. Generate an in-memory candidate, reject unchanged/semantic duplicates, and verify that required clicks, emissions, callbacks, schedulers, or observations are present.
8. Structure-aware merge preserves unique tests, fields, helpers, imports, and required lifecycle statements while consolidating equivalent members.
9. Run local guardrails, ownership-aware Gradle repair, and Kover acceptance on the merged file. Static validation uses one full-file repair first; a new validation category gets focused JSON patch repair, while any repeated category stops the candidate.
10. Reuse fresh successful Gradle/Kover output from repair when the candidate did not change. Run again, with one `--rerun-tasks` fallback, only when freshness cannot be proved.
11. Keep the candidate only when Gradle passes and the selected Kover gap improves. Restore the original file on validation/repair failure, missing Kover proof, or no delta.
12. Record the attempt disposition, reject its fingerprint for the current run when it fails, rebuild from fresh Kover after an accepted change, and continue within the configured attempt budget.

When no matching test file exists but Kover shows indirect coverage from other tests, the script can still generate a standalone `*CoverageSupplementTest.kt`. That standalone path is not used to validate merges into an existing test file.

Every incremental cycle uses the same acceptance rule: owned Gradle/JUnit success plus measurable Kover improvement. Accepted cycles can continue to newly remaining gaps; no-delta and pipeline failures do not become source blockers.

Full generation follows the same evidence rule. It captures a baseline before writing a candidate, restores an existing test or deletes a new one when acceptance fails, and records a hard stop when Kover proof cannot be obtained.

## Classification-Driven Prompt and RAG Assembly

The generator no longer treats every file as one generic Android problem. Before prompt construction it assigns categories from the production source shape:

| Category | Prompt/RAG effect |
|---|---|
| Fragment / Android UI | Adds lifecycle, Robolectric, view, and navigation rules. |
| Hilt Fragment | Adds Hilt host Activity, `HiltTestApplication`, and verified binding rules. |
| Delegated ViewModel | Avoids `@BindValue ViewModel` for `by viewModels()` / `by activityViewModels()` and prefers real delegated instances or safer public-contract tests. |
| Large complex Fragment | Uses fewer, safer lifecycle tests instead of broad observer/navigation matrices. |
| Car UI / toolbar | Adds `CarUi.requireToolbar(...)`, static mock, and `toolbar.progressBar` rules. |
| Apollo mapper | Adds reflection-only generated type handling. |
| Flow / coroutine / ViewModel | Adds dispatcher, `InstantTaskExecutorRule`, and state assertion rules. |
| Room / network / WorkManager / storage / Firebase | Adds only the matching framework section when those markers are present. |

This reduces prompt noise. For example, a complex Hilt Fragment that uses Car UI receives Fragment, Hilt, navigation, delegated ViewModel, and Car UI guidance, but not Room, WorkManager, Retrofit, or AppAuth instructions.

The same category data is also used to:

- Retrieve source files and nearby tests through core RAG ranking, after Kotlin filters remove reverse consumers and unrelated examples.
- Label dependency snippets with matched source categories.
- Rank persisted memory lessons and prompt rules using the same generic RAG mechanics.
- Insert a `SOURCE-SPECIFIC TEST STRATEGY` into generation, incremental coverage, and repair prompts.
- Filter prompt rule modules so Kotlin/Android bullets, Apollo rules, selected Android architecture blueprints, and framework blueprints are included only when source categories require them.

## Validation Guardrails

The guardrail catalog is shared by generation prompts, repair intent, and validator codes. Prompt guardrails are always selected by source classification. Deterministic pre-Gradle validation is enabled by default (`TESTGEN_ENABLE_GUARDRAILS=1`; set `0` to disable) and rejects common model mistakes before Gradle:

- JUnit5/instrumentation-only imports in JVM tests.
- Missing JUnit4 imports or private test methods.
- JUnit4 is enforced in generation, incremental, and full-file repair system prompts; Jupiter imports/annotations/assertions are not valid output.
- Invented Apollo nested response types.
- Direct construction or typed Mockito mocks of Apollo operation response receivers.
- Wildcard generated Apollo imports in mapper tests.
- Local fake Apollo `Mutation`/`Query` receiver classes.
- Brittle AppAuth/AuthState patterns such as static `AuthState` mocks, raw token JSON, direct `AuthState.jsonDeserialize(...)`, and untyped overloaded `AuthState.update(any(), any())`.
- MockK imports and DSL usage. The default strategy is Mockito-Kotlin only.
- `assertThrows` around suspend functions that catch their own exceptions.
- Plain `launchFragmentInContainer()` for `@AndroidEntryPoint` Fragment lifecycle tests.
- Resuming a Hilt host Activity before installing the Fragment NavController.
- `@BindValue` ViewModel fields for Fragment properties created by `viewModels()` / `activityViewModels()`.
- Mocked `ToolbarController` usage where source reads `toolbar.progressBar` but the returned progress controller is not handled correctly.
- `Mockito.when(navController.navigate(...))` and Java `Mockito.any(...)` for Kotlin non-null `NavDeepLinkRequest`.
- Mixed Mockito import styles such as importing both `org.mockito.kotlin.any/verify` and `org.mockito.Mockito.any/verify`.
- `Mockito.when(...)` around Unit/void Firebase calls such as `FirebaseAnalytics.logEvent(...)`; use `doThrow`/`doAnswer` only for selected exception paths.
- Java `ArgumentCaptor.forClass(...).capture()` passed into Kotlin non-null navigation APIs.
- Manual Fragment lifecycle re-entry such as calling `onViewCreated(...)` after `commitNow()`.
- Private method/field reflection for Fragment internals and singleton internals.
- `MockedStatic<T>.when { ... }` without an explicit return type for static singleton `getInstance()` APIs.
- Unverified project-owned `R.type.name` references and guessed ViewBinding-to-ID conversions.

Apollo mapper tests should use Java reflection and raw Mockito mocks for generated operation response receivers:

```kotlin
val receiverClass = Class.forName("...Operation$Receiver")
val receiver = org.mockito.Mockito.mock(receiverClass)
org.mockito.Mockito.`when`(receiverClass.getMethod("getStatusCode").invoke(receiver)).thenReturn(200)
val result = Class.forName("com.example.generated.api.ApiExtKt")
  .getDeclaredMethod("toCustomResponse", receiverClass)
  .invoke(null, receiver)
```

Project DTOs/models and GraphQL input classes can still be constructed directly when verified from source/generated signatures.

## Verified Android Resources

Resource context is bounded to references used by the source and selected coverage trigger:

1. Prefer the active variant's generated `R.txt`.
2. Fall back to module `src/*/res` XML and resource filenames.
3. Resolve ViewBinding fields from generated binding classes or the owning layout.
4. Exempt `android.R` and external dependency resources from local-project validation.
5. Auto-correct only a unique same-type match; ambiguous resources go to focused repair and are never guessed.

Missing or guessed test resources are `pipeline_unresolved`, not proof that production coverage is blocked.

Android `Context`, `ApplicationProvider`, resources, and Robolectric sources also receive module SDK guidance. The prompt and validator read the owning module `minSdk`/`compileSdk`, check installed SDK platforms, and prefer a valid `@Config(sdk = [...])` only when explicit SDK config is needed.

## Repair Loop

Gradle is the source of truth. The repair loop:

- Groups errors by root cause and exact file/line location.
- Uses JUnit XML to split runtime failures by test method, failure type, and normalized failure message.
- Repairs only error groups owned by the current generated test path or generated test class.
- Logs external module failures separately; when owned Mockito/static misuse exists, later external failures are marked as possible contamination.
- Reads generated-test code around failing lines and caret locations.
- Searches verified source declarations and nearby context through MCP.
- Applies JSON patches when possible.
- Falls back to full-file repair when needed.
- Uses source-specific strategy during repair, so complex Fragments do not drift into broad observer-state rewrites.
- Preserves useful passing coverage and avoids changing production code or unrelated tests.

The script repairs generated tests only. Production code and unrelated tests are not modified.

## Coverage Accountability

| Disposition | Meaning |
|---|---|
| `covered` | Gradle passed and current Kover proves the selected gap improved. |
| `safe_pending` | A deterministic public trigger is available but has not been accepted yet. |
| `attemptable_pending` | A controlled trigger exists but needs a fixture, callback, scheduler, or framework state. |
| `measured_no_delta` | The candidate passed execution but did not reduce the selected Kover gap. |
| `blocked_source` | Production structure has no verified public trigger or injectable seam. |
| `blocked_environment` | The required build variant, dependency, SDK, or runtime environment is unavailable. |
| `pipeline_unresolved` | Generation, validation, merge, repair, Gradle, resource lookup, or Kover verification failed. This is not a source blocker. |

Per-source blocked reports use the stable `{Source}.blocked-coverage.md` filename. Project-level Kover dashboards call the same planner used by generation and rebuild current safe, attemptable, blocked, excluded, and attempted-but-failed classifications. Each row preserves source-specific `Why`, `Recommended action`, `What to test next`, evidence, and provenance. Accepted improvements are never reported as blocked.

## Kover HTML Reports

Reports are built from whatever Kover XML files exist under each module's `build/reports/kover/` directory. Discovery is path-based, not hardcoded to ProdGlobal/China/Korea variant names:

- `common/build/reports/kover/report.xml` and `app/build/reports/kover/report.xml` both contribute to one `default` variant group when only unsuffixed `report.xml` files exist.
- `reportProdGlobalRelease.xml`, `reportProdChinaRelease.xml`, and similar suffixed files form separate variant groups when present.
- One discovered group produces one report pair (`coverage_summary_<variant>.html`, `kover_gaps_<variant>.html`). Multiple groups produce multiple reports plus index pages.

Before each generation pass, `UnitTest_gen/data/htmlreport/` is cleared so stale dashboards are not left behind.

Build reports manually:

```bash
python3 UnitTest_gen/helper/dashboard/blocked_coverage_kover_report.py --summary-only
python3 UnitTest_gen/helper/dashboard/blocked_coverage_kover_report.py --gaps-only
python3 UnitTest_gen/helper/dashboard/blocked_coverage_kover_report.py
```

Useful flags:

| Flag | Use |
|---|---|
| `--root <path>` | Gradle project root to scan for module Kover XML (default: repo root). |
| `--output-dir <path>` | HTML output directory (default: `UnitTest_gen/data/htmlreport`). |
| `--variant <name>` | Filter to one discovered variant group such as `default` or `ProdGlobalRelease` (repeatable). |
| `--include-generated` | Include generated/Hilt sources in gap dashboards. |

Open `UnitTest_gen/data/htmlreport/kover_reports_index.html` for the hub linking coverage summaries and gap dashboards.

Gap-dashboard module options come from every loaded module Kover XML, including fully covered modules with no gap cards. Module, bucket, and reason filters support multiple selections; no selection means all values.

The generator also refreshes gap dashboards through `refresh_kover_gap_reports()` after incremental runs when a Gradle project root can be resolved.

## Developer Doc Chatbot

`helper/chatbot/codebase_chatbot.py` is a terminal REPL over `UnitTest_gen` docs and Python source. It indexes `.md`, `.puml`, and `.py` files, retrieves top-k context, and answers architecture/debugging questions with file-path citations.

```bash
python3 UnitTest_gen/helper/chatbot/codebase_chatbot.py
python3 UnitTest_gen/helper/chatbot/codebase_chatbot.py --reindex --top-k 10
python3 UnitTest_gen/helper/chatbot/codebase_chatbot.py --no-manage-server
```

By default it auto-starts a small local chat server on `http://127.0.0.1:8082/v1` when none is reachable. Use `--no-manage-server` when you already have a compatible endpoint configured through `CHATBOT_BASE_URL` or `TESTGEN_CHAT_BASE_URL`.

## Cache and Memory

`ai_test_memory.json` stores only successful generation/repair lessons after final Gradle and Kover acceptance. It is not loaded as broad model memory. The generator loads it only through stateless helper calls that retrieve a small prompt lesson block matching the current source and repair classification.

Source-scoped vector caches are temporary by default:

- Default: use `vector_cache_<source>_<path-hash>.json`, build for the source root, and delete after that source.
- With `--index-root`: build once for the provided module/project path, reuse for queued files, delete at script cleanup.
- With embeddings unavailable: entries may contain `vector: null`; dependency context falls back to AST/structural references.

Enable real vectors:

```bash
TESTGEN_ENABLE_EMBEDDINGS=1
TESTGEN_EMBEDDING_BASE_URL=http://127.0.0.1:8081/v1
TESTGEN_EMBEDDING_MODEL=nomic-embed-text
TESTGEN_EMBEDDING_MAX_CHARS=1200
```

Slot `.bin` cache is disabled by default:

```bash
TESTGEN_SAVE_LLAMA_SLOT_BIN=0
```

Enable it only when wanted:

```bash
TESTGEN_SAVE_LLAMA_SLOT_BIN=1
# or
--enable-slot-bin-cache
```

Even when enabled, the script saves/overwrites `coding_session.bin` only after a clean successful run. Ctrl+C, exceptions, and failed generations skip slot save.

## Outputs

Expected runtime outputs:

- Kotlin tests under matching `src/test/java` paths.
- Per-source logs under `UnitTest_gen/log`.
- Temporary Gradle/error/context side files, archived after generation.
- Kover XML under module `build/reports/kover`.
- Interactive HTML dashboards under `UnitTest_gen/data/htmlreport` (`coverage_summary_*.html`, `kover_gaps_*.html`, `kover_reports_index.html`).
- Stable per-source blocked reports and refreshed Kover gap dashboards.
- Updated `UnitTest_gen/ai_test_memory.json` after Gradle and Kover acceptance.

Ignored local artifacts:

- `UnitTest_gen/log/`
- `UnitTest_gen/data/htmlreport/` (regenerated and cleared by the Kover report CLI)
- `UnitTest_gen/vector_cache_<source>_<path-hash>.json`
- `UnitTest_gen/refined_prompt_cache.json`
- Python `__pycache__/`
- generated artifact zips

## Architecture Diagram

See:

- `simple_architecture_flow.puml`
- `simple_architecture_flow.svg`
