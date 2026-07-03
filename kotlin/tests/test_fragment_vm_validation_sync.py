# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Fragment/ViewModel guardrail sync tests.
"""Sync tests for fragment/ViewModel validators, orchestration, and catalog."""

from __future__ import annotations

import unittest

from UnitTest_gen.kotlin.guardrail_catalog import VALIDATION_REPAIR_INTENTS, enrich_validation_error_block
from UnitTest_gen.kotlin.incremental_coverage import (
    CoverageOpportunity,
    _infer_callback_trigger_from_path,
    collect_incremental_orchestration_issues,
    coverage_candidate_intent_issues,
)
from UnitTest_gen.kotlin.prompts import FIXTURE_PLAYBOOK_BY_ID
from UnitTest_gen.kotlin.test_code_utils.validate import validate_generated_test_code
from UnitTest_gen.kotlin.validation_rules.fragment_viewmodel_quality import (
    collect_fragment_viewmodel_quality_issues,
)
from UnitTest_gen.kotlin.validation_rules.fragment import collect_validation_issues


_FRAGMENT_VM_SOURCE = """
@AndroidEntryPoint
class LoginFragment : Fragment() {
    private val viewModel: LoginViewModel by viewModels()
    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        viewModel.uiState.collect { }
    }
    private fun setupToolbar() {
        CarUi.requireToolbar(requireActivity()).setMenuItems(listOf(menuItem))
    }
}
"""

_MENU_PLAN = {
    "selected_safe": [
        CoverageOpportunity(
            name="logout_menu",
            bucket="safe",
            fixture="verified_menu_callback",
            entry_points=["onViewCreated"],
            lines=[176],
            branches=[],
            reason="menu",
            action="capture menu listener",
        )
    ],
    "selected_attemptable": [],
}


class FragmentViewModelValidationSyncTest(unittest.TestCase):
    def test_all_executable_planner_fixtures_have_prompt_playbooks(self):
        fixtures = {
            "attached_hilt_fragment",
            "branch_probe",
            "controlled_countdown_callback",
            "controlled_exception_path",
            "public_method",
            "robolectric_delayed_handler",
            "verified_activity_result_callback",
            "verified_callback",
            "verified_coroutine_completion",
            "verified_dialog_callback",
            "verified_menu_callback",
            "verified_observer_and_click",
            "verified_observer_emission",
            "verified_stream_emission",
            "verified_ui_click",
            "viewmodel_public_method",
            "viewmodel_sync_public",
        }
        self.assertFalse(fixtures - FIXTURE_PLAYBOOK_BY_ID.keys())

    def test_unused_lateinit_fragment_field_does_not_block_local_fixture(self):
        test_code = """
class SampleFragmentTest {
    private lateinit var fragment: SampleFragment
    @Test fun coversLifecycle() {
        val target = SampleFragment()
        host.supportFragmentManager.beginTransaction().add(target, null).commitNow()
    }
}
"""
        issues = collect_validation_issues(
            test_code,
            "/tmp/SampleFragmentTest.kt",
            "class SampleFragment : Fragment()",
            None,
        )
        self.assertFalse(any("lateinit fragment fixture" in issue for issue in issues))

    def test_rejects_stateflow_reflection(self):
        test_code = """
package com.example
class LoginFragmentTest {
    @Test fun `emits`() {
        val field = LoginViewModel::class.java.getDeclaredField("_uiState")
        field.isAccessible = true
    }
}
"""
        issues = collect_fragment_viewmodel_quality_issues(
            test_code, "/tmp/LoginFragmentTest.kt", _FRAGMENT_VM_SOURCE, test_report=None
        )
        self.assertTrue(any("invalid_stateflow_reflection" in issue for issue in issues))

    def test_rejects_delegated_viewmodel_provider(self):
        test_code = """
package com.example
class LoginFragmentTest {
    @Test fun `vm`() {
        val vm = ViewModelProvider(activity).get(LoginViewModel::class.java)
    }
}
"""
        issues = collect_fragment_viewmodel_quality_issues(
            test_code, "/tmp/LoginFragmentTest.kt", _FRAGMENT_VM_SOURCE, test_report=None
        )
        self.assertTrue(any("invalid_delegated_viewmodel_provider" in issue for issue in issues))

    def test_menu_fixture_requires_trigger(self):
        test_code = "package com.example\nclass T { @Test fun x() { verify(toolbar).menuItems } }"
        issues = coverage_candidate_intent_issues(test_code, _MENU_PLAN)
        self.assertTrue(any("missing_coverage_trigger_menu" in issue for issue in issues))

    def test_duplicate_incremental_candidate(self):
        existing = "package com.example\nclass T { @Test fun x() { fragment.onStop() } }"
        supplemental = "package com.example\nclass T { @Test fun x() { fragment.onStop() } }"
        issues = collect_incremental_orchestration_issues(supplemental, _MENU_PLAN, existing_test_code=existing)
        self.assertTrue(any("duplicate_incremental_candidate" in issue for issue in issues))

    def test_infer_menu_callback_before_click(self):
        result = _infer_callback_trigger_from_path(
            {"coverage_path": ["onViewCreated", "setupToolbar", "callback"]},
            "toolbar.setMenuItems(items)",
            "class F { fun setupToolbar() { toolbar.setMenuItems(listOf(item)) } }",
        )
        self.assertIsNotNone(result)
        self.assertEqual(result[1], "verified_menu_callback")

    def test_validate_with_opportunity_plan_includes_intent_issues(self):
        test_code = "package com.example\nclass T { @Test fun x() { verify(toolbar) } }"
        issues = validate_generated_test_code(
            test_code,
            "/tmp/ClaimOwnershipFragmentTest.kt",
            source_code=_FRAGMENT_VM_SOURCE,
            opportunity_plan=_MENU_PLAN,
        )
        self.assertTrue(any("missing_coverage_trigger_menu" in issue for issue in issues))

    def test_enrich_validation_error_block_adds_repair_bullets(self):
        block = enrich_validation_error_block(
            ["missing_coverage_trigger_menu: Capture the exact typed menu listener."]
        )
        self.assertIn("Repair bullets:", block)
        self.assertIn(VALIDATION_REPAIR_INTENTS["missing_coverage_trigger_menu"], block)


if __name__ == "__main__":
    unittest.main()
