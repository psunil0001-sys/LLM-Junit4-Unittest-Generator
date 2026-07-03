# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Pinned test-dependency versions and API guidance for generation/repair prompts.
"""Resolve module test dependency versions and emit library-specific API guidance."""

from __future__ import annotations

import os
import re
from pathlib import Path

from UnitTest_gen.kotlin.project_context import (
    find_owning_module_dir,
    find_project_root_for_path,
    project_hilt_version,
)

# Explicit Maven coordinates in module build.gradle(.kts).
TEST_COORDINATE_PATTERNS: dict[str, str] = {
    "mockito_kotlin": r"org\.mockito\.kotlin:mockito-kotlin:([^\"')\s]+)",
    "mockito_inline": r"org\.mockito:mockito-inline:([^\"')\s]+)",
    "robolectric": r"org\.robolectric:robolectric:([^\"')\s]+)",
    "coroutines_test": r"kotlinx-coroutines-test:([^\"')\s]+)",
    "androidx_test_core": r"androidx\.test:core:([^\"')\s]+)",
    "fragment_testing": r"fragment-testing:([^\"')\s]+)",
    "navigation_testing": r"navigation-testing:([^\"')\s]+)",
    "arch_core_testing": r"arch\.core:core-testing:([^\"')\s]+)",
    "hilt_android_testing": r"hilt-android-testing:([^\"')\s]+)",
    "androidx_test_ext_junit": r"androidx\.test\.ext:junit:([^\"')\s]+)",
    "mockk": r"io\.mockk:mockk:([^\"')\s]+)",
}

# testImplementation(libs.<alias>) -> [libraries].<alias> in libs.versions.toml
CATALOG_LIB_ALIASES: dict[str, str] = {
    "junit": "junit",
}


def load_version_catalog(project_root: str) -> tuple[dict[str, str], dict[str, tuple[str, str, str | None]]]:
    catalog_path = os.path.join(project_root, "gradle", "libs.versions.toml")
    if not os.path.isfile(catalog_path):
        return {}, {}
    try:
        text = Path(catalog_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}, {}

    versions: dict[str, str] = {}
    for match in re.finditer(r'(?m)^\s*([A-Za-z0-9_-]+)\s*=\s*"([^"]+)"', text):
        versions[match.group(1)] = match.group(2)

    libraries: dict[str, tuple[str, str, str | None]] = {}
    for match in re.finditer(
        r'(?m)^\s*([A-Za-z0-9_-]+)\s*=\s*\{\s*group\s*=\s*"([^"]+)"\s*,\s*name\s*=\s*"([^"]+)"(?:\s*,\s*version\.ref\s*=\s*"([^"]+)")?',
        text,
    ):
        alias, group, name, version_ref = match.groups()
        libraries[alias] = (group, name, version_ref)
    return versions, libraries


def _resolve_catalog_lib_version(
    lib_alias: str,
    catalog_versions: dict[str, str],
    catalog_libraries: dict[str, tuple[str, str, str | None]],
) -> str:
    entry = catalog_libraries.get(lib_alias)
    if not entry:
        return ""
    _group, _name, version_ref = entry
    if not version_ref:
        return ""
    return catalog_versions.get(version_ref, "")


def parse_versions_from_gradle_content(content: str, project_root: str) -> dict[str, str]:
    versions: dict[str, str] = {}
    catalog_versions, catalog_libraries = load_version_catalog(project_root) if project_root else ({}, {})

    for key, pattern in TEST_COORDINATE_PATTERNS.items():
        match = re.search(pattern, content or "")
        if match:
            versions[key] = match.group(1)

    for gradle_alias, catalog_key in CATALOG_LIB_ALIASES.items():
        if re.search(rf"testImplementation\s*\(\s*libs\.{re.escape(gradle_alias)}\s*\)", content or ""):
            resolved = _resolve_catalog_lib_version(catalog_key, catalog_versions, catalog_libraries)
            if resolved:
                versions[catalog_key] = resolved

    if "hilt_android_testing" not in versions and project_root:
        hilt_version = project_hilt_version(project_root)
        if hilt_version and "hilt-android-testing" in (content or ""):
            versions["hilt_android_testing"] = hilt_version

    return versions


def read_module_gradle_content(module_dir: str) -> str:
    for build_file_name in ("build.gradle.kts", "build.gradle"):
        build_file_path = os.path.join(module_dir, build_file_name)
        if not os.path.exists(build_file_path):
            continue
        try:
            return Path(build_file_path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
    return ""


def collect_module_test_dependency_versions(module_dir: str, project_root: str) -> dict[str, str]:
    merged: dict[str, str] = {}
    content = read_module_gradle_content(module_dir)
    if content:
        merged.update(parse_versions_from_gradle_content(content, project_root))

    if not merged.get("robolectric") and project_root:
        for fallback_module in ("common", "app", "feature/digitalkeymanagement"):
            fallback_dir = os.path.join(project_root, fallback_module)
            if os.path.isdir(fallback_dir) and os.path.abspath(fallback_dir) != os.path.abspath(module_dir):
                fallback_versions = parse_versions_from_gradle_content(
                    read_module_gradle_content(fallback_dir),
                    project_root,
                )
                for key, value in fallback_versions.items():
                    merged.setdefault(key, value)

    if not merged.get("hilt_android_testing") and project_root:
        hilt_version = project_hilt_version(project_root)
        if hilt_version:
            merged.setdefault("hilt_android_testing", hilt_version)

    return merged


def parse_module_robolectric_version(module_dir: str) -> str | None:
    content = read_module_gradle_content(module_dir)
    match = re.search(TEST_COORDINATE_PATTERNS["robolectric"], content)
    return match.group(1) if match else None


def project_robolectric_version(project_root: str) -> str:
    for module_name in ("common", "app", "feature/digitalkeymanagement"):
        version = parse_module_robolectric_version(os.path.join(project_root, module_name))
        if version:
            return version
    for build_file in Path(project_root).glob("**/build.gradle.kts"):
        version = parse_module_robolectric_version(str(build_file.parent))
        if version:
            return version
    return ""


def robolectric_api_guidance_for_version(version: str) -> str:
    if not version:
        return ""
    try:
        major = int(version.split(".", 1)[0])
    except ValueError:
        major = 0
    if major < 4:
        return (
            f"Robolectric {version}: use shadow APIs verified for this major version in the owning module Gradle file."
        )
    return (
        f"Robolectric {version} (4.x): dialogs -> org.robolectric.shadows.ShadowAlertDialog.getLatestDialog(); "
        "main looper -> org.robolectric.shadows.ShadowLooper.idleMainLooper() or runUiThreadTasksIncludingDelayedTasks(). "
        "Do NOT use org.robolectric.ShadowDialog, ShadowDialog.getLatestDialog(), or Robolectric.flushMainThreadQueue()."
    )


def mockito_api_guidance(versions: dict[str, str]) -> str:
    kotlin_version = versions.get("mockito_kotlin", "")
    inline_version = versions.get("mockito_inline", "")
    if not kotlin_version and not inline_version:
        return ""
    version_text = ", ".join(
        part
        for part in (
            f"mockito-kotlin {kotlin_version}" if kotlin_version else "",
            f"mockito-inline {inline_version}" if inline_version else "",
        )
        if part
    )
    return (
        f"Mockito ({version_text}) — project mocking standard for generated tests:\n"
        "- Use org.mockito.kotlin only: mock(), whenever(), verify(), any(), argumentCaptor<T>().\n"
        "- Static mocks: org.mockito.Mockito.mockStatic(Type::class.java); never MockedStatic.mockStatic(...).\n"
        "- Primitives: any<Int>(), any<Boolean>() — not anyInt() / anyBoolean().\n"
        "- Do not verify Kotlin properties with `verify(mock).prop = value`; use method verify or public behavior.\n"
        "- CarUI ProgressBarController: verify setIndeterminate(true/false), not setIsIndeterminate.\n"
        "- Do not generate io.mockk APIs when Mockito is pinned (mockk may exist for legacy tests only)."
    )


def hilt_testing_api_guidance(version: str) -> str:
    if not version:
        return ""
    return (
        f"Hilt Android Testing {version}:\n"
        "- testImplementation hilt-android-testing version must match project Hilt ({version}).\n"
        "- Local JVM Fragment tests: @HiltAndroidTest, @get:Rule HiltAndroidRule, @Config(application = HiltTestApplication::class).\n"
        "- Use the module manifest-declared HiltTestActivity; do not invent a local fake host Activity."
    )


def coroutines_test_api_guidance(version: str) -> str:
    if not version:
        return ""
    return (
        f"kotlinx-coroutines-test {version}:\n"
        "- Use runTest { } for suspend/Flow tests; advance StandardTestDispatcher with scheduler.advanceUntilIdle() when needed.\n"
        "- Pair with InstantTaskExecutorRule when LiveData posts must flush on the main thread in the same test."
    )


def arch_core_testing_api_guidance(version: str) -> str:
    if not version:
        return ""
    return (
        f"androidx.arch.core:core-testing {version}:\n"
        "- Use androidx.arch.core.executor.testing.InstantTaskExecutorRule for synchronous LiveData/ViewModel observer tests."
    )


def androidx_test_core_api_guidance(version: str) -> str:
    if not version:
        return ""
    return (
        f"androidx.test:core {version}:\n"
        "- Real Context: androidx.test.core.app.ApplicationProvider.getApplicationContext()."
    )


def fragment_testing_api_guidance(version: str) -> str:
    if not version:
        return ""
    return (
        f"androidx.fragment:fragment-testing {version}:\n"
        "- launchFragmentInContainer / FragmentScenario only for plain non-Hilt Fragments.\n"
        "- @AndroidEntryPoint Fragments use Robolectric ActivityController + HiltTestActivity + commitNow()."
    )


def navigation_testing_api_guidance(version: str) -> str:
    if not version:
        return ""
    return (
        f"androidx.navigation:navigation-testing {version}:\n"
        "- TestNavHostController when the exact graph is installed; otherwise mock NavController + argumentCaptor<NavDeepLinkRequest>().\n"
        "- Never use ArgumentCaptor.forClass(NavDeepLinkRequest::class.java).capture() in Kotlin tests."
    )


def junit_api_guidance(version: str) -> str:
    if not version:
        return ""
    return (
        f"JUnit {version}:\n"
        "- JUnit4 only: org.junit.Test, org.junit.Before/After, org.junit.Rule, @RunWith(...).\n"
        "- Do not generate JUnit5 (@ExtendWith, org.junit.jupiter) unless explicitly present in nearby tests."
    )


def build_test_stack_api_guidance(versions: dict[str, str]) -> str:
    if not versions:
        return ""

    blocks: list[str] = ["### PINNED TEST STACK (from owning module Gradle file)"]
    builders = (
        ("junit", junit_api_guidance),
        ("mockito_kotlin", lambda _: mockito_api_guidance(versions)),
        ("mockito_inline", lambda _: mockito_api_guidance(versions)),
        ("hilt_android_testing", hilt_testing_api_guidance),
        ("coroutines_test", coroutines_test_api_guidance),
        ("arch_core_testing", arch_core_testing_api_guidance),
        ("androidx_test_core", androidx_test_core_api_guidance),
        ("fragment_testing", fragment_testing_api_guidance),
        ("navigation_testing", navigation_testing_api_guidance),
        ("robolectric", robolectric_api_guidance_for_version),
    )
    seen_mockito = False
    for key, builder in builders:
        version = versions.get(key, "")
        if not version:
            continue
        if key in {"mockito_kotlin", "mockito_inline"}:
            if seen_mockito:
                continue
            seen_mockito = True
        block = builder(version)
        if block and block not in blocks:
            blocks.append(block)

    mockk_version = versions.get("mockk", "")
    if mockk_version:
        blocks.append(
            f"io.mockk {mockk_version} is on the classpath for legacy tests; "
            "do not generate mockk in new AI tests — use Mockito-Kotlin per project standard."
        )

    return "\n\n".join(blocks) if len(blocks) > 1 else ""


_TEST_STACK_MARKERS = (
    "module sdk and test stack configuration",
    "shadowalertdialog",
    "shadowlooper",
    "robolectric.properties",
    "hilt-android-testing",
    "navigation-testing",
)


def rule_overlaps_test_stack(text: str) -> bool:
    lower = (text or "").lower()
    return any(marker in lower for marker in _TEST_STACK_MARKERS)
