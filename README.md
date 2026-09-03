# UnitTest_gen Tool Overview

Single offline pipeline for generating Kotlin/JUnit4 tests for Android Gradle modules, verifying via Gradle/Kover and (optionally) JaCoCo, and repairing only generated test code until acceptance tasks pass.

This doc consolidates the previous `UnitTest_gen/doc/` docs plus the AgenticLLM README into one concise, on-point reference.

---

## Stack at a glance

| Layer | Key path(s) | Responsibility |
|---|---|---|
| Offline agent | `UnitTest_gen/AgenticLLM/` | llama.cpp servers + Claude Code CLI + local proxy ports |
| CLI entrypoints | `UnitTest_gen/AI_Unittestgenerator.py`, `UnitTest_gen/multifile_orchestrater.py` | parse flags/env, pick targets, run pipeline |
| Core runtime | `UnitTest_gen/core/` | model runtime, agent transport, hooks, Gradle runner, caches, diagnostics |
| Kotlin pipeline | `UnitTest_gen/kotlin/` | plan/coder orchestration, validation, coverage parsing, layer assignment |
| Data assets | `UnitTest_gen/data/` | recipes, prompt skeletons, validation rules, and prompt decision blocks |

---

## Quick start

### 1) Start the local offline agent stack

```bash
cd UnitTest_gen/AgenticLLM
cp .env.example .env   # set MODEL_PATH / ANTHROPIC_MODEL (and optional ports/keys)
./scripts/setup.sh
./scripts/start-llama-server.sh
./scripts/health-check.sh --models
```

Ports:

| Service | Port |
|---|---:|
| llama-server | 8080 |
| Sampling proxy (OpenAI-compatible `/v1/messages`) | 8081 |

### 2) Run the generator

From the repo root:

```bash
python UnitTest_gen/AI_Unittestgenerator.py \
  --plan-env claude --coder-env claude \
  -p path/to/Source.kt \
  -R /path/to/android/project
```

Notes:
- The generator starts/uses llama-server automatically unless disabled via CLI/env.
- `--agent-model` and `--agent-model-path` override the model alias/path resolved from `UnitTest_gen/AgenticLLM/.env`.

---

## Tool flow (high level)

```mermaid
flowchart LR
  CLI[AI_Unittestgenerator.py / multifile_orchestrater.py] --> Gen[kotlin/generator.py]
  Gen --> Pipe[kotlin/pipeline.py]

  Pipe --> Plan[kotlin/plan.py<br/>build + clamp plan]
  Pipe --> Coder[kotlin/codegen.py<br/>extract + generate + merge + validate]

  Plan --> ValPlan[plan markdown validation + frontmatter sync]
  Coder --> ValGen[validate_generated_test_code<br/>static guardrails]
  Coder --> Gradle[core/gradle.py<br/>run acceptance tasks + locks]

  Gradle --> Coverage[kotlin/coverage.py<br/>parse Kover/JaCoCo + unify]
  Coverage --> Gate[acceptance gate(s)]

  Gate -->|fail| Repair[repair loop<br/>tickets + fix turns]
  Repair --> Coder
  Gate -->|pass| Done[write test outputs]

  Pipe --> Hooks[Claude hooks<br/>.claude/hooks/remap_read_path.py<br/>-> core/hooks.py]
  Hooks --> Coder
```

---

## Layered architecture (what owns what)

```mermaid
flowchart TD
  subgraph AgentStack[Offline AgenticLLM stack]
    Llama[llama-server (8080)]
    Proxy[Sampling proxy (8081)]
    Claude[Claude Code CLI wrapper]
    Llama --> Proxy --> Claude
  end

  subgraph CoreRuntime[Core runtime (Python)]
    Config[core/config.py]
    LLM[core/llm.py]
    Agent[core/agent.py]
    Hooks[core/hooks.py]
    Gradle[core/gradle.py]
    IO[core/io.py]
    Diag[core/diagnostics.py]
  end

  subgraph KotlinPipeline[Kotlin pipeline (Python orchestration)]
    Generator[kotlin/generator.py]
    Pipeline[kotlin/pipeline.py]
    Plan[kotlin/plan.py + kotlin/prompts.py]
    ValidatePlan[plan validation/clamp]
    Codegen[kotlin/codegen.py]
    Validate[validate.py (data-driven rules)]
    Coverage[kotlin/coverage.py]
    Layer[kotlin/layer.py (exclusive layer policy)]
  end

  subgraph DataAssets[Data assets]
    Recipes[data/recipes/]
    Skeletons[data/prompt_skeletons/]
    Rules[data/validation_rules/]
    ApiDocs[data/api_doc/]
  end

  Claude --> LLM
  Config --> Generator
  Generator --> Pipeline
  Pipeline --> Plan
  Pipeline --> Codegen
  Plan --> ValidatePlan
  Codegen --> Validate --> Coverage
  Pipeline --> Layer
  Recipes --> Plan
  Skeletons --> Plan
  Rules --> Validate
  ApiDocs --> Plan

  Gradle --> Coverage
  IO --> Coverage
  Diag --> Coverage
```

---

## Configuration (critical only)

### CLI: key flags for the main generator

| Flag | Effect |
|---|---|
| `-R` / `--project-root` | Android/Gradle project root (required) |
| `-p` / `--path` OR `-F` / `--folder` | target Kotlin file or folder (path/folder mutex) |
| `--pipeline-phase` | `both` / `plan` / `coder` |
| `--env` / `--plan-env` / `--coder-env` | choose which offline agent env to use |
| `--test-mode` | `auto` / `unit` / `instrumented` |
| `--no-post-validation` | skip deterministic post-agent static validation |
| `--agent-timeout` | seconds per agent invocation |
| `--disable-kover-acceptance` / `--disable-jacoco-acceptance` | skip acceptance gates |
| `--no-auto-start-local-llm` / `--skip-server-preflight` | manage local server startup behavior |
| `-G` / `--gradle-task` | append coverage/verify tasks (optional) |

### TESTGEN env: key variables

Keep these minimal (examples show common usage):

- `TESTGEN_AGENT_ENV` (default: `claude`)
- `TESTGEN_PIPELINE_PHASE` (default: `both`)
- `TESTGEN_LOCAL_LLM_ROOT` (default: `UnitTest_gen/AgenticLLM`)
- `TESTGEN_LLAMA_PORT` / `TESTGEN_PROXY_PORT` (default: `8080` / `8081`)
- `TESTGEN_AGENT_MODEL` / `TESTGEN_AGENT_MODEL_PATH` (override model alias/path)
- `TESTGEN_TEST_MODE` (default: `auto`)
- `TESTGEN_ENABLE_GUARDRAILS` (maps to deterministic post-agent validation)
- `TESTGEN_*_TEMP` and thinking/budget toggles (affect sampling/thinking per phase)
- `TESTGEN_COVERAGE_BUCKETS` (controls safe/attemptable/blocked coverage buckets)

### PipelineConfig defaults (authoritative)

From `UnitTest_gen/core/config.py`:

- `agent_timeout_seconds`: `10800` (180 min)
- `plan_temperature`: `0.3`
- `coder_temperature`: `0.2`
- `fix_temperature`: `0.1`
- `enable_guardrails`: `False` (post-agent static validation is disabled by default)

---

## Key policies (critical)

### 1) Exclusive test layer assignment

Generator runs in `auto` mode by default:
- Each clamped plan item is assigned exclusively to `unit` (`src/test`, Kover gate) or `instrumented` (`src/androidTest`, JaCoCo gate).
- The same delta lines are not generated in both layers.

### 2) Mocking lane strategy

Rule: one mocking framework per Kotlin test file.

- If the target is blank: planner defaults `mocking_lane: mockito` and writes fixture syntax in Mockito-Kotlin.
- If the target already uses MockK: lane is pinned to MockK.
- If the target already uses Mockito: lane is pinned to Mockito-Kotlin.
- Mixed frameworks in one file are rejected by validation rules.

### 3) Plan loop vs coder/repair loop (responsibilities)

Plan turn:
- Write a slim plan frontmatter and sections for the selected slice.
- Clamp to keep stable IDs/targets and validate plan markdown structure.

Coder/repair turns:
- Generate/merge extract + validate generated code.
- Run Gradle acceptance tasks.
- On failure: produce `RepairTicket` lists and apply fix turns that only modify generated test code.

---

## Coverage and gap handling (critical)

Coverage sources of truth:
- Unit/Kover gaps: Kover XML reports (module `build/reports/kover/` or unified unit report paths).
- Instrumented/JaCoCo gaps: JaCoCo XML + unified instrumented report paths.

Deferrals:
- If a line cannot be exercised honestly in unit tests, it is recorded into the unreachable-defer store with a concrete reason.
- The pipeline re-tightens deferrals after Kover refresh to avoid stale “not possible” entries.

---

## Gradle prerequisites (required for reliable pipeline output)

For each Android library module you test, apply:

```kotlin
apply(from = rootProject.file("gradle/testgen-coverage.gradle"))
```

This shared Gradle script configures:
- Kover plugin + consistent excludes
- JaCoCo coverage for `androidTest` variants
- Stable verification tasks and report paths.

Stable XML paths (module-relative):
- Unit (preferred): `module/build/reports/unit-coverage/report.xml`
- Instrumented (preferred): `module/build/reports/instrumented-coverage/report.xml`

When instrumented gaps are selected without an existing androidTest harness, the pipeline auto-bootstrap helper content (manifest, Hilt test hosts, nav graph stubs) before seeding targets.

---

## Minimal manual references (when you need to author harnesses by hand)

- Instrumented tests use tiered harness hosts: one for layout/lifecycle attach-only flows (Tier A) and one for dialog/navigation/deeplink flows (Tier B).
- The generator’s instrumented layer selection and bootstrap logic exist to reduce harness drift; for high-cost harness cases, follow the same host tier rules and stub deep-link destinations.

---

## Repo outputs (where to look)

- Generated tests land under:
  - `src/test/java/...` for unit items
  - `src/androidTest/java/...` for instrumented items
- Logs, diagnostics, and parsed coverage summaries are emitted from the core diagnostics + coverage modules (for inspection during acceptance/repair).

