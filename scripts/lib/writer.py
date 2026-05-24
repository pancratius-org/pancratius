"""The writer — the ONLY filesystem mutator for import (docs/import-pipeline.md).

A `WritePlan` is a pure value (`scripts/lib/writeplan.py`); this module is the
single component permitted to change `src/content`. It validates the plan's
paths, refuses to apply if any diagnostic is fatal, then applies operations
through temporary paths and atomic replace. It never pre-deletes directories,
never rmtrees, and only ever touches paths named in the plan — so author-added
neighbours are preserved by construction.

This module deliberately does NOT carry the `# import-pure` marker: it is the
designated mutator, the one place the PAN018-writer-only-mutation audit allows
filesystem mutation to live. Volatile provenance (timestamps, source hashes) is
written OUTSIDE the bundle, under `data/imports/`, so committed bundles stay
byte-identical on re-import.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from lib import svg_sanitize
from lib.writeplan import AssetTransform, Diagnostic, WriteOp, WritePlan, has_fatal, validate

# Raster formats the cap applies to (vector/animated are copied untouched) and the
# JPEG/WEBP re-encode quality, mirroring the import-time cap exactly so a capped
# image is byte-identical to the pre-boundary behaviour. Cross-checked by the
# golden net (book62 has oversized rasters).
_CAP_RASTER_FORMATS: frozenset[str] = frozenset({"PNG", "JPEG", "WEBP"})
_CAP_QUALITY: dict[str, int] = {"JPEG": 82, "WEBP": 80}

# Provenance lives outside the committed bundle (docs/import-pipeline.md
# "Idempotency"): per-import manifest under data/imports/<work-key>.json, never
# committed. Resolved relative to the repo root (scripts/lib/ -> repo root).
_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_IMPORTS_DIR = _REPO_ROOT / "data" / "imports"


@dataclass(frozen=True)
class WriteReport:
    """The outcome of applying (or refusing, or dry-running) a `WritePlan`."""

    created: tuple[PurePosixPath, ...]
    changed: tuple[PurePosixPath, ...]
    skipped: tuple[PurePosixPath, ...]
    refused: tuple[PurePosixPath, ...]
    diagnostics: tuple[Diagnostic, ...]
    manifest_path: Path | None


def _target_exists(plan: WritePlan, rel: PurePosixPath) -> bool:
    """fs-backed `target_exists` predicate: does target_root/rel exist?"""
    return (plan.target_root / rel).exists()


def _escapes_scope(plan: WritePlan, rel: PurePosixPath) -> bool:
    """fs-backed `escapes_scope` predicate: does the REAL resolved path of
    target_root/rel leave target_root/target_scope? `Path.resolve()` follows
    symlinks, so this catches a symlinked component pointing outside the bundle.
    The scope root is resolved too (it may itself sit behind a symlink, e.g. a
    macOS /tmp -> /private/tmp), so a legitimate in-scope path is not flagged.
    """
    scope_root = (plan.target_root / plan.target_scope).resolve()
    resolved = (plan.target_root / rel).resolve()
    if resolved == scope_root:
        return False
    return scope_root not in resolved.parents


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_source_bytes(op: WriteOp) -> bytes:
    if op.source is None:
        raise ValueError(f"copy op for {op.rel_path} has no source")
    return op.source.read_bytes()


def _atomic_write(dest: Path, payload: bytes) -> None:
    """Write `payload` to `dest` via a UNIQUE temp sibling + os.replace (atomic-ish).

    Never pre-deletes `dest`; os.replace overwrites in one step if it exists.

    The temp is created with `tempfile.mkstemp` in `dest.parent`: a unique,
    UNPREDICTABLE name opened with `O_CREAT|O_EXCL` (and `O_NOFOLLOW` where the
    platform offers it). A previous version used a DETERMINISTIC `.<name>.import-tmp`
    written via `Path.write_bytes`, which FOLLOWS a symlink — a pre-seeded symlink at
    that predictable path could redirect the write outside the bundle scope. The
    unpredictable, exclusively-created temp closes that hole: a pre-existing path
    (symlink or file) at the chosen name cannot exist (mkstemp would pick another),
    so the write always lands on a fresh real file before the atomic rename. On any
    failure the temp is removed so no residue is left.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    # `tempfile.mkstemp` opens with O_CREAT|O_EXCL and an unpredictable name, so the
    # chosen path cannot pre-exist as an attacker-seeded symlink (O_EXCL refuses an
    # existing path; the unpredictable name removes the ability to pre-seed one). Its
    # fd already points at the freshly-created real file — write through it directly,
    # no second open (and thus no symlink to follow). os.replace is the atomic swap.
    fd, tmp_name = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.name}.", suffix=".import-tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
        os.replace(tmp, dest)
    except BaseException:
        # Don't leak a temp file on any failure path.
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _capped_raster_bytes(op: WriteOp, transform: AssetTransform) -> tuple[bytes, Diagnostic | None]:
    """Return the bytes a `cap_raster` transform lands, plus an optional warning.

    Down-scales (LANCZOS) only when the source is a readable raster in
    `_CAP_RASTER_FORMATS` whose longest edge exceeds `max_long_edge`; otherwise
    returns the original bytes. A per-image failure is NON-FATAL: it falls back to
    the original bytes and surfaces a warning diagnostic, so one bad image never
    fails the import (docs/import-pipeline.md "capped image" is a warning, not
    fatal). This is the only place PIL runs.
    """
    if op.source is None:
        raise ValueError(f"transform_asset op for {op.rel_path} has no source")
    original = op.source.read_bytes()
    if transform.max_long_edge is None:
        return original, None
    try:
        # PIL is imported lazily so the writer (and its pure unit tests) don't pay
        # the import unless a raster cap actually runs.
        from PIL import Image

        with Image.open(op.source) as img:
            img.load()
            width, height = img.size
            fmt = img.format
            if fmt not in _CAP_RASTER_FORMATS or max(width, height) <= transform.max_long_edge:
                # Vector/animated/unknown formats, and already-small rasters, are
                # copied verbatim — the cap only ever down-scales oversized rasters.
                return original, None
            resized = img.copy()
        resized.thumbnail((transform.max_long_edge, transform.max_long_edge), Image.LANCZOS)
        save_kwargs: dict[str, Any] = {}
        quality = transform.quality if transform.quality is not None else _CAP_QUALITY.get(fmt)
        if fmt in _CAP_QUALITY and quality is not None:
            save_kwargs["quality"] = quality
        buf = io.BytesIO()
        resized.save(buf, format=fmt, **save_kwargs)
        return buf.getvalue(), None
    except Exception as exc:  # one bad image must not fail import
        return original, Diagnostic(
            "warning",
            "writer.cap-failed",
            f"could not cap raster {op.rel_path} ({exc}); copied original bytes.",
        )


# SVG sanitization is scoped to BODY-IMAGE assets (role `imported_asset`), the
# DOCX-extracted, content-hash-named SVGs the threat model names. COVERS (role
# `cover`) are DELIBERATELY excluded: the committed author cover SVGs legitimately
# use `<foreignObject>` to render the styled title (cover.en.svg), and stripping it
# would corrupt the published cover. Covers are admin-curated design assets on a
# different trust path (committed directly / passed by an explicit `--cover`), not
# DOCX body content — so sanitizing them risks corruption for no body-XSS gain.
# (Cross-checked: the 3 committed body SVGs are clean — the sanitizer is a no-op on
# them byte-for-byte; only the cover carries a foreignObject.)
_SVG_SANITIZE_ROLES: frozenset[str] = frozenset({"imported_asset"})


def _maybe_sanitize_svg(op: WriteOp, payload: bytes) -> bytes:
    """Sanitize SVG XSS gadgets at the body-image asset-copy boundary.

    A DOCX-extracted SVG body image is served RAW same-origin, so a `<script>`/
    `on*`/`javascript:`/`<foreignObject>`/external-href gadget in it is stored XSS.
    The writer is the sole component that copies assets in, so it is the gate: a
    body-image (`imported_asset`) op landing a `.svg` target is routed through
    `svg_sanitize.sanitize_svg`, which strips the gadgets and returns CLEAN input
    byte-for-byte (the real body SVGs are untouched). Rasters and covers are not
    touched (see `_SVG_SANITIZE_ROLES`)."""
    if op.role in _SVG_SANITIZE_ROLES and svg_sanitize.is_svg_name(op.rel_path.name):
        return svg_sanitize.sanitize_svg(payload)
    return payload


def _op_payload(op: WriteOp) -> tuple[bytes, Diagnostic | None]:
    """The bytes a write_text/copy/transform_asset op will land, plus an optional
    warning the transform produced. (ensure_dir has none.) SVG asset payloads are
    sanitized at this boundary (Fix D)."""
    if op.kind == "write_text":
        if op.content is None:
            raise ValueError(f"write_text op for {op.rel_path} has no content")
        return op.content.encode("utf-8"), None
    if op.kind == "copy":
        return _maybe_sanitize_svg(op, _read_source_bytes(op)), None
    if op.kind == "transform_asset":
        transform = op.transform or AssetTransform(kind="copy")
        if transform.kind == "cap_raster":
            # cap_raster only ever runs on rasters (PNG/JPEG/WEBP); an SVG asset is a
            # `copy` transform. If a non-raster slips into a cap op it falls back to
            # original bytes, which we still sanitize if it is an SVG.
            payload, warning = _capped_raster_bytes(op, transform)
            return _maybe_sanitize_svg(op, payload), warning
        return _maybe_sanitize_svg(op, _read_source_bytes(op)), None
    raise ValueError(f"{op.kind} op has no payload")


def _preflight_sources(plan: WritePlan) -> tuple[Diagnostic, ...]:
    """Prove EVERY `copy`/`transform_asset` op's source is a readable file, BEFORE
    the writer mutates anything — the WritePlan safety contract's preflight step.

    Returns one FATAL diagnostic per op whose source is missing/None/unreadable
    (empty == every source readable). The writer refuses the WHOLE plan on any
    fatal, so a later unreadable source can never leave a half-written, manifest-
    less bundle (the partial-apply bug). This checks READABILITY (the source exists
    and a 1-byte read succeeds), not decodability: a cap_raster source that exists
    but is an undecodable raster is a per-image NON-fatal fallback handled later in
    `_capped_raster_bytes`, not a plan-level refusal. `write_text`/`ensure_dir` ops
    carry no source and are not preflighted here (their payload is already in hand).
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


def _classify(dest: Path, payload: bytes) -> str:
    """`created` if dest is absent, `skipped` if its bytes already match,
    `changed` otherwise — so re-importing an identical bundle reports skips, not
    rewrites."""
    if not dest.exists():
        return "created"
    try:
        return "skipped" if dest.read_bytes() == payload else "changed"
    except OSError:
        return "changed"


def _write_manifest(plan: WritePlan, *, imports_dir: Path) -> Path:
    """Write the per-import provenance manifest under data/imports/.

    Volatile-only (generated_at, the ORIGINAL source document + its sha256, the
    target scope, the op list) — it never feeds the committed bundle, so re-import
    stays byte-identical. The recorded source is `plan.source_document` (the real
    input the user imported), not the staged scratch copies (which are deleted
    after the run). The filename is derived from the FULL scope so two kinds that
    share a work number (books/01-x vs poetry/01-x) cannot collide.
    """
    source = plan.source_document
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target_scope": str(plan.target_scope),
        "replace": plan.replace,
        "source_document": str(source) if source is not None else None,
        "source_sha256": _sha256(source) if source is not None and source.is_file() else None,
        "operations": [
            {
                "kind": op.kind,
                "rel_path": str(op.rel_path),
                "role": op.role,
                "reason": op.reason,
            }
            for op in plan.operations
        ],
    }
    imports_dir.mkdir(parents=True, exist_ok=True)
    manifest_name = str(plan.target_scope).replace("/", "-") + ".json"
    manifest_path = imports_dir / manifest_name
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest_path


def apply(
    plan: WritePlan,
    *,
    dry_run: bool,
    imports_dir: Path | None = None,
) -> WriteReport:
    """Validate and (unless dry-run) apply `plan` — the only fs-mutating call.

    Preflight: `validate` with fs-backed predicates, combined with the plan's own
    diagnostics. If ANY diagnostic is fatal, write NOTHING and return a report
    listing the would-be writes as `refused` plus the fatal diagnostics. On
    dry_run, report what WOULD happen and touch nothing. Otherwise apply ops in
    order through atomic replace, then write the volatile manifest.
    """
    imports_dir = imports_dir or DEFAULT_IMPORTS_DIR

    validation = validate(
        plan,
        target_exists=lambda rel: _target_exists(plan, rel),
        escapes_scope=lambda rel: _escapes_scope(plan, rel),
    )
    # Preflight every source as readable BEFORE any write, so a later unreadable
    # source refuses the whole plan instead of leaving a partial, manifest-less
    # bundle (the WritePlan safety contract). Folded into the same fatal-diagnostic
    # refusal path as `validate`.
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
            manifest_path=None,
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
        bucket = _classify(dest, payload)
        if not dry_run and bucket != "skipped":
            _atomic_write(dest, payload)
        {"created": created, "changed": changed, "skipped": skipped}[bucket].append(op.rel_path)

    manifest_path: Path | None = None
    if not dry_run:
        manifest_path = _write_manifest(plan, imports_dir=imports_dir)

    return WriteReport(
        created=tuple(created),
        changed=tuple(changed),
        skipped=tuple(skipped),
        refused=(),
        diagnostics=(*diagnostics, *transform_diags),
        manifest_path=manifest_path,
    )
