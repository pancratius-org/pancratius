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
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from lib.writeplan import Diagnostic, WriteOp, WritePlan, has_fatal, validate

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
    """Write `payload` to `dest` via a temp sibling + os.replace (atomic-ish).

    Never pre-deletes `dest`; os.replace overwrites in one step if it exists.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / f".{dest.name}.import-tmp"
    tmp.write_bytes(payload)
    os.replace(tmp, dest)


def _op_payload(op: WriteOp) -> bytes:
    """The bytes a write_text/copy op will land. (ensure_dir has none.)"""
    if op.kind == "write_text":
        if op.content is None:
            raise ValueError(f"write_text op for {op.rel_path} has no content")
        return op.content.encode("utf-8")
    if op.kind == "copy":
        return _read_source_bytes(op)
    raise ValueError(f"{op.kind} op has no payload")


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
    diagnostics = (*plan.diagnostics, *validation)

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

    for op in plan.operations:
        dest = plan.target_root / op.rel_path
        if op.kind == "ensure_dir":
            if not dry_run:
                dest.mkdir(parents=True, exist_ok=True)
            continue

        payload = _op_payload(op)
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
        diagnostics=diagnostics,
        manifest_path=manifest_path,
    )
