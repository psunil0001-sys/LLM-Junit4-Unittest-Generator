"""Recipe and api_doc catalogs for agent prompts."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from functools import lru_cache
from pathlib import Path

from UnitTest_gen.core import io as file_cache

PACKAGE_DIR = Path(__file__).resolve().parents[1]
API_DOC_DIR = PACKAGE_DIR / "data" / "api_doc"
RECIPES_DIR = PACKAGE_DIR / "data" / "recipes"
INSTRUMENTED_RECIPES_DIR = PACKAGE_DIR / "data" / "recipes" / "instrumented"

def select_by_triggers(
    base_dir: Path,
    *,
    categories: Sequence[str] = (),
    category_triggers: Mapping[str, str] | None = None,
    source_code: str = "",
    source_triggers: Sequence[tuple[Sequence[str], str]] = (),
    casefold: bool = True,
    skip: Callable[[str], bool] | None = None,
    haystack_extra: str = "",
) -> list[Path]:
    """Category-first then needle-triggered file selection under ``base_dir`` (deduped)."""
    chosen: list[Path] = []
    category_map = category_triggers or {}
    normalized = [str(tag).strip() for tag in (categories or ()) if str(tag).strip()]
    hay = f"{source_code}\n{haystack_extra}\n{' '.join(normalized)}"
    hay_l = hay.lower()

    def _add(filename: str) -> None:
        if skip and skip(filename):
            return
        path = base_dir / filename
        if path.is_file() and path not in chosen:
            chosen.append(path)

    for tag in normalized:
        filename = category_map.get(tag)
        if filename:
            _add(filename)

    for needles, filename in source_triggers:
        if skip and skip(filename):
            continue
        path = base_dir / filename
        if not path.is_file() or path in chosen:
            continue
        if casefold:
            if any(n.lower() in hay_l for n in needles):
                _add(filename)
        elif any(n in hay for n in needles) or any(n.lower() in hay_l for n in needles):
            _add(filename)
    return chosen

def format_path_catalog(
    paths: Sequence[Path],
    *,
    header_lines: Sequence[str],
    label_for: Callable[[Path], str] | None = None,
) -> str:
    """Absolute-path bullet catalog; empty string when ``paths`` is empty."""
    if not paths:
        return ""
    lines = list(header_lines)
    for path in paths:
        label = label_for(path) if label_for else path.stem
        lines.append(f"- {path.resolve()}  ({label})")
    return "\n".join(lines)

_API_CATEGORY_TRIGGERS: dict[str, str] = {
    "hilt_worker": "hilt_worker_testing.json",
    "hilt_fragment": "hilt_android_testing_2_49_api_index.json",
    "hilt_entrypoint": "hilt_android_testing_2_49_api_index.json",
    "hilt_service": "hilt_android_testing_2_49_api_index.json",
    "room": "room_testing_api_index.json",
    "osmdroid": "osmdroid_api_index.json",
    "apollo": "apollo_3_8_2_api_index.json",
    "coroutines_flow": "kotlinx_coroutines_test_1_7_3_api_index.json",
    "viewmodel": "kotlinx_coroutines_test_1_7_3_api_index.json",
    "android_navigation": "navigation_testing_2_8_api_index.json",
    "network": "retrofit_2_11_0_api_index.json",
    "car": "car_ui_lib_2_6_0_api_index.json",
    "carui_toolbar": "car_ui_lib_2_6_0_api_index.json",
    "storage_json_auth": "appauth_0_11_1_api_index.json",
}

_INSTRUMENTED_API_CATEGORY_TRIGGERS: dict[str, str] = {
    "hilt_fragment": "hilt_android_testing_2_49_api_index.json",
    "hilt_android_activity": "hilt_android_testing_2_49_api_index.json",
    "hilt_worker": "hilt_worker_testing.json",
    "android_work_manager": "hilt_worker_testing.json",
    "android_foreground_service": "androidx_test_ext_junit_api_index.json",
    "android_fragment": "androidx_test_ext_junit_api_index.json",
    "android_navigation": "navigation_testing_2_8_api_index.json",
}

_INSTRUMENTED_API_LIBRARY_TRIGGERS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("@HiltAndroidTest", "HiltAndroidRule", "@BindValue"), "hilt_android_testing_2_49_api_index.json"),
    (("ActivityScenario", "FragmentScenario", "AndroidJUnit4"), "androidx_test_ext_junit_api_index.json"),
    (("WorkManager", "TestListenableWorkerBuilder"), "hilt_worker_testing.json"),
    (("androidx.test.ext.junit",), "androidx_test_ext_junit_api_index.json"),
)

# (filename stem keywords in source) → api_doc file
_API_LIBRARY_TRIGGERS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("robolectric", "android.", "androidx.", "Timber", "Log.", "Context", "Looper"), "robolectric_4_13_api_index.json"),
    (("mockito.kotlin", "org.mockito", "whenever(", "argumentCaptor"), "mockito_kotlin_5_2_1_api_index.json"),
    (("io.mockk", "mockk(", "every {"), "mockk_1_13_12_api_index.json"),
    (("okhttp", "OkHttp", "HttpLoggingInterceptor", "Interceptor"), "okhttp_4_12_0_api_index.json"),
    (("retrofit", "Retrofit", "@GET", "@POST"), "retrofit_2_11_0_api_index.json"),
    (("hilt", "HiltAndroid", "@HiltAndroidTest", "@BindValue", "@HiltWorker", "AssistedInject"), "hilt_android_testing_2_49_api_index.json"),
    (("runTest", "coroutines.test", "TestDispatcher", "UnconfinedTestDispatcher"), "kotlinx_coroutines_test_1_7_3_api_index.json"),
    (("AppAuth", "net.openid.appauth"), "appauth_0_11_1_api_index.json"),
    (("car.ui", "CarUi", "com.android.car.ui"), "car_ui_lib_2_6_0_api_index.json"),
    (("TestNavHostController", "androidx.navigation.testing", "setViewNavController"), "navigation_testing_2_8_api_index.json"),
    (("InstantTaskExecutorRule", "LiveData", "androidx.arch.core"), "arch_core_testing_2_2_api_index.json"),
    (("apollographql", "ApolloClient", "ApolloHttpException", "NetworkTransport"), "apollo_3_8_2_api_index.json"),
    (("androidx.room", "@Dao", "@Database", "Room.inMemoryDatabaseBuilder", "Room.databaseBuilder"), "room_testing_api_index.json"),
    (("org.osmdroid", "osmdroid", "IMapController"), "osmdroid_api_index.json"),
)

# Longest prefix first (see api_doc_path_for_fqcn).
_FQCN_PREFIX_DOCS: tuple[tuple[str, str], ...] = (
    ("androidx.hilt.work", "hilt_worker_testing.json"),
    ("androidx.work", "hilt_worker_testing.json"),
    ("androidx.room", "room_testing_api_index.json"),
    ("org.osmdroid", "osmdroid_api_index.json"),
    ("androidx.hilt", "hilt_android_testing_2_49_api_index.json"),
    ("dagger.hilt", "hilt_android_testing_2_49_api_index.json"),
    ("dagger.", "hilt_android_testing_2_49_api_index.json"),
    ("androidx.navigation.testing", "navigation_testing_2_8_api_index.json"),
    ("androidx.navigation", "navigation_testing_2_8_api_index.json"),
    ("androidx.arch.core", "arch_core_testing_2_2_api_index.json"),
    ("com.apollographql.", "apollo_3_8_2_api_index.json"),
    ("kotlinx.coroutines", "kotlinx_coroutines_test_1_7_3_api_index.json"),
    ("org.mockito", "mockito_kotlin_5_2_1_api_index.json"),
    ("io.mockk", "mockk_1_13_12_api_index.json"),
    ("okhttp3.", "okhttp_4_12_0_api_index.json"),
    ("retrofit2.", "retrofit_2_11_0_api_index.json"),
    ("net.openid.", "appauth_0_11_1_api_index.json"),
    ("com.android.car.ui", "car_ui_lib_2_6_0_api_index.json"),
    ("timber.", "robolectric_4_13_api_index.json"),
    ("androidx.", "robolectric_4_13_api_index.json"),
    ("android.", "robolectric_4_13_api_index.json"),
)

def select_api_doc_files(source_code: str = "", *, categories: list[str] | tuple[str, ...] = ()) -> list[Path]:
    normalized_categories = [str(tag).strip() for tag in (categories or ()) if str(tag).strip()]
    chosen = select_by_triggers(
        API_DOC_DIR,
        categories=normalized_categories,
        category_triggers=_API_CATEGORY_TRIGGERS,
        source_code=source_code,
        source_triggers=_API_LIBRARY_TRIGGERS,
        casefold=True,
        haystack_extra=" ".join(normalized_categories),
    )
    # Always offer mockito-kotlin for JVM unit tests when nothing matched but file exists.
    if not chosen:
        fallback = API_DOC_DIR / "mockito_kotlin_5_2_1_api_index.json"
        if fallback.is_file():
            chosen.append(fallback)
    return chosen

def select_instrumented_api_doc_files(
    source_code: str = "", *, categories: list[str] | tuple[str, ...] = ()
) -> list[Path]:
    normalized_categories = [str(tag).strip() for tag in (categories or ()) if str(tag).strip()]
    chosen = select_by_triggers(
        API_DOC_DIR,
        categories=normalized_categories,
        category_triggers=_INSTRUMENTED_API_CATEGORY_TRIGGERS,
        source_code=source_code,
        source_triggers=_INSTRUMENTED_API_LIBRARY_TRIGGERS,
        casefold=True,
        haystack_extra=" ".join(normalized_categories),
    )
    if not chosen:
        for fallback_name in (
            "androidx_test_ext_junit_api_index.json",
            "hilt_android_testing_2_49_api_index.json",
        ):
            fallback = API_DOC_DIR / fallback_name
            if fallback.is_file():
                chosen.append(fallback)
                break
    return chosen

def api_doc_path_for_fqcn(fqcn: str) -> Path | None:
    """Absolute api_doc JSON for an SDK/library import, or None if uncatalogued."""
    lookup = (fqcn or "").strip()
    if lookup.endswith(".*"):
        lookup = lookup[:-2]
    if not lookup:
        return None
    simple = lookup.rsplit(".", 1)[-1]
    suffix_hit = ""
    for name, path in _documented_api_names():
        if name == lookup:
            return Path(path)
        if simple and (name == simple or name.endswith(f".{simple}")) and not suffix_hit:
            suffix_hit = path
    if suffix_hit:
        return Path(suffix_hit)
    prefixes = sorted(_FQCN_PREFIX_DOCS, key=lambda item: len(item[0]), reverse=True)
    for prefix, filename in prefixes:
        if prefix.endswith("."):
            matched = lookup.startswith(prefix)
        else:
            matched = lookup == prefix or lookup.startswith(prefix + ".")
        if matched and (path := API_DOC_DIR / filename).is_file():
            return path.resolve()
    return None

@lru_cache(maxsize=1)
def _documented_api_names() -> tuple[tuple[str, str], ...]:
    entries: list[tuple[str, str]] = []
    if not API_DOC_DIR.is_dir():
        return ()
    for path in sorted(API_DOC_DIR.glob("*.json")):
        payload = file_cache.read_json(path, default_factory=dict)
        if not payload:
            continue
        abs_path = str(path.resolve())
        for name in _documented_type_names(payload):
            entries.append((name, abs_path))
    return tuple(entries)

def _documented_type_names(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return []
    names: list[str] = []
    for key in ("api", "api_index"):
        items = payload.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            for field in ("qualified_name", "class"):
                value = item.get(field)
                if isinstance(value, str) and value.strip():
                    names.append(value.strip())
    return names

def _library_label(path: Path) -> str:
    """Short library name + version from JSON header only (not the full index)."""
    payload = file_cache.read_json(path, default_factory=dict)
    if not isinstance(payload, dict) or not payload:
        return path.stem
    name = str(payload.get("library") or payload.get("name") or path.stem)
    version = ""
    coords = payload.get("coordinates") or {}
    if isinstance(coords, dict) and coords.get("version"):
        version = str(coords.get("version"))
    elif payload.get("version"):
        version = str(payload.get("version"))
    return f"{name} {version}".strip() if version else name

def format_api_doc_catalog(
    source_code: str = "",
    *,
    categories: list[str] | tuple[str, ...] = (),
    test_layer: str = "unit",
) -> str:
    """Absolute-path catalog for on-demand view/grep (no embedded API bodies)."""
    from UnitTest_gen.kotlin.layer import TestLayer

    if TestLayer.normalize(test_layer) == TestLayer.INSTRUMENTED:
        paths = select_instrumented_api_doc_files(source_code, categories=categories)
        header = (
            "VERIFIED INSTRUMENTED LIBRARY API DOCS — androidTest lane; do not use Robolectric api_docs.",
            "view or grep ONE listed file when you need a signature "
            "(prefer grep for a class/method name; view only if needed). "
            "At most 2 api_doc files per turn. Recipes define harness order; api_docs define APIs.",
        )
    else:
        paths = select_api_doc_files(source_code, categories=categories)
        header = (
            "VERIFIED LIBRARY API DOCS — mandatory for third-party signatures; do not invent or contradict.",
            "view or grep ONE listed file when you need a signature "
            "(prefer grep for a class/method name; view only if needed). "
            "At most 2 api_doc files per turn. Recipes define harness order; api_docs define APIs.",
        )
    return format_path_catalog(
        paths,
        header_lines=header,
        label_for=_library_label,
    )

_FRAGMENT_SUPER_NEEDLES = (
    ": Fragment(",
    ": Fragment()",
    ": Fragment {",
    ": Fragment\n",
    "androidx.fragment.app.Fragment()",
)
_VIEWMODEL_DELEGATE_NEEDLES = (
    "by viewModels",
    "activityViewModels(",
    "by activityViewModels",
)
_SERVICE_NEEDLES = (
    ": Service(",
    ": Service()",
    ": Service {",
    ": Service\n",
    "android.app.Service",
)
_ACTIVITY_SUPERS = ("AppCompatActivity", "FragmentActivity", "ComponentActivity", ": Activity")
_HEADING = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)

_COMPANION_RECIPES = frozenset({
    "carui_toolbar_host.md",
    "android_navigation_host.md",
    "permissions_activity_result.md",
    "osmdroid_mapview.md",
})

_PRIMARY_TO_LANE: dict[str, str] = {
    "hilt_worker_dowork.md": "hilt_worker",
    "hilt_service_full_harness.md": "hilt_service",
    "hilt_fragment_activity_viewmodels.md": "hilt_fragment",
    "hilt_fragment_lifecycle.md": "hilt_fragment",
    "hilt_android_activity_create.md": "hilt_activity",
    "plain_fragment_activity_viewmodels.md": "robolectric_non_hilt",
    "plain_activity_robolectric.md": "robolectric_non_hilt",
    "worker_service_receiver.md": "robolectric_non_hilt",
    "permissions_activity_result.md": "robolectric_non_hilt",
    "viewmodel_flow_livedata.md": "plain_jvm",
    "android_object_singleton.md": "plain_jvm",
    "apollo_client_network_transport.md": "plain_jvm",
    "room_in_memory_dao.md": "plain_jvm",
    "osmdroid_mapview.md": "plain_jvm",
}

_RECIPE_LABELS: dict[str, str] = {
    "hilt_android_activity_create.md": "Hilt Activity .create() harness",
    "hilt_fragment_lifecycle.md": "Hilt Fragment attach",
    "hilt_fragment_activity_viewmodels.md": "Hilt Fragment viewModels()",
    "hilt_worker_dowork.md": "@HiltWorker doWork()",
    "hilt_service_full_harness.md": "Hilt Service full harness",
    "carui_toolbar_host.md": "CarUi toolbar stub host",
    "android_navigation_host.md": "Navigation test host",
    "plain_fragment_activity_viewmodels.md": "Plain Fragment viewModels()",
    "plain_activity_robolectric.md": "Plain Activity Robolectric",
    "viewmodel_flow_livedata.md": "ViewModel Flow/LiveData",
    "android_object_singleton.md": "Kotlin object Android seam",
    "worker_service_receiver.md": "Service/Receiver unit host",
    "permissions_activity_result.md": "Permissions / ActivityResult",
    "apollo_client_network_transport.md": "Apollo QueueNetworkTransport harness",
    "room_in_memory_dao.md": "Room in-memory DAO",
    "osmdroid_mapview.md": "osmdroid MapView / IMapController",
}

def _has_fragment_super(hay: str) -> bool:
    return any(token in hay for token in _FRAGMENT_SUPER_NEEDLES)

def _has_viewmodel_delegate(hay: str) -> bool:
    return any(token in hay for token in _VIEWMODEL_DELEGATE_NEEDLES)

def _has_service_super(hay: str) -> bool:
    return any(token in hay for token in _SERVICE_NEEDLES)

def _recipe_path(filename: str) -> Path | None:
    path = RECIPES_DIR / filename
    return path if path.is_file() else None

def _select_primary_recipe(
    source_code: str = "",
    *,
    categories: list[str] | tuple[str, ...] = (),
) -> tuple[str, Path | None]:
    """Exclusive first-match decision tree → (recipe_lane, primary path)."""
    normalized = [str(tag).strip() for tag in (categories or ()) if str(tag).strip()]
    tags = set(normalized)
    hay = source_code or ""
    hay_l = hay.lower()
    has_entrypoint = "@AndroidEntryPoint" in hay
    has_fragment = _has_fragment_super(hay)
    has_delegate = _has_viewmodel_delegate(hay) or "delegated_viewmodel_fragment" in tags
    has_worker = "@HiltWorker" in hay or "hilt_worker" in tags
    has_hilt_service = "hilt_service" in tags or (has_entrypoint and _has_service_super(hay))

    def _pick(filename: str, lane: str) -> tuple[str, Path | None]:
        path = _recipe_path(filename)
        return (lane, path) if path else ("plain_jvm", None)

    # 1–2 Hilt Worker / Service
    if has_worker:
        return _pick("hilt_worker_dowork.md", "hilt_worker")
    if has_hilt_service:
        return _pick("hilt_service_full_harness.md", "hilt_service")

    # 3–5 Hilt Fragment / Activity
    if has_fragment and has_entrypoint:
        if has_delegate:
            return _pick("hilt_fragment_activity_viewmodels.md", "hilt_fragment")
        return _pick("hilt_fragment_lifecycle.md", "hilt_fragment")
    if has_entrypoint and not has_fragment and (
        "hilt_android_activity" in tags
        or "android_activity" in tags
        or any(token in hay for token in _ACTIVITY_SUPERS)
    ):
        return _pick("hilt_android_activity_create.md", "hilt_activity")

    # 6–8 Roboelectric non-Hilt
    if has_fragment and has_delegate and not has_entrypoint:
        return _pick("plain_fragment_activity_viewmodels.md", "robolectric_non_hilt")
    if not has_entrypoint and (
        "android_activity" in tags or any(token in hay for token in _ACTIVITY_SUPERS)
    ):
        return _pick("plain_activity_robolectric.md", "robolectric_non_hilt")
    if "worker_service" in tags or any(
        n in hay for n in ("BroadcastReceiver", "ForegroundService", "onStartCommand")
    ):
        return _pick("worker_service_receiver.md", "robolectric_non_hilt")

    # 9 Plain JVM / specialized — never Hilt or Fragment attach
    jvm_order = (
        ("apollo_client_network_transport.md", (("ApolloClient", "ApolloHttpException", "com.apollographql"), ("apollo",))),
        ("room_in_memory_dao.md", (("androidx.room", "@Dao", "@Database", "Room.inMemoryDatabaseBuilder"), ("room",))),
        ("viewmodel_flow_livedata.md", (("ViewModel", "StateFlow", "LiveData"), ("viewmodel", "hilt_viewmodel"))),
        ("android_object_singleton.md", (("object ",), ("kotlin_object_seam",))),
        ("osmdroid_mapview.md", (("org.osmdroid", "IMapController", "osmdroid.views.MapView"), ("osmdroid",))),
    )
    for filename, (needles, cat_tags) in jvm_order:
        if any(t in tags for t in cat_tags) or any(n in hay for n in needles) or any(
            n.lower() in hay_l for n in needles
        ):
            path = _recipe_path(filename)
            if path:
                return _PRIMARY_TO_LANE.get(filename, "plain_jvm"), path
    return "plain_jvm", None

def _select_companion_recipes(
    source_code: str = "",
    *,
    categories: list[str] | tuple[str, ...] = (),
    primary_name: str = "",
) -> list[Path]:
    """Up to two companion stub recipes (never the primary attach playbook)."""
    normalized = [str(tag).strip() for tag in (categories or ()) if str(tag).strip()]
    tags = set(normalized)
    hay = source_code or ""
    hay_l = hay.lower()
    candidates: list[str] = []
    if "carui_toolbar" in tags or "carui_progress" in tags or "carui_back_listener" in tags or "CarUi.requireToolbar" in hay:
        candidates.append("carui_toolbar_host.md")
    if "android_navigation" in tags or any(n in hay for n in ("findNavController", "NavController", "NavDeepLinkRequest")):
        candidates.append("android_navigation_host.md")
    if (
        "permissions" in tags
        or any(n in hay_l for n in ("checkselfpermission", "registerforactivityresult", "requestpermissions"))
    ):
        candidates.append("permissions_activity_result.md")
    if "osmdroid" in tags or any(n in hay for n in ("org.osmdroid", "IMapController", "osmdroid.views.MapView")):
        candidates.append("osmdroid_mapview.md")
    out: list[Path] = []
    for name in candidates:
        if name == primary_name or name not in _COMPANION_RECIPES:
            continue
        path = _recipe_path(name)
        if path and path not in out:
            out.append(path)
        if len(out) >= 2:
            break
    return out

def select_recipe_lane(
    source_code: str = "",
    *,
    categories: list[str] | tuple[str, ...] = (),
) -> str:
    """Exclusive harness lane for this CUT."""
    lane, _ = _select_primary_recipe(source_code, categories=categories)
    return lane

def select_recipe_files(
    source_code: str = "",
    *,
    categories: list[str] | tuple[str, ...] = (),
    pinned_recipe_id: str = "",
) -> list[Path]:
    """Return [primary, optional companion] for this CUT (decision-tree exclusive)."""
    lane, primary = _select_primary_recipe(source_code, categories=categories)
    del lane  # lane exposed via select_recipe_lane / selected_recipe_selection
    pinned = str(pinned_recipe_id or "").strip()
    if pinned and pinned != "none":
        pinned_path = _recipe_path(f"{pinned}.md")
        if pinned_path is not None:
            primary = pinned_path
    primary_name = primary.name if primary else ""
    companions = _select_companion_recipes(
        source_code, categories=categories, primary_name=primary_name,
    )
    out: list[Path] = []
    if primary is not None:
        out.append(primary)
    for path in companions:
        if path not in out:
            out.append(path)
    return out[:3]

def selected_recipe_id(
    source_code: str = "",
    *,
    categories: list[str] | tuple[str, ...] = (),
    pinned_recipe_id: str = "",
) -> str:
    """Primary recipe stem for plan frontmatter, or ``none``."""
    _lane, recipe_id = selected_recipe_selection(
        source_code, categories=categories, pinned_recipe_id=pinned_recipe_id,
    )
    return recipe_id

def selected_recipe_selection(
    source_code: str = "",
    *,
    categories: list[str] | tuple[str, ...] = (),
    pinned_recipe_id: str = "",
) -> tuple[str, str]:
    """Return ``(recipe_lane, recipe_id)`` for pinning into plan frontmatter."""
    lane, primary = _select_primary_recipe(source_code, categories=categories)
    pinned = str(pinned_recipe_id or "").strip()
    if pinned and pinned != "none":
        path = _recipe_path(f"{pinned}.md")
        if path is not None:
            return _PRIMARY_TO_LANE.get(path.name, lane), pinned
    if primary is None:
        return lane, "none"
    return _PRIMARY_TO_LANE.get(primary.name, lane), primary.stem

def _recipe_label(path: Path) -> str:
    return _RECIPE_LABELS.get(path.name, path.stem)

def _body_after_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end < 0:
        return text
    return text[end + 4 :].lstrip("\n")

def _keep_heading(title: str, *, stubs_only: bool = False) -> bool:
    head = title.strip()
    lower = head.lower()
    if lower.startswith("must not"):
        return True
    if lower.startswith("must"):
        return True
    if stubs_only:
        return False
    return "ordered write steps" in lower or "full coverage checklist" in lower

def _keep_fix_heading(title: str) -> bool:
    lower = title.strip().lower()
    if lower.startswith("must not") or lower.startswith("must"):
        return True
    if "case table" in lower or lower.startswith("forbidden"):
        return True
    return False

def _extract_recipe_fix_playbook(path: Path) -> str:
    """Compact MUST / MUST NOT sections for fix passes."""
    text = file_cache.read_text(path, default="")
    if not text:
        return ""
    body = _body_after_frontmatter(text)
    matches = list(_HEADING.finditer(body))
    if not matches:
        return ""
    chunks: list[str] = []
    for index, match in enumerate(matches):
        if not _keep_fix_heading(match.group(1)):
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        chunks.append(body[match.start() : end].rstrip())
    return "\n\n".join(chunks).strip()

def _extract_recipe_playbook(path: Path, *, stubs_only: bool = False) -> str:
    text = file_cache.read_text(path, default="")
    if not text:
        return ""
    body = _body_after_frontmatter(text)
    matches = list(_HEADING.finditer(body))
    if not matches:
        return ""
    chunks: list[str] = []
    for index, match in enumerate(matches):
        if not _keep_heading(match.group(1), stubs_only=stubs_only):
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        chunks.append(body[match.start() : end].rstrip())
    return "\n\n".join(chunks).strip()

def format_recipe_catalog(
    source_code: str = "",
    *,
    categories: list[str] | tuple[str, ...] = (),
    pinned_recipe_id: str = "",
    recipe_lane: str = "",
) -> str:
    """Pipeline-owned recipe block: lane + primary Ordered steps; companion stubs only."""
    lane, recipe_id = selected_recipe_selection(
        source_code, categories=categories, pinned_recipe_id=pinned_recipe_id,
    )
    if recipe_lane.strip():
        lane = recipe_lane.strip()
    primary = _recipe_path(f"{recipe_id}.md") if recipe_id != "none" else None
    companions = _select_companion_recipes(
        source_code,
        categories=categories,
        primary_name=primary.name if primary else "",
    )
    lines = [
        "VERIFIED TEST RECIPES — pipeline-owned; do not invent or violate harness order.",
        f"SELECTED LANE: {lane}",
        f"SELECTED RECIPE (PRIMARY): {recipe_id}",
        f"Frontmatter recipe_id MUST be: {recipe_id} (primary stem only; do not invent a second harness).",
        f"Frontmatter recipe_lane MUST be: {lane}",
    ]
    if primary is None and not companions:
        lines.append(
            "No specialized harness playbook for this CUT. "
            "Do not invent Hilt/ViewModel/Apollo recipes."
        )
        return "\n".join(lines)
    lines.append(
        "Approach MUST follow the PRIMARY Ordered write steps below. "
        "COMPANION playbooks are API stubs only — not a second attach strategy."
    )
    if primary is not None:
        lines.append(f"PRIMARY RECIPE: {primary.resolve()}  ({_recipe_label(primary)})")
        playbook = _extract_recipe_playbook(primary, stubs_only=False)
        if playbook:
            lines.extend(["", playbook])
    for path in companions:
        lines.append(f"COMPANION STUBS: {path.resolve()}  ({_recipe_label(path)})")
        playbook = _extract_recipe_playbook(path, stubs_only=True)
        if playbook:
            lines.extend(["", playbook])
    return "\n".join(lines).rstrip()

_INSTRUMENTED_CATEGORY_RECIPES: dict[str, str] = {
    "android_foreground_service": "instrumented_foreground_service_test.md",
    "android_work_manager": "instrumented_work_manager_test.md",
    "needs_real_system_service": "instrumented_hilt_android_test.md",
    "real_notification_channel": "instrumented_hilt_android_test.md",
    "bluetooth_hardware": "instrumented_hilt_android_test.md",
    "camera_hardware": "instrumented_hilt_android_test.md",
    "hilt_android_activity": "instrumented_hilt_android_test.md",
    "hilt_fragment": "instrumented_hilt_fragment_test.md",
    "android_fragment": "instrumented_hilt_fragment_test.md",
    "android_navigation": "instrumented_hilt_fragment_test.md",
    "android_ui": "instrumented_hilt_fragment_test.md",
    "hilt_service": "instrumented_foreground_service_test.md",
    "hilt_worker": "instrumented_work_manager_test.md",
    "worker_service": "instrumented_work_manager_test.md",
}

_INSTRUMENTED_SOURCE_TRIGGERS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("@HiltAndroidTest", "FragmentScenario"), "instrumented_hilt_fragment_test.md"),
    (("@HiltAndroidTest", "ActivityScenario"), "instrumented_hilt_android_test.md"),
    (("@HiltWorker",), "instrumented_work_manager_test.md"),
    (("WorkManager",), "instrumented_work_manager_test.md"),
    (("ForegroundService",), "instrumented_foreground_service_test.md"),
)

def _instrumented_recipe_path(filename: str) -> Path | None:
    path = INSTRUMENTED_RECIPES_DIR / filename
    return path if path.is_file() else None

def select_instrumented_recipe_files(
    source_code: str = "",
    *,
    categories: list[str] | tuple[str, ...] = (),
) -> list[Path]:
    """Return instrumented (androidTest) playbooks — never unit/Robolectric recipes."""
    normalized = [str(tag).strip() for tag in (categories or ()) if str(tag).strip()]
    tags = set(normalized)
    hay = source_code or ""
    hay_l = hay.lower()
    chosen: list[Path] = []
    for tag in normalized:
        filename = _INSTRUMENTED_CATEGORY_RECIPES.get(tag)
        if filename:
            path = _instrumented_recipe_path(filename)
            if path and path not in chosen:
                chosen.append(path)
    for needles, filename in _INSTRUMENTED_SOURCE_TRIGGERS:
        path = _instrumented_recipe_path(filename)
        if not path or path in chosen:
            continue
        if any(n.lower() in hay_l for n in needles):
            chosen.append(path)
    if not chosen:
        default = _instrumented_recipe_path("instrumented_hilt_android_test.md")
        if default:
            chosen.append(default)
    return chosen[:2]

def format_instrumented_recipe_catalog(
    source_code: str = "",
    *,
    categories: list[str] | tuple[str, ...] = (),
) -> str:
    """Pipeline-owned instrumented recipe block for androidTest generation."""
    paths = select_instrumented_recipe_files(source_code, categories=categories)
    lines = [
        "VERIFIED INSTRUMENTED TEST RECIPES — androidTest lane; do not use Robolectric or src/test.",
        "Output MUST live under src/androidTest/.../*InstrumentedTest.kt (or project androidTest convention).",
        "MUST NOT duplicate scenarios already closed by unit/Kover tests in src/test.",
    ]
    if not paths:
        lines.append("No instrumented playbook matched — use Hilt + AndroidJUnit4 + ActivityScenario defaults.")
        return "\n".join(lines)
    primary = paths[0]
    lines.append(f"SELECTED INSTRUMENTED RECIPE (PRIMARY): {primary.stem}")
    lines.append(f"PRIMARY RECIPE: {primary.resolve()}")
    playbook = _extract_recipe_playbook(primary, stubs_only=False)
    if playbook:
        lines.extend(["", playbook])
    for companion in paths[1:]:
        lines.append(f"COMPANION: {companion.resolve()}")
        stub = _extract_recipe_playbook(companion, stubs_only=True)
        if stub:
            lines.extend(["", stub])
    return "\n".join(lines).rstrip()

def format_instrumented_fix_recipe_context(
    source_code: str = "",
    *,
    categories: list[str] | tuple[str, ...] = (),
    api_doc_text: str = "",
) -> str:
    """Compact instrumented recipe context for fix passes."""
    paths = select_instrumented_recipe_files(source_code, categories=categories)
    if not paths:
        return (
            "FIX INSTRUMENTED RECIPE CONTEXT — use @HiltAndroidTest + AndroidJUnit4 + "
            "ActivityScenario/FragmentScenario; no Robolectric."
        )
    primary = paths[0]
    lines = [
        "FIX INSTRUMENTED RECIPE CONTEXT — androidTest lane only; no Robolectric or src/test.",
        f"SELECTED INSTRUMENTED RECIPE: {primary.stem}",
        f"PRIMARY RECIPE: {primary.resolve()}",
    ]
    playbook = _extract_recipe_fix_playbook(primary)
    if playbook:
        lines.extend(["", playbook])
    doc_text = (api_doc_text or "").strip()
    if doc_text:
        first_doc_line = next(
            (line for line in doc_text.splitlines() if line.strip().startswith("- ")),
            "",
        )
        if first_doc_line:
            lines.extend(["", "INSTRUMENTED API DOC (grep/view for signatures):", first_doc_line])
    return "\n".join(lines).rstrip()

def format_fix_recipe_context(
    source_code: str = "",
    *,
    project_root: str = "",
    categories: list[str] | tuple[str, ...] = (),
    pinned_recipe_id: str = "",
    recipe_lane: str = "",
    api_doc_text: str = "",
    gradle_output: str = "",
) -> str:
    """Compact recipe + harness paths for fix passes (gen prompt owns the full catalog)."""
    lane, recipe_id = selected_recipe_selection(
        source_code, categories=categories, pinned_recipe_id=pinned_recipe_id,
    )
    if recipe_lane.strip():
        lane = recipe_lane.strip()
    primary = _recipe_path(f"{recipe_id}.md") if recipe_id != "none" else None
    hay = (source_code or "") + "\n" + (gradle_output or "")
    apollo_cut = (
        recipe_id == "apollo_client_network_transport"
        or "com.apollographql" in hay
        or "ApolloHttpException" in hay
        or "apollo3" in hay.lower()
    )
    if primary is None and not apollo_cut:
        return ""

    lines = [
        "FIX RECIPE CONTEXT — re-read harness/recipe paths below; do not invent Apollo APIs.",
        f"SELECTED RECIPE: {recipe_id} (lane={lane})",
    ]
    if primary is not None:
        lines.append(f"PRIMARY RECIPE: {primary.resolve()}  ({_recipe_label(primary)})")
        playbook = _extract_recipe_fix_playbook(primary)
        if playbook:
            lines.extend(["", playbook])
    if apollo_cut and project_root:
        from UnitTest_gen.kotlin.imports import apollo_harness_reference_paths

        harness_rows = apollo_harness_reference_paths(project_root)
        if harness_rows:
            lines.append("")
            lines.append("APOLLO TEST HARNESS (Read these absolute paths before editing TARGET):")
            for fqcn, abs_path in harness_rows:
                lines.append(f"- {fqcn} -> {abs_path}")
    doc_text = (api_doc_text or "").strip()
    if apollo_cut and not doc_text:
        doc_text = format_api_doc_catalog(source_code, categories=list(categories or ("apollo",)))
    if apollo_cut and doc_text:
        first_doc_line = next(
            (line for line in doc_text.splitlines() if line.strip().startswith("- ")),
            "",
        )
        if first_doc_line:
            lines.extend(["", "APOLLO API DOC (grep/view for signatures):", first_doc_line])
    return "\n".join(lines).rstrip()
