# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Runs local Semgrep policies and returns normalized JSON findings.
"""Generic offline Semgrep-core execution helpers."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass

import yaml


class SemgrepUnavailableError(RuntimeError):
    """Raised when mandatory local Semgrep analysis cannot run."""


class SemgrepParseError(SemgrepUnavailableError):
    """Raised when semgrep-core cannot parse the supplied Kotlin source."""


@dataclass(frozen=True)
class SemgrepScanResult:
    version: str
    findings: tuple[dict, ...]


def require_semgrep_executable(executable: str = "") -> str:
    candidates = [executable, os.environ.get("SEMGREP_CORE_EXECUTABLE", ""), shutil.which("semgrep-core") or ""]
    package_spec = importlib.util.find_spec("semgrep")
    if package_spec and package_spec.origin:
        candidates.append(os.path.join(os.path.dirname(package_spec.origin), "bin", "semgrep-core"))
    resolved = next((os.path.abspath(path) for path in candidates if path and os.path.isfile(path)), "")
    if not resolved:
        raise SemgrepUnavailableError(
            "Semgrep is required for Kotlin test generation but semgrep-core was not found. "
            "Install UnitTest_gen/requirements.txt or set SEMGREP_CORE_EXECUTABLE."
        )
    return resolved


def scan_text_with_semgrep(
    source_text: str,
    rule_config: str | tuple[str, ...] | list[str],
    suffix: str,
    executable: str = "",
    jobs: int | None = 1,
    source_path: str = "",
) -> SemgrepScanResult:
    core = require_semgrep_executable(executable)
    rule_configs = (rule_config,) if isinstance(rule_config, str) else tuple(rule_config)
    if not rule_configs:
        raise SemgrepUnavailableError("At least one local Semgrep rule config is required.")
    rules = []
    for config_path in rule_configs:
        if not os.path.isfile(config_path):
            raise SemgrepUnavailableError(f"Local Semgrep rule config does not exist: {config_path}")
        try:
            with open(config_path, "r", encoding="utf-8") as handle:
                payload = yaml.safe_load(handle) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise SemgrepUnavailableError(f"Cannot parse local Semgrep rules {config_path}: {exc}") from exc
        rules.extend(payload.get("rules") or [])
    if not rules:
        raise SemgrepUnavailableError("Local Semgrep configuration contains no rules.")

    work_dir = _semgrep_work_dir()
    source_temp = ""
    rule_temp = ""
    target_temp = ""
    try:
        scan_path = ""
        if source_path and os.path.isfile(source_path):
            try:
                with open(source_path, "r", encoding="utf-8") as handle:
                    if handle.read() == (source_text or ""):
                        scan_path = os.path.abspath(source_path)
            except OSError:
                scan_path = ""
        if not scan_path:
            with tempfile.NamedTemporaryFile(
                "w", suffix=suffix, encoding="utf-8", delete=False, dir=work_dir
            ) as handle:
                handle.write(source_text or "")
                source_temp = handle.name
                scan_path = source_temp

        with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8", delete=False, dir=work_dir) as handle:
            json.dump({"rules": rules}, handle)
            rule_temp = handle.name
        target_payload = [
            "Targets",
            [[
                "CodeTarget",
                {
                    "path": {"fpath": scan_path, "ppath": "/" + scan_path.lstrip("/")},
                    "analyzer": "kotlin",
                    "products": ["sast"],
                },
            ]],
        ]
        with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8", delete=False, dir=work_dir) as handle:
            json.dump(target_payload, handle)
            target_temp = handle.name

        command = [
            core,
            "-json",
            "-rules",
            rule_temp,
            "-targets",
            target_temp,
            "-timeout",
            "10",
            "-timeout_threshold",
            "3",
            "-max_memory",
            "0",
            "-fast",
        ]
        if jobs is not None:
            command.extend(["-j", str(max(1, int(jobs)))])
        result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
        if result.returncode != 0:
            raise SemgrepUnavailableError(
                "Semgrep-core scan failed: " + (result.stderr or result.stdout or "unknown error").strip()
            )
        try:
            raw_output = result.stdout or ""
            json_start = raw_output.find("{")
            payload = json.loads(raw_output[json_start:] if json_start >= 0 else "{}")
        except json.JSONDecodeError as exc:
            raise SemgrepUnavailableError(f"Semgrep-core returned invalid JSON: {exc}") from exc
        errors = payload.get("errors") or []
        findings = tuple(payload.get("results") or ())
        if errors:
            details = "; ".join(str(item.get("message") or item) for item in errors[:3])
            if findings:
                return SemgrepScanResult(
                    version=str(payload.get("version") or "unknown"),
                    findings=findings,
                )
            raise SemgrepParseError(f"Semgrep-core reported analysis errors: {details}")
        return SemgrepScanResult(
            version=str(payload.get("version") or "unknown"),
            findings=findings,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SemgrepUnavailableError(f"Semgrep-core scan could not complete: {exc}") from exc
    finally:
        for path in (source_temp, rule_temp, target_temp):
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass


def _semgrep_work_dir() -> str:
    path = os.environ.get("TESTGEN_SEMGREP_CACHE_DIR") or os.path.join(
        os.getcwd(), "UnitTest_gen", ".cache", "semgrep"
    )
    os.makedirs(path, exist_ok=True)
    return path
