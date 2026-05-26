"""The writer — the only filesystem mutator for import (docs/import-pipeline.md).

A `WritePlan` is a pure value (`pancratius/writeplan.py`); this module applies it.
It validates the plan's paths, refuses if any diagnostic is fatal, then applies
operations through temporary paths and atomic replace. It never pre-deletes and
only touches paths named in the plan, so author-added neighbours survive.

It carries no `# import-pure` marker: it is the designated mutator PAN018 permits
to write. It is GENERAL — it emits no import provenance (the per-import manifest is
the importer's concern), so a non-import mutation like `project page add` reuses it.
"""

from __future__ import annotations

import io
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, assert_never

from pancratius import svg_sanitize
from pancratius.writeplan import AssetTransform, Diagnostic, WriteOp, WritePlan, has_fatal, validate

# How an applied op landed against an existing target.
type _Bucket = Literal["created", "changed", "skipped"]

# Raster formats the cap applies to (vector/animated are copied untouched) and the
# JPEG/WEBP re-encode quality. Mirrors the import-time cap so a capped image is
# byte-identical; cross-checked by the goldens (book62 has oversized rasters).
_CAP_RASTER_FORMATS: frozenset[str] = frozenset({"PNG", "JPEG", "WEBP"})
_CAP_QUALITY: dict[str, int] = {"JPEG": 82, "WEBP": 80}


@dataclass(frozen=True)
class WriteReport:
    """The outcome of applying (or refusing, or dry-running) a `WritePlan`."""

    created: tuple[PurePosixPath, ...]
    changed: tuple[PurePosixPath, ...]
    skipped: tuple[PurePosixPath, ...]
    refused: tuple[PurePosixPath, ...]
    diagnostics: tuple[Diagnostic, ...]


def _target_exists(plan: WritePlan, rel: PurePosixPath) -> bool:
    """fs-backed `target_exists` predicate: does target_root/rel exist?"""
    return (plan.target_root / rel).exists()


def _escapes_scope(plan: WritePlan, rel: PurePosixPath) -> bool:
    """fs-backed `escapes_scope`: does the real resolved path of target_root/rel
    leave the scope? `resolve()` follows symlinks (catching a symlinked component
    pointing outside the bundle); the scope root is resolved too so a legitimate
    in-scope path behind a symlinked root (macOS /tmp -> /private/tmp) is not flagged.
    """
    scope_root = (plan.target_root / plan.target_scope).resolve()
    resolved = (plan.target_root / rel).resolve()
    if resolved == scope_root:
        return False
    return scope_root not in resolved.parents


def _read_source_bytes(op: WriteOp) -> bytes:
    if op.source is None:
        raise ValueError(f"copy op for {op.rel_path} has no source")
    return op.source.read_bytes()


def _atomic_write(dest: Path, payload: bytes) -> None:
    """Write `payload` to `dest` via a unique temp sibling + os.replace.

    Symlink-TOCTOU safe: `mkstemp` opens an unpredictable name with O_CREAT|O_EXCL,
    so the temp cannot pre-exist as an attacker-seeded symlink, and its fd already
    points at the fresh real file — no second open, no symlink to follow. os.replace
    is the atomic swap (never pre-deletes `dest`). On any failure the temp is removed.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.name}.", suffix=".import-tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
        os.replace(tmp, dest)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _capped_raster_bytes(op: WriteOp, transform: AssetTransform) -> tuple[bytes, Diagnostic | None]:
    """Return the bytes a `cap_raster` transform lands, plus an optional warning.

    Down-scales (LANCZOS) only a readable raster in `_CAP_RASTER_FORMATS` whose
    longest edge exceeds `max_long_edge`; everything else returns the original
    bytes. A per-image failure is non-fatal: original bytes + a warning, so one bad
    image never fails the import. The only place PIL runs.
    """
    if op.source is None:
        raise ValueError(f"transform_asset op for {op.rel_path} has no source")
    original = op.source.read_bytes()
    if transform.max_long_edge is None:
        return original, None
    try:
        # PIL is imported lazily so the writer (and its pure tests) don't pay for it
        # unless a cap actually runs.
        from PIL import Image

        with Image.open(op.source) as img:
            img.load()
            width, height = img.size
            fmt = img.format
            if fmt not in _CAP_RASTER_FORMATS or max(width, height) <= transform.max_long_edge:
                return original, None  # vector/animated/unknown or already small
            resized = img.copy()
        resized.thumbnail((transform.max_long_edge, transform.max_long_edge), Image.LANCZOS)
        save_kwargs: dict[str, int] = {}
        quality = transform.quality if transform.quality is not None else _CAP_QUALITY.get(fmt)
        if fmt in _CAP_QUALITY and quality is not None:
            save_kwargs["quality"] = quality
        buf = io.BytesIO()
        resized.save(buf, format=fmt, **save_kwargs)
        return buf.getvalue(), None
    except (ImportError, OSError) as exc:
        return original, Diagnostic(
            "warning",
            "writer.cap-failed",
            f"could not cap raster {op.rel_path} ({type(exc).__name__}: {exc}); copied original bytes.",
        )


# SVG sanitization is scoped to body-image assets (`imported_asset`), the
# DOCX-extracted SVGs the threat model names. Covers are excluded: the committed
# author cover SVGs legitimately use `<foreignObject>` for the styled title, and
# they arrive on a curated trust path, not as DOCX body content.
_SVG_SANITIZE_ROLES: frozenset[str] = frozenset({"imported_asset"})


def _maybe_sanitize_svg(op: WriteOp, payload: bytes) -> bytes:
    """Sanitize SVG XSS gadgets at the body-image asset-copy boundary.

    A body SVG is served raw same-origin, so a script/on*/javascript:/foreignObject/
    external-href gadget in it is stored XSS; the writer is the sole asset-copy gate.
    A clean SVG returns byte-for-byte (real body SVGs untouched).
    """
    if op.role in _SVG_SANITIZE_ROLES and svg_sanitize.is_svg_name(op.rel_path.name):
        return svg_sanitize.sanitize_svg(payload)
    return payload


def _transform_payload(op: WriteOp) -> tuple[bytes, Diagnostic | None]:
    """The bytes a `transform_asset` op lands, plus any cap warning. SVG payloads are
    sanitized whichever transform produced them (a non-raster cap falls back to
    original bytes, still sanitized)."""
    transform = op.transform or AssetTransform(kind="copy")
    match transform.kind:
        case "cap_raster":
            payload, warning = _capped_raster_bytes(op, transform)
            return _maybe_sanitize_svg(op, payload), warning
        case "copy":
            return _maybe_sanitize_svg(op, _read_source_bytes(op)), None
    assert_never(transform.kind)


def _op_payload(op: WriteOp) -> tuple[bytes, Diagnostic | None]:
    """The bytes a content op lands, plus any transform warning. SVG asset payloads
    are sanitized at this boundary. Partial over `OpKind`: `ensure_dir` carries no
    payload and is dispatched separately in `apply`."""
    match op.kind:
        case "write_text":
            if op.content is None:
                raise ValueError(f"write_text op for {op.rel_path} has no content")
            return op.content.encode("utf-8"), None
        case "copy":
            return _maybe_sanitize_svg(op, _read_source_bytes(op)), None
        case "transform_asset":
            return _transform_payload(op)
        case _:
            raise ValueError(f"{op.kind} op has no payload")


def _preflight_sources(plan: WritePlan) -> tuple[Diagnostic, ...]:
    """One FATAL diagnostic per `copy`/`transform_asset` op whose source is
    missing/None/unreadable, checked BEFORE any mutation so a later unreadable
    source refuses the whole plan instead of leaving a half-written bundle.

    Checks readability (exists + a 1-byte read), not decodability — an undecodable
    cap_raster source is a per-image non-fatal fallback in `_capped_raster_bytes`.
    `write_text`/`ensure_dir` carry no source.
    """
    diags: list[Diagnostic] = []
    for op in plan.operations:
        if op.kind not in {"copy", "transform_asset"}:
            continue
        if op.source is None:
            diags.append(
                Diagnostic(
                    "fatal",
                    "writer.missing-source",
                    f"{op.kind} op for {op.rel_path} has no source path; refusing the "
                    "whole plan rather than writing a partial bundle.",
                )
            )
            continue
        try:
            with op.source.open("rb") as fh:
                fh.read(1)
        except OSError as exc:
            diags.append(
                Diagnostic(
                    "fatal",
                    "writer.unreadable-source",
                    f"{op.kind} op for {op.rel_path} cannot read its source "
                    f"{op.source} ({exc}); refusing the whole plan — nothing written "
                    "(a later unreadable source must not leave a partial bundle).",
                )
            )
    return tuple(diags)


def _classify(dest: Path, payload: bytes) -> _Bucket:
    """`created` if dest is absent, `skipped` if its bytes already match, `changed`
    otherwise — so re-importing an identical bundle reports skips, not rewrites."""
    if not dest.exists():
        return "created"
    try:
        return "skipped" if dest.read_bytes() == payload else "changed"
    except OSError:
        return "changed"


def apply(
    plan: WritePlan,
    *,
    dry_run: bool,
) -> WriteReport:
    """Validate and (unless dry-run) apply `plan` — the only fs-mutating call.

    Combines `validate` (fs-backed predicates) and source preflight with the plan's
    own diagnostics. On any fatal diagnostic, writes nothing and reports the
    would-be writes as `refused`. On dry-run, reports the same buckets but touches
    nothing. Otherwise applies ops in order through atomic replace.
    """
    validation = validate(
        plan,
        target_exists=lambda rel: _target_exists(plan, rel),
        escapes_scope=lambda rel: _escapes_scope(plan, rel),
    )
    # Preflight sources before any write so a later unreadable source refuses the
    # whole plan rather than leaving a partial bundle (same fatal path as `validate`).
    source_check = _preflight_sources(plan)
    diagnostics = (*plan.diagnostics, *validation, *source_check)

    if has_fatal(diagnostics):
        refused = tuple(op.rel_path for op in plan.operations if op.kind != "ensure_dir")
        return WriteReport(
            created=(),
            changed=(),
            skipped=(),
            refused=refused,
            diagnostics=diagnostics,
        )

    created: list[PurePosixPath] = []
    changed: list[PurePosixPath] = []
    skipped: list[PurePosixPath] = []
    transform_diags: list[Diagnostic] = []

    for op in plan.operations:
        dest = plan.target_root / op.rel_path
        if op.kind == "ensure_dir":
            if not dry_run:
                dest.mkdir(parents=True, exist_ok=True)
            continue

        payload, warning = _op_payload(op)
        if warning is not None:
            transform_diags.append(warning)
        match _classify(dest, payload):
            case "created":
                if not dry_run:
                    _atomic_write(dest, payload)
                created.append(op.rel_path)
            case "changed":
                if not dry_run:
                    _atomic_write(dest, payload)
                changed.append(op.rel_path)
            case "skipped":
                skipped.append(op.rel_path)

    return WriteReport(
        created=tuple(created),
        changed=tuple(changed),
        skipped=tuple(skipped),
        refused=(),
        diagnostics=(*diagnostics, *transform_diags),
    )
