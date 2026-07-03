# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Tests classification-gated RAG behavior.
"""Classification-gated RAG and prompting package smoke tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from UnitTest_gen.kotlin import prompts
from UnitTest_gen.kotlin.project_context import (
    collect_nearby_test_pattern_context,
    find_semantic_and_structural_dependencies,
)
from UnitTest_gen.kotlin.prompting.generation import generate_test_code_streaming
from UnitTest_gen.kotlin.prompting.rules import (
    build_generation_source_shape_guidance,
    retrieve_kotlin_rule_context,
)
from UnitTest_gen.kotlin.prompting.rules import format_grouped_kotlin_rule_context
from UnitTest_gen.kotlin.strategy_contracts import (
    ATTACHED_HILT_FRAGMENT_CONTRACT,
    COVERAGE_BLOCKED_PATH_CONTRACT,
    NAV_DEEPLINK_VERIFICATION_CONTRACT,
)


class TestClassificationGatedRag(unittest.TestCase):
    def test_classification_gated_dependencies_prefers_category_overlap(self):
        firebase_source = {
            "content": (
                "import com.example.app.firebasehelper.BaseFirebaseEvents\n"
                "class HomeFragmentEvents : BaseFirebaseEvents() { fun pushEvent() {} }"
            ),
            "class_name": "HomeFragmentEvents",
        }
        base_firebase = {
            "content": "class BaseFirebaseEvents { fun pushEvent(data: Map<String, String>) {} }",
            "class_name": "BaseFirebaseEvents",
        }
        utils_source = {
            "content": (
                "object DigitalKeyUtils { fun getAppVersion(context: android.content.Context): String = \"1\" }"
            ),
            "class_name": "DigitalKeyUtils",
        }
        project_index = {
            "HomeFragmentEvents": firebase_source,
            "BaseFirebaseEvents": base_firebase,
            "DigitalKeyUtils": utils_source,
        }
        context = find_semantic_and_structural_dependencies(
            "HomeFragmentEvents",
            firebase_source,
            project_index,
        )
        self.assertIn("BaseFirebaseEvents", context)
        self.assertNotIn("DigitalKeyUtils", context)

    def test_nearby_requires_category_overlap(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_dir = Path(temp_dir)
            (test_dir / "ImportOnlyNeighborTest.kt").write_text(
                "package app\nimport com.example.ForeignOnly\nclass ImportOnlyNeighborTest { @Test fun x() {} }\n",
                encoding="utf-8",
            )
            output = test_dir / "TargetTest.kt"
            output.write_text("package app\nclass TargetTest { @Test fun y() {} }\n", encoding="utf-8")
            context = collect_nearby_test_pattern_context(
                str(output),
                output.read_text(encoding="utf-8"),
                "Target",
                source_categories={"constructor_injection", "firebase"},
                require_category_overlap=True,
            )
        self.assertIn("No nearby same-package test examples were found.", context)

    def test_grouped_rules_drop_unrelated_hilt_directives(self):
        raw = (
            "Section: CORE_GENERATION_RULES\n"
            "- Use @HiltAndroidTest and HiltAndroidRule for attached @AndroidEntryPoint Fragment lifecycle tests.\n"
            "- Constructor-injected repositories should be instantiated directly with Mockito-Kotlin mocks.\n"
        )
        grouped = format_grouped_kotlin_rule_context(
            raw,
            "generation",
            {"constructor_injection", "firebase"},
        )
        self.assertNotIn("HiltAndroidRule", grouped)
        self.assertIn("Constructor-injected repositories", grouped)

    def test_shape_guidance_firebase_spy_without_unrelated_car_bullets(self):
        source = (
            "class HomeFragmentEvents : BaseFirebaseEvents() {\n"
            "  fun emit() { pushEvent(mapOf(\"k\" to \"v\")) }\n"
            "}\n"
        )
        guidance = build_generation_source_shape_guidance("HomeFragmentEvents", source)
        self.assertIn("spy", guidance.lower())
        self.assertNotIn("CarPropertyManager", guidance)

    def test_strategy_contracts_baseline_restored(self):
        self.assertTrue(
            any("HiltAndroidTest" in rule and "HiltTestApplication" in rule for rule in ATTACHED_HILT_FRAGMENT_CONTRACT.prompt_rules)
        )
        self.assertEqual(len(NAV_DEEPLINK_VERIFICATION_CONTRACT.prompt_rules), 3)
        self.assertTrue(
            any("no-op or method-entry" in rule.lower() for rule in COVERAGE_BLOCKED_PATH_CONTRACT.prompt_rules)
        )

    def test_prompting_package_imports(self):
        self.assertTrue(callable(generate_test_code_streaming))
        self.assertTrue(hasattr(prompts, "filter_retrieval_tags"))
        retrieved = retrieve_kotlin_rule_context(
            "class Repo @Inject constructor(private val api: Api)",
            "generation",
        )
        self.assertIsNotNone(retrieved.text)

    def test_prompt_rule_sources_keep_core_blocks_and_playbooks(self):
        source_names = set(prompts.RULE_SOURCE_NAMES)
        self.assertIn("CORE_GENERATION_RULES", source_names)
        self.assertIn("FIXTURE_PLAYBOOK_VERIFIED_UI_CLICK", source_names)
        self.assertIn("INCREMENTAL_COVERAGE_RULES", source_names)
        self.assertTrue(prompts.has_fixture_playbook("verified_ui_click"))
        self.assertIn("### PLAYBOOK: verified_ui_click", prompts.format_fixture_playbooks(["verified_ui_click"]))


if __name__ == "__main__":
    unittest.main()
