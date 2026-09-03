"""androidTest Hilt host/manifest/nav-graph bootstrap."""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from UnitTest_gen.core import io as file_cache
from UnitTest_gen.core.io import log_message
from UnitTest_gen.kotlin.project import (
    module_ready_for_instrumented_tests,
    read_owning_module_gradle,
)
from UnitTest_gen.kotlin.project import (
    derive_instrumented_test_output_path,
    find_nav_graph_for_fragment,
    source_kotlin_fqcn,
    source_requires_main_activity_delegate,
)

_HILT_TAGS = frozenset({
    "hilt_fragment",
    "hilt_entrypoint",
    "hilt_activity",
    "hilt_android_activity",
    "hilt_service",
    "hilt_worker",
})

_DEFAULT_HILT_TESTING = "2.49"
_DEFAULT_ANDROIDX_TEST_EXT_JUNIT = "1.1.5"
_DEFAULT_ANDROIDX_TEST_RUNNER = "1.5.2"
_DEFAULT_ANDROIDX_TEST_RULES = "1.5.0"
_DEFAULT_MOCKK_ANDROID = "1.13.10"
_DEFAULT_ESPRESSO_CORE = "3.6.1"

_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "data" / "templates" / "instrumented"

_UI_INSTRUMENTED_TAGS = frozenset({
    "android_fragment",
    "android_ui",
    "android_navigation",
    "hilt_fragment",
})

@dataclass(frozen=True)
class BootstrapResult:
    ready: bool
    patched: bool
    changes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

def _needs_hilt(categories: frozenset[str] | set[str], source_code: str) -> bool:
    tags = frozenset(categories or ())
    if tags & _HILT_TAGS:
        return True
    hay = source_code or ""
    return "@AndroidEntryPoint" in hay or "@HiltAndroidApp" in hay or "@HiltWorker" in hay

def _needs_mockk(categories: frozenset[str] | set[str], source_code: str) -> bool:
    hay = (source_code or "").lower()
    return "mockk" in hay or "io.mockk" in hay

def _needs_espresso(categories: frozenset[str] | set[str]) -> bool:
    return bool(frozenset(categories or ()) & _UI_INSTRUMENTED_TAGS)

def _hilt_version_from_gradle(gradle_content: str) -> str:
    if match := re.search(r"hilt-android-testing:([^\"')\s]+)", gradle_content):
        return match.group(1)
    if match := re.search(r"hilt\.android\.compiler\)[:\s]*[\"']([^\"']+)", gradle_content):
        return match.group(1)
    return _DEFAULT_HILT_TESTING

def _find_matching_brace(content: str, open_brace_index: int) -> int | None:
    """Return index of ``}`` matching ``{`` at ``open_brace_index``, skipping comments/strings."""
    depth = 0
    idx = open_brace_index
    length = len(content)
    while idx < length:
        ch = content[idx]
        if ch == "/" and idx + 1 < length:
            nxt = content[idx + 1]
            if nxt == "/":
                newline = content.find("\n", idx)
                idx = length if newline == -1 else newline + 1
                continue
            if nxt == "*":
                end = content.find("*/", idx + 2)
                if end == -1:
                    return None
                idx = end + 2
                continue
        if ch in {'"', "'"}:
            quote = ch
            idx += 1
            while idx < length:
                if content[idx] == "\\":
                    idx += 2
                    continue
                if content[idx] == quote:
                    idx += 1
                    break
                idx += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return idx
        idx += 1
    return None

def _dependencies_block_insert_index(content: str) -> int | None:
    """Index before closing ``}`` of the last line-start ``dependencies {`` block."""
    pattern = re.compile(r"^dependencies\s*\{", re.MULTILINE)
    matches = list(pattern.finditer(content))
    if not matches:
        return None
    match = matches[-1]
    open_brace = content.find("{", match.start())
    if open_brace == -1:
        return None
    close_brace = _find_matching_brace(content, open_brace)
    return close_brace

def _insert_before_dependencies_close(content: str, lines: list[str]) -> tuple[str, list[str]]:
    """Insert dependency lines before the closing brace of the last dependencies block."""
    applied: list[str] = []
    if not lines:
        return content, applied
    insert_at = _dependencies_block_insert_index(content)
    if insert_at is None:
        return content, applied
    block_start = content.rfind("dependencies", 0, insert_at)
    block = content[block_start:insert_at]
    to_add = [line for line in lines if line.strip() and line.strip() not in block]
    if not to_add:
        return content, applied
    prefix = "\n    // UnitTest_gen instrumented bootstrap\n"
    insertion = prefix + "\n".join(f"    {line}" for line in to_add) + "\n"
    new_content = content[:insert_at] + insertion + content[insert_at:]
    applied.extend(to_add)
    return new_content, applied

def _patch_gradle_file(
    gradle_path: str,
    gradle_content: str,
    *,
    needs_hilt: bool,
    needs_mockk: bool,
    needs_espresso: bool = False,
) -> tuple[str, list[str]]:
    changes: list[str] = []
    content = gradle_content

    if "testgen-coverage.gradle" not in content:
        append = '\napply(from = rootProject.file("gradle/testgen-coverage.gradle"))\n'
        content = content.rstrip() + append
        changes.append("apply testgen-coverage.gradle")

    dep_lines: list[str] = []
    if "androidx.test.ext:junit" not in content and "androidx-test-ext-junit" not in content:
        dep_lines.append(
            f'androidTestImplementation("androidx.test.ext:junit:{_DEFAULT_ANDROIDX_TEST_EXT_JUNIT}")'
        )
    if "androidx.test:runner" not in content:
        dep_lines.append(
            f'androidTestImplementation("androidx.test:runner:{_DEFAULT_ANDROIDX_TEST_RUNNER}")'
        )
    if "androidx.test:rules" not in content:
        dep_lines.append(
            f'androidTestImplementation("androidx.test:rules:{_DEFAULT_ANDROIDX_TEST_RULES}")'
        )
    if needs_hilt:
        hilt_version = _hilt_version_from_gradle(content)
        if not re.search(r"androidTestImplementation\s*\([^)]*hilt-android-testing", content):
            dep_lines.append(
                f'androidTestImplementation("com.google.dagger:hilt-android-testing:{hilt_version}")'
            )
        if "kspAndroidTest(" not in content and "kaptAndroidTest(" not in content:
            if "ksp(" in content or "kspTest(" in content or "alias(libs.plugins.devtools.ksp)" in content:
                dep_lines.append(f'kspAndroidTest("com.google.dagger:hilt-android-compiler:{hilt_version}")')
            else:
                dep_lines.append(f'kaptAndroidTest("com.google.dagger:hilt-android-compiler:{hilt_version}")')
    if needs_mockk and "mockk-android" not in content:
        dep_lines.append(f'androidTestImplementation("io.mockk:mockk-android:{_DEFAULT_MOCKK_ANDROID}")')
    if needs_espresso and "espresso-core" not in content:
        dep_lines.append(
            f'androidTestImplementation("androidx.test.espresso:espresso-core:{_DEFAULT_ESPRESSO_CORE}")'
        )

    content, inserted = _insert_before_dependencies_close(content, dep_lines)
    changes.extend(inserted)

    if content != gradle_content and gradle_path:
        try:
            Path(gradle_path).write_text(content, encoding="utf-8")
        except OSError:
            return gradle_content, []
    return content, changes

def _kotlin_package(kotlin_path: Path) -> str | None:
    try:
        text = kotlin_path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r"^package\s+([\w.]+)", text, re.MULTILINE)
    return match.group(1) if match else None

def _kotlin_type_fqcn(kotlin_path: Path, *, class_name: str = "HiltTestActivity") -> str | None:
    try:
        text = kotlin_path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r"^package\s+([\w.]+)", text, re.MULTILINE)
    if not match:
        return None
    return f"{match.group(1)}.{class_name}"

def _unit_hilt_test_activity_candidates(module_dir: str) -> list[Path]:
    test_root = Path(module_dir) / "src" / "test" / "java"
    if not test_root.is_dir():
        return []
    return sorted(test_root.rglob("HiltTestActivity.kt"))

def _select_unit_hilt_test_activity(
    module_dir: str,
    *,
    require_main_activity_delegate: bool,
) -> Path | None:
    for candidate in _unit_hilt_test_activity_candidates(module_dir):
        content = file_cache.read_text(str(candidate), default="")
        if "@AndroidEntryPoint" not in content or "class HiltTestActivity" not in content:
            continue
        if require_main_activity_delegate and "MainActivityDelegate" not in content:
            continue
        return candidate
    return None

def _mirror_hilt_test_activity(module_dir: str, source_activity: Path) -> tuple[Path | None, bool]:
    """Copy unit-test HiltTestActivity into androidTest when missing. Returns (dest, created)."""
    test_java = Path(module_dir) / "src" / "test" / "java"
    try:
        rel = source_activity.relative_to(test_java)
    except ValueError:
        return None, False
    dest = Path(module_dir) / "src" / "androidTest" / "java" / rel
    if dest.is_file():
        return dest, False
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_activity, dest)
    return dest, True

def _minimal_android_test_manifest() -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android" />\n'
    )

def _hilt_host_android_test_manifest(activity_fqcn: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android">\n'
        "    <application>\n"
        "        <activity\n"
        f'            android:name="{activity_fqcn}"\n'
        '            android:exported="false" />\n'
        "    </application>\n"
        "</manifest>\n"
    )

def _ensure_android_test_manifest(
    module_dir: str,
    source_code: str,
    categories: frozenset[str] | set[str],
) -> list[str]:
    """Create src/androidTest/AndroidManifest.xml when missing; mirror HiltTestActivity if needed."""
    changes: list[str] = []
    manifest_path = Path(module_dir) / "src" / "androidTest" / "AndroidManifest.xml"
    if manifest_path.is_file():
        return changes

    needs_hilt = _needs_hilt(categories, source_code)
    activity_fqcn: str | None = None
    if needs_hilt:
        require_delegate = source_requires_main_activity_delegate(source_code)
        unit_host = _select_unit_hilt_test_activity(
            module_dir, require_main_activity_delegate=require_delegate,
        )
        if unit_host is None and require_delegate:
            unit_host = _select_unit_hilt_test_activity(
                module_dir, require_main_activity_delegate=False,
            )
        if unit_host is not None:
            dest, created = _mirror_hilt_test_activity(module_dir, unit_host)
            if created and dest is not None:
                changes.append(f"copied HiltTestActivity to {dest.relative_to(module_dir)}")
                log_message(
                    f"🔧 Instrumented bootstrap: copied HiltTestActivity → {dest}",
                    category="gradle",
                )
            target = dest or unit_host
            activity_fqcn = _kotlin_type_fqcn(target)
        elif needs_hilt:
            log_message(
                "⚠️ Instrumented bootstrap: no unit-test HiltTestActivity found; "
                "writing minimal androidTest manifest only.",
                category="warning",
            )

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    body = (
        _hilt_host_android_test_manifest(activity_fqcn)
        if activity_fqcn
        else _minimal_android_test_manifest()
    )
    manifest_path.write_text(body, encoding="utf-8")
    changes.append("created src/androidTest/AndroidManifest.xml")
    log_message(
        f"🔧 Instrumented bootstrap: created {manifest_path}",
        category="gradle",
    )
    return changes

def _needs_nav_host(
    categories: frozenset[str] | set[str],
    source_code: str,
    *,
    module_dir: str,
    source_file_path: str,
) -> bool:
    tags = frozenset(categories or ())
    if "android_navigation" in tags:
        return True
    if "findNavController" in (source_code or ""):
        return True
    fqcn = source_kotlin_fqcn(source_file_path)
    return bool(fqcn and find_nav_graph_for_fragment(module_dir, fqcn))

def _merge_manifest_activity(manifest_path: Path, activity_fqcn: str) -> bool:
    try:
        text = manifest_path.read_text(encoding="utf-8")
    except OSError:
        return False
    if activity_fqcn in text:
        return False
    insert = (
        "        <activity\n"
        f'            android:name="{activity_fqcn}"\n'
        '            android:exported="false" />\n'
    )
    if "</application>" not in text:
        return False
    manifest_path.write_text(
        text.replace("    </application>", insert + "    </application>", 1),
        encoding="utf-8",
    )
    return True

def _android_test_host_package(module_dir: str) -> str | None:
    module = Path(module_dir)
    for pattern in ("src/androidTest/java/**/HiltTestActivity.kt", "src/test/java/**/HiltTestActivity.kt"):
        for candidate in sorted(module.glob(pattern)):
            pkg = _kotlin_package(candidate)
            if pkg:
                return pkg
    return None

def _write_template_if_missing(dest: Path, template_name: str, **replacements: str) -> bool:
    if dest.is_file():
        return False
    template_path = _TEMPLATES_DIR / template_name
    try:
        body = template_path.read_text(encoding="utf-8")
    except OSError:
        return False
    for key, value in replacements.items():
        body = body.replace(f"{{{key}}}", value)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(body, encoding="utf-8")
    return True

def _module_namespace(module_dir: str, fallback_package: str) -> str:
    gradle = Path(module_dir) / "build.gradle.kts"
    if not gradle.is_file():
        gradle = Path(module_dir) / "build.gradle"
    text = file_cache.read_text(str(gradle), default="")
    if match := re.search(r'namespace\s*=\s*"([^"]+)"', text):
        return match.group(1)
    return fallback_package

def _extract_deeplink_uris(source_code: str) -> list[str]:
    return sorted(set(re.findall(r'"(android-app://[^"]+)"', source_code or "")))

def _fragment_destination_id(module_dir: str, nav_graph_stem: str, fragment_fqcn: str) -> str:
    nav_xml = Path(module_dir) / "src" / "main" / "res" / "navigation" / f"{nav_graph_stem}.xml"
    text = file_cache.read_text(str(nav_xml), default="")
    pattern = re.compile(
        r'<fragment\b[^>]*\bandroid:id="@\+id/([^"]+)"[^>]*\bandroid:name="' + re.escape(fragment_fqcn) + r'"',
        re.DOTALL,
    )
    match = pattern.search(text)
    if match:
        return match.group(1)
    alt = re.compile(
        r'<fragment\b[^>]*\bandroid:name="' + re.escape(fragment_fqcn) + r'"[^>]*\bandroid:id="@\+id/([^"]+)"',
        re.DOTALL,
    )
    match = alt.search(text)
    if match:
        return match.group(1)
    simple = fragment_fqcn.rsplit(".", 1)[-1]
    return simple[:1].lower() + simple[1:]

def _build_hilt_nav_test_graph(
    *,
    package: str,
    fragment_fqcn: str,
    start_destination_id: str,
    deeplink_uris: list[str],
) -> str:
    deeplink_tpl = (_TEMPLATES_DIR / "hilt_nav_test_graph_deeplink_fragment.xml").read_text(encoding="utf-8")
    deeplink_blocks = []
    for index, uri in enumerate(deeplink_uris):
        deeplink_blocks.append(
            deeplink_tpl.format(index=index, package=package, deeplink_uri=uri).rstrip()
        )
    graph_tpl = (_TEMPLATES_DIR / "hilt_nav_test_graph.xml").read_text(encoding="utf-8")
    return graph_tpl.format(
        start_destination_id=start_destination_id,
        fragment_fqcn=fragment_fqcn,
        fragment_simple_name=fragment_fqcn.rsplit(".", 1)[-1],
        deeplink_destinations=("\n" + "\n".join(deeplink_blocks) + "\n") if deeplink_blocks else "\n",
    )

def _ensure_nav_host_harness(
    module_dir: str,
    source_file_path: str,
    source_code: str,
    categories: frozenset[str] | set[str],
) -> list[str]:
    """Create NavHost androidTest harness (layout + HiltNavTestActivity + manifest entry)."""
    if not _needs_nav_host(categories, source_code, module_dir=module_dir, source_file_path=source_file_path):
        return []

    fqcn = source_kotlin_fqcn(source_file_path)
    nav_graph_stem = find_nav_graph_for_fragment(module_dir, fqcn) if fqcn else ""
    if not nav_graph_stem:
        return []

    package = _android_test_host_package(module_dir)
    if not package:
        return []

    changes: list[str] = []
    rel_java = Path(*package.split("."))
    deeplink_uris = _extract_deeplink_uris(source_code)
    start_destination_id = _fragment_destination_id(module_dir, nav_graph_stem, fqcn)

    graph_path = Path(module_dir) / "src" / "androidTest" / "res" / "navigation" / "hilt_nav_test_graph.xml"
    if not graph_path.is_file():
        graph_path.parent.mkdir(parents=True, exist_ok=True)
        graph_path.write_text(
            _build_hilt_nav_test_graph(
                package=package,
                fragment_fqcn=fqcn,
                start_destination_id=start_destination_id,
                deeplink_uris=deeplink_uris,
            ),
            encoding="utf-8",
        )
        rel = graph_path.relative_to(module_dir)
        changes.append(f"created {rel.as_posix()}")
        log_message(f"🔧 Instrumented bootstrap: created {graph_path}", category="gradle")

    if deeplink_uris:
        stub_path = Path(module_dir) / "src" / "androidTest" / "java" / rel_java / "StubHomeFragment.kt"
        if _write_template_if_missing(stub_path, "StubHomeFragment.kt", package=package):
            rel = stub_path.relative_to(module_dir)
            changes.append(f"created {rel.as_posix()}")
            log_message(f"🔧 Instrumented bootstrap: created {stub_path}", category="gradle")

    layout_path = Path(module_dir) / "src" / "androidTest" / "res" / "layout" / "hilt_nav_test_activity.xml"
    if _write_template_if_missing(layout_path, "hilt_nav_test_activity.xml"):
        rel = layout_path.relative_to(module_dir)
        changes.append(f"created {rel.as_posix()}")
        log_message(f"🔧 Instrumented bootstrap: created {layout_path}", category="gradle")

    activity_path = Path(module_dir) / "src" / "androidTest" / "java" / rel_java / "HiltNavTestActivity.kt"
    if _write_template_if_missing(
        activity_path,
        "HiltNavTestActivity.kt",
        package=package,
        r_import=f"{_module_namespace(module_dir, package)}.test.R",
    ):
        rel = activity_path.relative_to(module_dir)
        changes.append(f"created {rel.as_posix()}")
        log_message(f"🔧 Instrumented bootstrap: created {activity_path}", category="gradle")

    manifest_path = Path(module_dir) / "src" / "androidTest" / "AndroidManifest.xml"
    nav_activity_fqcn = f"{package}.HiltNavTestActivity"
    if manifest_path.is_file() and _merge_manifest_activity(manifest_path, nav_activity_fqcn):
        changes.append(f"declared {nav_activity_fqcn} in androidTest manifest")
        log_message(
            f"🔧 Instrumented bootstrap: added {nav_activity_fqcn} to manifest",
            category="gradle",
        )

    if source_requires_main_activity_delegate(source_code):
        plain_path = Path(module_dir) / "src" / "androidTest" / "java" / rel_java / "PlainHiltActivity.kt"
        if _write_template_if_missing(plain_path, "PlainHiltActivity.kt", package=package):
            rel = plain_path.relative_to(module_dir)
            changes.append(f"created {rel.as_posix()}")
            log_message(f"🔧 Instrumented bootstrap: created {plain_path}", category="gradle")
        plain_fqcn = f"{package}.PlainHiltActivity"
        if manifest_path.is_file() and _merge_manifest_activity(manifest_path, plain_fqcn):
            changes.append(f"declared {plain_fqcn} in androidTest manifest")
            log_message(
                f"🔧 Instrumented bootstrap: added {plain_fqcn} to manifest",
                category="gradle",
            )

    return changes

def ensure_instrumented_module_ready(
    *,
    module_dir: str,
    source_file_path: str,
    source_root: str,
    source_code: str = "",
    categories: frozenset[str] | set[str] | None = None,
    output_base_directory: str | None = None,
) -> BootstrapResult:
    """Patch Gradle and create androidTest dirs when module is not instrumented-ready."""
    tags = frozenset(categories or ())
    warnings: list[str] = []
    changes: list[str] = []

    ready_before, missing = module_ready_for_instrumented_tests(source_file_path)

    gradle_path, gradle_content = read_owning_module_gradle(source_file_path)
    if not ready_before:
        if not gradle_path or not gradle_content.strip():
            msg = f"No build.gradle found for instrumented bootstrap ({module_dir})"
            log_message(f"⚠️ {msg}", category="warning")
            warnings.append(msg)
        else:
            needs_hilt = _needs_hilt(tags, source_code)
            needs_mockk = _needs_mockk(tags, source_code)
            needs_espresso = _needs_espresso(tags)
            _, gradle_changes = _patch_gradle_file(
                gradle_path,
                gradle_content,
                needs_hilt=needs_hilt,
                needs_mockk=needs_mockk,
                needs_espresso=needs_espresso,
            )
            for change in gradle_changes:
                log_message(f"🔧 Instrumented bootstrap: {change}", category="gradle")
            changes.extend(gradle_changes)

    manifest_changes = _ensure_android_test_manifest(module_dir, source_code, tags)
    for change in manifest_changes:
        if change not in changes:
            changes.append(change)

    if _needs_hilt(tags, source_code):
        nav_changes = _ensure_nav_host_harness(module_dir, source_file_path, source_code, tags)
        for change in nav_changes:
            if change not in changes:
                changes.append(change)

    _ensure_android_test_package_dir(
        source_file_path, source_root, output_base_directory=output_base_directory,
    )

    ready_after, still_missing = module_ready_for_instrumented_tests(source_file_path)
    if not ready_after:
        detail = ", ".join(still_missing[:3]) if still_missing else "unknown"
        msg = f"Module still not instrumented-ready after bootstrap ({detail})"
        log_message(f"⚠️ {msg}", category="warning")
        warnings.append(msg)
    elif (missing and not ready_before) or manifest_changes:
        log_message("✅ Instrumented bootstrap: module now instrumented-ready.", category="info")

    from UnitTest_gen.kotlin.project import instrumented_manifest_missing

    if instrumented_manifest_missing(source_file_path):
        log_message(
            "⚠️ Instrumented bootstrap: androidTest AndroidManifest.xml still missing "
            "(Hilt androidTest deps present).",
            category="warning",
        )
        warnings.append("androidTest AndroidManifest.xml missing")

    return BootstrapResult(
        ready=ready_after,
        patched=bool(changes),
        changes=tuple(changes),
        warnings=tuple(warnings),
    )

def _ensure_android_test_package_dir(
    source_file_path: str,
    source_root: str,
    *,
    output_base_directory: str | None = None,
) -> None:
    instr_path = derive_instrumented_test_output_path(
        source_file_path=source_file_path,
        source_root=source_root,
        output_base_directory=output_base_directory,
    )
    Path(instr_path).parent.mkdir(parents=True, exist_ok=True)
