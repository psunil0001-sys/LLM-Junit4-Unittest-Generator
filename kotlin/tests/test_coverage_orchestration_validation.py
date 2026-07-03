# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Tests coverage orchestration validation and incremental acceptance.
"""Tests for incremental coverage acceptance and orchestration validators."""

from __future__ import annotations

import unittest

from UnitTest_gen.kotlin.incremental_coverage import (
    CoverageOpportunity,
    coverage_candidate_acceptance_decision,
    coverage_candidate_intent_issues,
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


if __name__ == "__main__":
    unittest.main()
