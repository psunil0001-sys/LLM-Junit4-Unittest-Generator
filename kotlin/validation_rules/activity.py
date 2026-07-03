# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Validates Activity lifecycle and Hilt test patterns.
"""Activity lifecycle, Hilt reflection, and BuildConfig validation rules."""

from __future__ import annotations

import re

from UnitTest_gen.kotlin.kotlin_analysis import classify_source


def collect_validation_issues(test_code: str, output_file_path: str, source_code: str, test_report) -> list[str]:
    issues: list[str] = []
    source_categories = classify_source(source_code or "").categories
    if "android_activity" in source_categories:
        direct_lifecycle = re.findall(
            r"\b(?!controller\b)([A-Za-z_][A-Za-z0-9_]*)\.(onCreate|onStart|onResume|onPause|onStop|onDestroy)\s*\(",
            test_code or "",
        )
        if direct_lifecycle:
            calls = ", ".join(sorted({f"{receiver}.{method}()" for receiver, method in direct_lifecycle}))
            issues.append(
                "invalid_direct_activity_lifecycle_call: "
                f"Do not invoke protected Activity lifecycle methods directly ({calls}); use Robolectric ActivityController."
            )
        if "hilt_android_activity" in source_categories and (
            re.search(r"\b[A-Za-z_][A-Za-z0-9_]*::class\.java\.getDeclaredField\s*\(", test_code or "")
            or "isAccessible = true" in (test_code or "")
        ):
            issues.append(
                "invalid_hilt_activity_reflection: Do not replace Hilt-injected or private Activity state through reflection. "
                "Use the verified production graph and public lifecycle/UI behavior, or report the missing seam."
            )
        if (
            "BuildConfig.IS_GAS_COUNTRY" in (source_code or "")
            and re.search(
                r"fun\s+`?[^`\n]*(?:isGasCountry|GasCountry)[^`\n]*true[^`\n]*`?\s*\(",
                test_code or "",
                re.IGNORECASE,
            )
        ):
            issues.append(
                "invalid_fixed_buildconfig_branch: IS_GAS_COUNTRY is fixed by the active Gradle variant. "
                "Do not generate a forced true-path Activity test without a source-visible override seam."
            )

    if re.search(r"\bassert(?:True|False)?\s*\(\s*!?\s*BuildConfig\.[A-Z0-9_]+\s*(?:,|\))", test_code or ""):
        issues.append(
            "invalid_buildconfig_assertion: BuildConfig flags are fixed by the active Gradle variant. "
            "Do not hard-code the opposite branch or assert the flag itself as behavior."
        )
    if re.search(
        r"assert(?:Equals|True)\s*\(\s*(BuildConfig\.[A-Z0-9_]+)\s*(?:==|,)\s*\1\s*\)",
        test_code or "",
    ):
        issues.append(
            "invalid_trivial_assertion: A BuildConfig value compared with itself is a tautology and cannot justify keeping the test."
        )

    return issues
