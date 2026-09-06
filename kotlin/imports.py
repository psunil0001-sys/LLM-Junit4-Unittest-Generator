"""SOURCE import resolution and called-import shapes."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING
from zipfile import ZipFile

from UnitTest_gen.core import io as file_cache

from UnitTest_gen.kotlin.recipes import api_doc_path_for_fqcn
from UnitTest_gen.kotlin.analysis import (
    ast_text,
    declaration_name,
    direct_child,
    direct_children,
    parse_kotlin_ast,
    walk_ast,
)

if TYPE_CHECKING:
    from UnitTest_gen.kotlin.imports import ResolvedImport

_SHAPE_CAP = 16
_SKIP_PREFIXES = ("java.", "kotlin.", "kotlinx.")
_NO_JAVAP_PREFIXES = ("java.", "javax.", "kotlin.", "kotlinx.", "android.", "androidx.")
_JAVAP_TIMEOUT_S = 8
_CTOR_LINE = re.compile(
    r"^\s*public(?:\s+protected|\s+private)?\s+(?:final\s+)?"
    r"(?P<name>[\w.$]+)\((?P<params>[^)]*)\);?\s*$"
)
_STATIC_LINE = re.compile(
    r"^\s*public\s+static(?:\s+final)?\s+\S+\s+(?P<name>\w+)\("
)
_KIND_LINE = re.compile(
    r"^\s*public\s+(?:final\s+)?(?P<kind>class|interface|enum)\s+"
)

@dataclass(frozen=True)
class ImportShape:
    fqcn: str
    kind: str
    constructors: tuple[str, ...] = ()
    static_methods: tuple[str, ...] = ()
    seam: str = "constructor"
    mock: str = "mockkConstructor"

def format_called_import_shapes(
    source_code: str,
    resolved: list["ResolvedImport"],
) -> str:
    shapes = resolve_called_import_shapes(source_code, resolved)
    if not shapes:
        return ""
    lines = [
        "CALLED IMPORT SHAPES (constructor vs static vs object; do not contradict):",
    ]
    for shape in shapes:
        lines.append(f"- {_format_shape_line(shape)}")
    return "\n".join(lines)

def resolve_called_import_shapes(
    source_code: str,
    resolved: list["ResolvedImport"],
) -> list[ImportShape]:
    invoked = _invoked_imports(source_code, resolved)
    out: list[ImportShape] = []
    seen: set[str] = set()
    for item, call_style in invoked:
        if item.fqcn in seen:
            continue
        shape = _lookup_shape(item, call_style=call_style)
        if shape is None:
            continue
        seen.add(item.fqcn)
        out.append(shape)
        if len(out) >= _SHAPE_CAP:
            break
    return out

def parse_javap_public(text: str, *, simple_name: str = "") -> ImportShape | None:
    """Parse `javap -public` stdout into constructors vs static methods."""
    kind = "class"
    constructors: list[str] = []
    statics: list[str] = []
    fqcn = ""
    for raw in (text or "").splitlines():
        line = raw.strip()
        kind_m = _KIND_LINE.match(line)
        if kind_m:
            kind = kind_m.group("kind")
            parts = line.split()
            if parts:
                fqcn = parts[-1].rstrip("{").strip()
            continue
        static_m = _STATIC_LINE.match(line)
        if static_m:
            name = static_m.group("name")
            if name and name not in statics:
                statics.append(name)
            continue
        ctor_m = _CTOR_LINE.match(line)
        if not ctor_m:
            continue
        name = ctor_m.group("name")
        short = name.rsplit(".", 1)[-1]
        if simple_name and short != simple_name and name != simple_name:
            continue
        if short[:1].isupper() or "." in name:
            constructors.append(_short_params(ctor_m.group("params")))
    if not constructors and not statics and kind == "class" and not fqcn:
        return None
    seam, mock = _seam_and_mock(
        kind=kind,
        constructors=tuple(constructors),
        statics=tuple(statics),
        call_style="constructor" if constructors and not statics else (
            "static" if statics and not constructors else "constructor"
        ),
    )
    if constructors and statics:
        seam, mock = ("constructor", "mockkConstructor") if constructors else ("static", "mockkStatic")
    return ImportShape(
        fqcn=fqcn or simple_name,
        kind=kind,
        constructors=tuple(constructors),
        static_methods=tuple(statics),
        seam=seam,
        mock=mock,
    )

def _format_shape_line(shape: ImportShape) -> str:
    bits = [f"{shape.fqcn}:", f"kind={shape.kind}"]
    if shape.constructors:
        bits.append("constructors=" + ",".join(shape.constructors))
    if shape.static_methods:
        shown = ",".join(shape.static_methods[:6])
        bits.append(f"static={shown}")
    bits.append(f"seam={shape.seam}")
    bits.append(f"mock={shape.mock}")
    return " ".join(bits)

def _invoked_imports(
    source_code: str,
    resolved: list["ResolvedImport"],
) -> list[tuple["ResolvedImport", str]]:
    by_simple: dict[str, "ResolvedImport"] = {}
    for item in resolved:
        if item.fqcn.endswith(".*"):
            continue
        if any(item.fqcn == p or item.fqcn.startswith(p) for p in _SKIP_PREFIXES):
            continue
        simple = item.fqcn.rsplit(".", 1)[-1]
        if simple and simple not in by_simple:
            by_simple[simple] = item
    if not by_simple or not source_code:
        return []
    source_bytes = source_code.encode("utf-8")
    root = parse_kotlin_ast(source_code)
    hits: list[tuple["ResolvedImport", str]] = []
    seen: set[str] = set()
    for node in walk_ast(root):
        if node.type != "call_expression" or not node.children:
            continue
        func = node.children[0]
        # Chained Type(args).method(): classify the inner Type(args) call only.
        if any(child.type == "call_expression" for child in walk_ast(func)):
            continue
        identifiers = [
            ast_text(source_bytes, child)
            for child in walk_ast(func)
            if child.type == "identifier"
        ]
        if not identifiers:
            continue
        simple = identifiers[0]
        item = by_simple.get(simple)
        if item is None or item.fqcn in seen:
            continue
        seen.add(item.fqcn)
        call_style = "constructor" if len(identifiers) == 1 else "static"
        hits.append((item, call_style))
        if len(hits) >= _SHAPE_CAP:
            break
    return hits

def _lookup_shape(item: "ResolvedImport", *, call_style: str) -> ImportShape | None:
    from_doc = _shape_from_api_doc(item.fqcn, call_style=call_style)
    if from_doc is not None:
        return from_doc
    if item.kind == "file" and item.path:
        from_proj = _shape_from_project_file(item.path, item.fqcn, call_style=call_style)
        if from_proj is not None:
            return from_proj
    if _allow_javap(item.fqcn):
        from_javap = _shape_from_javap(item.fqcn, call_style=call_style)
        if from_javap is not None:
            return from_javap
    return None

def _shape_from_api_doc(fqcn: str, *, call_style: str) -> ImportShape | None:
    path = api_doc_path_for_fqcn(fqcn)
    if path is None:
        return None
    entry = _api_doc_class_entry(str(path), fqcn)
    if entry is None:
        return None
    kind = str(entry.get("kind") or "class").strip() or "class"
    constructors = tuple(
        _short_params(str(c))
        for c in (entry.get("constructors") or [])
        if str(c).strip()
    )
    statics: list[str] = []
    for raw in entry.get("high_value_methods") or []:
        text = str(raw).strip()
        if not text.lower().startswith("static "):
            continue
        name = _static_method_name(text)
        if name and name not in statics:
            statics.append(name)
    if kind == "object":
        seam, mock = "object", "mockkObject"
    else:
        seam, mock = _seam_and_mock(
            kind=kind,
            constructors=constructors,
            statics=tuple(statics),
            call_style=call_style,
        )
    if not constructors and not statics and kind not in {"object", "interface"}:
        if call_style == "constructor":
            seam, mock = "constructor", "mockkConstructor"
        elif call_style == "static":
            seam, mock = "static", "mockkStatic"
    return ImportShape(
        fqcn=fqcn,
        kind=kind,
        constructors=constructors,
        static_methods=tuple(statics),
        seam=seam,
        mock=mock,
    )

def _shape_from_project_file(path: str, fqcn: str, *, call_style: str) -> ImportShape | None:
    simple = fqcn.rsplit(".", 1)[-1]
    text = file_cache.read_text(path, default="")
    if not text:
        return None
    source_bytes = text.encode("utf-8")
    root = parse_kotlin_ast(text)
    for node in walk_ast(root):
        if node.type == "object_declaration" and declaration_name(source_bytes, node) == simple:
            return ImportShape(
                fqcn=fqcn,
                kind="object",
                seam="object",
                mock="mockkObject",
            )
        if node.type == "interface_declaration" and declaration_name(source_bytes, node) == simple:
            return ImportShape(
                fqcn=fqcn,
                kind="interface",
                seam="static" if call_style == "static" else "constructor",
                mock="mockkStatic" if call_style == "static" else "mockkConstructor",
            )
        if node.type != "class_declaration":
            continue
        if declaration_name(source_bytes, node) != simple:
            continue
        primary = direct_child(node, "primary_constructor")
        params = _primary_ctor_types(source_bytes, primary)
        ctors = (f"({', '.join(params)})",) if params else (("()",) if primary is not None else ())
        seam, mock = _seam_and_mock(
            kind="class",
            constructors=ctors,
            statics=(),
            call_style=call_style,
        )
        return ImportShape(
            fqcn=fqcn,
            kind="class",
            constructors=ctors,
            seam=seam,
            mock=mock,
        )
    return None

def _shape_from_javap(fqcn: str, *, call_style: str) -> ImportShape | None:
    jar = _library_jar_for_fqcn(fqcn)
    if not jar:
        return None
    text = _run_javap(jar, fqcn)
    if not text:
        return None
    parsed = parse_javap_public(text, simple_name=fqcn.rsplit(".", 1)[-1])
    if parsed is None:
        return None
    seam, mock = _seam_and_mock(
        kind=parsed.kind,
        constructors=parsed.constructors,
        statics=parsed.static_methods,
        call_style=call_style,
    )
    return ImportShape(
        fqcn=fqcn,
        kind=parsed.kind,
        constructors=parsed.constructors,
        static_methods=parsed.static_methods,
        seam=seam,
        mock=mock,
    )

def _seam_and_mock(
    *,
    kind: str,
    constructors: tuple[str, ...],
    statics: tuple[str, ...],
    call_style: str,
) -> tuple[str, str]:
    if kind == "object":
        return "object", "mockkObject"
    if call_style == "constructor":
        return "constructor", "mockkConstructor"
    if call_style == "static" or (statics and not constructors):
        return "static", "mockkStatic"
    if constructors:
        return "constructor", "mockkConstructor"
    return "static", "mockkStatic"

def _allow_javap(fqcn: str) -> bool:
    return not any(fqcn == p or fqcn.startswith(p) for p in _NO_JAVAP_PREFIXES)

def _primary_ctor_types(source_bytes: bytes, primary_constructor) -> list[str]:
    if primary_constructor is None:
        return []
    params_node = direct_child(primary_constructor, "function_value_parameters") or direct_child(
        primary_constructor, "class_parameters"
    )
    if params_node is None:
        for child in primary_constructor.children:
            if child.type in {"function_value_parameters", "class_parameters"}:
                params_node = child
                break
    if params_node is None:
        return []
    types: list[str] = []
    for param in direct_children(params_node, "parameter") + direct_children(
        params_node, "class_parameter"
    ):
        type_node = direct_child(param, "type")
        raw = ""
        if type_node is not None:
            raw = ast_text(source_bytes, type_node).removeprefix(":").strip()
        else:
            user_type = direct_child(param, "user_type")
            if user_type is not None:
                raw = ast_text(source_bytes, user_type).strip()
        if raw:
            types.append(raw.rsplit(".", 1)[-1].rstrip("?"))
    return types

def _short_params(params: str) -> str:
    inner = (params or "").strip()
    if inner.startswith("(") and inner.endswith(")"):
        inner = inner[1:-1]
    parts = []
    for piece in inner.split(",") if inner else []:
        token = piece.strip().split()[-1] if piece.strip() else ""
        token = token.rsplit(".", 1)[-1].rstrip(";")
        if token:
            parts.append(token)
    return f"({', '.join(parts)})" if parts else "()"

def _static_method_name(signature: str) -> str:
    text = signature.strip()
    if "(" in text:
        text = text.split("(", 1)[0]
    return text.split()[-1] if text.split() else ""

@lru_cache(maxsize=64)
def _api_doc_class_entry(path: str, fqcn: str) -> dict | None:
    payload = file_cache.read_json(path, default_factory=dict)
    if not isinstance(payload, dict) or not payload:
        return None
    simple = fqcn.rsplit(".", 1)[-1]
    fallback = None
    for key in ("api_index", "api"):
        items = payload.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("class") or item.get("qualified_name") or "").strip()
            if name == fqcn:
                return item
            if name.endswith(f".{simple}") or name == simple:
                fallback = item
    return fallback

@lru_cache(maxsize=32)
def _library_jar_for_fqcn(fqcn: str) -> str:
    path = api_doc_path_for_fqcn(fqcn)
    if path is None:
        return ""
    payload = file_cache.read_json(path, default_factory=dict)
    if not isinstance(payload, dict) or not payload:
        return ""
    coords = _maven_coords(payload)
    if not coords:
        return ""
    group, artifact, version = coords
    cache = Path(os.environ.get("GRADLE_USER_HOME") or (Path.home() / ".gradle"))
    root = cache / "caches" / "modules-2" / "files-2.1" / group / artifact / version
    if not root.is_dir():
        return ""
    jars = [
        p
        for p in root.rglob("*.jar")
        if p.is_file() and "-sources" not in p.name and "-javadoc" not in p.name
    ]
    if jars:
        return str(sorted(jars, key=lambda p: (len(p.parts), p.name))[0])
    aars = [p for p in root.rglob("*.aar") if p.is_file()]
    if not aars:
        return ""
    return _classes_jar_from_aar(str(sorted(aars)[0]))

def _maven_coords(payload: dict) -> tuple[str, str, str] | None:
    raw = payload.get("maven_coordinates")
    if isinstance(raw, list) and raw:
        raw = raw[0]
    if isinstance(raw, dict):
        group = str(raw.get("group_id") or raw.get("group") or "")
        artifact = str(raw.get("artifact_id") or raw.get("name") or "")
        version = str(raw.get("version") or "")
        if group and artifact and version:
            return group, artifact, version
        return None
    if isinstance(raw, str) and raw.count(":") >= 2:
        group, artifact, version = raw.split(":", 2)
        return group, artifact, version
    nested = payload.get("official_sources")
    if isinstance(nested, dict):
        return _maven_coords({"maven_coordinates": nested.get("maven_coordinates")})
    coords = payload.get("coordinates")
    if isinstance(coords, dict):
        return _maven_coords(coords)
    return None

def _classes_jar_from_aar(aar_path: str) -> str:
    try:
        with ZipFile(aar_path) as zf:
            if "classes.jar" not in zf.namelist():
                return ""
            dest_dir = Path(tempfile.gettempdir()) / "utg_aar_classes" / Path(aar_path).stem
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / "classes.jar"
            if not dest.is_file():
                dest.write_bytes(zf.read("classes.jar"))
            return str(dest)
    except OSError:
        return ""

@lru_cache(maxsize=64)
def _run_javap(jar: str, fqcn: str) -> str:
    try:
        proc = subprocess.run(
            ["javap", "-public", "-classpath", jar, fqcn],
            capture_output=True,
            text=True,
            timeout=_JAVAP_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout or ""

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from UnitTest_gen.core import io as file_cache
from UnitTest_gen.core.io import DEFAULT_SKIP_DIRS, walk_source_roots
from UnitTest_gen.kotlin.analysis import (
    declaration_name,
    direct_child,
    kotlin_package_name,
    kotlin_identifier_set,
    parse_kotlin_ast,
    ast_text,
)
from UnitTest_gen.kotlin.recipes import api_doc_path_for_fqcn
from UnitTest_gen.kotlin.project import find_project_root_for_path

_WILDCARD_FILE_CAP = 24
_DECL_TYPES = frozenset(
    {
        "class_declaration",
        "object_declaration",
        "interface_declaration",
        "function_declaration",
        "typealias_declaration",
    }
)
_EXTERNAL_PREFIXES = (
    "android.",
    "androidx.",
    "java.",
    "javax.",
    "kotlin.",
    "kotlinx.",
    "dagger.",
    "org.junit",
    "org.mockito",
    "io.mockk",
    "timber.",
    "org.json",
    "okhttp3.",
    "retrofit2.",
    "com.google.",
    "com.apollographql.",
    "net.openid.",
    "com.auth0.",
    "com.android.car.ui",
)

@dataclass(frozen=True)
class ResolvedImport:
    fqcn: str
    path: str  # absolute, or ""
    kind: str  # "file" | "external" | "unresolved"

def resolve_source_imports(
    source_path: str,
    source_code: str = "",
    *,
    project_root: str = "",
) -> list[ResolvedImport]:
    if not source_code:
        source_code = file_cache.read_text(source_path, default="")
    imports = _import_fqcns(source_code)
    root = project_root or find_project_root_for_path(source_path) or ""
    resolved: list[ResolvedImport] = []
    for fqcn in imports:
        resolved.extend(_resolve_one(fqcn, root))
    resolved = _append_same_package_references(resolved, source_code, root)
    return _append_apollo_harness(resolved, root)

def format_source_imports_block(
    resolved: list[ResolvedImport],
    source_code: str = "",
) -> str:
    if not resolved:
        return ""
    lines = ["SOURCE IMPORTS (absolute paths — Read these files; do not invent filenames):"]
    for item in resolved:
        if item.kind == "file" and item.path:
            lines.append(f"- {item.fqcn} -> {Path(item.path).resolve()}")
        elif item.kind == "external":
            if item.path:
                lines.append(
                    f"- {item.fqcn} -> SDK/library (not a project .kt). "
                    f"Declaration and other API data: {Path(item.path).resolve()}"
                )
            else:
                lines.append(
                    f"- {item.fqcn} -> SDK/library; no matching api_doc JSON "
                    "(do not invent a project file)"
                )
        else:
            lines.append(f"- {item.fqcn} -> unresolved (no project file found)")
    block = "\n".join(lines)
    if not source_code:
        return block
    from UnitTest_gen.kotlin.imports import format_called_import_shapes

    shapes = format_called_import_shapes(source_code, resolved)
    if not shapes:
        return block
    return block + "\n\n" + shapes

def insert_source_imports_block(
    sections: list[str],
    resolved: list[ResolvedImport],
    source_code: str = "",
) -> list[str]:
    """Append formatted import block — unused by plan/coder prompts (tools discover imports)."""
    block = format_source_imports_block(resolved, source_code=source_code)
    if not block:
        return sections
    return [*sections, block]

def _import_fqcns(source_code: str) -> tuple[str, ...]:
    source_bytes = (source_code or "").encode("utf-8")
    root = parse_kotlin_ast(source_code or "")
    out: list[str] = []
    seen: set[str] = set()
    for child in root.children:
        if child.type != "import":
            continue
        qualified = direct_child(child, "qualified_identifier")
        if qualified is None:
            continue
        fqcn = ast_text(source_bytes, qualified)
        if _import_is_wildcard(source_bytes, child) and not fqcn.endswith(".*"):
            fqcn = f"{fqcn}.*"
        if fqcn and fqcn not in seen:
            seen.add(fqcn)
            out.append(fqcn)
    return tuple(out)

def _import_is_wildcard(source_bytes: bytes, import_node) -> bool:
    for child in import_node.children:
        if child.type in {"*", "wildcard_import"}:
            return True
        if ast_text(source_bytes, child) == "*":
            return True
    return False

def _is_external(fqcn: str) -> bool:
    for prefix in _EXTERNAL_PREFIXES:
        if prefix.endswith("."):
            if fqcn.startswith(prefix):
                return True
        elif fqcn == prefix or fqcn.startswith(prefix + "."):
            return True
    return False

def _resolve_one(fqcn: str, project_root: str) -> list[ResolvedImport]:
    if _is_external(fqcn):
        doc = api_doc_path_for_fqcn(fqcn)
        return [
            ResolvedImport(
                fqcn=fqcn,
                path=str(doc) if doc else "",
                kind="external",
            )
        ]
    if not project_root:
        return [ResolvedImport(fqcn=fqcn, path="", kind="unresolved")]
    if fqcn.endswith(".*"):
        return _resolve_wildcard(fqcn, project_root)
    package, name = _split_fqcn(fqcn)
    if not name:
        return [ResolvedImport(fqcn=fqcn, path="", kind="unresolved")]
    conventional = _conventional_file(project_root, package, name)
    if conventional:
        return [ResolvedImport(fqcn=fqcn, path=conventional, kind="file")]
    ast_hit = _package_dir_match(project_root, package, name)
    if ast_hit:
        return [ResolvedImport(fqcn=fqcn, path=ast_hit, kind="file")]
    doc = api_doc_path_for_fqcn(fqcn)
    if doc:
        return [ResolvedImport(fqcn=fqcn, path=str(doc), kind="external")]
    return [ResolvedImport(fqcn=fqcn, path="", kind="unresolved")]

def _split_fqcn(fqcn: str) -> tuple[str, str]:
    parts = [p for p in fqcn.split(".") if p]
    if not parts:
        return "", ""
    return ".".join(parts[:-1]), parts[-1]

def _package_rel(package: str) -> Path:
    return Path(*package.split(".")) if package else Path()

def _conventional_file(project_root: str, package: str, name: str) -> str:
    rel = _package_rel(package) / f"{name}.kt"
    for src_root in _main_source_roots(project_root):
        candidate = Path(src_root) / rel
        if candidate.is_file():
            return str(candidate.resolve())
    return ""

def _resolve_wildcard(fqcn: str, project_root: str) -> list[ResolvedImport]:
    package = fqcn[:-2] if fqcn.endswith(".*") else fqcn
    rel = _package_rel(package)
    files: list[str] = []
    for src_root in _main_source_roots(project_root):
        pkg_dir = Path(src_root) / rel
        if not pkg_dir.is_dir():
            continue
        for path in sorted(pkg_dir.glob("*.kt")):
            if not path.is_file():
                continue
            files.append(str(path.resolve()))
            if len(files) >= _WILDCARD_FILE_CAP:
                break
        if len(files) >= _WILDCARD_FILE_CAP:
            break
    if not files:
        return [ResolvedImport(fqcn=fqcn, path="", kind="unresolved")]
    return [ResolvedImport(fqcn=fqcn, path=path, kind="file") for path in files]

def _package_dir_match(project_root: str, package: str, name: str) -> str:
    rel = _package_rel(package)
    hits: list[str] = []
    for src_root in _main_source_roots(project_root):
        pkg_dir = Path(src_root) / rel
        if not pkg_dir.is_dir():
            continue
        for decl_package, decl_name, path in _package_dir_scan(str(pkg_dir.resolve())):
            if decl_package == package and decl_name == name:
                hits.append(path)
    return sorted(hits)[0] if hits else ""

@lru_cache(maxsize=32)
def _main_source_roots(project_root: str) -> tuple[str, ...]:
    roots: set[str] = set()
    for file_path in walk_source_roots(
        project_root,
        roots=("src/main",),
        skip_dirs=DEFAULT_SKIP_DIRS,
        extensions=(".kt", ".java"),
    ):
        path = Path(file_path)
        for parent in [path.parent, *path.parents]:
            if (
                parent.name in {"java", "kotlin"}
                and parent.parent.name == "main"
                and parent.parent.parent.name == "src"
            ):
                roots.add(str(parent))
                break
    return tuple(sorted(roots))

@lru_cache(maxsize=256)
def _package_dir_scan(dir_path: str) -> tuple[tuple[str, str, str], ...]:
    entries: list[tuple[str, str, str]] = []
    directory = Path(dir_path)
    for path in sorted(directory.glob("*.kt")):
        if not path.is_file():
            continue
        text = file_cache.read_text(path, default="")
        if not text:
            continue
        package = kotlin_package_name(text)
        abs_path = str(path.resolve())
        for name in _top_level_decl_names(text):
            entries.append((package, name, abs_path))
    return tuple(entries)

def _top_level_decl_names(source_code: str) -> frozenset[str]:
    source_bytes = source_code.encode("utf8")
    root = parse_kotlin_ast(source_code)
    names = set()
    for child in root.children:
        if child.type not in _DECL_TYPES:
            continue
        name = declaration_name(source_bytes, child)
        if name:
            names.add(name)
    return frozenset(names)

_APOLLO_HARNESS_FILES = (
    (
        ),
    (
         ),
)

def apollo_harness_reference_paths(project_root: str) -> list[tuple[str, str]]:
    """Return (fqcn, absolute path) for Apollo test harness files when present."""
    if not project_root:
        return []
    rows: list[tuple[str, str]] = []
    for fqcn, rel_path in _APOLLO_HARNESS_FILES:
        abs_path = str((Path(project_root) / rel_path).resolve())
        if Path(abs_path).is_file():
            rows.append((fqcn, abs_path))
    return rows

def _append_apollo_harness(
    resolved: list[ResolvedImport], project_root: str
) -> list[ResolvedImport]:
    if not project_root or not any(
        r.fqcn.startswith("com.apollographql.") for r in resolved
    ):
        return resolved
    out = list(resolved)
    for fqcn, rel_path in _APOLLO_HARNESS_FILES:
        abs_path = str((Path(project_root) / rel_path).resolve())
        if Path(abs_path).is_file():
            out.append(ResolvedImport(fqcn=fqcn, path=abs_path, kind="file"))
    return out

_SAME_PACKAGE_CLASS_NAME_RE = re.compile(r"^[A-Z][A-Za-z0-9_]*$")

def _append_same_package_references(
    resolved: list[ResolvedImport],
    source_code: str,
    project_root: str,
) -> list[ResolvedImport]:
    """Also resolve same-package type refs that appear without import statements."""
    if not project_root or not source_code:
        return resolved
    pkg = kotlin_package_name(source_code)
    if not pkg:
        return resolved

    existing_fqcn = {r.fqcn for r in resolved}
    candidates = kotlin_identifier_set(source_code)
    for ident in candidates:
        if not _SAME_PACKAGE_CLASS_NAME_RE.match(ident):
            continue
        fqcn = f"{pkg}.{ident}"
        if fqcn in existing_fqcn:
            continue
        conventional = _conventional_file(project_root, pkg, ident)
        if conventional:
            resolved.append(
                ResolvedImport(
                    fqcn=fqcn,
                    path=conventional,
                    kind="file",
                )
            )
            existing_fqcn.add(fqcn)
    return resolved
