#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pillow>=10.4", "pyyaml>=6"]
# ///

"""Local admin tool: render release downloads into the work bundle.

Per ``docs/downloads.md``, PDFs and EPUBs are committed release artefacts under
``src/content/<kind>/<work>/<lang>.{pdf,epub}``. Merged multi-part works may also
need a committed ``<lang>.docx`` release artefact, because the optimized source
DOCX parts are not the same thing as the public "one URL = one work" download.
This script regenerates those artefacts locally; CI never runs it.

Tools required on PATH:
  - pandoc (>= 3.0)
  - typst  (>= 0.13)

Inputs:
  - ``src/content/<kind>/<work>/<lang>.md`` and any ``images/`` body assets
  - ``scripts/downloads-templates/book.typ`` (typst page layout)
  - ``scripts/downloads-templates/epub.css`` (optional EPUB stylesheet)
  - ``scripts/downloads-fonts/*/*.ttf`` (committed Cyrillic-capable fonts)
  - ``src/content/<kind>/<work>/cover.<lang>.<ext>`` (optional cover)

Outputs are written next to ``<lang>.md``.

Usage:

  uv run scripts/render_downloads.py              # render everything
  uv run scripts/render_downloads.py --book 33    # one work by kind+number
  uv run scripts/render_downloads.py --poem 1
  uv run scripts/render_downloads.py --lang en    # restrict to one language
  uv run scripts/render_downloads.py --skip-pdf   # only EPUBs
  uv run scripts/render_downloads.py --skip-epub  # only PDFs
  uv run scripts/render_downloads.py --book 2 --docx --skip-pdf --skip-epub
  uv run scripts/render_downloads.py --force      # ignore existing outputs
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml
from PIL import Image

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from lib.kinds import SEGMENT_OF, WORK_KINDS  # noqa: E402  (after sys.path bootstrap)
from lib.locales import DEFAULT_LOCALE, LOCALES  # noqa: E402  (after sys.path bootstrap)

REPO_ROOT  = _SCRIPT_DIR.parent
CONTENT    = REPO_ROOT / "src" / "content"
TEMPLATES  = REPO_ROOT / "scripts" / "downloads-templates"
FONTS_ROOT = REPO_ROOT / "scripts" / "downloads-fonts"

KIND_DIRS = SEGMENT_OF
LANGS = LOCALES
AUTHOR = "Сергей Орехов (Панкратиус)"
RIGHTS = "CC0 1.0 Universal — public domain"

EXPORT_MAX_LONG_EDGE = 1200
EXPORT_JPEG_QUALITY = 82

HTML_IMG_RE = re.compile(r"<img\b([^>]*?)/?>", re.IGNORECASE)
HTML_ATTR_RE = re.compile(r"\b([a-zA-Z_-]+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')")


@dataclass(slots=True)
class WorkEntry:
    kind: str
    number: int
    folder: Path
    lang: str
    md: Path
    slug: str
    title: str

    @property
    def cover(self) -> Path | None:
        for ext in ("jpg", "jpeg", "png", "webp", "avif", "svg"):
            p = self.folder / f"cover.{self.lang}.{ext}"
            if p.exists():
                return p
        if self.lang != DEFAULT_LOCALE:
            for ext in ("jpg", "jpeg", "png", "webp", "avif", "svg"):
                p = self.folder / f"cover.{DEFAULT_LOCALE}.{ext}"
                if p.exists():
                    return p
        return None


def _read_frontmatter(md: Path) -> dict[str, object]:
    text = md.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError(f"{md}: missing frontmatter")
    _, fm, _ = text.split("---", 2)
    data = yaml.safe_load(fm)
    if not isinstance(data, dict):
        raise ValueError(f"{md}: frontmatter is not a mapping")
    return data


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    _, _, rest = text.split("---", 2)
    return rest.lstrip()


def _html_images_to_markdown(body: str) -> str:
    """Pandoc treats raw <img> as HTML blocks; rewriting to ![](src) form
    makes pandoc emit a real image AST node that typst/EPUB embed."""
    def repl(match: re.Match[str]) -> str:
        attrs: dict[str, str] = {}
        for m in HTML_ATTR_RE.finditer(match.group(1)):
            attrs[m.group(1).lower()] = m.group(2) or m.group(3) or ""
        src = attrs.get("src")
        if not src:
            return match.group(0)
        alt = attrs.get("alt", "")
        return f"![{alt}]({src})"
    return HTML_IMG_RE.sub(repl, body)


def discover_works() -> Iterable[WorkEntry]:
    # Projects are themed sections, not works: they have no per-work download
    # matrix (docs/content-model.md), so the download renderer covers WORK kinds
    # only — never src/content/projects/.
    for kind in WORK_KINDS:
        folder_name = KIND_DIRS[kind]
        root = CONTENT / folder_name
        if not root.exists():
            continue
        for work_dir in sorted(root.iterdir()):
            if not work_dir.is_dir():
                continue
            for md in sorted(work_dir.glob("*.md")):
                if md.stem not in LANGS:
                    continue
                fm = _read_frontmatter(md)
                kind_in_fm = fm.get("kind")
                if kind_in_fm != kind:
                    continue
                number = fm.get("number")
                slug = fm.get("slug")
                title = fm.get("title")
                if not (isinstance(number, int) and isinstance(slug, str) and isinstance(title, str)):
                    continue
                yield WorkEntry(
                    kind=kind, number=number, folder=work_dir, lang=md.stem,
                    md=md, slug=slug, title=title,
                )


def _ensure_tools(formats: Iterable[str]) -> None:
    required = {"pandoc"}
    if "pdf" in formats:
        required.add("typst")
    for tool in sorted(required):
        if shutil.which(tool) is None:
            print(f"error: {tool} is not on PATH. Install it before running.", file=sys.stderr)
            sys.exit(2)


def _font_paths() -> list[Path]:
    if not FONTS_ROOT.exists():
        return []
    return sorted(p for p in FONTS_ROOT.iterdir() if p.is_dir())


def _is_jpeg_safe(img: Image.Image) -> bool:
    if img.mode in ("1", "P"):
        return False
    if "A" in img.getbands():
        return False
    return True


def _resize_for_export(img: Image.Image) -> Image.Image:
    w, h = img.size
    long_edge = max(w, h)
    if long_edge <= EXPORT_MAX_LONG_EDGE:
        return img
    scale = EXPORT_MAX_LONG_EDGE / long_edge
    size = (max(1, round(w * scale)), max(1, round(h * scale)))
    return img.resize(size, Image.LANCZOS)


def _write_jpeg(img: Image.Image, dest: Path) -> None:
    if img.mode != "RGB":
        img = img.convert("RGB")
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "JPEG", quality=EXPORT_JPEG_QUALITY, optimize=True, progressive=True)


def _write_png(img: Image.Image, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "PNG", optimize=True)


def _append_suffix(path_without_extension: Path, suffix: str) -> Path:
    return path_without_extension.parent / f"{path_without_extension.name}{suffix}"


def _stage_image(src: Path, dest_no_ext: Path) -> Path:
    """Write an export-sized copy of `src`, returning its staged path.

    The work bundle keeps source/master images. PDF/EPUB get export renditions:
    photographic images become JPEG, line/alpha images stay PNG, and long edges
    are capped to a reader-friendly size.
    """
    ext = src.suffix.lower()
    if ext == ".svg":
        dest = _append_suffix(dest_no_ext, ".svg")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        return dest
    try:
        img = Image.open(src)
        img.load()
    except Exception:
        dest = _append_suffix(dest_no_ext, src.suffix)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        return dest
    img = _resize_for_export(img)
    if _is_jpeg_safe(img):
        dest = _append_suffix(dest_no_ext, ".jpg")
        _write_jpeg(img, dest)
        return dest
    dest = _append_suffix(dest_no_ext, ".png")
    _write_png(img, dest)
    return dest


def _stage_export_bundle(entry: WorkEntry, scratch_dir: Path) -> tuple[Path, Path | None, dict[str, str]]:
    root = scratch_dir / f"{entry.kind}-{entry.slug}-{entry.lang}-bundle"
    root.mkdir(parents=True, exist_ok=True)
    image_map: dict[str, str] = {}

    images_root = entry.folder / "images"
    if images_root.exists():
        for src in sorted(p for p in images_root.rglob("*") if p.is_file()):
            rel = src.relative_to(images_root)
            staged = _stage_image(src, root / "images" / rel.with_suffix(""))
            old = Path("images") / rel
            new = staged.relative_to(root)
            old_posix = old.as_posix()
            new_posix = new.as_posix()
            image_map[f"./{old_posix}"] = f"./{new_posix}"
            image_map[old_posix] = new_posix

    cover = entry.cover
    staged_cover: Path | None = None
    if cover:
        staged_cover = _stage_image(cover, root / cover.stem)
    return root, staged_cover, image_map


def _rewrite_image_paths(body: str, image_map: dict[str, str]) -> str:
    for old, new in sorted(image_map.items(), key=lambda item: len(item[0]), reverse=True):
        body = body.replace(f"]({old})", f"]({new})")
        body = body.replace(f'src="{old}"', f'src="{new}"')
        body = body.replace(f"src='{old}'", f"src='{new}'")
    return body


def _write_export_markdown(entry: WorkEntry, dest: Path, image_map: dict[str, str]) -> None:
    raw = entry.md.read_text(encoding="utf-8")
    body = _rewrite_image_paths(_html_images_to_markdown(_strip_frontmatter(raw)), image_map)
    dest.write_text(body, encoding="utf-8")


def _pandoc_from(entry: WorkEntry) -> list[str]:
    base = "markdown-yaml_metadata_block"
    # Why: poetry uses the verse contract — single newline = line break,
    # blank line = stanza break. `+hard_line_breaks` makes pandoc emit a
    # real hard break for each in-paragraph newline, so PDF/EPUB preserve
    # lineation. Prose works (books, projects) stay on the default reader.
    if entry.kind == "poem":
        return ["--from", f"{base}+hard_line_breaks"]
    return ["--from", base]


def render_pdf(entry: WorkEntry, scratch_dir: Path) -> Path:
    template = TEMPLATES / "book.typ"
    if not template.exists():
        raise FileNotFoundError(f"Missing typst template at {template}")
    export_root, cover, image_map = _stage_export_bundle(entry, scratch_dir)
    scratch_md = export_root / f"{entry.kind}-{entry.slug}-{entry.lang}.md"
    _write_export_markdown(entry, scratch_md, image_map)
    out = entry.folder / f"{entry.lang}.pdf"
    typst_opts: list[str] = [
        "--pdf-engine-opt=--root=/",
        "--pdf-engine-opt=--ignore-system-fonts",
    ]
    for fp in _font_paths():
        typst_opts.append(f"--pdf-engine-opt=--font-path={fp}")
    args = [
        "pandoc", str(scratch_md),
        *_pandoc_from(entry),
        "-o", str(out),
        "--pdf-engine=typst",
        "--template", str(template),
        *typst_opts,
        "--resource-path", str(export_root),
        "--metadata", f"title={entry.title}",
        "--metadata", f"lang={entry.lang}",
        "--metadata", f"author={AUTHOR}",
    ]
    if cover:
        args += ["--metadata", f"cover-path={cover}"]
    subprocess.run(args, check=True)
    return out


def render_epub(entry: WorkEntry, scratch_dir: Path) -> Path:
    export_root, cover, image_map = _stage_export_bundle(entry, scratch_dir)
    scratch_md = export_root / f"{entry.kind}-{entry.slug}-{entry.lang}.md"
    _write_export_markdown(entry, scratch_md, image_map)
    out = entry.folder / f"{entry.lang}.epub"
    css = TEMPLATES / "epub.css"
    epub_cover = cover if entry.kind == "book" else None
    args = [
        "pandoc", str(scratch_md),
        *_pandoc_from(entry),
        "-o", str(out),
        "--to", "epub3",
        "--resource-path", str(export_root),
        "--metadata", f"title={entry.title}",
        "--metadata", f"lang={entry.lang}",
        "--metadata", f"author={AUTHOR}",
        "--metadata", f"rights={RIGHTS}",
    ]
    if css.exists():
        args += ["--css", str(css)]
    if epub_cover:
        args += ["--epub-cover-image", str(epub_cover)]
    subprocess.run(args, check=True)
    return out


def _has_source_parts(entry: WorkEntry) -> bool:
    return any(entry.folder.glob(f"{entry.lang}-part*.docx"))


def render_docx(entry: WorkEntry, scratch_dir: Path) -> Path:
    """Render a merged DOCX release artefact from canonical Markdown.

    This is intentionally for multi-part works only. Single-source works keep
    their optimized source DOCX as the public DOCX download.
    """
    if not _has_source_parts(entry):
        raise ValueError(f"{entry.kind}#{entry.number}/{entry.lang} has no source DOCX parts")
    export_root, _cover, image_map = _stage_export_bundle(entry, scratch_dir)
    scratch_md = export_root / f"{entry.kind}-{entry.slug}-{entry.lang}.md"
    _write_export_markdown(entry, scratch_md, image_map)
    out = entry.folder / f"{entry.lang}.docx"
    args = [
        "pandoc", str(scratch_md),
        *_pandoc_from(entry),
        "-o", str(out),
        "--resource-path", str(export_root),
        "--metadata", f"title={entry.title}",
        "--metadata", f"lang={entry.lang}",
        "--metadata", f"author={AUTHOR}",
        "--metadata", f"rights={RIGHTS}",
    ]
    subprocess.run(args, check=True)
    return out


def render(
    *,
    book: int | None = None,
    poem: int | None = None,
    lang: str | None = None,
    skip_pdf: bool = False,
    skip_epub: bool = False,
    docx: bool = False,
    force: bool = False,
) -> int:
    selected: list[WorkEntry] = []
    for entry in discover_works():
        if lang and entry.lang != lang:
            continue
        if book is not None and (entry.kind != "book" or entry.number != book):
            continue
        if poem is not None and (entry.kind != "poem" or entry.number != poem):
            continue
        selected.append(entry)

    if not selected:
        print("no matching works", file=sys.stderr)
        return 1

    formats: list[str] = []
    if not skip_pdf:  formats.append("pdf")
    if not skip_epub: formats.append("epub")
    if docx: formats.append("docx")
    _ensure_tools(formats)

    pdfs_made = 0
    epubs_made = 0
    docxs_made = 0
    skipped = 0

    scratch_parent = REPO_ROOT / ".cache"
    scratch_parent.mkdir(parents=True, exist_ok=True)
    scratch_dir = scratch_parent / f"pancratius-render-{uuid.uuid4().hex}"
    scratch_dir.mkdir(parents=True)
    try:
        for entry in selected:
            src_mtime = entry.md.stat().st_mtime
            if "pdf" in formats:
                out = entry.folder / f"{entry.lang}.pdf"
                if not force and out.exists() and out.stat().st_mtime >= src_mtime:
                    skipped += 1
                else:
                    render_pdf(entry, scratch_dir)
                    pdfs_made += 1
                    print(f"  PDF  {entry.kind}#{entry.number}/{entry.lang}  →  {out.relative_to(REPO_ROOT)}")
            # EPUB scope per docs/downloads.md: books only.
            if "epub" in formats and entry.kind == "book":
                out = entry.folder / f"{entry.lang}.epub"
                if not force and out.exists() and out.stat().st_mtime >= src_mtime:
                    skipped += 1
                else:
                    render_epub(entry, scratch_dir)
                    epubs_made += 1
                    print(f"  EPUB {entry.kind}#{entry.number}/{entry.lang}  →  {out.relative_to(REPO_ROOT)}")
            if "docx" in formats and _has_source_parts(entry):
                out = entry.folder / f"{entry.lang}.docx"
                if not force and out.exists() and out.stat().st_mtime >= src_mtime:
                    skipped += 1
                else:
                    render_docx(entry, scratch_dir)
                    docxs_made += 1
                    print(f"  DOCX {entry.kind}#{entry.number}/{entry.lang}  →  {out.relative_to(REPO_ROOT)}")
    finally:
        shutil.rmtree(scratch_dir, ignore_errors=True)

    print(f"\nrendered: {pdfs_made} PDF, {epubs_made} EPUB, {docxs_made} DOCX ({skipped} skipped; --force to rebuild)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--book", type=int, help="render only this book number")
    parser.add_argument("--poem", type=int, help="render only this poem number")
    parser.add_argument("--lang", choices=LANGS, help="restrict to one language")
    parser.add_argument("--skip-pdf", action="store_true")
    parser.add_argument("--skip-epub", action="store_true")
    parser.add_argument("--docx", action="store_true",
                        help="also render merged DOCX release artifacts for multi-part works")
    parser.add_argument("--force", action="store_true",
                        help="re-render even if the output is newer than the source")
    ns = parser.parse_args()
    return render(
        book=ns.book,
        poem=ns.poem,
        lang=ns.lang,
        skip_pdf=ns.skip_pdf,
        skip_epub=ns.skip_epub,
        docx=ns.docx,
        force=ns.force,
    )


if __name__ == "__main__":
    sys.exit(main())
