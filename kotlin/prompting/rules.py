# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Kotlin prompt rule corpus, retrieval, and rule-tail formatting.
"""Kotlin prompt rule corpus, retrieval, and rule-tail formatting."""

import re

from UnitTest_gen.core.logging_utils import log_block
from UnitTest_gen.core.model_runtime import (
    final_output_instruction,
    thinking_enabled,
)
from UnitTest_gen.core.prompt_assembly import (
    RetrievedRuleContext,
    build_rule_corpus,
    format_retrieved_rule_documents,
    retrieve_rule_context,
)
from UnitTest_gen.core.rag_context import RetrievalQuery
from UnitTest_gen.kotlin import prompts as prompt_constants
from UnitTest_gen.kotlin.kotlin_analysis import (
    classify_fragment_source,
    classify_repair,
    classify_source,
    source_complex_fragment_markers,
    source_declares_android_fragment,
    source_declares_viewmodel_class,
    source_rule_categories,
)
from UnitTest_gen.kotlin.project_context import (
    build_hilt_fragment_lifecycle_strategy,
    module_has_manifest_declared_hilt_test_activity,
    module_has_shared_test_hilt_binding,
    owning_module_dir_for_output,
    parse_module_namespace,
)
from UnitTest_gen.kotlin.test_stack_guidance import rule_overlaps_test_stack
from UnitTest_gen.kotlin.strategy_contracts import (
    contract_rule_tags,
    filter_contracts_for_prompt,
    format_contract_prompt_rules,
    select_strategy_contracts,
)
from UnitTest_gen.kotlin.static_analysis import analyze_kotlin_code

PROMPT_CATEGORY_TERMS = {
    "android_activity": {"activity", "activitycontroller", "robolectric", "lifecycle"},
    "hilt_android_activity": {"activity", "androidentrypoint", "hilt", "activitycontroller"},
    "android_application": {"application", "oncreate", "robolectric"},
    "hilt_android_application": {"application", "hiltandroidapp", "hilt", "buildconfig"},
    "dagger_provider_logic": {"dagger", "provides", "provider", "mockito"},
    "declarative_dagger_binding": {"dagger", "binds", "bindsoptionalof", "compile-time"},
    "android_fragment": {"fragment", "fragmentactivity", "fragmentscenario", "lifecycle"},
    "plain_fragment": {"fragment", "fragmentscenario", "fragmentactivity", "lifecycle"},
    "dialog_fragment": {"dialog", "dialogfragment", "show", "window"},
    "hilt_fragment": {"hilt", "androidentrypoint", "hilttestactivity", "hiltandroidtest"},
    "navigation_fragment": {"navigation", "navcontroller", "deeplink", "findnavcontroller"},
    "delegated_viewmodel_fragment": {"activityviewmodels", "viewmodels", "viewmodelprovider", "delegated"},
    "carui_toolbar_fragment": {"carui", "toolbar"},
    "carui_progress_fragment": {"carui", "toolbar", "progressbar", "progressbarcontroller"},
    "carui_toolbar": {"carui", "toolbar", "requiretoolbar", "toolbarcontroller"},
    "carui_progress": {"progressbar", "progressbarcontroller", "toolbar.progressbar"},
    "carui_back_listener": {"registerbacklistener", "backlistener", "supplier", "toolbarcontroller"},
    "nav_deeplink": {"navdeeplinkrequest", "deeplink", "uri", "navcontroller"},
    "static_singleton_get_instance": {"getinstance", "mockedstatic", "singleton", "static"},
    "companion_singleton_static_mock": {"companion", "getinstance", "mockedstatic", "when"},
    "alert_dialog_helper": {"alertdialoghelper", "showalert", "dialog", "callback"},
    "fragment_triggers_uncontrolled_viewmodel_io": {"dispatchers.io", "viewmodel", "click", "delay", "static"},
    "hardcoded_dispatcher_entry": {"dispatchers.io", "coroutinescope", "delay", "viewmodelscope"},
    "detached_coroutine_scope": {"globalscope", "coroutinescope", "detached", "scheduler"},
    "mocking_framework_mismatch": {"mockk", "mockito", "mocking", "framework"},
    "speculative_sdk_exception_constructor": {"exception", "constructor", "sdk", "verified"},
    "sdk_static_seam": {"carmanager", "passiveentry", "static", "sdk", "platform"},
    "blocked_coverage_report": {"blocked", "skip", "report", "coverage", "seam"},
    "coverage_blocked_path": {"blocked", "defer", "seam", "unreachable", "private"},
    "needs_dependency": {"dependency", "missingbinding", "classpath", "binding"},
    "needs_environment": {"environment", "robolectric", "sdk", "platform"},
    "needs_test_setup": {"fixture", "setup", "hilt", "navigation", "carui"},
    "needs_source_seam": {"dispatcher", "static", "private", "seam"},
    "complex_fragment": {"complex", "lifecycle", "public contract", "smoke"},
    "android_ui": {"robolectric", "resources", "views", "callbacks", "ui"},
    "android_context": {"context", "activity", "application", "permissions", "resources"},
    "android_dialog": {"dialog", "dialogfragment", "callback"},
    "android_navigation": {"navigation", "navcontroller", "deeplink"},
    "hilt": {"hilt", "dependency", "injection", "bindvalue", "dagger"},
    "hilt_graph": {"missingbinding", "graph", "closure", "binding", "dependency", "qualifier", "named"},
    "hilt_entrypoint": {"hilt", "androidentrypoint", "hilttestactivity", "hiltandroidtest"},
    "hilt_viewmodel": {"hiltviewmodel", "constructor", "viewmodel"},
    "constructor_injection": {"constructor", "injected", "mock"},
    "viewmodel": {"viewmodel", "livedata", "stateflow", "savedstatehandle"},
    "livedata": {"livedata", "instanttaskexecutorrule"},
    "coroutines_flow": {"coroutines", "flow", "suspend", "runtest", "dispatcher"},
    "dagger_module": {"hilt", "dagger", "module", "binds", "provides"},
    "room": {"room", "dao", "database"},
    "network": {"retrofit", "okhttp", "authenticator", "interceptor", "network"},
    "apollo": {"apollo", "graphql", "mapper", "reflection"},
    "car": {"android", "car", "carui", "toolbar", "progressbar"},
    "worker_service": {"workmanager", "services", "receivers", "worker"},
    "storage_json_auth": {"shared", "preferences", "json", "appauth", "authstate"},
    "firebase": {"firebase"},
    "logging": {"timber", "logging"},
    "android_log": {"android", "log", "robolectric"},
    "static_api": {"static", "mockedstatic", "jvmstatic"},
    "dependency_error": {"unresolved", "dependency", "classpath", "binding"},
    "fixture_strategy": {"fixture", "host", "lifecycle", "activitycontroller", "commitnow"},
    "mocking_error": {"mockito", "mock", "matcher", "verification"},
    "lifecycle_error": {"fragment", "lifecycle", "activity", "view"},
    "assertion_error": {"assertion", "expected", "verify"},
    "coroutine_error": {"coroutine", "flow", "suspend", "dispatcher", "uncaughtexceptionsbeforetest"},
    "api_error": {"type", "overload", "parameter", "api"},
    "syntax_error": {"syntax", "imports", "kotlin"},
    "mockito": {"mockito", "mock", "whenever", "verify", "mockedstatic", "argumentcaptor"},
    "mockk": {"mockk", "every", "verify", "mocking", "framework"},
    "play_services": {"task", "playservices", "gms", "await", "coroutines-play-services"},
    "coverage_strategy": {"coverage", "incremental", "kover", "opportunity", "blocked", "fixture"},
    "verified_observer_and_click": {"observer", "performclick", "postvalue", "setvalue", "idlemainlooper", "composite"},
    "dialog_callback_chain": {"onuseraction", "alertdialoghelper", "dialog", "callback", "fullscreen"},
    "inject_lateinit_field": {"inject", "lateinit", "isadded", "field"},
    "fragment_click_viewmodel_io": {"dispatchers.io", "viewmodel", "click", "viewmodelscope"},
    "fixture_playbooks": {"playbook", "fixture", "verified_ui_click", "trigger"},
}


def _prompt_rule_sources() -> dict[str, str]:
    return {
        name: getattr(prompt_constants, name)
        for name in prompt_constants.RULE_SOURCE_NAMES
    }


def _fixture_ids_from_classification(source_categories: set[str], source_code: str) -> list[str]:
    categories = set(source_categories or ())
    source_code = source_code or ""
    fixtures: list[str] = []
    if "verified_observer_and_click" in categories or (
        "delegated_viewmodel_fragment" in categories
        and "setOnClickListener" in source_code
        and re.search(r"\.observe\s*\(\s*viewLifecycleOwner", source_code)
    ):
        fixtures.append("verified_observer_and_click")
    if categories & {"dialog_callback_chain", "alert_dialog_helper"}:
        fixtures.append("verified_dialog_callback")
    if categories & {"hilt_fragment", "hilt_entrypoint"} and source_declares_android_fragment(source_code):
        fixtures.append("attached_hilt_fragment")
    if "android_fragment" in categories and "setOnClickListener" in source_code:
        fixtures.append("verified_ui_click")
    if "delegated_viewmodel_fragment" in categories and "verified_observer_and_click" not in fixtures:
        fixtures.append("verified_observer_emission")
    if categories & {"coroutines_flow", "viewmodel"}:
        fixtures.append("verified_coroutine_completion")
    elif "coroutines_flow" in categories and "android_fragment" not in categories and "suspend fun" in source_code:
        fixtures.append("verified_coroutine_completion")
    return list(dict.fromkeys(fixtures))


def _fixture_ids_from_opportunity_plan(opportunity_plan) -> list[str]:
    if not opportunity_plan:
        return []
    selected = list(opportunity_plan.get("selected_safe", [])) + list(opportunity_plan.get("selected_attemptable", []))
    return list(dict.fromkeys(getattr(opportunity, "fixture", "") for opportunity in selected if getattr(opportunity, "fixture", "")))


def _prompt_rule_metadata(source_name: str, title: str, text: str):
    title_lower = title.lower()
    lower = f"{title} {text}".lower()
    section_tags = {
        "fragment testing": {"android_fragment", "android_ui"},
        "hilt / dependency injection": {"hilt"},
        "viewmodels, livedata, flow, coroutines": {"viewmodel", "livedata", "coroutines_flow", "coroutine_error"},
        "permissions, activity results, android context": set(),
        "robolectric ui, resources, system services, callbacks": set(),
        "test priority": {"universal"},
        "room": {"room"},
        "hilt / dagger modules": {"hilt", "dagger_module"},
        "retrofit / okhttp / authenticator / interceptor": {"network"},
        "apollo graphql": {"apollo"},
        "android car / car ui": {"car", "android_ui", "static_api"},
        "workmanager / services / receivers": {"worker_service", "android_context"},
        "shared preferences / json / appauth": {"storage_json_auth"},
        "firebase / timber / logging": {"firebase", "logging", "android_log"},
    }
    if source_name == "APOLLO_MAPPER_BLUEPRINTS":
        tags = {"apollo"}
    elif source_name == "FRAGMENT_VIEWMODEL_PLAYBOOKS":
        tags = {
            "android_fragment",
            "delegated_viewmodel_fragment",
            "verified_observer_and_click",
            "dialog_callback_chain",
            "inject_lateinit_field",
            "fixture_playbooks",
            "coverage_strategy",
        }
    elif source_name in {"INCREMENTAL_COVERAGE_RULES", "INCREMENTAL_EXECUTION_PLAYBOOKS"}:
        tags = {"coverage_strategy", "fixture_playbooks", "incremental"}
    elif source_name.startswith("FIXTURE_PLAYBOOK_"):
        fixture_id = {
            "FIXTURE_PLAYBOOK_VERIFIED_UI_CLICK": "verified_ui_click",
            "FIXTURE_PLAYBOOK_VERIFIED_OBSERVER_AND_CLICK": "verified_observer_and_click",
            "FIXTURE_PLAYBOOK_VERIFIED_OBSERVER_EMISSION": "verified_observer_emission",
            "FIXTURE_PLAYBOOK_VERIFIED_DIALOG_CALLBACK": "verified_dialog_callback",
            "FIXTURE_PLAYBOOK_VERIFIED_MENU_CALLBACK": "verified_menu_callback",
            "FIXTURE_PLAYBOOK_VERIFIED_ACTIVITY_RESULT_CALLBACK": "verified_activity_result_callback",
            "FIXTURE_PLAYBOOK_CONTROLLED_EXCEPTION_PATH": "controlled_exception_path",
            "FIXTURE_PLAYBOOK_CONTROLLED_COUNTDOWN_CALLBACK": "controlled_countdown_callback",
            "FIXTURE_PLAYBOOK_VERIFIED_COROUTINE_COMPLETION": "verified_coroutine_completion",
            "FIXTURE_PLAYBOOK_ATTACHED_HILT_FRAGMENT": "attached_hilt_fragment",
            "FIXTURE_PLAYBOOK_PUBLIC_METHOD": "public_method",
            "FIXTURE_PLAYBOOK_VIEWMODEL_PUBLIC_METHOD": "viewmodel_public_method",
            "FIXTURE_PLAYBOOK_VIEWMODEL_SYNC_PUBLIC": "viewmodel_sync_public",
            "FIXTURE_PLAYBOOK_BRANCH_PROBE": "branch_probe",
        }.get(source_name, "")
        tags = {fixture_id, "fixture_playbooks", "coverage_strategy"} if fixture_id else {"fixture_playbooks"}
    elif title_lower in section_tags:
        tags = set(section_tags[title_lower])
    else:
        tags = {
            category
            for category, terms in PROMPT_CATEGORY_TERMS.items()
            if any(term in lower for term in terms)
        }
    if title_lower == "hilt / dependency injection":
        # Hilt lifecycle/graph guidance must not become eligible for plain constructor-injected
        # repositories just because the section text mentions constructors or mocks.
        tags = set()
        if any(term in lower for term in ("fragment", "androidentrypoint", "hiltandroidtest", "hilttestactivity", "robolectric")):
            tags.update({"hilt_entrypoint", "hilt_fragment", "android_fragment", "fixture_strategy"})
        if "@hiltviewmodel" in lower or "hilt viewmodel" in lower or "hiltviewmodel" in lower:
            tags.update({"hilt_viewmodel", "constructor_injection"})
        if "missingbinding" in lower or "graph closure" in lower or "unclosed" in lower or "qualifier" in lower:
            tags.update({"hilt_graph", "dependency_error"})
        if "duplicatebindings" in lower or "bindvalue" in lower:
            tags.update({"hilt", "dependency_error"})
        if "local fake hilttestactivity" in lower or "private-field reflection" in lower or "partial non-hilt" in lower:
            tags.update({"hilt_graph", "fixture_strategy"})

    if title_lower == "fragment testing":
        tags.discard("android_navigation")
        tags.update({"android_fragment", "android_ui"})
        if any(term in lower for term in ("fragmentscenario", "launchfragmentincontainer", "plain non-hilt", "default empty host")):
            tags = {"plain_fragment"}
        if "dialogfragment" in lower or "dialog/window" in lower or "fragment.show" in lower:
            tags = {"dialog_fragment"}
        if any(term in lower for term in ("findnavcontroller", "navcontroller", "navdeeplinkrequest", "deep-link", "navigation.setviewnavcontroller")):
            tags = {"android_navigation", "navigation_fragment"}
        hilt_positive = any(term in lower for term in ("hilttestactivity", "hiltandroidtest"))
        hilt_positive = hilt_positive or (
            any(term in lower for term in ("androidentrypoint", "hilt"))
            and not any(term in lower for term in ("no @androidentrypoint", "no hilt", "non-hilt"))
        )
        if hilt_positive:
            tags = {"hilt_entrypoint", "hilt_fragment", "fixture_strategy"}
        if any(term in lower for term in ("missingbinding", "graph closure", "unclosed hilt graph", "missing dependency", "missing key")):
            tags.update({"hilt_graph", "dependency_error"})
        if any(term in lower for term in ("activityviewmodels", "by viewmodels", "delegated viewmodel", "viewmodelprovider")):
            tags.add("delegated_viewmodel_fragment")
        if "carui" in lower or "toolbarcontroller" in lower:
            tags.update({"car", "carui_toolbar", "carui_toolbar_fragment", "fixture_strategy"})
        if "progressbarcontroller" in lower or "toolbar.progressbar" in lower:
            tags.update({"carui_progress", "carui_progress_fragment"})
        if "registerbacklistener" in lower or "back-listener" in lower or "back listener" in lower:
            tags.update({"carui_back_listener", "carui_toolbar", "fixture_strategy"})
        if "navdeeplinkrequest" in lower or "deep-link" in lower or "deep link" in lower:
            tags.update({"nav_deeplink", "android_navigation", "navigation_fragment", "fixture_strategy"})
        if "hard-coded dispatchers.io" in lower or "uncontrolled viewmodel" in lower:
            tags.update({"fragment_triggers_uncontrolled_viewmodel_io", "hardcoded_dispatcher_entry", "coverage_strategy"})
        if "skip" in lower and "report" in lower and "blocked" in lower:
            tags.update({"blocked_coverage_report", "coverage_blocked_path"})

    if title_lower == "robolectric ui, resources, system services, callbacks":
        tags = {tag for tag in tags if tag in {"static_api"}}
        if any(term in lower for term in ("real app context", "applicationprovider", "explicitly mocked context")):
            tags.add("android_context")
        if any(term in lower for term in ("view", "resource", "dialog", "widget", "layout", "color", "toast", "intent")):
            tags.add("android_ui")
        if "dialog" in lower:
            tags.add("android_dialog")
        if "carui.requiretoolbar" in lower or "toolbarcontroller" in lower:
            tags.update({"car", "carui_toolbar", "carui_toolbar_fragment", "fixture_strategy"})
        if "progressbarcontroller" in lower or "toolbar.progressbar" in lower:
            tags.update({"carui_progress", "carui_progress_fragment"})
        if "registerbacklistener" in lower or "back-listener" in lower or "back listener" in lower:
            tags.update({"carui_back_listener", "carui_toolbar", "fixture_strategy"})
        if "carmanager" in lower or "passiveentry" in lower or "static sdk" in lower or "platform call" in lower:
            tags.update({"sdk_static_seam", "static_api", "coverage_strategy"})
        if "skip" in lower and "report" in lower and "blocked" in lower:
            tags.update({"blocked_coverage_report", "coverage_blocked_path"})

    if title_lower == "permissions, activity results, android context":
        tags = set()
        if "permission" in lower:
            tags.add("android_permission")
        if "activityresult" in lower:
            tags.add("android_activity_result")
        if "permission" not in lower and any(
            term in lower
            for term in (
                "applicationprovider",
                "generic context",
                "application context",
                "mock context",
                "mocked context",
            )
        ):
            tags.add("android_context")

    if title_lower == "firebase / timber / logging":
        if "firebase" not in lower:
            tags.discard("firebase")
        if "timber" not in lower and "logging" not in lower:
            tags.discard("logging")

    if source_name in {"CORE_GENERATION_RULES", "TEST_QUALITY_RULES"}:
        tags.add("universal")
    if source_name == "REPAIR_RULES" and any(term in lower for term in ("root-cause", "preserve production", "keep imports")):
        tags.add("universal")
    if source_name == "KOTLIN_ANDROID_TEST_RULES" and any(term in lower for term in ("junit4", "mockito-kotlin", "unresolved references")):
        tags.add("universal")
    phases = {"repair"} if source_name == "REPAIR_RULES" else {"generation", "incremental", "repair"}
    priority = 100 if "universal" in tags else 40
    if tags & {
        "hilt_graph",
        "fixture_strategy",
        "carui_toolbar",
        "carui_progress",
        "carui_back_listener",
        "nav_deeplink",
        "fragment_triggers_uncontrolled_viewmodel_io",
        "sdk_static_seam",
        "blocked_coverage_report",
        "coverage_blocked_path",
    }:
        priority += 35
    if "hilt_graph" in tags and any(
        term in lower
        for term in (
            "missingbinding",
            "missing binding",
            "graph closure",
            "graph as unclosed",
            "unclosed hilt graph",
            "qualifier",
            "@named",
        )
    ):
        priority += 200
    if source_name == "REPAIR_RULES":
        priority += 30
    return tags, phases, priority


PROMPT_RULE_CORPUS = build_rule_corpus(_prompt_rule_sources(), _prompt_rule_metadata)


def _prompt_title(section_titles: dict[str, str], key: str) -> str:
    return section_titles[key]


_RULE_ENTRY_RE = re.compile(
    r"(?m)^(?P<source>[A-Z][A-Z0-9_]+)\s+rule\s+(?P<number>\d+):\s*$"
)

_RULE_GROUP_ORDER = (
    "Verified context, public contracts & test strategy",
    "Fragment lifecycle, Hilt & navigation",
    "Coroutines, suspend APIs, LiveData & state",
    "Mockito & deterministic collaborators",
    "Robolectric, Android runtime & logging",
    "Workers, services & receivers",
    "Focused repair safeguards",
    "Other applicable safeguards",
)


def _split_retrieved_rules(raw_text: str) -> list[tuple[str, int, str]]:
    matches = list(_RULE_ENTRY_RE.finditer(raw_text or ""))
    if not matches:
        return []

    rules = []
    for index, match in enumerate(matches):
        body_start = match.end()
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(raw_text)
        body = raw_text[body_start:body_end].strip()
        if body:
            rules.append(
                (
                    match.group("source"),
                    int(match.group("number")),
                    body,
                )
            )
    return rules


def _compact_rule_text(text: str) -> str:
    # Keep every instruction, but remove per-rule bullet formatting.
    without_section_lines = re.sub(r"(?m)^\s*Section:\s+.+\n?", "", text or "")
    without_bullets = re.sub(r"(?m)^\s*[-*]\s*", "", without_section_lines)
    return re.sub(r"\s+", " ", without_bullets).strip()


def _rule_group_name(source_name: str, rule_text: str, categories: set[str] | None = None) -> str:
    lower = f"{source_name} {rule_text}".lower()
    categories = set(categories or set())

    if categories & {"android_fragment", "android_dialog"} and any(
        token in lower
        for token in (
            "fragment",
            "lifecycle",
            "navcontroller",
            "navigation",
            "findnavcontroller",
            "activityviewmodels",
            "by viewmodels",
            "delegated viewmodel",
        )
    ):
        return "Fragment lifecycle, Hilt & navigation"

    if any(
        token in lower
        for token in (
            "stateflow",
            "sharedflow",
            "livedata",
            "flow",
            "coroutine",
            "runtest",
            "dispatcher",
            "viewmodelscope",
            "savedstatehandle",
            "suspend",
        )
    ):
        return "Coroutines, suspend APIs, LiveData & state"

    if any(
        token in lower
        for token in (
            "mockito",
            "mock<",
            "whenever",
            "verify",
            "argumentcaptor",
            "matcher",
            "mockedstatic",
            "fake repository",
            "fake clock",
        )
    ):
        return "Mockito & deterministic collaborators"

    if categories & {"android_fragment", "android_ui", "android_dialog", "car", "android_log"} and any(
        token in lower
        for token in (
            "robolectric",
            "applicationprovider",
            "android log",
            "timber",
            "firebase",
            "resources",
            "views",
            "context",
            "shadow",
        )
    ):
        return "Robolectric, Android runtime & logging"

    if "worker_service" in categories and any(
        token in lower
        for token in (
            "workmanager",
            "worker",
            "service",
            "receiver",
            "broadcastreceiver",
        )
    ):
        return "Workers, services & receivers"

    if categories & {"hilt_entrypoint", "android_fragment", "dagger_module"} and any(
        token in lower
        for token in (
            "hilt",
            "dagger",
            "bindvalue",
            "missingbinding",
            "duplicatebindings",
        )
    ):
        return "Fragment lifecycle, Hilt & navigation" if "android_fragment" in categories else "Verified context, public contracts & test strategy"

    if source_name == "REPAIR_RULES":
        return "Focused repair safeguards"

    if source_name in {
        "CORE_GENERATION_RULES",
        "TEST_QUALITY_RULES",
        "KOTLIN_ANDROID_TEST_RULES",
    } or any(
        token in lower
        for token in (
            "verified source",
            "public contract",
            "smallest reliable style",
            "deterministic",
            "assert public state",
            "test quality",
        )
    ):
        return "Verified context, public contracts & test strategy"

    return "Other applicable safeguards"


def format_grouped_kotlin_rule_context(raw_text: str, phase: str, categories: set[str] | None = None) -> str:
    rules = _split_retrieved_rules(raw_text)
    if not rules:
        synthetic = []
        current_source = "CORE_GENERATION_RULES"
        for line in (raw_text or "").splitlines():
            stripped = line.strip()
            if stripped.startswith("Section:"):
                current_source = "CORE_GENERATION_RULES"
                continue
            if stripped.startswith("- "):
                synthetic.append((current_source, 0, stripped[2:]))
        rules = synthetic
    if not rules:
        return (raw_text or "").strip()

    grouped: dict[str, list[str]] = {
        group_name: [] for group_name in _RULE_GROUP_ORDER
    }
    seen: set[str] = set()

    for source_name, _, rule_text in rules:
        compact_text = _compact_rule_text(rule_text)
        fingerprint = compact_text.casefold()

        if not compact_text or fingerprint in seen:
            continue
        if rule_overlaps_test_stack(compact_text):
            continue

        inferred_tags = {
            category
            for category, terms in PROMPT_CATEGORY_TERMS.items()
            if any(term in compact_text.lower() for term in terms)
        }
        if categories and inferred_tags and not (set(categories) & inferred_tags):
            continue

        seen.add(fingerprint)
        group_name = _rule_group_name(source_name, compact_text, categories)
        grouped.setdefault(group_name, []).append(compact_text)

    heading = (
        "### GROUPED PATCH REPAIR DIRECTIVES"
        if phase == "repair"
        else "### GROUPED TEST GENERATION DIRECTIVES"
    )

    sections = [
        heading,
        (
            "Apply exact compiler locations and verified project context first. "
            "Use only the topic blocks relevant to the current source/error."
        ),
    ]

    for group_name in _RULE_GROUP_ORDER:
        directives = grouped.get(group_name, [])
        if not directives:
            continue

        bullet_lines = "\n".join(f"- {directive}" for directive in directives)
        sections.append(f"#### {group_name}\n{bullet_lines}")

    return "\n\n".join(sections)

def filter_incremental_rule_categories(
    categories: set[str],
    source_code: str,
) -> set[str]:
    filtered = set(categories)

    if (
        "viewmodel" in filtered
        and "android_fragment" not in filtered
        and "fragment" not in source_code.lower()
    ):
        filtered.discard("android_navigation")
        filtered.discard("worker_service")
        filtered.discard("android_context")

    return filtered

def retrieve_kotlin_rule_context(
    source_code: str,
    phase: str,
    error_context: str = "",
    *,
    fixture_ids=None,
    direct_orchestration_in_prompt: bool = False,
):
    source_profile = classify_source(source_code)
    profile = classify_repair(source_profile, error_context) if phase == "repair" else None
    categories = set(profile.categories if profile else source_profile.categories)
    if phase == "incremental":
        categories = filter_incremental_rule_categories(categories, source_code)
    contracts = select_strategy_contracts(source_profile.categories, profile.categories if profile else None)
    contract_tags = contract_rule_tags(contracts)
    categories = prompt_constants.filter_retrieval_tags(categories, source_code, contract_tags)
    query_terms = set(profile.query_terms if profile else source_profile.query_terms)
    for category in categories:
        query_terms.update(PROMPT_CATEGORY_TERMS.get(category, set()))
    limit, max_chars = prompt_constants.rag_retrieval_limits(
        categories,
        source_code,
        phase,
        direct_orchestration_in_prompt=direct_orchestration_in_prompt,
    )
    result = retrieve_rule_context(
        PROMPT_RULE_CORPUS,
        RetrievalQuery(frozenset(categories), frozenset(query_terms), phase),
        limit=limit,
        max_chars=max_chars,
    )
    raw_document_count = len(result.documents)
    filtered_documents = prompt_constants.filter_retrieved_rule_documents(
        result.documents,
        source_profile.categories,
        source_code,
        fixture_ids=tuple(fixture_ids or ()),
        direct_orchestration_in_prompt=direct_orchestration_in_prompt,
    )
    if len(filtered_documents) != len(result.documents):
        allowed_ids = {document.rule_id for document in filtered_documents}
        filtered_scores = tuple(
            score
            for document, score in zip(result.documents, result.scores)
            if document.rule_id in allowed_ids
        )
        result = RetrievedRuleContext(
            filtered_documents,
            filtered_scores,
            format_retrieved_rule_documents(filtered_documents),
        )
    log_block(
        f"CLASSIFICATION-RETRIEVED PROMPT RULES ({phase})",
        "Categories: "
        + ", ".join(sorted(categories))
        + f"\ndirect_orchestration_in_prompt={direct_orchestration_in_prompt}"
        + f"\nretrieved={len(result.documents)} excluded_by_filter={raw_document_count - len(result.documents)}"
        + "\n"
        + "\n".join(
            f"- {document.rule_id} score={score:.1f}"
            for document, score in zip(result.documents, result.scores)
        ),
        category="context",
        console=False,
    )
    return result


def _meaningful_context(text: str) -> str:
    stripped = (text or "").strip()
    if not stripped:
        return ""
    empty_markers = (
        "No nearby same-package test examples were found.",
        "No output file path was available for nearby test lookup.",
        "No nearby test directory was available.",
        "No static generated-test smells detected for this error block.",
        "No special source-shape generation guidance detected.",
    )
    return "" if stripped in empty_markers else stripped


def build_source_strategy_context(class_name: str, source_code: str, output_file_path: str | None = None) -> str:
    return "\n".join(
        part
        for part in [
            build_source_specific_test_strategy(class_name, source_code, output_file_path),
            _meaningful_context(build_generation_source_shape_guidance(class_name, source_code, output_file_path)),
        ]
        if part and part.strip()
    )


def should_include_repair_source_risks(focused_error_block: str, group_key: str) -> bool:
    text = f"{focused_error_block}\n{group_key}".lower()
    return any(
        marker in text
        for marker in (
            "assertion",
            "expected:<",
            "but was:<",
            "wantedbutnotinvoked",
            "neverwanted",
            "uncaughtexceptionsbeforetest",
            "coverage",
            "kover",
            "behavior",
        )
    )

def _format_prompt_playbooks(
    fixture_ids,
    source_categories,
    source_code: str,
) -> str:
    return prompt_constants.format_fixture_playbooks(
        fixture_ids,
        source_categories=source_categories,
        source_code=source_code,
        is_android_fragment=source_declares_android_fragment(source_code),
    )


def build_generation_rule_tail(
    is_fragment: bool,
    is_hilt: bool,
    is_viewmodel: bool,
    uses_framework_blueprints: bool,
    is_apollo_mapper: bool,
    source_code: str = "",
    dependency_context: str = "",
    source_categories=None,
    phase: str = "generation",
    fixture_ids=None,
    playbooks_in_prompt: bool = False,
    direct_orchestration_in_prompt: bool = False,
) -> str:
    categories = filter_incremental_rule_categories(
        set(source_categories or source_rule_categories(source_code)),
        source_code,
    )
    orchestration_direct = direct_orchestration_in_prompt or playbooks_in_prompt
    contracts = filter_contracts_for_prompt(
        select_strategy_contracts(categories),
        fixture_ids=fixture_ids,
        source_categories=categories,
        source_code=source_code,
    )
    mandatory_contract_rules = format_contract_prompt_rules(contracts, include_templates=False)
    playbook_text = ""
    if not playbooks_in_prompt:
        playbook_text = _format_prompt_playbooks(
            fixture_ids or [],
            categories,
            source_code,
        )
    retrieved = retrieve_kotlin_rule_context(
        source_code,
        phase,
        fixture_ids=fixture_ids,
        direct_orchestration_in_prompt=orchestration_direct,
    )
    grouped_rules = format_grouped_kotlin_rule_context(retrieved.text, phase, categories)

    sections = [
        "### FINAL GENERATION RULES - APPLY AFTER READING ALL CONTEXT",
    ]
    if mandatory_contract_rules:
        sections.append("### MANDATORY STRATEGY CONTRACTS\n" + mandatory_contract_rules)
    if playbook_text:
        sections.append("### MANDATORY FIXTURE PLAYBOOKS\n" + playbook_text)
    if phase == "incremental" and not playbooks_in_prompt:
        sections.append(
            prompt_constants.incremental_coverage_rules_for_source(categories, source_code)
        )
    sections.append(grouped_rules)
    sections.append(
        """
        - Static mocking is permitted only when verified context explicitly shows a Java static or Kotlin @JvmStatic API.
        - Do not use MockedStatic for Kotlin object methods, top-level functions, or unverified utility declarations.
        - For unverified static APIs or mutable global flags, prefer reachable false-path/public behavior and skip only branches with no verified execution path.
        """
    )
    sections.append(
        build_rule_conflict_resolution(
            categories,
            "source-specific test strategy, source-shape guidance, deterministic coverage strategy, and selected framework blueprint",
            source_code=source_code,
        )
    )

    sections.append(
        "Final output contract:\n"
        "- Return ONLY one complete executable Kotlin file inside ```kotlin markdown.\n"
        "- The file must begin with package <same_as_source_under_test> before any imports.\n"
        f"- {final_output_instruction()}\n"
        "- Favor precise, meaningful, compile-safe tests with strong assertions.\n"
    )

    return "\n\n".join(section.strip() for section in sections if section and section.strip())

def build_source_specific_test_strategy(class_name: str, source_code: str, output_file_path: str | None = None) -> str:
    """
    Deterministic source classification for prompt/context building. This does
    not generate code or mutate files; it narrows the model's strategy before
    it sees the generic Android blueprint.
    """
    static_report = analyze_kotlin_code(source_code)
    fragment_profile = classify_fragment_source(source_code, static_report)
    is_fragment = fragment_profile.is_fragment
    is_viewmodel_source = source_declares_viewmodel_class(source_code, static_report)
    is_hilt_fragment = fragment_profile.is_hilt
    delegated_viewmodel = fragment_profile.has_delegated_viewmodel
    carui_progress = fragment_profile.has_carui_progress
    source_profile = classify_source(source_code, static_report)
    contracts = select_strategy_contracts(source_profile.categories)
    markers = source_complex_fragment_markers(source_code, static_report) if is_fragment else []
    complex_fragment = fragment_profile.is_complex

    classification = "plain Kotlin/source"
    if complex_fragment:
        classification = "large complex Fragment"
    elif is_hilt_fragment:
        classification = "@AndroidEntryPoint Fragment"
    elif is_fragment:
        classification = "Fragment/DialogFragment"
    elif is_viewmodel_source:
        classification = "ViewModel"

    bullets = [
        f"Classification: {classification}.",
        f"Target class: {class_name}.",
    ]
    if markers:
        bullets.append("Detected source features: " + ", ".join(markers) + ".")
    confirmed_execution_risks = static_report.confirmed_findings(
        "hardcoded_dispatcher",
        "detached_coroutine_scope",
    )
    if confirmed_execution_risks:
        risk_methods = sorted(
            {
                function.name
                for finding in confirmed_execution_risks
                for function in static_report.functions
                if function.span.start_line <= finding.span.start_line <= function.span.end_line
            }
        )
        bullets.append(
            "Confirmed static execution risks: hard-coded/detached coroutine work in "
            + (", ".join(risk_methods) if risk_methods else "the reported source spans")
            + ". These paths require a verified dispatcher/scope seam before final-state or missed-body coverage tests."
        )
    contract_rules = format_contract_prompt_rules(
        filter_contracts_for_prompt(
            [
                contract
                for contract in contracts
                if contract.id
                in {
                    "attached_hilt_fragment",
                    "carui_back_listener_fixture",
                    "nav_deeplink_verification",
                    "static_singleton_get_instance",
                    "fragment_uncontrolled_viewmodel_io",
                    "plain_fragment",
                    "direct_constructor_viewmodel",
                    "hardcoded_dispatcher_viewmodel",
                    "sdk_static_or_platform_seam",
                }
            ],
            source_categories=source_profile.categories,
            source_code=source_code,
        ),
        include_templates=is_hilt_fragment,
    )
    if contract_rules:
        bullets.append(contract_rules)

    if complex_fragment:
        bullets.extend(
            [
                "Use complex-fragment mode: generate a compact portfolio of high-confidence tests from one verified fixture instead of a broad speculative matrix.",
                "If multiple missed lines are reachable from the same setup, cover them in one test or a small table-style group; prefer 2-4 tests only when each test covers a distinct public behavior path.",
                "Avoid reflection into private Fragment methods, private binding fields, and large observer-state matrices unless a real lifecycle and the same real state owner are available.",
                "Pre-generation fail checklist: reject private-method reflection, private-field reflection, manual singleton injection, property-assignment verification, Java ArgumentCaptor for Kotlin non-null NavDeepLinkRequest, TestNavHostController for cross-graph NavDeepLinkRequest, lifecycle re-entry after commitNow, and broad observer-state tests that cannot drive the real delegated ViewModel.",
            ]
        )

    if is_hilt_fragment:
        bullets.append(
            "Attached Hilt Fragment preferred lane: canonical Hilt/Robolectric attach, arguments set before attach, no manual onCreateView/onViewCreated re-entry, mocked NavController URI capture for cross-graph deep links, CarUi static mock, and only public lifecycle/click behavior."
        )
        module_dir = owning_module_dir_for_output(output_file_path)
        module_namespace = parse_module_namespace(module_dir) if module_dir else ""
        if module_namespace:
            bullets.append(
                f"Verified Hilt host import: import {module_namespace}.HiltTestActivity."
            )
        if output_file_path:
            host_status = (
                "available"
                if module_has_manifest_declared_hilt_test_activity(output_file_path)
                else "not verified"
            )
            bullets.append(
                f"Manifest-declared @AndroidEntryPoint HiltTestActivity status: {host_status}."
            )
            if module_has_shared_test_hilt_binding(
                output_file_path,
                "IDigitalKeyFirebase",
            ):
                bullets.append(
                    "Shared test Hilt binding available: unqualified IDigitalKeyFirebase is provided by the owning module test graph. Do not generate per-file @BindValue/@Provides/@TestInstallIn for that same key."
                )
    elif is_fragment:
        if not any(contract.id == "plain_fragment" for contract in contracts):
            bullets.append(
                "Use the plain Fragment/DialogFragment lane: FragmentScenario or a real Robolectric FragmentActivity/DialogFragment host is valid only when no Hilt host, Activity-level setup, or custom graph is required."
            )

    if delegated_viewmodel and is_fragment and not (
        {"verified_observer_and_click", "verified_observer_emission"} & source_profile.categories
    ):
        bullets.extend(
            [
                "Delegated ViewModel strategy: do not @BindValue a ViewModel that the Fragment obtains with by viewModels()/activityViewModels().",
                "Generate observer-state tests only when the same real delegated ViewModel can be obtained from the host/ViewModelProvider and driven through verified public methods after attachment.",
                "When the real delegated ViewModel graph is not practical, avoid observer-state tests and cover stable lifecycle UI, navigation setup, callbacks, or direct public contracts instead.",
            ]
        )
        if is_fragment and any(
            usage.kind == "delay"
            for usage in static_report.coroutine_usages
        ):
            bullets.append(
                "Delayed Fragment coroutine strategy: do not force delayed lifecycleScope navigation or final ViewModel/network state coverage unless the dispatcher and delegated ViewModels are test-controlled; cover stable immediate UI/callback behavior and report true delayed-only lines as blocked with evidence."
            )

    if carui_progress:
        bullets.extend(
            [
                "CarUi toolbar.progressBar strategy: create a small fixture helper that returns both ToolbarController and ProgressBarController.",
                "Stub CarUi.requireToolbar with the real host Activity and stub toolbar.progressBar before fragment attach/resume.",
                "Verify the returned ProgressBarController directly; never use BarController, Android ProgressBar, chained verification, or property assignment.",
            ]
        )
    elif fragment_profile.has_carui_toolbar:
        bullets.append(
            "CarUi toolbar strategy: hold the MockedStatic<CarUi>, stub CarUi.requireToolbar with the real host Activity, and close the static mock in teardown."
        )

    if "BuildConfig." in source_code:
        bullets.append(
            "BuildConfig-gated branches must align with the active Gradle variant or be exercised through a verified callback/public path; do not assert disabled-variant behavior as if it always runs."
        )

    if "BaseFirebaseEvents" in source_code and "firebase" in source_profile.categories:
        bullets.append(
            "BaseFirebaseEvents first-generation strategy: call public wrapper methods only for reachable source lines and TODO paths; "
            "do not assert digitalKeyFirebase.pushEvents(...) or setCrashlyticsCollectionEnabled(...) unless verified context proves the active Gradle variant has BuildConfig.IS_GAS_COUNTRY=true."
        )

    const_false_flags = [
        name
        for name in ("isFailedDependencyTestEnabled", "isTestingEnabledDK")
        if name in source_code
    ]
    if const_false_flags:
        bullets.append(
            "Compile-time constant test flags detected: "
            + ", ".join(const_false_flags)
            + ". Project context defines these as const val false. Do not force the flags true; cover reachable false-path behavior through public calls, and report true-only lines as blocked only when no production seam exists."
        )

    if is_viewmodel_source and static_report.confirmed_findings("hardcoded_dispatcher", "detached_coroutine_scope"):
        if not any(contract.id == "hardcoded_dispatcher_viewmodel" for contract in contracts):
            bullets.extend(
                [
                    "Hard-coded dispatcher strategy: production launches work on Dispatchers.IO, so runTest, runCurrent, and advanceUntilIdle cannot prove final coroutine state unless a verified dispatcher seam exists.",
                    "Prefer synchronous public methods, immediate state setters, direct pure branch methods, and collaborator interactions that happen before or outside the hard-coded IO launch.",
                    "For IO-launched public methods, assert only stable immediate effects or collaborator calls unless the source has an injectable dispatcher or test-controlled scheduler seam.",
                ]
            )

    if is_viewmodel_source and any(
        usage.kind == "delay"
        and re.search(r"delay\s*\(\s*(?:\d{4,}|[A-Za-z_][A-Za-z0-9_]*)", usage.evidence)
        for usage in static_report.coroutine_usages
    ):
        bullets.append(
            "Long real-delay strategy: do not wait in real time and do not assume virtual time controls delays inside hard-coded Dispatchers.IO or standalone CoroutineScope launches."
        )

    if "NavDeepLinkRequest" in source_code:
        bullets.append(
            "Deep-link navigation strategy: install a mocked NavController on the real Fragment view and capture the NavDeepLinkRequest URI per MODULE SDK AND TEST STACK CONFIGURATION."
        )
    elif "findNavController" in source_code:
        bullets.append(
            "Navigation strategy: install a TestNavHostController or mocked NavController on the created Fragment view before source code can navigate."
        )

    if "AlertDialogHelper.getInstance" in source_code:
        bullets.append(
            "Delegated singleton helper strategy: do not reflect private INSTANCE fields, do not mockStatic plain Kotlin companion getInstance(), and do not call the helper directly from tests. Cover missed lines through the target class public entry path (UI click, menu action, lifecycle host) that invokes the helper."
        )

    return "\n".join(f"- {bullet}" for bullet in dict.fromkeys(bullets))

def build_rule_conflict_resolution(categories: set[str], first_rule: str, source_code: str = "") -> str:
    orchestration = prompt_constants.source_requires_fixture_playbooks(categories, source_code)
    if orchestration:
        priority = (
            "- Priority: verified context > fixture playbook > strategy contract > grouped RAG rules > generic blueprints."
        )
    else:
        priority = (
            "- Priority: verified context > strategy contract > grouped RAG rules matched to source classification > generic blueprints."
        )
    lines = [
        "Rule conflict resolution:",
        priority,
        f"- Apply the most specific applicable rule first: {first_rule}, source-specific diagnostics, verified context, then generic Kotlin/JUnit rules.",
    ]
    if "hilt" in categories and "android_fragment" in categories:
        lines.append(
            "- For @AndroidEntryPoint Fragment sources, the Hilt/Robolectric lifecycle strategy overrides generic FragmentActivity/FragmentScenario rules."
        )
    if "android_fragment" in categories and "nav_deeplink" in categories:
        lines.append(
            "- For Fragment sources that emit NavDeepLinkRequest, mocked NavController URI capture overrides TestNavHostController graph ownership unless the exact graph is installed."
        )
    elif "android_fragment" in categories and "android_navigation" in categories:
        lines.append(
            "- For Fragment sources that call findNavController(), the TestNavHostController + Navigation.setViewNavController rule overrides mocked NavController patterns."
        )
    if "apollo" in categories:
        lines.append(
            "- For Apollo generated types, reflection-only handling overrides normal constructor/mocking patterns."
        )
    return "\n".join(lines)

def _repair_rule_modules(source_code: str, source_categories, error_context: str) -> tuple[set, str]:
    categories = filter_incremental_rule_categories(
        set(source_categories or source_rule_categories(source_code)),
        source_code,
    )
    selected_repair_modules = format_grouped_kotlin_rule_context(
        retrieve_kotlin_rule_context(
            source_code,
            "repair",
            error_context,
        ).text,
        phase="repair",
        categories=categories,
    )
    return categories, selected_repair_modules


def build_patch_repair_rule_tail(ticks: str, source_code: str = "", current_test_code: str = "", source_categories=None, error_context: str = "") -> str:
    categories, selected_repair_modules = _repair_rule_modules(source_code, source_categories, error_context)

    non_thinking_json_contract = (
        "\nNon-thinking model strictness:\n"
        "- Return only valid JSON. No markdown. No explanation.\n"
        if not thinking_enabled()
        else ""
    )

    return f"""
### FINAL PATCH REPAIR RULES - APPLY AFTER READING ALL CONTEXT
{selected_repair_modules}

{build_rule_conflict_resolution(categories, "exact error location and static generated-test diagnostics")}

Return contract:
- Return ONLY plain JSON without markdown.
- {final_output_instruction()}
- Patch the generated test file.
- Prefer one small concrete patch for the primary root cause; do not compare alternative framework strategies in final output.
- When EXACT GENERATED TEST ERROR LOCATIONS contains a highlighted line, repair that highlighted line or its immediate expression before considering other code.
- Use verified project APIs, constructors, symbols, and source behavior; use generated Apollo classes through Class.forName strings and Java reflection.
- Keep valid coverage and edge-case intent while replacing unsupported speculation with verified behavior.
- For far-apart errors, return multiple small patches in a patches array.
- For partial confidence, fix the first concrete occurrence with verified context.

JSON formats:
{{
  "old_text": "exact existing code block from CURRENT GENERATED TEST CODE",
  "new_text": "replacement code block"
}}
or:
{{
  "patches": [
    {{
      "old_text": "exact existing code block from CURRENT GENERATED TEST CODE",
      "new_text": "replacement code block"
    }}
  ]
}}

Patch requirements:
- old_text must exactly match current file content; copy old_text verbatim from CURRENT GENERATED TEST CODE or EXACT GENERATED TEST ERROR LOCATIONS excerpts.
- Patch the smallest meaningful block.
- Preserve full-file content outside the smallest needed patch.
- Preserve surrounding code whenever possible.
- For multiple far-apart same-root-cause occurrences, return multiple patches.
{non_thinking_json_contract}
""".strip()

def build_full_file_repair_rule_tail(ticks: str, source_code: str = "", current_test_code: str = "", source_categories=None, error_context: str = "") -> str:
    categories, selected_repair_modules = _repair_rule_modules(source_code, source_categories, error_context)

    return f"""
### FINAL FULL-FILE REPAIR RULES - APPLY AFTER READING ALL CONTEXT
{selected_repair_modules}

{build_rule_conflict_resolution(categories, "exact error location and static generated-test diagnostics")}

Return contract:
- Return ONLY executable Kotlin code inside {ticks}kotlin markdown.
- {final_output_instruction()}
- Fix the focused Gradle/Kotlin/JUnit failure and identical same-root-cause occurrences.
- Rewrite only what is needed for the primary root cause; avoid broad framework strategy changes when a local expression fix exists.
- When EXACT GENERATED TEST ERROR LOCATIONS contains a highlighted line, repair that highlighted line or its immediate expression before considering other code.
- Use verified project APIs, constructors, symbols, and source behavior; use generated Apollo classes through Class.forName strings and Java reflection.
- Preserve valid meaningful coverage and replace unsupported speculation with verified behavior.
- Keep imports complete and the file compile-safe.

""".strip()

def build_generation_source_shape_guidance(class_name: str, source_code: str, output_file_path: str | None = None) -> str:
    """
    Small deterministic guidance from the source shape. This keeps common
    setup traps out of first drafts without adding repo-specific prompt bulk.
    """
    lower_source = source_code.lower()
    bullets = []
    fragment_profile = classify_fragment_source(source_code)
    source_profile = classify_source(source_code)
    categories = set(source_profile.categories)
    dependency_context = ""

    if "todo(" in lower_source:
        bullets.append(
            "Source contains Kotlin TODO(...); tests for that path should expect NotImplementedError."
        )

    if ("digitalkeyutils.getappversion" in lower_source or "getappversion(context" in lower_source) and (
        categories & {"android_context", "firebase"}
    ):
        bullets.append(
            "Source reads app version from Context; prefer ApplicationProvider/Robolectric real context and assert against the helper result."
        )

    if (
        "pushEvent(" in source_code
        and "BaseFirebaseEvents" in source_code
        and "firebase" in categories
    ):
        bullets.append(
            "BaseFirebaseEvents wrapper detected. Its downstream firebase calls are BuildConfig.IS_GAS_COUNTRY-gated; "
            "do not verify digitalKeyFirebase.pushEvents(...) or setCrashlyticsCollectionEnabled(...) unless the verified active variant is GAS-enabled. "
            "Default first draft should call public wrappers for reachable lines/TODO paths and avoid positive downstream firebase interaction assertions. "
            "When GAS-enabled, prefer a spy or downstream collaborator capture with Map<String, String> matching the source signature."
        )

    if fragment_profile.is_fragment and not fragment_profile.is_hilt and "android_fragment" in categories:
        bullets.append(
            "Plain Fragment/DialogFragment source detected; use FragmentScenario or a real Robolectric FragmentActivity/DialogFragment lifecycle only when that host satisfies the source's theme, dialog, navigation, and Activity requirements."
        )

    if fragment_profile.is_hilt and categories & {"android_fragment", "hilt_fragment"}:
        bullets.append("@AndroidEntryPoint Fragment detected.")
        bullets.append(build_hilt_fragment_lifecycle_strategy(output_file_path, source_code))

    if (
        "object " in source_code
        and ("Context" in source_code or "PackageManager" in source_code)
        and categories & {"android_context", "android_ui"}
    ):
        bullets.append(
            "Kotlin object utility with Android framework calls detected; prefer real Robolectric fixtures/shadows for framework behavior."
        )

    if ("android.car" in source_code or "CarPropertyManager" in source_code or "Car.createCar" in source_code) and "car" in categories:
        bullets.append(
            "Android Car API source detected; static Car.createCar can be mocked with org.mockito.Mockito.mockStatic, "
            "and CarPropertyManager.getProperty returns android.car.hardware.CarPropertyValue<T>, not CarPropertyManager.CarPropertyEventValue."
        )

    if "@Inject" in source_code and "constructor" in source_code and "constructor_injection" in categories:
        bullets.append(
            "Constructor-injected source detected; instantiate with explicit Mockito-Kotlin mocks for project-owned collaborators."
        )

    if not bullets:
        return "No special source-shape generation guidance detected."

    return "\n".join(f"- {bullet}" for bullet in dict.fromkeys(bullets))
