# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Validates miscellaneous generated test patterns.
"""Miscellaneous validation rules."""

from __future__ import annotations

import os
import re

from UnitTest_gen.kotlin.kotlin_analysis import source_uses_carui_toolbar_progress
from UnitTest_gen.kotlin.project_context import (
    choose_robolectric_sdk_for_module,
    find_owning_module_dir,
    find_project_root_for_path,
    is_android_platform_installed,
    parse_module_compile_sdk,
    parse_module_min_sdk,
)
from UnitTest_gen.kotlin.test_stack_guidance import (
    collect_module_test_dependency_versions,
    parse_module_robolectric_version,
    project_robolectric_version,
)
from UnitTest_gen.kotlin.strategy_contracts import validation_repair_intent


def collect_carui_validation_issues(
    test_code: str, output_file_path: str, source_code: str, test_report
) -> list[str]:
    issues: list[str] = []

    if source_uses_carui_toolbar_progress(source_code or "") and (
        "ToolbarController" in test_code or "CarUi.requireToolbar" in test_code
    ):
        has_progress_controller_stub = bool(
            re.search(
                r"(?:whenever|`when`|when)\s*\([^)]*\.\s*(?:progressBar|getProgressBar\s*\(\s*\))\s*\)\s*\.thenReturn\s*\(",
                test_code,
            )
            or re.search(
                r"doReturn\s*\([^)]*\)\s*\.\s*whenever\s*\([^)]*\)\s*\.\s*(?:progressBar|getProgressBar\s*\(\s*\))",
                test_code,
            )
        )
        if not has_progress_controller_stub:
            issues.append(
                "invalid_carui_progress_fixture: " + validation_repair_intent("invalid_carui_static_fixture")
            )
        if re.search(r"verify\s*\(\s*[^)]*\.progressBar\b|verify\s*\(\s*[^)]*getProgressBar\s*\(", test_code):
            issues.append(
                "invalid_carui_progress_verification: " + validation_repair_intent("invalid_carui_static_fixture")
            )
        if re.search(r"\.thenReturn\(\s*mock\s*(?:<[^>]+>)?\s*\(\s*\)\s*\)", test_code):
            issues.append(
                "invalid_carui_progress_fixture: " + validation_repair_intent("invalid_carui_static_fixture")
            )

    if "CarUi.requireToolbar" in test_code and re.search(r"CarUi\.requireToolbar\s*\(\s*any(?:<[^>]+>)?\s*\(", test_code):
        issues.append(
            "invalid_carui_static_fixture: " + validation_repair_intent("invalid_carui_static_fixture")
        )

    if "registerBackListener" in (source_code or "") and "registerBackListener" in test_code:
        guessed_kotlin_callback = bool(
            re.search(r"(?:argumentCaptor|ArgumentCaptor)\s*<\s*\(\s*\)\s*->\s*Boolean\s*>", test_code)
            or re.search(r"\b(?:val|var|lateinit\s+var)\s+[A-Za-z_][A-Za-z0-9_]*\s*:\s*\(\s*\)\s*->\s*Boolean", test_code)
            or re.search(r"\bslot\s*<\s*\(\s*\)\s*->\s*Boolean\s*>", test_code)
        )
        if guessed_kotlin_callback:
            issues.append(
                "invalid_carui_back_listener_fixture: "
                + validation_repair_intent("invalid_carui_back_listener_fixture")
            )

    if "MenuItem" in test_code and re.search(r"\b[A-Za-z_][A-Za-z0-9_]*\.onClick\??\.invoke\s*\(", test_code):
        issues.append(
            "invalid_carui_menu_callback_api: "
            + validation_repair_intent("invalid_carui_menu_callback_api")
        )

    if "NavDeepLinkRequest" in (source_code or "") and "TestNavHostController" in test_code and ".setGraph(" not in test_code:
        issues.append(
            "invalid_nav_deeplink_fixture: " + validation_repair_intent("invalid_nav_deeplink_fixture")
        )

    return issues


def collect_junit_declaration_validation_issues(
    test_code: str, output_file_path: str, source_code: str, test_report
) -> list[str]:
    issues: list[str] = []
    if "import org.junit.Test" not in test_code and "@Test" in test_code:
        issues.append("JUnit4 tests should import org.junit.Test.")
    if any(function.visibility == "private" for function in test_report.tests.test_functions):
        issues.append("Generated tests should keep @Test methods public/internal to JUnit.")
    if test_report.tests.malformed_test_declaration_lines or any(
        function.has_extra_parameter_list or function.parameter_list_count > 1
        for function in test_report.tests.test_functions
    ):
        issues.append(
            "JUnit4 test declarations should have exactly one parameter list. "
            "Use `fun `test name`() { ... }`, not `fun `test name`()()`."
        )
    return issues


def collect_robolectric_sdk_validation_issues(
    test_code: str, output_file_path: str, source_code: str, test_report
) -> list[str]:
    issues: list[str] = []
    if (
        "activeNetwork" in (source_code or "")
        and "getNetworkCapabilities" in (source_code or "")
        and (
            re.search(r"\.setActiveNetworkInfo\s*\(\s*null\s*\)", test_code)
            or (
                "ShadowNetworkInfo.newInstance(" in test_code
                and "NetworkInfo.DetailedState" not in test_code
            )
        )
    ):
        issues.append(
            "deprecated_robolectric_api: "
            + validation_repair_intent("deprecated_robolectric_api")
        )
    project_root = find_project_root_for_path(output_file_path)
    module_dir = find_owning_module_dir(project_root, output_file_path) if project_root else os.path.dirname(output_file_path)
    module_min_sdk = parse_module_min_sdk(module_dir)
    module_compile_sdk = parse_module_compile_sdk(module_dir)
    if module_min_sdk is not None:
        selected_robolectric_sdk = choose_robolectric_sdk_for_module(module_min_sdk, module_compile_sdk)
        for config_sdk in test_report.tests.config_sdks:
            if config_sdk < module_min_sdk:
                issues.append(
                    f"Robolectric @Config(sdk = [{config_sdk}]) is below the owning module minSdk {module_min_sdk}. "
                    f"Use @Config(sdk = [{selected_robolectric_sdk or module_min_sdk}]) when explicit SDK config is needed, or omit @Config."
                )
            elif module_compile_sdk is not None and config_sdk > module_compile_sdk:
                issues.append(
                    f"Robolectric @Config(sdk = [{config_sdk}]) is above the owning module compileSdk {module_compile_sdk}. "
                    f"Use @Config(sdk = [{selected_robolectric_sdk or module_compile_sdk}]) when explicit SDK config is needed, or omit @Config."
                )
            elif selected_robolectric_sdk is not None and config_sdk > selected_robolectric_sdk and not is_android_platform_installed(config_sdk):
                issues.append(
                    f"Robolectric @Config(sdk = [{config_sdk}]) targets an Android SDK platform that is not installed locally. "
                    f"Use installed SDK {selected_robolectric_sdk}, or install android-{config_sdk}."
                )
        if selected_robolectric_sdk is None:
            upper_bound_text = f" and <= compileSdk {module_compile_sdk}" if module_compile_sdk is not None else ""
            issues.append(
                f"No local Android SDK platform >= module minSdk {module_min_sdk}{upper_bound_text} was found. "
                f"Install one in that range, for example sdkmanager \"platforms;android-{module_min_sdk}\"."
            )

    robolectric_version = parse_module_robolectric_version(module_dir) if module_dir else None
    if not robolectric_version and project_root:
        robolectric_version = project_robolectric_version(project_root)
    stack_versions = collect_module_test_dependency_versions(module_dir, project_root or "") if module_dir else {}
    if robolectric_version:
        try:
            robolectric_major = int(robolectric_version.split(".", 1)[0])
        except ValueError:
            robolectric_major = 0
        if robolectric_major >= 4 and (
            "org.robolectric.ShadowDialog" in test_code
            or "flushMainThreadQueue" in test_code
        ):
            issues.append(
                "deprecated_robolectric_api: " + validation_repair_intent("deprecated_robolectric_api")
            )

    if stack_versions.get("mockito_kotlin") and re.search(r"\bio\.mockk\b|import\s+io\.mockk", test_code):
        issues.append(
            "nonstandard_mock_framework: " + validation_repair_intent("nonstandard_mock_framework")
        )

    if stack_versions.get("mockito_kotlin") and re.search(r"\bsetIsIndeterminate\s*\(", test_code):
        issues.append(
            "invalid_carui_progress_verification: " + validation_repair_intent("invalid_carui_progress_verification")
        )

    return issues


def collect_dotted_test_name_validation_issues(
    test_code: str, output_file_path: str, source_code: str, test_report
) -> list[str]:
    issues: list[str] = []
    dotted_backtick_test_name = re.search(
        r"@Test(?:\([^)]*\))?\s*\n\s*fun\s+`[^`]*\.[^`]*`\s*\(",
        test_code,
    )
    if dotted_backtick_test_name:
        issues.append(
            "JUnit test method names should use JVM-safe words even inside Kotlin backticks. "
            "Use JVM-safe names such as bodyTripList instead of body.tripList."
        )
    return issues


def collect_appauth_validation_issues(
    test_code: str, output_file_path: str, source_code: str, test_report
) -> list[str]:
    issues: list[str] = []
    uses_appauth_auth_state = any(
        marker in (source_code or "")
        for marker in (
            "net.openid.appauth.AuthState",
            "AuthState",
            "createTokenRefreshRequest",
            "TokenResponse.Builder",
            "AuthorizationServiceConfiguration",
        )
    )
    if uses_appauth_auth_state:
        if re.search(r"(?:net\.openid\.appauth\.)?AuthState\.Builder\s*\(", test_code):
            issues.append(
                "AppAuth AuthState.Builder is not a verified API in this project. "
                "Use AuthState().jsonSerializeString() for unauthorized storage paths, or inject a Mockito mock AuthState and stub public getters for authorized branches."
            )

        if re.search(r"AuthState::class\.java\.getDeclaredField\(\s*\"idToken\"\s*\)", test_code):
            issues.append(
                "Generated tests should use AppAuth AuthState public behavior for idToken. "
                "Inject a Mockito mock AuthState into the source/repository private authState field and stub mockAuthState.idToken."
            )

        if re.search(r"AuthState\.jsonDeserialize\s*\(", test_code):
            issues.append(
                "Generated tests should provide verified serialized AuthState values or inject a mocked AuthState into the source/repository private authState field."
            )

        if re.search(
            r"\.update\s*\(\s*any\(\)\s*,\s*(?:any\(\)|isNull\(\))\s*\)",
            test_code,
        ):
            issues.append(
                "AppAuth AuthState.update is overloaded; untyped any() matchers cause overload ambiguity. "
                "Use typed matchers for the exact overload (TokenResponse for refreshToken, AuthorizationResponse for login exchange) "
                "or exercise update through verified real AppAuth objects."
            )

        refresh_updates_auth_response = bool(
            re.search(r"\.update\s*\(\s*any<\s*AuthorizationResponse\s*>", test_code)
            and (
                "refreshToken" in (source_code or "")
                or "TokenResponse.Builder" in (source_code or "")
                or re.search(r"createTokenRefreshRequest\s*\(", source_code or "")
            )
        )
        if refresh_updates_auth_response:
            issues.append(
                "AppAuth refreshToken calls AuthState.update(TokenResponse, AuthorizationException?). "
                "Use verify(mockAuthState).update(any<TokenResponse>(), isNull()) (or isNull<AuthorizationException>()), "
                "not AuthorizationResponse."
            )

        appauth_value_property_stub = re.search(
            r"whenever\s*\(\s*[^)]*\.(?:configuration|clientId|requestParameters|request)\s*\)",
            test_code,
        )
        if appauth_value_property_stub and any(
            symbol in test_code
            for symbol in ("TokenRequest", "AuthorizationResponse", "AuthorizationRequest")
        ):
            issues.append(
                "Build real AppAuth request/config/token value objects for properties such as "
                "TokenRequest.configuration, TokenRequest.clientId, TokenRequest.requestParameters, "
                "or AuthorizationResponse.request in token exchange or refresh flows."
            )

        if "AuthorizationServiceDiscovery(" in test_code:
            missing_discovery_fields = [
                field
                for field in (
                    "issuer",
                    "authorization_endpoint",
                    "token_endpoint",
                    "jwks_uri",
                    "response_types_supported",
                    "subject_types_supported",
                    "id_token_signing_alg_values_supported",
                )
                if field not in test_code
            ]
            if missing_discovery_fields:
                issues.append(
                    "AppAuth AuthorizationServiceDiscovery test JSON must include mandatory OIDC fields: "
                    + ", ".join(missing_discovery_fields)
                    + "."
                )

        if (
            "getAuthorizationRequestIntent" in source_code
            and re.search(r"\.getLoginIntent\s*\(\s*true\s*\)", test_code)
            and "ActivityNotFoundException" not in test_code
        ):
            issues.append(
                "AppAuth browser login intent path can throw ActivityNotFoundException under Robolectric "
                "when no browser activity is registered. Prefer the webview branch or assert this local "
                "exception while verifying discovery was called."
            )

        if (
            re.search(r"assertFailsWith\s*<\s*IllegalStateException\s*>\s*\{[^}]*\.refreshToken\s*\(", test_code, re.DOTALL)
            or re.search(r"assertThrows\s*\([^)]*IllegalStateException[^)]*\)\s*\{[^}]*\.refreshToken\s*\(", test_code, re.DOTALL)
        ) and "catch (e: Exception)" in source_code and "clearAuthState()" in source_code:
            issues.append(
                "refreshToken source catches auth exceptions and clears state instead of propagating. "
                "Assert observable fallback behavior such as storage clear/no-crash instead of assertFailsWith."
            )

        appauth_storage_setup = (
            "storage.getAuthStateJson()" in test_code
            or "KEY_AUTH_STATE_JSON" in test_code
            or '"auth_storage"' in test_code
        )
        if appauth_storage_setup and any(
            token_field in test_code
            for token_field in ("access_token", "id_token", "refresh_token")
        ):
            issues.append(
                "Raw access_token/id_token/refresh_token JSON is not a verified AuthState serialized shape. "
                "Use AuthState().jsonSerializeString() only for empty unauthorized state, or inject a mocked AuthState with public getter stubs for authorized token/user-claim tests."
            )

    return issues


def collect_validation_issues(test_code: str, output_file_path: str, source_code: str, test_report) -> list[str]:
    """Aggregate all miscellaneous validation issues (for standalone use)."""
    issues: list[str] = []
    for collector in (
        collect_carui_validation_issues,
        collect_junit_declaration_validation_issues,
        collect_robolectric_sdk_validation_issues,
        collect_dotted_test_name_validation_issues,
        collect_appauth_validation_issues,
    ):
        issues.extend(collector(test_code, output_file_path, source_code, test_report))
    return issues
