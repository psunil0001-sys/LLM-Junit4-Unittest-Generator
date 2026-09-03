"""Tree-sitter/Semgrep Kotlin analysis and source classification."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

class FindingDisposition(str, Enum):
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    DISMISSED = "dismissed"

class SourceSpan(StrictModel):
    start_line: int = Field(ge=1)
    start_column: int = Field(ge=1)
    end_line: int = Field(ge=1)
    end_column: int = Field(ge=1)

class KotlinFunctionInfo(StrictModel):
    name: str
    visibility: Literal["public", "internal", "protected", "private"]
    annotations: tuple[str, ...]
    modifiers: tuple[str, ...]
    is_suspend: bool
    span: SourceSpan

class KotlinDeclarationInfo(StrictModel):
    kind: Literal["class", "object", "interface", "function", "property", "typealias"]
    name: str
    visibility: Literal["public", "internal", "protected", "private"]
    annotations: tuple[str, ...]
    modifiers: tuple[str, ...]
    super_types: tuple[str, ...] = ()
    span: SourceSpan

class KotlinPropertyInfo(StrictModel):
    name: str
    visibility: Literal["public", "internal", "protected", "private"]
    annotations: tuple[str, ...]
    modifiers: tuple[str, ...]
    type_text: str = ""
    is_lateinit: bool = False
    is_delegated: bool = False
    delegate_call: str = ""
    span: SourceSpan

class KotlinConstructorParam(StrictModel):
    name: str
    type_text: str
    annotations: tuple[str, ...]

class KotlinClassShape(StrictModel):
    name: str
    annotations: tuple[str, ...]
    super_types: tuple[str, ...]
    constructor_params: tuple[KotlinConstructorParam, ...]

class CoroutineUsage(StrictModel):
    kind: Literal["viewmodel_scope_launch", "global_scope", "standalone_scope", "with_context", "delay"]
    dispatcher: Literal["IO", "Default", "Main", "Unspecified"]
    function_name: str
    span: SourceSpan
    evidence: str

class CallbackUsage(StrictModel):
    kind: str
    function_name: str
    called_identifiers: tuple[str, ...]
    span: SourceSpan

class KotlinCallUsage(StrictModel):
    name: str
    function_name: str
    span: SourceSpan
    evidence: str

class KotlinTestFunctionInfo(StrictModel):
    name: str
    annotations: tuple[str, ...]
    visibility: Literal["public", "internal", "protected", "private"]
    span: SourceSpan
    parameter_list_count: int = 0
    has_extra_parameter_list: bool = False

class KotlinTestMetadata(StrictModel):
    annotations: tuple[str, ...] = ()
    fields: tuple[KotlinPropertyInfo, ...] = ()
    setup_teardown_functions: tuple[KotlinFunctionInfo, ...] = ()
    helper_functions: tuple[KotlinFunctionInfo, ...] = ()
    test_functions: tuple[KotlinTestFunctionInfo, ...] = ()
    config_sdks: tuple[int, ...] = ()
    mock_variables: tuple[str, ...] = ()
    mocked_static_targets: tuple[str, ...] = ()
    direct_lifecycle_call_names: tuple[str, ...] = ()
    property_assignment_verification_lines: tuple[int, ...] = ()
    malformed_test_declaration_lines: tuple[int, ...] = ()
    has_hilt_module_install_in: bool = False
    has_local_hilt_test_activity: bool = False

class FrameworkUsage(StrictModel):
    junit4: bool = False
    junit5: bool = False
    mockito: bool = False
    mockk: bool = False
    robolectric: bool = False
    hilt: bool = False
    coroutine_test: bool = False

class StaticFinding(StrictModel):
    rule_id: str
    category: str
    severity: str
    disposition: FindingDisposition
    span: SourceSpan
    evidence: str
    message: str
    verification: str = ""

class KotlinStaticAnalysisReport(StrictModel):
    schema_version: str
    source_path: str
    source_hash: str
    tree_sitter_language: str
    semgrep_version: str
    status: Literal["complete"]
    imports: tuple[str, ...]
    declarations: tuple[KotlinDeclarationInfo, ...]
    properties: tuple[KotlinPropertyInfo, ...]
    functions: tuple[KotlinFunctionInfo, ...]
    coroutine_usages: tuple[CoroutineUsage, ...]
    callbacks: tuple[CallbackUsage, ...]
    calls: tuple[KotlinCallUsage, ...]
    frameworks: FrameworkUsage
    tests: KotlinTestMetadata
    class_shapes: tuple[KotlinClassShape, ...] = ()
    identifiers: frozenset[str] = frozenset()
    findings: tuple[StaticFinding, ...]
    validation_errors: tuple[str, ...] = ()

    def function_for_line(self, line_no: int, public_only: bool = False) -> KotlinFunctionInfo | None:
        candidates = [
            item for item in self.functions
            if item.span.start_line <= line_no <= item.span.end_line
            and (not public_only or item.visibility != "private")
        ]
        return min(candidates, key=lambda item: item.span.end_line - item.span.start_line) if candidates else None

    def confirmed_findings(self, *categories: str) -> tuple[StaticFinding, ...]:
        allowed = set(categories)
        return tuple(
            finding for finding in self.findings
            if finding.disposition == FindingDisposition.CONFIRMED and (not allowed or finding.category in allowed)
        )

import re
from functools import lru_cache
from tree_sitter import Language, Parser
import tree_sitter_kotlin as tskotlin

from UnitTest_gen.core import io as file_cache
from UnitTest_gen.core.io import walk_source_roots

AST_PARSER = Parser(Language(tskotlin.language()))
PLAIN_UNIT_TAG = 'plain_unit'

def parse_kotlin_ast(source_text: str):
    return AST_PARSER.parse(bytes(source_text or '', 'utf8')).root_node

def ast_text(source_bytes: bytes, node) -> str:
    return source_bytes[node.start_byte:node.end_byte].decode('utf8', errors='replace')

def walk_ast(node):
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

def node_has_descendant_text(source_bytes: bytes, node, expected_text: str) -> bool:
    return any((ast_text(source_bytes, child) == expected_text for child in walk_ast(node)))

def declaration_name(source_bytes: bytes, node) -> str:
    identifier = direct_child(node, 'identifier')
    return ast_text(source_bytes, identifier) if identifier else ''

def annotation_names(source_bytes: bytes, node) -> set[str]:
    names = set()
    modifiers = direct_child(node, 'modifiers')
    if not modifiers:
        return names
    for annotation in direct_children(modifiers, 'annotation'):
        user_type = direct_child(annotation, 'user_type')
        if user_type:
            names.add(ast_text(source_bytes, user_type).split('.')[-1])
    return names

def kotlin_package_name(source_code: str) -> str:
    source_bytes = source_code.encode('utf8')
    root = parse_kotlin_ast(source_code)
    package_header = direct_child(root, 'package_header')
    if not package_header:
        return ''
    qualified_identifier = direct_child(package_header, 'qualified_identifier')
    return ast_text(source_bytes, qualified_identifier) if qualified_identifier else ''

def kotlin_identifier_set(source_code: str, root=None) -> set[str]:
    source_bytes = source_code.encode('utf8')
    tree_root = root if root is not None else parse_kotlin_ast(source_code)
    return {ast_text(source_bytes, node) for node in walk_ast(tree_root) if node.type == 'identifier'}

def kotlin_top_level_class_name(source_code: str, file_stem: str) -> str:
    source_bytes = source_code.encode('utf8')
    root = parse_kotlin_ast(source_code)
    for child in root.children:
        if child.type == 'function_declaration':
            return file_stem
    for child in root.children:
        if child.type in {'class_declaration', 'object_declaration', 'interface_declaration'}:
            name = declaration_name(source_bytes, child)
            if name:
                return name
    return file_stem

def kotlin_declared_class_names(source_code: str) -> set[str]:
    source_bytes = source_code.encode('utf8')
    root = parse_kotlin_ast(source_code)
    names = set()
    for node in walk_ast(root):
        if node.type not in {'class_declaration', 'object_declaration', 'interface_declaration'}:
            continue
        name = declaration_name(source_bytes, node)
        if name:
            names.add(name)
    return names

def kotlin_member_extension_functions(source_code: str) -> dict[str, tuple[str, str]]:
    """Return member extension name -> (owner, receiver type) from Kotlin AST."""
    source_bytes = source_code.encode('utf8')
    root = parse_kotlin_ast(source_code)
    extensions = {}
    for node in walk_ast(root):
        if node.type != 'function_declaration':
            continue
        modifier_text = ' '.join((ast_text(source_bytes, child) for child in node.children if child.type == 'modifiers'))
        if re.search('\\bprivate\\b', modifier_text):
            continue
        receiver = next((child for (index, child) in enumerate(node.children[:-1]) if child.type == 'user_type' and node.children[index + 1].type == '.'), None)
        if receiver is None:
            continue
        owner = node.parent
        while owner is not None and owner.type not in {'class_declaration', 'object_declaration'}:
            owner = owner.parent
        if owner is None:
            continue
        function_name = declaration_name(source_bytes, node)
        owner_name = declaration_name(source_bytes, owner)
        receiver_type = ast_text(source_bytes, receiver).strip()
        if function_name and owner_name and receiver_type:
            extensions[function_name] = (owner_name, receiver_type)
    return extensions

def has_apollo_response_extension_function(source_code: str) -> bool:
    source_bytes = source_code.encode('utf8')
    root = parse_kotlin_ast(source_code)
    for node in walk_ast(root):
        if node.type != 'function_declaration':
            continue
        receiver_type = direct_child(node, 'user_type')
        if not receiver_type:
            continue
        identifiers = [ast_text(source_bytes, child) for child in walk_ast(receiver_type) if child.type == 'identifier']
        if identifiers and (identifiers[0].endswith('Mutation') or identifiers[0].endswith('Query')):
            return True
    return False

def suspend_function_names_from_ast(source_code: str) -> set[str]:
    source_bytes = source_code.encode('utf8')
    root = parse_kotlin_ast(source_code)
    names = set()
    for node in walk_ast(root):
        if node.type != 'function_declaration':
            continue
        modifiers = direct_child(node, 'modifiers')
        if not modifiers or not node_has_descendant_text(source_bytes, modifiers, 'suspend'):
            continue
        name = declaration_name(source_bytes, node)
        if name:
            names.add(name)
    return names

def _function_has_executable_body(source_bytes: bytes, function_node) -> bool:
    block = direct_child(function_node, 'function_body')
    if not block:
        return False
    meaningful_nodes = [child for child in block.children if child.type not in {'{', '}'}]
    return bool(meaningful_nodes)

def is_declarative_dagger_binding(source_code: str, analysis_report=None) -> bool:
    combined = source_code or ''
    if '@Module' not in combined:
        return False
    source_bytes = combined.encode('utf8')
    root = parse_kotlin_ast(combined)
    has_binds_like = False
    for node in walk_ast(root):
        if node.type != 'function_declaration':
            continue
        annotations = annotation_names(source_bytes, node)
        if 'Provides' in annotations and _function_has_executable_body(source_bytes, node):
            return False
        if annotations & {'Binds', 'BindsOptionalOf'}:
            has_binds_like = True
    return has_binds_like

def source_get_instance_types(source_code: str) -> set[str]:
    """Simple type names that appear as Type.getInstance(...) in source."""
    jdk_noise = {'MessageDigest', 'KeyGenerator', 'KeyPairGenerator', 'Cipher', 'Mac', 'SecureRandom', 'Calendar', 'Currency', 'Locale', 'Logger', 'Executors'}
    return {match.group(1) for match in re.finditer('\\b([A-Za-z_][A-Za-z0-9_]*)\\.getInstance\\s*\\(', source_code or '') if match.group(1) not in jdk_noise}
_KOTLIN_OBJECT_RECEIVER_EXCLUDE = frozenset({'BuildConfig', 'Bundle', 'Dispatchers', 'Intent', 'Log', 'R', 'Result', 'System', 'Unit', 'Uri', 'UUID', 'TextUtils', 'View', 'Collections', 'Arrays', 'Objects', 'String', 'Integer', 'Boolean', 'Long', 'Math', 'File', 'Paths', 'Files', 'Pattern', 'Locale', 'TimeUnit', 'Duration', 'Instant', 'JSONObject', 'JSONArray', 'CarUi', 'Navigation', 'NavDeepLinkRequest', 'FirebaseAnalytics', 'FirebaseInstallations', 'FirebaseCrashlytics', 'FirebaseAuth', 'FirebaseMessaging', 'FirebaseApp', 'Robolectric', 'Shadows', 'ShadowLooper', 'ArgumentMatchers', 'Mockito', 'Matchers'})

def _receiver_type_names(source_code: str) -> set[str]:
    return {name for name in re.findall('(?<![.\\w])([A-Z][A-Za-z0-9_]*)\\s*\\.\\s*[A-Za-z_]', source_code or '') if name not in _KOTLIN_OBJECT_RECEIVER_EXCLUDE}

def _project_kotlin_object_names(source_file_path: str) -> set[str]:
    """Scan owning project src/main trees for top-level Kotlin object names."""
    from pathlib import Path
    if not source_file_path:
        return set()
    current = Path(source_file_path).resolve()
    project_root = ''
    for parent in [current.parent, *current.parents]:
        if (parent / 'settings.gradle.kts').exists() or (parent / 'settings.gradle').exists():
            project_root = str(parent)
            break
    if not project_root:
        return set()
    return set(_cached_project_kotlin_objects(project_root))

_TOP_LEVEL_OBJECT_RE = re.compile('(?m)^\\s*object\\s+([A-Za-z_][A-Za-z0-9_]*)\\b')

@lru_cache(maxsize=8)
def _cached_project_kotlin_objects(project_root: str) -> frozenset[str]:
    """Index top-level Kotlin object names under every module's src/main tree."""
    found: set[str] = set()
    for path in walk_source_roots(project_root, roots=("src/main",), extensions=(".kt",)):
        text = file_cache.read_text(path, default="")
        if not text:
            continue
        found.update(_TOP_LEVEL_OBJECT_RE.findall(text))
    return frozenset(found)

def _call_evidence_receiver_names(analysis_report) -> set[str]:
    """Receivers seen in call evidence, so snippet-scoped callers keep whole-file signal."""
    if analysis_report is None:
        return set()
    evidence = '\n'.join((call.evidence or '' for call in getattr(analysis_report, 'calls', ())))
    return _receiver_type_names(evidence)

def source_referenced_kotlin_object_names(source_code: str, source_file_path: str='', analysis_report=None) -> set[str]:
    """Return Kotlin object types referenced as ObjectName.member in the CUT."""
    declared_local = set(_TOP_LEVEL_OBJECT_RE.findall(source_code or ''))
    receivers = _receiver_type_names(source_code) | _call_evidence_receiver_names(analysis_report)
    project_objects = _project_kotlin_object_names(source_file_path) if source_file_path else set()
    if project_objects:
        return receivers & project_objects | declared_local & receivers
    return declared_local & receivers if declared_local else set()

def source_uses_kotlin_object_seam(source_code: str, source_file_path: str='', analysis_report=None) -> bool:
    return bool(source_referenced_kotlin_object_names(source_code, source_file_path, analysis_report))

def classify_source(source_code: str, analysis_report=None, *, source_file_path: str='') -> 'SourceProfile':
    from UnitTest_gen.kotlin.analysis import SourceProfile, classify_from_report
    from UnitTest_gen.kotlin.analysis import analyze_kotlin_code

    combined = source_code or ''
    report = analysis_report or analyze_kotlin_code(combined, source_file_path)
    return classify_from_report(report, source_file_path=source_file_path, source_code=combined)

# Re-export for callers that import SourceProfile from kotlin_analysis.
def __getattr__(name: str):
    if name == 'SourceProfile':
        from UnitTest_gen.kotlin.analysis import SourceProfile
        return SourceProfile
    if name == 'FragmentSourceProfile':
        from UnitTest_gen.kotlin.analysis import FragmentSourceProfile
        return FragmentSourceProfile
    raise AttributeError(name)

COVERAGE_SLICE_PRIORITY: tuple[str, ...] = (
    "android_fragment",
    "android_activity",
    "android_application",
    "android_ui",
    "carui_toolbar",
    "carui_progress",
    "carui_back_listener",
    "android_navigation",
    "delegated_viewmodel_fragment",
    "hilt_worker",
    "hilt_fragment",
    "coroutines_flow",
    "viewmodel",
    "kotlin_object_seam",
    PLAIN_UNIT_TAG,
)

_SLICE_ALIASES = {
    "plain_fragment": "android_fragment",
    "carui_toolbar_fragment": "carui_toolbar",
    "carui_progress_fragment": "carui_progress",
    "hilt_entrypoint": "hilt_fragment",
    "closed_hilt_graph": "hilt_fragment",
    "hilt_assisted_inject": "hilt_worker",
    "delegated_viewmodel_drive": "delegated_viewmodel_fragment",
}

def normalize_slice_tag(tag: str) -> str:
    """Map a classify_source / planner tag onto COVERAGE_SLICE_PRIORITY, or ''."""
    name = str(tag or "").strip()
    name = _SLICE_ALIASES.get(name, name)
    return name if name in COVERAGE_SLICE_PRIORITY else ""

def coverage_slice_tags(file_categories) -> list[str]:
    """Priority-ordered slice tags present on this source (plain_unit if none)."""
    normalized: set[str] = set()
    for raw in file_categories or ():
        mapped = normalize_slice_tag(str(raw))
        if mapped:
            normalized.add(mapped)
    ordered = [tag for tag in COVERAGE_SLICE_PRIORITY if tag in normalized and tag != PLAIN_UNIT_TAG]
    if not ordered:
        return [PLAIN_UNIT_TAG]
    return ordered

def source_rule_categories(source_code: str, dependency_context: str='', source_file_path: str='') -> set[str]:
    del dependency_context
    return set(classify_source(source_code, source_file_path=source_file_path).categories)

def _analysis_report_for(source_code: str):
    from UnitTest_gen.kotlin.analysis import analyze_kotlin_code
    return analyze_kotlin_code(source_code or '')

def simple_type_name(type_text: str) -> str:
    return (type_text or '').split('.')[-1].split('<', 1)[0].strip()

def source_uses_delegated_viewmodels(source_code: str, analysis_report=None) -> bool:
    analysis_report = analysis_report or _analysis_report_for(source_code)
    return bool((property_info.is_delegated and property_info.delegate_call.split('<', 1)[0].split('(', 1)[0].strip() in {'viewModels', 'activityViewModels'} for property_info in analysis_report.properties))

def source_uses_carui_toolbar_progress(source_code: str, analysis_report=None) -> bool:
    analysis_report = analysis_report or _analysis_report_for(source_code)
    return bool('CarUi.requireToolbar' in (source_code or '') and ('progressBar' in kotlin_identifier_set(source_code or '') or any((call.name == 'getProgressBar' for call in analysis_report.calls))))

def classify_fragment_source(source_code: str, analysis_report=None) -> 'FragmentSourceProfile':
    from UnitTest_gen.kotlin.analysis import classify_fragment_from_report
    return classify_fragment_from_report(analysis_report or _analysis_report_for(source_code or ''))

def source_declares_android_fragment(source_code: str, analysis_report=None) -> bool:
    from UnitTest_gen.kotlin.analysis import _declares_super_type
    return _declares_super_type(analysis_report or _analysis_report_for(source_code), {'Fragment', 'DialogFragment'})

def source_declares_viewmodel_class(source_code: str, analysis_report=None) -> bool:
    from UnitTest_gen.kotlin.analysis import _declares_super_type
    return _declares_super_type(analysis_report or _analysis_report_for(source_code), {'ViewModel', 'AndroidViewModel'})

import re
from dataclasses import dataclass

PLAIN_UNIT_TAG = 'plain_unit'

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

JDK_GET_INSTANCE_NOISE = frozenset(
    {
        'MessageDigest',
        'KeyGenerator',
        'KeyPairGenerator',
        'Cipher',
        'Mac',
        'SecureRandom',
        'Calendar',
        'Currency',
        'Locale',
        'Logger',
        'Executors',
    }
)

WORKER_SUPER_TYPES = frozenset({'Worker', 'CoroutineWorker', 'ListenableWorker'})
FRAGMENT_SUPER_TYPES = frozenset({'Fragment', 'DialogFragment'})
ACTIVITY_SUPER_TYPES = frozenset(
    {'Activity', 'ComponentActivity', 'FragmentActivity', 'AppCompatActivity'}
)
APPLICATION_SUPER_TYPES = frozenset({'Application', 'MultiDexApplication'})
VIEWMODEL_SUPER_TYPES = frozenset({'ViewModel', 'AndroidViewModel'})
BROADCAST_SERVICE_SUPER_TYPES = frozenset({'BroadcastReceiver', 'Service'})

@dataclass(frozen=True)
class SourceProfile:
    categories: frozenset[str]
    imports: tuple[str, ...]
    required_dependencies: frozenset[str]

def _declares_super_type(report: KotlinStaticAnalysisReport, type_names: set[str]) -> bool:
    wanted = {simple_type_name(name) for name in type_names}
    for shape in report.class_shapes:
        if any(simple_type_name(super_type) in wanted for super_type in shape.super_types):
            return True
    return any(
        simple_type_name(super_type) in wanted
        for decl in report.declarations
        if decl.kind in {'class', 'object', 'interface'}
        for super_type in decl.super_types
    )

def declaration_annotations(report: KotlinStaticAnalysisReport) -> set[str]:
    names: set[str] = set()
    for decl in report.declarations:
        names.update(decl.annotations)
    for shape in report.class_shapes:
        names.update(shape.annotations)
    for fn in report.functions:
        names.update(fn.annotations)
    for prop in report.properties:
        names.update(prop.annotations)
    return names

def has_annotation(report: KotlinStaticAnalysisReport, *names: str) -> bool:
    wanted = set(names)
    return bool(declaration_annotations(report) & wanted)

def imports_match(report: KotlinStaticAnalysisReport, *needles: str) -> bool:
    joined = '\n'.join(report.imports)
    return any(needle in joined for needle in needles)

def imports_startswith(report: KotlinStaticAnalysisReport, *prefixes: str) -> bool:
    return any(path.startswith(prefix) for path in report.imports for prefix in prefixes)

def calls_named(report: KotlinStaticAnalysisReport, *names: str) -> bool:
    wanted = set(names)
    return any(call.name in wanted for call in report.calls)

def call_evidence_contains(report: KotlinStaticAnalysisReport, *needles: str) -> bool:
    return any(
        any(needle in (call.evidence or '') for needle in needles)
        for call in report.calls
    )

def identifiers_contain(report: KotlinStaticAnalysisReport, *names: str) -> bool:
    wanted = set(names)
    return bool(report.identifiers & wanted)

def _primary_class_shape(report: KotlinStaticAnalysisReport) -> KotlinClassShape | None:
    for shape in report.class_shapes:
        return shape
    return None

def _get_instance_types_from_report(report: KotlinStaticAnalysisReport) -> set[str]:
    types: set[str] = set()
    for call in report.calls:
        if call.name != 'getInstance':
            continue
        match = re.match(r'^([A-Za-z_][A-Za-z0-9_.]*)\.getInstance\s*\(', call.evidence or '')
        if match:
            simple = match.group(1).split('.')[-1]
            if simple not in JDK_GET_INSTANCE_NOISE:
                types.add(simple)
    for fn in report.functions:
        if fn.name == 'getInstance':
            for shape in report.class_shapes:
                types.add(shape.name)
    return types

def _classify_fragment_from_report(
    report: KotlinStaticAnalysisReport,
    *,
    source_file_path: str = '',
) -> FragmentSourceProfile:
    is_fragment = _declares_super_type(report, FRAGMENT_SUPER_TYPES)
    is_dialog = is_fragment and _declares_super_type(report, {'DialogFragment'})
    is_hilt = is_fragment and has_annotation(report, 'AndroidEntryPoint')
    has_navigation = (
        calls_named(report, 'findNavController', 'navigate')
        or imports_match(report, 'androidx.navigation')
        or identifiers_contain(report, 'NavController', 'NavDeepLinkRequest')
    )
    has_delegated_viewmodel = source_uses_delegated_viewmodels('', analysis_report=report)
    has_carui_toolbar = call_evidence_contains(report, 'CarUi.requireToolbar')
    has_carui_progress = has_carui_toolbar and (
        identifiers_contain(report, 'progressBar') or calls_named(report, 'getProgressBar')
    )
    has_carui_back_listener = calls_named(report, 'registerBackListener')
    markers: list[str] = []
    if is_fragment:
        checks = [
            ('Hilt injection', has_annotation(report, 'AndroidEntryPoint', 'Inject')),
            ('navigation', has_navigation),
            ('delegated ViewModel', has_delegated_viewmodel),
            ('CarUi toolbar', has_carui_toolbar),
            ('CarUi back listener', has_carui_back_listener),
            ('toolbar progress', has_carui_progress),
            (
                'dialog/callback UI',
                _declares_super_type(report, {'DialogFragment'})
                or calls_named(report, 'show')
                and identifiers_contain(report, 'AlertDialog', 'DialogFragment'),
            ),
            ('BuildConfig branch', identifiers_contain(report, 'BuildConfig')),
            (
                'async/lifecycle timing',
                calls_named(
                    report,
                    'postDelayed',
                    'observe',
                    'repeatOnLifecycle',
                )
                or call_evidence_contains(report, 'lifecycleScope', 'Handler('),
            ),
            (
                'multiple navigation paths',
                sum(1 for call in report.calls if call.name in {'findNavController', 'navigate'}) >= 2,
            ),
        ]
        for name, present in checks:
            if present:
                markers.append(name)
    method_count = len(report.functions)
    line_estimate = max(
        (fn.span.end_line for fn in report.functions),
        default=0,
    )
    is_complex = is_fragment and (len(markers) >= 4 or line_estimate >= 220 or method_count >= 10)
    tags: set[str] = set()
    if is_fragment:
        tags.update({'android_fragment', 'android_ui'})
        tags.add('dialog_fragment' if is_dialog else 'plain_fragment')
    if is_hilt:
        tags.update({'hilt_entrypoint', 'hilt_fragment', 'hilt_graph'})
        tags.discard('plain_fragment')
    if has_navigation:
        tags.update({'android_navigation', 'navigation_fragment'})
    if identifiers_contain(report, 'NavDeepLinkRequest') or call_evidence_contains(report, 'NavDeepLinkRequest'):
        tags.add('nav_deeplink')
    if _get_instance_types_from_report(report):
        tags.update({'alert_dialog_helper', 'static_singleton_get_instance', 'companion_singleton_static_mock'})
    if has_delegated_viewmodel:
        tags.add('delegated_viewmodel_fragment')
    if has_carui_toolbar:
        tags.update({'car', 'carui_toolbar_fragment'})
    if has_carui_back_listener:
        tags.add('carui_back_listener')
    if has_carui_progress:
        tags.add('carui_progress_fragment')
    if (
        is_fragment
        and has_delegated_viewmodel
        and calls_named(report, 'setOnClickListener')
        and (
            call_evidence_contains(report, 'Dispatchers.IO', 'CoroutineScope(Dispatchers.IO')
            or any(usage.dispatcher == 'IO' for usage in report.coroutine_usages)
        )
    ):
        tags.add('fragment_triggers_uncontrolled_viewmodel_io')
    if is_complex:
        tags.add('complex_fragment')
    del source_file_path
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

def _constructor_has_injection(shape: KotlinClassShape) -> bool:
    if has_annotation_on_shape(shape, 'AssistedInject', 'Inject'):
        return True
    return any('Inject' in param.annotations or 'AssistedInject' in param.annotations for param in shape.constructor_params)

def has_annotation_on_shape(shape: KotlinClassShape, *names: str) -> bool:
    wanted = set(names)
    return bool(set(shape.annotations) & wanted)

_DEP_RULES: tuple[tuple[frozenset[str], tuple[str, ...]], ...] = (
    (frozenset({'android_fragment', 'android_ui', 'android_navigation', 'android_dialog', 'car', 'android_log', 'hilt_worker', 'worker_service', 'android_context'}), ('org.robolectric:robolectric', 'androidx.test:core')),
    (frozenset({'android_fragment'}), ('androidx.fragment:fragment-testing',)),
    (frozenset({'android_navigation'}), ('androidx.navigation:navigation-testing',)),
    (frozenset({'hilt_entrypoint', 'android_fragment'}), ('com.google.dagger:hilt-android-testing', 'com.google.dagger:hilt-android-compiler')),
    (frozenset({'hilt_worker'}), ('org.jetbrains.kotlinx:kotlinx-coroutines-test',)),
    (frozenset({'coroutines_flow', 'viewmodel'}), ('org.jetbrains.kotlinx:kotlinx-coroutines-test',)),
    (frozenset({'livedata'}), ('androidx.arch.core:core-testing',)),
    (frozenset({'storage_json_auth'}), ('org.json:json',)),
    (frozenset({'play_services'}), ('org.jetbrains.kotlinx:kotlinx-coroutines-play-services',)),
    (frozenset({'static_api'}), ('org.mockito:mockito-inline',)),
    (frozenset({'mockk'}), ('io.mockk:mockk',)),
)

_INSTRUMENTED_DEP_RULES: tuple[tuple[frozenset[str], tuple[str, ...]], ...] = (
    (frozenset({'hilt_fragment', 'hilt_entrypoint', 'hilt_android_activity', 'hilt_service'}), (
        'androidTestImplementation:com.google.dagger:hilt-android-testing',
        'kspAndroidTest:com.google.dagger:hilt-android-compiler',
        'androidTestImplementation:androidx.test.ext:junit',
        'androidTestImplementation:androidx.test:runner',
    )),
    (frozenset({'android_fragment', 'android_ui', 'android_navigation', 'hilt_fragment'}), (
        'androidTestImplementation:androidx.test.ext:junit',
        'androidTestImplementation:androidx.test.espresso:espresso-core',
    )),
    (frozenset({'android_work_manager', 'hilt_worker', 'worker_service'}), (
        'androidTestImplementation:androidx.work:work-testing',
        'androidTestImplementation:androidx.test.ext:junit',
    )),
    (frozenset({'android_foreground_service'}), (
        'androidTestImplementation:androidx.test.ext:junit',
        'androidTestImplementation:androidx.test:rules',
    )),
    (frozenset({'mockk'}), ('androidTestImplementation:io.mockk:mockk-android',)),
)

def _required_dependencies(categories: set[str]) -> frozenset[str]:
    required = {'libs.junit', 'kotlin:test'}
    cats = frozenset(categories)
    for triggers, deps in _DEP_RULES:
        if cats & triggers:
            required.update(deps)
    return frozenset(required)

def instrumented_required_dependencies(categories: set[str]) -> frozenset[str]:
    required: set[str] = {
        'androidTestImplementation:androidx.test.ext:junit',
        'androidTestImplementation:androidx.test:runner',
    }
    cats = frozenset(categories)
    for triggers, deps in _INSTRUMENTED_DEP_RULES:
        if cats & triggers:
            required.update(deps)
    return frozenset(required)

def classify_from_report(
    report: KotlinStaticAnalysisReport,
    *,
    source_file_path: str = '',
    source_code: str = '',
) -> SourceProfile:
    """Classify a Kotlin source using only tree-sitter/Semgrep report facts."""
    categories: set[str] = set()
    if (
        report.coroutine_usages
        or any(fn.is_suspend for fn in report.functions)
        or imports_startswith(report, 'kotlinx.coroutines')
    ):
        categories.add('coroutines_flow')
    if report.confirmed_findings('hardcoded_dispatcher'):
        categories.update({'hardcoded_dispatcher_entry', 'coverage_strategy'})
    if report.confirmed_findings('detached_coroutine_scope'):
        categories.update({'detached_coroutine_scope', 'coverage_strategy'})
    if report.frameworks.mockito:
        categories.add('mockito')
    if report.frameworks.mockk:
        categories.add('mockk')

    fragment_profile = _classify_fragment_from_report(report, source_file_path=source_file_path)
    if fragment_profile.is_fragment:
        categories.update({'android_fragment', 'android_ui'})
        categories.update(fragment_profile.strategy_tags)
    if fragment_profile.has_navigation:
        categories.add('android_navigation')
    if 'nav_deeplink' in fragment_profile.strategy_tags:
        categories.add('nav_deeplink')
    if 'carui_toolbar_fragment' in fragment_profile.strategy_tags:
        categories.add('carui_toolbar')
    if 'carui_progress_fragment' in fragment_profile.strategy_tags:
        categories.add('carui_progress')

    primary = _primary_class_shape(report)
    if primary and _constructor_has_injection(primary):
        categories.add('constructor_injection')
    if has_annotation(report, 'AndroidEntryPoint'):
        categories.add('hilt_entrypoint')
        if _declares_super_type(report, {'Service'}):
            categories.add('hilt_service')
    if has_annotation(report, 'HiltViewModel'):
        categories.add('hilt_viewmodel')
    if has_annotation(report, 'HiltWorker'):
        categories.add('hilt_worker')
    if has_annotation(report, 'AssistedInject'):
        categories.add('hilt_assisted_inject')
    if has_annotation(report, 'HiltViewModel') or _declares_super_type(report, VIEWMODEL_SUPER_TYPES):
        categories.add('viewmodel')
    if _declares_super_type(report, ACTIVITY_SUPER_TYPES):
        categories.add('android_activity')
        if has_annotation(report, 'AndroidEntryPoint'):
            categories.add('hilt_android_activity')
    if _declares_super_type(report, APPLICATION_SUPER_TYPES):
        categories.add('android_application')
        if has_annotation(report, 'HiltAndroidApp'):
            categories.add('hilt_android_application')
    if has_annotation(report, 'Module', 'Binds', 'Provides'):
        categories.add('dagger_module')
    if is_declarative_dagger_binding(source_code or '', report):
        categories.add('declarative_dagger_binding')
    elif has_annotation(report, 'Provides'):
        categories.add('dagger_provider_logic')
    if (
        imports_match(report, 'androidx.room', 'RoomDatabase')
        or has_annotation(report, 'Dao', 'Entity')
        or identifiers_contain(report, 'Room')
    ):
        categories.add('room')
    if imports_match(report, 'retrofit2', 'okhttp3') or identifiers_contain(report, 'Authenticator', 'Interceptor'):
        categories.add('network')
    if imports_match(report, 'com.apollographql', 'ApolloClient') or identifiers_contain(report, 'ApolloClient'):
        categories.add('apollo')

    get_instance_types = _get_instance_types_from_report(report) | source_get_instance_types(source_code or '')
    singleton_calls = call_evidence_contains(
        report,
        'FirebaseAnalytics.getInstance',
        'FirebaseInstallations.getInstance',
        'FirebaseCrashlytics.getInstance',
    )
    if singleton_calls or get_instance_types:
        categories.add('static_singleton_get_instance')
        categories.add('companion_singleton_static_mock')
    if (
        imports_match(report, 'com.android.car.ui', 'android.car')
        or call_evidence_contains(report, 'CarUi.', 'CarPropertyManager')
    ):
        categories.add('car')
    if _declares_super_type(report, WORKER_SUPER_TYPES | BROADCAST_SERVICE_SUPER_TYPES):
        categories.add('worker_service')
    if (
        _declares_super_type(report, {'Service'})
        and call_evidence_contains(report, 'onStartCommand', 'startForeground', 'stopForeground')
    ):
        categories.add('android_foreground_service')
    if (
        imports_match(report, 'androidx.work', 'WorkManager')
        or identifiers_contain(report, 'WorkManager', 'WorkerParameters')
        or has_annotation(report, 'HiltWorker')
    ):
        categories.add('android_work_manager')
    if imports_match(report, 'android.bluetooth') or identifiers_contain(report, 'BluetoothAdapter', 'BluetoothManager'):
        categories.add('bluetooth_hardware')
    if imports_match(report, 'android.hardware.camera') or identifiers_contain(report, 'CameraManager', 'CameraDevice'):
        categories.add('camera_hardware')
    if (
        imports_match(report, 'SharedPreferences', 'PreferenceManager', 'org.json', 'net.openid.appauth')
        or calls_named(report, 'AuthState')
    ):
        categories.add('storage_json_auth')
    if imports_startswith(report, 'com.google.firebase') or identifiers_contain(report, 'Firebase'):
        categories.add('firebase')
    if imports_match(report, 'timber.log') or calls_named(report, 'Timber') or call_evidence_contains(report, 'Timber.'):
        categories.add('logging')
    if imports_match(report, 'android.util.Log') or call_evidence_contains(report, 'Log.'):
        categories.add('android_log')
    if (
        imports_match(report, 'android.content', 'android.app', 'android.content.res')
        or identifiers_contain(report, 'Context', 'Activity', 'Application', 'Resources')
        or any('R.' in call.evidence for call in report.calls)
    ):
        categories.add('android_context')
    if _declares_super_type(report, {'DialogFragment'}) or identifiers_contain(report, 'AlertDialog', 'MaterialAlertDialog'):
        categories.add('android_dialog')
    if get_instance_types and (
        'android_dialog' in categories
        or calls_named(report, 'showAlert', 'onUserAction', 'onPositiveAction', 'setButton')
        or identifiers_contain(report, 'AlertDialog')
    ):
        categories.add('alert_dialog_helper')
    if imports_match(report, 'kotlinx.coroutines.tasks', 'com.google.android.gms.tasks') or call_evidence_contains(report, '.await()'):
        categories.add('play_services')
    if (
        call_evidence_contains(
            report,
            'CarUi.',
            'FirebaseAnalytics.getInstance',
            'FirebaseInstallations.getInstance',
            'FirebaseCrashlytics.getInstance',
            'System.',
        )
        or get_instance_types
    ):
        categories.update({'static_api', 'sdk_static_seam'})
    if imports_match(report, 'androidx.lifecycle.LiveData', 'LiveData') or any(
        'LiveData' in prop.type_text for prop in report.properties
    ):
        categories.add('livedata')
    if source_referenced_kotlin_object_names(source_code or '', source_file_path, report):
        categories.add('kotlin_object_seam')
    if 'delegated_viewmodel_fragment' in categories:
        categories.add('delegated_viewmodel_drive')
    if 'hilt_fragment' in categories:
        categories.add('closed_hilt_graph')
    if 'dagger_provider_logic' in categories or 'dagger_module' in categories:
        categories.add('dagger_module_contract_probe')
    if 'viewmodel' in categories and 'livedata' in categories and 'android_fragment' not in categories:
        categories.add('viewmodel_private_live_data_seam')

    return SourceProfile(
        frozenset(categories),
        report.imports,
        _required_dependencies(categories),
    )

_STRATEGY_HINTS: tuple[tuple[str, str], ...] = (
    ('hilt_worker', 'test_strategy: @HiltWorker — construct via @AssistedInject constructor with Robolectric Context, mock<WorkerParameters>() (or mockk), and mocked inject params; runTest { doWork() }; assert ListenableWorker.Result; do NOT use HiltWorkerFactory, buildWorkerParams, work-testing, or 2-arg constructor for unit tests'),
    ('hilt_service', 'test_strategy: Hilt @AndroidEntryPoint Service — use @HiltAndroidTest + HiltAndroidRule + @Config(application = HiltTestApplication::class), bind @Inject fields via @BindValue/@TestInstallIn, call hiltRule.inject() in @Before, and trigger lifecycle via Android startService(Intent) so Hilt runs.'),
    ('hilt_fragment', 'test_strategy: @AndroidEntryPoint Fragment — @HiltAndroidTest + HiltAndroidRule + @BindValue for collaborators; Robolectric fragment lifecycle'),
    ('hilt_android_activity', 'test_strategy: @AndroidEntryPoint Activity — @HiltAndroidTest + HiltAndroidRule + @Config(application = HiltTestApplication::class) + @BindValue/@TestInstallIn for @Inject deps BEFORE Robolectric.create(); never assign inject fields after create(); never claim Hilt N/A when planning .create()'),
    ('viewmodel', 'test_strategy: ViewModel — direct construction or factory with mocked deps; runTest for suspend APIs'),
    ('worker_service', 'test_strategy: Worker/Receiver/Service — Robolectric context + direct construction; mock WorkerParameters (do not use work-testing builders)'),
)

def format_classification_brief(
    profile: SourceProfile,
    report: KotlinStaticAnalysisReport,
) -> str:
    """Compact authoritative classification block for the plan agent."""
    lines: list[str] = [f"confidence: {'low' if report.validation_errors else 'high'}"]
    primary = _primary_class_shape(report)
    if primary:
        ann = ', '.join(f'@{name}' for name in primary.annotations) or '(none)'
        supers = ', '.join(primary.super_types) or '(none)'
        lines.append(f'primary_class: {primary.name} annotations={ann} extends={supers}')
    if suspend_fns := [fn.name for fn in report.functions if fn.is_suspend]:
        lines.append(f'suspend_entrypoints: {", ".join(suspend_fns)}')
    if primary and primary.constructor_params:
        assisted = [f'{p.name}:{p.type_text or "?"}' for p in primary.constructor_params if 'Assisted' in p.annotations]
        injected = [f'{p.name}:{p.type_text or "?"}' for p in primary.constructor_params if 'Assisted' not in p.annotations and p.name]
        if assisted:
            lines.append(f'assisted_ctor_params: {", ".join(assisted)}')
        if injected:
            lines.append(f'inject_ctor_params (mock in unit tests): {", ".join(injected)}')
    for tag, hint in _STRATEGY_HINTS:
        if tag in profile.categories:
            lines.append(hint)
            break
    lines.append('mocking: choose exactly one of mockito-kotlin or io.mockk; never both')
    if profile.required_dependencies:
        lines.append('required_test_deps: ' + ', '.join(sorted(profile.required_dependencies)))
    return '\n'.join(lines)

def classify_fragment_from_report(
    report: KotlinStaticAnalysisReport,
    *,
    source_file_path: str = '',
) -> FragmentSourceProfile:
    return _classify_fragment_from_report(report, source_file_path=source_file_path)

__all__ = [
    'FragmentSourceProfile',
    'SourceProfile',
    'PLAIN_UNIT_TAG',
    'classify_from_report',
    'classify_fragment_from_report',
    'format_classification_brief',
    'declaration_annotations',
    'has_annotation',
    'imports_match',
    'calls_named',
    'call_evidence_contains',
    'identifiers_contain',
]

import hashlib
import os
import re
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from UnitTest_gen.core import io as file_cache
from UnitTest_gen.core.io import get_pipeline_log_dir, log_message
from UnitTest_gen.core.io import DEFAULT_SKIP_DIRS, walk_source_roots
from UnitTest_gen.core.semgrep import SemgrepParseError, SemgrepUnavailableError, require_semgrep_executable, scan_text_with_semgrep

__all__ = [
    "CallbackUsage", "CoroutineUsage", "FindingDisposition", "FrameworkUsage", "KotlinCallUsage",
    "KotlinClassShape", "KotlinConstructorParam", "KotlinDeclarationInfo", "KotlinFunctionInfo",
    "KotlinPropertyInfo", "KotlinStaticAnalysisReport", "KotlinTestFunctionInfo", "KotlinTestMetadata",
    "SemgrepParseError", "SemgrepUnavailableError", "StaticFinding", "analyze_kotlin_code",
    "analyze_kotlin_test_code", "extract_junit4_test_blocks", "persist_static_analysis_report",
    "require_semgrep_preflight", "verify_project_exception_findings",
]

REPORT_SCHEMA_VERSION = "1.2"
SEMGREP_RULE_CONFIG = os.path.join(os.path.dirname(__file__), "semgrep_rules", "kotlin-testgen.yml")
SEMGREP_EXCEPTION_RULE_CONFIG = os.path.join(os.path.dirname(__file__), "semgrep_rules", "kotlin-exception-constructors.yml")
_CALLBACK_NAMES = {
    "setMenuItems", "setOnClickListener", "setOnLongClickListener", "registerBackListener", "observeForever",
    "observe", "onEach", "collectLatest", "collect", "postDelayed", "registerForActivityResult", "setFragmentResultListener",
}
_LIFECYCLE_CALLS = {"onAttach", "onCreate", "onCreateView", "onViewCreated", "onStart", "onResume"}
_RULE_CATEGORY = {
    "kotlin.hardcoded-dispatcher": "hardcoded_dispatcher",
    "kotlin.detached-coroutine-scope": "detached_coroutine_scope",
    "kotlin.mockk-api": "mocking_framework_mismatch",
    "kotlin.sdk-exception-constructor-candidate": "speculative_sdk_exception_constructor",
}
_STANDARD_EXCEPTIONS = {
    "Exception", "RuntimeException", "IllegalArgumentException", "IllegalStateException",
    "NullPointerException", "UnsupportedOperationException", "IOException", "AssertionError",
}
_REPORT_CACHE: dict[tuple[str, str], KotlinStaticAnalysisReport] = {}
_PREFLIGHT_VERSION = ""

def analyze_kotlin_code(source_code: str, source_path: str = "", *, persist: bool = False, expected_mocking_framework: str = "mockito") -> KotlinStaticAnalysisReport:
    source_hash = hashlib.sha256((source_code or "").encode("utf-8")).hexdigest()
    cache_key = (source_hash, expected_mocking_framework)
    cached = _REPORT_CACHE.get(cache_key)
    if cached:
        resolved = cached.model_copy(update={"source_path": source_path}) if source_path and cached.source_path != source_path else cached
        if persist and source_path:
            persist_static_analysis_report(resolved)
        return resolved
    source_code = source_code or ""
    source_bytes = source_code.encode("utf-8")
    root = parse_kotlin_ast(source_code)
    imports = tuple(ast_text(source_bytes, qualified) for child in root.children if child.type == "import" for qualified in [direct_child(child, "qualified_identifier")] if qualified is not None)
    declarations = tuple(_extract_declarations(source_code, root))
    properties = tuple(_extract_properties(source_code, root))
    functions = tuple(_extract_functions(source_code, root))
    coroutine_usages = tuple(_extract_coroutine_usages(source_code, functions, root))
    callbacks = tuple(_extract_callbacks(source_code, functions, root))
    calls = tuple(_extract_calls(source_code, functions, root))
    class_shapes = tuple(_extract_class_shapes(source_code, root))
    identifiers = frozenset(_identifier_texts(source_code, root))
    frameworks = _detect_frameworks(imports, source_code)
    tests = _extract_test_metadata(source_code, functions, properties, calls, root)
    semgrep_runtime_version = require_semgrep_preflight()
    is_test_candidate = bool(frameworks.junit4 or frameworks.junit5 or frameworks.mockito or frameworks.mockk or "/src/test/" in (source_path or "").replace("\\", "/"))
    policy_relevant = bool(frameworks.mockk or any(token in source_code for token in ("Dispatchers.IO", "Dispatchers.Default", "GlobalScope", "CoroutineScope(", "mockk(", "coEvery", "coVerify", "mockkObject", "mockkStatic")))
    rule_configs = [SEMGREP_RULE_CONFIG] if policy_relevant or is_test_candidate else []
    if is_test_candidate and any(identifier.endswith("Exception") or (identifier.endswith("Error") and identifier != "Error") for identifier in identifiers):
        rule_configs.append(SEMGREP_EXCEPTION_RULE_CONFIG)
    scans = ()
    if rule_configs:
        scan_jobs = 1 if len(rule_configs) > 1 or len(source_code.splitlines()) > 500 else None
        try:
            scans = (scan_text_with_semgrep(source_code, tuple(rule_configs), ".kt", jobs=scan_jobs, source_path=source_path),)
        except SemgrepUnavailableError as exc:
            message = str(exc).lower()
            if not isinstance(exc, SemgrepParseError) and not any(marker in message for marker in ("reported analysis errors:", "syntax error", "was unexpected")):
                raise
            log_message(f"⚠️ Semgrep could not parse this Kotlin snippet; continuing with tree-sitter analysis only. {exc}", category="warning")
    findings = tuple(_normalize_finding(item, source_code, imports, frameworks, expected_mocking_framework) for scan in scans for item in scan.findings)
    tree_sitter_error_lines = sorted({node.start_point[0] + 1 for node in walk_ast(root) if node.type == "ERROR"})
    syntax_error_lines = [] if tree_sitter_error_lines and scans and all(scan.parse_clean for scan in scans) else tree_sitter_error_lines
    validation_errors = tuple(f"invalid_kotlin_syntax: malformed backtick function declaration at line {line_no}" for line_no, line in enumerate(source_code.splitlines(), start=1) if re.search(r"\bfun\s+`[^`]*\([^`]*\)`\s*\{", line)) + tuple(f"invalid_kotlin_syntax: Tree-sitter found malformed Kotlin near line {line_no}" for line_no in syntax_error_lines)
    report = KotlinStaticAnalysisReport(
        schema_version=REPORT_SCHEMA_VERSION, source_path=source_path, source_hash=source_hash, tree_sitter_language="kotlin",
        semgrep_version="/".join(dict.fromkeys(scan.version for scan in scans)) if scans else semgrep_runtime_version,
        status="complete", imports=imports, declarations=declarations, properties=properties, functions=functions,
        coroutine_usages=coroutine_usages, callbacks=callbacks, calls=calls, frameworks=frameworks, tests=tests,
        class_shapes=class_shapes, identifiers=identifiers, findings=findings, validation_errors=validation_errors,
    )
    _REPORT_CACHE[cache_key] = report
    if persist and source_path:
        persist_static_analysis_report(report)
    return report

def analyze_kotlin_test_code(test_code: str, source_code: str = "") -> KotlinStaticAnalysisReport:
    del source_code
    return analyze_kotlin_code(test_code or "", expected_mocking_framework="mockito")

def persist_static_analysis_report(report: KotlinStaticAnalysisReport) -> str:
    os.makedirs(get_pipeline_log_dir(), exist_ok=True)
    stem = Path(report.source_path or "KotlinSource.kt").stem
    path = os.path.join(get_pipeline_log_dir(), f"{stem}.static-analysis.json")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(report.model_dump_json(indent=2) + "\n")
    log_message(f"🔎 Wrote strict static analysis report: {path}", category="context", console=False)
    return path

def verify_project_exception_findings(report: KotlinStaticAnalysisReport, project_root: str, exclude_path: str = "") -> KotlinStaticAnalysisReport:
    if not project_root or not os.path.isdir(project_root):
        return report
    updated = []
    for finding in report.findings:
        if finding.category != "speculative_sdk_exception_constructor" or finding.disposition != FindingDisposition.CANDIDATE:
            updated.append(finding)
            continue
        exception_name = finding.evidence.split("(", 1)[0].strip().split(".")[-1]
        nested_shape = ".Subcode" in finding.evidence or f"{exception_name}." in finding.evidence
        if _project_has_exception_shape(project_root, exception_name, nested_shape, exclude_path):
            updated.append(finding.model_copy(update={"disposition": FindingDisposition.DISMISSED, "verification": "A matching project declaration or established constructor/member usage was found."}))
        elif nested_shape:
            updated.append(finding.model_copy(update={"disposition": FindingDisposition.CONFIRMED, "verification": "No project declaration or established usage verifies this nested SDK constructor/member shape."}))
        else:
            updated.append(finding)
    return report.model_copy(update={"findings": tuple(updated)})

def require_semgrep_preflight() -> str:
    global _PREFLIGHT_VERSION
    if _PREFLIGHT_VERSION:
        return _PREFLIGHT_VERSION
    require_semgrep_executable()
    for config_path in (SEMGREP_RULE_CONFIG, SEMGREP_EXCEPTION_RULE_CONFIG):
        if not os.path.isfile(config_path):
            raise SemgrepUnavailableError(f"Mandatory local Semgrep rules are missing: {config_path}")
    scan = scan_text_with_semgrep("class SemgrepPreflight", SEMGREP_RULE_CONFIG, ".kt", jobs=1)
    try:
        package_version = version("semgrep")
    except PackageNotFoundError:
        package_version = "package-unknown"
    _PREFLIGHT_VERSION = f"package={package_version}, core={scan.version}"
    return _PREFLIGHT_VERSION

def _span(node) -> SourceSpan:
    return SourceSpan(start_line=node.start_point[0] + 1, start_column=node.start_point[1] + 1, end_line=node.end_point[0] + 1, end_column=node.end_point[1] + 1)

def _identifier_texts(source_code: str, root=None) -> set[str]:
    return kotlin_identifier_set(source_code, root=root)

def _modifier_texts(source_bytes: bytes, node) -> tuple[str, ...]:
    modifiers_node = direct_child(node, "modifiers")
    return tuple(ast_text(source_bytes, child) for child in (modifiers_node.children if modifiers_node else ()) if ast_text(source_bytes, child).strip() and child.type != "annotation")

def _declaration_visibility(source_bytes: bytes, node) -> tuple[str, tuple[str, ...]]:
    texts = _modifier_texts(source_bytes, node)
    for visibility in ("private", "protected", "internal", "public"):
        if visibility in texts:
            return visibility, texts
    return "public", texts

def _variable_name(source_bytes: bytes, node) -> str:
    variable = direct_child(node, "variable_declaration")
    if not variable:
        return declaration_name(source_bytes, node)
    identifier = direct_child(variable, "identifier")
    return ast_text(source_bytes, identifier) if identifier else ""

def _property_type_text(source_bytes: bytes, node) -> str:
    variable = direct_child(node, "variable_declaration")
    if not variable:
        return ""
    type_node = direct_child(variable, "type")
    return ast_text(source_bytes, type_node).removeprefix(":").strip() if type_node else ""

def _super_types(source_bytes: bytes, node) -> tuple[str, ...]:
    specs = direct_child(node, "delegation_specifiers")
    if not specs:
        return ()
    values = []
    for spec in direct_children(specs, "delegation_specifier"):
        user_type = next((child for child in walk_ast(spec) if child.type == "user_type"), None)
        if user_type:
            values.append(ast_text(source_bytes, user_type).split("<", 1)[0].strip())
    return tuple(dict.fromkeys(value for value in values if value))

def _declaration_kind(node) -> str:
    return {"class_declaration": "class", "object_declaration": "object", "interface_declaration": "interface", "function_declaration": "function", "property_declaration": "property", "typealias_declaration": "typealias"}.get(node.type, "")

def _constructor_param_type_text(source_bytes: bytes, param_node) -> str:
    type_node = direct_child(param_node, "type")
    if type_node:
        return ast_text(source_bytes, type_node).removeprefix(":").strip()
    for child in param_node.children:
        if child.type == "user_type":
            return ast_text(source_bytes, child).strip()
    return ""

def _extract_constructor_params(source_bytes: bytes, primary_constructor) -> list[KotlinConstructorParam]:
    if primary_constructor is None:
        return []
    params_node = direct_child(primary_constructor, "function_value_parameters") or direct_child(primary_constructor, "class_parameters")
    if params_node is None:
        for child in primary_constructor.children:
            if child.type in {"function_value_parameters", "class_parameters"}:
                params_node = child
                break
    if params_node is None:
        return []
    params: list[KotlinConstructorParam] = []
    for param in direct_children(params_node, "parameter") + direct_children(params_node, "class_parameter"):
        identifier = direct_child(param, "identifier")
        if identifier is None:
            continue
        name = ast_text(source_bytes, identifier)
        if name:
            params.append(KotlinConstructorParam(name=name, type_text=_constructor_param_type_text(source_bytes, param), annotations=tuple(sorted(annotation_names(source_bytes, param)))))
    return params

def _extract_class_shapes(source_code: str, root=None) -> list[KotlinClassShape]:
    source_bytes = source_code.encode("utf-8")
    root = root or parse_kotlin_ast(source_code)
    shapes: list[KotlinClassShape] = []
    for node in walk_ast(root):
        if node.type != "class_declaration":
            continue
        name = declaration_name(source_bytes, node)
        if not name:
            continue
        primary_constructor = direct_child(node, "primary_constructor")
        ctor_annotations = annotation_names(source_bytes, primary_constructor) if primary_constructor is not None else set()
        shapes.append(KotlinClassShape(name=name, annotations=tuple(sorted(annotation_names(source_bytes, node) | ctor_annotations)), super_types=_super_types(source_bytes, node), constructor_params=tuple(_extract_constructor_params(source_bytes, primary_constructor))))
    return shapes

def _extract_declarations(source_code: str, root=None) -> list[KotlinDeclarationInfo]:
    source_bytes = source_code.encode("utf-8")
    root = root or parse_kotlin_ast(source_code)
    declarations = []
    for node in walk_ast(root):
        kind = _declaration_kind(node)
        if not kind:
            continue
        name = _variable_name(source_bytes, node) if kind == "property" else declaration_name(source_bytes, node)
        if not name:
            continue
        visibility, modifiers = _declaration_visibility(source_bytes, node)
        declarations.append(KotlinDeclarationInfo(kind=kind, name=name, visibility=visibility, annotations=tuple(sorted(annotation_names(source_bytes, node))), modifiers=modifiers, super_types=_super_types(source_bytes, node) if kind in {"class", "object", "interface"} else (), span=_span(node)))
    return declarations

def _extract_properties(source_code: str, root=None) -> list[KotlinPropertyInfo]:
    source_bytes = source_code.encode("utf-8")
    root = root or parse_kotlin_ast(source_code)
    properties = []
    for node in walk_ast(root):
        if node.type != "property_declaration":
            continue
        name = _variable_name(source_bytes, node)
        if not name:
            continue
        visibility, modifiers = _declaration_visibility(source_bytes, node)
        delegate = direct_child(node, "property_delegate")
        delegate_call = ast_text(source_bytes, delegate).removeprefix("by").strip() if delegate else ""
        properties.append(KotlinPropertyInfo(name=name, visibility=visibility, annotations=tuple(sorted(annotation_names(source_bytes, node))), modifiers=modifiers, type_text=_property_type_text(source_bytes, node), is_lateinit="lateinit" in modifiers, is_delegated=delegate is not None, delegate_call=delegate_call, span=_span(node)))
    return properties

def _extract_functions(source_code: str, root=None) -> list[KotlinFunctionInfo]:
    source_bytes = source_code.encode("utf-8")
    root = root or parse_kotlin_ast(source_code)
    functions = []
    for node in walk_ast(root):
        if node.type != "function_declaration":
            continue
        name = declaration_name(source_bytes, node)
        if not name:
            continue
        visibility, modifiers = _declaration_visibility(source_bytes, node)
        functions.append(KotlinFunctionInfo(name=name, visibility=visibility, annotations=tuple(sorted(annotation_names(source_bytes, node))), modifiers=modifiers, is_suspend="suspend" in modifiers, span=_span(node)))
    return functions

def _annotation_texts(source_code: str, root=None) -> tuple[str, ...]:
    source_bytes = source_code.encode("utf-8")
    return tuple(dict.fromkeys(ast_text(source_bytes, node).strip() for node in walk_ast(root or parse_kotlin_ast(source_code)) if node.type == "annotation"))

def _config_sdks(annotation_texts: tuple[str, ...]) -> tuple[int, ...]:
    values: list[int] = []
    for text in annotation_texts:
        if not text.startswith("@Config") or "sdk" not in text:
            continue
        digits, in_sdk_value = "", False
        for char in text.split("sdk", 1)[1]:
            if char.isdigit():
                digits += char
                in_sdk_value = True
            elif in_sdk_value:
                break
        if digits:
            values.append(int(digits))
    return tuple(values)

def _owner_function_for_span(functions: tuple[KotlinFunctionInfo, ...], line_no: int) -> KotlinFunctionInfo | None:
    candidates = [item for item in functions if item.span.start_line <= line_no <= item.span.end_line]
    return min(candidates, key=lambda item: item.span.end_line - item.span.start_line) if candidates else None

def _owner_name(functions: tuple[KotlinFunctionInfo, ...], line_no: int) -> str:
    owner = _owner_function_for_span(functions, line_no)
    return owner.name if owner else "<top-level>"

def _extract_test_metadata(source_code: str, functions: tuple[KotlinFunctionInfo, ...], properties: tuple[KotlinPropertyInfo, ...], calls: tuple[KotlinCallUsage, ...], root=None) -> KotlinTestMetadata:
    source_bytes = source_code.encode("utf-8")
    root = root or parse_kotlin_ast(source_code)
    annotations = _annotation_texts(source_code, root)
    test_functions, setup_teardown, helpers = [], [], []
    for function in functions:
        annotation_set = set(function.annotations)
        parameter_lists, has_extra_parameter_list = 0, False
        for node in walk_ast(root):
            if node.type == "function_declaration" and _span(node) == function.span:
                parameter_lists = sum(1 for child in node.children if child.type == "function_value_parameters")
                has_extra_parameter_list = ")(" in ast_text(source_bytes, node).split("{", 1)[0]
                break
        if "Test" in annotation_set:
            test_functions.append(KotlinTestFunctionInfo(name=function.name, annotations=function.annotations, visibility=function.visibility, span=function.span, parameter_list_count=parameter_lists, has_extra_parameter_list=has_extra_parameter_list))
        elif annotation_set & {"Before", "After", "BeforeEach", "AfterEach"}:
            setup_teardown.append(function)
        else:
            helpers.append(function)
    mock_variables = tuple(sorted(property_info.name for property_info in properties if any(call.name == "mock" and property_info.span.start_line == call.span.start_line for call in calls)))
    mocked_static_targets = [call.evidence.split("(", 1)[1].split("::class", 1)[0].strip() for call in calls if call.name == "mockStatic" and "(" in call.evidence]
    lifecycle_names = tuple(call.name for call in calls if call.name in _LIFECYCLE_CALLS)
    property_assignment_lines, malformed_test_declaration_lines = [], []
    for node in walk_ast(root):
        if node.type == "ERROR":
            text = ast_text(source_bytes, node)
            if "@Test" in text and ")(" in text:
                malformed_test_declaration_lines.append(node.start_point[0] + 1)
        if node.type == "assignment":
            text = ast_text(source_bytes, node)
            if text.strip().startswith("verify(") or "verify(" in text.split("=", 1)[0]:
                property_assignment_lines.append(node.start_point[0] + 1)
    return KotlinTestMetadata(
        annotations=annotations, fields=tuple(properties), setup_teardown_functions=tuple(setup_teardown), helper_functions=tuple(helpers),
        test_functions=tuple(test_functions), config_sdks=_config_sdks(annotations), mock_variables=mock_variables,
        mocked_static_targets=tuple(dict.fromkeys(mocked_static_targets)), direct_lifecycle_call_names=tuple(dict.fromkeys(lifecycle_names)),
        property_assignment_verification_lines=tuple(sorted(set(property_assignment_lines))),
        malformed_test_declaration_lines=tuple(sorted(set(malformed_test_declaration_lines))),
        has_hilt_module_install_in=any(text.startswith("@Module") for text in annotations) and any(text.startswith("@InstallIn") for text in annotations),
        has_local_hilt_test_activity=any(declaration.name == "HiltTestActivity" and declaration.kind == "class" for declaration in _extract_declarations(source_code)),
    )

def _dispatcher(text: str) -> str:
    if "Dispatchers.IO" in text:
        return "IO"
    if "Dispatchers.Default" in text:
        return "Default"
    if "Dispatchers.Main" in text:
        return "Main"
    return "Unspecified"

def _extract_coroutine_usages(source_code: str, functions: tuple[KotlinFunctionInfo, ...], root=None) -> list[CoroutineUsage]:
    source_bytes = source_code.encode("utf-8")
    root = root or parse_kotlin_ast(source_code)
    usages: list[CoroutineUsage] = []
    seen: set[tuple[str, int, int]] = set()
    for node in walk_ast(root):
        if node.type != "call_expression":
            continue
        text = ast_text(source_bytes, node)
        kind = ""
        if text.startswith("viewModelScope.launch"):
            kind = "viewmodel_scope_launch"
        elif text.startswith("GlobalScope."):
            kind = "global_scope"
        elif text.startswith("CoroutineScope(") and (".launch" in text or ".async" in text):
            kind = "standalone_scope"
        elif text.startswith("withContext("):
            kind = "with_context"
        elif text.startswith("delay("):
            kind = "delay"
        if not kind:
            continue
        key = (kind, node.start_point[0], node.start_point[1])
        if key in seen:
            continue
        seen.add(key)
        usages.append(CoroutineUsage(kind=kind, dispatcher=_dispatcher(text), function_name=_owner_name(functions, node.start_point[0] + 1), span=_span(node), evidence=text.splitlines()[0][:500]))
    return usages

def _extract_callbacks(source_code: str, functions: tuple[KotlinFunctionInfo, ...], root=None) -> list[CallbackUsage]:
    source_bytes = source_code.encode("utf-8")
    root = root or parse_kotlin_ast(source_code)
    callbacks = []
    for node in walk_ast(root):
        if node.type != "call_expression":
            continue
        callee_identifiers = [ast_text(source_bytes, child) for child in walk_ast(node.children[0]) if child.type == "identifier"]
        kind = next((name for name in callee_identifiers if name in _CALLBACK_NAMES), "")
        if not kind:
            kind = next((name for name in callee_identifiers if re.search(r"(?:Callback|Listener)(?:Impl)?$", name)), "")
        if not kind:
            continue
        callback_body = next((child for child in walk_ast(node) if child is not node and child.type in {"lambda_literal", "object_literal"}), None)
        if callback_body is None:
            continue
        callbacks.append(CallbackUsage(kind=kind, function_name=_owner_name(functions, node.start_point[0] + 1), called_identifiers=tuple(sorted({ast_text(source_bytes, child) for child in walk_ast(node) if child.type == "identifier"})), span=_span(callback_body)))
    return [callback for callback in callbacks if not any(other is not callback and other.kind == callback.kind and other.function_name == callback.function_name and (other.span.start_line, other.span.start_column) <= (callback.span.start_line, callback.span.start_column) and (callback.span.end_line, callback.span.end_column) <= (other.span.end_line, other.span.end_column) and other.span != callback.span for other in callbacks)]

def _extract_calls(source_code: str, functions: tuple[KotlinFunctionInfo, ...], root=None) -> list[KotlinCallUsage]:
    source_bytes = source_code.encode("utf-8")
    root = root or parse_kotlin_ast(source_code)
    calls = []
    for node in walk_ast(root):
        if node.type != "call_expression" or not node.children:
            continue
        identifiers = [ast_text(source_bytes, child) for child in walk_ast(node.children[0]) if child.type == "identifier"]
        if not identifiers:
            continue
        calls.append(KotlinCallUsage(name=identifiers[-1], function_name=_owner_name(functions, node.start_point[0] + 1), span=_span(node), evidence=ast_text(source_bytes, node).splitlines()[0][:500]))
    return calls

def _detect_frameworks(imports: tuple[str, ...], source_code: str) -> FrameworkUsage:
    joined = "\n".join(imports)
    return FrameworkUsage(junit4="org.junit." in joined and "org.junit.jupiter" not in joined, junit5="org.junit.jupiter" in joined, mockito="org.mockito" in joined or "Mockito." in source_code or "whenever(" in source_code, mockk="io.mockk" in joined, robolectric="org.robolectric" in joined, hilt="dagger.hilt" in joined or "@Hilt" in source_code, coroutine_test="kotlinx.coroutines.test" in joined)

def _normalize_finding(raw: dict, source_code: str, imports: tuple[str, ...], frameworks: FrameworkUsage, expected_mocking_framework: str) -> StaticFinding:
    del frameworks
    raw_rule_id = str(raw.get("check_id") or "unknown")
    rule_id = next((rule for rule in _RULE_CATEGORY if raw_rule_id.endswith(rule)), raw_rule_id)
    extra = raw.get("extra") or {}
    start = raw.get("start") or {}
    end = raw.get("end") or start
    start_line = int(start.get("line") or 1)
    end_line = int(end.get("line") or start_line)
    lines = source_code.splitlines()
    start_offset, end_offset = start.get("offset"), end.get("offset")
    evidence = source_code[start_offset:end_offset].strip() if isinstance(start_offset, int) and isinstance(end_offset, int) else "\n".join(lines[max(0, start_line - 1):min(len(lines), end_line)]).strip()
    category = _RULE_CATEGORY.get(rule_id, "static_policy")
    disposition = FindingDisposition.CONFIRMED
    verification = "Matched a local deterministic Semgrep policy."
    if category == "mocking_framework_mismatch":
        if expected_mocking_framework != "mockito":
            disposition, verification = FindingDisposition.DISMISSED, "The configured project mocking framework is not Mockito."
        else:
            verification = "The generator/project lane is Mockito-only; MockK APIs are incompatible."
    elif category == "speculative_sdk_exception_constructor":
        exception_name = evidence.split("(", 1)[0].strip().split(".")[-1]
        line_evidence = "\n".join(lines[max(0, start_line - 1):min(len(lines), end_line)]).strip()
        nested_shape = ".Subcode" in line_evidence or f"{exception_name}." in line_evidence
        if exception_name in _STANDARD_EXCEPTIONS or any(f"class {exception_name}" in line for line in lines):
            disposition, verification = FindingDisposition.DISMISSED, "The exception is standard or declared in the analyzed source."
        else:
            disposition = FindingDisposition.CANDIDATE
            verification = "External nested exception shape requires project verification." if nested_shape and next((path for path in imports if path.endswith("." + exception_name)), "") else "No verified constructor declaration/usage was available; do not block until project context confirms it."
    return StaticFinding(rule_id=rule_id, category=category, severity=str(extra.get("severity") or "WARNING"), disposition=disposition, span=SourceSpan(start_line=start_line, start_column=int(start.get("col") or 1), end_line=end_line, end_column=int(end.get("col") or start.get("col") or 1)), evidence=evidence, message=str(extra.get("message") or "Semgrep policy finding"), verification=verification)

@lru_cache(maxsize=256)
def _project_has_exception_shape(project_root: str, exception_name: str, nested_shape: bool, exclude_path: str) -> bool:
    excluded = os.path.abspath(exclude_path) if exclude_path else ""
    declaration_markers = (f"class {exception_name}", f"interface {exception_name}", f"enum class {exception_name}")
    usage_markers = (f"{exception_name}.Subcode", f"{exception_name}(") if nested_shape else (f"{exception_name}(",)
    for path in walk_source_roots(project_root, roots=(), skip_dirs=DEFAULT_SKIP_DIRS, extensions=(".kt", ".java")):
        path = os.path.abspath(path)
        if excluded and path == excluded:
            continue
        try:
            text = file_cache.read_text(path)
        except file_cache.FileCacheError:
            continue
        if any(marker in text for marker in declaration_markers) or any(marker in text for marker in usage_markers):
            return True
    return False

def extract_junit4_test_blocks(test_code: str) -> list[str]:
    """Return source-ordered JUnit4 test function blocks from Kotlin code."""
    report = analyze_kotlin_code(test_code or "")
    lines = (test_code or "").splitlines()
    return ["\n".join(lines[function.span.start_line - 1:function.span.end_line]) for function in report.functions if "Test" in function.annotations]
