# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Adapts generic memory retrieval to Kotlin and Android source classifications.
"""Kotlin/Android tag inference for the reusable core memory store."""

import re

from UnitTest_gen.core.memory_store import (
    add_repair_lesson as _add_repair_lesson,
    retrieve_generation_lessons as _retrieve_generation_lessons,
    retrieve_repair_lessons as _retrieve_repair_lessons,
)
from UnitTest_gen.core.pipeline_config import get_config


MEMORY_TAG_MARKERS = {
    "android_fragment": ("fragment", "dialogfragment", "fragmentactivity"),
    "android_ui": ("robolectric", "view", "dialog", "activity", "android ui"),
    "android_navigation": ("navcontroller", "navigation", "navigate"),
    "hilt": ("hilt", "androidentrypoint", "bindvalue", "dagger/missingbinding"),
    "constructor_injection": ("constructor-injected", "constructor injection", "@inject constructor"),
    "viewmodel": ("viewmodel", "livedata", "savedstatehandle"),
    "coroutines_flow": ("coroutine", "flow", "runtest", "dispatcher"),
    "dagger_module": ("@binds", "@provides", "dagger module"),
    "room": ("room", "database", "@dao", "@entity"),
    "network": ("retrofit", "okhttp", "authenticator", "interceptor"),
    "apollo": ("apollo", "graphql", "mutation", "query"),
    "car": ("carui", "carproperty", "toolbarcontroller", "progressbarcontroller"),
    "worker_service": ("workmanager", "worker", "broadcastreceiver", "service"),
    "storage_json_auth": ("sharedpreferences", "json", "authstate", "appauth"),
    "firebase": ("firebase",),
    "logging": ("timber", "logging"),
    "android_log": ("android log", "log."),
    "mocking": ("mockito", "mockstatic", "mockedstatic", "matcher", "stubbing"),
    "assertion": ("assert", "wantedbutnotinvoked", "verification"),
    "coroutine_error": ("uncaughtexceptionsbeforetest", "dispatcher", "coroutine", "runtest"),
    "runtime_failure": ("junit", "assertionerror", "runtime exception", "failed"),
}


def infer_kotlin_memory_tags(key, item) -> set[str]:
    fields = [str(key)]
    if isinstance(item, dict):
        fields.extend(str(value) for value in item.values() if isinstance(value, (str, int, float)))
    text = "\n".join(fields).lower()
    return {
        tag
        for tag, markers in MEMORY_TAG_MARKERS.items()
        if any(marker in text for marker in markers)
    }


def _primary_memory_tags(source_categories) -> set[str]:
    categories = {str(category).strip().lower() for category in (source_categories or [])}
    required: set[str] = set()

    if "android_fragment" in categories:
        required.add("android_fragment")
    elif "viewmodel" in categories or "hilt_viewmodel" in categories:
        required.add("viewmodel")
    elif "apollo" in categories:
        required.add("apollo")
    elif "network" in categories:
        required.add("network")
    elif "room" in categories:
        required.add("room")
    elif "storage_json_auth" in categories:
        required.add("storage_json_auth")
    elif "worker_service" in categories:
        required.add("worker_service")
    elif "car" in categories:
        required.add("car")
    elif "dagger_module" in categories:
        required.add("dagger_module")
    elif "constructor_injection" in categories:
        required.add("constructor_injection")

    if "coroutines_flow" in categories and "viewmodel" not in categories and "hilt_viewmodel" not in categories:
        required.add("coroutines_flow")
    if "livedata" in categories:
        required.add("livedata")
    if "hilt_viewmodel" in categories:
        required.add("hilt_viewmodel")

    return required


def _forbidden_memory_tags(source_categories) -> set[str]:
    categories = {str(category).strip().lower() for category in (source_categories or [])}
    forbidden: set[str] = set()

    if "android_fragment" not in categories:
        forbidden.update({"android_fragment", "hilt_entrypoint"})
    if "viewmodel" not in categories and "hilt_viewmodel" not in categories:
        forbidden.update({"viewmodel", "hilt_viewmodel"})
    if "firebase" not in categories:
        forbidden.add("firebase")
    if "network" not in categories:
        forbidden.add("network")
    if "worker_service" not in categories:
        forbidden.add("worker_service")
    if "storage_json_auth" not in categories:
        forbidden.add("storage_json_auth")
    for tag in ("apollo", "room", "car", "dagger_module", "coroutines_flow"):
        if tag not in categories:
            forbidden.add(tag)
    if categories <= {"constructor_injection", "firebase"} and not (categories & {"android_ui", "android_fragment", "android_context"}):
        forbidden.update({"logging", "android_log"})

    return forbidden


def _filter_appcompat_r_lessons(formatted: str, source_categories) -> str:
    categories = {str(category).strip().lower() for category in (source_categories or [])}
    if "android_fragment" not in categories or "androidx.appcompat.r.id" not in (formatted or "").lower():
        return formatted or ""
    kept: list[str] = []
    for block in re.split(r"\n(?=Lesson: )", formatted or ""):
        if "androidx.appcompat.r.id" in block.lower():
            continue
        kept.append(block)
    return "\n".join(part for part in kept if part.strip()).strip()


def retrieve_generation_lessons(source_categories, class_name=None, phase="generation"):
    if not get_config().enable_memory_lessons:
        return ""
    formatted = _retrieve_generation_lessons(
        source_categories,
        class_name=class_name,
        phase=phase,
        tag_inference=infer_kotlin_memory_tags,
        required_categories=_primary_memory_tags(source_categories),
        forbidden_categories=_forbidden_memory_tags(source_categories),
        limit=2 if phase == "incremental" else 3,
    )
    return _filter_appcompat_r_lessons(formatted, source_categories)


def retrieve_repair_lessons(source_categories=None, repair_categories=None):
    if not get_config().enable_memory_lessons:
        return ""
    return _retrieve_repair_lessons(
        source_categories,
        repair_categories=repair_categories,
        tag_inference=infer_kotlin_memory_tags,
        required_categories=repair_categories,
    )


def add_repair_lesson(
    group_key,
    patch=None,
    fixed_code=None,
    source_categories=None,
    repair_categories=None,
):
    return _add_repair_lesson(
        group_key,
        patch=patch,
        fixed_code=fixed_code,
        source_categories=source_categories,
        repair_categories=repair_categories,
        tag_inference=infer_kotlin_memory_tags,
    )
