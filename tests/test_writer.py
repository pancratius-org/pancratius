"""Integration tests for the writer — the only fs mutator (scripts/lib/writer.py).

These run on a real tmp_path tree (the writer's whole job is fs mutation, so it
cannot be tested purely). They prove: ops apply with correct content; the
manifest lands under the injected imports dir; author-added neighbours survive;
dry-run writes nothing; a fatal plan / existing-canonical-without-replace is
refused; and a real symlink escape in a tmp tree is refused.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
import json
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from lib.writeplan import Diagnostic, WriteOp, WritePlan  # noqa: E402
from lib import writer  # noqa: E402

SCOPE = PurePosixPath("books/99-probe")


def _source(tmp: Path, name: str, content: str) -> Path:
    src = tmp / "src" / name
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(content, encoding="utf-8")
    return src


def _plan(
    root: Path,
    ops: tuple[WriteOp, ...],
    *,
    replace: bool = False,
    diagnostics: tuple[Diagnostic, ...] = (),
    source_document: Path | None = None,
) -> WritePlan:
    return WritePlan(
        target_root=root,
        target_scope=SCOPE,
        operations=ops,
        diagnostics=diagnostics,
        replace=replace,
        source_document=source_document,
    )


def _bundle_ops(tmp: Path) -> tuple[WriteOp, ...]:
    md = _source(tmp, "ru.md", "---\nkind: book\n---\n\nbody\n")
    img = _source(tmp, "a.png", "PNGDATA")
    return (
        WriteOp(kind="ensure_dir", rel_path=SCOPE, role="canonical_source", reason="dir"),
        WriteOp(kind="write_text", rel_path=SCOPE / "ru.md", role="canonical_source", reason="md", content=md.read_text()),
        WriteOp(kind="copy", rel_path=SCOPE / "images" / "a.png", role="imported_asset", reason="img", source=img),
    )


def test_apply_creates_files_with_content(tmp_path: Path) -> None:
    root = tmp_path / "content"
    plan = _plan(root, _bundle_ops(tmp_path))
    report = writer.apply(plan, dry_run=False, imports_dir=tmp_path / "imports")

    assert (root / "books/99-probe/ru.md").read_text() == "---\nkind: book\n---\n\nbody\n"
    assert (root / "books/99-probe/images/a.png").read_text() == "PNGDATA"
    assert set(report.created) == {SCOPE / "ru.md", SCOPE / "images" / "a.png"}
    assert report.refused == ()


def test_manifest_written_to_injected_imports_dir(tmp_path: Path) -> None:
    root = tmp_path / "content"
    imports = tmp_path / "imports"
    source = _source(tmp_path, "input.docx", "DOCXBYTES")
    plan = _plan(root, _bundle_ops(tmp_path), source_document=source)
    report = writer.apply(plan, dry_run=False, imports_dir=imports)

    # Filename is derived from the FULL scope (no books/poetry collision on a
    # shared work number).
    assert report.manifest_path == imports / "books-99-probe.json"
    assert report.manifest_path is not None
    manifest = json.loads(report.manifest_path.read_text())
    assert manifest["target_scope"] == "books/99-probe"
    assert "generated_at" in manifest
    assert {op["rel_path"] for op in manifest["operations"]} >= {
        "books/99-probe/ru.md",
        "books/99-probe/images/a.png",
    }
    # Provenance records the ORIGINAL source document + its sha256 (not the
    # scratch copies, which are deleted after the run).
    assert manifest["source_document"] == str(source)
    assert manifest["source_sha256"]


def test_author_neighbour_is_left_untouched(tmp_path: Path) -> None:
    root = tmp_path / "content"
    bundle = root / "books/99-probe"
    bundle.mkdir(parents=True)
    neighbour = bundle / "AUTHOR-NOTES.md"
    neighbour.write_text("hand-written, not in any plan", encoding="utf-8")

    plan = _plan(root, _bundle_ops(tmp_path))
    writer.apply(plan, dry_run=False, imports_dir=tmp_path / "imports")

    # The plan never names the neighbour, so it is preserved by construction.
    assert neighbour.read_text() == "hand-written, not in any plan"


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    root = tmp_path / "content"
    imports = tmp_path / "imports"
    plan = _plan(root, _bundle_ops(tmp_path))
    report = writer.apply(plan, dry_run=True, imports_dir=imports)

    assert not (root / "books").exists()
    assert not imports.exists()
    assert report.manifest_path is None
    # Dry-run still reports WHAT would be created.
    assert set(report.created) == {SCOPE / "ru.md", SCOPE / "images" / "a.png"}


def test_fatal_diagnostic_in_plan_refuses_all(tmp_path: Path) -> None:
    root = tmp_path / "content"
    plan = _plan(
        root,
        _bundle_ops(tmp_path),
        diagnostics=(Diagnostic("fatal", "test.boom", "upstream said no"),),
    )
    report = writer.apply(plan, dry_run=False, imports_dir=tmp_path / "imports")

    assert not (root / "books").exists()
    assert report.created == ()
    assert set(report.refused) == {SCOPE / "ru.md", SCOPE / "images" / "a.png"}
    assert any(d.code == "test.boom" for d in report.diagnostics)


def test_existing_canonical_without_replace_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "content"
    bundle = root / "books/99-probe"
    bundle.mkdir(parents=True)
    (bundle / "ru.md").write_text("ORIGINAL committed body", encoding="utf-8")

    plan = _plan(root, _bundle_ops(tmp_path), replace=False)
    report = writer.apply(plan, dry_run=False, imports_dir=tmp_path / "imports")

    assert SCOPE / "ru.md" in report.refused
    # The existing canonical file is untouched.
    assert (bundle / "ru.md").read_text() == "ORIGINAL committed body"


def test_existing_canonical_with_replace_is_applied(tmp_path: Path) -> None:
    root = tmp_path / "content"
    bundle = root / "books/99-probe"
    bundle.mkdir(parents=True)
    (bundle / "ru.md").write_text("ORIGINAL committed body", encoding="utf-8")

    plan = _plan(root, _bundle_ops(tmp_path), replace=True)
    report = writer.apply(plan, dry_run=False, imports_dir=tmp_path / "imports")

    assert report.refused == ()
    assert (bundle / "ru.md").read_text() == "---\nkind: book\n---\n\nbody\n"


def test_symlink_escape_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "content"
    bundle = root / "books/99-probe"
    bundle.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    # `images` is a symlink pointing OUTSIDE the scope; a copy into it would escape.
    (bundle / "images").symlink_to(outside, target_is_directory=True)

    img = _source(tmp_path, "a.png", "PNGDATA")
    plan = _plan(
        root,
        (
            WriteOp(kind="ensure_dir", rel_path=SCOPE, role="canonical_source", reason="dir"),
            WriteOp(
                kind="copy",
                rel_path=SCOPE / "images" / "a.png",
                role="imported_asset",
                reason="img",
                source=img,
            ),
        ),
    )
    report = writer.apply(plan, dry_run=False, imports_dir=tmp_path / "imports")

    assert SCOPE / "images" / "a.png" in report.refused
    assert not (outside / "a.png").exists()
    assert any(d.code == "writeplan.scope-escape" for d in report.diagnostics)
