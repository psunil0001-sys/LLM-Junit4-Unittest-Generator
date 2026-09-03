"""Module discovery, Gradle metadata, coverage-task resolution."""

from __future__ import annotations

import difflib
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from UnitTest_gen.kotlin.analysis import kotlin_top_level_class_name
from UnitTest_gen.core import io as file_cache
from UnitTest_gen.core.io import DEFAULT_SKIP_DIRS, walk_source_roots

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
            text = file_cache.read_text(path, default="")
            for name in _ANDROID_XML_ID_PATTERN.findall(text):
                index[("id", name)] = VerifiedAndroidResource("id", name, "source", str(path), path.stem)
        elif resource_type in {"drawable", "navigation", "menu", "raw"}:
            index[(resource_type, path.stem)] = VerifiedAndroidResource(resource_type, path.stem, "source", str(path))
        elif resource_type == "values":
            text = file_cache.read_text(path, default="")
            for kind, name in re.findall(r'<(string|color|dimen|style|item)\b[^>]*\bname="([^"]+)"', text):
                kind = "attr" if kind == "item" and 'type="attr"' in text else kind
                index[(kind, name)] = VerifiedAndroidResource(kind, name, "source", str(path))

    for path in sorted(module.glob("build/intermediates/**/R.txt"), key=lambda p: p.stat().st_mtime):
        variant = _resource_variant(path)
        for line in file_cache.read_text(path, default="").splitlines():
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
    normalized_matches = [
        candidate for candidate in candidates if re.sub(r"_", "", candidate).lower() == normalized_name
    ]
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

def find_owning_module_dir(project_root: str, target_path: str) -> str:
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

def format_owning_module_anchor(source_path: str, project_root: str = "") -> str:
    """One-line module scope anchor for Bash find/grep discovery."""
    root = project_root or find_project_root_for_path(source_path) or ""
    module_dir = find_owning_module_dir(root, source_path) if root else ""
    if not module_dir:
        return ""
    return (
        f"OWNING MODULE DIR: {module_dir}\n"
        "Bash find/grep: scope here first (embedded bfs/ugrep), then widen only if zero hits."
    )

def source_kotlin_fqcn(source_path: str) -> str:
    """Derive FQCN from a path under src/main/java/.../*.kt."""
    path = Path(source_path)
    parts = path.parts
    try:
        idx = parts.index("main")
        if idx + 2 < len(parts) and parts[idx + 1] == "java":
            pkg = ".".join(parts[idx + 2 : -1])
            return f"{pkg}.{path.stem}"
    except ValueError:
        pass
    return ""

def find_nav_graph_for_fragment(module_dir: str, fragment_fqcn: str) -> str:
    """Return navigation graph stem (filename without .xml) containing fragment FQCN."""
    if not fragment_fqcn:
        return ""
    nav_dir = Path(module_dir) / "src" / "main" / "res" / "navigation"
    if not nav_dir.is_dir():
        return ""
    needle = f'android:name="{fragment_fqcn}"'
    for xml in sorted(nav_dir.glob("*.xml")):
        if needle in file_cache.read_text(str(xml), default=""):
            return xml.stem
    return ""

def _android_test_hilt_host_path(module_dir: str) -> str:
    module = Path(module_dir)
    android_candidates = sorted(module.glob("src/androidTest/java/**/HiltTestActivity.kt"))
    if android_candidates:
        return str(android_candidates[0].resolve())
    unit_candidates = sorted(module.glob("src/test/java/**/HiltTestActivity.kt"))
    if unit_candidates:
        return str(unit_candidates[0].resolve())
    return ""

def _android_test_nav_host_path(module_dir: str) -> str:
    module = Path(module_dir)
    candidates = sorted(module.glob("src/androidTest/java/**/HiltNavTestActivity.kt"))
    return str(candidates[0].resolve()) if candidates else ""

def _nav_host_layout_path(module_dir: str) -> str:
    layout = Path(module_dir) / "src" / "androidTest" / "res" / "layout" / "hilt_nav_test_activity.xml"
    return str(layout.resolve()) if layout.is_file() else ""

def _nav_graph_path(module_dir: str, source_path: str) -> str:
    fqcn = source_kotlin_fqcn(source_path)
    stem = find_nav_graph_for_fragment(module_dir, fqcn)
    if not stem:
        return ""
    nav_dir = Path(module_dir) / "src" / "main" / "res" / "navigation"
    path = nav_dir / f"{stem}.xml"
    return str(path.resolve()) if path.is_file() else ""

def _test_nav_graph_path(module_dir: str) -> str:
    graph = Path(module_dir) / "src" / "androidTest" / "res" / "navigation" / "hilt_nav_test_graph.xml"
    return str(graph.resolve()) if graph.is_file() else ""

def _stub_home_fragment_path(module_dir: str) -> str:
    module = Path(module_dir)
    candidates = sorted(module.glob("src/androidTest/java/**/StubHomeFragment.kt"))
    return str(candidates[0].resolve()) if candidates else ""

def _plain_hilt_activity_path(module_dir: str) -> str:
    module = Path(module_dir)
    candidates = sorted(module.glob("src/androidTest/java/**/PlainHiltActivity.kt"))
    return str(candidates[0].resolve()) if candidates else ""

def _fragment_layout_path(module_dir: str, source_path: str) -> str:
    stem = Path(source_path).stem
    if not stem.endswith("Fragment"):
        return ""
    prefix = stem[: -len("Fragment")]
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", prefix).lower()
    layout_name = f"{snake}_fragment.xml"
    module = Path(module_dir)
    for pattern in (
        module / "src" / "main" / "res" / "layout" / layout_name,
        module / "src" / "main" / "res" / "layout-*" / layout_name,
    ):
        if pattern.is_file():
            return str(pattern.resolve())
    matches = sorted(module.glob(f"src/main/res/layout*/{layout_name}"))
    return str(matches[0].resolve()) if matches else ""

def format_nav_discovery_paths_block(source_path: str, project_root: str = "") -> str:
    """Nav graph paths for plan discovery — lighter than full instrumented bootstrap block."""
    root = project_root or find_project_root_for_path(source_path) or ""
    module_dir = find_owning_module_dir(root, source_path) if root else ""
    if not module_dir:
        return ""
    lines = ["NAV DISCOVERY PATHS (Read once — do not guess):"]
    nav_graph = _nav_graph_path(module_dir, source_path)
    if nav_graph:
        lines.append(f"- nav graph (main): {nav_graph}")
    test_nav_graph = _test_nav_graph_path(module_dir)
    if test_nav_graph:
        lines.append(f"- nav graph (androidTest): {test_nav_graph}")
    if len(lines) == 1:
        return ""
    return "\n".join(lines)

def format_instrumented_bootstrap_paths_block(source_path: str, project_root: str = "") -> str:
    """Absolute bootstrap paths the agent should Read once (host, manifest, layout)."""
    root = project_root or find_project_root_for_path(source_path) or ""
    module_dir = find_owning_module_dir(root, source_path) if root else ""
    if not module_dir:
        return ""
    lines = ["INSTRUMENTED BOOTSTRAP PATHS (Read once — do not guess):"]
    host = _android_test_hilt_host_path(module_dir)
    if host:
        lines.append(f"- HiltTestActivity host: {host}")
    nav_host = _android_test_nav_host_path(module_dir)
    if nav_host:
        lines.append(f"- HiltNavTestActivity host: {nav_host}")
    manifest = Path(module_dir) / "src" / "androidTest" / "AndroidManifest.xml"
    if manifest.is_file():
        lines.append(f"- androidTest manifest: {manifest.resolve()}")
    nav_graph = _nav_graph_path(module_dir, source_path)
    if nav_graph:
        lines.append(f"- nav graph (main): {nav_graph}")
    test_nav_graph = _test_nav_graph_path(module_dir)
    if test_nav_graph:
        lines.append(f"- nav graph (androidTest): {test_nav_graph}")
    nav_layout = _nav_host_layout_path(module_dir)
    if nav_layout:
        lines.append(f"- nav host layout: {nav_layout}")
    stub_home = _stub_home_fragment_path(module_dir)
    if stub_home:
        lines.append(f"- StubHomeFragment: {stub_home}")
    plain_host = _plain_hilt_activity_path(module_dir)
    if plain_host:
        lines.append(f"- PlainHiltActivity (negative onAttach): {plain_host}")
    layout = _fragment_layout_path(module_dir, source_path)
    if layout:
        lines.append(f"- fragment layout: {layout}")
    if len(lines) == 1:
        return ""
    return "\n".join(lines)

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

def android_sdk_roots() -> list[str]:
    roots = []
    for env_name in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        value = os.environ.get(env_name, "").strip()
        if value and value not in roots:
            roots.append(value)
    home = Path.home()
    for candidate in (home / "Android" / "Sdk", home / "Library" / "Android" / "sdk"):
        if candidate.is_dir() and str(candidate) not in roots:
            roots.append(str(candidate))
    return roots

def is_android_platform_installed(api_level: int) -> bool:
    for root in android_sdk_roots():
        if (Path(root) / "platforms" / f"android-{api_level}").is_dir():
            return True
    return False

def choose_robolectric_sdk_for_module(min_sdk: int, compile_sdk: int | None = None) -> int | None:
    upper = compile_sdk or min_sdk
    for level in range(upper, min_sdk - 1, -1):
        if is_android_platform_installed(level):
            return level
    return None

@dataclass(frozen=True)
class ModuleSdkResolution:
    """Resolved Robolectric @Config SDK for an owning Android module."""

    min_sdk: int | None
    compile_sdk: int | None
    selected_sdk: int | None
    sdk_roots: tuple[str, ...]
    ok: bool
    reason: str = ""

def missing_platform_in_range_reason(min_sdk: int, compile_sdk: int | None) -> str:
    """Shared reason when no installed platform satisfies [minSdk, compileSdk]."""
    upper = f" and <= compileSdk {compile_sdk}" if compile_sdk is not None else ""
    return (
        f"No local Android SDK platform >= module minSdk {min_sdk}{upper} was found. "
        f'Install one in that range, for example sdkmanager "platforms;android-{min_sdk}".'
    )

def resolve_module_robolectric_sdk(module_dir: str | None) -> ModuleSdkResolution:
    """Parse module min/compileSdk and pick highest installed platform in range."""
    roots = tuple(android_sdk_roots())
    # Lazy import avoids cycle: gradle_context imports project_context at module load.
    from UnitTest_gen.kotlin.project import (
        parse_module_compile_sdk,
        parse_module_min_sdk,
    )

    if not module_dir:
        return ModuleSdkResolution(
            min_sdk=None,
            compile_sdk=None,
            selected_sdk=None,
            sdk_roots=roots,
            ok=False,
            reason="Owning module directory is unknown; cannot resolve Android SDK for Robolectric.",
        )
    min_sdk = parse_module_min_sdk(module_dir)
    compile_sdk = parse_module_compile_sdk(module_dir)
    if min_sdk is None:
        return ModuleSdkResolution(
            min_sdk=None,
            compile_sdk=compile_sdk,
            selected_sdk=None,
            sdk_roots=roots,
            ok=False,
            reason=(
                f"Could not parse minSdk from module Gradle under {module_dir}. "
                "Numeric minSdk / minSdkVersion is required to resolve a Robolectric @Config SDK."
            ),
        )
    if not roots:
        return ModuleSdkResolution(
            min_sdk=min_sdk,
            compile_sdk=compile_sdk,
            selected_sdk=None,
            sdk_roots=(),
            ok=False,
            reason=(
                "No Android SDK root found (set ANDROID_HOME or ANDROID_SDK_ROOT, "
                "or install platforms under ~/Android/Sdk)."
            ),
        )
    selected = choose_robolectric_sdk_for_module(min_sdk, compile_sdk)
    if selected is None:
        return ModuleSdkResolution(
            min_sdk=min_sdk,
            compile_sdk=compile_sdk,
            selected_sdk=None,
            sdk_roots=roots,
            ok=False,
            reason=missing_platform_in_range_reason(min_sdk, compile_sdk),
        )
    return ModuleSdkResolution(
        min_sdk=min_sdk,
        compile_sdk=compile_sdk,
        selected_sdk=selected,
        sdk_roots=roots,
        ok=True,
        reason="",
    )

def format_module_sdk_prompt_block(resolution: ModuleSdkResolution) -> str:
    """Cached-zone prompt text pinning the pipeline-owned Robolectric SDK."""
    if not resolution.ok or resolution.selected_sdk is None:
        return ""
    roots = ", ".join(resolution.sdk_roots) if resolution.sdk_roots else "(none)"
    compile_display = (
        str(resolution.compile_sdk) if resolution.compile_sdk is not None else "(unparsed)"
    )
    lo = resolution.min_sdk
    hi = resolution.compile_sdk if resolution.compile_sdk is not None else resolution.min_sdk
    return "\n".join(
        [
            "MODULE ANDROID SDK (pipeline-owned):",
            f"- minSdk: {resolution.min_sdk}",
            f"- compileSdk: {compile_display}",
            f"- SDK roots: {roots}",
            f"- selected API for @Config(sdk=[...]): {resolution.selected_sdk}",
            (
                f"Use @Config(sdk=[{resolution.selected_sdk}]) when explicit SDK config is needed. "
                f"Do not use API levels outside [{lo}, {hi}] or platforms not installed locally."
            ),
        ]
    )

def resolve_module_sdk_prompt_block_for_path(source_or_test_path: str) -> str:
    """Resolve owning module SDK from a source/test path; empty when unavailable."""
    project_root = find_project_root_for_path(source_or_test_path)
    if not project_root:
        return ""
    module_dir = find_owning_module_dir(project_root, source_or_test_path)
    return format_module_sdk_prompt_block(resolve_module_robolectric_sdk(module_dir))

def default_gradle_tasks_for_target(project_root: str, target_path: str):
    module_dir = find_owning_module_dir(project_root, target_path)
    module_path = module_path_for_dir(project_root, module_dir)
    if not module_path:
        return ["testDevDebugUnitTest"]
    return [f"{module_path}:testDevDebugUnitTest"]

def owning_module_dir_for_output(output_file_path: str | None) -> str:
    if not output_file_path:
        return ""
    project_root = find_project_root_for_path(output_file_path)
    return find_owning_module_dir(project_root, output_file_path) if project_root else os.path.dirname(output_file_path)

def source_requires_main_activity_delegate(source_code: str) -> bool:
    """True when SOURCE onAttach requires MainActivityDelegate (throws otherwise)."""
    if not source_code or "MainActivityDelegate" not in source_code:
        return False
    if "onAttach" not in source_code:
        return False
    return (
        "Activity must be MainActivityDelegate" in source_code
        or "IllegalArgumentException" in source_code
        and "MainActivityDelegate" in source_code
    )

def _hilt_test_activity_candidates(module_dir: str) -> list[Path]:
    test_src_root = os.path.join(module_dir, "src", "test", "java")
    if not os.path.isdir(test_src_root):
        return []
    return list(Path(test_src_root).rglob("HiltTestActivity.kt"))

def module_has_manifest_declared_hilt_test_activity(
    output_file_path: str | None, *, require_main_activity_delegate: bool = False
) -> bool:
    module_dir = owning_module_dir_for_output(output_file_path)
    if not module_dir:
        return False
    manifest_path = os.path.join(module_dir, "src", "test", "AndroidManifest.xml")
    if not os.path.exists(manifest_path):
        return False
    try:
        manifest_content = file_cache.read_text(manifest_path)
    except file_cache.FileCacheError:
        return False
    if "HiltTestActivity" not in manifest_content:
        return False
    for candidate in _hilt_test_activity_candidates(module_dir):
        candidate_content = file_cache.read_text(candidate, default="")
        if "@AndroidEntryPoint" not in candidate_content or "class HiltTestActivity" not in candidate_content:
            continue
        if require_main_activity_delegate and "MainActivityDelegate" not in candidate_content:
            continue
        return True
    return False

def module_has_hilt_robolectric_fragment_support(
    output_file_path: str | None, source_code: str = ""
) -> tuple[bool, list[str]]:
    from UnitTest_gen.kotlin.project import module_has_hilt_robolectric_gradle_prereqs

    _, missing = module_has_hilt_robolectric_gradle_prereqs(output_file_path, source_code)
    needs_delegate = source_requires_main_activity_delegate(source_code)
    if not module_has_manifest_declared_hilt_test_activity(
        output_file_path, require_main_activity_delegate=needs_delegate
    ):
        if needs_delegate:
            missing.append("manifest-declared HiltTestActivity implementing MainActivityDelegate")
        else:
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
        content = file_cache.read_text(test_file, default="")
        if not content:
            continue
        if "@BindValue" in content:
            continue
        if "@Module" not in content or "@InstallIn" not in content:
            continue
        if binding_fqcn in content or re.search(rf"\b{re.escape(simple_name)}\b", content):
            return True
    return False

def is_indexable_kotlin_source(path: str, scan_root: str) -> bool:
    normalized = os.path.abspath(path)
    if not normalized.endswith(".kt") or normalized.endswith("Test.kt"):
        return False
    parts = set(normalized.split(os.sep))
    if any(part in parts for part in {"build", ".gradle", ".git"}):
        return False
    return (
        f"{os.sep}src{os.sep}main{os.sep}java{os.sep}" in normalized
        or f"{os.sep}src{os.sep}main{os.sep}kotlin{os.sep}" in normalized
        or scan_root.endswith(os.path.join("src", "main", "java"))
        or scan_root.endswith(os.path.join("src", "main", "kotlin"))
    )

def discover_target_sources(target_path: str, source_root: str) -> dict:
    targets = {}

    def add_source(path: str):
        content = file_cache.read_text(path, default="")
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

    for path in walk_source_roots(
        target_path,
        roots=(),
        skip_dirs=DEFAULT_SKIP_DIRS,
        extensions=(".kt",),
    ):
        if is_indexable_kotlin_source(path, source_root):
            add_source(path)
    return targets

def find_source_root_for_path(target_path: str) -> str:
    normalized = os.path.abspath(target_path)
    parts = normalized.split(os.sep)
    for lang_dir in ("java", "kotlin"):
        marker = ["src", "main", lang_dir]
        for i in range(0, len(parts) - len(marker) + 1):
            if parts[i : i + len(marker)] == marker:
                return os.sep.join(parts[: i + len(marker)])
    return os.path.dirname(normalized) if os.path.isfile(normalized) else normalized

def derive_test_output_path(source_file_path: str, source_root: str, output_base_directory: str = None) -> str:
    """Map a source ``.kt`` path to the pipeline target ``{Stem}Test.kt`` path (unit / JVM)."""
    return _derive_test_output_path_for_root(
        source_file_path, source_root, output_base_directory,
        test_root=("src", "test"),
    )

def derive_instrumented_test_output_path(
    source_file_path: str,
    source_root: str,
    output_base_directory: str = None,
) -> str:
    """Map a source ``.kt`` path to ``{Stem}InstrumentedTest.kt`` under ``src/androidTest``."""
    source_file_path = os.path.abspath(source_file_path)
    stem = Path(source_file_path).stem
    test_file_name = f"{stem}InstrumentedTest.kt"
    source_root = os.path.abspath(source_root)
    rel_dir = os.path.relpath(os.path.dirname(source_file_path), source_root)
    if output_base_directory:
        return os.path.normpath(
            os.path.join(os.path.abspath(output_base_directory), rel_dir, test_file_name)
        )
    return _derive_test_output_path_for_root(
        source_file_path,
        source_root,
        output_base_directory=None,
        test_root=("src", "androidTest"),
        test_file_name=test_file_name,
    )

def is_instrumented_test_path(test_path: str) -> bool:
    normalized = os.path.abspath(test_path).replace("\\", "/")
    return "/src/androidTest/" in normalized

def _derive_test_output_path_for_root(
    source_file_path: str,
    source_root: str,
    output_base_directory: str | None,
    *,
    test_root: tuple[str, str],
    test_file_name: str | None = None,
) -> str:
    source_file_path = os.path.abspath(source_file_path)
    source_root = os.path.abspath(source_root)
    test_file_name = test_file_name or (
        os.path.basename(source_file_path).replace(".kt", "Test.kt")
    )
    rel_dir = os.path.relpath(os.path.dirname(source_file_path), source_root)
    if output_base_directory:
        return os.path.normpath(
            os.path.join(os.path.abspath(output_base_directory), rel_dir, test_file_name)
        )
    test_seg = os.sep.join(test_root)
    replacements = [
        (f"{os.sep}src{os.sep}main{os.sep}java{os.sep}", f"{os.sep}{test_seg}{os.sep}java{os.sep}"),
        (f"{os.sep}src{os.sep}main{os.sep}kotlin{os.sep}", f"{os.sep}{test_seg}{os.sep}kotlin{os.sep}"),
    ]
    for old, new in replacements:
        if old in source_file_path:
            mapped = source_file_path.replace(old, new)
            return str(Path(mapped).with_name(test_file_name))
    lang = "java" if test_root[1] == "androidTest" else "java"
    return os.path.normpath(
        os.path.join(os.getcwd(), test_root[0], test_root[1], lang, rel_dir, test_file_name)
    )

def discover_sibling_test_files(source_file_path: str, target_test_path: str) -> list[str]:
    """Find existing ``{SourceStem}*Test.kt`` files next to the target test path.

    Excludes the target test file itself. Example: ``LocationUtil.kt`` +
    ``LocationUtilTest.kt`` finds ``LocationUtilRobolectricTest.kt`` /
    ``LocationUtilJvmTest.kt`` (and any other ``LocationUtil*Test.kt`` siblings).
    """
    source_stem = Path(source_file_path).stem
    if not source_stem:
        return []
    target = Path(target_test_path).resolve()
    test_dir = target.parent
    if not test_dir.is_dir():
        return []
    pattern = f"{source_stem}*Test.kt"
    siblings: list[str] = []
    for path in sorted(test_dir.glob(pattern)):
        if not path.is_file():
            continue
        resolved = path.resolve()
        if resolved == target:
            continue
        siblings.append(str(resolved))
    return siblings

def format_sibling_test_paths(sibling_paths: list[str]) -> str:
    """Prompt section listing sibling absolute paths (optional host-setup reference)."""
    if not sibling_paths:
        return ""
    lines = [
        "SIBLING TEST FILES (absolute paths — optional host-setup reference ONLY, NEVER edit)",
        "Implement plan TCs from SOURCE + TARGET first; do not search siblings for scenarios.",
        "View a sibling only for proven Robolectric/@Config/shadow/permission/Hilt host setup "
        "when TARGET lacks it — at most one sibling file per turn; do not copy whole tests.",
        "Do not copy mocking framework from siblings into a new TARGET; obey MOCKING LANE "
        "(choose exactly one for a blank TARGET, or stay on the framework already in TARGET).",
        "All new tests MUST be written only to the pipeline target *Test.kt file named in TASK "
        "(the absolute PIPELINE TARGET path).",
    ]
    for path in sibling_paths:
        lines.append(f"- {path}")
    return "\n".join(lines) + "\n"

_SIBLING_REF_HEADER = (
    "SIBLING TEST FILES (read-only — optional host-setup reference ONLY, NEVER edit these paths)\n"
    "Implement plan TCs from SOURCE + TARGET first; do not search siblings for scenarios.\n"
    "Use a sibling only for proven Robolectric/@Config/shadow/permission/Hilt host setup "
    "when TARGET lacks it — at most one sibling file; do not copy whole tests.\n"
    "Do not copy mocking framework from siblings into a new TARGET; obey MOCKING LANE "
    "(choose exactly one for a blank TARGET, or stay on the framework already in TARGET).\n"
    "All new tests MUST be written only to the pipeline target *Test.kt file named in TASK "
    "(the absolute PIPELINE TARGET path)."
)

def format_sibling_test_context(
    sibling_paths: list[str],
    *,
    char_budget: int = 12_000,
    per_file_limit: int = 4_000,
) -> str:
    """Build a prompt section with sibling test excerpts (read-only reference)."""
    if not sibling_paths or char_budget <= 0:
        return ""
    parts = [_SIBLING_REF_HEADER]
    remaining = char_budget - len(_SIBLING_REF_HEADER) - 2
    for path in sibling_paths:
        if remaining <= 200:
            break
        text = file_cache.read_text(path, default="")
        if not text.strip():
            continue
        file_budget = min(per_file_limit, remaining - 80)
        if file_budget < 120:
            break
        excerpt = text if len(text) <= file_budget else text[: file_budget // 2] + "\n... truncated ...\n" + text[-max(file_budget // 2 - 40, 0) :]
        block = f"\n--- {path} ---\n{excerpt.rstrip()}\n"
        if len(block) > remaining:
            block = block[: remaining - 20] + "\n... truncated ...\n"
        parts.append(block)
        remaining -= len(block)
    return "".join(parts).rstrip() + "\n" if len(parts) > 1 else ""

def is_path_inside(child_path: str, parent_path: str) -> bool:
    child = os.path.abspath(child_path)
    parent = os.path.abspath(parent_path)
    try:
        return os.path.commonpath([child, parent]) == parent
    except ValueError:
        return False

def resolve_path_against_root(path_value: str, project_root: str) -> str:
    path_value = os.path.expanduser(path_value)
    if os.path.isabs(path_value):
        return os.path.abspath(path_value)
    return os.path.abspath(os.path.join(project_root, path_value))

import os
import re
from pathlib import Path

from UnitTest_gen.core import io as file_cache

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
CATALOG_LIB_ALIASES: dict[str, str] = {"junit": "junit"}
_SDK_PATTERNS = {
    "min": [
        r"\bminSdk\s*=\s*(\d+)\b",
        r"\bminSdkVersion\s*=\s*(\d+)\b",
        r"\bminSdkVersion\s+(\d+)\b",
        r"\bminSdk\s*\(\s*(\d+)\s*\)",
    ],
    "compile": [
        r"\bcompileSdk\s*=\s*(\d+)\b",
        r"\bcompileSdkVersion\s*=\s*(\d+)\b",
        r"\bcompileSdkVersion\s+(\d+)\b",
        r"\bcompileSdk\s*\(\s*(\d+)\s*\)",
    ],
}
_TEST_STACK_MARKERS = (
    "module sdk and test stack configuration",
    "shadowalertdialog",
    "shadowlooper",
    "robolectric.properties",
    "hilt-android-testing",
    "navigation-testing",
)

def project_hilt_version(project_root: str) -> str:
    catalog_path = Path(project_root) / "gradle" / "libs.versions.toml"
    if not catalog_path.is_file():
        return ""
    text = file_cache.read_text(catalog_path, default="")
    match = re.search(r'(?m)^\s*hilt\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else ""

def load_version_catalog(project_root: str) -> tuple[dict[str, str], dict[str, tuple[str, str, str | None]]]:
    catalog_path = os.path.join(project_root, "gradle", "libs.versions.toml")
    if not os.path.isfile(catalog_path):
        return {}, {}
    text = file_cache.read_text(catalog_path, default="")
    if not text:
        return {}, {}
    versions = {m.group(1): m.group(2) for m in re.finditer(r'(?m)^\s*([A-Za-z0-9_-]+)\s*=\s*"([^"]+)"', text)}
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
    return catalog_versions.get(version_ref or "", "")

def parse_versions_from_gradle_content(content: str, project_root: str) -> dict[str, str]:
    versions: dict[str, str] = {}
    catalog_versions, catalog_libraries = load_version_catalog(project_root) if project_root else ({}, {})
    for key, pattern in TEST_COORDINATE_PATTERNS.items():
        if match := re.search(pattern, content or ""):
            versions[key] = match.group(1)
    for gradle_alias, catalog_key in CATALOG_LIB_ALIASES.items():
        if re.search(rf"testImplementation\s*\(\s*libs\.{re.escape(gradle_alias)}\s*\)", content or ""):
            if resolved := _resolve_catalog_lib_version(catalog_key, catalog_versions, catalog_libraries):
                versions[catalog_key] = resolved
    if "hilt_android_testing" not in versions and project_root:
        if (hilt_version := project_hilt_version(project_root)) and "hilt-android-testing" in (content or ""):
            versions["hilt_android_testing"] = hilt_version
    return versions

def read_module_gradle_content(module_dir: str) -> str:
    for build_file_name in ("build.gradle.kts", "build.gradle"):
        build_file_path = os.path.join(module_dir, build_file_name)
        if not os.path.exists(build_file_path):
            continue
        text = file_cache.read_text(build_file_path, default="")
        if text:
            return text
    return ""

def read_owning_module_gradle(output_file_path: str | None) -> tuple[str, str]:
    if not output_file_path:
        return "", ""
    project_root = find_project_root_for_path(output_file_path)
    module_dir = find_owning_module_dir(project_root, output_file_path) if project_root else os.path.dirname(output_file_path)
    for gradle_name in ("build.gradle.kts", "build.gradle"):
        gradle_path = os.path.join(module_dir, gradle_name)
        if os.path.exists(gradle_path):
            return gradle_path, file_cache.read_text(gradle_path, default="")
    return "", ""

def collect_module_test_dependency_versions(module_dir: str, project_root: str) -> dict[str, str]:
    merged = dict(parse_versions_from_gradle_content(read_module_gradle_content(module_dir), project_root))
    if not merged.get("robolectric") and project_root:
        for fallback_module in ("common", "app"):
            fallback_dir = os.path.join(project_root, fallback_module)
            if os.path.isdir(fallback_dir) and os.path.abspath(fallback_dir) != os.path.abspath(module_dir):
                for key, value in parse_versions_from_gradle_content(read_module_gradle_content(fallback_dir), project_root).items():
                    merged.setdefault(key, value)
    if not merged.get("hilt_android_testing") and project_root and (hilt_version := project_hilt_version(project_root)):
        merged.setdefault("hilt_android_testing", hilt_version)
    return merged

def _parse_module_sdk(module_dir: str, kind: str) -> int | None:
    for gradle_name in ("build.gradle.kts", "build.gradle"):
        gradle_path = Path(module_dir) / gradle_name
        if not gradle_path.is_file():
            continue
        text = file_cache.read_text(gradle_path, default="")
        if not text:
            continue
        for pattern in _SDK_PATTERNS[kind]:
            if match := re.search(pattern, text):
                return int(match.group(1))
    return None

def parse_module_min_sdk(module_dir: str) -> int | None:
    return _parse_module_sdk(module_dir, "min")

def parse_module_compile_sdk(module_dir: str) -> int | None:
    return _parse_module_sdk(module_dir, "compile")

def parse_module_namespace(module_dir: str) -> str:
    for gradle_name in ("build.gradle.kts", "build.gradle"):
        gradle_path = os.path.join(module_dir, gradle_name)
        if not os.path.exists(gradle_path):
            continue
        gradle_content = file_cache.read_text(gradle_path, default="")
        if not gradle_content:
            continue
        for pattern in (r'\bnamespace\s*=\s*"([^"]+)"', r"\bnamespace\s+['\"]([^'\"]+)['\"]"):
            if match := re.search(pattern, gradle_content):
                return match.group(1)
    return ""

def parse_module_robolectric_version(module_dir: str) -> str | None:
    if match := re.search(TEST_COORDINATE_PATTERNS["robolectric"], read_module_gradle_content(module_dir)):
        return match.group(1)
    return None

def project_robolectric_version(project_root: str) -> str:
    for module_name in ("common", "app"):
        if version := parse_module_robolectric_version(os.path.join(project_root, module_name)):
            return version
    for build_file in Path(project_root).glob("**/build.gradle.kts"):
        if version := parse_module_robolectric_version(str(build_file.parent)):
            return version
    return ""

def module_ready_for_instrumented_tests(output_file_path: str | None) -> tuple[bool, list[str]]:
    """True when the owning module can compile connected androidTests (Hilt or existing sources)."""
    if not output_file_path:
        return False, ["unknown module"]
    project_root = find_project_root_for_path(output_file_path)
    module_dir = (
        find_owning_module_dir(project_root, output_file_path)
        if project_root
        else os.path.dirname(output_file_path)
    )
    if not module_dir:
        return False, ["unknown module"]
    _, gradle_content = read_owning_module_gradle(output_file_path)
    hilt_android_test = bool(
        re.search(r"androidTestImplementation\s*\([^)]*hilt-android-testing", gradle_content)
        or re.search(
            r"androidTestImplementation\s*\(\s*[\"']com\.google\.dagger:hilt-android-testing",
            gradle_content,
        )
    )
    hilt_android_test_compiler = (
        ("kspAndroidTest(" in gradle_content or "kaptAndroidTest(" in gradle_content)
        and ("hilt.android.compiler" in gradle_content or "hilt-android-compiler" in gradle_content)
    )
    android_test_dir = Path(module_dir) / "src" / "androidTest"
    has_android_test_sources = android_test_dir.exists() and any(android_test_dir.rglob("*.kt"))
    if hilt_android_test and hilt_android_test_compiler:
        return True, []
    if has_android_test_sources:
        return True, []
    missing: list[str] = []
    if not hilt_android_test:
        missing.append("androidTestImplementation hilt-android-testing")
    if not hilt_android_test_compiler:
        missing.append("kspAndroidTest/kaptAndroidTest hilt compiler")
    if not has_android_test_sources:
        missing.append("src/androidTest sources")
    return False, missing

def instrumented_manifest_missing(output_file_path: str | None) -> bool:
    """Diagnostic: Hilt androidTest deps present but androidTest manifest absent. Does not block routing."""
    if not output_file_path:
        return False
    project_root = find_project_root_for_path(output_file_path)
    module_dir = (
        find_owning_module_dir(project_root, output_file_path)
        if project_root
        else os.path.dirname(output_file_path)
    )
    if not module_dir:
        return False
    _, gradle_content = read_owning_module_gradle(output_file_path)
    hilt_android_test = bool(
        re.search(r"androidTestImplementation\s*\([^)]*hilt-android-testing", gradle_content)
        or re.search(
            r"androidTestImplementation\s*\(\s*[\"']com\.google\.dagger:hilt-android-testing",
            gradle_content,
        )
    )
    hilt_android_test_compiler = (
        ("kspAndroidTest(" in gradle_content or "kaptAndroidTest(" in gradle_content)
        and ("hilt.android.compiler" in gradle_content or "hilt-android-compiler" in gradle_content)
    )
    if not (hilt_android_test and hilt_android_test_compiler):
        return False
    manifest = Path(module_dir) / "src" / "androidTest" / "AndroidManifest.xml"
    return not manifest.is_file()

def module_has_hilt_robolectric_gradle_prereqs(
    output_file_path: str | None, source_code: str = ""
) -> tuple[bool, list[str]]:
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

def _version_context(name: str, version: str) -> str:
    return f"{name} {version}: exact APIs are retrieved from VERIFIED TEST LIBRARY API CONTEXT." if version else ""

def robolectric_api_guidance_for_version(version: str) -> str:
    return _version_context("Robolectric", version)

def mockito_api_guidance(versions: dict[str, str]) -> str:
    parts = [
        f"mockito-kotlin {versions['mockito_kotlin']}" if versions.get("mockito_kotlin") else "",
        f"mockito-inline {versions['mockito_inline']}" if versions.get("mockito_inline") else "",
    ]
    version_text = ", ".join(part for part in parts if part)
    return _version_context("Mockito", version_text)

def hilt_testing_api_guidance(version: str) -> str:
    return _version_context("Hilt Android Testing", version)

def coroutines_test_api_guidance(version: str) -> str:
    return _version_context("kotlinx-coroutines-test", version)

def arch_core_testing_api_guidance(version: str) -> str:
    return _version_context("androidx.arch.core:core-testing", version)

def androidx_test_core_api_guidance(version: str) -> str:
    return _version_context("androidx.test:core", version)

def fragment_testing_api_guidance(version: str) -> str:
    return _version_context("androidx.fragment:fragment-testing", version)

def navigation_testing_api_guidance(version: str) -> str:
    return _version_context("androidx.navigation:navigation-testing", version)

def junit_api_guidance(version: str) -> str:
    return _version_context("JUnit", version)

def rule_overlaps_test_stack(text: str) -> bool:
    lower = (text or "").lower()
    return any(marker in lower for marker in _TEST_STACK_MARKERS)

import os
import re
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# Import project-context helpers lazily inside functions to avoid heavy
# import-time dependencies (e.g., openai) that are not required for simple
# unit tests of task-resolution logic.

_UNIT_TEST_TASK_NAME = re.compile(r"^test([A-Za-z0-9]+)UnitTest$")
_KOVER_TASK_VARIANT = re.compile(
    r"^kover(?:Html|Xml|Binary|Log|Verify|CachedVerify)?Report([A-Za-z0-9]+)$"
)
_EXECUTED_UNIT_TEST = re.compile(r"test([A-Za-z0-9]+)UnitTest\b")
_TASKS_OUTPUT_UNIT_TEST = re.compile(r"^(test[A-Za-z0-9]+UnitTest)\b")

_VARIANT_PREFERENCE = (
    "ProdGlobalRelease",
    "ProdGlobalDebug",
    "ProdRelease",
    "ProdDebug",
    "ProdChinaRelease",
    "ProdChinaDebug",
    "ProdKoreaRelease",
    "ProdKoreaDebug",
    "StagingRelease",
    "StagingDebug",
    "DevRelease",
    "DevDebug",
)

_VARIANT_PATH_HINTS = (
    ("prodglobalrelease", "ProdGlobalRelease"),
    ("prodchinarelease", "ProdChinaRelease"),
    ("prodkorearelease", "ProdKoreaRelease"),
    ("stagingrelease", "StagingRelease"),
    ("stagingdebug", "StagingDebug"),
    ("prodrelease", "ProdRelease"),
    ("proddebug", "ProdDebug"),
    ("devrelease", "DevRelease"),
    ("devdebug", "DevDebug"),
)

@dataclass(frozen=True)
class GradleInvocation:
    tasks: list[str]
    gradle_args: list[str]

def _test_file_text(test_file_path: str, test_code: str | None) -> tuple[Path, str]:
    from UnitTest_gen.core import io as file_cache

    path = Path(test_file_path)
    if test_code is not None:
        return path, test_code or ""
    if not path.is_file():
        return path, ""
    return path, file_cache.read_text(test_file_path, default="")
def _test_package_and_classes(test_file_path: str, *, test_code: str | None = None) -> tuple[str, list[str]]:
    path, text = _test_file_text(test_file_path, test_code)
    match = re.search(r"^\s*package\s+([A-Za-z_][A-Za-z0-9_.]*)\s*$", text, re.MULTILINE)
    package = match.group(1) if match else ""
    from UnitTest_gen.kotlin.codegen import top_level_test_class_names

    class_names = top_level_test_class_names(text) or [path.stem]
    return package, class_names

def test_class_fqn(test_file_path: str, *, test_code: str | None = None) -> str:
    package, class_names = _test_package_and_classes(test_file_path, test_code=test_code)
    class_name = class_names[0]
    return f"{package}.{class_name}" if package else class_name

def test_class_gradle_args(test_file_path: str, *, test_code: str | None = None) -> list[str]:
    """Build one --tests flag per top-level test class declared in the file."""
    package, class_names = _test_package_and_classes(test_file_path, test_code=test_code)
    args: list[str] = []
    for class_name in class_names:
        fqn = f"{package}.{class_name}" if package else class_name
        args.extend(["--tests", fqn])
    return args

def _module_path_from_task(task: str) -> str:
    if not task.startswith(":"):
        return ""
    parts = task.split(":")
    if len(parts) <= 2:
        return ""
    return ":".join(parts[:-1])

def _task_name_from_task(task: str) -> str:
    return str(task).split(":")[-1]

def _module_path_for_test(project_root: str, test_file_path: str, kover_tasks: list[str] | None) -> str:
    for task in kover_tasks or []:
        module_path = _module_path_from_task(str(task))
        if module_path:
            return module_path
    from UnitTest_gen.kotlin.project import find_owning_module_dir, module_path_for_dir

    module_dir = find_owning_module_dir(project_root, test_file_path)
    return module_path_for_dir(project_root, module_dir)

def variant_from_unit_test_task(task_name: str) -> str | None:
    match = _UNIT_TEST_TASK_NAME.match(task_name)
    return match.group(1) if match else None

def variant_from_configured_task(task: str) -> str | None:
    task_name = _task_name_from_task(task)
    variant = variant_from_unit_test_task(task_name)
    if variant:
        return variant
    match = _KOVER_TASK_VARIANT.match(task_name)
    return match.group(1) if match else None

def variant_from_gradle_output(gradle_output: str, *, module_path: str = "") -> str | None:
    """Return the unit-test variant Gradle executed for the target module, if visible."""
    task_lines = [
        line for line in (gradle_output or "").splitlines() if "> Task " in line and "UnitTest" in line
    ]
    for require_module in (True, False):
        for line in task_lines:
            if require_module and module_path and module_path not in line:
                continue
            if match := _EXECUTED_UNIT_TEST.search(line):
                return match.group(1)
    return None

def variant_from_kover_report_path(module_dir: str) -> str | None:
    from UnitTest_gen.kotlin.coverage import find_latest_kover_xml

    kover_xml = find_latest_kover_xml(module_dir)
    if not kover_xml:
        return None
    normalized = re.sub(r"[^a-z0-9]", "", kover_xml.lower())
    for hint, variant in _VARIANT_PATH_HINTS:
        if hint in normalized:
            return variant
    return None

def gradle_variant_hint(gradle_tasks: list[str] | None) -> str:
    """Infer the release variant label from configured Gradle task names (reporting only)."""
    if override := os.environ.get("TESTGEN_GRADLE_VARIANT", "").strip():
        return override
    for task in gradle_tasks or []:
        if variant := variant_from_configured_task(str(task)):
            return variant
    joined = " ".join(gradle_tasks or []).lower()
    for hint, variant in _VARIANT_PATH_HINTS:
        if hint in joined:
            return variant
    return "ProdGlobalRelease"

def _preferred_variants(
    *,
    kover_tasks: list[str] | None,
    gradle_output: str | None = None,
    module_path: str = "",
    module_dir: str = "",
) -> list[str]:
    preferred: list[str] = []

    override = os.environ.get("TESTGEN_GRADLE_VARIANT", "").strip()
    if override:
        preferred.append(override)

    for task in kover_tasks or []:
        variant = variant_from_configured_task(str(task))
        if variant and variant not in preferred:
            preferred.append(variant)

    from_output = variant_from_gradle_output(gradle_output or "", module_path=module_path)
    if from_output and from_output not in preferred:
        preferred.append(from_output)

    if module_dir:
        from_report = variant_from_kover_report_path(module_dir)
        if from_report and from_report not in preferred:
            preferred.append(from_report)

    hinted = gradle_variant_hint(kover_tasks)
    if hinted not in preferred:
        preferred.append(hinted)

    for variant in _VARIANT_PREFERENCE:
        if variant not in preferred:
            preferred.append(variant)

    return preferred

def parse_unit_test_tasks_from_gradle_output(output: str) -> list[str]:
    tasks: list[str] = []
    for line in (output or "").splitlines():
        line = line.strip()
        if not line:
            continue
        match = _TASKS_OUTPUT_UNIT_TEST.match(line)
        if match:
            task_name = match.group(1)
            if task_name not in tasks:
                tasks.append(task_name)
    return tasks

@lru_cache(maxsize=32)
def discover_module_unit_test_tasks(project_root: str, module_path: str) -> tuple[str, ...]:
    """Query Gradle for verification-group unit-test tasks available on a module."""
    if not project_root or not module_path:
        return ()

    gradlew = os.path.join(project_root, "gradlew")
    if not os.path.isfile(gradlew):
        return ()

    command = [
        "./gradlew",
        f"{module_path}:tasks",
        "--group=verification",
        "--no-daemon",
        "-q",
    ]
    try:
        result = subprocess.run(
            command,
            cwd=project_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ()

    if result.returncode != 0:
        return tuple(parse_unit_test_tasks_from_gradle_output(result.stdout or ""))

    return tuple(parse_unit_test_tasks_from_gradle_output(result.stdout or ""))

def pick_unit_test_task_name(
    available_tasks: list[str] | tuple[str, ...],
    preferred_variants: list[str],
) -> str | None:
    available = list(available_tasks)
    if not available:
        return None

    for variant in preferred_variants:
        candidate = f"test{variant}UnitTest"
        if candidate in available:
            return candidate

    for variant in _VARIANT_PREFERENCE:
        candidate = f"test{variant}UnitTest"
        if candidate in available:
            return candidate

    return available[0]

def resolve_module_unit_test_task_name(
    *,
    project_root: str,
    module_path: str,
    kover_tasks: list[str] | None = None,
    gradle_output: str | None = None,
    test_file_path: str = "",
) -> str:
    configured = [str(task) for task in (kover_tasks or [])]
    explicit_test_tasks = [
        _task_name_from_task(task)
        for task in configured
        if re.search(r":?test[A-Za-z0-9]*UnitTest$", task)
    ]
    if explicit_test_tasks:
        return explicit_test_tasks[0]

    module_dir = ""
    if project_root and test_file_path:
        from UnitTest_gen.kotlin.project import find_owning_module_dir

        module_dir = find_owning_module_dir(project_root, test_file_path)

    preferred = _preferred_variants(
        kover_tasks=configured,
        gradle_output=gradle_output,
        module_path=module_path,
        module_dir=module_dir,
    )
    available = discover_module_unit_test_tasks(project_root, module_path)
    picked = pick_unit_test_task_name(available, preferred)
    if picked:
        return picked

    return f"test{preferred[0]}UnitTest"

_UNIT_TEST_TASK = re.compile(r":?test[A-Za-z0-9]*UnitTest$")
_ANDROID_TEST_TASK = re.compile(r":?connected[A-Za-z0-9]*AndroidTest$")
_JACOCO_REPORT_TASK = re.compile(
    r":?create[A-Za-z0-9]*CoverageReport$|:?jacoco[A-Za-z0-9]*Report$",
    re.IGNORECASE,
)
_KOVER_TASK = re.compile(r":?kover", re.IGNORECASE)
_ANDROID_TEST_TASK_NAME = re.compile(r"^connected([A-Za-z0-9]+)AndroidTest$")
_TASKS_OUTPUT_ANDROID_TEST = re.compile(r"^(connected[A-Za-z0-9]+AndroidTest)\b")
_COMPILE_ANDROID_TEST_TASK = re.compile(r":?compile[A-Za-z0-9]*AndroidTestKotlin$")
_COMPILE_ANDROID_TEST_TASK_NAME = re.compile(r"^compile([A-Za-z0-9]+)AndroidTestKotlin$")
_TASKS_OUTPUT_COMPILE_ANDROID_TEST = re.compile(r"^(compile[A-Za-z0-9]+AndroidTestKotlin)\b")

def split_cli_gradle_tasks(
    tasks: list[str] | None,
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Split CLI ``-G`` tasks into unit, androidTest, jacoco, and kover/other buckets."""
    unit_test: list[str] = []
    android_test: list[str] = []
    jacoco: list[str] = []
    kover_or_other: list[str] = []
    for task in tasks or []:
        name = str(task).strip()
        if not name:
            continue
        if _UNIT_TEST_TASK.search(name):
            unit_test.append(name)
        elif _ANDROID_TEST_TASK.search(name):
            android_test.append(name)
        elif _JACOCO_REPORT_TASK.search(name):
            jacoco.append(name)
        elif _KOVER_TASK.search(name):
            kover_or_other.append(name)
        else:
            kover_or_other.append(name)
    return unit_test, android_test, jacoco, kover_or_other

def cli_acceptance_tasks(tasks: list[str] | None) -> list[str]:
    """Tasks for baseline/final unit coverage gates — never paired with ``--tests``."""
    unit_test, android, jacoco, other = split_cli_gradle_tasks(tasks)
    ordered = list(dict.fromkeys([*(tasks or []), *unit_test, *other]))
    return [str(task).strip() for task in ordered if str(task).strip()]

def cli_instrumented_acceptance_tasks(tasks: list[str] | None) -> list[str]:
    """Connected androidTest + JaCoCo report tasks for instrumented gates."""
    _, android, jacoco, _ = split_cli_gradle_tasks(tasks)
    if android or jacoco:
        return list(dict.fromkeys([*android, *jacoco]))
    return []

def parse_android_test_tasks_from_gradle_output(output: str) -> list[str]:
    tasks: list[str] = []
    for line in (output or "").splitlines():
        line = line.strip()
        if not line:
            continue
        match = _TASKS_OUTPUT_ANDROID_TEST.match(line)
        if match:
            name = match.group(1)
            if name not in tasks:
                tasks.append(name)
    return tasks

@lru_cache(maxsize=32)
def discover_module_android_test_tasks(project_root: str, module_path: str) -> tuple[str, ...]:
    if not project_root or not module_path:
        return ()
    gradlew = os.path.join(project_root, "gradlew")
    if not os.path.isfile(gradlew):
        return ()
    command = ["./gradlew", f"{module_path}:tasks", "--group=verification", "--no-daemon", "-q"]
    try:
        result = subprocess.run(
            command,
            cwd=project_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ()
    if result.returncode != 0:
        return tuple(parse_android_test_tasks_from_gradle_output(result.stdout or ""))
    return tuple(parse_android_test_tasks_from_gradle_output(result.stdout or ""))

def parse_compile_android_test_tasks_from_gradle_output(output: str) -> list[str]:
    tasks: list[str] = []
    for line in (output or "").splitlines():
        line = line.strip()
        if not line:
            continue
        match = _TASKS_OUTPUT_COMPILE_ANDROID_TEST.match(line)
        if match:
            name = match.group(1)
            if name not in tasks:
                tasks.append(name)
    return tasks

@lru_cache(maxsize=32)
def discover_module_compile_android_test_tasks(project_root: str, module_path: str) -> tuple[str, ...]:
    if not project_root or not module_path:
        return ()
    gradlew = os.path.join(project_root, "gradlew")
    if not os.path.isfile(gradlew):
        return ()
    command = ["./gradlew", f"{module_path}:tasks", "--group=build", "--no-daemon", "-q"]
    try:
        result = subprocess.run(
            command,
            cwd=project_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ()
    if result.returncode != 0:
        return tuple(parse_compile_android_test_tasks_from_gradle_output(result.stdout or ""))
    return tuple(parse_compile_android_test_tasks_from_gradle_output(result.stdout or ""))

def pick_compile_android_test_task_name(
    available_tasks: list[str] | tuple[str, ...],
    preferred_variants: list[str],
) -> str | None:
    available = list(available_tasks)
    if not available:
        return None
    for variant in preferred_variants:
        candidate = f"compile{variant}AndroidTestKotlin"
        if candidate in available:
            return candidate
    for variant in _VARIANT_PREFERENCE:
        candidate = f"compile{variant}AndroidTestKotlin"
        if candidate in available:
            return candidate
    return available[0]

def pick_android_test_task_name(
    available_tasks: list[str] | tuple[str, ...],
    preferred_variants: list[str],
) -> str | None:
    available = list(available_tasks)
    if not available:
        return None
    for variant in preferred_variants:
        candidate = f"connected{variant}AndroidTest"
        if candidate in available:
            return candidate
    for variant in _VARIANT_PREFERENCE:
        candidate = f"connected{variant}AndroidTest"
        if candidate in available:
            return candidate
    return available[0]

def resolve_module_android_test_task_name(
    *,
    project_root: str,
    module_path: str,
    configured_tasks: list[str] | None = None,
    gradle_output: str | None = None,
    test_file_path: str = "",
) -> str:
    configured = [str(task) for task in (configured_tasks or [])]
    explicit = [
        _task_name_from_task(task)
        for task in configured
        if _ANDROID_TEST_TASK.search(task)
    ]
    if explicit:
        return explicit[0]

    module_dir = ""
    if project_root and test_file_path:
        from UnitTest_gen.kotlin.project import find_owning_module_dir

        module_dir = find_owning_module_dir(project_root, test_file_path)
    preferred = _preferred_variants(
        kover_tasks=configured,
        gradle_output=gradle_output,
        module_path=module_path,
        module_dir=module_dir,
    )
    available = discover_module_android_test_tasks(project_root, module_path)
    picked = pick_android_test_task_name(available, preferred)
    if picked:
        return picked
    return f"connected{preferred[0]}AndroidTest"

def default_instrumented_gradle_tasks(module_path: str) -> list[str]:
    variant = os.environ.get("TESTGEN_GRADLE_VARIANT", "DevDebug")
    return [
        f"{module_path}:connected{variant}AndroidTest",
        f"{module_path}:create{variant}CoverageReport",
    ]

def resolve_instrumented_verify_invocation(
    *,
    project_root: str,
    test_file_path: str,
    cli_tasks: list[str] | None,
    gradle_output: str | None = None,
) -> GradleInvocation:
    """Connected androidTest scoped to one instrumentation class."""
    configured = cli_instrumented_acceptance_tasks(cli_tasks)
    if not configured:
        module_path = _module_path_for_test(project_root, test_file_path, cli_tasks)
        configured = default_instrumented_gradle_tasks(module_path or ":app")
    android_tasks = [t for t in configured if _ANDROID_TEST_TASK.search(t)]
    if not android_tasks:
        module_path = _module_path_for_test(project_root, test_file_path, cli_tasks)
        task_name = resolve_module_android_test_task_name(
            project_root=project_root,
            module_path=module_path,
            configured_tasks=cli_tasks,
            gradle_output=gradle_output,
            test_file_path=test_file_path,
        )
        android_tasks = [f"{module_path}:{task_name}" if module_path else task_name]
    fqn = test_class_fqn(test_file_path)
    return GradleInvocation(
        tasks=android_tasks,
        gradle_args=[
            "-Pandroid.testInstrumentationRunnerArguments.class",
            fqn,
        ],
    )

def resolve_instrumented_compile_invocation(
    *,
    project_root: str,
    test_file_path: str,
    cli_tasks: list[str] | None,
    gradle_output: str | None = None,
) -> GradleInvocation:
    """Compile androidTest Kotlin sources without running connected tests."""
    configured = [str(task) for task in (cli_tasks or [])]
    explicit = [
        task
        for task in configured
        if _COMPILE_ANDROID_TEST_TASK.search(task)
    ]
    if explicit:
        return GradleInvocation(tasks=[explicit[0]], gradle_args=[])
    module_path = _module_path_for_test(project_root, test_file_path, configured)
    module_dir = ""
    if project_root and test_file_path:
        from UnitTest_gen.kotlin.project import find_owning_module_dir

        module_dir = find_owning_module_dir(project_root, test_file_path)
    preferred = _preferred_variants(
        kover_tasks=configured,
        gradle_output=gradle_output,
        module_path=module_path,
        module_dir=module_dir,
    )
    available = discover_module_compile_android_test_tasks(project_root, module_path)
    picked = pick_compile_android_test_task_name(available, preferred)
    if picked:
        task = f"{module_path}:{picked}" if module_path else picked
        return GradleInvocation(tasks=[task], gradle_args=[])
    variant = preferred[0] if preferred else "DevDebug"
    task = f"{module_path}:compile{variant}AndroidTestKotlin" if module_path else f"compile{variant}AndroidTestKotlin"
    return GradleInvocation(tasks=[task], gradle_args=[])

def resolve_instrumented_gate_tasks(
    *,
    project_root: str,
    test_file_path: str,
    cli_tasks: list[str] | None,
) -> list[str]:
    configured = cli_instrumented_acceptance_tasks(cli_tasks)
    if configured:
        return configured
    module_path = _module_path_for_test(project_root, test_file_path, cli_tasks)
    return default_instrumented_gradle_tasks(module_path or ":app")

def resolve_target_test_invocation(
    *,
    project_root: str,
    test_file_path: str,
    kover_tasks: list[str] | None,
    gradle_output: str | None = None,
) -> GradleInvocation:
    """Return the active module/variant unit-test task scoped to one test class."""
    configured = [str(task) for task in (kover_tasks or [])]
    explicit_test_tasks = [task for task in configured if _UNIT_TEST_TASK.search(task)]
    if explicit_test_tasks:
        task = explicit_test_tasks[0]
    else:
        if project_root:
            module_path = _module_path_for_test(project_root, test_file_path, configured)
        else:
            from UnitTest_gen.kotlin.project import find_project_root_for_path

            module_path = _module_path_for_test(
                find_project_root_for_path(test_file_path) or "",
                test_file_path,
                configured,
            )
        task_name = resolve_module_unit_test_task_name(
            project_root=project_root,
            module_path=module_path,
            kover_tasks=configured,
            gradle_output=gradle_output,
            test_file_path=test_file_path,
        )
        task = f"{module_path}:{task_name}" if module_path else task_name
    return GradleInvocation(tasks=[task], gradle_args=test_class_gradle_args(test_file_path))

def resolve_fast_verify_invocation(
    *,
    project_root: str,
    test_file_path: str,
    cli_tasks: list[str] | None,
    gradle_output: str | None = None,
) -> GradleInvocation:
    """Unit-test task + ``--tests`` only — never include Kover report tasks."""
    return resolve_target_test_invocation(
        project_root=project_root,
        test_file_path=test_file_path,
        kover_tasks=cli_tasks,
        gradle_output=gradle_output,
    )

def format_gradle_command(invocation: GradleInvocation) -> str:
    """Human-readable ``./gradlew …`` line for prompts."""
    parts = ["./gradlew", *invocation.tasks, *invocation.gradle_args]
    return " ".join(parts)
