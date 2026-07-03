# UnitTest_gen Module Table of Contents

This document is a developer map for understanding and debugging the unit-test generator. It lists the current public entrypoint, reusable `core` modules, Kotlin/Android generator modules, and helper scripts.

## Module Summary

| Module | One-line responsibility |
|---|---|
| `AI_Unittestgenerator.py` | Public CLI entrypoint that starts the async Kotlin/Android generator. |
| `core/logging_utils.py` | Pipeline logging, console output, log directories, and generated side-file archival. |
| `core/mcp_tools.py` | Generic MCP stdio client with safe file and project-search helpers. |
| `core/memory_store.py` | Stateless lesson store for sanitized generation/repair lessons retrieved by source and repair tags. |
| `core/model_runtime.py` | OpenAI-compatible chat streaming, embedding access, prompt printing, stuck detection, and runtime cache state. |
| `core/prompt_assembly.py` | Generic prompt-rule parsing, retrieval, section assembly, templates, and prompt message bundles. |
| `core/rag_context.py` | Deterministic tag/term/priority ranking and formatting for retrieved context blocks. |
| `core/pipeline_config.py` | Central runtime flow-control settings loaded from env and CLI (`PipelineConfig`, `get_config`). |
| `core/semgrep_runner.py` | Reusable offline Semgrep-core runner for local static-policy rule sets. |
| `core/server_manager.py` | Optional local server startup/shutdown with thinking-aware command construction and slot-path preflight. |
| `core/vector_cache.py` | Source-scoped vector-cache naming, loading, saving, atomic writes, and deletion. |
| `core/vector_index.py` | Generic file-index lifecycle with MD5 reuse, optional embeddings, and metadata callbacks. |
| `kotlin/coverage_analysis.py` | Kover XML discovery, missed-line/branch parsing, gap context, and delta comparison. |
| `kotlin/generator.py` | Top-level Kotlin/Android generation orchestration, CLI parsing, source loop, and cleanup. |
| `kotlin/gradle_analysis/` | Gradle/Kotlin/JUnit error grouping, causal repair planning, and fingerprints (`errors.py`, `junit.py`). |
| `kotlin/incremental_coverage.py` | Existing-test incremental Kover generation, merge, rollback, and coverage-proof flow. |
| `kotlin/kotlin_analysis.py` | Kotlin AST parsing, imports, source classification, signatures, and source-risk analysis. |
| `kotlin/mcp_gradle_server.py` | MCP server exposing project-safe file, Gradle, search, and Kotlin declaration tools. |
| `kotlin/mcp_tool_adapter.py` | Kotlin adapter over generic MCP tools for Gradle heartbeat and Kotlin declaration lookup. |
| `kotlin/memory_context.py` | Kotlin/Android tag inference plus stateless classification-based lesson retrieval for prompts. |
| `kotlin/project_context.py` | Android module discovery, Gradle dependencies, Hilt support, vector indexing, and nearby context. |
| `kotlin/prompting/` | Kotlin prompt-rule selection, generation/repair streaming, and model-call entrypoints (`generation.py`, `repair.py`, `rules.py`). |
| `kotlin/prompts.py` | Authoritative Kotlin/Android system prompts, user-section labels, rules, and blueprint text. |
| `kotlin/repair_flow.py` | Owned Gradle/JUnit failure filtering, bounded repair orchestration, and final memory save. |
| `kotlin/static_analysis.py` | Strict Pydantic report combining Kotlin Tree-sitter structure, Semgrep findings, and project verification. |
| `kotlin/test_code_utils/` | Kotlin test extraction (`extract.py`), validation (`validate.py`), and merge (`merge.py`) helpers. |
| `kotlin/tests/` | Compact production-flow smoke tests plus focused config/guardrail/incremental-selection checks. |
| `helper/dashboard/blocked_coverage_kover_report.py` | Path-based Kover XML discovery, gap dashboards, coverage-summary orchestration, and HTML report output. |
| `helper/dashboard/kover_coverage_summary.py` | Interactive Kover coverage-summary HTML from discovered module XML reports. |
| `helper/dashboard/kover_gap_dashboard.py` | Renders Kover gap dashboard HTML. |
| `helper/chatbot/codebase_chatbot.py` | RAG REPL over UnitTest_gen docs and Python source with optional auto-started chat server. |

## Core Module Functions and Important Globals

### `core/logging_utils.py`

| Name | Type | One-line summary |
|---|---|---|
| `ACTIVE_LOGGER` | global | Current per-source logger used by `log_message`, `log_section`, and `log_block`. |
| `set_active_logger` | function | Installs the active logger for the current source-file run. |
| `log_message` | function | Writes one message to console and active log. |
| `log_section` | function | Writes a formatted section heading. |
| `log_block` | function | Writes a titled multi-line block. |
| `get_pipeline_log_dir` | function | Resolves `UnitTest_gen/log`. |
| `archive_generated_side_files` | function | Archives Gradle/error/context side files for a generated test. |

### `core/mcp_tools.py`

| Name | Type | One-line summary |
|---|---|---|
| `LocalMcpTools` | class | Generic async MCP stdio client with `call`, file helpers, patching, and project search. |

### `core/memory_store.py`

| Name | Type | One-line summary |
|---|---|---|
| `AI_MEMORY_FILE` | global | Active JSON memory file, overridable by `TESTGEN_AI_MEMORY_FILE`. |
| `REPO_SPECIFIC_TEXT_REPLACEMENTS` | global | Sanitization rules for removing project-specific text from saved lessons. |
| `sanitize_memory_text` | function | Redacts project-specific package/path/class details from saved memory. |
| `retrieve_generation_lessons` | function | Loads memory JSON and returns bounded generation/incremental lessons selected by classification tags. |
| `add_generation_lesson` | function | Sanitizes and saves a verified generation lesson. |
| `retrieve_repair_lessons` | function | Loads memory JSON and returns bounded repair lessons selected by source and repair-category tags. |
| `add_repair_lesson` | function | Sanitizes and saves a verified repair lesson. |

### `core/model_runtime.py`

| Name | Type | One-line summary |
|---|---|---|
| `CHAT_BASE_URL`, `EMBEDDING_BASE_URL` | global | OpenAI-compatible chat and embedding endpoints. |
| `PRINT_PROMPTS_IN_TERMINAL` | global | Global prompt-print switch for generation and repair prompts. |
| `MODEL_STUCK_DETECTOR_ENABLED` | global | Global stream repetition/stuck detector switch. |
| `STREAM_RETRY_INSTRUCTION_BUILDER` | global | Optional domain adapter for retry instructions. |
| `configure_vector_cache_scope` | function | Selects the active source-scoped vector-cache file. |
| `get_vector_cache_file` / `get_vector_cache` | function | Returns the active vector-cache path/object. |
| `load_cache` / `save_cache` | function | Loads/saves the active vector cache. |
| `get_vector` | function | Requests an embedding vector when embeddings are enabled. |
| `is_model_server_available` / `is_embedding_server_available` | function | Checks local model server reachability. |
| `print_prompt_to_terminal` | function | Prints prompts when `PRINT_PROMPTS_IN_TERMINAL` is enabled. |
| `detect_stream_stuck` | function | Detects repeated or non-final model streams. |
| `stream_chat_completion` | function | Main retrying chat-completion streaming API. |
| `thinking_enabled` / `final_output_instruction` | function | Controls thinking fields and final-output wording from `TESTGEN_ENABLE_THINKING`. |

### `core/prompt_assembly.py`

| Name | Type | One-line summary |
|---|---|---|
| `RuleDocument` | dataclass | One retrievable prompt rule with tags, phases, and priority. |
| `RetrievedRuleContext` | dataclass | Ranked rule documents plus formatted text. |
| `PromptSection` | dataclass | One titled prompt section with an optional character budget. |
| `PromptBuildSpec` / `PromptMessageBundle` | dataclass | Generic prompt input and OpenAI message output shapes. |
| `build_rule_corpus` | function | Parses supplied rule constants into retrievable documents. |
| `retrieve_rule_context` | function | Retrieves eligible rules by tags, phase, terms, and budget. |
| `assemble_prompt_sections` | function | Removes empty/duplicate sections and assembles ordered prompt text. |
| `format_template` | function | Formats named prompt templates. |
| `build_prompt_bundle` | function | Builds system/user prompt plus message tuple. |

### `core/rag_context.py`

| Name | Type | One-line summary |
|---|---|---|
| `RetrievalQuery`, `ContextItem`, `RankedContextItem` | dataclass | Generic RAG query, candidate, and ranked result records. |
| `normalize_terms` / `tokenize` | function | Normalizes tags and lexical terms. |
| `rank_context_items` | function | Deterministically ranks context by tag overlap, term overlap, priority, and callbacks. |
| `format_ranked_context` | function | Formats ranked items under an optional heading. |
| `format_context_blocks` | function | Formats arbitrary blocks with an optional character cap. |

### `core/server_manager.py`

| Name | Type | One-line summary |
|---|---|---|
| `DEFAULT_*` | globals | Default llama.cpp paths, model paths, commands, logs, and slot location. |
| `build_default_coding_server_command` | function | Builds the default llama-server command and adds reasoning flags only when thinking is enabled. |
| `env_flag` | function | Reads boolean environment variables. |
| `normalize_server_command` | function | Removes dangling shell-continuation backslashes from default or custom commands. |
| `ensure_slot_save_path` | function | Creates a configured `--slot-save-path` directory before server startup. |
| `resolve_cwd` | function | Resolves command working directories. |
| `LocalServerManager` | class | Starts/stops optional local coding and embedding servers. |

### `core/pipeline_config.py`

| Name | Type | One-line summary |
|---|---|---|
| `PipelineConfig` | dataclass | Central flow-control settings (model, servers, repair, incremental, feature gates). |
| `load_config_from_env` | function | Builds config from environment defaults. |
| `apply_cli_args` | function | Applies CLI flag overrides to config. |
| `get_config` / `set_active_config` | function | Active singleton config for runtime consumers. |

### `core/semgrep_runner.py`

| Name | Type | One-line summary |
|---|---|---|
| `SemgrepUnavailableError` | exception | Stops generation when mandatory Semgrep-core analysis cannot run. |
| `require_semgrep_executable` | function | Resolves the installed Semgrep-core engine. |
| `scan_text_with_semgrep` | function | Runs one or more local rule files and returns normalized JSON findings. |

### `core/vector_cache.py`

| Name | Type | One-line summary |
|---|---|---|
| `ScopedVectorCache` | dataclass | Source-scoped vector-cache path plus load/save/delete operations. |

### `core/vector_index.py`

| Name | Type | One-line summary |
|---|---|---|
| `file_md5` | function | Calculates a file checksum for cache reuse. |
| `build_vector_index` | function | Builds a generic indexed file map with optional vector reuse/generation. |

## Kotlin Module Functions and Important Globals

### `kotlin/coverage_analysis.py`

| Name | Type | One-line summary |
|---|---|---|
| `find_latest_kover_xml` / `find_latest_kover_xml_for_context` | function | Finds the newest relevant Kover XML report. |
| `parse_kover_gap` / `parse_latest_coverage_gap` | function | Parses missed lines/branches for a source file. |
| `coverage_gap_context` | function | Builds prompt-safe Kover gap context. |
| `coverage_gap_fingerprint`, `coverage_gap_delta_summary`, `coverage_gap_improved` | function | Compare before/after Kover gaps. |
| `kover_report_module_dirs` | function | Finds module report directories for configured Gradle tasks. |
| `compact_ranges` | function | Formats line numbers into compact ranges. |
| `private_declaration_context` | function | Uses Tree-sitter/static analysis ranges to classify private implementation coverage gaps. |

### `kotlin/generator.py`

| Name | Type | One-line summary |
|---|---|---|
| `ROOT` | global | Repository root inserted into `sys.path` for script execution. |
| `format_source_elapsed_minutes` | function | Formats per-source runtime in minutes. |
| `generate_android_tests` | async function | Main per-target pipeline: index, classify, prompt, generate, verify, repair, cleanup. |
| `async_main` | async function | CLI parser, server setup, MCP lifecycle, signal handling, and exit result. |

### `kotlin/gradle_analysis.py`

| Name | Type | One-line summary |
|---|---|---|
| `extract_first_gradle_error` | function | Extracts a focused Gradle failure block. |
| `group_gradle_errors` | function | Groups Kotlin compiler, Gradle, JUnit, KSP, and Hilt evidence. |
| `summarize_all_error_groups` | function | Produces the human-readable error summary in logs. |
| `is_compile_repair_group` / `should_try_batch_compile_repair` | function | Decide whether small compile errors can be repaired together. |
| `group_junit_failures_by_report` helpers | function | Normalize and group XML-backed JUnit/runtime failures. |
| `is_gradle_success`, `error_group_fingerprint`, `text_fingerprint` | function | Success detection and loop/fingerprint helpers. |

### `kotlin/incremental_coverage.py`

| Name | Type | One-line summary |
|---|---|---|
| `filter_source_risk_context_for_gap` | function | Keeps source-risk hints relevant to the current Kover gap. |
| `build_coverage_opportunity_plan` | function | Classifies Kover gaps into safe, attemptable, blocked, and excluded opportunities. |
| `_select_largest_fixture_opportunities` | function | Picks the highest-weight fixture group and opportunities under cap/line budget. |
| `build_incremental_coverage_strategy` | function | Builds deterministic coverage targeting instructions. |
| `run_gradle_and_parse_gap` | async function | Runs Gradle/Kover and parses the latest gap. |
| `run_incremental_coverage_generation` | async function | Generates, merges, repairs, verifies, and rolls back incremental coverage candidates. |

### `kotlin/kotlin_analysis.py`

| Name | Type | One-line summary |
|---|---|---|
| `AST_PARSER` | global | Tree-sitter Kotlin parser used for source inspection. |
| `parse_kotlin_ast`, `walk_ast`, `ast_text` | function | Parse and traverse Kotlin syntax trees. |
| `kotlin_package_name`, `kotlin_imports`, `kotlin_identifier_set` | function | Extract package/import/symbol facts. |
| `kotlin_top_level_class_name`, `kotlin_declared_class_names` | function | Extract class names for indexing and output paths. |
| `classify_source`, `classify_repair`, `source_rule_categories` | function | Produce source/repair categories for prompts and retrieval. |
| `classify_fragment_source` | function | Detect Fragment/Hilt/navigation/ViewModel/CarUi complexity. |
| `analyze_source_bug_risks` | function | Finds source risk hints used in prompts and repairs. |
| `summarize_kotlin_source_signatures` | function | Summarizes Kotlin signatures for dependency context. |

### `kotlin/static_analysis.py`

| Name | Type | One-line summary |
|---|---|---|
| `KotlinStaticAnalysisReport` | Pydantic model | Strict versioned report of functions, calls, coroutine usage, frameworks, callbacks, and findings. |
| `analyze_kotlin_code` | function | Builds and caches the combined Tree-sitter/Semgrep report. |
| `verify_project_exception_findings` | function | Confirms or dismisses speculative SDK constructor findings using project declarations/usages. |
| `persist_static_analysis_report` | function | Writes `<Source>.static-analysis.json` into the generator log directory. |
| `require_semgrep_preflight` | function | Validates the mandatory Semgrep-core engine and local policies. |

### `kotlin/mcp_gradle_server.py`

| Name | Type | One-line summary |
|---|---|---|
| `run_gradle` | MCP tool | Runs Gradle tasks under the allowed project root. |
| `read_file`, `read_file_range`, `write_file`, `delete_file`, `patch_file` | MCP tool | Safe file operations exposed to the generator. |
| `search_in_project` | MCP tool | Text search constrained to the allowed root. |
| `find_kotlin_declaration`, `read_kotlin_file_around_symbol` | MCP tool | Kotlin declaration lookup for repair context. |

### `kotlin/mcp_tool_adapter.py`

| Name | Type | One-line summary |
|---|---|---|
| `run_gradle_with_heartbeat` | async function | Calls the Gradle MCP tool and keeps progress visible. |
| `find_kotlin_declaration` / `read_kotlin_file_around_symbol` | async function | Kotlin-specific wrappers over generic MCP calls. |

### `kotlin/memory_context.py`

| Name | Type | One-line summary |
|---|---|---|
| `MEMORY_TAG_MARKERS` | global | Kotlin/Android tag inference markers for legacy/new memory entries. |
| `infer_kotlin_memory_tags` | function | Infers memory tags from keys and lesson text. |
| `retrieve_generation_lessons` / `retrieve_repair_lessons` | function | Kotlin-filtered stateless memory retrieval for prompt lesson blocks. |
| `add_repair_lesson` | function | Saves a Kotlin-tagged repair lesson through core memory storage. |

### `kotlin/project_context.py`

| Name | Type | One-line summary |
|---|---|---|
| `find_owning_module_dir`, `module_path_for_dir`, `find_project_root_for_path` | function | Resolve Android/Gradle ownership. |
| `parse_module_min_sdk`, `parse_module_compile_sdk`, `module_sdk_test_guidance` | function | Inspect SDK bounds for Robolectric prompts/validation. |
| `ensure_hilt_test_activity_support`, `module_has_hilt_robolectric_fragment_support` | function | Manage/verify Hilt Fragment local-test support. |
| `check_and_add_dependencies`, `should_auto_add_missing_test_dependencies` | function | Add or detect missing test dependencies when safe. |
| `build_project_index`, `ensure_project_index_cache`, `delete_vector_cache_file` | function | Kotlin adapter over core vector indexing/cache lifecycle. |
| `find_semantic_and_structural_dependencies` | function | Retrieves up to three source-relevant dependency examples. |
| `collect_nearby_test_pattern_context` | function | Summarizes nearby test patterns without full raw files. |
| `derive_test_output_path`, `derive_coverage_supplement_path` | function | Compute generated test paths. |
| `resolve_path_against_root`, `is_path_inside` | function | Safe project-relative path helpers. |

### `kotlin/prompting/`

| Name | Type | One-line summary |
|---|---|---|
| `PROMPT_RULE_CORPUS` | global | Parsed prompt-rule corpus built from `prompts.py`. |
| `retrieve_kotlin_rule_context` | function | Retrieves source/phase-eligible Kotlin prompt rules. |
| `build_generation_rule_tail` | function | Builds final generation rules from retrieved rule context. |
| `build_source_specific_test_strategy` | function | Adds deterministic source-shape strategy guidance. |
| `generate_test_code_streaming` | function | Builds initial prompt and streams generated Kotlin test code. |
| `generate_coverage_supplement_test_streaming` | function | Builds incremental coverage prompt and streams supplement code. |
| `repair_focused_error_patch_streaming` | function | Builds patch-repair prompt and parses JSON patches. |
| `repair_focused_error_block_streaming` | function | Builds full-file repair prompt and returns full Kotlin code. |
| `format_error_location_context` / `build_generated_test_static_diagnostics` | function | Adds exact generated-test evidence for repair prompts. |

### `kotlin/prompts.py`

| Name | Type | One-line summary |
|---|---|---|
| `GENERATION_SYSTEM_PROMPT`, `INCREMENTAL_SYSTEM_PROMPT` | global | System prompts for normal and incremental generation. |
| `PATCH_REPAIR_SYSTEM_PROMPT`, `FULL_FILE_REPAIR_SYSTEM_PROMPT` | global | System prompts for repair calls. |
| `*_USER_SECTION_TITLES` | global | Canonical user-prompt section labels. |
| `ANDROID_BLUEPRINTS`, `FRAMEWORK_TESTING_BLUEPRINTS`, `APOLLO_MAPPER_BLUEPRINTS` | global | Domain-specific retrieved prompt sections. |
| `CORE_GENERATION_RULES`, `TEST_QUALITY_RULES`, `KOTLIN_ANDROID_TEST_RULES`, `REPAIR_RULES` | global | Authoritative prompt rules. |
| `REQUIRED_DEPENDENCIES`, `SAFE_KOVER_EXCLUDE_CLASSES`, `UNSAFE_KOVER_EXCLUDE_CLASSES` | global | Dependency and coverage-support constants. |

### `kotlin/repair_flow.py`

| Name | Type | One-line summary |
|---|---|---|
| `MAX_REPAIR_ROUNDS` | global | Upper bound for repair attempts per generated test. |
| `MAX_UNCHANGED_ERROR_REPAIR_ATTEMPTS` | global | Loop guard for unchanged repair locations. |
| `collect_junit_failure_report_context` | async function | Reads relevant XML/JUnit failure context. |
| `collect_verified_repair_context` | async function | Builds exact line, symbol, declaration, and search context for repair. |
| `verify_and_repair_test_with_mcp` | async function | Runs Gradle, groups failures, applies repair, reruns, and saves lessons on success. |
| `split_error_groups_by_generated_test` | function | Separates repair-owned failures from external module failures. |
| `groups_include_owned_mockito_misuse` | function | Marks downstream failures as possible contamination when owned Mockito misuse exists. |

### `kotlin/test_code_utils.py`

| Name | Type | One-line summary |
|---|---|---|
| `AST_PARSER`, `CLASS_MEMBER_DECLARATION_RE` | global | Kotlin parser plus bounded regex support for generated-snippet processing. |
| `extract_kotlin_code`, `normalize_kotlin_test_code` | function | Extracts and normalizes generated Kotlin test code. |
| `validate_generated_test_code` | function | Main deterministic pre-Gradle guardrail validator. |
| `has_invalid_void_navigation_stubbing` | function | Rejects `Mockito.when(navController.navigate(...))` and Java matcher misuse for Unit navigation. |
| `has_invalid_java_nav_deeplink_captor` | function | Rejects Java `ArgumentCaptor.forClass(...).capture()` against Kotlin non-null navigation APIs. |
| `has_mocked_static_when_missing_return_type` | function | Detects `MockedStatic.when { ... }` calls missing an explicit return type. |
| `has_invalid_singleton_reflection` | function | Rejects brittle singleton/private-field reflection in generated tests. |
| `collect_generation_quality_issues` | function | Non-Gradle quality checks for generated tests. |
| `extract_json_patches`, `normalize_json_patches` | function | Parse model patch responses. |
| `merge_supplemental_test_code`, `merge_test_code_with_member_blocks` | function | Merge incremental supplement members into existing tests. |
| `build_supplemental_merge_report`, `log_supplemental_merge_report` | function | Report merge decisions. |
| `remember_successful_generation_if_high_quality` | function | Saves generation memory only for verified high-quality tests. |

## Helper Modules

### `helper/dashboard/blocked_coverage_kover_report.py`

| Name | Type | One-line summary |
|---|---|---|
| `discover_kover_xml_groups` | function | Scans `**/build/reports/kover/**/*.xml` and groups paths by filename variant suffix (`report.xml` → `default`). |
| `prepare_html_report_dir` | function | Clears and recreates the HTML output directory before writing reports. |
| `write_all_kover_html_reports` | function | Builds coverage summaries, gap dashboards, and the reports hub in one CLI-oriented pass. |
| `write_kover_gap_reports` | function | Rebuilds Kover gap dashboards for all discovered variant groups. |
| `build_kover_gap_report` | function | Builds the dashboard data model from Kover XML gaps and planner classifications. |

### `helper/dashboard/kover_coverage_summary.py`

| Name | Type | One-line summary |
|---|---|---|
| `parse_kover_summary_xml` | function | Parses one module Kover XML into package/file coverage counters. |
| `build_variant_coverage_summary` | function | Aggregates discovered module XMLs into one variant coverage model. |
| `write_coverage_summary_reports` | function | Writes interactive coverage-summary HTML for discovered variant groups. |
| `render_kover_reports_hub` | function | Renders the top-level hub linking coverage and gap dashboards. |

### `helper/dashboard/kover_gap_dashboard.py`

| Name | Type | One-line summary |
|---|---|---|
| `render_kover_gap_dashboard` | function | Renders one interactive Kover gap dashboard. |
| `render_kover_gap_index` | function | Renders the dashboard index page. |

### `helper/chatbot/codebase_chatbot.py`

| Name | Type | One-line summary |
|---|---|---|
| `build_chat_index` | function | Indexes `UnitTest_gen` docs and Python source into a scoped vector/keyword cache. |
| `chat_once` | function | Retrieves top-k context and asks the local chat model one question. |
| `create_chatbot_server_manager` | function | Builds a `LocalServerManager` for optional auto-start on port 8082. |
| `main` | function | REPL entrypoint with `--reindex`, `--top-k`, `--no-manage-server`, and `--no-server-terminal`. |

## Focused Test Modules

| Module | One-line responsibility |
|---|---|
| `kotlin/tests/test_incremental_opportunity_selection.py` | Largest-first safe/attemptable fixture selection smoke tests. |
| `kotlin/tests/test_pipeline_config.py` | Incremental cap/budget and pipeline config env/CLI checks. |
| `helper/dashboard/test_kover_xml_discovery.py` | Path-based Kover XML grouping and HTML output-dir cleanup tests. |
| `helper/dashboard/test_kover_coverage_summary.py` | Kover coverage-summary parsing smoke tests. |
| `helper/chatbot/test_codebase_chatbot.py` | Chatbot indexing, retrieval, and auto-server smoke tests. |
