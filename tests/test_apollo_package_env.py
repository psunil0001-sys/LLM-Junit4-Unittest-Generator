"""TESTGEN_APOLLO_API_PACKAGE placeholder + harness rename smoke checks."""
from __future__ import annotations

import os
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def _apollo_api_package_regex_from_env() -> str:
    """Mirror kotlin.validate._apollo_api_package_regex (local to avoid heavy imports)."""
    raw = (os.environ.get("TESTGEN_APOLLO_API_PACKAGE") or "your.app.api").strip()
    parts = [p for p in raw.split(".") if p] or ["your", "app", "api"]
    escaped = r"\.".join(re.escape(p) for p in parts)
    return rf"(?:[A-Za-z_][A-Za-z0-9_]*\.)+{escaped}"


def test_validate_defines_apollo_package_helper():
    text = (_REPO / "kotlin" / "validate.py").read_text(encoding="utf-8")
    assert "def _apollo_api_package_regex" in text
    assert "TESTGEN_APOLLO_API_PACKAGE" in text
    assert "your.app.api" in text
    assert "journeylog" not in text.lower()


def test_default_apollo_package_is_placeholder(monkeypatch):
    monkeypatch.delenv("TESTGEN_APOLLO_API_PACKAGE", raising=False)
    rx = _apollo_api_package_regex_from_env()
    assert "your" in rx and "app" in rx and "api" in rx
    assert "journeylog" not in rx.lower()
    assert re.search(rx + r"\.type", "com.example.your.app.api.type.Foo")


def test_custom_apollo_package_env(monkeypatch):
    monkeypatch.setenv("TESTGEN_APOLLO_API_PACKAGE", "acme.mobile.api")
    rx = _apollo_api_package_regex_from_env()
    assert "acme" in rx and "mobile" in rx and "api" in rx
    assert "journeylog" not in rx.lower()
    assert re.search(rx, "com.acme.mobile.api.Query")


def test_recipes_point_at_apollo_5_api_doc():
    recipes = (_REPO / "kotlin" / "recipes.py").read_text(encoding="utf-8")
    assert "apollo_runtime_5_1_0_api_index.json" in recipes
    assert "apollo_3_8_2_api_index.json" not in recipes
    assert (_REPO / "data" / "api_doc" / "apollo_runtime_5_1_0_api_index.json").is_file()
    assert (_REPO / "data" / "recipes" / "apollo_client_network_transport.md").is_file()


def test_no_project_harness_lockin_in_recipes():
    banned = (
        "journeylog",
        "HiltCarUiTestActivity",
        "HiltDelegateActivity",
        "HiltNavTestActivity",
        "HiltTestActivity",
        "TripsRepo",
        "HiltCarUiTheme",
    )
    roots = [
        _REPO / "data" / "recipes",
        _REPO / "data" / "prompt_skeletons",
        _REPO / "data" / "validation_rules",
    ]
    for root in roots:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in {".md", ".yaml", ".yml"}:
                continue
            text = path.read_text(encoding="utf-8")
            for token in banned:
                assert token not in text, f"{token} still in {path.relative_to(_REPO)}"
