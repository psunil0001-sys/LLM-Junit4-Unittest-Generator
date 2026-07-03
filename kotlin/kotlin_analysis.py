# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Classifies Kotlin sources and extracts AST-based symbols and framework signals.
"""Kotlin/tree-sitter source analysis helpers."""

import re
from dataclasses import dataclass
from tree_sitter import Language, Parser
import tree_sitter_kotlin as tskotlin

AST_PARSER = Parser(Language(tskotlin.language()))

DECLARATION_NODE_TYPES = {
    "class_declaration",
    "object_declaration",
    "interface_declaration",
    "function_declaration",
    "property_declaration",
    "typealias_declaration",
}

TYPE_NODE_TYPES = {
    "type",
    "user_type",
    "nullable_type",
    "function_type",
    "generic_type",
}


@dataclass(frozen=True)
class SourceProfile:
    categories: frozenset[str]
    imports: tuple[str, ...]
    symbols: frozenset[str]
    query_terms: frozenset[str]
    required_dependencies: frozenset[str]


@dataclass(frozen=True)
class RepairProfile:
    source: SourceProfile
    categories: frozenset[str]
    query_terms: frozenset[str]

@dataclass(frozen=True)
class FragmentSourceProfile:
    is_fragment: bool
    is_dialog: bool
    is_hilt: bool
    has_navigation: bool
    has_delegated_viewmodel: bool
    has_carui_toolbar: bool
    has_carui_progress: bool
    is_complex: bool
    strategy_tags: frozenset[str]

def parse_kotlin_ast(source_text: str):
    return AST_PARSER.parse(bytes(source_text or "", "utf8")).root_node

def ast_text(source_bytes: bytes, node) -> str:
    return source_bytes[node.start_byte:node.end_byte].decode("utf8", errors="replace")

def walk_ast(node):
    """Recursively walk Kotlin AST nodes depth-first.
    
    Args:
        node: Tree-sitter node to start walking from
        
    Yields:
        Each node in the subtree (node and all descendants)
    """
    yield node
    for child in node.children:
        yield from walk_ast(child)

def direct_child(node, node_type: str):
    for child in node.children:
        if child.type == node_type:
            return child
    return None

def direct_children(node, node_type: str):
    return [child for child in node.children if child.type == node_type]

def node_has_direct_token(source_bytes: bytes, node, token_text: str) -> bool:
    return any(ast_text(source_bytes, child) == token_text for child in node.children)

def node_has_descendant_text(source_bytes: bytes, node, expected_text: str) -> bool:
    return any(ast_text(source_bytes, child) == expected_text for child in walk_ast(node))

def declaration_name(source_bytes: bytes, node) -> str:
    identifier = direct_child(node, "identifier")
    return ast_text(source_bytes, identifier) if identifier else ""

def annotation_names(source_bytes: bytes, node) -> set[str]:
    names = set()
    modifiers = direct_child(node, "modifiers")
    if not modifiers:
        return names

    for annotation in direct_children(modifiers, "annotation"):
        user_type = direct_child(annotation, "user_type")
        if user_type:
            names.add(ast_text(source_bytes, user_type).split(".")[-1])

    return names

def has_annotation(source_bytes: bytes, node, annotation_name: str) -> bool:
    return annotation_name in annotation_names(source_bytes, node)

def kotlin_package_name(source_code: str) -> str:
    source_bytes = source_code.encode("utf8")
    root = parse_kotlin_ast(source_code)
    package_header = direct_child(root, "package_header")
    if not package_header:
        return ""

    qualified_identifier = direct_child(package_header, "qualified_identifier")
    return ast_text(source_bytes, qualified_identifier) if qualified_identifier else ""

def kotlin_imports(source_code: str) -> list[str]:
    source_bytes = source_code.encode("utf8")
    root = parse_kotlin_ast(source_code)
    imports = []

    for child in root.children:
        if child.type != "import":
            continue

        qualified_identifier = direct_child(child, "qualified_identifier")
        if qualified_identifier:
            imports.append(ast_text(source_bytes, qualified_identifier))

    return imports

def kotlin_identifier_set(source_code: str) -> set[str]:
    source_bytes = source_code.encode("utf8")
    root = parse_kotlin_ast(source_code)
    return {
        ast_text(source_bytes, node)
        for node in walk_ast(root)
        if node.type == "identifier"
    }

def kotlin_top_level_class_name(source_code: str, file_stem: str) -> str:
    source_bytes = source_code.encode("utf8")
    root = parse_kotlin_ast(source_code)

    for child in root.children:
        if child.type == "function_declaration":
            return file_stem

    for child in root.children:
        if child.type in {"class_declaration", "object_declaration", "interface_declaration"}:
            name = declaration_name(source_bytes, child)
            if name:
                return name

    return file_stem

def kotlin_declared_class_names(source_code: str) -> set[str]:
    source_bytes = source_code.encode("utf8")
    root = parse_kotlin_ast(source_code)
    names = set()

    for node in walk_ast(root):
        if node.type not in {"class_declaration", "object_declaration", "interface_declaration"}:
            continue
        name = declaration_name(source_bytes, node)
        if name:
            names.add(name)

    return names

def has_apollo_response_extension_function(source_code: str) -> bool:
    source_bytes = source_code.encode("utf8")
    root = parse_kotlin_ast(source_code)

    for node in walk_ast(root):
        if node.type != "function_declaration":
            continue

        receiver_type = direct_child(node, "user_type")
        if not receiver_type:
            continue

        identifiers = [
            ast_text(source_bytes, child)
            for child in walk_ast(receiver_type)
            if child.type == "identifier"
        ]
        if identifiers and (identifiers[0].endswith("Mutation") or identifiers[0].endswith("Query")):
            return True

    return False

def node_line_text(source_code: str, node) -> str:
    lines = source_code.splitlines()
    row = node.start_point[0]
    if 0 <= row < len(lines):
        return lines[row].strip()
    return ""

def declaration_header_text(source_code: str, node) -> str:
    lines = source_code.splitlines()
    start = node.start_point[0]
    end = node.end_point[0]
    body = direct_child(node, "class_body")
    if body:
        end = max(start, body.start_point[0])

    return "\n".join(lines[start:end + 1]).rstrip()

def add_unique_risk(risks, title: str, evidence: str, test_strategy: str):
    key = (title, evidence)
    if any((risk["title"], risk["evidence"]) == key for risk in risks):
        return

    risks.append(
        {
            "title": title,
            "evidence": evidence.strip(),
            "test_strategy": test_strategy.strip(),
        }
    )

def function_names_from_ast(source_code: str) -> list[str]:
    source_bytes = source_code.encode("utf8")
    root = parse_kotlin_ast(source_code)
    names = []

    for node in walk_ast(root):
        if node.type != "function_declaration":
            continue
        name = declaration_name(source_bytes, node)
        if name:
            names.append(name)

    return names

def suspend_function_names_from_ast(source_code: str) -> set[str]:
    source_bytes = source_code.encode("utf8")
    root = parse_kotlin_ast(source_code)
    names = set()

    for node in walk_ast(root):
        if node.type != "function_declaration":
            continue
        modifiers = direct_child(node, "modifiers")
        if not modifiers or not node_has_descendant_text(source_bytes, modifiers, "suspend"):
            continue
        name = declaration_name(source_bytes, node)
        if name:
            names.add(name)

    return names

def is_override_function(source_bytes: bytes, node) -> bool:
    modifiers = direct_child(node, "modifiers")
    return bool(modifiers and node_has_descendant_text(source_bytes, modifiers, "override"))

def is_empty_catch_block(catch_node) -> bool:
    block = direct_child(catch_node, "block")
    if not block:
        return False

    meaningful_nodes = [
        child
        for child in block.children
        if child.type not in {"{", "}"}
    ]
    return len(meaningful_nodes) == 0

def call_expression_name(source_bytes: bytes, node) -> str:
    identifiers = [
        ast_text(source_bytes, child)
        for child in walk_ast(node)
        if child.type == "identifier"
    ]
    return identifiers[-1] if identifiers else ""

def analyze_source_bug_risks(source_code: str, max_risks: int = 12) -> str:
    """
    Generic static risk scan. This does not try to prove bugs; it gives the
    generator concrete edge cases that can expose source defects while staying
    independent of any one module or class.
    """
    risks = []
    source_bytes = source_code.encode("utf8")
    root = parse_kotlin_ast(source_code)
    function_names = []
    override_count = 0
    numeric_conversions = {"toInt", "toLong", "toDouble", "toFloat"}
    collection_access_calls = {"first", "last", "single"}

    for node in walk_ast(root):
        evidence = f"Line {node.start_point[0] + 1}: {node_line_text(source_code, node)}"

        if node.type == "function_declaration":
            name = declaration_name(source_bytes, node)
            if name:
                function_names.append(name)
            if is_override_function(source_bytes, node):
                override_count += 1

        if node.type == "unary_expression" and node_has_direct_token(source_bytes, node, "!!"):
            add_unique_risk(
                risks,
                "Forced null unwrap",
                evidence,
                "Consider the nearest public path only when it has deterministic execution and verified test-controlled dispatching. If the path uses hard-coded Dispatchers.IO, uncontrolled CoroutineScope, real delay, static construction, or unavailable framework behavior, mark this risk as deferred rather than generating an exception test solely for coverage.",
            )

        if node.type == "call_expression":
            call_name = call_expression_name(source_bytes, node)
            if call_name in numeric_conversions:
                add_unique_risk(
                    risks,
                    "Unchecked numeric conversion",
                    evidence,
                    "Test numeric boundary values and overflow/truncation cases through the nearest public API.",
                )
            if call_name in collection_access_calls:
                add_unique_risk(
                    risks,
                    "Collection access without empty guard",
                    evidence,
                    "Test empty collections and single-item collections through the public API.",
                )
            if call_name == "valueOf":
                add_unique_risk(
                    risks,
                    "Enum/string conversion without invalid-value guard",
                    evidence,
                    "Test valid values, lowercase/unknown values, and blank strings if accepted by the public API.",
                )
            if call_name in {"TODO", "NotImplementedError"}:
                add_unique_risk(
                    risks,
                    "Unimplemented code path",
                    evidence,
                    "Call the public API path that reaches this branch and assert that it currently throws.",
                )

        if node.type == "as_expression" and node_has_direct_token(source_bytes, node, "as"):
            add_unique_risk(
                risks,
                "Unsafe cast",
                evidence,
                "Exercise the public path with a non-matching runtime type when possible; assert the intended fallback or thrown exception.",
            )

        if node.type == "index_expression":
            add_unique_risk(
                risks,
                "Collection access without empty guard",
                evidence,
                "Test empty collections and single-item collections through the public API.",
            )

        if node.type == "catch_block":
            has_print_stack_trace = any(
                child.type == "call_expression"
                and call_expression_name(source_bytes, child) == "printStackTrace"
                for child in walk_ast(node)
            )
            if is_empty_catch_block(node) or has_print_stack_trace:
                add_unique_risk(
                    risks,
                    "Exception path may be swallowed",
                    evidence,
                    "Force the dependency to throw and assert the observable fallback, propagated exception, or logged side effect.",
                )

        if node.type == "return_expression" and node_has_descendant_text(source_bytes, node, "null"):
            add_unique_risk(
                risks,
                "Explicit null return",
                evidence,
                "Test the null branch through public inputs and assert callers receive null only when the API contract permits it.",
            )

    getters = {
        name[3:]
        for name in function_names
        if name.startswith("get") and len(name) > 3
    }
    getters.update(
        name[2:]
        for name in function_names
        if name.startswith("is") and len(name) > 2
    )
    setters = {
        name[3:]
        for name in function_names
        if name.startswith("set") and len(name) > 3
    }
    paired_properties = sorted(getters & setters)
    if paired_properties:
        add_unique_risk(
            risks,
            "Getter/setter symmetry",
            "Paired accessors: " + ", ".join(paired_properties[:10]),
            "For each accessible pair, set a representative value and verify the getter delegates to the same stored value or dependency call.",
        )

    if override_count >= 4:
        add_unique_risk(
            risks,
            "Interface implementation delegation",
            f"{override_count} override functions found",
            "Use a mock or fake dependency to verify each override delegates to the expected dependency method with the exact argument and return value.",
        )

    if not risks:
        return (
            "### STATIC SOURCE BUG-HUNTING TARGETS\n"
            "- No obvious generic static-risk patterns were detected. Still test boundary values, "
            "state transitions, dependency failures, and public API contracts."
        )

    output = ["### STATIC SOURCE BUG-HUNTING TARGETS"]
    for index, risk in enumerate(risks[:max_risks], 1):
        output.append(f"{index}. Risk: {risk['title']}")
        output.append(f"   Evidence: {risk['evidence']}")
        output.append(f"   Test strategy: {risk['test_strategy']}")

    return "\n".join(output)

def extract_package_name(source_code: str) -> str:
    return kotlin_package_name(source_code)

def _function_has_executable_body(source_bytes: bytes, function_node) -> bool:
    block = direct_child(function_node, "function_body")
    if not block:
        return False
    meaningful_nodes = [
        child
        for child in block.children
        if child.type not in {"{", "}"}
    ]
    return bool(meaningful_nodes)


def is_declarative_dagger_binding(source_code: str, analysis_report=None) -> bool:
    combined = source_code or ""
    if "@Module" not in combined:
        return False

    source_bytes = combined.encode("utf8")
    root = parse_kotlin_ast(combined)
    has_binds_like = False
    for node in walk_ast(root):
        if node.type != "function_declaration":
            continue
        annotations = annotation_names(source_bytes, node)
        if "Provides" in annotations and _function_has_executable_body(source_bytes, node):
            return False
        if annotations & {"Binds", "BindsOptionalOf"}:
            has_binds_like = True
    return has_binds_like


def classify_source(source_code: str, analysis_report=None) -> SourceProfile:
    combined = source_code or ""
    if analysis_report is None:
        from UnitTest_gen.kotlin.static_analysis import analyze_kotlin_code

        analysis_report = analyze_kotlin_code(combined)
    imports = tuple(analysis_report.imports)
    import_text = "\n".join(imports)
    symbols = frozenset(kotlin_identifier_set(combined))
    categories: set[str] = set()

    if analysis_report.coroutine_usages:
        categories.add("coroutines_flow")
    if analysis_report.confirmed_findings("hardcoded_dispatcher"):
        categories.update({"hardcoded_dispatcher_entry", "coverage_strategy"})
    if analysis_report.confirmed_findings("detached_coroutine_scope"):
        categories.update({"detached_coroutine_scope", "coverage_strategy"})
    if analysis_report.frameworks.mockito:
        categories.add("mockito")
    if analysis_report.frameworks.mockk:
        categories.add("mockk")

    fragment_profile = classify_fragment_source(source_code or "", analysis_report)
    if fragment_profile.is_fragment:
        categories.update({"android_fragment", "android_ui"})
        categories.update(fragment_profile.strategy_tags)
    if "carui_toolbar_fragment" in categories:
        categories.add("carui_toolbar")
    if "carui_progress_fragment" in categories:
        categories.add("carui_progress")
    if "registerBackListener" in combined:
        categories.add("carui_back_listener")
    if "@Inject" in combined and "constructor" in combined:
        categories.add("constructor_injection")
    if "@AndroidEntryPoint" in combined:
        categories.add("hilt_entrypoint")
    if "@HiltViewModel" in combined:
        categories.add("hilt_viewmodel")
    if "@HiltViewModel" in combined or _declares_super_type(analysis_report, {"ViewModel", "AndroidViewModel"}):
        categories.add("viewmodel")
    if _declares_super_type(
        analysis_report,
        {"Activity", "ComponentActivity", "FragmentActivity", "AppCompatActivity"},
    ):
        categories.add("android_activity")
        if "@AndroidEntryPoint" in combined:
            categories.add("hilt_android_activity")
    if _declares_super_type(analysis_report, {"Application", "MultiDexApplication"}):
        categories.add("android_application")
        if "@HiltAndroidApp" in combined:
            categories.add("hilt_android_application")
    if (
        analysis_report.coroutine_usages
        or any(function.is_suspend for function in analysis_report.functions)
        or any(path.startswith("kotlinx.coroutines") for path in imports)
    ):
        categories.add("coroutines_flow")
    if any(marker in combined for marker in ["@Module", "@Binds", "@Provides"]):
        categories.add("dagger_module")
    if is_declarative_dagger_binding(combined, analysis_report):
        categories.add("declarative_dagger_binding")
    elif "@Provides" in combined:
        categories.add("dagger_provider_logic")
    if any(marker in combined for marker in ["Room", "RoomDatabase", "@Dao", "@Entity"]):
        categories.add("room")
    if any(marker in combined for marker in ["retrofit2", "okhttp3", "Authenticator", "Interceptor"]):
        categories.add("network")
    if any(marker in combined for marker in ["ApolloClient", "com.apollographql", "Mutation.", "Query."]):
        categories.add("apollo")
    if any(marker in combined for marker in ["NavController", "findNavController", "NavDeepLinkRequest", "NavigationView", "DrawerLayout"]):
        categories.add("android_navigation")
    if "NavDeepLinkRequest" in combined:
        categories.add("nav_deeplink")
    singleton_factories = (
        "FirebaseAnalytics.getInstance",
        "FirebaseInstallations.getInstance",
        "FirebaseCrashlytics.getInstance",
        "AlertDialogHelper.getInstance",
        "DigitalKeyCommonStorage.getInstance",
    )
    if any(marker in combined for marker in singleton_factories) or any(
        function.name == "getInstance" for function in analysis_report.functions
    ):
        categories.add("static_singleton_get_instance")
        categories.add("companion_singleton_static_mock")
    if "AlertDialogHelper.getInstance" in combined:
        categories.add("alert_dialog_helper")
    if any(marker in combined for marker in ["com.android.car.ui", "CarUi.", "android.car", "CarPropertyManager"]):
        categories.add("car")
    if _declares_super_type(
        analysis_report,
        {"Worker", "CoroutineWorker", "ListenableWorker", "BroadcastReceiver", "Service"},
    ):
        categories.add("worker_service")
    if (
        any(marker in combined for marker in ["SharedPreferences", "PreferenceManager", "JSONObject", "JSONArray"])
        or "net.openid.appauth" in import_text
        or any(call.name == "AuthState" for call in analysis_report.calls)
    ):
        categories.add("storage_json_auth")
    if "Firebase" in combined:
        categories.add("firebase")
    if "Timber" in combined:
        categories.add("logging")
    if "Log." in combined:
        categories.add("android_log")
    if any(marker in combined for marker in ["Context", "Activity", "Application", "Resources", "R."]):
        categories.add("android_context")
    if any(marker in combined for marker in ["DialogFragment", "AlertDialog", "MaterialAlertDialog"]):
        categories.add("android_dialog")
    if any(marker in combined for marker in ["Task<", ".await()", "kotlinx.coroutines.tasks"]):
        categories.add("play_services")
    if any(marker in combined for marker in ["CarUi.", "FirebaseAnalytics.getInstance", "FirebaseInstallations.getInstance", "FirebaseCrashlytics.getInstance", "AlertDialogHelper.getInstance", "System."]):
        categories.add("static_api")
    if any(marker in combined for marker in ["CarManager.", "PassiveEntry.", "getVinNumber", "setVOCDeviceId"]):
        categories.update({"static_api", "sdk_static_seam"})
    if "LiveData" in combined or "MutableLiveData" in combined:
        categories.add("livedata")
    if fragment_profile.is_fragment and source_uses_delegated_viewmodels(source_code or "", analysis_report) and "setOnClickListener" in combined:
        if any(
            marker in combined
            for marker in (
                "continueProfileConnection",
                "disconnectProfileConnection",
                "deleteDevice",
                "deleteOneSharedKey",
                "deleteAllSharedKey",
                "Dispatchers.IO",
                "CoroutineScope(Dispatchers.IO",
            )
        ):
            categories.add("fragment_triggers_uncontrolled_viewmodel_io")

    query_terms = set(categories)
    query_terms.update(path.split(".")[-1].lower() for path in imports)

    required = {"libs.junit", "org.mockito.kotlin:mockito-kotlin", "kotlin:test"}
    if categories & {"android_fragment", "android_ui", "android_navigation", "android_dialog", "car", "android_log"}:
        required.update({"org.robolectric:robolectric", "androidx.test:core"})
    if "android_fragment" in categories:
        required.add("androidx.fragment:fragment-testing")
    if "android_navigation" in categories:
        required.add("androidx.navigation:navigation-testing")
    if "hilt_entrypoint" in categories and "android_fragment" in categories:
        required.add("com.google.dagger:hilt-android-testing")
    if categories & {"coroutines_flow", "viewmodel"}:
        required.add("org.jetbrains.kotlinx:kotlinx-coroutines-test")
    if "livedata" in categories:
        required.add("androidx.arch.core:core-testing")
    if "storage_json_auth" in categories:
        required.add("org.json:json")
    if "play_services" in categories:
        required.add("org.jetbrains.kotlinx:kotlinx-coroutines-play-services")
    if "static_api" in categories:
        required.add("org.mockito:mockito-inline")

    return SourceProfile(
        frozenset(categories),
        imports,
        symbols,
        frozenset(query_terms),
        frozenset(required),
    )


def classify_repair(source_profile: SourceProfile, error_context: str) -> RepairProfile:
    text = (error_context or "").lower()
    categories = set(source_profile.categories)
    terms = set(source_profile.query_terms)
    mappings = {
        "dependency_error": ("unresolved reference", "cannot access", "missingbinding", "classpath"),
        "hilt_graph": ("missingbinding", "hilt graph unclosed", "cannot be provided without an @provides", "generated hilt component", "qualifier_mismatch", "@named"),
        "mocking_error": ("mockito", "mockk", "notamock", "wantedbutnotinvoked", "mockedstatic", "matcher", "invalid_mocking_framework"),
        "lifecycle_error": ("lifecycle", "not attached", "requirecontext", "requireview", "fragment"),
        "fixture_strategy": ("invalid_activity_controller_order", "fragmentmanager has not been attached", "invalid_host_strategy", "invalid_hilt_lifecycle_setup"),
        "assertion_error": ("assertion", "expected", "but was", "comparisonfailure"),
        "coroutine_error": ("suspend function", "dispatcher", "coroutine", "flow"),
        "api_error": ("type mismatch", "unknown parameter", "no value passed", "overload"),
        "speculative_sdk_exception_constructor": ("invalid_speculative_sdk_exception_constructor", "unverified sdk exception", "exception constructor"),
        "syntax_error": ("syntax error", "expecting", "imports are only allowed"),
    }
    for category, markers in mappings.items():
        if any(marker in text for marker in markers):
            categories.add(category)
            terms.add(category)
    terms.update(re.findall(r"[a-z][a-z0-9_]{3,}", text))
    return RepairProfile(source_profile, frozenset(categories), frozenset(terms))


def source_rule_categories(source_code: str, dependency_context: str = "") -> set[str]:
    return set(classify_source(source_code).categories)

def _analysis_report_for(source_code: str):
    from UnitTest_gen.kotlin.static_analysis import analyze_kotlin_code

    return analyze_kotlin_code(source_code or "")


def _simple_type_name(type_text: str) -> str:
    return (type_text or "").split(".")[-1].split("<", 1)[0].strip()


def _declares_super_type(analysis_report, type_names: set[str]) -> bool:
    wanted = {_simple_type_name(type_name) for type_name in type_names}
    return any(
        _simple_type_name(super_type) in wanted
        for declaration in analysis_report.declarations
        if declaration.kind in {"class", "object", "interface"}
        for super_type in declaration.super_types
    )


def source_uses_delegated_viewmodels(source_code: str, analysis_report=None) -> bool:
    analysis_report = analysis_report or _analysis_report_for(source_code)
    return bool(
        property_info.is_delegated
        and property_info.delegate_call.split("<", 1)[0].split("(", 1)[0].strip() in {"viewModels", "activityViewModels"}
        for property_info in analysis_report.properties
    )


def source_has_observer_and_click_chain(source_code: str, analysis_report=None) -> bool:
    text = (source_code or "").lower()
    return bool(re.search(r"\b(?:observe|observeforever)\b", text) and "setonclicklistener" in text)


def source_uses_carui_toolbar_progress(source_code: str, analysis_report=None) -> bool:
    analysis_report = analysis_report or _analysis_report_for(source_code)
    return bool(
        "CarUi.requireToolbar" in (source_code or "")
        and (
            "progressBar" in kotlin_identifier_set(source_code or "")
            or any(call.name == "getProgressBar" for call in analysis_report.calls)
        )
    )

def source_complex_fragment_markers(source_code: str, analysis_report=None) -> list[str]:
    analysis_report = analysis_report or _analysis_report_for(source_code)
    markers = []
    checks = [
        ("Hilt injection", "@AndroidEntryPoint" in source_code or "@Inject" in source_code),
        ("navigation", "findNavController" in source_code or "NavDeepLinkRequest" in source_code),
        ("delegated ViewModel", source_uses_delegated_viewmodels(source_code, analysis_report)),
        ("CarUi toolbar", "CarUi.requireToolbar" in source_code),
        ("CarUi back listener", "registerBackListener" in source_code),
        ("toolbar progress", source_uses_carui_toolbar_progress(source_code, analysis_report)),
        ("dialog/callback UI", any(token in source_code for token in ["AlertDialog", "DialogFragment", "FullScreenDialogFragment"])),
        ("BuildConfig branch", "BuildConfig." in source_code),
        ("async/lifecycle timing", any(token in source_code for token in ["Handler(", "postDelayed", "lifecycleScope", "repeatOnLifecycle", "observe(viewLifecycleOwner"])),
        ("multiple navigation paths", source_code.count("findNavController()") + source_code.count(".navigate(") >= 2),
    ]
    for name, present in checks:
        if present:
            markers.append(name)
    return markers


def classify_fragment_source(source_code: str, analysis_report=None) -> FragmentSourceProfile:
    source_code = source_code or ""
    analysis_report = analysis_report or _analysis_report_for(source_code)
    is_fragment = source_declares_android_fragment(source_code, analysis_report)
    is_dialog = is_fragment and _declares_super_type(analysis_report, {"DialogFragment"})
    is_hilt = is_fragment and "@AndroidEntryPoint" in source_code
    has_navigation = bool("findNavController" in source_code or "NavController" in source_code or "NavDeepLinkRequest" in source_code)
    has_delegated_viewmodel = source_uses_delegated_viewmodels(source_code, analysis_report)
    has_carui_toolbar = "CarUi.requireToolbar" in source_code
    has_carui_progress = source_uses_carui_toolbar_progress(source_code, analysis_report)
    has_carui_back_listener = "registerBackListener" in source_code
    markers = source_complex_fragment_markers(source_code, analysis_report) if is_fragment else []
    method_count = len(analysis_report.functions)
    line_count = len(source_code.splitlines())
    is_complex = is_fragment and (len(markers) >= 4 or line_count >= 220 or method_count >= 10)

    tags: set[str] = set()
    if is_fragment:
        tags.update({"android_fragment", "android_ui"})
        tags.add("dialog_fragment" if is_dialog else "plain_fragment")
    if is_hilt:
        tags.update({"hilt_entrypoint", "hilt_fragment", "hilt_graph"})
        tags.discard("plain_fragment")
    if has_navigation:
        tags.update({"android_navigation", "navigation_fragment"})
    if "NavDeepLinkRequest" in source_code:
        tags.add("nav_deeplink")
    if "AlertDialogHelper.getInstance" in source_code:
        tags.update({"alert_dialog_helper", "static_singleton_get_instance", "companion_singleton_static_mock"})
    if has_delegated_viewmodel:
        tags.add("delegated_viewmodel_fragment")
    if has_carui_toolbar:
        tags.update({"car", "carui_toolbar_fragment"})
    if has_carui_back_listener:
        tags.add("carui_back_listener")
    if has_carui_progress:
        tags.add("carui_progress_fragment")
    if is_fragment and has_delegated_viewmodel and "setOnClickListener" in source_code and any(
        marker in source_code
        for marker in (
            "continueProfileConnection",
            "disconnectProfileConnection",
            "deleteDevice",
            "deleteOneSharedKey",
            "deleteAllSharedKey",
            "Dispatchers.IO",
            "CoroutineScope(Dispatchers.IO",
        )
    ):
        tags.add("fragment_triggers_uncontrolled_viewmodel_io")
    if is_complex:
        tags.add("complex_fragment")

    return FragmentSourceProfile(
        is_fragment,
        is_dialog,
        is_hilt,
        has_navigation,
        has_delegated_viewmodel,
        has_carui_toolbar,
        has_carui_progress,
        is_complex,
        frozenset(tags),
    )


def source_declares_android_fragment(source_code: str, analysis_report=None) -> bool:
    analysis_report = analysis_report or _analysis_report_for(source_code)
    return _declares_super_type(analysis_report, {"Fragment", "DialogFragment"})

def source_declares_viewmodel_class(source_code: str, analysis_report=None) -> bool:
    analysis_report = analysis_report or _analysis_report_for(source_code)
    return _declares_super_type(analysis_report, {"ViewModel", "AndroidViewModel"})

def summarize_kotlin_source_signatures(source_code: str, max_lines: int = 140) -> str:
    selected = []
    source_bytes = source_code.encode("utf8")
    root = parse_kotlin_ast(source_code)

    package_header = direct_child(root, "package_header")
    if package_header:
        selected.extend(ast_text(source_bytes, package_header).splitlines())

    for node in walk_ast(root):
        if node.type not in DECLARATION_NODE_TYPES:
            continue

        header = declaration_header_text(source_code, node)
        if not header:
            continue

        selected.extend(header.splitlines())
        if len(selected) >= max_lines:
            break

    if len(selected) > max_lines:
        selected = selected[:max_lines]
        selected.append("// ... truncated ...")

    return "\n".join(selected)

def summarize_kotlin_signature_file(path: str, max_lines: int = 140) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return summarize_kotlin_source_signatures(f.read(), max_lines=max_lines)
    except Exception:
        return ""
