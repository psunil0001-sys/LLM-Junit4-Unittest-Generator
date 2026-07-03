# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Smoke tests for incremental opportunity selection ordering.
"""Tests for largest-first safe/attemptable fixture selection."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from UnitTest_gen.kotlin.coverage_analysis import CoverageGap
from UnitTest_gen.kotlin.attempted_failed_report import (
    DISPOSITION_PIPELINE_UNRESOLVED,
    load_blocked_report,
    record_kover_rejected_attempt,
)
from UnitTest_gen.kotlin.incremental_coverage import (
    CoverageOpportunity,
    _select_largest_fixture_opportunities,
    build_coverage_opportunity_plan,
)
from UnitTest_gen.kotlin.kotlin_analysis import classify_source
from UnitTest_gen.kotlin.strategy_contracts import select_strategy_contracts


def _opp(
    name: str,
    *,
    fixture: str,
    lines: list[int],
    branches: list[int] | None = None,
    bucket: str = "safe",
) -> CoverageOpportunity:
    return CoverageOpportunity(
        name=name,
        bucket=bucket,
        fixture=fixture,
        entry_points=[name],
        lines=lines,
        branches=branches or [],
        reason="test",
        action="test",
    )


def _weight(item: CoverageOpportunity) -> int:
    return len(set(item.lines)) * 2 + len(set(item.branches))


class IncrementalOpportunitySelectionTest(unittest.TestCase):
    def test_declared_activity_and_application_categories_are_emitted(self):
        activity = classify_source("@AndroidEntryPoint class MainActivity : AppCompatActivity()")
        application = classify_source("@HiltAndroidApp class App : Application()")
        self.assertIn("android_activity", activity.categories)
        self.assertIn("hilt_android_activity", activity.categories)
        self.assertIn("android_application", application.categories)
        self.assertIn("hilt_android_application", application.categories)

    def test_fragment_using_alert_dialog_is_not_a_dialog_fragment(self):
        profile = classify_source(
            "class ScreenFragment : Fragment() { fun show() = AlertDialog.Builder(requireContext()) }"
        )
        self.assertIn("plain_fragment", profile.categories)
        self.assertNotIn("dialog_fragment", profile.categories)

    def test_importing_service_does_not_classify_an_unrelated_class_as_service(self):
        profile = classify_source(
            "import android.app.Service\nclass ServiceClient { fun bind(service: Service) = service }"
        )
        self.assertNotIn("worker_service", profile.categories)

    def test_regular_factory_get_instance_is_not_a_static_singleton_contract(self):
        profile = classify_source(
            'class Hashing { fun hash() = MessageDigest.getInstance("SHA-256") }'
        )
        self.assertNotIn("static_singleton_get_instance", profile.categories)

    def test_declarative_dagger_module_does_not_select_provider_contract(self):
        profile = classify_source(
            "@Module interface Bindings { @Binds fun bind(impl: Impl): Api }"
        )
        contract_ids = {contract.id for contract in select_strategy_contracts(profile.categories)}
        self.assertIn("declarative_dagger_binding", profile.categories)
        self.assertNotIn("dagger_provider_logic", contract_ids)

    def test_fragment_setup_method_is_not_classified_as_viewmodel_sync(self):
        source = """
class SampleFragment : Fragment() {
    override fun onViewCreated(view: View, state: Bundle?) { setupToolbar() }
    private fun setupToolbar() { toolbar.setTitle(R.string.title) }
}
"""
        line = next(i for i, text in enumerate(source.splitlines(), 1) if "setTitle" in text)
        gap = CoverageGap("SampleFragment.kt", "sample", [line], [], [], [], (1, 1), (0, 0), {})
        plan = build_coverage_opportunity_plan(source, gap, classify_source(source).categories)
        self.assertFalse(plan["selected_safe"])
        self.assertEqual("public_method", plan["selected_attemptable"][0].fixture)

    def test_lines_after_hardcoded_dispatcher_are_blocked_but_earlier_lines_remain_safe(self):
        source = """
class SampleViewModel : ViewModel() {
    suspend fun load() {
        publishLoading()
        withContext(Dispatchers.IO) { repository.load() }
        publishDone()
    }
}
"""
        lines = source.splitlines()
        before = next(i for i, text in enumerate(lines, 1) if "publishLoading" in text)
        after = next(i for i, text in enumerate(lines, 1) if "publishDone" in text)
        gap = CoverageGap("SampleViewModel.kt", "sample", [before, after], [], [], [], (2, 1), (0, 0), {})
        plan = build_coverage_opportunity_plan(source, gap, classify_source(source).categories)
        self.assertEqual([before], plan["selected_safe"][0].lines)
        self.assertEqual([after], plan["blocked"][0].lines)
        self.assertEqual("uncontrolled_coroutine_body", plan["blocked"][0].fixture)

    def test_private_callee_dispatcher_blocks_following_private_helper_lines(self):
        source = """
class SampleViewModel : ViewModel() {
    suspend fun load() { update() }
    private suspend fun update() {
        val value = fetch()
        publish(value)
    }
    private suspend fun fetch(): String = withContext(Dispatchers.IO) { repository.load() }
}
"""
        line = next(i for i, text in enumerate(source.splitlines(), 1) if "publish(value)" in text)
        gap = CoverageGap("SampleViewModel.kt", "sample", [line], [], [], [], (1, 1), (0, 0), {})
        plan = build_coverage_opportunity_plan(source, gap, classify_source(source).categories)
        self.assertFalse(plan["selected_safe"])
        self.assertEqual("uncontrolled_coroutine_body", plan["blocked"][0].fixture)

    def test_picks_fixture_with_highest_aggregate_weight(self):
        opportunities = [
            _opp("smallA", fixture="fixture_a", lines=[1, 2]),
            _opp("bigB1", fixture="fixture_b", lines=list(range(1, 11))),
            _opp("bigB2", fixture="fixture_b", lines=list(range(20, 25))),
        ]
        selected, fixture = _select_largest_fixture_opportunities(
            opportunities,
            weight_fn=_weight,
            cap=4,
            line_budget=100,
        )
        self.assertEqual("fixture_b", fixture)
        self.assertEqual({"bigB1", "bigB2"}, {item.name for item in selected})

    def test_tiebreak_aggregate_by_largest_single_opportunity(self):
        opportunities = [
            _opp("midA1", fixture="fixture_a", lines=[1, 2, 3, 4]),
            _opp("midA2", fixture="fixture_a", lines=[5, 6, 7, 8]),
            _opp("peakB", fixture="fixture_b", lines=list(range(1, 9))),
            _opp("tinyB", fixture="fixture_b", lines=[50]),
        ]
        selected, fixture = _select_largest_fixture_opportunities(
            opportunities,
            weight_fn=_weight,
            cap=4,
            line_budget=100,
        )
        self.assertEqual("fixture_b", fixture)
        self.assertEqual("peakB", selected[0].name)

    def test_respects_cap_and_descending_weight_within_fixture(self):
        opportunities = [
            _opp("large", fixture="fixture_a", lines=list(range(1, 21))),
            _opp("medium", fixture="fixture_a", lines=list(range(30, 40))),
            _opp("small", fixture="fixture_a", lines=[100]),
        ]
        selected, fixture = _select_largest_fixture_opportunities(
            opportunities,
            weight_fn=_weight,
            cap=2,
            line_budget=100,
        )
        self.assertEqual("fixture_a", fixture)
        self.assertEqual(["large", "medium"], [item.name for item in selected])

    def test_safe_before_attemptable_via_empty_safe_list_only(self):
        safe = [_opp("safeOne", fixture="fixture_a", lines=[1, 2, 3])]
        attemptable = [_opp("tryOne", fixture="fixture_b", lines=list(range(1, 30)))]
        safe_selected, _ = _select_largest_fixture_opportunities(
            safe,
            weight_fn=_weight,
            cap=2,
            line_budget=80,
        )
        self.assertTrue(safe_selected)
        attemptable_selected = []
        if not safe_selected:
            attemptable_selected, _ = _select_largest_fixture_opportunities(
                attemptable,
                weight_fn=_weight,
                cap=2,
                line_budget=80,
            )
        self.assertEqual(["safeOne"], [item.name for item in safe_selected])
        self.assertEqual([], attemptable_selected)

    def test_fragment_on_each_lines_require_stream_emission(self):
        source = """
class SampleFragment : Fragment() {
    override fun onViewCreated(view: View, state: Bundle?) {
        viewLifecycleOwner.lifecycleScope.launch {
            viewModel.state.flowWithLifecycle(lifecycle).onEach {
                if (it) showProgress(true)
            }.launchIn(viewLifecycleOwner.lifecycleScope)
        }
    }
    private fun showProgress(value: Boolean) = Unit
}
"""
        line = next(i for i, text in enumerate(source.splitlines(), 1) if "showProgress(true)" in text)
        gap = CoverageGap("SampleFragment.kt", "sample", [line], [], [], [], (1, 1), (0, 0), {})
        plan = build_coverage_opportunity_plan(source, gap, {"fragment"})
        selected = plan["selected_attemptable"]
        self.assertEqual("verified_stream_emission", selected[0].fixture)

    def test_private_helper_behind_buildconfig_is_blocked(self):
        source = """
class SampleFragment : Fragment() {
    override fun onViewCreated(view: View, state: Bundle?) {
        if (BuildConfig.ENABLED) helper()
    }
    private fun helper() {
        consume("target")
    }
}
"""
        line = next(i for i, text in enumerate(source.splitlines(), 1) if 'consume("target")' in text)
        gap = CoverageGap("SampleFragment.kt", "sample", [line], [], [], [], (1, 1), (0, 0), {})
        plan = build_coverage_opportunity_plan(source, gap, {"fragment"})
        self.assertEqual("fixed_build_variant", plan["blocked"][0].fixture)

    def test_private_null_activity_branch_is_not_safe(self):
        source = """
class SampleFragment : Fragment() {
    override fun onViewCreated(view: View, state: Bundle?) { setup() }
    private fun setup() {
        (activity as? Activity)?.let { use(it) }
    }
}
"""
        line = next(i for i, text in enumerate(source.splitlines(), 1) if "activity as? Activity" in text)
        gap = CoverageGap("SampleFragment.kt", "sample", [], [line], [], [], (0, 1), (1, 1), {})
        plan = build_coverage_opportunity_plan(source, gap, {"fragment"})
        self.assertEqual("private_lifecycle_null_branch", plan["blocked"][0].fixture)

    def test_hilt_fragment_lifecycle_uses_attached_fixture(self):
        source = """
@AndroidEntryPoint
class SampleFragment : Fragment() {
    override fun onViewCreated(view: View, state: Bundle?) {
        renderState()
    }
    private fun renderState() = Unit
}
"""
        line = next(i for i, text in enumerate(source.splitlines(), 1) if "renderState()" in text)
        gap = CoverageGap("SampleFragment.kt", "sample", [line], [], [], [], (1, 1), (0, 0), {})
        plan = build_coverage_opportunity_plan(source, gap, {"android_fragment", "hilt_fragment"})
        self.assertFalse(plan["selected_safe"])
        self.assertEqual("attached_hilt_fragment", plan["selected_attemptable"][0].fixture)

    def test_lifecycle_framework_safe_call_null_arm_is_blocked(self):
        source = """
class SampleFragment : Fragment() {
    override fun onStart() {
        super.onStart()
        context?.registerReceiver(receiver, filter)
    }
}
"""
        line = next(i for i, text in enumerate(source.splitlines(), 1) if "context?." in text)
        gap = CoverageGap("SampleFragment.kt", "sample", [], [line], [], [], (0, 1), (1, 1), {})
        plan = build_coverage_opportunity_plan(source, gap, {"android_fragment"})
        self.assertFalse(plan["selected_safe"])
        self.assertFalse(plan["selected_attemptable"])
        self.assertEqual("lifecycle_nullable_framework_branch", plan["blocked"][0].fixture)

    def test_appauth_refresh_success_path_is_attemptable_not_safe(self):
        source = """
class AuthRepositoryImpl {
    suspend fun refreshToken() {
        val request = authState.createTokenRefreshRequest()
        authSource.token(request.configuration.tokenEndpoint.toString(), emptyMap(), request.requestParameters)
    }
}
"""
        line = next(i for i, text in enumerate(source.splitlines(), 1) if "authSource.token" in text)
        gap = CoverageGap("AuthRepositoryImpl.kt", "sample", [line], [], [], [], (1, 1), (0, 0), {})
        plan = build_coverage_opportunity_plan(source, gap, {"storage_json_auth"})
        self.assertFalse(plan["selected_safe"])
        self.assertIn("AppAuth", plan["selected_attemptable"][0].reason)

    def test_countdown_final_state_is_attemptable_callback(self):
        source = """
class SampleViewModel : ViewModel() {
    fun setBusy(value: Boolean) {
        if (value) startCountdown(onFinish = { setBusy(false) })
    }
}
"""
        line = next(i for i, text in enumerate(source.splitlines(), 1) if "startCountdown" in text)
        gap = CoverageGap("SampleViewModel.kt", "sample", [line], [], [], [], (1, 1), (0, 0), {})
        plan = build_coverage_opportunity_plan(source, gap, {"viewmodel"})
        self.assertFalse(plan["selected_safe"])
        self.assertEqual("controlled_countdown_callback", plan["selected_attemptable"][0].fixture)

    def test_fixed_local_nonempty_input_branch_is_blocked(self):
        source = """
class SampleViewModel : ViewModel() {
    fun createData() {
        val item = Device(deviceId = "fixed")
        if (item.deviceId.isNotEmpty()) publish(item) else publishEmpty()
    }
}
"""
        line = next(i for i, text in enumerate(source.splitlines(), 1) if "isNotEmpty" in text)
        gap = CoverageGap("SampleViewModel.kt", "sample", [], [line], [], [], (0, 1), (1, 1), {})
        plan = build_coverage_opportunity_plan(source, gap, {"viewmodel"})
        self.assertFalse(plan["selected_safe"])
        self.assertEqual("fixed_internal_branch_input", plan["blocked"][0].fixture)

    def test_buildconfig_alias_branch_is_blocked(self):
        source = """
class SampleApp : Application() {
    val enabled = BuildConfig.FEATURE_ENABLED
    override fun onCreate() {
        if (enabled) initializeSdk()
    }
}
"""
        line = next(i for i, text in enumerate(source.splitlines(), 1) if "if (enabled)" in text)
        gap = CoverageGap("SampleApp.kt", "sample", [], [line], [], [], (0, 1), (1, 1), {})
        plan = build_coverage_opportunity_plan(source, gap, {"android_application"})
        self.assertFalse(plan["selected_safe"])
        self.assertEqual("fixed_build_variant", plan["blocked"][0].fixture)

    def test_internally_constructed_callback_owner_is_blocked(self):
        source = """
class SampleViewModel : ViewModel() {
    fun connect(context: Context) {
        viewModelScope.launch {
            val manager = ProfilesApiManager(context, ProfilesApiCallback { api ->
                consume(api)
            })
            manager.connect()
        }
    }
}
"""
        line = next(i for i, text in enumerate(source.splitlines(), 1) if "consume(api)" in text)
        gap = CoverageGap("SampleViewModel.kt", "sample", [line], [], [], [], (1, 1), (0, 0), {})
        plan = build_coverage_opportunity_plan(source, gap, {"viewmodel"})
        self.assertFalse(plan["selected_attemptable"])
        self.assertEqual("internally_constructed_callback_owner", plan["blocked"][0].fixture)

    def test_hardcoded_dispatcher_catch_does_not_become_attemptable_or_absorb_tail(self):
        source = """
class SampleViewModel : ViewModel() {
    fun load(value: String) {
        viewModelScope.launch(Dispatchers.IO) {
            try {
                value.toInt()
            } catch (error: NumberFormatException) {
                recordInvalid()
            }
            publishDone()
        }
    }
}
"""
        lines = source.splitlines()
        catch_line = next(i for i, text in enumerate(lines, 1) if "recordInvalid" in text)
        tail_line = next(i for i, text in enumerate(lines, 1) if "publishDone" in text)
        gap = CoverageGap(
            "SampleViewModel.kt",
            "sample",
            [catch_line, tail_line],
            [],
            [],
            [],
            (2, 2),
            (0, 0),
            {},
        )
        plan = build_coverage_opportunity_plan(source, gap, {"viewmodel"})
        self.assertFalse(plan["selected_attemptable"])
        self.assertEqual({catch_line, tail_line}, {line for item in plan["blocked"] for line in item.lines})

    def test_delayed_is_added_branch_requires_detach_before_looper_advance(self):
        source = """
class WelcomeFragment : Fragment() {
    override fun onResume() {
        super.onResume()
        Handler(Looper.getMainLooper()).postDelayed({
            if (isAdded) navigate()
        }, 5000)
    }
}
"""
        line = next(i for i, text in enumerate(source.splitlines(), 1) if "if (isAdded)" in text)
        gap = CoverageGap("WelcomeFragment.kt", "sample", [], [line], [], [], (0, 1), (1, 1), {})
        plan = build_coverage_opportunity_plan(source, gap, {"fragment"})
        selected = plan["selected_attemptable"]
        self.assertEqual("robolectric_delayed_handler", selected[0].fixture)
        self.assertIn("detach", selected[0].trigger_recipe.lower())
        self.assertFalse(plan["selected_safe"])

    def test_pipeline_failure_is_persisted_for_selected_opportunity(self):
        opportunity = _opp("onResume", fixture="robolectric_delayed_handler", lines=[59])
        with TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "WelcomeFragment.kt"
            source.write_text("class WelcomeFragment", encoding="utf-8")
            record_kover_rejected_attempt(
                temp_dir,
                source_file_path=str(source),
                opportunity_plan={"selected_safe": [], "selected_attemptable": [opportunity]},
                gradle_tasks=[":feature:login:testProdGlobalReleaseUnitTest"],
                failure_stage="gradle_repair",
                evidence="Generated fixture did not pass Gradle.",
                disposition=DISPOSITION_PIPELINE_UNRESOLVED,
            )
            entries = load_blocked_report(temp_dir)["entries"]
        self.assertEqual(1, len(entries))
        self.assertEqual(DISPOSITION_PIPELINE_UNRESOLVED, entries[0]["disposition"])
        self.assertEqual("gradle_repair", entries[0]["failure_stage"])


if __name__ == "__main__":
    unittest.main()
