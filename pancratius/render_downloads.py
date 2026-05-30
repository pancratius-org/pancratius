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
  - ``pancratius/download_assets/templates/book.typ`` (typst page layout)
  - ``pancratius/download_assets/templates/epub.css`` (optional EPUB stylesheet)
  - ``pancratius/download_assets/fonts/*/*.ttf`` (committed Cyrillic-capable fonts)
  - ``src/content/<kind>/<work>/cover.<lang>.<ext>`` (optional cover)

Outputs are written next to ``<lang>.md``.

Usage:

  uv run pancratius downloads render              # render everything
  uv run pancratius downloads render --book 33    # one work by kind+number
  uv run pancratius downloads render --poem 1
  uv run pancratius downloads render --lang en    # restrict to one language
  uv run pancratius downloads render --skip-pdf   # only EPUBs
  uv run pancratius downloads render --skip-epub  # only PDFs
  uv run pancratius downloads render --book 2 --docx --skip-pdf --skip-epub
  uv run pancratius downloads render --force      # ignore existing outputs
"""

from __future__ import annotations

import re
import shutil
import subprocess
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from html import unescape
from pathlib import Path

from PIL import Image

from pancratius.content_catalog import split_frontmatter
from pancratius.kinds import CORPUS_WORK_KINDS, SEGMENT_OF
from pancratius.locales import DEFAULT_LOCALE, LOCALES
from pancratius.paths import (
    CACHE_ROOT,
    CONTENT_ROOT,
    DOWNLOAD_FONTS_ROOT,
    DOWNLOAD_TEMPLATES_ROOT,
    REPO_ROOT,
)

CONTENT = CONTENT_ROOT
TEMPLATES = DOWNLOAD_TEMPLATES_ROOT
FONTS_ROOT = DOWNLOAD_FONTS_ROOT

KIND_DIRS = SEGMENT_OF
LANGS = LOCALES
AUTHOR = "Сергей Орехов (Панкратиус)"
RIGHTS = "CC0 1.0 Universal — public domain"

EXPORT_MAX_LONG_EDGE = 1200
EXPORT_JPEG_QUALITY = 82

HTML_IMG_RE = re.compile(r"<img\b([^>]*?)/?>", re.IGNORECASE)
HTML_TAG_PATTERN = (
    r"<(?P<closing>/)?(?P<name>[A-Za-z][A-Za-z0-9:-]*)(?P<attrs>(?:\s+[^<>]*?)?)\s*(?P<self>/)?>"
)
HTML_MARKUP_RE = re.compile(
    r"<!--[\s\S]*?(?:-->|$)|<![^\s<>][^>]*(?:>|$)|<\?[A-Za-z][\s\S]*?(?:\?>|$)|" + HTML_TAG_PATTERN
)
HTML_ATTR_RE = re.compile(r"\s+([A-Za-z_:][A-Za-z0-9_:.-]*)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')")
URL_SCHEME_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]*):")
ALLOWED_HREF_SCHEMES = {"http", "https", "mailto"}
SPAN_DIR_VALUES = {"ltr", "rtl", "auto"}


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


@dataclass(frozen=True, slots=True)
class RenderSummary:
    pdfs_made: int
    epubs_made: int
    docxs_made: int
    skipped: int


class DownloadRenderError(Exception):
    """Release-artifact rendering cannot proceed."""


def _read_frontmatter(md: Path) -> dict[str, object]:
    fm, _body = split_frontmatter(md.read_text(encoding="utf-8"))
    if not fm:
        raise ValueError(f"{md}: missing frontmatter")
    return fm


def _html_error(tag: str, source: Path, line: int, reason: str) -> DownloadRenderError:
    return DownloadRenderError(f"{source}:{line}: unsupported raw HTML tag {tag!r}: {reason}")


def _parse_html_attrs(attrs: str, tag: str, source: Path, line: int) -> dict[str, str]:
    parsed: dict[str, str] = {}
    end = 0
    for match in HTML_ATTR_RE.finditer(attrs):
        if attrs[end:match.start()].strip():
            raise _html_error(tag, source, line, "attributes must be quoted name=value pairs")
        name = match.group(1).lower()
        if name in parsed:
            raise _html_error(tag, source, line, f"duplicate {name} attribute")
        parsed[name] = match.group(2) or match.group(3) or ""
        end = match.end()
    if attrs[end:].strip():
        raise _html_error(tag, source, line, "attributes must be quoted name=value pairs")
    return parsed


def _require_no_attrs(attrs: str, tag: str, source: Path, line: int) -> None:
    if attrs.strip():
        raise _html_error(tag, source, line, "attributes are not supported on this tag")


def _require_only_attrs(
    attrs: dict[str, str],
    allowed: set[str],
    tag: str,
    source: Path,
    line: int,
) -> None:
    unsupported = sorted(set(attrs) - allowed)
    if unsupported:
        raise _html_error(tag, source, line, f"unsupported attribute {unsupported[0]!r}")


def _require_class(
    attrs_text: str,
    expected: set[str],
    tag: str,
    source: Path,
    line: int,
) -> str:
    attrs = _parse_html_attrs(attrs_text, tag, source, line)
    _require_only_attrs(attrs, {"class"}, tag, source, line)
    class_name = attrs.get("class")
    if class_name not in expected:
        allowed = ", ".join(sorted(expected))
        raise _html_error(tag, source, line, f"expected class {allowed}")
    return class_name


def _require_safe_href(href: str, tag: str, source: Path, line: int) -> None:
    decoded = unescape(href).strip()
    if not decoded:
        raise _html_error(tag, source, line, "missing required href attribute")
    match = URL_SCHEME_RE.match(decoded)
    if match and match.group(1).lower() not in ALLOWED_HREF_SCHEMES:
        raise _html_error(
            tag,
            source,
            line,
            f"unsupported href URL scheme {match.group(1).lower()!r}",
        )


def _validate_download_html_allowlist(body: str, source: Path) -> None:
    stack: list[tuple[str, str, str, int]] = []
    line = 1
    cursor = 0

    for match in HTML_MARKUP_RE.finditer(body):
        tag = match.group(0)
        line += body.count("\n", cursor, match.start())
        match_line = line
        line += body.count("\n", match.start(), match.end())
        cursor = match.end()

        if match.group("name") is None:
            raise _html_error(tag, source, match_line, "raw HTML comments, declarations, and processing instructions are not supported")

        name = match.group("name").lower()
        attrs_text = match.group("attrs") or ""
        closing = bool(match.group("closing"))
        self_closing = bool(match.group("self"))

        if closing:
            if attrs_text.strip() or self_closing:
                raise _html_error(tag, source, match_line, "closing tags cannot carry attributes")
            if name in {"strong", "em", "a", "span"}:
                continue
            if name in {"br", "img"}:
                raise _html_error(tag, source, match_line, "void tag cannot be closed")
            if name in {"div", "blockquote", "p", "footer"}:
                if not stack or stack[-1][0] != name:
                    raise _html_error(tag, source, match_line, "does not close the current supported wrapper")
                stack.pop()
                continue
            raise _html_error(tag, source, match_line, "tag is outside the download HTML allowlist")

        if name in {"strong", "em"}:
            if self_closing:
                raise _html_error(tag, source, match_line, "inline emphasis tag cannot be self-closing")
            _require_no_attrs(attrs_text, tag, source, match_line)
            continue

        if name == "br":
            _require_no_attrs(attrs_text, tag, source, match_line)
            continue

        if name == "img":
            attrs = _parse_html_attrs(attrs_text, tag, source, match_line)
            _require_only_attrs(attrs, {"src", "alt"}, tag, source, match_line)
            if not attrs.get("src"):
                raise _html_error(tag, source, match_line, "missing required src attribute")
            continue

        if name == "a":
            if self_closing:
                raise _html_error(tag, source, match_line, "anchor tag cannot be self-closing")
            attrs = _parse_html_attrs(attrs_text, tag, source, match_line)
            _require_only_attrs(attrs, {"href"}, tag, source, match_line)
            href = attrs.get("href")
            if href is None:
                raise _html_error(tag, source, match_line, "missing required href attribute")
            _require_safe_href(href, tag, source, match_line)
            continue

        if name == "span":
            if self_closing:
                raise _html_error(tag, source, match_line, "span tag cannot be self-closing")
            attrs = _parse_html_attrs(attrs_text, tag, source, match_line)
            _require_only_attrs(attrs, {"dir"}, tag, source, match_line)
            if attrs.get("dir", "").lower() not in SPAN_DIR_VALUES:
                raise _html_error(tag, source, match_line, 'span requires dir="ltr", dir="rtl", or dir="auto"')
            continue

        if name == "div":
            if self_closing:
                raise _html_error(tag, source, match_line, "verse-block wrapper cannot be self-closing")
            role = _require_class(attrs_text, {"verse-block"}, tag, source, match_line)
            stack.append((name, role, tag, match_line))
            continue

        if name == "blockquote":
            if self_closing:
                raise _html_error(tag, source, match_line, "blockquote wrapper cannot be self-closing")
            role = _require_class(attrs_text, {"epigraph"}, tag, source, match_line)
            stack.append((name, role, tag, match_line))
            continue

        if name == "p":
            if self_closing:
                raise _html_error(tag, source, match_line, "paragraph wrapper cannot be self-closing")
            attrs = _parse_html_attrs(attrs_text, tag, source, match_line)
            if attrs:
                _require_only_attrs(attrs, {"class"}, tag, source, match_line)
                if attrs.get("class") != "signature" or stack:
                    raise _html_error(tag, source, match_line, 'only top-level <p class="signature"> is allowed')
                stack.append((name, "signature", tag, match_line))
                continue
            if stack and stack[-1][0] == "blockquote" and stack[-1][1] == "epigraph":
                stack.append((name, "plain", tag, match_line))
                continue
            raise _html_error(tag, source, match_line, "plain <p> is only allowed inside canonical blockquotes")

        if name == "footer":
            if self_closing:
                raise _html_error(tag, source, match_line, "footer wrapper cannot be self-closing")
            _require_no_attrs(attrs_text, tag, source, match_line)
            if not stack or stack[-1][0] != "blockquote" or stack[-1][1] != "epigraph":
                raise _html_error(tag, source, match_line, "footer is only allowed directly inside epigraph blockquotes")
            stack.append((name, "footer", tag, match_line))
            continue

        raise _html_error(tag, source, match_line, "tag is outside the download HTML allowlist")

    if stack:
        _name, _role, tag, line = stack[-1]
        raise _html_error(tag, source, line, "wrapper is not closed")


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
    for kind in CORPUS_WORK_KINDS:
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
            raise DownloadRenderError(f"{tool} is not on PATH. Install it before running.")


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
    except OSError:
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
    _frontmatter, body = split_frontmatter(raw)
    body = body.lstrip()
    _validate_download_html_allowlist(body, entry.md)
    body = _rewrite_image_paths(_html_images_to_markdown(body), image_map)
    dest.write_text(body, encoding="utf-8")


def _pandoc_from(_entry: WorkEntry) -> list[str]:
    # One plain reader for every kind. LINEATION (books AND poems) is encoded in
    # the generated Markdown as CommonMark two-trailing-space hard breaks, which
    # the default reader already turns into real hard breaks — so PDF/EPUB
    # preserve lineation without the poem-only `+hard_line_breaks` extension.
    return ["--from", "markdown-yaml_metadata_block"]


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
) -> RenderSummary:
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
        raise DownloadRenderError("no matching works")

    formats: list[str] = []
    if not skip_pdf:
        formats.append("pdf")
    if not skip_epub:
        formats.append("epub")
    if docx:
        formats.append("docx")
    _ensure_tools(formats)

    pdfs_made = 0
    epubs_made = 0
    docxs_made = 0
    skipped = 0

    scratch_parent = CACHE_ROOT
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

    summary = RenderSummary(
        pdfs_made=pdfs_made,
        epubs_made=epubs_made,
        docxs_made=docxs_made,
        skipped=skipped,
    )
    print(
        f"\nrendered: {summary.pdfs_made} PDF, {summary.epubs_made} EPUB, "
        f"{summary.docxs_made} DOCX ({summary.skipped} skipped; --force to rebuild)"
    )
    return summary
