# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Minimal smoke tests for the production UnitTest_gen flow.
"""Small production-flow smoke tests that avoid model, Gradle, and MCP execution."""

from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile
import unittest

from UnitTest_gen.kotlin.gradle_analysis.errors import group_gradle_errors
from UnitTest_gen.kotlin.prompting.rules import build_generation_rule_tail
from UnitTest_gen.kotlin.repair_flow import group_junit_failures_by_report
from UnitTest_gen.kotlin.test_code_utils.validate import normalize_kotlin_test_code, validate_generated_test_code


REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]


class ToolFlowSmokeTest(unittest.TestCase):
    def test_production_modules_import(self):
        failures = []
        excluded_parts = {"tests", "tools", "__pycache__"}
        for path in sorted((REPO_ROOT / "UnitTest_gen").rglob("*.py")):
            rel = path.relative_to(REPO_ROOT / "UnitTest_gen")
            if any(part in excluded_parts for part in rel.parts):
                continue
            module = ".".join(path.with_suffix("").relative_to(REPO_ROOT).parts)
            try:
                __import__(module)
            except Exception as exc:  # pragma: no cover - assertion reports details
                failures.append(f"{module}: {type(exc).__name__}: {exc}")
        self.assertEqual([], failures)

    def test_cli_help_starts(self):
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "UnitTest_gen" / "AI_Unittestgenerator.py"), "--help"],
            cwd=str(REPO_ROOT),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("--project-root", result.stdout)
        self.assertIn("--mcp-server", result.stdout)

    def test_empty_gradle_groups_fall_back_to_owned_junit_xml(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            module = root / "feature"
            (module / "build.gradle.kts").parent.mkdir(parents=True)
            (module / "build.gradle.kts").write_text("plugins {}", encoding="utf-8")
            output = module / "src/test/java/sample/UserFragmentTest.kt"
            output.parent.mkdir(parents=True)
            output.write_text("class UserFragmentTest", encoding="utf-8")
            reports = module / "build/test-results/testDebugUnitTest"
            reports.mkdir(parents=True)
            (reports / "TEST-sample.UserFragmentTest.xml").write_text(
                '<testsuite><testcase classname="sample.UserFragmentTest" name="navigates">'
                '<failure type="java.lang.AssertionError" message="expected navigation"/>'
                '</testcase></testsuite>',
                encoding="utf-8",
            )
            groups = group_junit_failures_by_report(str(root), str(output), {})
        self.assertTrue(groups)
        self.assertIn("UserFragmentTest", "\n".join(line for lines in groups.values() for line in lines))

    def test_prompt_validation_and_gradle_grouping_smoke(self):
        source = "class LoginViewModel { fun load() = Unit }"
        rules = build_generation_rule_tail(
            is_fragment=False,
            is_hilt=False,
            is_viewmodel=True,
            uses_framework_blueprints=False,
            is_apollo_mapper=False,
            source_code=source,
            source_categories={"viewmodel"},
        )
        self.assertIn("FINAL GENERATION RULES", rules)

        issues = validate_generated_test_code(
            "import org.junit.jupiter.api.Test\nclass LoginViewModelTest",
            "/tmp/LoginViewModelTest.kt",
            source_code=source,
        )
        self.assertTrue(any("org.junit.jupiter" in issue for issue in issues))

        groups = group_gradle_errors("e: /tmp/LoginViewModelTest.kt:12:13 Unresolved reference: foo")
        self.assertTrue(groups)

    def test_validation_rules_emit_mockito_patterns_once(self):
        test_code = """
package sample

import org.junit.Test
import org.mockito.kotlin.anyInt

class FooTest {
    @Test fun invalid() {
        anyInt()
        MockedStatic.mockStatic(Foo::class.java)
    }
}
"""
        issues = validate_generated_test_code(test_code, "/tmp/FooTest.kt", source_code="class Foo")
        self.assertEqual(
            1,
            sum("primitive helper matchers such as anyInt()" in issue for issue in issues),
        )
        self.assertEqual(
            1,
            sum("invalid_mockedstatic_factory" in issue for issue in issues),
        )

    def test_normalize_removes_invalid_void_navigation_stubbing(self):
        normalized = normalize_kotlin_test_code(
            """
package sample

import org.junit.Test

class FooTest {
    @Test fun invalid() {
        Mockito.`when`(
            navController.navigate(Mockito.any(NavDeepLinkRequest::class.java))
        ).thenAnswer { null }
    }
}
""",
            "",
            "/tmp/FooTest.kt",
        )
        self.assertNotIn("thenAnswer { null }", normalized)
        self.assertNotIn("Mockito.any(NavDeepLinkRequest::class.java)", normalized)

    def test_normalize_swaps_test_nav_host_for_mock_on_deeplink_source(self):
        source = "fun go() { NavDeepLinkRequest.Builder.fromUri(uri).build().also { findNavController().navigate(it) } }"
        normalized = normalize_kotlin_test_code(
            """
package sample

import androidx.navigation.testing.TestNavHostController
import org.junit.Test

class LoginFragmentTest {
    private lateinit var navController: androidx.navigation.NavController

    @Test fun setup() {
        navController = TestNavHostController(activity, "graph")
        Navigation.setViewNavController(fragment.requireView(), navController)
    }
}
""",
            source,
            "/tmp/LoginFragmentTest.kt",
        )
        self.assertNotIn("TestNavHostController", normalized)
        self.assertIn("navController = mock()", normalized)
        self.assertIn("import org.mockito.kotlin.mock", normalized)

        issues = validate_generated_test_code(
            normalized,
            "/tmp/LoginFragmentTest.kt",
            source_code=source,
        )
        self.assertFalse(any("invalid_nav_deeplink_fixture" in issue for issue in issues))

    def test_normalize_adds_missing_package_from_source(self):
        normalized = normalize_kotlin_test_code(
            """
import org.junit.Test

class FooTest {
    @Test fun ok() {}
}
""",
            "package com.example.feature\nclass Foo",
            "/tmp/src/test/java/com/example/feature/FooTest.kt",
        )
        self.assertTrue(normalized.lstrip().startswith("package com.example.feature"))

    def test_normalize_merged_mockito_imports_and_carui_setter_verification(self):
        normalized = normalize_kotlin_test_code(
            """
package sample
import org.mockito.kotlin.verify
import org.mockito.kotlin.any
import org.mockito.Mockito.verify
import org.mockito.Mockito.any

class FragmentTest {
    fun verifyProgress() {
        org.mockito.Mockito.verify(progress).isVisible = true
        verify(progress).progress = 100
        verify(progress).isIndeterminate = false
    }
}
"""
        )
        self.assertNotIn("import org.mockito.Mockito.verify", normalized)
        self.assertNotIn("import org.mockito.Mockito.any", normalized)
        self.assertIn("verify(progress).setVisible(true)", normalized)
        self.assertIn("verify(progress).setProgress(100)", normalized)
        self.assertIn("verify(progress).setIndeterminate(false)", normalized)

    def test_validation_rules_emit_moved_categories_once(self):
        cases = [
            (
                "static policy",
                "package sample\nimport org.junit.Test\nimport io.mockk.mockk\nclass FooTest { @Test fun invalid() { mockk<Foo>() } }",
                "/tmp/FooTest.kt",
                "class Foo",
                "invalid_mocking_framework",
            ),
            (
                "viewmodel lateinit",
                "package sample\nimport org.junit.Test\nclass LoginViewModelTest { @Test fun invalid() { LoginViewModel().apply { repo = mock() } } }",
                "/tmp/LoginViewModelTest.kt",
                "class LoginViewModel : ViewModel() { @javax.inject.Inject lateinit var repo: Repo }",
                "invalid_lateinit_injection_setup_order",
            ),
            (
                "suspend assert",
                "package sample\nimport org.junit.Test\nclass RepoTest { @Test fun invalid() { assertThrows(Exception::class.java) { repo.load() } } }",
                "/tmp/RepoTest.kt",
                "class Repo { suspend fun load() {} }",
                "assertThrows cannot directly call suspend function load",
            ),
            (
                "junit declaration",
                "package sample\nclass FooTest { @Test fun invalid()() {} }",
                "/tmp/FooTest.kt",
                "class Foo",
                "JUnit4 test declarations should have exactly one parameter list",
            ),
            (
                "apollo reflection",
                "package sample\nclass ApiExtKtTest { fun invalid(methodName: String, parameterTypes: Array<Class<*>>, args: Array<Any?>) = ApiExtKt::class.java.getDeclaredMethod(methodName, *parameterTypes).invoke(null, *args) }",
                "/tmp/ApiExtKtTest.kt",
                "package com.example.app.journetlog.api.type\nclass Source",
                "Reflected ApiExtKt invocation should unwrap InvocationTargetException",
            ),
        ]
        for name, test_code, output_path, source_code, expected in cases:
            with self.subTest(name=name):
                issues = validate_generated_test_code(test_code, output_path, source_code=source_code)
                self.assertEqual(1, sum(expected in issue for issue in issues), issues)


if __name__ == "__main__":
    unittest.main()
