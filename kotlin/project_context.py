# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Adapts project layout, dependencies, vector indexing, and context retrieval for Kotlin.
"""Project/module discovery, Gradle module inspection, indexing, and context helpers."""

import os
import re
import subprocess
import difflib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from UnitTest_gen.core.rag_context import ContextItem, RetrievalQuery, format_context_blocks, normalize_terms, rank_context_items
from UnitTest_gen.core.logging_utils import log_message
from UnitTest_gen.core.model_runtime import (
    embeddings_enabled,
    get_vector_cache,
    get_vector_cache_file,
    get_vector,
    is_embedding_server_available,
)
from UnitTest_gen.core.vector_index import build_vector_index
from UnitTest_gen.kotlin.prompt_constants import REQUIRED_DEPENDENCIES, SAFE_KOVER_EXCLUDE_CLASSES, UNSAFE_KOVER_EXCLUDE_CLASSES
from UnitTest_gen.core.server_manager import LocalServerManager
from UnitTest_gen.kotlin.kotlin_analysis import (
    SourceProfile,
    classify_source,
    extract_package_name,
    kotlin_identifier_set,
    kotlin_imports,
    kotlin_package_name,
    kotlin_top_level_class_name,
    source_declares_android_fragment,
    source_rule_categories,
    summarize_kotlin_signature_file,
    summarize_kotlin_source_signatures,
)
from UnitTest_gen.kotlin.static_analysis import analyze_kotlin_test_code
from UnitTest_gen.core.pipeline_config import get_config
from UnitTest_gen.kotlin.strategy_contracts import (
    canonical_hilt_fragment_fixture_template as _canonical_hilt_fragment_fixture_template,
)

_ANDROID_R_REF_PATTERN = re.compile(
    r"\b(?P<prefix>android\.)?R\.(?P<type>id|layout|string|drawable|navigation|color|dimen|menu|raw|style|attr)"
    r"\.(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
)
_ANDROID_XML_ID_PATTERN = re.compile(r'android:id\s*=\s*"@\+?id/([^"]+)"')


@dataclass(frozen=True)
class VerifiedAndroidResource:
    resource_type: str
    name: str
    variant: str
    declaration_path: str
    layout_name: str | None = None


def android_id_to_view_binding_field(resource_id: str) -> str:
    head, *tail = resource_id.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail if part)


def _resource_variant(path: Path) -> str:
    normalized = path.parent.as_posix()
    marker = "/runtime_symbol_list/"
    return normalized.split(marker, 1)[1].split("/", 1)[0] if marker in normalized else path.parent.name


@lru_cache(maxsize=32)
def load_verified_android_resource_index(module_dir: str) -> dict[tuple[str, str], VerifiedAndroidResource]:
    module = Path(module_dir)
    index: dict[tuple[str, str], VerifiedAndroidResource] = {}
    for path in module.glob("src/*/res/**/*"):
        if not path.is_file():
            continue
        resource_type = path.parent.name.split("-", 1)[0]
        if resource_type == "layout":
            index[("layout", path.stem)] = VerifiedAndroidResource("layout", path.stem, "source", str(path))
            text = path.read_text(encoding="utf-8", errors="replace")
            for name in _ANDROID_XML_ID_PATTERN.findall(text):
                index[("id", name)] = VerifiedAndroidResource("id", name, "source", str(path), path.stem)
        elif resource_type in {"drawable", "navigation", "menu", "raw"}:
            index[(resource_type, path.stem)] = VerifiedAndroidResource(resource_type, path.stem, "source", str(path))
        elif resource_type == "values":
            text = path.read_text(encoding="utf-8", errors="replace")
            for kind, name in re.findall(r'<(string|color|dimen|style|item)\b[^>]*\bname="([^"]+)"', text):
                kind = "attr" if kind == "item" and 'type="attr"' in text else kind
                index[(kind, name)] = VerifiedAndroidResource(kind, name, "source", str(path))

    rtxt_files = sorted(
        module.glob("build/intermediates/**/R.txt"),
        key=lambda path: path.stat().st_mtime,
    )
    for path in rtxt_files:
        variant = _resource_variant(path)
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[0] == "int":
                resource_type, name = parts[1], parts[2]
                index[(resource_type, name)] = VerifiedAndroidResource(resource_type, name, variant, str(path))
    return index


def lookup_verified_android_resource(index, resource_type: str, name: str):
    return (index or {}).get((resource_type, name))


def find_single_same_type_resource_match(index, resource_type: str, name: str):
    candidates = sorted(candidate for kind, candidate in (index or {}) if kind == resource_type)
    normalized_name = re.sub(r"_", "", name).lower()
    normalized_matches = [candidate for candidate in candidates if re.sub(r"_", "", candidate).lower() == normalized_name]
    if len(normalized_matches) == 1:
        return index[(resource_type, normalized_matches[0])]
    matches = difflib.get_close_matches(name, candidates, n=2, cutoff=0.55)
    return index[(resource_type, matches[0])] if len(matches) == 1 else None


def apply_verified_android_resource_corrections(test_code: str, output_file_path: str) -> str:
    project_root = find_project_root_for_path(output_file_path)
    if not project_root:
        return test_code
    module_dir = find_owning_module_dir(project_root, output_file_path)
    index = load_verified_android_resource_index(module_dir)

    def replace(match):
        if match.group("prefix") or lookup_verified_android_resource(index, match.group("type"), match.group("name")):
            return match.group(0)
        candidate = find_single_same_type_resource_match(index, match.group("type"), match.group("name"))
        return f"R.{candidate.resource_type}.{candidate.name}" if candidate else match.group(0)

    return _ANDROID_R_REF_PATTERN.sub(replace, test_code or "")


def collect_verified_android_resource_context(project_root: str, target_path: str, source_code: str) -> str:
    module_dir = find_owning_module_dir(project_root, target_path)
    index = load_verified_android_resource_index(module_dir)
    lines = []
    for field in sorted(set(re.findall(r"\bbinding\.([A-Za-z_][A-Za-z0-9_]*)", source_code or ""))):
        matches = [item for (kind, _), item in index.items() if kind == "id" and android_id_to_view_binding_field(item.name) == field]
        if len(matches) == 1:
            lines.append(f"binding.{field} -> R.id.{matches[0].name} ({matches[0].declaration_path})")
    for match in _ANDROID_R_REF_PATTERN.finditer(source_code or ""):
        if match.group("prefix"):
            continue
        item = lookup_verified_android_resource(index, match.group("type"), match.group("name"))
        if item:
            lines.append(f"R.{item.resource_type}.{item.name} ({item.declaration_path})")
    if not lines:
        return ""
    return "### VERIFIED ANDROID RESOURCE CONTEXT\nUse only the exact verified resource names below.\nDo not derive resource names from ViewBinding naming conventions.\n" + "\n".join(dict.fromkeys(lines))


def collect_android_resource_repair_context(
    project_root: str,
    output_file_path: str,
    source_code: str,
    focused_error_block: str,
) -> str:
    module_dir = find_owning_module_dir(project_root, output_file_path)
    index = load_verified_android_resource_index(module_dir)
    missing = re.findall(r"R\.(id|layout|string|drawable|navigation|color|dimen|menu|raw|style|attr)\.([A-Za-z_][A-Za-z0-9_]*)", focused_error_block or "")
    missing.extend(("id", name) for name in re.findall(r"Unresolved reference:?\s*'?([A-Za-z_][A-Za-z0-9_]*)", focused_error_block or ""))
    lines = []
    for resource_type, name in missing:
        candidate = find_single_same_type_resource_match(index, resource_type, name)
        if not candidate:
            continue
        lines.append(f"Use `R.{candidate.resource_type}.{candidate.name}` from {candidate.declaration_path}.")
        path = Path(candidate.declaration_path)
        if path.suffix == ".xml" and path.is_file():
            xml_line = next((line.strip() for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if candidate.name in line), "")
            if xml_line:
                lines.append(xml_line)
    context = collect_verified_android_resource_context(project_root, output_file_path, source_code)
    return "\n".join([context, *lines]).strip()

def _extract_imports(code: str) -> set[str]:
    return {
        match.group(1).strip()
        for match in re.finditer(r"^\s*import\s+(.+?)\s*$", code or "", re.MULTILINE)
    }

def find_owning_module_dir(project_root: str, target_path: str) -> str:
    """Find the Gradle module directory containing the target file.
    
    Walks up from target_path searching for build.gradle.kts or build.gradle files
    to determine module boundaries. Returns the first directory containing a Gradle
    build file.
    
    Args:
        project_root: Root directory of the Android project
        target_path: File or directory to locate within a module
        
    Returns:
        Absolute path to the module directory. Returns project_root if no module
        build file is found.
        
    Example:
        find_owning_module_dir('/project', '/project/feature/login/src/LoginActivity.kt')
        -> '/project/feature/login'
    """
    current = Path(target_path).resolve()
    if current.is_file():
        current = current.parent

    project_root_path = Path(project_root).resolve()

    while True:
        if (current / "build.gradle.kts").exists() or (current / "build.gradle").exists():
            return str(current)

        if current == project_root_path or current.parent == current:
            return project_root

        current = current.parent

def module_path_for_dir(project_root: str, module_dir: str) -> str:
    rel = os.path.relpath(module_dir, project_root)
    if rel == ".":
        return ""
    return ":" + rel.replace(os.sep, ":")

def find_project_root_for_path(path: str) -> str | None:
    current = Path(path).resolve()
    if current.is_file():
        current = current.parent

    for parent in [current, *current.parents]:
        if (parent / "settings.gradle.kts").exists() or (parent / "settings.gradle").exists():
            return str(parent)

    return None


def resolve_project_path(path_value: str, project_root: str) -> str:
    path = Path(path_value or "")
    if path.is_absolute():
        return str(path.resolve())
    root = Path(project_root or ".").resolve()
    return str((root / path_value).resolve())


def find_gradle_project_root(
    start_path: str,
    *,
    markers: tuple[str, ...] = ("gradlew", "settings.gradle", "settings.gradle.kts"),
) -> str | None:
    path = Path(start_path or "")
    if not path.is_absolute():
        path = Path.cwd() / path
    start = path if path.is_dir() else path.parent
    for candidate in (start, *start.parents):
        if any((candidate / marker).exists() for marker in markers):
            return str(candidate.resolve())
    return None


def project_hilt_version(project_root: str) -> str:
    catalog_path = Path(project_root) / "gradle" / "libs.versions.toml"
    if not catalog_path.is_file():
        return ""
    try:
        text = catalog_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    match = re.search(r'(?m)^\s*hilt\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else ""


def explicit_hilt_testing_version(gradle_content: str) -> str:
    match = re.search(r"hilt-android-testing:([^\"')\s]+)", gradle_content or "")
    return match.group(1) if match else ""


def parse_module_min_sdk(module_dir: str) -> int | None:
    return parse_module_android_sdk_value(
        module_dir,
        [
            r"\bminSdk\s*=\s*(\d+)\b",
            r"\bminSdkVersion\s*=\s*(\d+)\b",
            r"\bminSdkVersion\s+(\d+)\b",
            r"\bminSdk\s*\(\s*(\d+)\s*\)",
        ],
    )

def parse_module_compile_sdk(module_dir: str) -> int | None:
    return parse_module_android_sdk_value(
        module_dir,
        [
            r"\bcompileSdk\s*=\s*(\d+)\b",
            r"\bcompileSdkVersion\s*=\s*(\d+)\b",
            r"\bcompileSdkVersion\s+(\d+)\b",
            r"\bcompileSdk\s*\(\s*(\d+)\s*\)",
        ],
    )

def parse_module_android_sdk_value(module_dir: str, patterns: list[str]) -> int | None:
    for build_file_name in ("build.gradle.kts", "build.gradle"):
        build_file_path = os.path.join(module_dir, build_file_name)
        if not os.path.exists(build_file_path):
            continue

        try:
            content = Path(build_file_path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        for pattern in patterns:
            match = re.search(pattern, content)
            if match:
                return int(match.group(1))

    return None

def android_sdk_roots() -> list[str]:
    roots = []
    for env_name in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        value = os.environ.get(env_name)
        if value and value not in roots:
            roots.append(value)

    for candidate in (
        str(Path.home() / "Android" / "Sdk"),
        "/opt/android-sdk",
        "/usr/lib/android-sdk",
    ):
        if candidate not in roots:
            roots.append(candidate)

    return roots

def is_android_platform_installed(api_level: int) -> bool:
    return any(
        os.path.isdir(os.path.join(root, "platforms", f"android-{api_level}"))
        for root in android_sdk_roots()
    )

def installed_android_platform_versions() -> list[int]:
    versions = set()
    for sdk_root in android_sdk_roots():
        platform_root = os.path.join(sdk_root, "platforms")
        if not os.path.isdir(platform_root):
            continue
        try:
            for name in os.listdir(platform_root):
                match = re.fullmatch(r"android-(\d+)", name)
                if match:
                    versions.add(int(match.group(1)))
        except OSError:
            continue

    return sorted(versions)

def choose_robolectric_sdk_for_module(min_sdk: int, compile_sdk: int | None = None) -> int | None:
    for api_level in installed_android_platform_versions():
        if api_level >= min_sdk and (compile_sdk is None or api_level <= compile_sdk):
            return api_level
    return None

def selected_robolectric_sdk_for_output(output_file_path: str | None) -> int | None:
    if not output_file_path:
        return None
    project_root = find_project_root_for_path(output_file_path)
    module_dir = find_owning_module_dir(project_root, output_file_path) if project_root else os.path.dirname(output_file_path)
    min_sdk = parse_module_min_sdk(module_dir)
    compile_sdk = parse_module_compile_sdk(module_dir)
    if min_sdk is None:
        return None
    return choose_robolectric_sdk_for_module(min_sdk, compile_sdk)

def module_sdk_test_guidance(output_file_path: str | None) -> str:
    if not output_file_path:
        return "Module SDK guidance: output file path unavailable; use module default SDK and avoid hard-coded low Robolectric SDKs."

    project_root = find_project_root_for_path(output_file_path)
    module_dir = find_owning_module_dir(project_root, output_file_path) if project_root else os.path.dirname(output_file_path)
    min_sdk = parse_module_min_sdk(module_dir)
    compile_sdk = parse_module_compile_sdk(module_dir)

    if min_sdk is None:
        return (
            "Module SDK guidance: minSdk was not found in the owning module Gradle file. "
            "Use the module default Robolectric SDK or a verified SDK at/above the manifest requirements."
        )

    selected_sdk = choose_robolectric_sdk_for_module(min_sdk, compile_sdk)
    if selected_sdk is None:
        upper_bound_text = f" and <= compileSdk {compile_sdk}" if compile_sdk is not None else ""
        log_message(
            f"⚠️ No local Android SDK platform >= module minSdk {min_sdk}{upper_bound_text} was found. "
            f"Install one in that range, for example sdkmanager \"platforms;android-{min_sdk}\".",
            category="warning",
        )
        return (
            f"Module SDK guidance: owning module minSdk is {min_sdk}. "
            + (f"compileSdk is {compile_sdk}. " if compile_sdk is not None else "")
            + f"Robolectric @Config sdk values must be >= {min_sdk}"
            + (f" and <= {compile_sdk}" if compile_sdk is not None else "")
            + f". No local Android platform in that range was found; install one with sdkmanager \"platforms;android-{min_sdk}\"."
        )

    if selected_sdk > min_sdk:
        log_message(
            f"ℹ️ Android SDK platform android-{min_sdk} is not installed locally; "
            f"using installed higher platform android-{selected_sdk} for Robolectric @Config guidance.",
            category="info",
        )

    return (
        f"Module SDK guidance: owning module minSdk is {min_sdk}. "
        + (f"compileSdk is {compile_sdk}. " if compile_sdk is not None else "")
        + f"Robolectric @Config sdk values must be >= {min_sdk}"
        + (f" and <= {compile_sdk}" if compile_sdk is not None else "")
        + f"; prefer @Config(sdk = [{selected_sdk}]) "
        "when an explicit SDK is needed, or omit @Config when the module default is enough. "
        f"Selected SDK {selected_sdk} is installed locally and satisfies the module SDK bounds."
    )

def default_gradle_tasks_for_target(project_root: str, target_path: str):
    module_dir = find_owning_module_dir(project_root, target_path)
    module_path = module_path_for_dir(project_root, module_dir)

    if not module_path:
        return ["testDevDebugUnitTest"]

    return [f"{module_path}:testDevDebugUnitTest"]

def read_owning_module_gradle(output_file_path: str | None) -> tuple[str, str]:
    if not output_file_path:
        return "", ""
    project_root = find_project_root_for_path(output_file_path)
    module_dir = find_owning_module_dir(project_root, output_file_path) if project_root else os.path.dirname(output_file_path)
    for gradle_name in ("build.gradle.kts", "build.gradle"):
        gradle_path = os.path.join(module_dir, gradle_name)
        if os.path.exists(gradle_path):
            try:
                with open(gradle_path, "r", encoding="utf-8") as f:
                    return gradle_path, f.read()
            except OSError:
                return gradle_path, ""
    return "", ""








def owning_module_dir_for_output(output_file_path: str | None) -> str:
    if not output_file_path:
        return ""
    project_root = find_project_root_for_path(output_file_path)
    return find_owning_module_dir(project_root, output_file_path) if project_root else os.path.dirname(output_file_path)

def module_has_manifest_declared_hilt_test_activity(output_file_path: str | None) -> bool:
    module_dir = owning_module_dir_for_output(output_file_path)
    if not module_dir:
        return False

    manifest_path = os.path.join(module_dir, "src", "test", "AndroidManifest.xml")
    if not os.path.exists(manifest_path):
        return False

    try:
        manifest_content = Path(manifest_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False

    if "HiltTestActivity" not in manifest_content:
        return False

    test_src_root = os.path.join(module_dir, "src", "test", "java")
    for candidate in Path(test_src_root).rglob("HiltTestActivity.kt") if os.path.isdir(test_src_root) else []:
        try:
            candidate_content = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "@AndroidEntryPoint" in candidate_content and "class HiltTestActivity" in candidate_content:
            return True

    return False

def parse_module_namespace(module_dir: str) -> str:
    for gradle_name in ("build.gradle.kts", "build.gradle"):
        gradle_path = os.path.join(module_dir, gradle_name)
        if not os.path.exists(gradle_path):
            continue
        try:
            gradle_content = Path(gradle_path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        match = re.search(r'\bnamespace\s*=\s*"([^"]+)"', gradle_content)
        if match:
            return match.group(1)
        match = re.search(r"\bnamespace\s+['\"]([^'\"]+)['\"]", gradle_content)
        if match:
            return match.group(1)
    return ""

def module_has_hilt_robolectric_gradle_prereqs(output_file_path: str | None, source_code: str = "") -> tuple[bool, list[str]]:
    _, gradle_content = read_owning_module_gradle(output_file_path)
    checks = {
        "hilt-android-testing": "hilt-android-testing" in gradle_content,
        "hilt test compiler": (
            ("kspTest(" in gradle_content or "kaptTest(" in gradle_content)
            and ("hilt.android.compiler" in gradle_content or "hilt-android-compiler" in gradle_content)
        ),
        "Robolectric": "robolectric" in gradle_content.lower(),
        "fragment-testing": "fragment-testing" in gradle_content,
        "Android test core": "androidx.test:core" in gradle_content or "androidx-test-core" in gradle_content,
    }
    if "findNavController" in (source_code or ""):
        checks["navigation-testing"] = "navigation-testing" in gradle_content

    missing = [name for name, present in checks.items() if not present]
    return not missing, missing

def ensure_hilt_test_activity_support(output_file_path: str | None, source_code: str = "") -> bool:
    """Create module-local Hilt host Activity support for Hilt Fragment JVM lifecycle tests."""
    if not (
        "@AndroidEntryPoint" in (source_code or "")
        and source_declares_android_fragment(source_code or "")
    ):
        return False

    gradle_ready, missing_gradle = module_has_hilt_robolectric_gradle_prereqs(output_file_path, source_code)
    if not gradle_ready:
        log_message(
            "ℹ️ Skipping HiltTestActivity auto-setup because Gradle prerequisites are missing: "
            + ", ".join(missing_gradle),
            category="info",
        )
        return False

    module_dir = owning_module_dir_for_output(output_file_path)
    if not module_dir:
        return False

    changed = False
    manifest_path = Path(module_dir) / "src" / "test" / "AndroidManifest.xml"
    if not manifest_path.exists():
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<manifest xmlns:android="http://schemas.android.com/apk/res/android">\n\n'
            '    <application>\n'
            '        <activity\n'
            '            android:name=".HiltTestActivity"\n'
            '            android:theme="@style/Theme.AppCompat"\n'
            '            android:exported="false" />\n'
            '    </application>\n\n'
            '</manifest>\n',
            encoding="utf-8",
        )
        changed = True
        log_message(f"🧩 Created Hilt test manifest: {manifest_path}", category="fix")
    else:
        try:
            manifest_content = manifest_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            manifest_content = ""
        if "HiltTestActivity" not in manifest_content:
            activity_entry = (
                '        <activity\n'
                '            android:name=".HiltTestActivity"\n'
                '            android:theme="@style/Theme.AppCompat"\n'
                '            android:exported="false" />\n'
            )
            if "</application>" in manifest_content:
                manifest_content = manifest_content.replace("</application>", activity_entry + "    </application>", 1)
            elif "</manifest>" in manifest_content:
                manifest_content = manifest_content.replace(
                    "</manifest>",
                    "    <application>\n" + activity_entry + "    </application>\n\n</manifest>",
                    1,
                )
            else:
                manifest_content = (
                    '<?xml version="1.0" encoding="utf-8"?>\n'
                    '<manifest xmlns:android="http://schemas.android.com/apk/res/android">\n\n'
                    '    <application>\n'
                    + activity_entry
                    + '    </application>\n\n'
                    '</manifest>\n'
                )
            manifest_path.write_text(manifest_content, encoding="utf-8")
            changed = True
            log_message(f"🧩 Added HiltTestActivity to test manifest: {manifest_path}", category="fix")
        else:
            updated_manifest_content = re.sub(
                r'(<activity\b(?=[^>]*android:name="\.HiltTestActivity")(?![^>]*android:theme=)(?:[^>]|\n)*?)\s*/>',
                r'\1\n            android:theme="@style/Theme.AppCompat" />',
                manifest_content,
                count=1,
            )
            if updated_manifest_content != manifest_content:
                manifest_path.write_text(updated_manifest_content, encoding="utf-8")
                changed = True
                log_message(f"🧩 Added AppCompat theme to HiltTestActivity manifest entry: {manifest_path}", category="fix")

    module_namespace = parse_module_namespace(module_dir) or kotlin_package_name(source_code or "")
    if not module_namespace:
        log_message("⚠️ Could not resolve module namespace for HiltTestActivity auto-setup.", category="warning")
        return changed

    activity_path = Path(module_dir) / "src" / "test" / "java" / Path(*module_namespace.split(".")) / "HiltTestActivity.kt"
    desired_activity_content = (
        f"package {module_namespace}\n\n"
        "import androidx.appcompat.app.AppCompatActivity\n"
        "import dagger.hilt.android.AndroidEntryPoint\n\n"
        "@AndroidEntryPoint\n"
        "class HiltTestActivity : AppCompatActivity()\n"
    )
    if not activity_path.exists():
        activity_path.parent.mkdir(parents=True, exist_ok=True)
        activity_path.write_text(desired_activity_content, encoding="utf-8")
        changed = True
        log_message(f"🧩 Created Hilt test Activity: {activity_path}", category="fix")
    else:
        try:
            activity_content = activity_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            activity_content = ""
        if "class HiltTestActivity" in activity_content and "@AndroidEntryPoint" not in activity_content:
            activity_path.write_text(desired_activity_content, encoding="utf-8")
            changed = True
            log_message(f"🧩 Updated Hilt test Activity annotation: {activity_path}", category="fix")

    return changed

def module_has_hilt_robolectric_fragment_support(output_file_path: str | None, source_code: str = "") -> tuple[bool, list[str]]:
    _, missing = module_has_hilt_robolectric_gradle_prereqs(output_file_path, source_code)
    if not module_has_manifest_declared_hilt_test_activity(output_file_path):
        missing.append("manifest-declared HiltTestActivity")
    return not missing, missing


def module_has_shared_test_hilt_binding(output_file_path: str | None, binding_fqcn: str) -> bool:
    module_dir = owning_module_dir_for_output(output_file_path)
    if not module_dir or not binding_fqcn:
        return False

    test_java_dir = Path(module_dir) / "src" / "test" / "java"
    if not test_java_dir.exists():
        return False

    simple_name = binding_fqcn.rsplit(".", 1)[-1]
    for test_file in test_java_dir.rglob("*.kt"):
        try:
            content = test_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "@BindValue" in content:
            continue
        if "@Module" not in content or "@InstallIn" not in content:
            continue
        if binding_fqcn in content or re.search(rf"\b{re.escape(simple_name)}\b", content):
            return True
    return False


def build_hilt_fragment_lifecycle_strategy(output_file_path: str | None, source_code: str) -> str:
    supported, missing = module_has_hilt_robolectric_fragment_support(output_file_path, source_code)
    if supported:
        return (
            "Full Hilt/Robolectric fragment lifecycle strategy is enabled for this module. "
            "Use @HiltAndroidTest, HiltAndroidRule, @Config(application = HiltTestApplication::class), "
            "a graph that is already closed by verified production/test bindings, and the manifest-declared "
            "top-level @AndroidEntryPoint HiltTestActivity launched directly with ActivityScenario/Robolectric. "
            "Use the canonical fixture template; do not let the model invent ActivityController, CarUi, or navigation setup order. "
            "If source navigates by NavDeepLinkRequest to destinations owned by another feature graph, use a mocked NavController installed with Navigation.setViewNavController and capture/verify the emitted NavDeepLinkRequest URI instead of requiring the current module graph to contain every destination. "
            "Do not generate a nested TestActivity and do not use plain launchFragmentInContainer/FragmentScenario.launchInContainer for @AndroidEntryPoint lifecycle tests.\n\n"
            + _canonical_hilt_fragment_fixture_template()
        )

    return (
        "Full Hilt/Robolectric fragment lifecycle strategy is not enabled for this module because these prerequisites "
        f"are missing: {', '.join(missing) if missing else 'unknown'}. "
        "Generate direct public-contract tests with observable assertions that avoid fragment attachment, or add the missing Gradle/test-manifest support first."
    )

def kts_string_literal(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace("$", "\\$").replace('"', '\\"') + '"'

def find_block_end_line(lines, start_index):
    depth = 0
    seen_open = False

    for index in range(start_index, len(lines)):
        depth += lines[index].count("{")
        if "{" in lines[index]:
            seen_open = True
        depth -= lines[index].count("}")

        if seen_open and depth == 0:
            return index

    return -1

def insert_into_block_before_close(lines, block_start_index, snippet_lines):
    block_end_index = find_block_end_line(lines, block_start_index)
    if block_end_index == -1:
        return False

    lines[block_end_index:block_end_index] = snippet_lines
    return True

def ensure_android_unit_test_resources(target_gradle: str):
    if not target_gradle.endswith(".kts"):
        return

    with open(target_gradle, "r", encoding="utf-8") as f:
        content = f.read()

    if "android {" not in content:
        return

    original_content = content
    lines = content.splitlines()
    android_index = next((i for i, line in enumerate(lines) if line.strip().startswith("android {")), -1)

    if android_index == -1:
        return

    if "unitTests.isIncludeAndroidResources" not in content:
        inserted = insert_into_block_before_close(
            lines,
            android_index,
            [
                "",
                "    testOptions {",
                "        unitTests.isIncludeAndroidResources = true",
                "    }",
            ],
        )
        if inserted:
            content = "\n".join(lines)

    namespace_match = re.search(r'namespace\s*=\s*"([^"]+)"', content)
    appauth_redirect_scheme = namespace_match.group(1) if namespace_match else "com.example.test"
    needs_appauth_placeholder = "appAuthRedirectScheme" not in content
    if needs_appauth_placeholder:
        lines = content.splitlines()
        default_config_index = next(
            (i for i, line in enumerate(lines) if line.strip().startswith("defaultConfig {")),
            -1,
        )
        if default_config_index != -1:
            insert_into_block_before_close(
                lines,
                default_config_index,
                [f'        manifestPlaceholders["appAuthRedirectScheme"] = "{appauth_redirect_scheme}"'],
            )
            content = "\n".join(lines)

    if content != original_content:
        with open(target_gradle, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"🔧 Enabled Android local-test resources/placeholders in {target_gradle}")

def ensure_kover_filters(target_gradle: str):
    if not target_gradle.endswith(".kts"):
        return

    with open(target_gradle, "r", encoding="utf-8") as f:
        content = f.read()

    if "id(\"org.jetbrains.kotlinx.kover\")" not in content and "kover {" not in content:
        return

    original_content = content

    for unsafe in UNSAFE_KOVER_EXCLUDE_CLASSES:
        literal = kts_string_literal(unsafe)
        content = re.sub(
            rf"^\s*{re.escape(literal)}\s*,?\s*\n",
            "",
            content,
            flags=re.MULTILINE,
        )

    if "kover {" not in content:
        excludes = "\n".join(
            f"                    {kts_string_literal(item)},"
            for item in SAFE_KOVER_EXCLUDE_CLASSES
        )
        content = content.rstrip() + f"""

kover {{
    reports {{
        filters {{
            excludes {{
                classes(
{excludes}
                )
            }}
        }}
    }}
}}
"""
    elif "classes(" in content:
        missing_excludes = [
            item
            for item in SAFE_KOVER_EXCLUDE_CLASSES
            if kts_string_literal(item) not in content
        ]

        if missing_excludes:
            insertion = "\n".join(
                f"                    {kts_string_literal(item)},"
                for item in missing_excludes
            )
            content = content.replace("                classes(", "                classes(\n" + insertion, 1)

    if content != original_content:
        with open(target_gradle, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"🔧 Updated Kover generated-code filters in {target_gradle}")

def check_and_add_dependencies(project_root: str, target_path: str, source_profile: SourceProfile | None = None):
    module_dir = find_owning_module_dir(project_root, target_path)
    gradle_files = [
        os.path.join(module_dir, "build.gradle.kts"),
        os.path.join(module_dir, "build.gradle"),
    ]

    target_gradle = None
    for gf in gradle_files:
        if os.path.exists(gf):
            target_gradle = gf
            break

    if not target_gradle:
        return

    ensure_android_unit_test_resources(target_gradle)
    ensure_kover_filters(target_gradle)

    with open(target_gradle, "r", encoding="utf-8") as f:
        content = f.read()

    lines = content.splitlines()
    dep_index = -1

    for i, line in enumerate(lines):
        if "dependencies {" in line:
            dep_index = i
            break

    if dep_index == -1:
        return

    modified = False

    dependency_keys = source_profile.required_dependencies if source_profile else frozenset(REQUIRED_DEPENDENCIES)
    for key, snippet in REQUIRED_DEPENDENCIES.items():
        if key not in dependency_keys:
            continue
        if key not in content and snippet not in content:
            lines.insert(dep_index + 1, f"    {snippet}")
            modified = True

    if modified:
        with open(target_gradle, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"🔧 Added missing unit-test dependencies to {target_gradle}")

def should_auto_add_missing_test_dependencies(test_code: str, groups: dict) -> bool:
    """
    Detect compile failures caused by using valid test APIs whose artifacts are
    missing from the module Gradle dependencies. These should be fixed in
    build.gradle(.kts), not by inventing replacements in the generated test file.
    """
    if not test_code or not groups:
        return False

    combined_errors = "\n".join(
        line
        for group_lines in groups.values()
        for line in group_lines
    )

    unresolved_test_package = (
        "Unresolved reference 'test'" in combined_errors
        or "Unresolved reference: test" in combined_errors
    )
    unresolved_assert_fails_with = (
        "Unresolved reference 'assertFailsWith'" in combined_errors
        or "Unresolved reference: assertFailsWith" in combined_errors
    )

    uses_coroutines_test = "kotlinx.coroutines.test." in test_code
    uses_kotlin_test = "kotlin.test." in test_code or "assertFailsWith" in test_code

    return (
        unresolved_test_package and (uses_coroutines_test or uses_kotlin_test)
    ) or (
        unresolved_assert_fails_with and uses_kotlin_test
    )

def is_indexable_kotlin_source(path: str, scan_root: str) -> bool:
    normalized = os.path.abspath(path)
    scan_root = os.path.abspath(scan_root)

    if not normalized.endswith(".kt") or normalized.endswith("Test.kt"):
        return False

    parts = set(normalized.split(os.sep))
    if any(part in parts for part in {"build", ".gradle", ".git"}):
        return False

    if scan_root.endswith(os.path.join("src", "main", "java")) or scan_root.endswith(os.path.join("src", "main", "kotlin")):
        return True

    return (
        f"{os.sep}src{os.sep}main{os.sep}java{os.sep}" in normalized
        or f"{os.sep}src{os.sep}main{os.sep}kotlin{os.sep}" in normalized
    )

def vector_cache_key_for_path(path: str) -> str:
    return os.path.abspath(path)


def build_project_index(base_scan_dir, write_cache=True):
    return build_vector_index(
        base_scan_dir,
        get_vector_cache(),
        file_filter=is_indexable_kotlin_source,
        item_key_fn=lambda path, content: kotlin_top_level_class_name(content, os.path.basename(path).removesuffix(".kt")),
        vector_fn=get_vector,
        vectors_enabled_fn=embeddings_enabled,
        write_cache=write_cache,
    )

def ensure_project_index_cache(index_scan_dir: str):
    auto_build_enabled = get_config().auto_build_vector_cache

    cache_file = get_vector_cache_file()
    if os.path.exists(cache_file):
        print(f"📦 Using existing project vector cache: {cache_file}")
    elif not auto_build_enabled:
        print(
            "📦 Project vector cache not found and auto-build is disabled. "
            f"Continuing with in-memory indexing for: {index_scan_dir}"
        )
    else:
        print(
            "📦 Project vector cache not found. "
            f"Building it now for index root: {index_scan_dir}"
        )
        print(f"📦 Cache file: {cache_file}")

    return build_project_index(index_scan_dir, write_cache=os.path.exists(cache_file) or auto_build_enabled)

def delete_vector_cache_file(reason: str):
    cache_file = get_vector_cache_file()
    if os.path.exists(cache_file):
        try:
            os.remove(cache_file)
            print(f"📦 Deleted temporary vector cache ({reason}): {cache_file}")
        except OSError as exc:
            print(f"⚠️ Could not delete vector cache {cache_file}: {exc}")

async def start_embedding_server_for_cache_if_needed(server_manager: LocalServerManager | None):
    if not server_manager or not embeddings_enabled():
        return

    if is_embedding_server_available():
        print(
            "✅ Embedding server is already reachable for vector cache build "
            "(not started by this run, so it will be left running)."
        )
        return

    server_manager.start_embedding_server()
    if not server_manager.wait_until("embedding", is_embedding_server_available):
        raise RuntimeError(
            "Auto-started embedding server did not become reachable. "
            "Check TESTGEN_EMBEDDING_BASE_URL and embedding server logs."
        )

def discover_target_sources(target_path: str, source_root: str) -> dict:
    targets = {}

    def add_source(path: str):
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        class_name = kotlin_top_level_class_name(content, os.path.basename(path).replace(".kt", ""))
        index_key = class_name
        if index_key in targets and os.path.abspath(targets[index_key]["path"]) != os.path.abspath(path):
            rel_path = os.path.relpath(path, source_root)
            index_key = f"{class_name}@{rel_path}"
        targets[index_key] = {
            "class_name": class_name,
            "path": path,
            "content": content,
            "vector": None,
        }

    if os.path.isfile(target_path):
        add_source(target_path)
        return targets

    for root, dirs, files in os.walk(target_path):
        dirs[:] = [
            directory
            for directory in dirs
            if directory not in {"build", ".gradle", ".git"}
        ]
        for file_name in files:
            path = os.path.join(root, file_name)
            if is_indexable_kotlin_source(path, source_root):
                add_source(path)

    return targets


def collect_kotlin_sources(root, exclude_names=None) -> list[str]:
    excluded = set(exclude_names or ())
    return [
        str(path)
        for path in Path(root).rglob("*.kt")
        if not ({"build", "test", "androidTest", "generated"} & set(path.parts))
        and path.name not in excluded
    ]

def find_file_data_for_path(project_index: dict, source_file_path: str):
    for index_key, data in project_index.items():
        if os.path.abspath(data["path"]) == os.path.abspath(source_file_path):
            return index_key, data
    return None, None

def _dependency_eligible(item: ContextItem, query: RetrievalQuery) -> bool:
    item_tags = normalize_terms(item.tags)
    if "universal" in item_tags:
        return True
    return bool(normalize_terms(query.tags) & item_tags)


def find_semantic_and_structural_dependencies(target_class, target_data, project_index, top_k=3):
    profile = classify_source(target_data["content"])
    effective_top_k = min(max(int(top_k or 3), 0), 3)
    items = []

    for class_name, data in project_index.items():
        if class_name == target_class:
            continue
        candidate_name = data.get("class_name", class_name)
        is_structural = candidate_name in profile.symbols or any(path.endswith(f".{candidate_name}") for path in profile.imports)
        if not is_structural:
            continue
        candidate_profile = classify_source(data["content"])
        if not (candidate_profile.categories & profile.categories):
            continue
        summary = summarize_kotlin_source_signatures(data["content"], max_lines=24)
        if len(summary) > 1400:
            summary = summary[:1400].rstrip() + "\n// ... truncated ..."
        items.append(ContextItem(class_name, summary, candidate_profile.categories, frozenset({"dependency"}), 100, data))

    ranked = rank_context_items(
        items,
        RetrievalQuery(profile.categories, profile.query_terms, "dependency"),
        limit=effective_top_k,
        eligibility_fn=_dependency_eligible,
    )
    blocks = []
    total_chars = 0
    max_chars = 4000
    for value in ranked:
        block = (
            f"// Context Dependency: {value.item.key} (Relevance Score: {value.score:.2f})\n"
            + value.item.text
        )
        if total_chars and total_chars + len(block) > max_chars:
            break
        total_chars += len(block)
        blocks.append(block)
    return format_context_blocks(blocks)


def summarize_kotlin_test_pattern(code: str, max_chars: int = 1600) -> str:
    report = analyze_kotlin_test_code(code or "")
    imports = set(report.imports)
    selected_imports = [
        item for item in imports
        if any(token in item for token in ("junit", "mockito", "robolectric", "hilt", "androidx.test", "navigation", "coroutines"))
    ][:18]
    annotations = [
        item for item in report.tests.annotations
        if any(token in item for token in ("RunWith", "Config", "HiltAndroidTest", "UninstallModules", "TestInstallIn"))
    ]
    fields = [
        " ".join(
            part
            for part in (
                "private" if field.visibility == "private" else "",
                "lateinit" if field.is_lateinit else "",
                f"val {field.name}" if "val" in field.modifiers or "var" not in field.modifiers else f"var {field.name}",
                f": {field.type_text}" if field.type_text else "",
            )
            if part
        )
        for field in report.tests.fields
    ]
    setup = [
        f"@{next(iter(function.annotations), 'Before')} fun {function.name}()"
        for function in report.tests.setup_teardown_functions
    ]
    helpers = [
        f"{'private ' if function.visibility == 'private' else ''}fun {function.name}()"
        for function in report.tests.helper_functions
    ][:12]
    tests = [function.name.strip("`") for function in report.tests.test_functions[:14]]
    markers = []
    for marker in ("ApplicationProvider", "Robolectric.buildActivity", "launchFragmentInContainer", "mockStatic", "HiltAndroidRule", "runTest", "InstantTaskExecutorRule", "Navigation.setViewNavController"):
        if marker in code:
            markers.append(marker)
    lines = []
    if selected_imports:
        lines.append("Imports: " + ", ".join(sorted(selected_imports)))
    if annotations:
        lines.append("Annotations: " + ", ".join(dict.fromkeys(item.strip() for item in annotations)))
    if fields:
        lines.append("Fields/rules: " + "; ".join(item.strip() for item in fields[:12]))
    if setup:
        lines.append("Setup/teardown: " + "; ".join(item.strip() for item in setup[:6]))
    if helpers:
        lines.append("Helper signatures: " + "; ".join(helpers))
    if tests:
        lines.append("Test intents: " + "; ".join(name.strip() for name in tests))
    if markers:
        lines.append("Framework markers: " + ", ".join(markers))
    summary = "\n".join(lines).strip()
    return summary[:max_chars].rstrip()

def _test_pattern_fixture_tags(code: str) -> set[str]:
    text = code or ""
    tags: set[str] = set()
    if "HiltAndroidRule" in text or "@HiltAndroidTest" in text or "HiltTestActivity" in text:
        tags.add("hilt_fixture")
    if "FragmentScenario" in text or "launchFragmentInContainer" in text:
        tags.add("fragment_scenario_fixture")
    if "DialogFragment" in text or ".show(" in text:
        tags.add("dialog_fixture")
    if "TestNavHostController" in text or "Navigation.setViewNavController" in text:
        tags.add("navigation_fixture")
    if "CarUi.requireToolbar" in text or "MockedStatic<CarUi>" in text or "mockStatic(CarUi" in text:
        tags.add("carui_fixture")
    if "ProgressBarController" in text or "toolbar.progressBar" in text:
        tags.add("carui_progress_fixture")
    if "activityViewModels" in text or "viewModels" in text or "ViewModelProvider" in text:
        tags.add("viewmodel_fixture")
    return tags

def collect_verified_apollo_context(project_root: str, target_path: str, source_code: str, max_symbols: int = 18) -> str:
    if "apollo" not in classify_source(source_code).categories:
        return ""
    module_dir = find_owning_module_dir(project_root, target_path)
    generated_root = os.path.join(module_dir, "build", "generated", "source", "apollo")

    if not os.path.isdir(generated_root):
        return ""

    identifiers = sorted(kotlin_identifier_set(source_code))
    symbols = [
        identifier
        for identifier in identifiers
        if identifier.endswith("Mutation") or identifier.endswith("Query")
    ]
    for import_path in kotlin_imports(source_code):
        if ".journetlog.api.type." in import_path:
            symbols.append(import_path.split(".")[-1])

    unique_symbols = []
    for symbol in symbols:
        if symbol not in unique_symbols:
            unique_symbols.append(symbol)

    blocks = []
    for symbol in unique_symbols[:max_symbols]:
        expected_name = symbol + ".kt"
        matched_path = None

        for root, _, files in os.walk(generated_root):
            if expected_name in files:
                matched_path = os.path.join(root, expected_name)
                break

        if not matched_path:
            continue

        summary = summarize_kotlin_signature_file(matched_path)
        if summary:
            blocks.append(f"// Verified generated Apollo signature: {symbol}\n{summary}")

    if not blocks:
        return ""

    return "### VERIFIED APOLLO GENERATED SIGNATURES\n" + "\n\n".join(blocks)

def find_project_root(start_path: str) -> str:
    current = os.path.abspath(start_path)

    if os.path.isfile(current):
        current = os.path.dirname(current)

    while True:
        if (
            os.path.exists(os.path.join(current, "gradlew"))
            or os.path.exists(os.path.join(current, "settings.gradle"))
            or os.path.exists(os.path.join(current, "settings.gradle.kts"))
            or os.path.exists(os.path.join(current, "build.gradle"))
            or os.path.exists(os.path.join(current, "build.gradle.kts"))
        ):
            return current

        parent = os.path.dirname(current)
        if parent == current:
            return os.getcwd()

        current = parent

def stop_gradle_daemons(project_root: str):
    """
    Stop Gradle daemons started or reused by verification tasks so long-running
    generator sessions do not leave JVMs consuming memory after completion.
    """
    gradlew = os.path.join(os.path.abspath(project_root), "gradlew")
    if not os.path.isfile(gradlew):
        print(f"⚠️ Skipping Gradle daemon stop because gradlew was not found at {gradlew}.")
        return

    print("🛑 Stopping Gradle daemons...")
    try:
        result = subprocess.run(
            [gradlew, "--stop"],
            cwd=os.path.abspath(project_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=60,
            check=False,
        )
        output = (result.stdout or "").strip()
        if output:
            print(output)
        if result.returncode != 0:
            print(f"⚠️ Gradle daemon stop exited with code {result.returncode}.")
    except subprocess.TimeoutExpired:
        print("⚠️ Gradle daemon stop timed out after 60 seconds.")
    except Exception as exc:
        print(f"⚠️ Failed to stop Gradle daemons: {exc}")

def find_source_root_for_path(target_path: str) -> str:
    normalized = os.path.abspath(target_path)
    parts = normalized.split(os.sep)

    for lang_dir in ("java", "kotlin"):
        marker = ["src", "main", lang_dir]

        for i in range(0, len(parts) - len(marker) + 1):
            if parts[i:i + len(marker)] == marker:
                return os.sep.join(parts[:i + len(marker)])

    return os.path.dirname(normalized) if os.path.isfile(normalized) else normalized

def derive_test_output_path(source_file_path: str, source_root: str, output_base_directory: str = None) -> str:
    source_file_path = os.path.abspath(source_file_path)
    source_root = os.path.abspath(source_root)

    test_file_name = os.path.basename(source_file_path).replace(".kt", "Test.kt")
    rel_dir = os.path.relpath(os.path.dirname(source_file_path), source_root)

    if output_base_directory:
        output_base_directory = os.path.abspath(output_base_directory)
        return os.path.normpath(os.path.join(output_base_directory, rel_dir, test_file_name))

    replacements = [
        (f"{os.sep}src{os.sep}main{os.sep}java{os.sep}", f"{os.sep}src{os.sep}test{os.sep}java{os.sep}"),
        (f"{os.sep}src{os.sep}main{os.sep}kotlin{os.sep}", f"{os.sep}src{os.sep}test{os.sep}kotlin{os.sep}"),
    ]

    for old, new in replacements:
        if old in source_file_path:
            return source_file_path.replace(old, new).replace(".kt", "Test.kt")

    fallback_base = os.path.join(os.getcwd(), "src", "test", "java")
    return os.path.normpath(os.path.join(fallback_base, rel_dir, test_file_name))

def derive_coverage_supplement_path(output_file_path: str) -> str:
    if output_file_path.endswith("Test.kt"):
        return output_file_path[:-len("Test.kt")] + "CoverageSupplementTest.kt"
    return output_file_path.replace(".kt", "CoverageSupplementTest.kt")

def derive_indirect_coverage_test_path(output_file_path: str) -> str:
    """
    Standalone test path for sources that have Kover coverage from other tests
    but no local/direct test file yet.
    """
    stem = os.path.basename(output_file_path).replace("Test.kt", "").replace(".kt", "")
    return os.path.join(os.path.dirname(output_file_path), f"{stem}Test.kt")

def source_root_to_test_root(source_root: str) -> str:
    source_root = os.path.abspath(source_root)
    replacements = [
        (f"{os.sep}src{os.sep}main{os.sep}java", f"{os.sep}src{os.sep}test{os.sep}java"),
        (f"{os.sep}src{os.sep}main{os.sep}kotlin", f"{os.sep}src{os.sep}test{os.sep}kotlin"),
    ]
    for old, new in replacements:
        if source_root.endswith(old):
            return source_root[: -len(old)] + new
    return os.path.join(os.path.dirname(source_root), "test", "java")

def find_existing_test_file_referencing_class(
    class_name: str,
    source_file_path: str,
    source_root: str,
    derived_output_file_path: str,
) -> str | None:
    """
    Find an existing test file that already exercises the source class even
    when it does not follow the SourceClassTest.kt naming convention.
    """
    if os.path.exists(derived_output_file_path):
        return derived_output_file_path

    test_root = source_root_to_test_root(source_root)
    if not os.path.isdir(test_root):
        return None

    source_package = extract_package_name(
        Path(source_file_path).read_text(encoding="utf-8")
    )
    candidate_scores = []
    class_reference = re.compile(r"\b" + re.escape(class_name) + r"\b")
    constructor_reference = re.compile(r"\b" + re.escape(class_name) + r"\s*\(")
    typed_reference = re.compile(r":\s*" + re.escape(class_name) + r"\b")

    for root, dirs, files in os.walk(test_root):
        dirs[:] = [
            directory
            for directory in dirs
            if directory not in {"build", ".gradle", ".git"}
        ]
        for file_name in files:
            if not file_name.endswith(".kt"):
                continue

            path = os.path.join(root, file_name)
            try:
                test_code = Path(path).read_text(encoding="utf-8")
            except OSError:
                continue

            if not class_reference.search(test_code):
                continue

            score = 1
            if constructor_reference.search(test_code):
                score += 8
            if typed_reference.search(test_code):
                score += 4
            if re.search(
                rf"import\s+{re.escape(source_package)}\.{re.escape(class_name)}\b",
                test_code,
            ):
                score += 3
            if file_name == os.path.basename(derived_output_file_path):
                score += 20
            if file_name.endswith("Test.kt"):
                score += 1

            candidate_scores.append((score, path))

    if not candidate_scores:
        return None

    candidate_scores.sort(key=lambda item: (-item[0], item[1]))
    return candidate_scores[0][1]

def collect_nearby_test_pattern_context(
    output_file_path: str | None,
    current_test_code: str,
    class_name: str,
    max_files: int = 2,
    source_categories=None,
    require_category_overlap: bool = False,
) -> str:
    """
    Include tiny same-package examples from already-existing tests. These are
    examples to copy style from, not source of truth for signatures.
    """
    if not output_file_path:
        return "No output file path was available for nearby test lookup."

    output_path = Path(output_file_path)
    test_dir = output_path.parent
    if not test_dir.is_dir():
        return "No nearby test directory was available."

    current_imports = _extract_imports(current_test_code)
    current_categories = set(source_categories or source_rule_categories(current_test_code))
    current_fixture_tags = _test_pattern_fixture_tags(current_test_code)
    class_tokens = {
        token.lower()
        for token in re.findall(r"[A-Z][A-Za-z0-9]+", class_name or "")
        if len(token) >= 4
    }

    candidates = []
    for path in sorted(test_dir.glob("*.kt")):
        if path.resolve() == output_path.resolve() or path.name.endswith("CoverageSupplementTest.kt"):
            continue
        try:
            code = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        imports = _extract_imports(code)
        candidate_categories = source_rule_categories(code)
        category_overlap = current_categories & candidate_categories
        candidate_fixture_tags = _test_pattern_fixture_tags(code)
        fixture_overlap = current_fixture_tags & candidate_fixture_tags
        score = len(current_imports & imports)
        score += len(category_overlap) * 3
        score += len(fixture_overlap) * 8
        if current_fixture_tags and not fixture_overlap:
            score -= 4
        lower_code = code.lower()
        score += sum(2 for token in class_tokens if token in lower_code)
        for pattern in ("mockStatic", "DigitalKeyUtils.getAppVersion", "spy(", "doAnswer", "ApplicationProvider"):
            if pattern in current_test_code and pattern in code:
                score += 3
        if require_category_overlap and not category_overlap and not fixture_overlap:
            continue
        if score <= 0:
            continue
        candidates.append((score, path, code, candidate_categories, category_overlap, candidate_fixture_tags, fixture_overlap))

    if not candidates:
        return "No nearby same-package test examples were found."

    sections = []
    for _, path, code, candidate_categories, category_overlap, candidate_fixture_tags, fixture_overlap in sorted(candidates, key=lambda item: (-item[0], item[1].name))[:max_files]:
        summary = summarize_kotlin_test_pattern(code)
        if not summary:
            continue
        category_text = ", ".join(sorted(candidate_categories)) or "uncategorized"
        overlap_text = ", ".join(sorted(category_overlap)) or "import/name similarity"
        fixture_text = ", ".join(sorted(candidate_fixture_tags)) or "generic"
        fixture_match_text = ", ".join(sorted(fixture_overlap)) or "none"
        sections.append(
            f"### NEARBY EXISTING TEST PATTERN: {path.name}\n"
            f"Categories: {category_text}. Matched: {overlap_text}.\n"
            f"Fixture pattern: {fixture_text}. Fixture match: {fixture_match_text}.\n"
            "Use only generic style/patterns from this example; re-verify symbols against current source.\n"
            f"{summary}"
        )

    return "\n\n".join(sections)














def _find_kotlin_source_for_qualified_import(project_root: str, qualified_import: str) -> str | None:
    simple_name = qualified_import.rsplit(".", 1)[-1]
    expected_package = qualified_import.rsplit(".", 1)[0] if "." in qualified_import else ""
    for path in Path(project_root).rglob(f"{simple_name}.kt"):
        if any(part in {"build", ".git", ".gradle"} for part in path.parts):
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        package_name = extract_package_name(content) or kotlin_package_name(content)
        if expected_package and package_name != expected_package:
            continue
        return content
    return None


def collect_imported_project_declaration_context(
    project_root: str,
    source_code: str,
    max_items: int = 8,
    *,
    use_snippets: bool = True,
) -> str:
    profile = classify_source(source_code)
    sections = []
    for qualified_import in kotlin_imports(source_code):
        if qualified_import.startswith(("kotlin.", "java.", "android.", "androidx.", "javax.")):
            continue
        candidate_content = _find_kotlin_source_for_qualified_import(project_root, qualified_import)
        if not candidate_content:
            continue
        simple_name = qualified_import.rsplit(".", 1)[-1]
        candidate_profile = classify_source(candidate_content)
        if not (
            simple_name in profile.symbols
            or candidate_profile.categories & profile.categories
            or simple_name in {dep.split(".")[-1] for dep in profile.required_dependencies}
        ):
            continue
        summary = (
            summarize_kotlin_source_signatures(candidate_content, max_lines=40)
            if use_snippets
            else candidate_content.strip()
        )
        if summary:
            sections.append(f"Verified imported declaration: {qualified_import}\n{summary}")
        if len(sections) >= max_items:
            break
    return "\n\n".join(sections)


def collect_android_activity_host_context(project_root: str, source_file_path: str, source_code: str) -> str:
    module_dir = find_owning_module_dir(project_root, source_file_path)
    manifest_path = Path(module_dir) / "src" / "main" / "AndroidManifest.xml"
    if not manifest_path.is_file():
        return ""
    try:
        manifest_text = manifest_path.read_text(encoding="utf-8")
    except OSError:
        return ""
    theme_match = re.search(r'android:theme="@style/([^"]+)"', manifest_text)
    theme_name = theme_match.group(1) if theme_match else ""
    lines = ["ANDROID ACTIVITY HOST CONTEXT"]
    if theme_name:
        lines.append(f"Application theme: {theme_name}")
        styles_path = Path(module_dir) / "src" / "main" / "res" / "values" / "styles.xml"
        if styles_path.is_file():
            styles_text = styles_path.read_text(encoding="utf-8")
            parent_match = re.search(
                rf'<style\s+name="{re.escape(theme_name)}"[^>]*parent="([^"]+)"',
                styles_text,
            )
            if parent_match:
                lines.append(f"Theme parent: {parent_match.group(1)}")
    layout_dir = Path(module_dir) / "src" / "main" / "res" / "layout"
    nav_host = "no"
    nav_graph_name = ""
    if layout_dir.is_dir():
        for layout_file in layout_dir.glob("*.xml"):
            layout_text = layout_file.read_text(encoding="utf-8")
            if "NavHostFragment" in layout_text:
                nav_host = "yes"
                graph_match = re.search(r'app:navGraph="@navigation/([^"]+)"', layout_text)
                if graph_match:
                    nav_graph_name = graph_match.group(1)
                break
    lines.append(f"NavHostFragment: {nav_host}")
    if nav_graph_name:
        nav_graph_path = Path(module_dir) / "src" / "main" / "res" / "navigation" / f"{nav_graph_name}.xml"
        if nav_graph_path.is_file():
            nav_graph_text = nav_graph_path.read_text(encoding="utf-8")
            for include_match in re.finditer(r'app:graph="@navigation/([^"]+)"', nav_graph_text):
                included = include_match.group(1)
                for candidate in Path(project_root).rglob(f"{included}.xml"):
                    if "navigation" not in candidate.parts:
                        continue
                    included_text = candidate.read_text(encoding="utf-8")
                    fragment_match = re.search(r'android:name="([^"]+Fragment[^"]*)"', included_text)
                    if fragment_match:
                        lines.append(f"Start fragment candidate: {fragment_match.group(1)}")
    return "\n".join(lines)


def retrieve_classified_context(
    source_profile: SourceProfile,
    source_file_path: str,
    source_code: str,
    project_index: dict,
    project_root: str,
    *,
    phase: str = "generation",
) -> str:
    class_name, file_data = find_file_data_for_path(project_index, source_file_path)
    sections = []
    if file_data:
        dependency_context = find_semantic_and_structural_dependencies(
            class_name or "",
            file_data,
            project_index,
        )
        if dependency_context:
            sections.append(dependency_context)
    imported_context = collect_imported_project_declaration_context(project_root, source_code)
    if imported_context:
        sections.append(imported_context)
    activity_context = ""
    if source_profile.categories & {"android_fragment", "navigation_fragment", "android_navigation"}:
        activity_context = collect_android_activity_host_context(project_root, source_file_path, source_code)
    if activity_context:
        sections.append(activity_context)
    resource_context = collect_verified_android_resource_context(project_root, source_file_path, source_code)
    if resource_context:
        sections.append(resource_context)
    if "apollo" in source_profile.categories:
        apollo_context = collect_verified_apollo_context(
            project_root=project_root,
            target_path=source_file_path,
            source_code=source_code,
        )
        if apollo_context:
            sections.append(apollo_context)
    return "\n\n".join(section for section in sections if section.strip())


def cosine_similarity(v1, v2):
    if v1 is None or v2 is None:
        return 0.0

    dot_product = np.dot(v1, v2)
    norm_v1 = np.linalg.norm(v1)
    norm_v2 = np.linalg.norm(v2)

    if norm_v1 == 0 or norm_v2 == 0:
        return 0.0

    return dot_product / (norm_v1 * norm_v2)


def is_path_inside(child_path: str, parent_path: str) -> bool:
    child = os.path.abspath(child_path)
    parent = os.path.abspath(parent_path)
    try:
        return os.path.commonpath([child, parent]) == parent
    except ValueError:
        return False


def resolve_path_against_root(path_value: str, project_root: str) -> str:
    if not path_value:
        return path_value

    if os.path.isabs(path_value):
        return os.path.abspath(path_value)

    return os.path.abspath(os.path.join(project_root, path_value))
