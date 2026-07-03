# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Validates Apollo and API extension test patterns.
"""Apollo and API extension validation rules."""

from __future__ import annotations

import re

from UnitTest_gen.kotlin.kotlin_analysis import (
    has_apollo_response_extension_function,
    kotlin_declared_class_names,
)


_APOLLO_API_PACKAGE = r"(?:[A-Za-z_][A-Za-z0-9_]*\.)+journetlog\.api"


def collect_validation_issues(test_code: str, output_file_path: str, source_code: str, test_report) -> list[str]:
    issues: list[str] = []

    invented_apollo_nested_type = re.search(
        r"\b(?:[A-Za-z0-9_]+(?:Mutation|Query)\.[A-Za-z0-9_]+)\.(?:Body|Trip)\b",
        test_code,
    )
    if invented_apollo_nested_type:
        issues.append(
            "Possible invented Apollo nested type: "
            f"{invented_apollo_nested_type.group(0)}. "
            "Use only verified generated sibling classes such as Operation.Body or Operation.TripList."
        )

    invented_apollo_nested_class_name = re.search(
        r"Class\.forName\(\s*\"[^\"]+(?:Mutation|Query)\\\$[A-Z][A-Za-z0-9_]+\\\$(?:Body|TripList|Trip)\"",
        test_code,
    )
    if invented_apollo_nested_class_name:
        issues.append(
            "Apollo generated child class name looks nested under a response receiver. "
            "Use verified sibling class names such as Operation$Body or Operation$TripList, not Operation$Receiver$Body."
        )

    direct_apollo_response_constructor = re.search(
        r"\b[A-Z][A-Za-z0-9_]*(?:Mutation|Query)\.[A-Z][A-Za-z0-9_]*\s*\(",
        test_code,
    )
    if direct_apollo_response_constructor:
        issues.append(
            "Apollo operation response class appears to be constructed directly. "
            "Use Class.forName string references, raw Mockito.mock(Class<*>), and reflected ApiExtKt invocation "
            "for generated operation response receivers."
        )

    typed_apollo_receiver_mock = re.search(
        r"\bmock\s*<\s*[A-Z][A-Za-z0-9_]*(?:Mutation|Query)\.[A-Z][A-Za-z0-9_]*\s*>\s*\(",
        test_code,
    )
    if typed_apollo_receiver_mock:
        issues.append(
            "Apollo operation response receiver is mocked with a compile-time generated type. "
            "Use Class.forName plus raw Mockito.mock(Class<*>) and invoke ApiExtKt methods by Java reflection."
        )

    direct_apollo_type_import = re.search(
        rf"(?m)^\s*import\s+{_APOLLO_API_PACKAGE}\.(?:type\.)?\*\s*$",
        test_code,
    )
    if direct_apollo_type_import and has_apollo_response_extension_function(source_code):
        issues.append(
            "Wildcard Apollo generated imports make response tests depend on compile-time generated types. "
            "Use project model imports directly and reference operation response receivers through Class.forName strings."
        )

    apollo_generated_type_import = re.search(
        rf"(?m)^\s*import\s+{_APOLLO_API_PACKAGE}\.type\.[A-Za-z_][A-Za-z0-9_]*\s*$",
        test_code,
    )
    if apollo_generated_type_import and re.search(rf"{_APOLLO_API_PACKAGE}\.type", source_code):
        issues.append(
            "Apollo generated api.type imports make tests depend on compile-time generated classes. "
            "Use Class.forName strings and Java reflection for generated input return types."
        )

    invalid_invoke_helper_signature = re.search(
        r"fun\s+invokeApiExt\s*\([^)]*vararg\s+parameterTypes\s*:\s*Class<\*>[^)]*,\s*args\s*:\s*Array<Any\?>",
        test_code,
    )
    if invalid_invoke_helper_signature:
        issues.append(
            "invokeApiExt helper has invalid Kotlin parameter ordering: vararg cannot appear before args. "
            "Use invokeApiExt(methodName: String, parameterTypes: Array<Class<*>>, args: Array<Any?>)."
        )

    invalid_invoke_helper_call = re.search(
        r"invokeApiExt\s*\(\s*\"[A-Za-z0-9_]+\"\s*,\s*[A-Za-z_][A-Za-z0-9_]*\s*,\s*arrayOf\s*\(",
        test_code,
    )
    if invalid_invoke_helper_call:
        issues.append(
            "invokeApiExt should receive parameter types as an array. "
            "Call it as invokeApiExt(\"methodName\", arrayOf(receiverClass), arrayOf(receiver))."
        )

    if "mockApollo(" in test_code:
        mock_apollo_pair_as_getter_value = re.search(
            r"\"get[A-Za-z0-9_]+\"\s+to\s+mockApollo\s*\(",
            test_code,
        )
        if mock_apollo_pair_as_getter_value:
            issues.append(
                "mockApollo returns Pair<Class<*>, Any>. Getter maps should pass only the mocked instance. "
                "Use \"getBody\" to mockApollo(\"...\$Body\", mapOf(...)).second for nested Apollo body/list mocks."
            )

    return issues


def collect_reflection_validation_issues(
    test_code: str, output_file_path: str, source_code: str, test_report
) -> list[str]:
    issues: list[str] = []

    swallowed_reflection_getter = re.search(
        r"catch\s*\(\s*\w+\s*:\s*NoSuchMethodException\s*\)\s*\{[^}]*\}",
        test_code,
        re.DOTALL,
    )
    if swallowed_reflection_getter:
        issues.append(
            "Apollo reflection helper should fail fast when a getter is wrong. "
            "Remove catch/ignore around NoSuchMethodException so invalid generated API assumptions are visible."
        )

    if "ApiExtensionsKt" in test_code and "ApiExt.kt" not in test_code:
        issues.append(
            "ApiExt.kt top-level functions compile to ApiExtKt, not ApiExtensionsKt. "
            "Resolve the verified generated API package and use its ApiExtKt facade for reflected mapper invocation."
        )

    reflected_api_ext_without_unwrap = (
        re.search(
            r"getDeclaredMethod\s*\(\s*methodName\s*,\s*\*parameterTypes\s*\)\s*"
            r"(?:\.\s*)?invoke\s*\(\s*null\s*,\s*\*args\s*\)",
            test_code,
            re.DOTALL,
        )
        and "InvocationTargetException" not in test_code
        and "ApiExtKt" in test_code
    )
    if reflected_api_ext_without_unwrap:
        issues.append(
            "Reflected ApiExtKt invocation should unwrap InvocationTargetException by throwing targetException. "
            "This lets exception assertions see the source exception instead of the reflection wrapper."
        )

    if re.search(rf"{_APOLLO_API_PACKAGE}\.type", source_code):
        direct_input_mapper_result = re.search(
            r"(?m)^\s*val\s+[A-Za-z_][A-Za-z0-9_]*\s*=\s*[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\.to[A-Za-z0-9_]*\([^)]*\)",
            test_code,
        )
        if direct_input_mapper_result:
            issues.append(
                "Apollo generated input mapper is called directly, exposing the inaccessible api.type return type. "
                "Invoke the mapper through ApiExtKt Java reflection, store the result as Any?, and inspect verified getters by reflection only."
            )

        generated_input_cast = re.search(
            r"\bas\s+[A-Z][A-Za-z0-9_]*(?:Input|TripsInput|TripInput)\b",
            test_code,
        )
        if generated_input_cast:
            issues.append(
                "Apollo generated input mapper result is cast to a compile-time api.type class. "
                "Keep the reflected result as Any? and inspect verified getters through readApollo(result, \"getField\")."
            )

    if has_apollo_response_extension_function(source_code):
        fake_apollo_classes = [
            class_name
            for class_name in kotlin_declared_class_names(test_code)
            if class_name.endswith("Mutation") or class_name.endswith("Query")
        ]
        if fake_apollo_classes:
            issues.append(
                "Source extension functions need verified Apollo generated receiver classes. "
                "Use verified generated classes through reflection/Mockito, or target a reflected nullable/simple branch."
            )

    return issues
