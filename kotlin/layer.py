"""Unit vs instrumented test-layer policy."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from UnitTest_gen.core.io import log_message
from UnitTest_gen.core.config import PipelineConfig, get_config, normalize_test_mode
from UnitTest_gen.kotlin.project import (
    derive_instrumented_test_output_path,
    derive_test_output_path,
    is_instrumented_test_path,
)

def _plan_types():
    from UnitTest_gen.kotlin.plan import CoveragePlan, PlanGapItem
    return CoveragePlan, PlanGapItem

# Categories that usually need a real device / full framework (not Robolectric-only).
INSTRUMENTED_CATEGORIES = frozenset({
    "android_foreground_service",
    "android_work_manager",
    "needs_real_system_service",
    "real_notification_channel",
    "bluetooth_hardware",
    "camera_hardware",
})

# Categories where Robolectric/JVM unit tests are the default.
UNIT_CATEGORIES = frozenset({
    "plain_jvm",
    "kotlin_object_seam",
    "apollo",
    "room_dao",
    "coroutines_flow",
    "viewmodel_flow_livedata",
})

# Per-item tag hints that force instrumented regardless of file-level friction.
_ITEM_INSTRUMENTED_TAGS = frozenset({
    "android_foreground_service",
    "android_work_manager",
    "bluetooth_hardware",
    "camera_hardware",
    "real_notification_channel",
    "needs_real_system_service",
})

_UNIT_FRICTION_WEIGHTS: dict[str, int] = {
    "hilt_fragment": 3,
    "hilt_entrypoint": 1,
    "hilt_activity": 2,
    "hilt_android_activity": 2,
    "carui_toolbar": 2,
    "carui_progress": 2,
    "android_navigation": 2,
    "nav_deeplink": 2,
    "android_dialog": 2,
    "alert_dialog_helper": 1,
    "delegated_viewmodel_fragment": 2,
    "delegated_viewmodel_drive": 2,
    "firebase": 1,
    "static_api": 1,
    "sdk_static_seam": 1,
    "play_services": 2,
    "static_singleton_get_instance": 1,
}

class TestLayer(str, Enum):
    UNIT = "unit"
    INSTRUMENTED = "instrumented"

    @classmethod
    def normalize(cls, value: str | None) -> "TestLayer":
        raw = (value or cls.UNIT.value).strip().lower()
        if raw in {cls.INSTRUMENTED.value, "android", "androidtest", "connected"}:
            return cls.INSTRUMENTED
        return cls.UNIT

@dataclass(frozen=True)
class LayerDecision:
    layer: TestLayer
    unit_friction: int
    instrumented_friction: int
    reason: str

def _score_unit_friction(categories: frozenset[str] | set[str]) -> int:
    tags = frozenset(categories or ())
    score = sum(_UNIT_FRICTION_WEIGHTS.get(tag, 0) for tag in tags)
    if tags & {"android_fragment", "android_ui"} and "hilt_fragment" not in tags and "hilt_entrypoint" not in tags:
        score += 1
    return score

def _score_instrumented_friction(categories: frozenset[str] | set[str]) -> int:
    tags = frozenset(categories or ())
    score = 1
    if tags <= UNIT_CATEGORIES or (tags & UNIT_CATEGORIES and not tags & {"android_fragment", "android_activity", "hilt_entrypoint"}):
        if "plain_jvm" in tags or tags <= {"viewmodel", "coroutines_flow", "apollo", "room", "room_dao"}:
            score += 8
    if not tags & {"android_fragment", "android_activity", "android_ui", "hilt_entrypoint", "worker_service"}:
        if tags & UNIT_CATEGORIES:
            score += 4
    return score

def resolve_exclusive_layer(
    categories: frozenset[str] | set[str] | None,
    *,
    config: PipelineConfig | None = None,
    source_file_path: str | None = None,
) -> LayerDecision:
    """Pick a single exclusive test layer for a source using hard rules then friction."""
    cfg = config if config is not None else get_config()
    mode = normalize_test_mode(cfg.test_mode)
    tags = frozenset(categories or ())

    if mode == "unit":
        return LayerDecision(TestLayer.UNIT, 0, 0, "forced by test_mode=unit")
    if mode == "instrumented":
        return LayerDecision(TestLayer.INSTRUMENTED, 0, 0, "forced by test_mode=instrumented")

    if tags & INSTRUMENTED_CATEGORIES:
        return LayerDecision(
            TestLayer.INSTRUMENTED, _score_unit_friction(tags), _score_instrumented_friction(tags),
            f"hard rule: instrumented categories {sorted(tags & INSTRUMENTED_CATEGORIES)}",
        )

    unit_only = tags & UNIT_CATEGORIES
    has_android_ui = bool(tags & {"android_fragment", "android_activity", "android_ui", "hilt_entrypoint"})
    if unit_only and not has_android_ui:
        return LayerDecision(
            TestLayer.UNIT, _score_unit_friction(tags), _score_instrumented_friction(tags),
            f"hard rule: unit-only categories {sorted(unit_only)}",
        )

    unit_f = _score_unit_friction(tags)
    instr_f = _score_instrumented_friction(tags)
    if instr_f < unit_f:
        reason = f"instrumented friction {instr_f} < unit friction {unit_f}"
        layer = TestLayer.INSTRUMENTED
    else:
        reason = f"unit friction {unit_f} <= instrumented friction {instr_f}"
        layer = TestLayer.UNIT
    return LayerDecision(layer, unit_f, instr_f, reason)

def recommend_test_layer(categories: frozenset[str] | set[str] | None) -> TestLayer:
    """Heuristic: pick instrumented when source tags need real Android runtime."""
    return resolve_exclusive_layer(categories).layer

def _item_layer(
    item: PlanGapItem,
    *,
    default: TestLayer,
    categories: frozenset[str] | set[str],
    config: PipelineConfig | None = None,
) -> TestLayer:
    cfg = config if config is not None else get_config()
    mode = normalize_test_mode(cfg.test_mode)
    if mode == "unit":
        return TestLayer.UNIT
    if mode == "instrumented":
        return TestLayer.INSTRUMENTED
    existing = (item.test_layer or "").strip().lower()
    if existing in {TestLayer.UNIT.value, TestLayer.INSTRUMENTED.value}:
        return TestLayer.normalize(existing)
    tag = (item.tag or item.category or "").strip().lower()
    if tag in _ITEM_INSTRUMENTED_TAGS:
        return TestLayer.INSTRUMENTED
    return default

def assign_test_layers(
    plan: CoveragePlan,
    *,
    categories: frozenset[str] | set[str] | None = None,
    config: PipelineConfig | None = None,
    source_file_path: str | None = None,
) -> CoveragePlan:
    """Assign test_layer to each testable item; Python-owned, exclusive per item."""
    CoveragePlan, PlanGapItem = _plan_types()
    decision = resolve_exclusive_layer(
        categories, config=config, source_file_path=source_file_path,
    )
    default_layer = decision.layer

    assigned: list[PlanGapItem] = []
    demoted: list[PlanGapItem] = list(plan.not_testable)
    for item in plan.testable:
        layer = _item_layer(item, default=default_layer, categories=frozenset(categories or ()), config=config)
        layer_value = layer.value
        updated = PlanGapItem(
            lines=item.lines,
            methods=item.methods,
            approach=item.approach,
            fixture_hint=item.fixture_hint,
            risks=item.risks,
            reason=item.reason,
            category=item.category,
            tag=item.tag,
            tc_ids=item.tc_ids,
            branch_targets=item.branch_targets,
            test_layer=layer_value,
        )
        assigned.append(updated)
        log_message(
            f"Assigned test_layer={layer_value} lines={list(item.lines)} "
            f"(unit_friction={decision.unit_friction}, instr_friction={decision.instrumented_friction}, "
            f"reason={decision.reason})",
            category="info",
        )

    raw = dict(plan.raw or {})
    raw["primary_test_layer"] = plan_primary_layer(
        CoveragePlan(testable=tuple(assigned), not_testable=tuple(demoted))
    ).value
    return CoveragePlan(
        testable=tuple(assigned),
        not_testable=tuple(demoted),
        raw=raw,
        parsed=plan.parsed,
        fallback_reason=plan.fallback_reason,
        markdown_path=plan.markdown_path,
        markdown_body=plan.markdown_body,
    )

def filter_plan_by_layer(plan: CoveragePlan, layer: TestLayer) -> CoveragePlan:
    """Return a plan copy containing only testable items for the given layer."""
    CoveragePlan, PlanGapItem = _plan_types()
    want = layer.value
    filtered = tuple(item for item in plan.testable if (item.test_layer or TestLayer.UNIT.value) == want)
    return CoveragePlan(
        testable=filtered,
        not_testable=plan.not_testable,
        raw=plan.raw,
        parsed=plan.parsed,
        fallback_reason=plan.fallback_reason,
        markdown_path=plan.markdown_path,
        markdown_body=plan.markdown_body,
    )

def plan_primary_layer(plan: CoveragePlan) -> TestLayer:
    """Majority layer among testable items (tie → unit)."""
    if not plan.testable:
        return TestLayer.UNIT
    counts = {TestLayer.UNIT.value: 0, TestLayer.INSTRUMENTED.value: 0}
    for item in plan.testable:
        key = (item.test_layer or TestLayer.UNIT.value).strip().lower()
        counts[key] = counts.get(key, 0) + 1
    if counts.get(TestLayer.INSTRUMENTED.value, 0) > counts.get(TestLayer.UNIT.value, 0):
        return TestLayer.INSTRUMENTED
    return TestLayer.UNIT

def test_output_path(
    *,
    source_file_path: str,
    source_root: str,
    layer: TestLayer,
    output_base_directory: str | None = None,
) -> str:
    if layer == TestLayer.INSTRUMENTED:
        return derive_instrumented_test_output_path(
            source_file_path=source_file_path,
            source_root=source_root,
            output_base_directory=output_base_directory,
        )
    return derive_test_output_path(
        source_file_path=source_file_path,
        source_root=source_root,
        output_base_directory=output_base_directory,
    )

def layer_for_test_path(test_path: str) -> TestLayer:
    return TestLayer.INSTRUMENTED if is_instrumented_test_path(test_path) else TestLayer.UNIT

def test_layer_guidance() -> str:
    return (
        "TEST LAYER RULES (exclusive — one layer per plan item)\n"
        "- unit (src/test): JVM/Robolectric — ViewModels, repositories, API clients, Room DAOs, "
        "plain logic, Apollo fakes.\n"
        "- instrumented (src/androidTest): real device/emulator — FGS, WorkManager, hardware, "
        "Hilt @HiltAndroidTest + ActivityScenario when unit friction is high.\n"
        "- Python assigns test_layer per item after plan clamp; never duplicate the same lines "
        "across both layers.\n"
        "- Use ONLY the harness and recipes for the assigned test_layer."
    )

def layer_policy_block(decision: LayerDecision) -> str:
    return (
        "LAYER POLICY (exclusive)\n"
        f"- RESOLVED PRIMARY LAYER: {decision.layer.value}\n"
        f"- unit_friction={decision.unit_friction}, instrumented_friction={decision.instrumented_friction}\n"
        f"- reason: {decision.reason}\n"
        "- Plan scenarios only; Python assigns test_layer per item after clamp.\n"
        "- Never plan the same delta lines for both unit and instrumented."
    )
