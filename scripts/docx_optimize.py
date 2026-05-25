#!/usr/bin/env python3
"""
docx_optimize.py — shrink Sergey Orekhov's .docx corpus.

The bulk of the bytes in these books is uncompressed 1024x1536 PNGs (AI-generated
illustrations). This script unzips each .docx, walks ``word/media/``, recompresses
images, and rezips the result. Strategy:

* PNG, no alpha, looks photographic   -> JPEG quality 85
* PNG with alpha or sharp/screenshot  -> re-saved PNG (optimize=True)
* JPEG                                -> re-encoded JPEG q85 if larger than threshold
* Any image with long edge > 1600 px  -> downscaled to 1600 px (LANCZOS)
* Tiny icons (< 30 KB and < 256 px)   -> left alone

When a media file's extension changes (PNG -> JPG), the script rewrites the
``Target`` attribute in ``word/_rels/document.xml.rels`` (and any other ``*.rels``
under ``word/``). ``[Content_Types].xml`` is patched only if needed -- the corpus
files all already declare ``Default Extension="jpg|jpeg|png"``.

Originals stay read-only. Output is placed *into the content tree* alongside
the rendered Markdown: ``src/content/<kind>/<slug>/<lang>.docx`` for single-source
books/poems/projects, and ``src/content/books/<slug>/<lang>-part<N>.docx`` for
multi-part books (the few that exist). The mapping is derived from the central
``data/conversion-manifest.json`` source provenance (with a frontmatter
``original_filename`` fallback for poetry/projects) — there is no ``--out``
override, because the destination is a property of the corpus layout, not a
CLI knob.

The script is idempotent: if the output exists and is newer than the source it
is skipped, unless ``--force`` is given.

Usage:
    uv run scripts/docx_optimize.py                      # process default roots
    uv run scripts/docx_optimize.py path/to/file.docx    # single file
    uv run scripts/docx_optimize.py --force              # rebuild all
    uv run scripts/docx_optimize.py --verbose            # per-image stats
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml
from PIL import Image, ImageFilter

# ---------- tunables ----------------------------------------------------------

MAX_LONG_EDGE = 1600          # downscale anything bigger
JPEG_QUALITY = 85             # subjectively indistinguishable at 1600 px
JPEG_PROGRESSIVE = True
PNG_OPTIMIZE = True
SKIP_ICON_BYTES = 30_000      # below this, leave PNG/JPG alone
SKIP_ICON_LONG_EDGE = 256
RECOMPRESS_JPEG_IF_BIGGER_THAN = 200_000   # don't bother re-encoding already-small JPEGs

# Why: per-image cap to 2× the in-document display rect at 144 dpi. Source
# embeds 1024×1536 PNGs displayed at ~113×170 px (Word EMUs cx≈1080000 ≈ 113 px
# at 96 dpi), so the bytes are ~9× oversized. EMU → inches → px: 914400 EMU
# = 1 inch, so px = EMU / 914400 * 96. We then take 2× of that long edge at
# 144 dpi and cap to MAX_LONG_EDGE so big bibliography thumbnails behave sane.
EMU_PER_INCH = 914400
DISPLAY_DPI = 96
RENDER_DPI = 144
DISPLAY_RECT_MULTIPLIER = 2.0
MIN_PER_IMAGE_LONG_EDGE = 320  # below this, don't cap (lose too much detail)

# Photographic heuristic. In this corpus, PNGs come in two flavours:
#   (a) 1024x1536 RGB illustrations from an AI image generator -- treat as JPEG.
#   (b) Tiny icons / cover thumbnails -- already small, skip.
# Real screenshots / diagrams would either carry an alpha channel, be palettised
# (mode "P"), or be much smaller. The cleanest test is therefore:
#   "RGB, no alpha, long edge >= LARGE_PNG_MIN_EDGE OR file >= LARGE_PNG_MIN_BYTES"
# which catches the AI illustrations without false-positive on small icons.
# As a backstop we also compute an edge-density score so genuine pixel-art
# diagrams (sharp edges, narrow palette) get kept as PNG.
LARGE_PNG_MIN_EDGE = 512
LARGE_PNG_MIN_BYTES = 200_000
EDGE_DENSITY_DIAGRAM_MIN = 0.13     # > this => keep as PNG (sharp lines)

ROOT = Path(__file__).resolve().parent.parent
CONTENT_ROOT = ROOT / "src" / "content"
MANIFEST_PATH = ROOT / "data" / "conversion-manifest.json"
SOURCE_ROOTS = [
    ROOT / "legacy" / "books" / "ru",
    ROOT / "legacy" / "books" / "en",
    ROOT / "legacy" / "poetry",
    ROOT / "legacy" / "projects",
]

# Why: map src/content/<folder>/ → manifest by_work kind segment, so optimized DOCX
# writes register under the same per-work generated_paths entry the
# Markdown converter populates.
_CONTENT_FOLDER_TO_KIND: dict[str, str] = {
    "books": "book",
    "poetry": "poem",
    "projects": "project",
}


def _work_key_for_dst(dst: Path, content_root: Path) -> tuple[str, str, str] | None:
    """For a destination like src/content/books/<slug>/<lang>.docx return
    (kind, slug, work-folder-relative-path). Returns None if `dst` isn't a
    work-bundle file under content_root."""
    try:
        rel = dst.resolve().relative_to(content_root.resolve())
    except ValueError:
        return None
    parts = rel.parts
    if len(parts) < 3:
        return None
    folder, slug, *_rest = parts
    kind = _CONTENT_FOLDER_TO_KIND.get(folder)
    if not kind:
        return None
    work_rel = Path(*parts[2:]).as_posix()
    return kind, slug, work_rel


WORK_OWNER = "docx_optimize"


def update_manifest_generated_paths(
    written: list[Path],
    content_root: Path = CONTENT_ROOT,
    manifest_path: Path = MANIFEST_PATH,
) -> int:
    """Register optimized-docx writes into this owner's slot in the per-work
    `generated_paths` dict. The Markdown converter writes its own slot; we
    never touch it. Skips silently when the manifest doesn't exist."""
    if not manifest_path.exists() or not written:
        return 0
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return 0
    by_work = manifest.get("by_work")
    if not isinstance(by_work, dict):
        return 0
    added = 0
    for dst in written:
        info = _work_key_for_dst(dst, content_root)
        if info is None:
            continue
        kind, slug, work_rel = info
        key = f"{kind}/{slug}"
        entry = by_work.get(key)
        if not isinstance(entry, dict):
            continue
        gp = entry.setdefault("generated_paths", {})
        if not isinstance(gp, dict):
            continue
        mine = gp.setdefault(WORK_OWNER, [])
        if not isinstance(mine, list):
            continue
        if work_rel in mine:
            continue
        mine.append(work_rel)
        added += 1
    if added:
        for entry in by_work.values():
            if not isinstance(entry, dict):
                continue
            gp = entry.get("generated_paths")
            if not isinstance(gp, dict):
                continue
            mine = gp.get(WORK_OWNER)
            if isinstance(mine, list):
                gp[WORK_OWNER] = sorted(set(mine))
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return added

# ---------- helpers -----------------------------------------------------------


@dataclass
class ImgStats:
    name: str
    in_bytes: int
    out_bytes: int
    action: str            # "kept" | "png-opt" | "jpeg" | "resize+jpeg" | "resize+png"
    new_name: str | None   # set when extension changes


def edge_density(img: Image.Image) -> float:
    """Mean edge intensity (0..1) on a 256px-long-edge grayscale downscale."""
    sample = img.convert("L")
    w, h = sample.size
    scale = 256 / max(w, h)
    if scale < 1:
        sample = sample.resize((max(1, int(w * scale)), max(1, int(h * scale))),
                               Image.LANCZOS)
    edges = sample.filter(ImageFilter.FIND_EDGES)
    hist = edges.histogram()
    total = sum(hist)
    if total == 0:
        return 0.0
    return sum(i * c for i, c in enumerate(hist)) / total / 255.0


def is_jpeg_safe(img: Image.Image, in_size: int) -> bool:
    """
    True when re-encoding as JPEG is the right call:
      - no alpha (JPEG can't carry it)
      - either it's a large image (so the AI-illustration shape) OR
        the file is big (>200 KB) AND lacks sharp-line characteristics.
    """
    if img.mode in ("1", "P"):
        return False
    if "A" in img.getbands():
        return False
    if img.mode == "L":
        # grayscale photos are fine in JPEG, but small text/diagrams in L are
        # rare here. Treat as photo only if also large.
        pass
    w, h = img.size
    long_edge = max(w, h)
    large_by_dim = long_edge >= LARGE_PNG_MIN_EDGE
    large_by_bytes = in_size >= LARGE_PNG_MIN_BYTES
    if not (large_by_dim or large_by_bytes):
        return False
    # Backstop: if it looks like a hard-edge diagram, keep PNG.
    if edge_density(img) > EDGE_DENSITY_DIAGRAM_MIN:
        return False
    return True


def downscale_if_needed(img: Image.Image, max_long_edge: int = MAX_LONG_EDGE) -> Image.Image:
    w, h = img.size
    long_edge = max(w, h)
    if long_edge <= max_long_edge:
        return img
    scale = max_long_edge / long_edge
    new_size = (max(1, int(round(w * scale))), max(1, int(round(h * scale))))
    return img.resize(new_size, Image.LANCZOS)


def per_image_long_edge(display_emu: tuple[int, int] | None) -> int:
    """Cap derived from the in-document display rectangle. Falls back to the
    global MAX_LONG_EDGE when we don't know the display size."""
    if not display_emu:
        return MAX_LONG_EDGE
    cx, cy = display_emu
    long_emu = max(cx, cy)
    display_px = long_emu / EMU_PER_INCH * DISPLAY_DPI
    target = int(display_px * (RENDER_DPI / DISPLAY_DPI) * DISPLAY_RECT_MULTIPLIER)
    return max(MIN_PER_IMAGE_LONG_EDGE, min(MAX_LONG_EDGE, target))


def encode_jpeg(img: Image.Image) -> bytes:
    if img.mode != "RGB":
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=JPEG_QUALITY, optimize=True,
             progressive=JPEG_PROGRESSIVE)
    return buf.getvalue()


def encode_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=PNG_OPTIMIZE)
    return buf.getvalue()


def process_image(
    name: str,
    data: bytes,
    display_emu: tuple[int, int] | None = None,
) -> tuple[bytes, str | None, str]:
    """
    Return (new_bytes, new_name_or_None, action_tag).

    new_name is None when the filename (and therefore the rels Target) does
    not change.
    """
    in_size = len(data)
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        return data, None, "kept-undecodable"

    fmt = (img.format or "").upper()
    w, h = img.size
    long_edge = max(w, h)

    if in_size <= SKIP_ICON_BYTES and long_edge <= SKIP_ICON_LONG_EDGE:
        return data, None, "kept-tiny"

    max_long_edge = per_image_long_edge(display_emu)
    needs_resize = long_edge > max_long_edge
    photographic = is_jpeg_safe(img, in_size)

    base, _ = os.path.splitext(name)

    if fmt == "PNG":
        if photographic:
            img = downscale_if_needed(img, max_long_edge)
            new_bytes = encode_jpeg(img)
            if len(new_bytes) >= in_size:
                if not needs_resize:
                    return data, None, "kept-not-smaller"
            new_name = base + ".jpg"
            tag = "resize+jpeg" if needs_resize else "jpeg"
            return new_bytes, new_name, tag
        else:
            if needs_resize:
                img = downscale_if_needed(img, max_long_edge)
                return encode_png(img), None, "resize+png"
            new_bytes = encode_png(img)
            if len(new_bytes) < in_size:
                return new_bytes, None, "png-opt"
            return data, None, "kept-png-already-small"

    if fmt == "JPEG":
        if needs_resize:
            img = downscale_if_needed(img, max_long_edge)
            return encode_jpeg(img), None, "resize+jpeg"
        if in_size <= RECOMPRESS_JPEG_IF_BIGGER_THAN:
            return data, None, "kept-jpeg-small"
        new_bytes = encode_jpeg(img)
        if len(new_bytes) < in_size * 0.95:
            return new_bytes, None, "jpeg-recompress"
        return data, None, "kept-jpeg-no-win"

    if photographic:
        img = downscale_if_needed(img, max_long_edge)
        return encode_jpeg(img), base + ".jpg", "convert+jpeg"
    img = downscale_if_needed(img, max_long_edge)
    return encode_png(img), base + ".png", "convert+png"


# ---------- rels / content-types patching -------------------------------------

_TARGET_RE = re.compile(rb'Target="([^"]+)"')


def patch_rels_xml(xml: bytes, rename_map: dict[str, str]) -> bytes:
    """Rewrite Target attributes pointing at renamed media files."""
    if not rename_map:
        return xml

    def _sub(m: re.Match[bytes]) -> bytes:
        target = m.group(1).decode("utf-8")
        # Targets look like "media/image1.png"
        if target.startswith("media/"):
            basename = target[len("media/"):]
            if basename in rename_map:
                return b'Target="media/' + rename_map[basename].encode("utf-8") + b'"'
        return m.group(0)

    return _TARGET_RE.sub(_sub, xml)


_TYPES_DEFAULT_RE = re.compile(
    rb'<Default\s+Extension="([^"]+)"\s+ContentType="([^"]+)"\s*/>'
)
_TYPES_CLOSE_RE = re.compile(rb"</Types>")

# Why: walk every <w:drawing> element in document.xml, link its <wp:extent> to
# the <a:blip r:embed="rId…">, then resolve rId→media file via document.xml.rels.
# The largest extent across references wins (some thumbs reappear; we cap to
# the most generous slot they're rendered into).
_DRAWING_RE = re.compile(rb"<w:drawing\b[^>]*>.*?</w:drawing>", re.DOTALL)
_EXTENT_RE = re.compile(rb'<wp:extent\b[^>]*\bcx="(\d+)"[^>]*\bcy="(\d+)"')
_BLIP_EMBED_RE = re.compile(rb'<a:blip\b[^>]*\br:embed="([^"]+)"')
_REL_ID_TARGET_RE = re.compile(
    rb'<Relationship\b[^>]*\bId="([^"]+)"[^>]*\bTarget="([^"]+)"'
)


# Why: scrub the same rights boilerplate the MD pipeline removes, but at the
# XML level so the downloadable DOCX is consistent with the rendered MD.
# Bounded: only paragraphs (`<w:p>`) appearing before the first paragraph
# styled as a heading (`<w:pStyle w:val="Heading…">`). Never touches body
# prose.
_W_PARA_RE = re.compile(rb"<w:p\b[^>]*>.*?</w:p>", re.DOTALL)
_W_HEADING_RE = re.compile(rb'<w:pStyle\s+w:val="Heading\d+"')
_W_T_RE = re.compile(rb"<w:t[^>]*>([^<]*)</w:t>", re.DOTALL)

RIGHTS_TEXT_PATTERNS = [
    re.compile(rb"(?i)all rights reserved"),
    re.compile(b"(?i)copyright\\s+\xc2\xa9"),
    re.compile(rb"(?i)no part of this book may be reproduced"),
    re.compile(rb"(?i)the characters and events portrayed.*coincidental"),
    re.compile(b"(?i)\xd0\x92\xd1\x81\xd0\xb5 \xd0\xbf\xd1\x80\xd0\xb0\xd0\xb2\xd0\xb0 \xd0\xb7\xd0\xb0\xd1\x89\xd0\xb8\xd1\x89\xd0\xb5\xd0\xbd"),
]


def scrub_document_xml(document_xml: bytes) -> bytes:
    paragraphs: list[tuple[int, int, bytes]] = []
    for m in _W_PARA_RE.finditer(document_xml):
        paragraphs.append((m.start(), m.end(), m.group(0)))
    if not paragraphs:
        return document_xml
    first_heading_idx = next(
        (i for i, (_, _, p) in enumerate(paragraphs) if _W_HEADING_RE.search(p)),
        min(len(paragraphs), max(20, int(len(paragraphs) * 0.10))),
    )
    boilerplate_indices: set[int] = set()
    for i in range(first_heading_idx):
        text = b"".join(t.group(1) for t in _W_T_RE.finditer(paragraphs[i][2]))
        if any(pat.search(text) for pat in RIGHTS_TEXT_PATTERNS):
            boilerplate_indices.add(i)
    if not boilerplate_indices:
        return document_xml
    pieces: list[bytes] = []
    cursor = 0
    for i, (start, end, _) in enumerate(paragraphs):
        if i in boilerplate_indices:
            pieces.append(document_xml[cursor:start])
            cursor = end
    pieces.append(document_xml[cursor:])
    return b"".join(pieces)


def parse_display_rects(
    document_xml: bytes,
    rels_xml: bytes,
) -> dict[str, tuple[int, int]]:
    rid_to_target: dict[str, str] = {}
    for m in _REL_ID_TARGET_RE.finditer(rels_xml):
        rid = m.group(1).decode("utf-8")
        target = m.group(2).decode("utf-8")
        if target.startswith("media/"):
            rid_to_target[rid] = target[len("media/"):]
    rects: dict[str, tuple[int, int]] = {}
    for d in _DRAWING_RE.finditer(document_xml):
        block = d.group(0)
        ext = _EXTENT_RE.search(block)
        emb = _BLIP_EMBED_RE.search(block)
        if not ext or not emb:
            continue
        cx, cy = int(ext.group(1)), int(ext.group(2))
        rid = emb.group(1).decode("utf-8")
        target = rid_to_target.get(rid)
        if not target:
            continue
        cur = rects.get(target, (0, 0))
        if cx * cy > cur[0] * cur[1]:
            rects[target] = (cx, cy)
    return rects


def ensure_content_types(xml: bytes, extensions: set[str]) -> bytes:
    """Make sure every needed extension has a Default content type entry."""
    have = {m.group(1).decode("utf-8").lower() for m in _TYPES_DEFAULT_RE.finditer(xml)}
    needed_for_ext = {
        "jpg":  b'<Default Extension="jpg" ContentType="image/jpeg"/>',
        "jpeg": b'<Default Extension="jpeg" ContentType="image/jpeg"/>',
        "png":  b'<Default Extension="png" ContentType="image/png"/>',
        "gif":  b'<Default Extension="gif" ContentType="image/gif"/>',
        "bmp":  b'<Default Extension="bmp" ContentType="image/bmp"/>',
        "tiff": b'<Default Extension="tiff" ContentType="image/tiff"/>',
        "tif":  b'<Default Extension="tif" ContentType="image/tiff"/>',
        "webp": b'<Default Extension="webp" ContentType="image/webp"/>',
    }
    additions = []
    for ext in sorted(extensions):
        e = ext.lower()
        if e in have:
            continue
        snippet = needed_for_ext.get(e)
        if snippet is None:
            continue
        additions.append(snippet)
        have.add(e)
    if not additions:
        return xml
    return _TYPES_CLOSE_RE.sub(b"".join(additions) + b"</Types>", xml, count=1)


# ---------- docx pipeline -----------------------------------------------------


def optimize_docx(src: Path, dst: Path, *, verbose: bool = False) -> tuple[int, int, list[ImgStats]]:
    """Optimize one .docx. Returns (in_bytes, out_bytes, per-image stats)."""
    in_size = src.stat().st_size

    # We need a stable iteration order so the file list stays deterministic.
    stats: list[ImgStats] = []
    rename_map: dict[str, str] = {}     # basename in word/media -> new basename
    new_extensions: set[str] = set()

    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = dst.with_suffix(dst.suffix + ".tmp")

    with zipfile.ZipFile(src, "r") as zin:
        rewritten_media: dict[str, tuple[str, bytes, int]] = {}

        names = zin.namelist()
        display_rects: dict[str, tuple[int, int]] = {}
        scrubbed_doc_xml: bytes | None = None
        try:
            doc_xml = zin.read("word/document.xml")
            rels_xml = zin.read("word/_rels/document.xml.rels")
            display_rects = parse_display_rects(doc_xml, rels_xml)
            scrubbed_doc_xml = scrub_document_xml(doc_xml)
            if scrubbed_doc_xml == doc_xml:
                scrubbed_doc_xml = None
        except KeyError:
            display_rects = {}

        for name in names:
            if not name.startswith("word/media/"):
                continue
            data = zin.read(name)
            basename = name.split("/")[-1]
            new_bytes, new_basename, tag = process_image(
                basename, data, display_rects.get(basename),
            )
            if new_basename and new_basename != basename:
                rename_map[basename] = new_basename
                final_name = "word/media/" + new_basename
            else:
                final_name = name
            ext = final_name.rsplit(".", 1)[-1].lower()
            new_extensions.add(ext)
            # JPEG/PNG are already compressed; using ZIP_STORED avoids a tiny
            # double-deflate cost but ZIP_DEFLATED with level 1 is fine too.
            # Office tolerates either; we use STORED for media (matches Word).
            compress = zipfile.ZIP_STORED if ext in {"jpg", "jpeg", "png", "gif", "webp"} else zipfile.ZIP_DEFLATED
            rewritten_media[name] = (final_name, new_bytes, compress)
            stats.append(ImgStats(
                name=basename,
                in_bytes=len(data),
                out_bytes=len(new_bytes),
                action=tag,
                new_name=new_basename,
            ))

        # Pass 2: write new zip
        with zipfile.ZipFile(tmp_out, "w", compression=zipfile.ZIP_DEFLATED,
                              compresslevel=6) as zout:
            for info in zin.infolist():
                name = info.filename
                if name in rewritten_media:
                    final_name, blob, compress = rewritten_media[name]
                    # Preserve datetime if present; otherwise leave default
                    new_info = zipfile.ZipInfo(final_name, date_time=info.date_time or (1980, 1, 1, 0, 0, 0))
                    new_info.compress_type = compress
                    new_info.external_attr = info.external_attr
                    zout.writestr(new_info, blob)
                    continue

                if name == "word/document.xml" and scrubbed_doc_xml is not None:
                    new_info = zipfile.ZipInfo(name, date_time=info.date_time or (1980, 1, 1, 0, 0, 0))
                    new_info.compress_type = zipfile.ZIP_DEFLATED
                    new_info.external_attr = info.external_attr
                    zout.writestr(new_info, scrubbed_doc_xml)
                    continue

                if name == "[Content_Types].xml":
                    xml = zin.read(name)
                    xml = ensure_content_types(xml, new_extensions)
                    new_info = zipfile.ZipInfo(name, date_time=info.date_time or (1980, 1, 1, 0, 0, 0))
                    new_info.compress_type = zipfile.ZIP_DEFLATED
                    new_info.external_attr = info.external_attr
                    zout.writestr(new_info, xml)
                    continue

                if name.startswith("word/") and name.endswith(".rels"):
                    xml = zin.read(name)
                    xml = patch_rels_xml(xml, rename_map)
                    new_info = zipfile.ZipInfo(name, date_time=info.date_time or (1980, 1, 1, 0, 0, 0))
                    new_info.compress_type = zipfile.ZIP_DEFLATED
                    new_info.external_attr = info.external_attr
                    zout.writestr(new_info, xml)
                    continue

                # Copy everything else verbatim, but re-deflate text-ish parts
                # to maximise ratio.
                data = zin.read(name)
                new_info = zipfile.ZipInfo(name, date_time=info.date_time or (1980, 1, 1, 0, 0, 0))
                new_info.compress_type = zipfile.ZIP_DEFLATED
                new_info.external_attr = info.external_attr
                zout.writestr(new_info, data)

    tmp_out.replace(dst)
    out_size = dst.stat().st_size

    if verbose:
        for s in stats:
            arrow = f" -> {s.new_name}" if s.new_name else ""
            print(f"  [{s.action:>22s}] {s.name}{arrow}: "
                  f"{fmt_bytes(s.in_bytes):>9s} -> {fmt_bytes(s.out_bytes):>9s}")

    return in_size, out_size, stats


def fmt_bytes(n: int) -> str:
    size: float = n
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f}{unit}" if unit != "B" else f"{int(size)}B"
        size /= 1024
    return f"{size:.1f}GB"


# ---------- discovery / driver ------------------------------------------------


def iter_docx_files(root: Path) -> Iterable[Path]:
    if root.is_file() and root.suffix.lower() == ".docx":
        yield root
        return
    if not root.exists():
        return
    for p in sorted(root.rglob("*.docx")):
        if p.name.startswith("~$"):  # Word lock files
            continue
        yield p


def relative_under(child: Path, parent: Path) -> Path:
    return child.resolve().relative_to(parent.resolve())


def _read_frontmatter(md: Path) -> dict:
    """Pull the YAML frontmatter block out of a markdown file. Returns {} if
    there is none."""
    text = md.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end < 0:
        return {}
    return yaml.safe_load(text[4:end]) or {}


def _build_path_map_from_manifest(content_root: Path, legacy_root: Path) -> dict[Path, Path]:
    """Map legacy DOCX sources to optimized DOCX destinations using
    conversion-manifest provenance.

    This is the preferred path: source filenames are conversion provenance,
    not content frontmatter.
    """
    if not MANIFEST_PATH.exists():
        return {}
    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

    folder_for_kind = {"book": "books", "poem": "poetry", "project": "projects"}
    out: dict[Path, Path] = {}
    by_work = manifest.get("by_work")
    if not isinstance(by_work, dict):
        return out

    for entry in by_work.values():
        if not isinstance(entry, dict):
            continue
        kind = entry.get("kind")
        slug = entry.get("slug")
        sources = entry.get("sources")
        folder = folder_for_kind.get(str(kind))
        if not folder or not isinstance(slug, str) or not isinstance(sources, dict):
            continue
        for lang, raw_entries in sources.items():
            if not isinstance(lang, str) or not isinstance(raw_entries, list):
                continue
            docx_entries = [
                item for item in raw_entries
                if isinstance(item, dict)
                and isinstance(item.get("path"), str)
                and item["path"].endswith(".docx")
            ]
            total = len(docx_entries)
            for idx, item in enumerate(docx_entries, start=1):
                src = (ROOT / item["path"]).resolve()
                if not src.is_file():
                    continue
                suffix = f"{lang}.docx" if total <= 1 else f"{lang}-part{idx}.docx"
                out[src] = content_root / folder / slug / suffix
    return out


def build_path_map(content_root: Path, legacy_root: Path) -> dict[Path, Path]:
    """Walk the content tree and produce a map from resolved legacy source
    paths to content destination paths. Keys are full resolved paths so
    same-named files in different folders (e.g. two ``source.docx`` under
    different projects) don't collide.

    ``data/conversion-manifest.json`` source provenance is authoritative. A
    frontmatter ``original_filename`` fallback is retained for poetry/projects so
    the optimizer can run against trees whose manifest predates path-level
    sources. (The former in-bundle ``meta.json`` fallback is gone — provenance
    lives in the central manifest, not the work bundle.)

    Resolution by kind:
      books    — manifest sources; the source lives at
                 ``legacy/books/<lang>/<filename>``.
      poetry   — manifest sources, or frontmatter's ``original_filename`` is the file's basename;
                 the source lives somewhere under ``legacy/poetry/<folder>/``
                 where ``<folder>`` shares the same numeric prefix as the
                 content slug (e.g. ``02-...`` ↔ ``02. ...``). When that's
                 ambiguous we fall back to a direct filename match.
      projects — manifest sources, or content slug equals the legacy folder name exactly, so the
                 source is ``legacy/projects/<slug>/<filename>``.

    Destination naming: ``<lang>.docx`` for single-source entries,
    ``<lang>-part<N>.docx`` for multi-source.
    """
    out = _build_path_map_from_manifest(content_root, legacy_root)
    if out:
        return out

    out: dict[Path, Path] = {}

    # Books carry no frontmatter source-filename fallback: their source
    # provenance lives only in the central manifest (handled above). The former
    # in-bundle meta.json fallback was removed with the move to manifest-only
    # provenance.

    # --- poetry ----------------------------------------------------------
    for md_path in sorted((content_root / "poetry").glob("*/*.md")):
        slug = md_path.parent.name
        lang = md_path.stem
        fm = _read_frontmatter(md_path)
        single = fm.get("original_filename")
        names = fm.get("original_filenames") or ([single] if single else [])
        total = len(names)
        # Prefer legacy folders whose number prefix matches the content slug's.
        slug_num = slug.split("-", 1)[0]
        for idx, name in enumerate(names, start=1):
            candidates = list((legacy_root / "poetry").glob(f"*/{name}"))
            if not candidates:
                continue
            if len(candidates) > 1:
                tight = [c for c in candidates if c.parent.name.startswith(f"{slug_num}.")
                                              or c.parent.name.startswith(f"{slug_num} ")]
                candidates = tight or candidates
            if len(candidates) != 1:
                print(f"warn: ambiguous poetry source for {slug}/{name} "
                      f"({len(candidates)} candidates)", file=sys.stderr)
                continue
            suffix = f"{lang}.docx" if total <= 1 else f"{lang}-part{idx}.docx"
            out[candidates[0].resolve()] = content_root / "poetry" / slug / suffix

    # --- projects --------------------------------------------------------
    for md_path in sorted((content_root / "projects").glob("*/*.md")):
        slug = md_path.parent.name
        lang = md_path.stem
        fm = _read_frontmatter(md_path)
        single = fm.get("original_filename")
        names = fm.get("original_filenames") or ([single] if single else [])
        total = len(names)
        for idx, name in enumerate(names, start=1):
            src = legacy_root / "projects" / slug / name
            if not src.is_file():
                continue
            suffix = f"{lang}.docx" if total <= 1 else f"{lang}-part{idx}.docx"
            out[src.resolve()] = content_root / "projects" / slug / suffix

    return out


def optimize(
    *,
    paths: list[Path],
    force: bool = False,
    verbose: bool = False,
    dry_run: bool = False,
) -> int:
    path_map = build_path_map(CONTENT_ROOT, ROOT / "legacy")
    jobs: list[tuple[Path, Path]] = []
    unmapped: list[Path] = []

    def queue(src: Path) -> None:
        dst = path_map.get(src.resolve())
        if dst is None:
            unmapped.append(src)
            return
        jobs.append((src, dst))

    if paths:
        for raw in paths:
            p = Path(raw).resolve()
            if p.is_file():
                queue(p)
            elif p.is_dir():
                for f in iter_docx_files(p):
                    queue(f)
            else:
                print(f"warn: not found: {p}", file=sys.stderr)
    else:
        for src_root in SOURCE_ROOTS:
            for f in iter_docx_files(src_root):
                queue(f)

    if unmapped:
        print(f"warn: {len(unmapped)} source file(s) have no src/content/ entry "
              f"and will be skipped (e.g. {unmapped[0].name})", file=sys.stderr)

    if not jobs:
        print("no .docx files found")
        return 0

    total_in = 0
    total_out = 0
    skipped = 0
    processed = 0
    written: list[Path] = []

    for src, dst in jobs:
        if not force and dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
            in_size = src.stat().st_size
            out_size = dst.stat().st_size
            total_in += in_size
            total_out += out_size
            skipped += 1
            written.append(dst)
            print(f"SKIP   {src.name}: {fmt_bytes(in_size):>9s} -> {fmt_bytes(out_size):>9s} (cached)")
            continue
        if dry_run:
            print(f"WOULD  {src} -> {dst}")
            continue
        t0 = time.time()
        try:
            in_size, out_size, _ = optimize_docx(src, dst, verbose=verbose)
        except Exception as e:
            print(f"FAIL   {src}: {e}", file=sys.stderr)
            continue
        dt = time.time() - t0
        pct = 100 * (1 - out_size / in_size) if in_size else 0
        total_in += in_size
        total_out += out_size
        processed += 1
        written.append(dst)
        print(f"OK     {src.name}: {fmt_bytes(in_size):>9s} -> {fmt_bytes(out_size):>9s}  "
              f"({pct:5.1f}% saved, {dt:4.1f}s)")

    if not dry_run:
        added = update_manifest_generated_paths(written)
        if added:
            print(f"manifest: registered {added} optimized-docx path(s) into by_work.generated_paths")

    if total_in:
        pct = 100 * (1 - total_out / total_in)
        print(f"\nSUMMARY: {processed} processed, {skipped} cached, "
              f"{fmt_bytes(total_in)} -> {fmt_bytes(total_out)}  ({pct:.1f}% saved)")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[1])
    ap.add_argument("paths", nargs="*",
                    help="Specific .docx files or directories. Defaults to the corpus source roots.")
    ap.add_argument("--force", action="store_true",
                    help="Re-process even if dst is newer than src.")
    ap.add_argument("--verbose", "-v", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print what would be done; don't write outputs.")
    ns = ap.parse_args(argv)
    return optimize(
        paths=[Path(p) for p in ns.paths],
        force=ns.force,
        verbose=ns.verbose,
        dry_run=ns.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
