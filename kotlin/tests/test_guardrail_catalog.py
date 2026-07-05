# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Tests generated-test guardrail catalog behavior.
"""Guardrail catalog alignment with prompts, contracts, and validators."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from UnitTest_gen.kotlin import prompts
from UnitTest_gen.kotlin.guardrail_catalog import (
    GUARDRAIL_RULES,
    ORCHESTRATION_ONLY_CODES,
    VALIDATION_REPAIR_INTENTS,
    render_guardrails,
)
from UnitTest_gen.kotlin.prompting.rules import build_generation_rule_tail
from UnitTest_gen.kotlin.strategy_contracts import (
    COVERAGE_BLOCKED_PATH_CONTRACT,
    validation_repair_intent,
)
from UnitTest_gen.kotlin.validation_rules.misc import (
    collect_carui_validation_issues,
    collect_robolectric_sdk_validation_issues,
)


class TestGuardrailCatalog(unittest.TestCase):
    def test_repair_intents_cover_catalog_validator_codes(self):
        catalog_codes: set[str] = set()
        for rule in GUARDRAIL_RULES:
            catalog_codes.update(rule.validator_codes)
        for code in sorted(catalog_codes):
            self.assertIn(code, VALIDATION_REPAIR_INTENTS, msg=code)

    def test_recent_validator_and_orchestration_codes_have_specific_repairs(self):
        for code in (
            "invalid_fragment_lifecycle_reentry",
            "invalid_fragment_arguments_after_attach",
            "invalid_project_interface_call",
            "invalid_suspend_call_context",
            "missing_coverage_trigger_network",
        ):
            self.assertIn(code, VALIDATION_REPAIR_INTENTS, msg=code)

    def test_generation_tail_includes_canonical_guardrails(self):
        text = build_generation_rule_tail(
            is_fragment=True,
            is_hilt=True,
            is_viewmodel=False,
            uses_framework_blueprints=True,
            is_apollo_mapper=False,
            source_code="@AndroidEntryPoint class SampleFragment : Fragment()",
            source_categories={"android_fragment", "hilt"},
        )
        self.assertIn("MANDATORY GENERATED-TEST GUARDRAILS", text)
        self.assertIn("commitNow()/FragmentManager attachment already invokes Fragment lifecycle", text)
        self.assertNotIn("AppAuth refreshToken", text)
        self.assertLess(len(text), 12_000)

    def test_strategy_contract_uses_same_repair_intent(self):
        self.assertEqual(
            validation_repair_intent("invalid_host_strategy"),
            VALIDATION_REPAIR_INTENTS["invalid_host_strategy"],
        )

    def test_render_guardrails_deduplicates_within_section(self):
        text = render_guardrails("kotlin_android")
        lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
        self.assertEqual(len(lines), len(set(lines)))

    def test_blocked_policy_consistent_across_contract_and_incremental(self):
        blocked_text = " ".join(COVERAGE_BLOCKED_PATH_CONTRACT.prompt_rules).lower()
        self.assertIn("no-op or method-entry", blocked_text)
        self.assertNotIn("never generate tests", blocked_text)
        incremental = prompts.INCREMENTAL_COVERAGE_RULES.lower()
        self.assertIn("do not generate tests for blocked opportunities", incremental)

    def test_no_validator_tags_in_playbooks(self):
        playbook_blob = "\n".join(
            value
            for name, value in vars(prompts).items()
            if name.startswith("FIXTURE_PLAYBOOK_") and isinstance(value, str)
        )
        self.assertNotIn("Validator:", playbook_blob)

    def test_navigation_decision_tree_not_ambiguous(self):
        blob = prompts.ANDROID_BLUEPRINTS + prompts.INCREMENTAL_COVERAGE_RULES
        self.assertNotIn("TestNavHostController or mocked", blob)
        self.assertIn("mocked NavController", blob)
        self.assertIn("TestNavHostController", blob)

    def test_orchestration_only_codes_documented(self):
        self.assertGreaterEqual(len(ORCHESTRATION_ONLY_CODES), 8)

    def test_active_network_null_fixture_is_rejected(self):
        issues = collect_robolectric_sdk_validation_issues(
            "shadowOf(connectivityManager).setActiveNetworkInfo(null)",
            "SampleTest.kt",
            "connectivityManager.getNetworkCapabilities(connectivityManager.activeNetwork)",
            SimpleNamespace(tests=SimpleNamespace(config_sdks=())),
        )
        self.assertTrue(any("setDefaultNetworkActive(false)" in issue for issue in issues))

    def test_current_robolectric_shadow_dialog_import_is_allowed(self):
        issues = collect_robolectric_sdk_validation_issues(
            "import org.robolectric.shadows.ShadowDialog\nval dialog = ShadowDialog.getLatestDialog()",
            "SampleTest.kt",
            "",
            SimpleNamespace(tests=SimpleNamespace(config_sdks=())),
        )
        self.assertFalse(any("deprecated_robolectric_api" in issue for issue in issues))

    def test_carui_menu_item_onclick_property_is_rejected(self):
        issues = collect_carui_validation_issues(
            "val item: MenuItem = mock(); item.onClick?.invoke()",
            "SampleTest.kt",
            "toolbar.setMenuItems(listOf(MenuItem.Builder(context).build()))",
            SimpleNamespace(),
        )
        self.assertTrue(any("invalid_carui_menu_callback_api" in issue for issue in issues))


if __name__ == "__main__":
    unittest.main()
