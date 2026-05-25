"""Integration tests for the writer — the only fs mutator (scripts/lib/writer.py).

These run on a real tmp_path tree (the writer's whole job is fs mutation, so it
cannot be tested purely). They prove: ops apply with correct content; author-added
neighbours survive; dry-run writes nothing; a fatal plan /
existing-canonical-without-replace is refused; and a real symlink escape in a tmp
tree is refused. (Provenance is the importer's concern now, not the writer's — its
relayer is covered in test_import_docx.)
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path, PurePosixPath

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from lib.writeplan import AssetTransform, Diagnostic, WriteOp, WritePlan  # noqa: E402
from lib import writer  # noqa: E402

SCOPE = PurePosixPath("books/99-probe")

_HAS_PIL = importlib.util.find_spec("PIL") is not None


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
    report = writer.apply(plan, dry_run=False)

    assert (root / "books/99-probe/ru.md").read_text() == "---\nkind: book\n---\n\nbody\n"
    assert (root / "books/99-probe/images/a.png").read_text() == "PNGDATA"
    assert set(report.created) == {SCOPE / "ru.md", SCOPE / "images" / "a.png"}
    assert report.refused == ()


def test_author_neighbour_is_left_untouched(tmp_path: Path) -> None:
    root = tmp_path / "content"
    bundle = root / "books/99-probe"
    bundle.mkdir(parents=True)
    neighbour = bundle / "AUTHOR-NOTES.md"
    neighbour.write_text("hand-written, not in any plan", encoding="utf-8")

    plan = _plan(root, _bundle_ops(tmp_path))
    writer.apply(plan, dry_run=False)

    # The plan never names the neighbour, so it is preserved by construction.
    assert neighbour.read_text() == "hand-written, not in any plan"


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    root = tmp_path / "content"
    plan = _plan(root, _bundle_ops(tmp_path))
    report = writer.apply(plan, dry_run=True)

    assert not (root / "books").exists()
    # Dry-run still reports WHAT would be created.
    assert set(report.created) == {SCOPE / "ru.md", SCOPE / "images" / "a.png"}


def test_fatal_diagnostic_in_plan_refuses_all(tmp_path: Path) -> None:
    root = tmp_path / "content"
    plan = _plan(
        root,
        _bundle_ops(tmp_path),
        diagnostics=(Diagnostic("fatal", "test.boom", "upstream said no"),),
    )
    report = writer.apply(plan, dry_run=False)

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
    report = writer.apply(plan, dry_run=False)

    assert SCOPE / "ru.md" in report.refused
    # The existing canonical file is untouched.
    assert (bundle / "ru.md").read_text() == "ORIGINAL committed body"


def test_existing_canonical_with_replace_is_applied(tmp_path: Path) -> None:
    root = tmp_path / "content"
    bundle = root / "books/99-probe"
    bundle.mkdir(parents=True)
    (bundle / "ru.md").write_text("ORIGINAL committed body", encoding="utf-8")

    plan = _plan(root, _bundle_ops(tmp_path), replace=True)
    report = writer.apply(plan, dry_run=False)

    assert report.refused == ()
    assert (bundle / "ru.md").read_text() == "---\nkind: book\n---\n\nbody\n"


def test_missing_copy_source_refuses_whole_plan_before_any_write(tmp_path: Path) -> None:
    # Bug 1 repro: a plan whose 1st op is a write_text (ru.md) and whose 2nd op is a
    # copy with a MISSING source must write NOTHING — the writer must preflight every
    # source as readable BEFORE writing any target, so the bundle is never left
    # partial. Before the fix, the writer wrote ru.md, then raised on the missing
    # source, leaving a half-written bundle and no manifest (the WritePlan safety
    # contract violation).
    root = tmp_path / "content"
    md = _source(tmp_path, "ru.md", "---\nkind: book\n---\n\nbody\n")
    missing = tmp_path / "src" / "does-not-exist.png"  # never created
    plan = _plan(
        root,
        (
            WriteOp(kind="ensure_dir", rel_path=SCOPE, role="canonical_source", reason="dir"),
            WriteOp(
                kind="write_text",
                rel_path=SCOPE / "ru.md",
                role="canonical_source",
                reason="md",
                content=md.read_text(),
            ),
            WriteOp(
                kind="copy",
                rel_path=SCOPE / "images" / "a.png",
                role="imported_asset",
                reason="img",
                source=missing,
            ),
        ),
    )
    report = writer.apply(plan, dry_run=False)

    # NOTHING was written — the first op's target must not exist afterward.
    assert not (root / "books/99-probe/ru.md").exists()
    assert not (root / "books").exists() or not any((root / "books").rglob("*.md"))
    assert report.created == ()
    assert report.changed == ()
    # The whole plan was refused with a surfaced fatal diagnostic.
    assert set(report.refused) == {SCOPE / "ru.md", SCOPE / "images" / "a.png"}
    assert any(d.severity == "fatal" and "source" in d.code for d in report.diagnostics)


def test_unreadable_transform_asset_source_refuses_whole_plan(tmp_path: Path) -> None:
    # Bug 1 (transform_asset variant): a cap_raster op whose source file is MISSING
    # is unreadable at preflight, so the whole plan is refused before any write. (A
    # source that EXISTS but is an undecodable raster is a per-image NON-fatal
    # fallback — that path is covered by test_cap_raster_corrupt_image_falls_back; a
    # MISSING source is a plan-level fatal, never a partial bundle.)
    root = tmp_path / "content"
    md = _source(tmp_path, "ru.md", "---\nkind: book\n---\n\nbody\n")
    missing = tmp_path / "src" / "gone.png"  # never created
    plan = _plan(
        root,
        (
            WriteOp(kind="ensure_dir", rel_path=SCOPE, role="canonical_source", reason="dir"),
            WriteOp(
                kind="write_text",
                rel_path=SCOPE / "ru.md",
                role="canonical_source",
                reason="md",
                content=md.read_text(),
            ),
            WriteOp(
                kind="transform_asset",
                rel_path=SCOPE / "images" / "big.png",
                role="imported_asset",
                reason="img",
                source=missing,
                transform=AssetTransform(kind="cap_raster", max_long_edge=1600),
            ),
        ),
    )
    report = writer.apply(plan, dry_run=False)

    assert not (root / "books/99-probe/ru.md").exists()
    assert report.created == ()
    assert SCOPE / "ru.md" in report.refused
    assert any(d.severity == "fatal" and "source" in d.code for d in report.diagnostics)


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
    report = writer.apply(plan, dry_run=False)

    assert SCOPE / "images" / "a.png" in report.refused
    assert not (outside / "a.png").exists()
    assert any(d.code == "writeplan.scope-escape" for d in report.diagnostics)


def test_preseeded_symlink_at_temp_path_does_not_redirect_write(tmp_path: Path) -> None:
    # Fix E: `_atomic_write` previously used a DETERMINISTIC temp sibling
    # `.<name>.import-tmp` and `Path.write_bytes`, which FOLLOWS a symlink. A
    # pre-seeded symlink at that predictable temp path could redirect the write to an
    # arbitrary out-of-scope file. The hardened writer uses a unique, unpredictable
    # temp (mkstemp) and never follows a pre-existing symlink — the destination gets
    # the real bytes and the out-of-scope target is untouched.
    dest_dir = tmp_path / "bundle"
    dest_dir.mkdir()
    dest = dest_dir / "ru.md"

    outside = tmp_path / "victim.txt"
    outside.write_text("ORIGINAL", encoding="utf-8")

    # Pre-seed the OLD deterministic temp path with a symlink to the victim file.
    legacy_tmp = dest_dir / f".{dest.name}.import-tmp"
    legacy_tmp.symlink_to(outside)

    writer._atomic_write(dest, b"NEWCONTENT")

    # The destination has the real new bytes (the write succeeded into scope).
    assert dest.read_bytes() == b"NEWCONTENT"
    # The out-of-scope victim was NOT overwritten through the pre-seeded symlink.
    assert outside.read_text(encoding="utf-8") == "ORIGINAL"


def test_atomic_write_does_not_leave_temp_files(tmp_path: Path) -> None:
    # The unique temp is renamed into place; no `.import-tmp`/`tmp*` residue is left.
    dest_dir = tmp_path / "bundle"
    dest_dir.mkdir()
    dest = dest_dir / "ru.md"
    writer._atomic_write(dest, b"X")
    leftovers = [p.name for p in dest_dir.iterdir() if p.name != "ru.md"]
    assert leftovers == [], f"no temp residue expected, found {leftovers}"


# --- transform_asset (the cap_raster transform — the only place PIL runs). These
# need a real raster, so they are skipped where pillow is absent. ---

pytestmark_pil = pytest.mark.skipif(not _HAS_PIL, reason="pillow required for cap_raster")


def _make_raster(path: Path, size: tuple[int, int], fmt: str = "PNG", quality: int | None = None) -> None:
    from PIL import Image

    img = Image.new("RGB", size)
    # Deterministic-ish pixels so a re-encode is meaningful (not a flat image).
    for x in range(0, size[0], 8):
        for y in range(0, size[1], 8):
            img.putpixel((x, y), ((x * 7) % 256, (y * 5) % 256, ((x + y) * 3) % 256))
    path.parent.mkdir(parents=True, exist_ok=True)
    save_kwargs = {"quality": quality} if quality is not None else {}
    img.save(path, format=fmt, **save_kwargs)


def _cap_op(source: Path, rel: str = "images/a.png") -> WriteOp:
    return WriteOp(
        kind="transform_asset",
        rel_path=SCOPE / PurePosixPath(rel),
        role="imported_asset",
        reason="img",
        source=source,
        transform=AssetTransform(kind="cap_raster", max_long_edge=1600),
    )


@pytestmark_pil
def test_cap_raster_resizes_oversized_image(tmp_path: Path) -> None:
    from PIL import Image

    root = tmp_path / "content"
    src = tmp_path / "src" / "big.png"
    _make_raster(src, (2400, 1200), fmt="PNG")

    plan = _plan(
        root,
        (
            WriteOp(kind="ensure_dir", rel_path=SCOPE, role="canonical_source", reason="dir"),
            _cap_op(src),
        ),
    )
    report = writer.apply(plan, dry_run=False)

    dest = root / "books/99-probe/images/a.png"
    assert SCOPE / "images" / "a.png" in report.created
    with Image.open(dest) as out:
        assert max(out.size) == 1600  # longest edge capped, aspect preserved
        assert out.size == (1600, 800)
    # The capped output is NOT the original bytes.
    assert dest.read_bytes() != src.read_bytes()
    assert not any(d.code == "writer.cap-failed" for d in report.diagnostics)


@pytestmark_pil
def test_cap_raster_under_cap_is_byte_copied(tmp_path: Path) -> None:
    root = tmp_path / "content"
    src = tmp_path / "src" / "small.png"
    _make_raster(src, (800, 600), fmt="PNG")

    plan = _plan(
        root,
        (
            WriteOp(kind="ensure_dir", rel_path=SCOPE, role="canonical_source", reason="dir"),
            _cap_op(src, rel="images/small.png"),
        ),
    )
    writer.apply(plan, dry_run=False)

    dest = root / "books/99-probe/images/small.png"
    # An under-cap raster is copied verbatim — byte-identical to the source.
    assert dest.read_bytes() == src.read_bytes()


@pytestmark_pil
def test_copy_transform_is_byte_copied(tmp_path: Path) -> None:
    # A `copy` transform (vector/animated assets) is always byte-for-byte, even on
    # a large raster — it never invokes the cap.
    root = tmp_path / "content"
    src = tmp_path / "src" / "vector.svg"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="9000" height="9000"/></svg>')

    op = WriteOp(
        kind="transform_asset",
        rel_path=SCOPE / "images" / "v.svg",
        role="imported_asset",
        reason="img",
        source=src,
        transform=AssetTransform(kind="copy"),
    )
    plan = _plan(
        root,
        (WriteOp(kind="ensure_dir", rel_path=SCOPE, role="canonical_source", reason="dir"), op),
    )
    writer.apply(plan, dry_run=False)

    assert (root / "books/99-probe/images/v.svg").read_bytes() == src.read_bytes()


@pytestmark_pil
def test_cap_raster_corrupt_image_falls_back_to_copy(tmp_path: Path) -> None:
    # A cap_raster op whose source is not a decodable image must NOT fail the
    # import: it falls back to copying the original bytes and emits a NON-fatal
    # warning diagnostic (docs/import-pipeline.md "one bad image must not fail").
    root = tmp_path / "content"
    src = tmp_path / "src" / "broken.png"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"this is not a real PNG")

    plan = _plan(
        root,
        (
            WriteOp(kind="ensure_dir", rel_path=SCOPE, role="canonical_source", reason="dir"),
            _cap_op(src, rel="images/broken.png"),
        ),
    )
    report = writer.apply(plan, dry_run=False)

    dest = root / "books/99-probe/images/broken.png"
    assert report.refused == ()  # non-fatal: the import still applied
    assert dest.read_bytes() == src.read_bytes()  # original bytes preserved
    warnings = [d for d in report.diagnostics if d.code == "writer.cap-failed"]
    assert len(warnings) == 1
    assert warnings[0].severity == "warning"


# --- Fix D: SVG sanitize-on-import (the asset-copy boundary) -----------------
# SVG is served raw same-origin, so an SVG body image carrying script/on*/external
# refs is a stored-XSS gadget. When the writer copies an SVG asset it must strip the
# gadget; a clean SVG must be byte-identical (so the real author SVGs are preserved).


def _svg_copy_op(src: Path, rel: str) -> WriteOp:
    return WriteOp(
        kind="transform_asset",
        rel_path=SCOPE / rel,
        role="imported_asset",
        reason="svg",
        source=src,
        transform=AssetTransform(kind="copy"),
    )


def test_svg_with_script_is_sanitized_on_copy(tmp_path: Path) -> None:
    root = tmp_path / "content"
    src = tmp_path / "src" / "evil.svg"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(
        b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)">'
        b'<script>alert(2)</script>'
        b'<rect width="10" height="10"/>'
        b'<a href="javascript:alert(3)"><text>x</text></a>'
        b'<foreignObject><body>html</body></foreignObject>'
        b"</svg>"
    )
    plan = _plan(
        root,
        (
            WriteOp(kind="ensure_dir", rel_path=SCOPE, role="canonical_source", reason="dir"),
            _svg_copy_op(src, "images/evil.svg"),
        ),
    )
    writer.apply(plan, dry_run=False)
    out = (root / "books/99-probe/images/evil.svg").read_text(encoding="utf-8")

    assert "<script" not in out
    assert "onload" not in out
    assert "javascript:" not in out
    assert "<foreignObject" not in out.lower()
    # the benign drawing content survives
    assert "<rect" in out


def test_clean_svg_is_byte_identical(tmp_path: Path) -> None:
    # A clean SVG (only internal #-fragment refs, no script/on*/external href) must
    # round-trip byte-for-byte — the real author SVGs are not corrupted.
    root = tmp_path / "content"
    src = tmp_path / "src" / "clean.svg"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(
        b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'
        b'<defs><linearGradient id="g1"/></defs>'
        b'<rect fill="url(#g1)" width="10" height="10"/>'
        b'<use xlink:href="#g1"/>'
        b"</svg>"
    )
    plan = _plan(
        root,
        (
            WriteOp(kind="ensure_dir", rel_path=SCOPE, role="canonical_source", reason="dir"),
            _svg_copy_op(src, "images/clean.svg"),
        ),
    )
    writer.apply(plan, dry_run=False)
    out = (root / "books/99-probe/images/clean.svg").read_bytes()
    assert out == src.read_bytes(), "a clean SVG must be preserved byte-for-byte"


def test_svg_external_xlink_href_is_neutralized(tmp_path: Path) -> None:
    # An EXTERNAL xlink:href (http/data/file) in an SVG is a fetch/exfil gadget; an
    # INTERNAL #-fragment ref (gradient/symbol) must be kept.
    root = tmp_path / "content"
    src = tmp_path / "src" / "ext.svg"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(
        b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'
        b'<image xlink:href="https://evil.example/x.png"/>'
        b'<use xlink:href="#internal"/>'
        b"</svg>"
    )
    plan = _plan(
        root,
        (
            WriteOp(kind="ensure_dir", rel_path=SCOPE, role="canonical_source", reason="dir"),
            _svg_copy_op(src, "images/ext.svg"),
        ),
    )
    writer.apply(plan, dry_run=False)
    out = (root / "books/99-probe/images/ext.svg").read_text(encoding="utf-8")
    assert "evil.example" not in out
    assert "#internal" in out, "an internal #-fragment ref must be kept"


def test_cover_svg_is_not_sanitized(tmp_path: Path) -> None:
    # A COVER SVG (role `cover`) is DELIBERATELY not sanitized: the committed author
    # covers legitimately use <foreignObject> to render the styled title, and
    # stripping it would corrupt the published cover. Covers are a different trust
    # path (admin-curated). The body-image gate is where the XSS risk lives.
    root = tmp_path / "content"
    src = tmp_path / "src" / "cover.en.svg"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(
        b'<svg xmlns="http://www.w3.org/2000/svg">'
        b'<foreignObject x="0" y="0" width="100" height="100"><body>Title</body></foreignObject>'
        b"</svg>"
    )
    op = WriteOp(
        kind="copy",
        rel_path=SCOPE / "cover.en.svg",
        role="cover",
        reason="cover",
        source=src,
    )
    plan = _plan(
        root,
        (WriteOp(kind="ensure_dir", rel_path=SCOPE, role="canonical_source", reason="dir"), op),
    )
    writer.apply(plan, dry_run=False)
    out = (root / "books/99-probe/cover.en.svg").read_bytes()
    assert out == src.read_bytes(), "a cover SVG must be preserved byte-for-byte (foreignObject kept)"
