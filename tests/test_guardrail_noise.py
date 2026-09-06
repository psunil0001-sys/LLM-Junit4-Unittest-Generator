"""Guardrail noise reductions: narrowed Hilt trigger + MockK teardown (no Android SDK)."""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).resolve().parents[1]
_PARENT = _REPO.parent
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

if not (_PARENT / "UnitTest_gen").exists() and _REPO.name != "UnitTest_gen":
    pkg = types.ModuleType("UnitTest_gen")
    pkg.__path__ = [str(_REPO)]  # type: ignore[attr-defined]
    sys.modules.setdefault("UnitTest_gen", pkg)


RULES = _REPO / "data" / "validation_rules"


def _load(name: str) -> list[dict]:
    data = yaml.safe_load((RULES / name).read_text(encoding="utf-8")) or {}
    return list(data.get("rules") or [])


def _install_validate_stubs() -> None:
    """Stub heavy kotlin.analysis / project / core.io so validate imports cleanly."""
    if "UnitTest_gen.kotlin.validate" in sys.modules:
        return

    def _mod(name: str, **attrs):
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        sys.modules[name] = m
        return m

    # Ensure package parents exist
    if "UnitTest_gen" not in sys.modules:
        pkg = types.ModuleType("UnitTest_gen")
        pkg.__path__ = [str(_REPO)]  # type: ignore[attr-defined]
        sys.modules["UnitTest_gen"] = pkg
    if "UnitTest_gen.core" not in sys.modules:
        core = types.ModuleType("UnitTest_gen.core")
        core.__path__ = [str(_REPO / "core")]  # type: ignore[attr-defined]
        sys.modules["UnitTest_gen.core"] = core
    kt = sys.modules.get("UnitTest_gen.kotlin")
    if kt is None or not hasattr(kt, "__path__"):
        kt = types.ModuleType("UnitTest_gen.kotlin")
        sys.modules["UnitTest_gen.kotlin"] = kt
    kt.__path__ = [str(_REPO / "kotlin")]  # type: ignore[attr-defined]
    kt.__package__ = "UnitTest_gen.kotlin"

    io = _mod(
        "UnitTest_gen.core.io",
        DEFAULT_SKIP_DIRS=frozenset(),
        walk_source_roots=lambda *a, **k: [],
        read_text=lambda *a, **k: "",
    )
    # validate does: from UnitTest_gen.core import io as file_cache
    sys.modules["UnitTest_gen.core"].io = io  # type: ignore[attr-defined]

    def _false(*_a, **_k):
        return False

    def _empty(*_a, **_k):
        return []

    def _none(*_a, **_k):
        return None

    _mod(
        "UnitTest_gen.kotlin.analysis",
        analyze_kotlin_code=_none,
        classify_source=lambda *_a, **_k: "",
        extract_junit4_test_blocks=_empty,
        has_apollo_response_extension_function=_false,
        kotlin_declared_class_names=_empty,
        kotlin_member_extension_functions=_empty,
        kotlin_top_level_class_name=lambda *_a, **_k: "",
        source_declares_android_fragment=_false,
        source_declares_viewmodel_class=_false,
        source_get_instance_types=lambda *_a, **_k: set(),
        source_uses_carui_toolbar_progress=_false,
        suspend_function_names_from_ast=_empty,
        verify_project_exception_findings=_empty,
    )
    _mod(
        "UnitTest_gen.kotlin.project",
        _ANDROID_R_REF_PATTERN=__import__("re").compile(r"R\."),
        choose_robolectric_sdk_for_module=_none,
        collect_module_test_dependency_versions=lambda *_a, **_k: {},
        find_owning_module_dir=lambda *_a, **_k: "",
        find_project_root_for_path=_none,
        find_single_same_type_resource_match=_none,
        is_android_platform_installed=_false,
        load_verified_android_resource_index=lambda *_a, **_k: {},
        lookup_verified_android_resource=_none,
        missing_platform_in_range_reason=lambda *_a, **_k: "",
        module_has_hilt_robolectric_fragment_support=_false,
        module_has_shared_test_hilt_binding=_false,
        owning_module_dir_for_output=_none,
        parse_module_compile_sdk=_none,
        parse_module_min_sdk=_none,
        parse_module_namespace=_none,
        parse_module_robolectric_version=_none,
        project_robolectric_version=_none,
    )

    _mod(
        "UnitTest_gen.kotlin.prompts",
        validation_repair_intent=lambda key: key,
        catalog_repair_intent=lambda key: key,
    )


@pytest.fixture(scope="module")
def pattern_api():
    _install_validate_stubs()
    from UnitTest_gen.kotlin.validate import load_rules, run_pattern_rules

    return load_rules, run_pattern_rules


def test_instrumented_hilt_yaml_drops_bare_inject_trigger():
    rules = _load("instrumented_hilt.yaml")
    hilt_anno = next(
        r for r in rules if "instrumented_missing_hilt_annotation" in str(r.get("message", ""))
    )
    source_any = list(hilt_anno["when"]["source_any"])
    assert "@Inject" not in source_any
    for marker in (
        "@AndroidEntryPoint",
        "@HiltAndroidApp",
        "@HiltViewModel",
        "@HiltWorker",
        "dagger.hilt",
        "androidx.hilt",
    ):
        assert marker in source_any


def test_instrumented_hilt_behavior_bare_inject_vs_entrypoint(pattern_api):
    load_rules, run_pattern_rules = pattern_api
    rules = load_rules("instrumented_hilt.yaml")

    bare = run_pattern_rules(
        "import org.junit.Test\nclass T { @Test fun a() {} }\n",
        rules,
        source_code="class Cut { @Inject lateinit var dep: Dep }\n",
    )
    assert not any("instrumented_missing_hilt_annotation" in m for m in bare)

    hilt = run_pattern_rules(
        "import org.junit.Test\nclass T { @Test fun a() {} }\n",
        rules,
        source_code="@AndroidEntryPoint\nclass Cut : Fragment()\n",
    )
    assert any("instrumented_missing_hilt_annotation" in m for m in hilt)

    imported = run_pattern_rules(
        "import org.junit.Test\nclass T { @Test fun a() {} }\n",
        rules,
        source_code="import dagger.hilt.android.AndroidEntryPoint\nclass Cut\n",
    )
    assert any("instrumented_missing_hilt_annotation" in m for m in imported)

    ok = run_pattern_rules(
        "@HiltAndroidTest\n@RunWith(AndroidJUnit4::class)\n"
        "class T { @get:Rule val hiltRule = HiltAndroidRule(this)\n@Test fun a() {} }\n",
        rules,
        source_code="@AndroidEntryPoint\nclass Cut\n",
    )
    assert not any("instrumented_missing_hilt_annotation" in m for m in ok)


def test_mockk_teardown_yaml_uses_call_site_and_after_exemption():
    rules = _load("mockk_patterns.yaml")
    teardown = [r for r in rules if "teardown" in str(r.get("message", ""))]
    assert len(teardown) >= 2
    for r in teardown:
        when = r["when"]
        assert "test" in when
        assert "mockk" in when["test"]
        assert when.get("test_contains") not in {"mockkObject", "mockkStatic"}
        assert "@After" in when["test_not"]
        assert "unmockkAll" in when["test_not"]


def test_mockk_teardown_behavior_after_and_non_mockk(pattern_api):
    load_rules, run_pattern_rules = pattern_api
    rules = load_rules("mockk_patterns.yaml")

    plain = run_pattern_rules(
        "import io.mockk.mockk\nimport io.mockk.every\nval d = mockk<Dep>()\nevery { d.x() } returns 1\n",
        rules,
        source_code="",
    )
    assert not any("teardown" in m for m in plain)

    leak = run_pattern_rules(
        "import io.mockk.mockkObject\nmockkObject(Helper)\nevery { Helper.x() } returns 1\n",
        rules,
        source_code="",
    )
    assert any("invalid_mockk_object_teardown" in m for m in leak)

    after = run_pattern_rules(
        "import io.mockk.*\n"
        "class T {\n"
        "  @Before fun setUp() { mockkObject(Helper) }\n"
        "  @After fun tearDown() { unmockkObject(Helper) }\n"
        "  @Test fun a() { every { Helper.x() } returns 1 }\n"
        "}\n",
        rules,
        source_code="",
    )
    assert not any("invalid_mockk_object_teardown" in m for m in after)

    all_clear = run_pattern_rules(
        "import io.mockk.*\nmockkStatic(Log::class)\nunmockkAll()\n",
        rules,
        source_code="",
    )
    assert not any("teardown" in m for m in all_clear)


def test_robolectric_deprecated_keeps_removed_apis_only():
    rules = _load("robolectric_deprecated.yaml")
    second = rules[1]
    patterns = list(second["when"]["test_any"])
    assert any("getShadowApplication" in p for p in patterns)
    assert any("getNextStartedIntent" in p for p in patterns)
    assert "org.robolectric.ShadowDialog" in patterns
