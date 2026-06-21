"""PAN023 — Python domain type-shape heuristic.

This is review pressure, not a hard gate. It flags new places where repo domain
vocabulary collapses back to raw primitives: locale/kind/format parameters typed
as ``str``, source registries typed as open primitive containers, primitive tuple
return contracts, and dataclasses that accumulate many optional fields.

Existing findings live in ``data/type-domain-baseline.json``. The baseline
keeps the heuristic useful for agents by reporting only newly introduced smells.
"""

from __future__ import annotations

import ast
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import cast


def _audit_root() -> Path:
    env = os.environ.get("PANCRATIUS_AUDIT_ROOT")
    return Path(env).resolve() if env else Path(__file__).resolve().parents[2]


ROOT = _audit_root()
BASELINE_REL = Path("data/type-domain-baseline.json")
SUPPRESSION = "pan-audit: allow domain-type-shape"

SKIP_DIR_NAMES = {
    ".astro",
    ".cache",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".ty_cache",
    ".venv",
    "__pycache__",
    "dist",
    "legacy",
    "node_modules",
}
SKIP_REL_PREFIXES = (
    "audit/fixtures/",
    "pancratius/cover/",
)
SKIP_REL_SUFFIXES = (
    "/client.py",
    "/schema.py",
    "pancratius/docx_adapter.py",
    "pancratius/conceptosphere.py",
    "pancratius/conceptosphere_embed.py",
)

DOMAIN_PARAM_EXPECTED: dict[str, str] = {
    "default_lang": "Locale",
    "lang": "Locale",
    "locale": "Locale",
    "kind": "RoutedKind or CorpusWorkKind",
    "format": "a named format type",
}
REGISTRY_EXPECTED: dict[str, str] = {
    "SEGMENT_OF": "dict[RoutedKind, RoutedSegment]",
    "KIND_OF_SEGMENT": "dict[RoutedSegment, RoutedKind]",
    "LOCALES": "tuple[Locale, ...]",
    "DEFAULT_LOCALE": "Locale",
    "CORPUS_WORK_KINDS": "tuple[CorpusWorkKind, ...]",
}
PRIMITIVE_TYPES = frozenset({"str", "int", "float", "bool"})
OPTIONAL_CLUSTER_SIZE = 3


@dataclass(frozen=True)
class Candidate:
    rel: str
    line: int
    kind: str
    subject: str
    annotation: str
    expected: str
    detail: str

    @property
    def fingerprint(self) -> str:
        return (
            f"py:{self.kind}:{self.rel}:{self.subject}:"
            f"{self.annotation}->{self.expected}"
        )


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _is_skipped(path: Path) -> bool:
    rel = _rel(path)
    if any(part in SKIP_DIR_NAMES for part in path.relative_to(ROOT).parts):
        return True
    return rel.startswith(SKIP_REL_PREFIXES) or rel.endswith(SKIP_REL_SUFFIXES)


def _python_files() -> list[Path]:
    roots = [ROOT / "pancratius", ROOT / "audit"]
    files: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if path.is_file() and not _is_skipped(path):
                files.append(path)
    return sorted(files)


def _annotation_text(source: str, node: ast.AST | None) -> str:
    if node is None:
        return "<missing>"
    segment = ast.get_source_segment(source, node)
    if segment is not None:
        return " ".join(segment.split())
    return ast.unparse(node)


def _is_none(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _name_of(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _subscript_name(node: ast.AST) -> str | None:
    return _name_of(node.value) if isinstance(node, ast.Subscript) else None


def _union_members(node: ast.AST) -> list[ast.AST] | None:
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left = _union_members(node.left) or [node.left]
        right = _union_members(node.right) or [node.right]
        return [*left, *right]
    if isinstance(node, ast.Subscript) and _subscript_name(node) in {"Optional", "Union"}:
        value = node.slice
        if isinstance(value, ast.Tuple):
            return list(value.elts)
        return [value]
    return None


def _contains_str(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "str"
    members = _union_members(node)
    if members is not None:
        return any(_contains_str(member) for member in members)
    return False


def _is_optional(node: ast.AST) -> bool:
    members = _union_members(node)
    return members is not None and any(_is_none(member) for member in members)


def _is_primitive(node: ast.AST) -> bool:
    return isinstance(node, ast.Name) and node.id in PRIMITIVE_TYPES


def _tuple_element_nodes(node: ast.Subscript) -> list[ast.AST]:
    if isinstance(node.slice, ast.Tuple):
        return list(node.slice.elts)
    return [node.slice]


def _is_primitive_tuple(node: ast.AST) -> bool:
    if not isinstance(node, ast.Subscript) or _subscript_name(node) != "tuple":
        return False
    elements = _tuple_element_nodes(node)
    if any(isinstance(element, ast.Constant) and element.value is Ellipsis for element in elements):
        return False
    fixed = [element for element in elements if not isinstance(element, ast.Constant)]
    return bool(fixed) and all(_is_primitive(element) for element in fixed)


def _contains_primitive_tuple(node: ast.AST) -> bool:
    if _is_primitive_tuple(node):
        return True
    members = _union_members(node)
    if members is not None:
        return any(not _is_none(member) and _contains_primitive_tuple(member) for member in members)
    if isinstance(node, ast.Subscript):
        return _contains_primitive_tuple(node.slice)
    if isinstance(node, ast.Tuple):
        return any(_contains_primitive_tuple(element) for element in node.elts)
    return False


def _is_open_registry_annotation(name: str, annotation: ast.AST) -> bool:
    expected = REGISTRY_EXPECTED.get(name)
    if expected is None:
        return False
    if name in {"DEFAULT_LOCALE"}:
        return isinstance(annotation, ast.Name) and annotation.id == "str"
    if name in {"LOCALES", "CORPUS_WORK_KINDS"}:
        return (
            isinstance(annotation, ast.Subscript)
            and _subscript_name(annotation) == "tuple"
            and _contains_str(annotation.slice)
        )
    return (
        isinstance(annotation, ast.Subscript)
        and _subscript_name(annotation) == "dict"
        and _contains_str(annotation.slice)
    )


def _has_dataclass_decorator(node: ast.ClassDef) -> bool:
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if _name_of(target) == "dataclass":
            return True
    return False


def _suppressed(lines: list[str], line: int) -> bool:
    indices = [line - 1, line - 2]
    return any(0 <= idx < len(lines) and SUPPRESSION in lines[idx] for idx in indices)


class Scanner(ast.NodeVisitor):
    def __init__(self, path: Path, source: str) -> None:
        self.path = path
        self.rel = _rel(path)
        self.source = source
        self.lines = source.splitlines()
        self.class_stack: list[str] = []
        self.findings: list[Candidate] = []

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if self.class_stack:
            return
        if not isinstance(node.target, ast.Name):
            return
        name = node.target.id
        if not _is_open_registry_annotation(name, node.annotation):
            return
        self._add(
            line=node.lineno,
            kind="registry-open-type",
            subject=name,
            annotation=_annotation_text(self.source, node.annotation),
            expected=REGISTRY_EXPECTED[name],
            detail=f"{name} is a closed registry but is annotated with an open primitive type",
        )

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if _has_dataclass_decorator(node):
            self._scan_dataclass_fields(node)
            self._scan_optional_cluster(node)
        self.class_stack.append(node.name)
        self.generic_visit(node)
        self.class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._scan_function(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._scan_function(node)
        self.generic_visit(node)

    def _scan_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qualname = ".".join([*self.class_stack, node.name])
        for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
            if arg.arg in {"self", "cls"} or arg.annotation is None:
                continue
            expected = self._expected_domain_param(arg.arg)
            if expected is None or not _contains_str(arg.annotation):
                continue
            self._add(
                line=arg.lineno,
                kind="domain-api-primitive",
                subject=f"{qualname}({arg.arg})",
                annotation=_annotation_text(self.source, arg.annotation),
                expected=expected,
                detail=f"parameter {arg.arg!r} names repo domain vocabulary but is typed as raw str",
            )
        if node.returns is not None and _contains_primitive_tuple(node.returns):
            self._add(
                line=node.lineno,
                kind="primitive-tuple-return",
                subject=f"{qualname} return",
                annotation=_annotation_text(self.source, node.returns),
                expected="a named result/dataclass/NamedTuple when the shape crosses the helper boundary",
                detail="return annotation exposes a primitive tuple contract",
            )

    def _expected_domain_param(self, name: str) -> str | None:
        if name == "kind" and self.rel.startswith("audit/"):
            return None
        return DOMAIN_PARAM_EXPECTED.get(name)

    def _expected_domain_field(self, name: str) -> str | None:
        if name == "kind" and self.rel.startswith("audit/"):
            return None
        return DOMAIN_PARAM_EXPECTED.get(name)

    def _scan_dataclass_fields(self, node: ast.ClassDef) -> None:
        for stmt in node.body:
            if not isinstance(stmt, ast.AnnAssign):
                continue
            if not isinstance(stmt.target, ast.Name):
                continue
            field_name = stmt.target.id
            expected = self._expected_domain_field(field_name)
            if expected is None or not _contains_str(stmt.annotation):
                continue
            self._add(
                line=stmt.lineno,
                kind="domain-field-primitive",
                subject=f"{node.name}.{field_name}",
                annotation=_annotation_text(self.source, stmt.annotation),
                expected=expected,
                detail=(
                    f"dataclass field {field_name!r} names repo domain vocabulary "
                    "but is typed as raw str"
                ),
            )

    def _scan_optional_cluster(self, node: ast.ClassDef) -> None:
        optional_fields: list[str] = []
        for stmt in node.body:
            if not isinstance(stmt, ast.AnnAssign):
                continue
            if not isinstance(stmt.target, ast.Name):
                continue
            if _is_optional(stmt.annotation):
                optional_fields.append(stmt.target.id)
        if len(optional_fields) < OPTIONAL_CLUSTER_SIZE:
            return
        self._add(
            line=node.lineno,
            kind="optionality-cluster",
            subject=node.name,
            annotation=", ".join(optional_fields),
            expected="an explicit state/request shape or documented boundary DTO",
            detail=(
                f"dataclass has {len(optional_fields)} optional fields "
                f"({', '.join(optional_fields)})"
            ),
        )

    def _add(
        self,
        *,
        line: int,
        kind: str,
        subject: str,
        annotation: str,
        expected: str,
        detail: str,
    ) -> None:
        if _suppressed(self.lines, line):
            return
        self.findings.append(
            Candidate(
                rel=self.rel,
                line=line,
                kind=kind,
                subject=subject,
                annotation=annotation,
                expected=expected,
                detail=detail,
            )
        )


def _load_baseline() -> frozenset[str]:
    path = ROOT / BASELINE_REL
    if not path.exists():
        return frozenset()
    data = cast("object", json.loads(path.read_text(encoding="utf-8")))
    if not isinstance(data, dict):
        return frozenset()
    raw = cast("dict[str, object]", data).get("python")
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(item for item in raw if isinstance(item, str))


def _scan_file(path: Path) -> list[Candidate]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    scanner = Scanner(path, source)
    scanner.visit(tree)
    return scanner.findings


def _all_candidates() -> list[Candidate]:
    findings: list[Candidate] = []
    for path in _python_files():
        try:
            findings.extend(_scan_file(path))
        except SyntaxError as exc:
            rel = _rel(path)
            findings.append(
                Candidate(
                    rel=rel,
                    line=exc.lineno or 1,
                    kind="parse-error",
                    subject=rel,
                    annotation=exc.msg,
                    expected="valid Python AST",
                    detail=f"could not parse {rel}: {exc.msg}",
                )
            )
    return sorted(findings, key=lambda item: item.fingerprint)


def _render(candidate: Candidate) -> str:
    return (
        f"{candidate.rel}:{candidate.line}: {candidate.kind}: {candidate.subject}: "
        f"{candidate.detail}; "
        f"saw `{candidate.annotation}`, expected {candidate.expected}"
    )


def _dump_baseline(candidates: list[Candidate]) -> None:
    for fingerprint in sorted(candidate.fingerprint for candidate in candidates):
        print(fingerprint)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    candidates = _all_candidates()
    if "--dump-baseline" in args:
        _dump_baseline(candidates)
        return 0

    accepted = _load_baseline()
    current = frozenset(candidate.fingerprint for candidate in candidates)
    fresh = [candidate for candidate in candidates if candidate.fingerprint not in accepted]
    stale = sorted(accepted - current)
    if fresh or stale:
        if fresh:
            print("FAIL: new Python domain type-shape findings")
        for candidate in fresh[:80]:
            print(f"  {_render(candidate)}")
        if len(fresh) > 80:
            print(f"  ... {len(fresh) - 80} more")
        if stale:
            if fresh:
                print()
            print("FAIL: stale Python domain type-shape baseline entries")
            for fingerprint in stale[:80]:
                print(f"  {fingerprint}")
            if len(stale) > 80:
                print(f"  ... {len(stale) - 80} more")
        print(
            "\nUse named domain/value types inside library and audit code. "
            "Raw strings and sparse DTOs are acceptable at external boundaries; "
            f"document true exceptions with `{SUPPRESSION} -- reason` or baseline "
            "existing debt deliberately."
        )
        return 1

    print(
        "PASS: no new Python domain type-shape findings "
        f"({len(candidates)} accepted by baseline)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
