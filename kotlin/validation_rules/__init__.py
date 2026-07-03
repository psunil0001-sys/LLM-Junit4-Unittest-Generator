# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Registers generated Kotlin test validation rules.
"""Generated Kotlin test validation rules.

Controlled by TESTGEN_ENABLE_GUARDRAILS (default 1). When disabled, validate_generated_test_code returns [].
"""

from __future__ import annotations

from .activity import collect_validation_issues as collect_activity_validation_issues
from .android_resources import collect_validation_issues as collect_android_resource_validation_issues
from .apollo import (
    collect_reflection_validation_issues,
    collect_validation_issues as collect_apollo_validation_issues,
)
from .assertions import collect_validation_issues as collect_assertions_validation_issues
from .coverage_orchestration import collect_coverage_orchestration_validation_issues
from .coroutines import (
    collect_suspend_assert_validation_issues,
    collect_validation_issues as collect_coroutines_validation_issues,
    collect_viewmodel_validation_issues,
)
from .fragment import collect_lifecycle_validation_issues, collect_validation_issues as collect_fragment_validation_issues
from .fragment_viewmodel_quality import collect_fragment_viewmodel_quality_issues
from .hilt import collect_validation_issues as collect_hilt_validation_issues
from .misc import (
    collect_appauth_validation_issues,
    collect_carui_validation_issues,
    collect_dotted_test_name_validation_issues,
    collect_junit_declaration_validation_issues,
    collect_robolectric_sdk_validation_issues,
)
from .mockk import (
    collect_apollo_mock_stub_validation_issues,
    collect_mockito_import_and_api_validation_issues,
    collect_nested_stub_validation_issues,
    collect_primitive_matcher_validation_issues,
    collect_static_and_navigation_validation_issues,
)

VALIDATION_RULES = [
    collect_activity_validation_issues,
    collect_android_resource_validation_issues,
    collect_hilt_validation_issues,
    collect_fragment_validation_issues,
    collect_coroutines_validation_issues,
    collect_nested_stub_validation_issues,
    collect_viewmodel_validation_issues,
    collect_primitive_matcher_validation_issues,
    collect_carui_validation_issues,
    collect_static_and_navigation_validation_issues,
    collect_junit_declaration_validation_issues,
    collect_robolectric_sdk_validation_issues,
    collect_lifecycle_validation_issues,
    collect_dotted_test_name_validation_issues,
    collect_mockito_import_and_api_validation_issues,
    collect_suspend_assert_validation_issues,
    collect_appauth_validation_issues,
    collect_assertions_validation_issues,
    collect_apollo_validation_issues,
    collect_apollo_mock_stub_validation_issues,
    collect_reflection_validation_issues,
    collect_coverage_orchestration_validation_issues,
    collect_fragment_viewmodel_quality_issues,
]

__all__ = ["VALIDATION_RULES"]
