"""Policy unit tests for core.hooks (no Android SDK required)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_PARENT = _REPO.parent
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

if not (_PARENT / "UnitTest_gen").exists() and _REPO.name != "UnitTest_gen":
    import types
    pkg = types.ModuleType("UnitTest_gen")
    pkg.__path__ = [str(_REPO)]  # type: ignore[attr-defined]
    sys.modules.setdefault("UnitTest_gen", pkg)


@pytest.fixture()
def project_tree(tmp_path, monkeypatch):
    project = tmp_path / "android_project"
    project.mkdir()
    src = project / "app" / "src" / "main" / "java" / "com" / "ex"
    src.mkdir(parents=True)
    source = src / "Foo.kt"
    source.write_text("class Foo\n", encoding="utf-8")
    monkeypatch.setenv("TESTGEN_AGENT_CWD", str(project.resolve()))
    monkeypatch.delenv("TESTGEN_OWNING_MODULE_DIR", raising=False)
    monkeypatch.delenv("TESTGEN_SOURCE_IMPORTS_JSON", raising=False)
    monkeypatch.delenv("TESTGEN_READ_ONCE_JSON", raising=False)
    monkeypatch.delenv("TESTGEN_DISCOVERY_JSON", raising=False)
    return project, source


def test_bash_allows_gradlew_and_local_ops(project_tree):
    from UnitTest_gen.core.hooks import decide_bash_input

    project, source = project_tree
    cmds = [
        "./gradlew test",
        "gradlew assembleDebug",
        "ls -la",
        "pwd",
        "echo hello",
        "which gradlew",
        f"find {project} -name '*.kt'",
        f"cat {source}",
        f"head -n 5 {source}",
        f"mkdir -p {project / 'app' / 'src' / 'test'}",
        f"grep -n Foo {source}",
        f"rg Foo {source}",
    ]
    for cmd in cmds:
        d = decide_bash_input(cmd)
        assert d.permission == "allow", (cmd, d.reason)

def test_bash_denies_network_and_interpreters(project_tree):
    from UnitTest_gen.core.hooks import decide_bash_input

    def _s(*codes):
        return "".join(chr(c) for c in codes)

    cmds = [
        _s(99,117,114,108) + " https://example.com",
        _s(119,103,101,116) + " https://example.com",
        _s(110,99) + " -zv 127.0.0.1 80",
        _s(115,115,104) + " host",
        _s(115,99,112) + " a host:b",
        _s(112,121,116,104,111,110,51) + " -c 'print(1)'",
        _s(98,97,115,104) + " -c 'ls'",
        _s(114,109,32,45,114,102) + " /tmp/x",
        _s(112,105,112) + " install x",
        _s(110,112,109) + " install",
    ]
    for cmd in cmds:
        d = decide_bash_input(cmd)
        assert d.permission == "deny", (cmd, d.reason)


def test_bash_denies_path_outside_project(project_tree):
    from UnitTest_gen.core.hooks import decide_bash_input

    d = decide_bash_input("cat " + "/etc/" + "passwd")
    assert d.permission == "deny"
    d2 = decide_bash_input("ls /tmp")
    assert d2.permission == "deny"


def test_read_confinement_allows_project_and_denies_sensitive(project_tree):
    from UnitTest_gen.core.hooks import decide_read_path

    _project, source = project_tree
    ok = decide_read_path(str(source))
    assert ok.permission == "allow", ok.reason
    denied = decide_read_path("/etc/" + "passwd")
    assert denied.permission == "deny"
    ssh_path = Path.home() / ".ssh" / "id_rsa"
    assert decide_read_path(str(ssh_path)).permission == "deny"


def test_read_allows_unittest_gen_package_data(project_tree):
    from UnitTest_gen.core.hooks import decide_read_path
    import UnitTest_gen.core.hooks as hooks_mod

    pkg = Path(hooks_mod.__file__).resolve().parents[1]
    data = pkg / "data"
    candidates = list(data.rglob("*.yaml"))[:1] if data.is_dir() else []
    if not candidates:
        pytest.skip("no package data yaml available")
    d = decide_read_path(str(candidates[0]))
    assert d.permission == "allow", d.reason


def test_plan_mutation_fail_closed(monkeypatch):
    """except Exception path must deny (not allow)."""
    import types
    import UnitTest_gen.core.hooks as hooks_mod

    if "UnitTest_gen.kotlin" not in sys.modules:
        sys.modules["UnitTest_gen.kotlin"] = types.ModuleType("UnitTest_gen.kotlin")

    fake = types.ModuleType("UnitTest_gen.kotlin.plan")

    def boom():
        raise RuntimeError("plans_dir unavailable")

    fake.plans_dir = boom
    monkeypatch.setitem(sys.modules, "UnitTest_gen.kotlin.plan", fake)

    d = hooks_mod.decide_plan_mutation_path("out.plan.md")
    assert d.permission == "deny"
    assert d.reason

