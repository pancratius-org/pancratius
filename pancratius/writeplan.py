# import-pure: no filesystem mutation
"""The `WritePlan` — import's safety boundary (docs/import-pipeline.md).

A `WritePlan` is an immutable value: a declared target scope, an ordered set of
scope-relative write operations, upstream diagnostics, and the overwrite policy. It
holds no absolute target paths. Import code *produces* a plan; only
`pancratius/writer.py` applies it.

PURE: no filesystem access. The path-boundary and overwrite rules live in
`validate`, which takes its filesystem questions as INJECTED predicates — the
writer supplies fs-backed ones, tests supply stubs — so the boundary is
unit-testable with no real tree. The marker above keys PAN018-writer-only-mutation.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

Severity = Literal["fatal", "warning", "info"]
OpKind = Literal["ensure_dir", "write_text", "copy", "transform_asset"]
Role = Literal["canonical_source", "source_artifact", "imported_asset", "cover", "sidecar"]

# Runtime guard for `validate`: a value outside `OpKind` (no `delete` — a normal
# import never deletes) is rejected, not just type-flagged. `transform_asset` lands
# one real target file, so the path/scope/escape checks treat it like `copy`.
_ALLOWED_OP_KINDS: frozenset[str] = frozenset(
    {"ensure_dir", "write_text", "copy", "transform_asset"}
)


@dataclass(frozen=True)
class PlannedAsset:
    """A deduped body image the conversion references; the writer copies it.

    `pancratius.ir.lower.assign_assets` rewrites the Markdown ref to `./images/<hash>.<ext>`
    (hash = content hash of the source bytes) and emits one of these per image; the
    importer turns it into a `transform_asset` op.
    """

    rel_within: str  # bundle-relative POSIX path, e.g. "images/<hash>.<ext>"
    source: Path  # the extracted pandoc media file to copy from
    is_raster: bool  # raster (cap-eligible) vs vector/animated (copied verbatim)


@dataclass(frozen=True)
class AssetTransform:
    """A declared transform the writer applies to an asset as it copies it.

    `copy` is byte-for-byte; `cap_raster` down-scales a raster whose longest edge
    exceeds `max_long_edge` (LANCZOS, same format, optional re-encode `quality`),
    falling back to a byte copy when the image is small or unreadable. The
    parameters live in the plan so the transform is a contract, not hidden writer
    behavior.
    """

    kind: Literal["copy", "cap_raster"]
    max_long_edge: int | None = None
    quality: int | None = None


@dataclass(frozen=True)
class Diagnostic:
    """A first-class diagnostic: severity + stable code + human message."""

    severity: Severity
    code: str
    message: str


@dataclass(frozen=True)
class WriteOp:
    """One planned operation against a scope-relative path.

    `rel_path` is relative to `target_root` and must stay under `target_scope`
    (plan enforces the lexical part; writer enforces it against the real
    filesystem). `content` carries inline text for `write_text`; `source` names the
    input file for `copy`/`transform_asset` (never a write target); `transform`
    declares how a `transform_asset` reshapes its source.
    """

    kind: OpKind
    rel_path: PurePosixPath
    role: Role
    reason: str
    content: str | None = None
    source: Path | None = None
    transform: AssetTransform | None = None


@dataclass(frozen=True)
class WritePlan:
    """An immutable description of one import's writes — never applied here."""

    target_root: Path
    target_scope: PurePosixPath
    operations: tuple[WriteOp, ...]
    diagnostics: tuple[Diagnostic, ...]
    replace: bool
    # The original input document this plan imports — recorded as provenance in
    # the out-of-bundle manifest. Never a write target; optional so unit tests can
    # build plans without a real source file.
    source_document: Path | None = None


def _is_lexically_unsafe(rel_path: PurePosixPath) -> bool:
    """True if `rel_path` is absolute, rooted at `/`, or contains `..` (lexical)."""
    if rel_path.is_absolute() or str(rel_path).startswith("/"):
        return True
    return ".." in rel_path.parts


def _within_scope(rel_path: PurePosixPath, scope: PurePosixPath) -> bool:
    """True if `rel_path` is the scope itself or lies under it (lexical)."""
    return rel_path == scope or scope in rel_path.parents


def validate(
    plan: WritePlan,
    *,
    target_exists: Callable[[PurePosixPath], bool],
    escapes_scope: Callable[[PurePosixPath], bool],
) -> tuple[Diagnostic, ...]:
    """Return one FATAL diagnostic per rule violation in `plan` (empty == clean).

    Pure: all filesystem questions arrive as injected predicates —
    `target_exists(rel)` (does the target file already exist) and
    `escapes_scope(rel)` (does the resolved real path leave the scope, e.g. via a
    symlink). The writer supplies fs-backed predicates; tests supply stubs.
    """
    diags: list[Diagnostic] = []
    for op in plan.operations:
        rel = op.rel_path

        # Reject an unusable op before the path checks would mislead.
        if op.kind not in _ALLOWED_OP_KINDS:
            diags.append(
                Diagnostic(
                    "fatal",
                    "writeplan.bad-op-kind",
                    f"operation kind {op.kind!r} is not one of {sorted(_ALLOWED_OP_KINDS)} "
                    f"(target {rel}); deletes and unknown ops are forbidden in a normal import.",
                )
            )
            continue

        if _is_lexically_unsafe(rel):
            diags.append(
                Diagnostic(
                    "fatal",
                    "writeplan.unsafe-path",
                    f"operation path {str(rel)!r} is absolute, rooted at '/', or "
                    "contains '..'; operation paths must be relative and inside the scope.",
                )
            )
            continue

        if not _within_scope(rel, plan.target_scope):
            diags.append(
                Diagnostic(
                    "fatal",
                    "writeplan.out-of-scope",
                    f"operation path {str(rel)!r} is not under the declared target "
                    f"scope {str(plan.target_scope)!r}.",
                )
            )
            continue

        # `_within_scope` admits `rel == scope` for the bundle's `ensure_dir`; a
        # write/copy at exactly the scope path would drop a file where the dir belongs.
        if op.kind != "ensure_dir" and rel == plan.target_scope:
            diags.append(
                Diagnostic(
                    "fatal",
                    "writeplan.scope-is-dir",
                    f"operation path {str(rel)!r} is the bundle directory itself; a "
                    "write/copy must target a file inside the scope, not the scope dir.",
                )
            )
            continue

        if escapes_scope(rel):
            diags.append(
                Diagnostic(
                    "fatal",
                    "writeplan.scope-escape",
                    f"operation path {str(rel)!r} resolves outside the target scope "
                    f"{str(plan.target_scope)!r} (symlink or real-path escape).",
                )
            )
            continue

        if (
            op.kind != "ensure_dir"
            and op.role == "canonical_source"
            and not plan.replace
            and target_exists(rel)
        ):
            diags.append(
                Diagnostic(
                    "fatal",
                    "writeplan.overwrite-refused",
                    f"refusing to overwrite existing converter-owned file {str(rel)!r} "
                    "without --replace; re-import is additive (add a new language) by "
                    "default and overwriting an existing <lang>.md is opt-in.",
                )
            )

    return tuple(diags)


def has_fatal(diags: Iterable[Diagnostic]) -> bool:
    """True if any diagnostic is fatal."""
    return any(d.severity == "fatal" for d in diags)
