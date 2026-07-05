# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Tests coverage orchestration validation and incremental acceptance.
"""Tests for incremental coverage acceptance and orchestration validators."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from UnitTest_gen.kotlin.incremental_coverage import (
    CoverageOpportunity,
    _apply_exact_patches,
    _infer_callback_trigger_from_path,
    _test_functions_named,
    _validation_issue_fingerprint,
    coverage_candidate_acceptance_decision,
    coverage_candidate_intent_issues,
    run_gradle_and_parse_gap,
)
from UnitTest_gen.kotlin.test_code_utils.validate import validate_generated_test_code
from UnitTest_gen.kotlin.validation_rules.coverage_orchestration import (
    collect_coverage_orchestration_validation_issues,
)


class _GapStub:
    def __init__(
        self,
        *,
        missed_lines,
        partial_branch_lines=None,
        missed_methods=None,
        partial_branch_methods=None,
        line_coverage=(0, 0),
        branch_coverage=(0, 0),
    ):
        self.missed_lines = missed_lines
        self.partial_branch_lines = partial_branch_lines or []
        self.missed_methods = missed_methods or []
        self.partial_branch_methods = partial_branch_methods or []
        self.line_coverage = line_coverage
        self.branch_coverage = branch_coverage


_FRAGMENT_SOURCE = """
class ClaimOwnershipFragment : Fragment() {
    private fun showLogoutDialog() {
        AlertDialogHelper.getInstance().init(alertBoxMessage)
        AlertDialogHelper.getInstance().showAlert(requireContext(), object : AlertDialogHelper.Callback {
            override fun onPositiveAction() { onUserAction() }
        })
    }
    override fun onStop() { super.onStop() }
}
"""


class CoverageOrchestrationValidationTest(unittest.TestCase):
    def test_validation_retry_fingerprint_uses_issue_category(self):
        first = _validation_issue_fingerprint(["invalid_resource: first detail"])
        second = _validation_issue_fingerprint(["invalid_resource: changed detail"])
        different = _validation_issue_fingerprint(["invalid_hilt_setup: detail"])
        self.assertEqual(first, second)
        self.assertNotEqual(first, different)

    def test_exact_patch_repair_applies_only_matching_patch(self):
        code, count = _apply_exact_patches(
            "findViewById(R.id.tvDeleteAll)",
            [
                {"old_text": "R.id.tvDeleteAll", "new_text": "R.id.tv_delete_all"},
                {"old_text": "missing", "new_text": "ignored"},
            ],
        )
        self.assertEqual("findViewById(R.id.tv_delete_all)", code)
        self.assertEqual(1, count)

    def test_menu_registration_on_parent_path_is_not_classified_as_view_click(self):
        source = """
class SampleFragment {
    fun onViewCreated() { setupToolbar() }
    private fun setupToolbar() {
        toolbar.setMenuItems(listOf(MenuItem.Builder().setOnClickListener { showLogoutDialog() }.build()))
    }
    private fun showLogoutDialog() { showSignOutAlert() }
    private fun showSignOutAlert() = Unit
}
"""
        inferred = _infer_callback_trigger_from_path(
            {"coverage_path": ["onViewCreated", "setupToolbar", "showLogoutDialog", "showSignOutAlert"]},
            "private fun showSignOutAlert() = Unit",
            source,
        )
        self.assertIsNotNone(inferred)
        self.assertEqual("verified_menu_callback", inferred[1])
        self.assertIn("performClick", inferred[3])
    def test_selected_test_name_without_required_trigger_is_rejected(self):
        opportunity = CoverageOpportunity(
            name="logout click",
            bucket="attemptable",
            fixture="verified_ui_click",
            entry_points=["onViewCreated"],
            lines=[42],
            branches=[],
            reason="click branch",
            action="click logout",
            trigger_recipe="performClick on logout",
        )
        repaired = """
class ExampleTest {
    @Test fun selectedLogoutTest() { assertTrue(true) }
    @Test fun unrelatedClickTest() { button.performClick() }
}
"""
        selected_code = _test_functions_named(repaired, {"selectedLogoutTest"})
        issues = coverage_candidate_intent_issues(
            selected_code,
            {"selected_safe": [], "selected_attemptable": [opportunity]},
        )
        self.assertTrue(any("missing_coverage_trigger_click" in issue for issue in issues))
    def test_network_click_requires_explicit_network_setup(self):
        opportunity = CoverageOpportunity(
            name="sign in click",
            bucket="attemptable",
            fixture="verified_ui_click",
            entry_points=["onViewCreated"],
            lines=[131],
            branches=[],
            reason="network branch",
            action="configure network then click",
            trigger_recipe="Configure Robolectric network state, then performClick().",
        )
        plan = {"selected_safe": [], "selected_attemptable": [opportunity]}

        issues = coverage_candidate_intent_issues("button.performClick()", plan)
        self.assertTrue(any("missing_coverage_trigger_network" in issue for issue in issues))

        issues = coverage_candidate_intent_issues(
            "shadow.setDefaultNetworkActive(false); button.performClick()", plan
        )
        self.assertFalse(any("missing_coverage_trigger_network" in issue for issue in issues))

    def test_rejects_helper_only_dialog_test(self):
        test_code = """
package com.example

import org.junit.Test

class ClaimOwnershipFragmentTest {
    @Test
    fun `logout_positive_clears_auth`() {
        AlertDialogHelper.getInstance().init(AlertBoxMessage.SIGN_OUT)
        AlertDialogHelper.getInstance().showAlert(context, object : AlertDialogHelper.Callback {
            override fun onPositiveAction() {}
        })
    }
}
"""
        issues = collect_coverage_orchestration_validation_issues(
            test_code,
            "/tmp/ClaimOwnershipFragmentTest.kt",
            _FRAGMENT_SOURCE,
            test_report=None,
        )
        self.assertTrue(any("missing_coverage_trigger_dialog" in issue for issue in issues))

    def test_allows_target_entry_dialog_test(self):
        test_code = """
package com.example

import org.junit.Test

class ClaimOwnershipFragmentTest {
    @Test
    fun `toolbar_logout_triggers_dialog`() {
        val fragment = ClaimOwnershipFragment()
        fragment.requireView().findViewById<View>(R.id.menu_logout).performClick()
    }
}
"""
        issues = collect_coverage_orchestration_validation_issues(
            test_code,
            "/tmp/ClaimOwnershipFragmentTest.kt",
            _FRAGMENT_SOURCE,
            test_report=None,
        )
        self.assertFalse(any("missing_coverage_trigger_dialog" in issue for issue in issues))

    def test_rejects_started_state_after_stop(self):
        test_code = """
package com.example

import androidx.lifecycle.Lifecycle
import org.junit.Assert.assertEquals
import org.junit.Test

class ClaimOwnershipFragmentTest {
    @Test
    fun `onStop_leaves_started_state`() {
        val fragment = ClaimOwnershipFragment()
        activityController.stop()
        assertEquals(Lifecycle.State.STARTED, fragment.lifecycle.currentState)
    }
}
"""
        issues = collect_coverage_orchestration_validation_issues(
            test_code,
            "/tmp/ClaimOwnershipFragmentTest.kt",
            _FRAGMENT_SOURCE,
            test_report=None,
        )
        self.assertTrue(any("invalid_fragment_lifecycle_state_assertion" in issue for issue in issues))

    def test_validate_generated_test_code_includes_orchestration_issues(self):
        test_code = """
package com.example

import org.junit.Test

class ClaimOwnershipFragmentTest {
    @Test
    fun `helper_only`() {
        AlertDialogHelper.getInstance().init(AlertBoxMessage.SIGN_OUT)
    }
}
"""
        issues = validate_generated_test_code(
            test_code,
            "/tmp/ClaimOwnershipFragmentTest.kt",
            source_code=_FRAGMENT_SOURCE,
        )
        self.assertTrue(any("missing_coverage_trigger_dialog" in issue for issue in issues))


class IncrementalAcceptanceDecisionTest(unittest.TestCase):
    def test_rejects_unselected_line_progress_only(self):
        before = _GapStub(missed_lines=[157, 158, 176, 177], line_coverage=(4, 10))
        after = _GapStub(missed_lines=[176, 177], line_coverage=(2, 12))
        plan = {
            "selected_safe": [
                CoverageOpportunity(
                    name="logout_dialog",
                    bucket="safe",
                    fixture="hilt_fragment",
                    entry_points=["showLogoutDialog"],
                    lines=[176, 177, 180, 187],
                    branches=[],
                    reason="dialog callback",
                    action="ui_click",
                )
            ],
            "selected_attemptable": [],
        }
        decision = coverage_candidate_acceptance_decision(plan, before, after, _FRAGMENT_SOURCE)
        self.assertFalse(decision.accepted)
        self.assertEqual("no Kover improvement", decision.reason)
        self.assertEqual([157, 158], decision.resolved_unselected_lines)


class GradleEvidenceReuseTest(unittest.IsolatedAsyncioTestCase):
    async def test_reuses_fresh_repair_gradle_output(self):
        output = (
            "> Task :feature:testProdUnitTest\n"
            "> Task :feature:koverXmlReportProd\n"
            "BUILD SUCCESSFUL\nEXIT_CODE: 0"
        )
        gap = object()
        with (
            patch(
                "UnitTest_gen.kotlin.incremental_coverage.run_gradle_with_heartbeat",
                new=AsyncMock(),
            ) as run_gradle,
            patch(
                "UnitTest_gen.kotlin.incremental_coverage.parse_latest_coverage_gap",
                new=AsyncMock(return_value=gap),
            ),
        ):
            passed, parsed_gap, returned_output = await run_gradle_and_parse_gap(
                mcp_tools=None,
                project_root="/project",
                gradle_offline=False,
                gradle_tasks=[":feature:koverXmlReportProd"],
                module_dir="/project/feature",
                source_file_path="/project/feature/Sample.kt",
                source_code="class Sample",
                log_title="acceptance",
                verified_gradle_output=output,
            )

        run_gradle.assert_not_awaited()
        self.assertTrue(passed)
        self.assertIs(gap, parsed_gap)
        self.assertEqual(output, returned_output)


if __name__ == "__main__":
    unittest.main()
