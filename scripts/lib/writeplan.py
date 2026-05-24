# import-pure: no filesystem mutation
"""The `WritePlan` — import's safety boundary (docs/import-pipeline.md).

A `WritePlan` is an immutable value: a declared target scope, an ordered set of
scope-relative write operations, the diagnostics gathered upstream, and the
overwrite policy. It holds no absolute target paths and performs no filesystem
access. Import code *produces* a plan; only `scripts/lib/writer.py` applies it.

This module is PURE: it imports nothing that touches the filesystem and contains
zero write/copy/mkdir/open-for-write calls. The path-boundary and overwrite
rules live here as a single module function (`validate`) with INJECTED
predicates, so the writer supplies fs-backed ones and tests supply stubs — the
boundary stays unit-testable with no real filesystem. The Phase-2 audit
(PAN018-writer-only-mutation) keys on the marker comment at the top of this file.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

Severity = Literal["fatal", "warning", "info"]
OpKind = Literal["ensure_dir", "write_text", "copy", "transform_asset"]
Role = Literal["canonical_source", "source_artifact", "imported_asset", "cover", "sidecar"]

# No `delete` kind exists — a normal import never deletes (docs/import-pipeline.md
# "A normal import never contains a delete"). Forbidden by construction.
# `transform_asset` is `copy` with a declared transform (e.g. a raster cap) the
# writer applies — it still lands one real target file, so the path/scope/escape
# checks below treat it exactly like `copy`/`write_text`.
ALLOWED_OP_KINDS: frozenset[str] = frozenset(
    {"ensure_dir", "write_text", "copy", "transform_asset"}
)


@dataclass(frozen=True)
class PlannedAsset:
    """A body image the conversion REFERENCES but does NOT copy.

    The converter (`ir_lower.assign_assets`) is pure w.r.t. the filesystem: it
    rewrites the Markdown ref to `./images/<hash>.<ext>` (hash = content hash of
    the original source bytes) and returns one of these per deduped image. The
    importer turns each into a `transform_asset` WriteOp and the writer is the
    only thing that copies it into the bundle (docs/import-pipeline.md "Import
    produces a WritePlan; only the writer applies it").
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
    behavior — and so capping is the ONLY place PIL runs (docs/import-pipeline.md
    "Image And Asset Policy").
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

    `rel_path` is always relative to the plan's `target_root` and must stay under
    `target_scope` (the writer enforces this against the real filesystem; the
    plan enforces the lexical part). `content` carries inline text for
    `write_text`; `source` names the input file for `copy`/`transform_asset` (an
    input path, never a write target); `transform` declares how a
    `transform_asset` op reshapes its source on the way to the target.
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
    """True if a relative path is absolute, rooted, or contains `..` — a pure,
    lexical check (no filesystem). `PurePosixPath.is_absolute()` covers a leading
    `/`; `..` anywhere in the parts is parent traversal."""
    text = str(rel_path)
    if rel_path.is_absolute() or text.startswith("/"):
        return True
    return ".." in rel_path.parts


def _within_scope(rel_path: PurePosixPath, scope: PurePosixPath) -> bool:
    """True if `rel_path` is the scope itself or lies under it — pure/lexical."""
    if rel_path == scope:
        return True
    return scope in rel_path.parents


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

        if op.kind not in ALLOWED_OP_KINDS:
            diags.append(
                Diagnostic(
                    "fatal",
                    "writeplan.bad-op-kind",
                    f"operation kind {op.kind!r} is not one of {sorted(ALLOWED_OP_KINDS)} "
                    f"(target {rel}); deletes and unknown ops are forbidden in a normal import.",
                )
            )
            # The op is unusable; the remaining path checks would be misleading.
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
            # Don't run scope/escape predicates on a path we already rejected.
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

        # A content op may target a file INSIDE the scope, never the scope dir
        # itself — `_within_scope` admits `rel == scope` for the `ensure_dir` of
        # the bundle, so a write/copy at exactly the scope path must be rejected
        # (it would drop a file where the bundle directory belongs).
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
