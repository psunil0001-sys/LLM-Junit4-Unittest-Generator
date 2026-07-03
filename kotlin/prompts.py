# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Assembles prompt rule helpers and fixture playbooks.
# Prompt helper functions and generated fixture playbook assembly.

from UnitTest_gen.kotlin.prompt_constants import *

from UnitTest_gen.kotlin.guardrail_catalog import orchestration_gate_text

FIXTURE_PLAYBOOK_VERIFIED_UI_CLICK = (
    "### PLAYBOOK: verified_ui_click\n"
    "Prerequisites: Fragment attached with lifecycle owner; target view registered in onViewCreated.\n"
    "Steps: performClick() on the view that received setOnClickListener; verify collaborator or navigation effect.\n"
    + orchestration_gate_text("missing_coverage_trigger_click", "performClick on registered view after attach")
)

FIXTURE_PLAYBOOK_VERIFIED_OBSERVER_AND_CLICK = (
    "### PLAYBOOK: verified_observer_and_click\n"
    "Prerequisites: delegated ViewModel reachable from Activity ViewModelStore.\n"
    "Steps: postValue/setValue on observed state, idle main looper, then performClick on the registered view.\n"
    + orchestration_gate_text(
        "missing_coverage_trigger_observer_before_click",
        "emit observed state, idle main looper, then performClick",
    )
)

FIXTURE_PLAYBOOK_VERIFIED_OBSERVER_EMISSION = (
    "### PLAYBOOK: verified_observer_emission\n"
    "Steps: attach Fragment, post/set observed LiveData/StateFlow, idle looper, assert observer-driven effect.\n"
    + orchestration_gate_text("missing_coverage_trigger_observer", "post/set observed state after attach")
)

FIXTURE_PLAYBOOK_VERIFIED_DIALOG_CALLBACK = (
    "### PLAYBOOK: verified_dialog_callback\n"
    "Steps: open dialog through public path; invoke real callback body; do not mockStatic AlertDialogHelper.\n"
    + orchestration_gate_text("missing_coverage_trigger_dialog", "invoke real dialog callback")
)

FIXTURE_PLAYBOOK_VERIFIED_MENU_CALLBACK = (
    "### PLAYBOOK: verified_menu_callback\n"
    "Steps: open menu through public path; invoke menu item callback; assert collaborator effect.\n"
    + orchestration_gate_text("missing_coverage_trigger_menu", "invoke menu callback after attach")
)

FIXTURE_PLAYBOOK_VERIFIED_ACTIVITY_RESULT_CALLBACK = (
    "### PLAYBOOK: verified_activity_result_callback\n"
    "Steps: attach Fragment; invoke stored ActivityResult callback with required payload.\n"
    + orchestration_gate_text("missing_coverage_trigger_activity_result", "invoke ActivityResult callback")
)

FIXTURE_PLAYBOOK_CONTROLLED_EXCEPTION_PATH = (
    "### PLAYBOOK: controlled_exception_path\n"
    "Steps: arrange verified throwable; assert stable fallback behavior from public API.\n"
    + orchestration_gate_text("missing_coverage_trigger_exception", "drive verified exception path")
)

FIXTURE_PLAYBOOK_CONTROLLED_COUNTDOWN_CALLBACK = (
    "### PLAYBOOK: controlled_countdown_callback\n"
    "Steps: capture countdown callback; advance time deterministically; invoke callback.\n"
    + orchestration_gate_text("missing_coverage_trigger_countdown", "invoke countdown callback")
)

FIXTURE_PLAYBOOK_VERIFIED_COROUTINE_COMPLETION = (
    "### PLAYBOOK: verified_coroutine_completion\n"
    "Steps: runTest; advanceUntilIdle/runCurrent; assert stable state after scheduler drains.\n"
    + orchestration_gate_text("missing_coverage_trigger_coroutine", "runTest plus advanceUntilIdle")
)

FIXTURE_PLAYBOOK_VERIFIED_STREAM_EMISSION = (
    "### PLAYBOOK: verified_stream_emission\n"
    "Steps: provide a finite flow/stream; invoke the public entry under runTest; advanceUntilIdle; assert emitted state.\n"
    + orchestration_gate_text("missing_coverage_trigger_coroutine", "emit finite stream and drain scheduler")
)

FIXTURE_PLAYBOOK_VERIFIED_CALLBACK = (
    "### PLAYBOOK: verified_callback\n"
    "Steps: reach registration through the public entry; capture or trigger the concrete callback; assert its observable effect.\n"
    + orchestration_gate_text("missing_coverage_trigger_click", "trigger registered callback through public path")
)

FIXTURE_PLAYBOOK_ATTACHED_HILT_FRAGMENT = (
    "### PLAYBOOK: attached_hilt_fragment\n"
    "Steps: HiltAndroidRule.inject(); manifest-declared HiltTestActivity; commitNow/start/resume; reuse shared test Hilt bindings.\n"
    + orchestration_gate_text("missing_coverage_observation_toolbar", "stub CarUi toolbar before attach when needed")
)

FIXTURE_PLAYBOOK_PUBLIC_METHOD = (
    "### PLAYBOOK: public_method\n"
    "Invoke the selected public method with verified fixtures; assert observable effect."
)
FIXTURE_PLAYBOOK_VIEWMODEL_PUBLIC_METHOD = (
    "### PLAYBOOK: viewmodel_public_method\n"
    "Construct ViewModel with mocks; call public method; assert state or collaborator interaction."
)
FIXTURE_PLAYBOOK_VIEWMODEL_SYNC_PUBLIC = (
    "### PLAYBOOK: viewmodel_sync_public\n"
    "Call synchronous public ViewModel API without coroutine final-state assertions."
)
FIXTURE_PLAYBOOK_BRANCH_PROBE = (
    "### PLAYBOOK: branch_probe\n"
    "Drive the exact branch label or guard condition from the Kover gap."
)

FIXTURE_PLAYBOOK_BY_ID = {
    "verified_ui_click": FIXTURE_PLAYBOOK_VERIFIED_UI_CLICK,
    "verified_observer_and_click": FIXTURE_PLAYBOOK_VERIFIED_OBSERVER_AND_CLICK,
    "verified_observer_emission": FIXTURE_PLAYBOOK_VERIFIED_OBSERVER_EMISSION,
    "verified_dialog_callback": FIXTURE_PLAYBOOK_VERIFIED_DIALOG_CALLBACK,
    "verified_menu_callback": FIXTURE_PLAYBOOK_VERIFIED_MENU_CALLBACK,
    "verified_activity_result_callback": FIXTURE_PLAYBOOK_VERIFIED_ACTIVITY_RESULT_CALLBACK,
    "controlled_exception_path": FIXTURE_PLAYBOOK_CONTROLLED_EXCEPTION_PATH,
    "controlled_countdown_callback": FIXTURE_PLAYBOOK_CONTROLLED_COUNTDOWN_CALLBACK,
    "robolectric_delayed_handler": FIXTURE_PLAYBOOK_CONTROLLED_COUNTDOWN_CALLBACK,
    "verified_coroutine_completion": FIXTURE_PLAYBOOK_VERIFIED_COROUTINE_COMPLETION,
    "verified_stream_emission": FIXTURE_PLAYBOOK_VERIFIED_STREAM_EMISSION,
    "verified_callback": FIXTURE_PLAYBOOK_VERIFIED_CALLBACK,
    "attached_hilt_fragment": FIXTURE_PLAYBOOK_ATTACHED_HILT_FRAGMENT,
    "public_method": FIXTURE_PLAYBOOK_PUBLIC_METHOD,
    "viewmodel_sync_public": FIXTURE_PLAYBOOK_VIEWMODEL_SYNC_PUBLIC,
    "viewmodel_public_method": FIXTURE_PLAYBOOK_VIEWMODEL_PUBLIC_METHOD,
    "branch_probe": FIXTURE_PLAYBOOK_BRANCH_PROBE,
}

FRAGMENT_VIEWMODEL_PLAYBOOKS = """
--- FRAGMENT / DELEGATED VIEWMODEL ORCHESTRATION ---
- Follow fixture playbooks for click, observer, dialog, and Hilt attach paths.
- Do not @BindValue delegated ViewModels obtained with by viewModels()/activityViewModels().
- Emit observed state only through mutable fields marked in VIEWMODEL AND DIALOG CONTEXT.
"""

INCREMENTAL_COVERAGE_RULES = """
--- FINAL INCREMENTAL COVERAGE RULES ---
- Generate tests ONLY for selected targets from COVERAGE OPPORTUNITY PLAN.
- Treat alternative opportunities as context only; do not generate tests for them in this attempt.
- Do not generate tests for blocked opportunities; report seam recommendations instead.
- Private methods are coverage consequences reached through selected public APIs only.
- For partial when/case branch gaps, follow the selected trigger recipe; if it says complementary/default input, do not repeat the printed case label.
- For Fragment sources: no per-file graph mutation; reuse shared test Hilt bindings.
- VIEWMODEL AND DIALOG CONTEXT mutability governs postValue/setValue versus public method driving.
"""

INCREMENTAL_EXECUTION_PLAYBOOKS = """
--- INCREMENTAL EXECUTION PLAYBOOKS ---
- Replay fixture steps in order: attach, emit, click/callback, assert.
- One compatible fixture group per supplemental round when multiple gaps share setup.
"""


def has_fixture_playbook(fixture_id: str) -> bool:
    return bool(FIXTURE_PLAYBOOK_BY_ID.get(str(fixture_id or "").strip()))


def _fixture_id_from_playbook_source(source_name: str) -> str:
    prefix = "FIXTURE_PLAYBOOK_"
    if not source_name.startswith(prefix):
        return ""
    suffix = source_name[len(prefix) :].lower()
    return suffix if suffix in FIXTURE_PLAYBOOK_BY_ID else ""


def source_requires_fixture_playbooks(source_categories, source_code: str = "") -> bool:
    categories = {str(category) for category in (source_categories or ())}
    if categories & FIXTURE_PLAYBOOK_UI_SOURCE_CATEGORIES:
        return True
    if "viewmodel" in categories or "hilt_viewmodel" in categories:
        return True
    if "coroutines_flow" in categories and "suspend fun" in (source_code or ""):
        return True
    return False


def filter_retrieval_tags(categories, source_code, contract_tags=None):
    filtered = set(categories or ()) | set(contract_tags or ())
    if not source_requires_fixture_playbooks(filtered, source_code):
        filtered -= ORCHESTRATION_ONLY_RAG_TAGS
    return filtered


def rag_retrieval_limits(
    source_categories,
    source_code: str = "",
    phase: str = "generation",
    *,
    direct_orchestration_in_prompt: bool = False,
) -> tuple[int, int]:
    if source_requires_fixture_playbooks(source_categories, source_code):
        if direct_orchestration_in_prompt:
            if phase == "incremental":
                return 18, 3500
            if phase == "repair":
                return 30, 6000
            return 24, 4500
        if phase == "incremental":
            return 30, 5500
        if phase == "repair":
            return 30, 6000
        return 42, 8000
    if phase == "incremental":
        return 18, 4200
    if phase == "repair":
        return 22, 5000
    return 20, 5000


def filter_retrieved_rule_documents(
    documents,
    source_categories,
    source_code: str = "",
    *,
    fixture_ids: tuple[str, ...] = (),
    direct_orchestration_in_prompt: bool = False,
):
    categories = {str(category) for category in (source_categories or ())}
    orchestration = source_requires_fixture_playbooks(categories, source_code)
    selected_fixtures = {str(fixture_id) for fixture_id in fixture_ids if fixture_id}
    filtered = []
    for document in documents or ():
        source_name = getattr(document, "source_name", "")
        title = (getattr(document, "title", "") or "").lower()
        doc_tags = {str(tag) for tag in getattr(document, "tags", ())}

        if direct_orchestration_in_prompt:
            if source_name in ORCHESTRATION_RULE_SOURCES:
                continue
            if source_name.startswith("FIXTURE_PLAYBOOK_"):
                continue
        elif selected_fixtures and source_name.startswith("FIXTURE_PLAYBOOK_"):
            playbook_fixture = _fixture_id_from_playbook_source(source_name)
            if playbook_fixture in selected_fixtures:
                continue

        if source_name.startswith("FIXTURE_PLAYBOOK_") and not orchestration:
            continue
        if source_name in ORCHESTRATION_RULE_SOURCES and not orchestration:
            continue

        if not orchestration:
            if VIEWMODEL_EXCLUSIVE_RAG_TAGS & doc_tags and not (categories & VIEWMODEL_EXCLUSIVE_RAG_TAGS):
                if title.startswith("viewmodels") or "viewmodel" in title:
                    continue
            if FRAGMENT_EXCLUSIVE_RAG_TAGS & doc_tags and not (categories & FRAGMENT_EXCLUSIVE_RAG_TAGS):
                if title.startswith("fragment testing") or title.startswith("1. fragment"):
                    continue

        filtered.append(document)
    return tuple(filtered)


def contract_ids_covered_by_fixtures(fixture_ids) -> set[str]:
    mapping = {
        "attached_hilt_fragment": "attached_hilt_fragment",
        "verified_ui_click": "plain_fragment",
        "verified_observer_and_click": "fragment_uncontrolled_viewmodel_io",
        "verified_observer_emission": "direct_constructor_viewmodel",
        "verified_dialog_callback": "static_singleton_get_instance",
        "verified_coroutine_completion": "hardcoded_dispatcher_viewmodel",
        "branch_probe": "coverage_blocked_path",
    }
    return {mapping[fid] for fid in (fixture_ids or ()) if fid in mapping}


def format_fixture_playbooks(
    fixture_ids,
    *,
    source_categories=None,
    source_code: str = "",
    is_android_fragment: bool = False,
    include_lifecycle: bool = False,
) -> str:
    selected = [str(fixture_id) for fixture_id in (fixture_ids or []) if fixture_id]
    if not selected:
        return ""
    sections = []
    for fixture_id in selected:
        playbook = FIXTURE_PLAYBOOK_BY_ID.get(fixture_id, "")
        if playbook:
            sections.append(playbook.strip())
    if include_lifecycle and is_android_fragment and "attached_hilt_fragment" not in selected:
        sections.append(FIXTURE_PLAYBOOK_ATTACHED_HILT_FRAGMENT.strip())
    return "\n\n".join(section for section in sections if section.strip())


def incremental_coverage_rules_for_source(categories, source_code: str = "") -> str:
    if not source_requires_fixture_playbooks(categories, source_code):
        return (
            "--- FINAL INCREMENTAL COVERAGE RULES ---\n"
            "- Generate tests ONLY for selected targets from COVERAGE OPPORTUNITY PLAN.\n"
            "- Treat alternative opportunities as context only; do not generate tests for them in this attempt.\n"
            "- Do not generate tests for blocked opportunities.\n"
            "- Private methods are reached only through selected public APIs.\n"
            "- For partial when/case branch gaps, follow the selected trigger recipe; complementary/default probes must not repeat the printed case label.\n"
        )
    return INCREMENTAL_COVERAGE_RULES.strip()
