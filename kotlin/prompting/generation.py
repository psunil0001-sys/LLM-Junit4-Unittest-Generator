# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Kotlin full-generation and incremental supplement prompt streaming.
"""Kotlin test generation prompt streaming."""

import re

from UnitTest_gen.core.logging_utils import log_block, log_message, log_section
from UnitTest_gen.core.model_runtime import (
    final_output_instruction,
    print_prompt_to_terminal,
    stream_chat_completion,
)
from UnitTest_gen.core.prompt_assembly import PromptBuildSpec, PromptSection, build_prompt_bundle
from UnitTest_gen.kotlin import prompt_constants
from UnitTest_gen.kotlin.kotlin_analysis import (
    SourceProfile,
    classify_source,
    source_declares_android_fragment,
    source_rule_categories,
)
from UnitTest_gen.kotlin.project_context import (
    collect_nearby_test_pattern_context,
    module_sdk_test_guidance,
    retrieve_classified_context,
)
from UnitTest_gen.kotlin.prompting.rules import (
    _fixture_ids_from_classification,
    _meaningful_context,
    _prompt_title,
    build_generation_rule_tail,
    build_source_strategy_context,
)
from UnitTest_gen.kotlin.test_code_utils.extract import extract_kotlin_code
from UnitTest_gen.kotlin.prompt_slices import slice_for_incremental
from UnitTest_gen.core.pipeline_config import get_config


def _kotlin_stream_retry_instruction(reason: str) -> str:
    return (
        "The previous stream was stopped because it appeared stuck: "
        f"{reason}. Return the requested final output now. "
        f"Return the final artifact only. {final_output_instruction()} "
        "If Kotlin code was requested, return one complete Kotlin file only. "
        "If JSON was requested, return only valid JSON matching the requested schema."
    )


def _source_requires_module_sdk_guidance(source_code: str, categories: set[str]) -> bool:
    return bool(
        categories
        & {
            "android_fragment",
            "android_ui",
            "android_navigation",
            "android_dialog",
            "android_context",
            "car",
            "android_log",
        }
        or any(token in (source_code or "") for token in ("R.", "Context", "ApplicationProvider", "Robolectric"))
    )


def _stream_kotlin_test(prompt_bundle, **kwargs):
    return stream_chat_completion(messages=list(prompt_bundle.messages), **kwargs)


def _category_overlap(source_categories, candidate_categories) -> bool:
    return bool(set(source_categories or ()) & set(candidate_categories or ()))


def filter_source_risk_context_for_generation(source_risk_context: str, source_code: str, source_categories=None) -> str:
    if not source_risk_context:
        return ""
    profile = classify_source(source_code)
    categories = set(source_categories or profile.categories)
    symbols = {symbol.lower() for symbol in profile.symbols}
    kept_blocks = []
    current_block = []
    for line in source_risk_context.splitlines():
        if re.match(r"^\d+\.\s+Risk:", line):
            if current_block:
                blob = "\n".join(current_block).lower()
                if any(symbol in blob for symbol in symbols) or any(cat.replace("_", " ") in blob for cat in categories):
                    kept_blocks.append("\n".join(current_block))
            current_block = [line]
            continue
        if current_block:
            current_block.append(line)
    if current_block:
        blob = "\n".join(current_block).lower()
        if any(symbol in blob for symbol in symbols) or any(cat.replace("_", " ") in blob for cat in categories):
            kept_blocks.append("\n".join(current_block))
    if kept_blocks:
        return "\n".join(kept_blocks)
    return source_risk_context


def generate_test_code_streaming(
    class_name,
    source_code,
    dependency_context="",
    source_risk_context="",
    output_file_path=None,
    source_categories=None,
    memory_context="",
    *,
    source_profile: SourceProfile | None = None,
    project_index=None,
    project_root: str | None = None,
    source_file_path: str | None = None,
):
    source_profile = source_profile or classify_source(source_code)
    source_categories = set(source_categories or source_profile.categories)
    if not dependency_context and project_index is not None and project_root and source_file_path:
        dependency_context = retrieve_classified_context(
            source_profile,
            source_file_path,
            source_code,
            project_index,
            project_root,
            phase="generation",
        )
    filtered_risks = filter_source_risk_context_for_generation(source_risk_context, source_code, source_categories)

    is_fragment = source_declares_android_fragment(source_code)
    is_hilt = "@AndroidEntryPoint" in source_code or "@Inject" in source_code
    is_viewmodel = "ViewModel" in source_code or ": ViewModel()" in source_code
    is_apollo_mapper = (
        "Mutation." in source_code
        or "Query." in source_code
        or "com.apollographql.apollo3.api.Optional" in source_code
    )
    uses_framework_blueprints = source_declares_android_fragment(source_code) or any(
        marker in source_code
        for marker in [
            "kotlinx.coroutines",
            "Flow<",
            "LiveData",
            "ViewModel",
            "SavedStateHandle",
            "@Module",
            "@Binds",
            "@Provides",
            "dagger.",
            "Room",
            "RoomDatabase",
            "@Dao",
            "@Entity",
            "retrofit2",
            "okhttp3",
            "Authenticator",
            "Interceptor",
            "ApolloClient",
            "com.apollographql",
            "NavController",
            "findNavController",
            "NavigationView",
            "DrawerLayout",
            "Material",
            "com.android.car.ui",
            "android.car",
            "WorkManager",
            "Worker",
            "BroadcastReceiver",
            "Service",
            "SharedPreferences",
            "PreferenceManager",
            "JSONObject",
            "JSONArray",
            "AuthState",
            "Firebase",
            "Timber",
            "Log.",
        ]
    )

    dynamic_system_rules = prompt_constants.GENERATION_SYSTEM_PROMPT

    source_specific_strategy = build_source_strategy_context(class_name, source_code, output_file_path)
    sdk_guidance = module_sdk_test_guidance(output_file_path) if _source_requires_module_sdk_guidance(source_code, source_categories) else ""
    nearby_test_patterns = collect_nearby_test_pattern_context(
        output_file_path=output_file_path,
        current_test_code=source_code,
        class_name=class_name,
        source_categories=source_categories,
        require_category_overlap=True,
    ) if source_categories else ""

    ticks = chr(96) * 3
    final_rule_tail = build_generation_rule_tail(
        is_fragment=is_fragment,
        is_hilt=is_hilt,
        is_viewmodel=is_viewmodel,
        uses_framework_blueprints=uses_framework_blueprints,
        is_apollo_mapper=is_apollo_mapper,
        source_code=source_code,
        dependency_context=dependency_context,
        source_categories=source_categories,
        fixture_ids=_fixture_ids_from_classification(source_categories, source_code),
    )
    prompt_bundle = build_prompt_bundle(
        PromptBuildSpec(
            dynamic_system_rules,
            [
            PromptSection(_prompt_title(prompt_constants.GENERATION_USER_SECTION_TITLES, "target_source"), f"{ticks}kotlin\n{source_code}\n{ticks}"),
            PromptSection(_prompt_title(prompt_constants.GENERATION_USER_SECTION_TITLES, "dependency_context"), dependency_context),
            PromptSection(_prompt_title(prompt_constants.GENERATION_USER_SECTION_TITLES, "source_strategy"), source_specific_strategy),
            PromptSection(_prompt_title(prompt_constants.GENERATION_USER_SECTION_TITLES, "sdk_guidance"), sdk_guidance, max_chars=900),
            PromptSection(_prompt_title(prompt_constants.GENERATION_USER_SECTION_TITLES, "nearby_patterns"), _meaningful_context(nearby_test_patterns)),
            PromptSection(_prompt_title(prompt_constants.GENERATION_USER_SECTION_TITLES, "source_risks"), _meaningful_context(filtered_risks)),
            PromptSection("", memory_context),
            PromptSection("", final_rule_tail),
            ],
        )
    )
    user_content = prompt_bundle.user_prompt

    log_section(f"GENERATION REQUEST FOR {class_name}", category="context")
    log_block("GENERATION SYSTEM PROMPT", prompt_bundle.system_prompt, category="context", console=False)
    log_block("GENERATION USER CONTEXT AND SOURCE", user_content, category="context", console=False)
    print_prompt_to_terminal(
        title=f"FINAL GENERATION PROMPT SENT TO MODEL: {class_name}",
        system_prompt=prompt_bundle.system_prompt,
        user_prompt=user_content,
    )
    log_message(f"📥 Streaming generated test for {class_name}:\n", category="info")

    raw_output = _stream_kotlin_test(
        prompt_bundle,
        temperature=0.6,
    

    )

    return extract_kotlin_code(raw_output)

def generate_coverage_supplement_test_streaming(
    class_name,
    supplemental_test_class_name,
    source_code,
    existing_test_code,
    dependency_context,
    coverage_context,
    incremental_strategy,
    source_risk_context="",
    output_file_path=None,
    standalone_mode=False,
    source_categories=None,
    memory_context="",
    *,
    fixture_ids=None,
    opportunity_plan=None,
    selected_entry_points=None,
    **kwargs,
):
    source_categories = set(source_categories or source_rule_categories(source_code))
    is_apollo_mapper = (
        "Mutation." in source_code
        or "Query." in source_code
        or "com.apollographql.apollo3.api.Optional" in source_code
    )
    uses_framework_blueprints = source_declares_android_fragment(source_code) or any(
        marker in source_code
        for marker in [
            "kotlinx.coroutines",
            "Flow<",
            "LiveData",
            "ViewModel",
            "SavedStateHandle",
            "@Module",
            "@Binds",
            "@Provides",
            "dagger.",
            "Room",
            "retrofit2",
            "okhttp3",
            "ApolloClient",
            "com.apollographql",
            "Context",
            "Log.",
        ]
    )

    dynamic_system_rules = prompt_constants.INCREMENTAL_SYSTEM_PROMPT

    ticks = chr(96) * 3
    resolved_fixture_ids = list(fixture_ids or [])
    if opportunity_plan is not None:
        from UnitTest_gen.kotlin.prompting.rules import _fixture_ids_from_opportunity_plan
        resolved_fixture_ids = _fixture_ids_from_opportunity_plan(opportunity_plan) or resolved_fixture_ids
    selected_blocked = bool((opportunity_plan or {}).get("selected_blocked"))
    if not resolved_fixture_ids:
        resolved_fixture_ids = _fixture_ids_from_classification(source_categories, source_code)
    final_rule_tail = build_generation_rule_tail(
        is_fragment=source_declares_android_fragment(source_code),
        is_hilt=("@AndroidEntryPoint" in source_code or "@Inject" in source_code),
        is_viewmodel=("ViewModel" in source_code or ": ViewModel()" in source_code),
        uses_framework_blueprints=uses_framework_blueprints,
        is_apollo_mapper=is_apollo_mapper,
        source_code=source_code,
        source_categories=source_categories,
        phase="incremental",
        fixture_ids=resolved_fixture_ids,
        playbooks_in_prompt=bool(resolved_fixture_ids),
        direct_orchestration_in_prompt=bool(resolved_fixture_ids),
    )
    source_specific_strategy = build_source_strategy_context(class_name, source_code, output_file_path)
    sdk_guidance = module_sdk_test_guidance(output_file_path) if _source_requires_module_sdk_guidance(source_code, source_categories) else ""
    nearby_test_patterns = collect_nearby_test_pattern_context(
        output_file_path=output_file_path,
        current_test_code=existing_test_code,
        class_name=class_name,
        source_categories=source_categories,
        require_category_overlap=True,
    )

    final_file_rule = (
        "- Keep the temporary file self-contained and compile-safe; it will remain as the standalone test file after Gradle/Kover passes.\n"
        if standalone_mode
        else (
            "- Treat this file as an in-memory merge candidate for the existing test file, not as a standalone coverage result.\n"
            "- Keep the merge candidate compile-safe, but design every new test/helper function to be merge-compatible with the existing test class.\n"
            "- Structure-aware merge preserves unique tests, helpers, fields, imports, and required @Before/@After statements while consolidating equivalent existing members.\n"
            "- Keep supplemental member names unique unless they intentionally extend an equivalent existing lifecycle block.\n"
            "- Kover improvement is measured only after the candidate has been merged into the existing test file.\n"
        )
    )
    incremental_rules = (
        "\n--- FINAL INCREMENTAL COVERAGE RULES ---\n"
        f"- Output exactly one complete Kotlin file whose test class name is `{supplemental_test_class_name}`.\n"
        "- Generate tests only for the selected targets from `COVERAGE OPPORTUNITY PLAN`; treat alternatives as context for later attempts.\n"
        "- A valid incremental test must execute at least one selected missed line, missed branch, or missed method; calling a public method is not enough.\n"
        "- A valid incremental test must reach selected lines through the target class public entry path; calling delegated singleton helpers directly from the test does not cover lines inside the target method.\n"
        "- Cover all selected safe opportunities that share the same fixture and fit the verified context.\n"
        "- If selected controlled-risk opportunities are listed, attempt them only when the plan provides execution proof for the missed lines.\n"
        "- Follow the deterministic coverage strategy exactly; it is the primary test-design plan.\n"
        "- Private methods are coverage consequences, not direct test targets.\n"
        "- Do not call, reflect on, spy on, or design a test around a private method.\n"
        "- Include a private-method gap only when the selected public API reaches it through deterministic verified setup.\n"
        "- Use the existing test file as already-covered context and create new, non-duplicate tests.\n"
        "- Choose selected opportunities that the deterministic strategy marks as reachable through stable public or verified fixture setup.\n"
        "- Generate as many meaningful, compile-safe supplemental tests as the verified context supports for the selected opportunity plan, using compact scenario/table-style tests with precise assertions.\n"
        + (
            "- The CLI explicitly selected blocked opportunities. Attempt only their documented public path; do not use reflection, private calls, invented seams, or production edits.\n"
            if selected_blocked
            else "- Do not generate no-op tests for blocked opportunities; ignore them even when memory or generic rules mention similar patterns.\n"
        )
        + "- Do not generate immediate pre-launch state assertions for Kover lines inside uncontrolled Dispatchers.IO, real delay, static/platform, or checked-exception paths.\n"
        "- For catch/fallback gaps, use only verified throwable shapes and deterministic execution paths; otherwise skip the cluster.\n"
        "- For branch-only gaps, write focused branch probes that complement already-covered success paths.\n"
        "- For Fragment lifecycle gaps, after activityController.stop() or onStop() assert Lifecycle.State.CREATED (or lower), not STARTED or RESUMED.\n"
        "- Keep Kover-verifiable candidates that reduce the target gap.\n"
        "- Include any helper methods needed by the supplemental class, using unique helper names when possible.\n"
        + final_file_rule
        + "- Return only Kotlin code inside ```kotlin markdown.\n"
        "------------------------------------------------\n"
    )

    prompt_source = source_code
    prompt_test = existing_test_code
    if get_config().prompt_slices_enabled:
        slices = slice_for_incremental(
            source_code,
            existing_test_code,
            source_path=output_file_path or "",
            opportunity_plan=opportunity_plan or {},
            selected_entry_points=selected_entry_points,
        )
        prompt_source = slices.source_slice
        prompt_test = slices.test_slice
        if slices.omitted_summary:
            prompt_test = (
                f"// Scoped for selected gaps; full file is used for merge.\n"
                f"// {slices.omitted_summary}\n\n"
                f"{prompt_test}"
            )

    prompt_bundle = build_prompt_bundle(
        PromptBuildSpec(
            dynamic_system_rules,
            [
            PromptSection(_prompt_title(prompt_constants.INCREMENTAL_USER_SECTION_TITLES, "target_source"), f"{ticks}kotlin\n{prompt_source}\n{ticks}"),
            PromptSection(_prompt_title(prompt_constants.INCREMENTAL_USER_SECTION_TITLES, "existing_test"), f"{ticks}kotlin\n{prompt_test}\n{ticks}"),
            PromptSection(_prompt_title(prompt_constants.INCREMENTAL_USER_SECTION_TITLES, "coverage_context"), coverage_context),
            PromptSection(_prompt_title(prompt_constants.INCREMENTAL_USER_SECTION_TITLES, "coverage_strategy"), incremental_strategy),
            PromptSection(_prompt_title(prompt_constants.INCREMENTAL_USER_SECTION_TITLES, "dependency_context"), dependency_context),
            PromptSection(_prompt_title(prompt_constants.INCREMENTAL_USER_SECTION_TITLES, "source_strategy"), source_specific_strategy),
            PromptSection(_prompt_title(prompt_constants.INCREMENTAL_USER_SECTION_TITLES, "sdk_guidance"), sdk_guidance),
            PromptSection(_prompt_title(prompt_constants.INCREMENTAL_USER_SECTION_TITLES, "nearby_patterns"), _meaningful_context(nearby_test_patterns)),
            PromptSection(_prompt_title(prompt_constants.INCREMENTAL_USER_SECTION_TITLES, "source_risks"), _meaningful_context(source_risk_context)),
            PromptSection("", memory_context),
            PromptSection("", final_rule_tail),
            PromptSection("", incremental_rules),
            ],
        )
    )
    user_content = prompt_bundle.user_prompt

    log_section(f"INCREMENTAL COVERAGE GENERATION REQUEST FOR {class_name}", category="context")
    log_block("INCREMENTAL GENERATION SYSTEM PROMPT", prompt_bundle.system_prompt, category="context", console=False)
    log_block("INCREMENTAL GENERATION USER CONTEXT", user_content, category="context", console=False)
    print_prompt_to_terminal(
        title=f"FINAL INCREMENTAL GENERATION PROMPT SENT TO MODEL: {class_name}",
        system_prompt=prompt_bundle.system_prompt,
        user_prompt=user_content,
    )
    log_message(f"📥 Streaming supplemental coverage tests for {class_name}:\n", category="info")

    raw_output = _stream_kotlin_test(
        prompt_bundle,
        temperature=0.45,
        
    )

    return extract_kotlin_code(raw_output)
