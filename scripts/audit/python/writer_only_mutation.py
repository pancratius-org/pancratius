#!/usr/bin/env -S uv run --quiet
"""PAN018 — writer-only-mutation guard (import safety boundary, PAN005 family).

Import's safety boundary (docs/import-pipeline.md): import code *produces* a
`WritePlan`; only the writer (`scripts/lib/writer.py`) mutates `src/content`.
Every other import-pipeline module that participates in producing the plan must
be PURE — no filesystem mutation. A module declares that contract by carrying the
marker comment on (or near) its first lines:

    # import-pure: no filesystem mutation

This audit DERIVES the scanned set from those markers (a self-extending source of
truth — later phases add the marker to the parser/normalizer/lowerer and they are
automatically covered) and asserts each marked module contains NO filesystem-
mutation call:

  - attribute calls: ``.write_text``, ``.write_bytes``, ``.mkdir``,
    ``shutil.copy*``, ``shutil.move``, ``shutil.rmtree``, ``os.replace``,
    ``os.remove``, ``os.rename``, ``os.unlink``, ``os.makedirs``, ``Path.touch``;
  - ``open(..., mode)`` where mode requests writing (``w``/``a``/``x`` or a
    binary/plus variant of those).

``writer.py`` deliberately does NOT carry the marker — it is the designated
mutator, so it is never scanned. A marked module containing a mutation call is the
exact boundary-leak this rule forbids.

Honours ``PANCRATIUS_AUDIT_ROOT`` (the harness points it at a fixture);
wrapped as PAN018 in scripts/audit/rules/imports.ts.
"""

from __future__ import annotations

import ast
import io
import os
import sys
import tokenize
from pathlib import Path

# The marker a module carries to declare it is in the pure import boundary.
PURITY_MARKER = "# import-pure: no filesystem mutation"

# Mutation attribute-call names (the method/function on the right of a dot).
MUTATING_ATTRS: frozenset[str] = frozenset(
    {
        "write_text",
        "write_bytes",
        "mkdir",
        "makedirs",
        "touch",
        "replace",  # os.replace / Path.replace
        "remove",
        "rename",  # os.rename / Path.rename
        "unlink",
        "rmtree",
        "move",
        "copy",
        "copy2",
        "copyfile",
        "copytree",
        "copyfileobj",
    }
)

# `open(path, mode)` is a write when the mode contains any of these.
WRITE_MODE_CHARS = frozenset("wax")


def _audit_root() -> Path:
    """The tree to scan: the fixture root when ``PANCRATIUS_AUDIT_ROOT`` is set,
    else the repo root (python -> audit -> scripts -> root)."""
    env = os.environ.get("PANCRATIUS_AUDIT_ROOT")
    return Path(env).resolve() if env else Path(__file__).resolve().parents[3]


ROOT = _audit_root()
# Mirror the TS walk's exclusions (scripts/audit/lib/repo.ts): never scan the
# harness's own fixtures (they intentionally contain a marked-module-with-a-write),
# nor disposable/vendor trees.
SKIP_DIR_NAMES = {"node_modules", "dist", "__pycache__", ".cache", ".astro", ".venv", "legacy"}
FIXTURES_REL = ("scripts", "audit", "fixtures")


def _is_skipped(path: Path) -> bool:
    parts = path.relative_to(ROOT).parts
    if any(p in SKIP_DIR_NAMES for p in parts):
        return True
    if parts[: len(FIXTURES_REL)] == FIXTURES_REL:
        return True
    # Skip every dot-directory (mirrors the TS dot-dir prune).
    return any(p.startswith(".") and p not in {".", ".."} for p in parts[:-1])


def _has_marker(source: str) -> bool:
    # The marker must be a real COMMENT token equal to the marker text — tokenize
    # so the same string appearing inside a docstring or a string literal (e.g.
    # this checker's own docstring) is NOT mistaken for the marker. The contract
    # is opt-in and explicit: an actual `#` comment, indentation-agnostic.
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        return any(tok.type == tokenize.COMMENT and tok.string.strip() == PURITY_MARKER for tok in tokens)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # A file we can't tokenize can't carry a valid marker; treat as unmarked
        # (a marked module that won't *parse* is caught separately in main()).
        return False


def _write_open(call: ast.Call) -> bool:
    """True if `call` is an open()/Path.open() requesting a write mode."""
    func = call.func
    is_open = (isinstance(func, ast.Name) and func.id == "open") or (
        isinstance(func, ast.Attribute) and func.attr == "open"
    )
    if not is_open:
        return False
    # mode is the 2nd positional arg or the `mode=` keyword.
    mode_node: ast.expr | None = None
    if len(call.args) >= 2:
        mode_node = call.args[1]
    for kw in call.keywords:
        if kw.arg == "mode":
            mode_node = kw.value
    if not (isinstance(mode_node, ast.Constant) and isinstance(mode_node.value, str)):
        return False
    return any(ch in WRITE_MODE_CHARS for ch in mode_node.value)


def _mutations(tree: ast.Module) -> list[tuple[int, str]]:
    """Return (lineno, name) for every mutation call found."""
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in MUTATING_ATTRS:
            hits.append((node.lineno, f".{func.attr}(...)"))
        elif _write_open(node):
            hits.append((node.lineno, "open(..., write-mode)"))
    return hits


def main() -> int:
    if not ROOT.exists():
        print(f"FAIL: audit root does not exist: {ROOT}", file=sys.stderr)
        return 1

    marked: list[Path] = []
    failures: list[str] = []

    for path in sorted(ROOT.rglob("*.py")):
        if not path.is_file() or _is_skipped(path):
            continue
        source = path.read_text(encoding="utf-8")
        if not _has_marker(source):
            continue
        marked.append(path)
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:  # a marked module that won't parse is a failure
            failures.append(f"{path.relative_to(ROOT)}: could not parse ({exc})")
            continue
        for lineno, name in _mutations(tree):
            rel = path.relative_to(ROOT)
            failures.append(
                f"{rel}:{lineno}: marked `import-pure` but calls a filesystem "
                f"mutation `{name}` — move it into the writer (scripts/lib/writer.py)."
            )

    if not marked:
        print(
            f"FAIL: no module carries the `{PURITY_MARKER}` marker under {ROOT}; "
            "the writer-only-mutation boundary has no anchored source of truth.",
            file=sys.stderr,
        )
        return 1

    if failures:
        print("FAIL: writer-only-mutation boundary violated", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        return 1

    print(
        f"PASS: {len(marked)} import-pure module(s) carry no filesystem mutation "
        f"({', '.join(str(p.relative_to(ROOT)) for p in marked)})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
