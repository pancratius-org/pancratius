#!/usr/bin/env -S uv run --quiet
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "pyyaml>=6.0",
# ]
# ///
"""
docx_to_md.py — convert Sergey Orekhov's legacy .docx corpus
to work-bundle Markdown with structured frontmatter and co-located assets.

Per .docx pipeline:
  1. pandoc → GFM markdown + extracted media
  2. lift bibliography tables → ./bibliography.yaml sidecar
  3. content-addressed body-image dedup inside ./images/<hash>.<ext>
     (relative to the work folder)
  4. extract footnote and inline cross-references → frontmatter cross_refs
  5. scrub Word's TOC blocks, AI alt-text, rights boilerplate (bounded), HTML
     residue (`<u>`, anchor spans, `[]{#…}`, smallcaps), `**\\**` artifacts,
     empty headings
  6. emit ASCII-slug work bundles under src/content/<kind>/<ascii-slug>/ with
     <lang>.md, cover.<lang>.<ext>, optional bibliography.yaml, meta.json,
     and images/. Frontmatter satisfies src/content.config.ts strict schema.

The converter is additive by default: every write is recorded in
data/conversion-manifest.json under by_work[<kind/slug>].generated_paths
(relative to the work folder), and reruns only delete stale entries the new
run does not reproduce. Unknown author-added neighbors survive. `--clean` is
the explicit destructive maintenance path; it removes only the selected work
bundles before regenerating them.

Run:
    uv run scripts/docx_to_md.py --kind book --kind poem
    uv run scripts/docx_to_md.py --kind book --number 33
    uv run scripts/docx_to_md.py --kind poem --number 2
    uv run scripts/docx_to_md.py --test
    uv run scripts/docx_to_md.py --kind book --kind poem --clean
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote
import xml.etree.ElementTree as ET

import yaml

ROOT = Path(__file__).resolve().parent.parent
LEGACY = ROOT / "legacy"
DATA = LEGACY / "data"

CONTENT_OUT = ROOT / "src" / "content"
MANIFEST_PATH = ROOT / "data" / "conversion-manifest.json"

TEST_CONTENT_OUT = ROOT / ".cache" / "converter-test" / "src" / "content"
TEST_MANIFEST_PATH = ROOT / ".cache" / "converter-test" / "conversion-manifest.json"

HASH_PREFIX_LEN = 12
PANDOC_FORMAT = "gfm"

EXT_FROM_MIME = {".jpeg": ".jpg", ".jpe": ".jpg"}

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".tiff", ".emf", ".wmf")


# Why: the corpus uses Cyrillic ASCII-ish slugs from the legacy site
# (e.g. `тои` for `той`, `выи` for `вый`). We freeze a practical
# transliteration that matches that historical choice so existing slugs
# round-trip stably to ASCII without ё/й/ц collisions.
CYR_TO_LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "zh", "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def transliterate(s: str) -> str:
    out: list[str] = []
    for ch in s.lower():
        if ch in CYR_TO_LAT:
            out.append(CYR_TO_LAT[ch])
        else:
            out.append(ch)
    return "".join(out)


_SLUG_NONALNUM = re.compile(r"[^a-z0-9]+")
_SLUG_DASHES = re.compile(r"-+")


def to_ascii_slug(s: str) -> str:
    s = transliterate(s)
    s = unicodedata.normalize("NFKD", s)
    s = s.encode("ascii", "ignore").decode("ascii")
    s = _SLUG_NONALNUM.sub("-", s.lower())
    s = _SLUG_DASHES.sub("-", s).strip("-")
    return s


# Why: book #2 is a merged trilogy. The library entry stores the first part's
# label as the work title; the merged work needs its own canonical title.
TITLE_OVERRIDES_BY_NUMBER: dict[int, dict[str, str]] = {
    2: {"ru": "Маленький Царь", "en": "The Kingdom Within"},
}


# Why: book #2 is a merged three-part work. Legacy's top-level annotation is a
# part-2 store blurb, so the merged work needs one work-level description.
DESCRIPTION_OVERRIDES_BY_NUMBER: dict[int, dict[str, str]] = {
    2: {
        "ru": (
            "«Маленький Царь» объединяет три части о Сергее и Царствии внутри: "
            "от детского незабвения Света и живого мира вокруг него, через "
            "встречу с теми, кто тоже начинает слышать Тишину, к возвращению "
            "к себе и Присутствию. Это не обычная сказка и не приключение, "
            "а тихий путь узнавания: всё живое, Царство внутри, а истина "
            "открывается сердцем."
        ),
        "en": (
            "The Kingdom Within brings together three parts about Sergey and "
            "the Kingdom within: from a child's memory of the Light and the "
            "living world around him, through encounters with others who begin "
            "to hear Silence, toward a return to the self and Presence. It is "
            "not an ordinary fairy tale or adventure, but a quiet path of "
            "recognition: everything is alive, the Kingdom is within, and truth "
            "is received by the heart."
        ),
    },
}


# Why: projects carry an editorial number to satisfy the (kind, number) invariant.
PROJECT_NUMBERS: dict[str, int] = {
    "enlightened-ai": 1,
    "holy-rus": 2,
}


# !!! TEMPORARY — see editorial.yaml header for the migration plan.
# This loader and the YAML file should both go away once the converter
# learns to preserve editor-owned frontmatter fields (title, description,
# abstract, translation, cross_refs) on existing markdown. At that point
# editorial.yaml is applied once into each en.md and then deleted; the
# residual review checklist lives in docs/editorial-notes.md. Don't bless
# this as architecture.
EDITORIAL_PATH = ROOT / "editorial.yaml"


def _load_en_titles() -> dict[int, str]:
    if not EDITORIAL_PATH.exists():
        return {}
    data = yaml.safe_load(EDITORIAL_PATH.read_text(encoding="utf-8")) or {}
    raw = data.get("en_titles") or {}
    return {int(k): str(v) for k, v in raw.items() if str(v).strip()}


EN_TITLE_OVERRIDES_BY_NUMBER: dict[int, str] = _load_en_titles()


# Why: AI image generators leave a verbose alt text in DOCX. Strip it (or its
# truncation) — the surface form is consistent. Keep `alt=""` so screen
# readers don't read filenames.
AI_ALT_FRAGMENTS = (
    "Содержимое, созданное искусственным интеллектом",
    "Содержимое создано искусственным интеллектом",
    "Content created by AI",
    "Изображение выглядит как",
    "AI-generated content may be incorrect",
    "может быть неверным",
)


# Why: rights-boilerplate scrub is bounded to the first 3% of body OR the
# region before the first H1, whichever comes first. The patterns are anchored
# at line starts, with explicit short maximum spans — never `.*?` across
# arbitrary content.
RIGHTS_PATTERNS = [
    re.compile(r"(?im)^\s*Copyright\s+©.*$"),
    re.compile(r"(?im)^\s*All rights reserved\.?\s*$"),
    re.compile(r"(?im)^\s*©\s*\d{4}.*$"),
    re.compile(r"(?im)^\s*No part of this book may be reproduced.*$"),
    re.compile(r"(?im)^\s*The characters and events portrayed.*coincidental.*$"),
    re.compile(r"(?im)^\s*Все\s+права\s+защищены\.?\s*$"),
    re.compile(r"(?im)^\s*Никакая\s+часть\s+(этой|данной)\s+книги.*$"),
    re.compile(r"(?im)^\s*Воспроизведение\s+(или\s+)?распространение.*запрещ.*$"),
]


# Why: TOC link lines have a consistent pandoc shape. The previous heuristic
# fired only when the block sat at the very top of the file; in practice some
# books have prologue paragraphs above the auto-TOC, so we look anywhere and
# match a contiguous run of ≥3 TOC lines (with an optional preceding heading).
_TOC_LINE = re.compile(r"^\[.+?\[\d+\]\(#[^)]+\)\]\(#[^)]+\)\s*$")
_TOC_HEADING_LINE = re.compile(
    r"^#{1,6}\s+(?:оглавление|содержание|table\s+of\s+contents|contents)\s*$",
    re.IGNORECASE,
)
_BARE_TOC_ANCHOR_LINE = re.compile(r"^_Toc\d+$", re.IGNORECASE)
_BIBLIO_HEADING_LINE = re.compile(
    r"^#{1,6}\s+(?:библиография|bibliography|список\s+литературы|литература)\s*$",
    re.IGNORECASE,
)
_BIBLIO_SECTION_TELL = re.compile(
    r"<img\b|!\[[^\]]*\]\([^)]+\)|<table\b|litres\.ru|kindbook\.net|"
    r"Книги\s+автора|Books\s+by\s+the\s+author",
    re.IGNORECASE,
)

_IMG_MD = re.compile(r"!\[([^\]]*)\]\(([^)]+?)\)")
_BODY_IMG_MD = re.compile(r"!\[[^\]]*\]\(\./images/[^)\s]+(?:\s+\"[^\"]*\")?\)")
_IMG_HTML = re.compile(r"<img\s+([^>]*?)src\s*=\s*\"([^\"]+)\"([^>]*?)/?>", re.IGNORECASE)
_HTML_DIM_ATTR = re.compile(r"\s+(?:style|width|height)\s*=\s*\"[^\"]*\"")
_HTML_ALT_ATTR = re.compile(r"\balt\s*=\s*\"([^\"]*)\"", re.IGNORECASE)

_LITRES_URL = re.compile(r"https?://(?:www\.)?litres\.ru/[\w\-/]+")
_FOOTNOTE_LINE = re.compile(r"^\[\^([^\]]+)\]:\s*(.+)$", re.MULTILINE)
_INLINE_BOOK_TITLE = re.compile(r"книг[аеу]\s+«([^»]{3,80})»")
_EN_INLINE_BOOK_TITLE = re.compile(r"the\s+book\s+\"([^\"]{3,80})\"", re.IGNORECASE)


def _strip_js_wrapper(text: str) -> str:
    eq = text.index("=")
    body = text[eq + 1:].strip()
    if body.endswith(";"):
        body = body[:-1]
    return body


def load_library() -> dict[str, Any]:
    return json.loads(_strip_js_wrapper((DATA / "library-data.js").read_text(encoding="utf-8")))


def load_poetry() -> dict[str, Any]:
    return json.loads(_strip_js_wrapper((DATA / "poetry-data.js").read_text(encoding="utf-8")))


def load_projects() -> dict[str, Any]:
    return json.loads(_strip_js_wrapper((DATA / "projects-data.js").read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# image dedup + records
# ---------------------------------------------------------------------------

@dataclass
class ImageRecord:
    book_slug: str
    image_index: int
    original_filename: str
    media_hash: str
    ext: str
    bytes: int
    role: str = "body"  # cover | body | bibliography_thumb


@dataclass
class ConversionOutcome:
    ascii_slug: str
    lang: str
    md_path: Path
    images: list[ImageRecord] = field(default_factory=list)


# Why: every converter-written file is recorded per work so a rerun can
# delete only what it previously generated and preserve unknown author-added
# neighbors (see docs/architecture.md "additive by default" rule). Paths are
# work-folder-relative so the manifest is portable across `--out-content`
# scratch directories.
@dataclass
class WorkWrites:
    kind: str
    slug: str
    work_dir: Path
    paths: set[str] = field(default_factory=set)
    sources: dict[str, list[dict[str, str]]] = field(default_factory=dict)

    def add(self, p: Path) -> None:
        self.paths.add(p.relative_to(self.work_dir).as_posix())

    def add_source(self, lang: str, p: Path) -> None:
        rel = p.resolve().relative_to(ROOT).as_posix()
        self.sources.setdefault(lang, [])
        record = {"path": rel, "filename": p.name}
        if record not in self.sources[lang]:
            self.sources[lang].append(record)


def _load_previous_works(path: Path) -> dict[str, list[str]]:
    """Read the converter's prior `generated_paths` per work key, scoped to
    its owner slot. Returns `{<kind/slug>: [<work-folder-relative path>]}`."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    by_work = data.get("by_work") or {}
    result: dict[str, list[str]] = {}
    for key, entry in by_work.items():
        if not isinstance(entry, dict):
            continue
        gp = entry.get("generated_paths") or {}
        if isinstance(gp, dict):
            mine = gp.get(WORK_OWNER) or []
            if isinstance(mine, list):
                result[key] = [str(p) for p in mine]
    return result


# Why: explicit per-path ownership in the manifest avoids any heuristic about
# which script wrote a file. The converter only reconciles paths recorded
# under its own owner slot; other slots (notably `docx_optimize` for the
# downloadable .docx files) are untouched.
WORK_OWNER = "docx_to_md"


def _reconcile_stale(writes: WorkWrites, previous_works: dict[str, list[str]]) -> int:
    key = f"{writes.kind}/{writes.slug}"
    removed = 0
    for rel in previous_works.get(key, []):
        rel_path = Path(rel)
        if rel_path.is_absolute() or ".." in rel_path.parts:
            continue
        if rel in writes.paths:
            continue
        p = writes.work_dir / rel_path
        if p.is_file():
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
    if removed and writes.work_dir.is_dir():
        # Why: clean up image-directory shells the converter left empty after
        # removing stale hashes; preserve any dir that still has an author-named
        # neighbor.
        for sub in sorted(
            (p for p in writes.work_dir.rglob("*") if p.is_dir()), reverse=True,
        ):
            try:
                next(sub.iterdir())
            except StopIteration:
                sub.rmdir()
            except OSError:
                pass
    return removed


def _normalize_ext(ext: str) -> str:
    ext = ext.lower()
    return EXT_FROM_MIME.get(ext, ext)


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()[:HASH_PREFIX_LEN]


def _is_image_path(p: str) -> bool:
    return any(p.lower().endswith(e) for e in IMAGE_EXTS)


def _run_pandoc(docx: Path, media_dir: Path, out_md: Path) -> str:
    cmd = [
        "pandoc",
        "--from", "docx",
        "--to", PANDOC_FORMAT,
        "--wrap=none",
        "--markdown-headings=atx",
        "--extract-media", str(media_dir),
        "-o", str(out_md),
        str(docx),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"pandoc failed on {docx.name}: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stderr.strip()


def _run_pandoc_json(docx: Path, media_dir: Path | None = None) -> tuple[dict[str, Any], str]:
    cmd = [
        "pandoc",
        "--from", "docx+empty_paragraphs",
        "--to", "json",
    ]
    if media_dir is not None:
        cmd.extend(["--extract-media", str(media_dir)])
    cmd.append(str(docx))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"pandoc failed on {docx.name}: {proc.stderr.strip() or proc.stdout.strip()}")
    return json.loads(proc.stdout), proc.stderr.strip()


# ---------------------------------------------------------------------------
# DOCX source metadata — signals Pandoc's Markdown writer drops
# ---------------------------------------------------------------------------

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"


@dataclass(frozen=True)
class DocxParagraphMeta:
    text: str
    align: str
    style: str
    bold: bool
    italic: bool
    line_breaks: int

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


def _w_val(el: ET.Element | None) -> str:
    if el is None:
        return ""
    return str(el.get(f"{W}val") or "")


def _run_prop_enabled(el: ET.Element | None) -> bool:
    if el is None:
        return False
    val = el.get(f"{W}val")
    return val not in {"0", "false", "False", "off"}


def read_docx_paragraph_meta(docx: Path) -> list[DocxParagraphMeta]:
    """Read paragraph-level Word metadata that Markdown cannot carry.

    Pandoc is still the content converter. This pass only captures narrow
    source signals that are otherwise lost, especially paragraph alignment.
    """
    with zipfile.ZipFile(docx) as zf:
        root = ET.fromstring(zf.read("word/document.xml"))

    paras: list[DocxParagraphMeta] = []
    for p in root.iter(f"{W}p"):
        text_parts: list[str] = []
        line_breaks = 0
        for el in p.iter():
            if el.tag == f"{W}t":
                text_parts.append(el.text or "")
            elif el.tag in {f"{W}br", f"{W}cr"}:
                text_parts.append("\n")
                line_breaks += 1
            elif el.tag == f"{W}tab":
                text_parts.append("\t")

        ppr = p.find(f"{W}pPr")
        style = _w_val(ppr.find(f"{W}pStyle") if ppr is not None else None)
        align = _w_val(ppr.find(f"{W}jc") if ppr is not None else None)
        bold = any(_run_prop_enabled(el) for el in p.findall(f".//{W}b"))
        italic = any(_run_prop_enabled(el) for el in p.findall(f".//{W}i"))
        paras.append(DocxParagraphMeta(
            text="".join(text_parts).strip(),
            align=align,
            style=style,
            bold=bold,
            italic=italic,
            line_breaks=line_breaks,
        ))
    return paras


# ---------------------------------------------------------------------------
# cleanup passes — each returns the transformed markdown
# ---------------------------------------------------------------------------

def strip_toc(md: str) -> str:
    """Drop contiguous blocks of pandoc-generated TOC link lines and the
    matching `# Оглавление`/`# Contents` heading immediately preceding them.
    Also strip bare `_TocXXXX` anchor-id lines anywhere."""
    lines = md.splitlines()
    n = len(lines)
    keep = [True] * n
    i = 0
    while i < n:
        ln = lines[i].strip()
        if _BARE_TOC_ANCHOR_LINE.match(ln):
            keep[i] = False
            i += 1
            continue
        if _TOC_LINE.match(ln):
            j = i
            while j < n and (_TOC_LINE.match(lines[j].strip()) or lines[j].strip() == ""):
                j += 1
            count = sum(1 for k in range(i, j) if _TOC_LINE.match(lines[k].strip()))
            if count >= 3:
                k = i - 1
                while k >= 0 and lines[k].strip() == "":
                    k -= 1
                if k >= 0 and _TOC_HEADING_LINE.match(lines[k].strip()):
                    keep[k] = False
                for m in range(i, j):
                    keep[m] = False
                i = j
                continue
        i += 1
    return "\n".join(l for l, k in zip(lines, keep) if k)


def strip_bibliography_sections(md: str) -> str:
    """Remove body bibliography/catalog sections before image rewriting.

    The corpus has several endmatter "Библиография" sections that are not
    readable prose: long catalog tables, LitRes link lists, or screenshots of
    book-cover grids. Structured catalog snapshots belong in `bibliography.yaml`
    when parseable; image-only snapshots are simply not reading-page content.
    """
    lines = md.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        if not _BIBLIO_HEADING_LINE.match(lines[i].strip()):
            out.append(lines[i])
            i += 1
            continue

        j = i + 1
        while j < len(lines) and not _ANY_HEADING_RE.match(lines[j].strip()):
            j += 1
        if out and out[-1].strip():
            out.append("")
        i = j
    return "\n".join(out)


def strip_bold_only_headings(md: str) -> str:
    """`# **TEXT**` → `# TEXT`. Also normalize residual setext headings that
    pandoc occasionally emits when a source paragraph carried partial bold
    markup, turning `Title**\n====` into `# Title`."""
    lines = md.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        if i + 1 < len(lines) and re.fullmatch(r"=+\s*", lines[i + 1]) and ln.strip():
            text = ln.strip().rstrip("*").rstrip()
            text = re.sub(r"^\*+\s*", "", text)
            out.append(f"# {text}")
            i += 2
            continue
        if i + 1 < len(lines) and re.fullmatch(r"-{3,}\s*", lines[i + 1]) and ln.strip() and not ln.startswith("#"):
            text = ln.strip().rstrip("*").rstrip()
            text = re.sub(r"^\*+\s*", "", text)
            out.append(f"## {text}")
            i += 2
            continue
        ln = re.sub(r"^(#{1,6})\s+\*\*(.+?)\*\*\s*$", r"\1 \2", ln)
        out.append(ln)
        i += 1
    return "\n".join(out)


def strip_empty_headings(md: str) -> str:
    """Drop lone `# ` / `## ` lines with no heading text."""
    return "\n".join(ln for ln in md.splitlines() if not re.match(r"^#{1,6}\s*$", ln))


def strip_formatting_artifacts(md: str) -> str:
    """Remove `**\\**`, `\\**`, `**\\`, lone backslash lines that pandoc emits
    when a docx run had only whitespace inside emphasis markers."""
    out: list[str] = []
    for ln in md.splitlines():
        s = ln.strip()
        if re.fullmatch(r"(?:\\?\*\s*){3}", s):
            out.append("***")
            continue
        if s in ("**\\**", "\\**", "**\\", "\\", "***\\***", "***\\*", "*\\***"):
            continue
        ln = re.sub(r"\*\*\\\*\*", "", ln)
        ln = re.sub(r"\*\*\*\\\*\*\*", "", ln)
        out.append(ln)
    return "\n".join(out)


def unwrap_spans_and_u(md: str) -> str:
    md = re.sub(r'<span\s+class="smallcaps">([^<]*)</span>', lambda m: m.group(1), md, flags=re.IGNORECASE)
    md = re.sub(r'<span\s+class="underline">([^<]*)</span>', lambda m: m.group(1), md, flags=re.IGNORECASE)
    md = re.sub(r'<span\s+[^>]*class="anchor"[^>]*>\s*</span>', "", md, flags=re.IGNORECASE)
    md = re.sub(r'<span\s+[^>]*class="anchor"[^>]*>([^<]*)</span>', lambda m: m.group(1), md, flags=re.IGNORECASE)
    md = re.sub(r'<span\s+id="[^"]*"\s*></span>', "", md, flags=re.IGNORECASE)
    md = re.sub(r'<span\s+id="[^"]*"[^>]*>([^<]*)</span>', lambda m: m.group(1), md, flags=re.IGNORECASE)
    md = re.sub(r"<u>([^<]*)</u>", lambda m: m.group(1), md, flags=re.IGNORECASE)
    md = re.sub(r"\[\]\{#[^}]+\}", "", md)
    md = re.sub(r"\{#[^}]+\}", "", md)
    return md


def strip_ai_alt(md: str) -> str:
    def fix_md_alt(m: re.Match) -> str:
        alt = m.group(1)
        for frag in AI_ALT_FRAGMENTS:
            if frag in alt:
                return f"![]({m.group(2)})"
        return m.group(0)

    def fix_html_alt(m: re.Match) -> str:
        full = m.group(0)
        alt_m = re.search(r'alt\s*=\s*"([^"]*)"', full, re.IGNORECASE)
        if not alt_m:
            return full
        if any(frag in alt_m.group(1) for frag in AI_ALT_FRAGMENTS):
            return full[:alt_m.start()] + 'alt=""' + full[alt_m.end():]
        return full

    md = _IMG_MD.sub(fix_md_alt, md)
    md = re.sub(r"<img\s+[^>]*>", fix_html_alt, md, flags=re.IGNORECASE)
    return md


def scrub_rights_boilerplate(md: str) -> str:
    """Bounded copyright scrub. Limits the scan window to (a) before the first
    H1 heading or (b) the first 3% of the file, whichever comes first."""
    lines = md.splitlines()
    n = len(lines)
    if n == 0:
        return md
    first_h1 = next((i for i, ln in enumerate(lines) if re.match(r"^#\s+\S", ln)), n)
    window_end = min(first_h1, max(20, int(n * 0.03)))
    body_head = "\n".join(lines[:window_end])
    for pat in RIGHTS_PATTERNS:
        body_head = pat.sub("", body_head)
    body_head_lines = body_head.splitlines()
    return "\n".join(body_head_lines + lines[window_end:])


def collapse_blank_lines(md: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"


def strip_trailing_hardbreak_markers(md: str) -> str:
    """Remove Pandoc's author-hostile hard-break backslashes.

    Source Markdown keeps natural newlines. If a short-line run needs visual
    lineation, the AST passes wrap it as `.verse-block` / `.answer-block`
    instead of leaving raw hard-break syntax in the author-facing file.
    """
    out: list[str] = []
    in_fence = False
    for line in md.splitlines():
        if re.match(r"^\s*(```|~~~)", line):
            in_fence = not in_fence
            out.append(line)
            continue
        if not in_fence and re.search(r"(?<!\\)\\[ \t]*$", line):
            out.append(re.sub(r"(?<!\\)\\[ \t]*$", "", line).rstrip())
        else:
            out.append(line)
    return "\n".join(out)


def demote_markdown_headings(md: str, levels: int) -> str:
    """Demote body headings so the page title remains the only H1.

    For a normal work, source H1 becomes H2. For a merged multi-part book,
    inserted `## Part N` headings own the body top level, so source H1 becomes
    H3 and the reading-page ToC can show both parts and chapters.
    """
    if levels <= 0:
        return md
    out: list[str] = []
    in_fence = False
    for line in md.splitlines():
        if line.startswith("```") or line.startswith("~~~"):
            in_fence = not in_fence
            out.append(line)
            continue
        if not in_fence:
            m = re.match(r"^(#{1,6})(\s+.+)$", line)
            if m:
                out.append(f"{'#' * min(6, len(m.group(1)) + levels)}{m.group(2)}")
                continue
        out.append(line)
    return "\n".join(out)


_DEDICATION_HEADING_RE = re.compile(r"^#{2,6}\s+(?:Посвящение|Dedication):?\s*$", re.IGNORECASE)
_ANY_HEADING_RE = re.compile(r"^#{1,6}\s+\S")
_VERSE_SECTION_TITLE_RE = re.compile(
    r"^(?:"
    r"посвящение|dedication|"
    r"предисловие\s+от\s+творца|preface\s+(?:from|by)\s+the\s+creator|"
    r"слово\s+творца|the\s+word\s+of\s+the\s+creator|creator'?s\s+word|"
    r"голос\s+творца|voice\s+of\s+the\s+creator|"
    r"ответ\s+творца|creator'?s\s+answer|"
    r"пояснение\s+творца|annotation\s+from\s+the\s+creator|"
    r"благословляющее\s+слово\s+творца|"
    r"молитва|prayer|псалом|psalm"
    r")\b",
    re.IGNORECASE,
)


def _is_short_plain_verse_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return True
    if len(s) > 120:
        return False
    if _ANY_HEADING_RE.match(s):
        return False
    if s.startswith(("!", "<", "|", ">")):
        return False
    if re.match(r"^[-*+]\s+", s) or re.match(r"^\d+[.)]\s+", s):
        return False
    if re.match(r"^\*\*[^*]{1,80}:\*\*", s):
        return False
    return True


def normalize_dedication_verse_sections(md: str) -> str:
    """Render short dedication sections as compact verse blocks.

    Several books open with a dedication that Pandoc emits as one normal
    paragraph per line. CSS cannot reliably infer that from plain `<p>` tags,
    and prose drop caps make it worse. Keep the Markdown body honest by adding
    one explicit HTML block for this narrow, named section.
    """
    lines = md.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        if not _DEDICATION_HEADING_RE.match(line.strip()):
            i += 1
            continue

        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        k = j
        section: list[str] = []
        while k < len(lines) and not _ANY_HEADING_RE.match(lines[k].strip()):
            section.append(lines[k])
            k += 1

        verse_lines = [ln.strip() for ln in section if ln.strip()]
        if (
            2 <= len(verse_lines) <= 24
            and section
            and all(_is_short_plain_verse_line(ln) for ln in section)
        ):
            out.append("")
            out.append('<div class="verse-block">')
            out.extend(html.escape(ln, quote=False) for ln in verse_lines)
            out.append("</div>")
            out.append("")
            i = k
            continue

        i += 1
    return "\n".join(out)


def _plain_text_inlines(inlines: list[dict[str, Any]]) -> str:
    out: list[str] = []
    for item in inlines:
        typ = item.get("t")
        val = item.get("c")
        if typ == "Str":
            out.append(str(val))
        elif typ in {"Space", "SoftBreak", "LineBreak"}:
            out.append(" ")
        elif typ in {"Strong", "Emph", "Underline", "Strikeout", "Superscript", "Subscript", "SmallCaps"}:
            out.append(_plain_text_inlines(val or []))
        elif typ == "Quoted":
            _quote_type, quoted = val
            out.append(_plain_text_inlines(quoted))
        elif typ == "Code":
            out.append(str(val[1]))
        elif typ == "Link":
            _attr, label, _target = val
            out.append(_plain_text_inlines(label))
        elif typ == "Image":
            _attr, label, _target = val
            out.append(_plain_text_inlines(label))
        elif typ == "Span":
            _attr, span_inlines = val
            out.append(_plain_text_inlines(span_inlines))
        elif isinstance(val, list):
            out.append(_plain_text_inlines(val))
    return "".join(out).strip()


def _pandoc_inlines_to_html(inlines: list[dict[str, Any]]) -> str:
    out: list[str] = []
    for item in inlines:
        typ = item.get("t")
        val = item.get("c")
        if typ == "Str":
            out.append(html.escape(str(val), quote=False))
        elif typ == "Space":
            out.append(" ")
        elif typ in {"SoftBreak", "LineBreak"}:
            out.append("<br>\n")
        elif typ == "Strong":
            out.append(f"<strong>{_pandoc_inlines_to_html(val or [])}</strong>")
        elif typ == "Emph":
            out.append(f"<em>{_pandoc_inlines_to_html(val or [])}</em>")
        elif typ in {"Underline", "SmallCaps"}:
            out.append(_pandoc_inlines_to_html(val or []))
        elif typ == "Strikeout":
            out.append(f"<s>{_pandoc_inlines_to_html(val or [])}</s>")
        elif typ == "Superscript":
            out.append(f"<sup>{_pandoc_inlines_to_html(val or [])}</sup>")
        elif typ == "Subscript":
            out.append(f"<sub>{_pandoc_inlines_to_html(val or [])}</sub>")
        elif typ == "Quoted":
            quote_type, quoted = val
            inner = _pandoc_inlines_to_html(quoted)
            if quote_type.get("t") == "SingleQuote":
                out.append(f"'{inner}'")
            else:
                out.append(f"«{inner}»")
        elif typ == "Code":
            out.append(f"<code>{html.escape(str(val[1]), quote=False)}</code>")
        elif typ == "Link":
            _attr, label, target = val
            label_html = _pandoc_inlines_to_html(label)
            href = html.escape(str(target[0]), quote=True)
            out.append(f'<a href="{href}">{label_html}</a>')
        elif typ == "Image":
            _attr, label, target = val
            alt = html.escape(_plain_text_inlines(label), quote=True)
            src = html.escape(str(target[0]), quote=True)
            out.append(f'<img src="{src}" alt="{alt}">')
        elif typ == "Span":
            _attr, span_inlines = val
            out.append(_pandoc_inlines_to_html(span_inlines))
        elif typ == "RawInline":
            fmt, raw = val
            if fmt == "html":
                out.append(str(raw))
            elif fmt == "markdown":
                out.append(html.escape(str(raw), quote=False))
        elif isinstance(val, list):
            out.append(_pandoc_inlines_to_html(val))
    return "".join(out)


def _merge_html_lines(lines: list[str], child_lines: list[str]) -> None:
    for idx, child in enumerate(child_lines):
        if idx:
            lines.append("")
        lines[-1] += child


def _wrap_html_lines(tag: str, child_lines: list[str]) -> list[str]:
    return [f"<{tag}>{line}</{tag}>" if line else "" for line in child_lines]


def _pandoc_inlines_to_html_lines(inlines: list[dict[str, Any]]) -> list[str]:
    """Render inline content as balanced HTML lines.

    Pandoc can represent a Word run such as **line 1 / line 2** as one Strong
    inline containing a LineBreak. Rendering that directly as
    `<strong>line 1<br>line 2</strong>` inside a `white-space: pre-line` block
    double-counts breaks and leaves the source hard to read. Split the run into
    separate display lines and balance tags per line instead.
    """
    lines = [""]
    for item in inlines:
        typ = item.get("t")
        val = item.get("c")
        if typ == "Str":
            lines[-1] += html.escape(str(val), quote=False)
        elif typ == "Space":
            lines[-1] += " "
        elif typ in {"SoftBreak", "LineBreak"}:
            lines.append("")
        elif typ == "Strong":
            _merge_html_lines(lines, _wrap_html_lines("strong", _pandoc_inlines_to_html_lines(val or [])))
        elif typ == "Emph":
            _merge_html_lines(lines, _wrap_html_lines("em", _pandoc_inlines_to_html_lines(val or [])))
        elif typ in {"Underline", "SmallCaps"}:
            _merge_html_lines(lines, _pandoc_inlines_to_html_lines(val or []))
        elif typ == "Strikeout":
            _merge_html_lines(lines, _wrap_html_lines("s", _pandoc_inlines_to_html_lines(val or [])))
        elif typ == "Superscript":
            _merge_html_lines(lines, _wrap_html_lines("sup", _pandoc_inlines_to_html_lines(val or [])))
        elif typ == "Subscript":
            _merge_html_lines(lines, _wrap_html_lines("sub", _pandoc_inlines_to_html_lines(val or [])))
        elif typ == "Quoted":
            quote_type, quoted = val
            child = _pandoc_inlines_to_html_lines(quoted)
            if child:
                open_q, close_q = ("'", "'") if quote_type.get("t") == "SingleQuote" else ("«", "»")
                child[0] = f"{open_q}{child[0]}"
                child[-1] = f"{child[-1]}{close_q}"
            _merge_html_lines(lines, child)
        elif typ == "Code":
            lines[-1] += f"<code>{html.escape(str(val[1]), quote=False)}</code>"
        elif typ == "Link":
            _attr, label, target = val
            label_html = "".join(_pandoc_inlines_to_html_lines(label))
            href = html.escape(str(target[0]), quote=True)
            lines[-1] += f'<a href="{href}">{label_html}</a>'
        elif typ == "Image":
            _attr, label, target = val
            alt = html.escape(_plain_text_inlines(label), quote=True)
            src = html.escape(str(target[0]), quote=True)
            lines[-1] += f'<img src="{src}" alt="{alt}">'
        elif typ == "Span":
            _attr, span_inlines = val
            _merge_html_lines(lines, _pandoc_inlines_to_html_lines(span_inlines))
        elif typ == "RawInline":
            fmt, raw = val
            if fmt == "html":
                lines[-1] += str(raw)
            elif fmt == "markdown":
                lines[-1] += html.escape(str(raw), quote=False)
        elif isinstance(val, list):
            _merge_html_lines(lines, _pandoc_inlines_to_html_lines(val))
    return lines


def _is_verse_section_title(title: str) -> bool:
    normalized = re.sub(r"\s+", " ", title.strip().lower())
    return bool(_VERSE_SECTION_TITLE_RE.match(normalized))


def _html_line_is_short_enough(line: str) -> bool:
    text = re.sub(r"<[^>]+>", "", line)
    text = html.unescape(text).strip()
    return len(text) <= 180


def _clean_verse_html_line(line: str) -> str:
    line = re.sub(r"<(strong|em)>\s*(?:<br>\s*)+\s*</\1>", "", line)
    line = re.sub(r"<(strong|em)>\s*</\1>", "", line)
    line = re.sub(r"(?:<br>\s*)+$", "", line)
    return line.strip()


def _verse_html_from_ast_blocks(blocks: list[dict[str, Any]], class_name: str = "verse-block") -> str | None:
    stanzas: list[list[str]] = []
    current: list[str] = []
    saw_content = False

    def flush() -> None:
        nonlocal current
        if current:
            stanzas.append(current)
            current = []

    for block in blocks:
        typ = block.get("t")
        if typ in {"Para", "Plain"}:
            inlines = block.get("c") or []
            if not inlines:
                flush()
                continue
            plain = _plain_text_inlines(inlines)
            if plain.strip() in {"***", r"\*\*\*", "* * *"}:
                flush()
                stanzas.append(["***"])
                continue
            rendered_lines = [
                _clean_verse_html_line(line)
                for line in _pandoc_inlines_to_html_lines(inlines)
                if _clean_verse_html_line(line)
            ]
            if not rendered_lines:
                flush()
                continue
            for line in rendered_lines:
                current.append(line)
            saw_content = True
            continue
        if typ == "HorizontalRule":
            flush()
            stanzas.append(["***"])
            continue
        # Do not risk swallowing structured content into a verse block. If a
        # named section contains tables, lists, code, or images as blocks, keep
        # Pandoc's normal Markdown for that section.
        if typ not in {"Null"}:
            return None

    flush()
    lines = [line for stanza in stanzas for line in stanza]
    if not saw_content or len(lines) < 2:
        return None
    short_ratio = sum(1 for line in lines if _html_line_is_short_enough(line)) / max(1, len(lines))
    if short_ratio < 0.75:
        return None

    out = [f'<div class="{class_name}">']
    for stanza in stanzas:
        out.extend(stanza)
        out.append("")
    while out and out[-1] == "":
        out.pop()
    out.append("</div>")
    return "\n".join(out)


def _has_inline_kind(inlines: list[dict[str, Any]], kinds: set[str]) -> bool:
    for item in inlines:
        typ = item.get("t")
        val = item.get("c")
        if typ in kinds:
            return True
        if typ in {"Strong", "Emph", "Underline", "Strikeout", "Superscript", "Subscript", "SmallCaps"}:
            if _has_inline_kind(val or [], kinds):
                return True
        elif typ == "Quoted":
            if _has_inline_kind(val[1], kinds):
                return True
        elif typ == "Link":
            _attr, label, _target = val
            if _has_inline_kind(label, kinds):
                return True
        elif typ == "Span":
            _attr, span_inlines = val
            if _has_inline_kind(span_inlines, kinds):
                return True
        elif isinstance(val, list) and _has_inline_kind(val, kinds):
            return True
    return False


def _pandoc_inlines_to_plain_lines(inlines: list[dict[str, Any]]) -> list[str]:
    lines = [""]

    def merge(child_lines: list[str]) -> None:
        for idx, child in enumerate(child_lines):
            if idx:
                lines.append("")
            lines[-1] += child

    for item in inlines:
        typ = item.get("t")
        val = item.get("c")
        if typ == "Str":
            lines[-1] += str(val)
        elif typ == "Space":
            lines[-1] += " "
        elif typ in {"SoftBreak", "LineBreak"}:
            lines.append("")
        elif typ in {"Strong", "Emph", "Underline", "Strikeout", "Superscript", "Subscript", "SmallCaps"}:
            merge(_pandoc_inlines_to_plain_lines(val or []))
        elif typ == "Quoted":
            quote_type, quoted = val
            child = _pandoc_inlines_to_plain_lines(quoted)
            if child:
                open_q, close_q = ("'", "'") if quote_type.get("t") == "SingleQuote" else ("«", "»")
                child[0] = f"{open_q}{child[0]}"
                child[-1] = f"{child[-1]}{close_q}"
            merge(child)
        elif typ == "Code":
            lines[-1] += str(val[1])
        elif typ == "Link":
            _attr, label, _target = val
            merge(_pandoc_inlines_to_plain_lines(label))
        elif typ == "Image":
            _attr, label, _target = val
            merge(_pandoc_inlines_to_plain_lines(label))
        elif typ == "Span":
            _attr, span_inlines = val
            merge(_pandoc_inlines_to_plain_lines(span_inlines))
        elif typ == "RawInline":
            _fmt, raw = val
            lines[-1] += str(raw)
        elif isinstance(val, list):
            merge(_pandoc_inlines_to_plain_lines(val))
    return [re.sub(r"\s+", " ", line).strip() for line in lines]


def _is_lineated_plain_text(text: str, *, allow_colon_line: bool = False) -> bool:
    s = re.sub(r"\s+", " ", text).strip()
    if not s or len(s) > 145:
        return False
    if _ANY_HEADING_RE.match(s):
        return False
    if s.startswith(("!", "<", "|", ">", "[]")):
        return False
    if re.match(r"^[-*+]\s+", s) or re.match(r"^\d+[.)]\s+", s):
        return False
    if not allow_colon_line and re.match(r"^[A-ZА-ЯЁ][\w .А-Яа-яЁё-]{1,48}:\s*$", s):
        return False
    if not allow_colon_line and re.match(r"^[A-ZА-ЯЁ][\w .А-Яа-яЁё-]{1,48}:\s", s):
        return False
    if re.match(r"^\*\*[^*]{1,80}:\*\*", s):
        return False
    if "http://" in s or "https://" in s:
        return False
    return True


def _is_lineated_ast_block(block: dict[str, Any], *, answer_context: bool = False) -> bool:
    if block.get("t") not in {"Para", "Plain"}:
        return False
    inlines = block.get("c") or []
    if not inlines:
        return False
    if _has_inline_kind(inlines, {"Image", "Link", "Code"}):
        return False
    lines = [line for line in _pandoc_inlines_to_plain_lines(inlines) if line]
    return bool(lines) and all(_is_lineated_plain_text(line, allow_colon_line=answer_context) for line in lines)


def _block_plain_lines(block: dict[str, Any]) -> list[str]:
    return [
        line
        for line in _pandoc_inlines_to_plain_lines(block.get("c") or [])
        if line.strip()
    ]


_NUMBERED_QUESTION_TITLE_RE = re.compile(r"^\d{1,3}[.)]\s+\S.*[?？]\s*$")


def _is_numbered_question_title(title: str) -> bool:
    return bool(_NUMBERED_QUESTION_TITLE_RE.match(re.sub(r"\s+", " ", title.strip())))


def _lineated_run_kind(
    blocks: list[dict[str, Any]],
    *,
    after_named_heading: bool,
    after_question_heading: bool,
    after_heading: bool,
    after_separator: bool,
) -> str | None:
    content_blocks = [b for b in blocks if (b.get("c") or [])]
    lines = [line for block in content_blocks for line in _block_plain_lines(block)]
    if after_question_heading and len(lines) >= 2 and len(lines) <= 12:
        lengths = [len(line) for line in lines]
        avg_len = sum(lengths) / len(lengths)
        return "answer-block" if avg_len <= 95 and max(lengths) <= 150 else None
    if len(lines) < 3:
        return None
    lengths = [len(line) for line in lines]
    avg_len = sum(lengths) / len(lengths)
    empty_count = sum(1 for block in blocks if block.get("t") in {"Para", "Plain"} and not (block.get("c") or []))
    linebreak_count = sum(
        1
        for block in content_blocks
        if _has_inline_kind(block.get("c") or [], {"SoftBreak", "LineBreak"})
    )

    if after_named_heading:
        return "verse-block" if avg_len <= 150 else None
    if after_separator and len(lines) <= 24:
        return "verse-block" if avg_len <= 110 and max(lengths) <= 160 else None
    if after_heading and len(lines) <= 14:
        return "verse-block" if avg_len <= 95 and max(lengths) <= 150 else None
    if linebreak_count:
        return "verse-block"
    if empty_count and avg_len <= 120:
        return "verse-block"
    return None


def _block_line_keys(block: dict[str, Any]) -> list[str]:
    return [_plain_key(line) for line in _block_plain_lines(block) if _plain_key(line)]


def _trim_trailing_protected_blocks(
    blocks: list[dict[str, Any]],
    protected_key_sequences: list[list[str]],
) -> list[dict[str, Any]]:
    if not blocks or not protected_key_sequences:
        return blocks

    out = list(blocks)
    while out:
        keys = [key for block in out for key in _block_line_keys(block)]
        matched: list[str] | None = None
        for seq in protected_key_sequences:
            seq = [key for key in seq if key]
            if seq and len(seq) <= len(keys) and keys[-len(seq):] == seq:
                matched = seq
                break
        if not matched:
            break

        remaining = len(matched)
        while out and remaining > 0:
            remaining -= len(_block_line_keys(out.pop()))
    return out


@dataclass
class _VerseRun:
    plain_lines: list[str]
    html_block: str


def _plain_key(text: str) -> str:
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[\^[^\]]+\]", "", text)
    text = text.replace("\\", "")
    text = re.sub(r"[*_`~^]+", "", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def _lineated_runs_from_ast(
    ast: dict[str, Any],
    protected_key_sequences: list[list[str]] | None = None,
) -> list[_VerseRun]:
    runs: list[_VerseRun] = []
    current: list[dict[str, Any]] = []
    last_heading_was_any = False
    last_heading_was_named = False
    last_heading_was_question = False
    last_block_was_separator = False
    current_after_heading = False
    current_after_named_heading = False
    current_after_question_heading = False
    current_after_separator = False
    protected_key_sequences = protected_key_sequences or []

    def flush() -> None:
        nonlocal current, current_after_heading, current_after_named_heading, current_after_question_heading, current_after_separator
        candidate = _trim_trailing_protected_blocks(current, protected_key_sequences)
        run_kind = _lineated_run_kind(
            candidate,
            after_named_heading=current_after_named_heading,
            after_question_heading=current_after_question_heading,
            after_heading=current_after_heading,
            after_separator=current_after_separator,
        ) if candidate else None
        if candidate and run_kind:
            html_block = _verse_html_from_ast_blocks(candidate, class_name=run_kind)
            if html_block:
                plain_lines = [
                    _plain_key(line)
                    for block in candidate
                    if (block.get("c") or [])
                    for line in _block_plain_lines(block)
                ]
                min_lines = 2 if run_kind == "answer-block" else 3
                if len(plain_lines) >= min_lines:
                    runs.append(_VerseRun(plain_lines=plain_lines, html_block=html_block))
        current = []
        current_after_heading = False
        current_after_named_heading = False
        current_after_question_heading = False
        current_after_separator = False

    for block in ast.get("blocks") or []:
        typ = block.get("t")
        if typ == "Header":
            flush()
            _level, _attr, inlines = block.get("c") or [None, None, []]
            title = _plain_text_inlines(inlines)
            last_heading_was_any = True
            last_heading_was_named = _is_verse_section_title(title)
            last_heading_was_question = _is_numbered_question_title(title)
            last_block_was_separator = False
            continue
        if typ == "HorizontalRule":
            flush()
            last_heading_was_any = False
            last_heading_was_named = False
            last_heading_was_question = False
            last_block_was_separator = True
            continue
        if typ in {"Para", "Plain"} and [line.strip() for line in _block_plain_lines(block)] in (["***"], [r"\*\*\*"], ["* * *"]):
            flush()
            last_heading_was_any = False
            last_heading_was_named = False
            last_heading_was_question = False
            last_block_was_separator = True
            continue
        if typ in {"Para", "Plain"} and not (block.get("c") or []):
            if current:
                current.append(block)
            continue
        answer_context = last_heading_was_question or current_after_question_heading
        if _is_lineated_ast_block(block, answer_context=answer_context):
            if not current:
                current_after_heading = last_heading_was_any
                current_after_named_heading = last_heading_was_named
                current_after_question_heading = last_heading_was_question
                current_after_separator = last_block_was_separator
            current.append(block)
            last_heading_was_any = False
            last_heading_was_named = False
            last_heading_was_question = False
            last_block_was_separator = False
            continue
        flush()
        last_heading_was_any = False
        last_heading_was_named = False
        last_heading_was_question = False
        last_block_was_separator = False
    flush()
    return runs


@dataclass
class _MdBlock:
    start: int
    end: int
    raw: str
    key: str
    line_keys: list[str]


def _markdown_blocks(md: str) -> tuple[list[str], list[_MdBlock]]:
    lines = md.splitlines()
    blocks: list[_MdBlock] = []
    i = 0
    while i < len(lines):
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i >= len(lines):
            break
        start = i
        while i < len(lines) and lines[i].strip():
            i += 1
        raw = "\n".join(lines[start:i])
        if (
            not _ANY_HEADING_RE.match(raw.strip())
            and not raw.lstrip().startswith(("<", "|", "!", ">"))
        ):
            line_keys = [_plain_key(line) for line in raw.splitlines() if _plain_key(line)]
            blocks.append(_MdBlock(start=start, end=i, raw=raw, key=_plain_key(raw), line_keys=line_keys))
    return lines, blocks


def _find_markdown_run_window(
    md_blocks: list[_MdBlock],
    target_keys: list[str],
    used: set[int],
) -> tuple[int, int, list[int]] | None:
    target_keys = [key for key in target_keys if key]
    if not target_keys:
        return None
    for idx in range(len(md_blocks)):
        collected: list[str] = []
        block_indexes: list[int] = []
        for j in range(idx, len(md_blocks)):
            if j in used:
                break
            next_keys = md_blocks[j].line_keys or ([md_blocks[j].key] if md_blocks[j].key else [])
            if not next_keys:
                break
            collected.extend(next_keys)
            block_indexes.append(j)
            if collected == target_keys:
                return md_blocks[idx].start, md_blocks[j].end, block_indexes
            if len(collected) >= len(target_keys) or collected != target_keys[:len(collected)]:
                break
    return None


@dataclass(frozen=True)
class _DocxStructuralRun:
    keys: list[str]
    html_block: str


_RIGHT_ALIGNS = {"right", "end"}
_SCRIPTURE_REF_RE = re.compile(
    r"^(?:"
    r"(?:[1-3]\s*)?[А-ЯЁA-Z][А-Яа-яЁёA-Za-z. ]+\s+\d{1,3}:\d{1,3}(?:[–—-]\d{1,3})?|"
    r"(?:Ин|Иоанн|Мф|Матф|Марк|Мк|Лк|Луки|Дан|Даниил|Откровение|Бытие|Кор|Пс)\.?\s*\d{1,3}:\d{1,3}(?:[–—-]\d{1,3})?"
    r")\.?$",
    re.IGNORECASE,
)
_SIGNATURE_LINE_RE = re.compile(
    r"^(?:"
    r"Панкратиус|Светозар|Сергей(?:\s+Панкратиус)?\.?|Я\s+Есмь|"
    r"Pankratius|Svetozar|Creator|The Creator|"
    r"[—-]\s*Панкратиус.*|[—-]\s*Светозар.*"
    r")$",
    re.IGNORECASE,
)
_SOURCE_LINE_RE = re.compile(
    r"(?:к\.ф\.|Матрица|Пифия|Платон|Даниил|Откровение|Евангелие|Ин\.|Мф\.|Лк\.|Кор\.)",
    re.IGNORECASE,
)


def _right_aligned_groups(paras: list[DocxParagraphMeta]) -> list[list[DocxParagraphMeta]]:
    groups: list[list[DocxParagraphMeta]] = []
    current: list[DocxParagraphMeta] = []
    for para in paras:
        if para.align in _RIGHT_ALIGNS and not para.is_empty:
            current.append(para)
            continue
        if current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def _is_signature_group(lines: list[str]) -> bool:
    if not (1 <= len(lines) <= 4):
        return False
    if any(len(line) > 90 for line in lines):
        return False
    if any("панкратиус" in line.casefold() for line in lines):
        return True
    if all(_SIGNATURE_LINE_RE.match(line.strip()) for line in lines):
        return True
    if len(lines) == 1 and re.fullmatch(r"[—-]\s*[\wА-Яа-яЁё .]{2,80}", lines[0]):
        return True
    return False


def _is_epigraph_group(lines: list[str], group: list[DocxParagraphMeta]) -> bool:
    if len(lines) < 2:
        return False
    joined = " ".join(lines)
    if len(joined) < 30:
        return False
    has_ref = any(_SCRIPTURE_REF_RE.match(line.strip()) for line in lines)
    has_source = any(_SOURCE_LINE_RE.search(line) for line in lines[1:])
    starts_quoted = lines[0].lstrip().startswith(("«", "\"", "“", "„"))
    mostly_italic = sum(1 for p in group if p.italic) >= max(1, len(group) // 2)
    compact_source_quote = has_source and len(lines) <= 4 and len(lines[0]) <= 180
    return bool(has_ref or compact_source_quote or (starts_quoted and has_source) or (starts_quoted and mostly_italic))


def _split_epigraph_lines(lines: list[str]) -> tuple[list[str], list[str]]:
    footer: list[str] = []
    quote = list(lines)
    while len(quote) > 1:
        candidate = quote[-1].strip()
        if _SCRIPTURE_REF_RE.match(candidate) or _SOURCE_LINE_RE.search(candidate):
            footer.insert(0, quote.pop())
            continue
        break
    if not footer:
        footer = [quote.pop()]
    return quote, footer


def _render_signature(lines: list[str]) -> str:
    body = "\n".join(html.escape(line, quote=False) for line in lines)
    return f'<p class="signature">\n{body}\n</p>'


def _render_epigraph(lines: list[str]) -> str:
    quote, footer = _split_epigraph_lines(lines)
    quote_html = "\n".join(html.escape(line, quote=False) for line in quote)
    footer_html = "\n".join(html.escape(line, quote=False) for line in footer)
    return "\n".join([
        '<blockquote class="epigraph">',
        "<p>",
        quote_html,
        "</p>",
        "<footer>",
        footer_html,
        "</footer>",
        "</blockquote>",
    ])


def _docx_structural_runs(paras: list[DocxParagraphMeta]) -> list[_DocxStructuralRun]:
    runs: list[_DocxStructuralRun] = []
    for group in _right_aligned_groups(paras):
        lines = [
            line.strip()
            for p in group
            for line in p.text.splitlines()
            if line.strip()
        ]
        if not lines:
            continue
        if _is_signature_group(lines):
            runs.append(_DocxStructuralRun(keys=[_plain_key(line) for line in lines], html_block=_render_signature(lines)))
            continue
        if _is_epigraph_group(lines, group):
            runs.append(_DocxStructuralRun(keys=[_plain_key(line) for line in lines], html_block=_render_epigraph(lines)))
    return runs


def _docx_structural_key_sequences(paras: list[DocxParagraphMeta]) -> list[list[str]]:
    return [run.keys for run in _docx_structural_runs(paras) if run.keys]


def normalize_docx_structural_blocks(md: str, paras: list[DocxParagraphMeta]) -> str:
    """Apply narrow DOCX-only semantic wrappers.

    Paragraph alignment is source metadata. Use it for right-aligned signatures
    and epigraph/scripture groups; do not infer these from rendered CSS,
    italic alone, or arbitrary short paragraphs.
    """
    runs = _docx_structural_runs(paras)
    if not runs:
        return md

    lines, md_blocks = _markdown_blocks(md)
    replacements: list[tuple[int, int, list[str]]] = []
    used: set[int] = set()

    for run in runs:
        keys = [key for key in run.keys if key]
        match = _find_markdown_run_window(md_blocks, keys, used)
        if not match:
            continue
        start, end, indexes = match
        replacements.append((start, end, ["", *run.html_block.splitlines(), ""]))
        used.update(indexes)

    if not replacements:
        return md
    for start, end, replacement in sorted(replacements, reverse=True):
        lines[start:end] = replacement
    return "\n".join(lines)


def normalize_ast_lineated_runs(
    md: str,
    ast: dict[str, Any],
    protected_key_sequences: list[list[str]] | None = None,
) -> str:
    """Wrap detected short-line runs from DOCX as explicit verse blocks.

    This is the general form of the earlier named-section fix. It uses the
    Pandoc JSON AST to decide what is a stack of source lines, then matches the
    same lines back into the cleaned Markdown and replaces only that range.
    Normal prose is left as normal paragraphs, so CSS does not have to choose
    between over-spaced stanzas and wall-of-text prose.
    """
    runs = _lineated_runs_from_ast(ast, protected_key_sequences=protected_key_sequences)
    if not runs:
        return md

    lines, md_blocks = _markdown_blocks(md)
    replacements: list[tuple[int, int, list[str]]] = []
    used: set[int] = set()

    for run in runs:
        if not run.plain_lines:
            continue
        match = _find_markdown_run_window(md_blocks, run.plain_lines, used)
        if not match:
            continue
        start, end, indexes = match
        replacements.append((start, end, ["", *run.html_block.splitlines(), ""]))
        used.update(indexes)

    if not replacements:
        return md

    for start, end, replacement in sorted(replacements, reverse=True):
        lines[start:end] = replacement
    return "\n".join(lines)


def _heading_title_from_md(line: str) -> str | None:
    m = re.match(r"^#{1,6}\s+(.+?)\s*$", line.strip())
    if not m:
        return None
    title = re.sub(r"\{#[^}]+\}\s*$", "", m.group(1)).strip()
    title = re.sub(r"[*_`]+", "", title)
    return re.sub(r"\s+", " ", title)


def _verse_section_replacements_from_ast(ast: dict[str, Any]) -> dict[str, list[str]]:
    blocks = ast.get("blocks") or []
    replacements: dict[str, list[str]] = {}
    i = 0
    while i < len(blocks):
        block = blocks[i]
        if block.get("t") != "Header":
            i += 1
            continue
        _level, _attr, inlines = block.get("c") or [None, None, []]
        title = _plain_text_inlines(inlines)
        if not _is_verse_section_title(title):
            i += 1
            continue
        j = i + 1
        section_blocks: list[dict[str, Any]] = []
        while j < len(blocks) and blocks[j].get("t") != "Header":
            section_blocks.append(blocks[j])
            j += 1
        html_block = _verse_html_from_ast_blocks(section_blocks)
        if html_block:
            key = re.sub(r"\s+", " ", title.strip())
            replacements.setdefault(key, []).append(html_block)
        i = j
    return replacements


def normalize_ast_verse_sections(md: str, ast: dict[str, Any]) -> str:
    """Replace named lineated sections with explicit verse HTML.

    Word source often represents liturgical / Creator-voice lineation as one
    paragraph per line, with empty paragraphs as stanza separators. Pandoc's
    Markdown writer loses the empty-paragraph signal, but `docx+empty_paragraphs`
    keeps it in JSON. Use that structural source only for named sections where
    lineation is intended, so normal prose can keep normal paragraph spacing.
    """
    replacements = _verse_section_replacements_from_ast(ast)
    if not replacements:
        return md

    lines = md.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        title = _heading_title_from_md(lines[i])
        candidates = replacements.get(title or "")
        if not candidates:
            out.append(lines[i])
            i += 1
            continue

        html_block = candidates.pop(0)
        out.append(lines[i])
        j = i + 1
        while j < len(lines) and not _ANY_HEADING_RE.match(lines[j].strip()):
            j += 1
        out.append("")
        out.append(html_block)
        out.append("")
        i = j
    return "\n".join(out)


def _pandoc_inlines_to_md(inlines: list[dict[str, Any]]) -> str:
    out: list[str] = []
    for item in inlines:
        typ = item.get("t")
        val = item.get("c")
        if typ == "Str":
            out.append(str(val))
        elif typ == "Space":
            out.append(" ")
        elif typ in {"SoftBreak", "LineBreak"}:
            out.append("\n")
        elif typ == "Strong":
            out.append(f"**{_pandoc_inlines_to_md(val or [])}**")
        elif typ == "Emph":
            out.append(f"*{_pandoc_inlines_to_md(val or [])}*")
        elif typ == "Underline":
            out.append(_pandoc_inlines_to_md(val or []))
        elif typ == "Strikeout":
            out.append(f"~~{_pandoc_inlines_to_md(val or [])}~~")
        elif typ == "Superscript":
            out.append(f"^{_pandoc_inlines_to_md(val or [])}^")
        elif typ == "Subscript":
            out.append(f"~{_pandoc_inlines_to_md(val or [])}~")
        elif typ == "SmallCaps":
            out.append(_pandoc_inlines_to_md(val or []))
        elif typ == "Quoted":
            quote_type, quoted = val
            inner = _pandoc_inlines_to_md(quoted)
            if quote_type.get("t") == "SingleQuote":
                out.append(f"'{inner}'")
            else:
                out.append(f"«{inner}»")
        elif typ == "Code":
            out.append(f"`{val[1]}`")
        elif typ == "Link":
            _attr, label, target = val
            label_text = _pandoc_inlines_to_md(label).strip()
            if label_text:
                out.append(f"[{label_text}]({target[0]})")
        elif typ == "Image":
            _attr, label, target = val
            out.append(f"![{_pandoc_inlines_to_md(label)}]({target[0]})")
        elif typ == "Span":
            _attr, span_inlines = val
            out.append(_pandoc_inlines_to_md(span_inlines))
        elif typ == "RawInline":
            fmt, raw = val
            if fmt in {"html", "markdown"}:
                out.append(str(raw))
        elif isinstance(val, list):
            out.append(_pandoc_inlines_to_md(val))
    return "".join(out)


def _is_strong_only_para(block: dict[str, Any]) -> bool:
    return block.get("t") == "Para" and len(block.get("c") or []) == 1 and block["c"][0].get("t") == "Strong"


def pandoc_poem_ast_to_md(ast: dict[str, Any]) -> str:
    """Emit author-facing verse Markdown from Pandoc's docx AST.

    The key is `docx+empty_paragraphs`: Word's empty paragraphs survive as
    `Para []`, so stanza breaks are still present before Pandoc's Markdown
    writer flattens them. Non-empty paragraphs become verse lines; paragraphs
    with real Word line breaks become one stanza block.
    """
    blocks = ast.get("blocks") or []
    groups: list[list[str]] = []
    current: list[str] = []
    seen_content = False

    def flush() -> None:
        nonlocal current
        if current:
            groups.append(current)
            current = []

    for block in blocks:
        typ = block.get("t")
        if typ == "Para":
            inlines = block.get("c") or []
            if not inlines:
                flush()
                continue
            text = _pandoc_inlines_to_md(inlines)
            lines = [re.sub(r"[ \t]+$", "", ln) for ln in text.split("\n")]
            lines = [ln for ln in lines if ln.strip()]
            if not lines:
                flush()
                continue
            if not seen_content and _is_strong_only_para(block):
                flush()
                groups.append(lines)
                seen_content = True
                continue
            seen_content = True
            if len(lines) == 1 and lines[0].strip() == r"\*\*\*":
                lines = ["***"]
            if len(lines) == 1 and lines[0].strip() == "***":
                flush()
                groups.append(["***"])
            elif len(lines) > 1:
                flush()
                groups.append(lines)
            else:
                current.append(lines[0])
            continue
        if typ == "Plain":
            text = _pandoc_inlines_to_md(block.get("c") or [])
            lines = [ln.rstrip() for ln in text.split("\n") if ln.strip()]
            if len(lines) > 1:
                flush()
                groups.append(lines)
            elif lines:
                current.append(lines[0])
                seen_content = True
            continue
        flush()
    flush()
    return "\n\n".join("\n".join(group) for group in groups).strip() + "\n"


def _poem_title_key(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"^[#>*_`\s-]+|[*_`\s-]+$", "", s.strip())
    s = s.replace("…", "...")
    s = re.sub(r"[.,;:!?]+$", "", s)
    return re.sub(r"\s+", " ", s).casefold().strip()


def _first_nonempty_docx_paras(paras: list[DocxParagraphMeta], limit: int = 2) -> list[DocxParagraphMeta]:
    out: list[DocxParagraphMeta] = []
    for para in paras:
        if para.is_empty:
            continue
        out.append(para)
        if len(out) >= limit:
            break
    return out


def _is_poem_section_heading_text(s: str) -> bool:
    return bool(re.match(r"^(?:[IVXLCDM]+\.|[А-ЯA-Z]\.)\s+\S", s.strip(), re.IGNORECASE))


def _strip_source_duplicate_poem_title(
    body: str,
    title: str,
    docx_paras: list[DocxParagraphMeta],
) -> str:
    """Drop DOCX editor-title boilerplate from poem bodies, not incipits.

    Some poem DOCX files start with a separate title paragraph and the page
    masthead already renders that title. Others legitimately start with a
    first verse line equal to the title/refrain ("А если буду я не прав?").
    Strip only when the DOCX itself proves a title paragraph: the first
    non-empty paragraph matches frontmatter title and is typographically
    distinct (bold) or is followed by a real Word line-break stanza.
    """
    key = _poem_title_key(title)
    first_two = _first_nonempty_docx_paras(docx_paras, 2)
    if not key or not first_two or _poem_title_key(first_two[0].text) != key:
        return body
    first = first_two[0]
    second = first_two[1] if len(first_two) > 1 else None
    source_says_title = (
        first.bold
        or first.line_breaks > 0
        or bool(second and second.line_breaks > 0)
        or bool(second and _is_poem_section_heading_text(second.text))
    )
    if not source_says_title:
        return body

    blocks = re.split(r"\n\s*\n", body.strip(), maxsplit=1)
    if not blocks or _poem_title_key(blocks[0]) != key:
        return body
    rest = blocks[1] if len(blocks) > 1 else ""
    return rest.lstrip() + ("\n" if rest and not rest.endswith("\n") else "")


def process_poem_markdown(
    md_raw: str,
    book_slug: str,
    lang: str,
    image_root: Path,
    work_dir: Path,
    image_records: list[ImageRecord],
    writes: WorkWrites,
    image_counter_start: int,
    cross_ref_title_index: dict[str, tuple[str, int | None, str | None]],
    own_ascii_slug: str,
) -> tuple[str, list[dict[str, Any]], int]:
    md = strip_ai_alt(md_raw)
    md, next_idx = rewrite_images(
        md, book_slug, lang, image_root, work_dir, image_records, writes, image_counter_start,
    )
    refs = extract_cross_refs(md, own_ascii_slug, cross_ref_title_index)
    md = unwrap_spans_and_u(md)
    md = strip_formatting_artifacts(md)
    md = strip_empty_headings(md)
    md = strip_trailing_hardbreak_markers(md)
    md = collapse_blank_lines(md)
    return md, refs, next_idx


def convert_poem_docx_to_md(
    docx: Path,
    title: str,
    book_slug: str,
    work_dir: Path,
    image_records: list[ImageRecord],
    writes: WorkWrites,
    image_counter_start: int,
    cross_ref_title_index: dict[str, tuple[str, int | None, str | None]],
    own_ascii_slug: str,
) -> tuple[str, list[dict[str, Any]], int, str]:
    if not docx.exists():
        raise FileNotFoundError(docx)
    with tempfile.TemporaryDirectory(prefix=f"pancratius-{book_slug}-") as td:
        tdp = Path(td)
        media_tmp = tdp / "media"
        ast, warnings = _run_pandoc_json(docx, media_tmp)
        docx_paras = read_docx_paragraph_meta(docx)
        md_raw = pandoc_poem_ast_to_md(ast)
        md, refs, next_idx = process_poem_markdown(
            md_raw=md_raw,
            book_slug=book_slug,
            lang="ru",
            image_root=tdp,
            work_dir=work_dir,
            image_records=image_records,
            writes=writes,
            image_counter_start=image_counter_start,
            cross_ref_title_index=cross_ref_title_index,
            own_ascii_slug=own_ascii_slug,
        )
        md = _strip_source_duplicate_poem_title(md, title, docx_paras)
    return md, refs, next_idx, warnings


# ---------------------------------------------------------------------------
# image rewriting (pandoc temp → work-bundle `images/<hash>.<ext>`)
# ---------------------------------------------------------------------------

def rewrite_images(
    md: str,
    book_slug: str,
    lang: str,
    image_root: Path,
    work_dir: Path,
    image_records: list[ImageRecord],
    writes: WorkWrites,
    image_counter_start: int = 1,
) -> tuple[str, int]:
    """Rewrite image refs to `./images/<hash>.<ext>` co-located with
    the work, dedup by hash within the work bundle, record metadata. Run
    *after* bibliography lift so thumbs from catalog tables are never
    copied."""
    seen: dict[str, tuple[str, str]] = {}
    next_idx = image_counter_start
    images_dir = work_dir / "images"

    def resolve(src: str) -> tuple[str, str] | None:
        if src in seen:
            return seen[src]
        candidate = (image_root / src).resolve()
        if not candidate.exists():
            alt = Path(src)
            if alt.exists():
                candidate = alt.resolve()
            else:
                return None
        if not _is_image_path(candidate.name):
            return None
        h = _hash_file(candidate)
        ext = _normalize_ext(candidate.suffix)
        dst = images_dir / f"{h}{ext}"
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(candidate, dst)
        writes.add(dst)
        seen[src] = (h, ext)
        return h, ext

    def record(src: str, h: str, ext: str, role: str) -> None:
        nonlocal next_idx
        size = (images_dir / f"{h}{ext}").stat().st_size
        image_records.append(ImageRecord(
            book_slug=book_slug,
            image_index=next_idx,
            original_filename=Path(src).name,
            media_hash=h,
            ext=ext,
            bytes=size,
            role=role,
        ))
        next_idx += 1

    def md_repl(m: re.Match) -> str:
        alt = m.group(1).strip() or _body_image_alt(lang)
        src = m.group(2).split(' "', 1)[0].strip()
        got = resolve(src)
        if not got:
            return m.group(0)
        h, ext = got
        record(src, h, ext, "body")
        return f"![{_escape_markdown_alt(alt)}](./images/{h}{ext})"

    def html_repl(m: re.Match) -> str:
        pre, src, post = m.group(1), m.group(2), m.group(3)
        got = resolve(src)
        if not got:
            return m.group(0)
        h, ext = got
        record(src, h, ext, "body")
        attrs = _HTML_DIM_ATTR.sub("", pre + post)
        alt_m = _HTML_ALT_ATTR.search(attrs)
        alt = (alt_m.group(1).strip() if alt_m else "") or _body_image_alt(lang)
        return f"![{_escape_markdown_alt(alt)}](./images/{h}{ext})"

    md = _IMG_MD.sub(md_repl, md)
    md = _IMG_HTML.sub(html_repl, md)
    md = normalize_body_image_blocks(md)
    return md, next_idx


def _body_image_alt(lang: str) -> str:
    return "Illustration" if lang == "en" else "Иллюстрация"


def _escape_markdown_alt(alt: str) -> str:
    return alt.replace("[", r"\[").replace("]", r"\]")


def normalize_body_image_blocks(md: str) -> str:
    """Keep imported DOCX body illustrations as block Markdown images.

    Pandoc often places an image run and adjacent paragraph text on the same
    Markdown line. These are book illustrations, not inline emoji/icons, so
    the author-facing source should make that structure explicit. Table rows
    are left alone because Markdown images are valid cell content there.
    """
    out: list[str] = []
    for line in md.splitlines():
        if not _BODY_IMG_MD.search(line) or line.lstrip().startswith("|"):
            out.append(line)
            continue

        pos = 0
        for m in _BODY_IMG_MD.finditer(line):
            before = line[pos:m.start()].strip()
            if before:
                out.append(before)
            if out and out[-1] != "":
                out.append("")
            out.append(m.group(0))
            out.append("")
            pos = m.end()

        after = line[pos:].strip()
        if after:
            out.append(after)
        elif out and out[-1] == "":
            out.pop()

    return "\n".join(out)


def _find_table_spans(md: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    cursor = 0
    while True:
        start = md.find("<table", cursor)
        if start < 0:
            return spans
        end = md.find("</table>", start)
        if end < 0:
            return spans
        spans.append((start, end + len("</table>")))
        cursor = end + 1


# ---------------------------------------------------------------------------
# bibliography extraction
# ---------------------------------------------------------------------------

def extract_bibliography(md: str, slug_lookup: dict[str, str]) -> tuple[str, list[dict[str, Any]]]:
    """Pull every `<table>` block that looks like a bibliography (cells with
    images + LitRes URLs) out of the markdown. Resolve each row to a target
    ASCII slug when its title or LitRes URL matches a known work. Return the
    md with those tables removed and the structured list."""
    spans = _find_table_spans(md)
    if not spans:
        return md, []
    entries: list[dict[str, Any]] = []
    keep_pieces: list[str] = []
    last = 0
    for start, end in spans:
        block = md[start:end]
        looks_like_biblio = (
            "<img" in block
            or "litres.ru" in block
            or "kindbook.net" in block
            or len(re.findall(r"<tr>", block)) >= 5
        )
        if not looks_like_biblio:
            keep_pieces.append(md[last:end])
            last = end
            continue
        keep_pieces.append(md[last:start])
        last = end
        entries.extend(_parse_biblio_table(block, slug_lookup))
    keep_pieces.append(md[last:])
    return "".join(keep_pieces), entries


_TR_RE = re.compile(r"<tr>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
_TD_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.DOTALL | re.IGNORECASE)
_A_RE = re.compile(r'<a\s+href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


_IMG_ALT_RE = re.compile(r'<img\s+[^>]*\balt="([^"]*)"', re.IGNORECASE)


def _parse_biblio_table(
    block: str,
    slug_lookup: dict[str, tuple[str, int | None, str | None]],
) -> list[dict[str, Any]]:
    rows = _TR_RE.findall(block)
    out: list[dict[str, Any]] = []
    for row in rows:
        cells = _TD_RE.findall(row)
        if not cells:
            continue
        links = []
        for cell in cells:
            for href, label in _A_RE.findall(cell):
                if "litres.ru" not in href and "kindbook.net" not in href:
                    continue
                title = _TAG_RE.sub("", label).strip()
                if not title or len(title) < 2:
                    continue
                links.append((title, href))
        if links:
            for title, href in links:
                _, number, kind = _resolve_to_slug(title, slug_lookup)
                entry: dict[str, Any] = {"title": title, "source_url": href}
                if number is not None and kind:
                    entry["target"] = {"kind": kind, "number": number}
                out.append(entry)
            continue
        alts: list[str] = []
        for cell in cells:
            for alt in _IMG_ALT_RE.findall(cell):
                alt = alt.strip()
                if alt and len(alt) > 2 and not any(frag in alt for frag in AI_ALT_FRAGMENTS):
                    alts.append(alt)
        for alt in alts:
            _, number, kind = _resolve_to_slug(alt, slug_lookup)
            entry: dict[str, Any] = {"title": alt}
            if number is not None and kind:
                entry["target"] = {"kind": kind, "number": number}
            out.append(entry)
    return out


def _resolve_to_slug(
    title: str,
    slug_lookup: dict[str, tuple[str, int | None, str | None]],
) -> tuple[str | None, int | None, str | None]:
    """Return (ascii_slug, number, kind) when the title resolves, else
    (None, None, None)."""
    norm = re.sub(r"\s+", " ", title.lower()).strip()
    got = slug_lookup.get(norm)
    if not got:
        return None, None, None
    return got


# ---------------------------------------------------------------------------
# cross-refs extraction
# ---------------------------------------------------------------------------

def extract_cross_refs(
    md: str,
    own_slug: str,
    title_index: dict[str, tuple[str, int | None, str | None]],
) -> list[dict[str, Any]]:
    """Scan footnote bodies and inline mentions for references to other works
    in the corpus. Emit `{target: {kind, number}, source, snippet}` entries
    when the reference resolves; drop unresolved mentions (they're noise)."""
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()

    def push(slug: str, number: int | None, kind: str | None, source: str, snippet: str, url: str | None = None) -> None:
        if slug == own_slug or number is None or not kind:
            return
        key = (kind, number)
        if key in seen:
            return
        seen.add(key)
        entry: dict[str, Any] = {
            "target": {"kind": kind, "number": number},
            "source": source,
            "snippet": snippet[:240],
        }
        if url:
            entry["source_url"] = url
        refs.append(entry)

    def lookup(key: str) -> tuple[str | None, int | None, str | None]:
        got = title_index.get(key)
        if not got:
            return None, None, None
        return got

    for m in _FOOTNOTE_LINE.finditer(md):
        body = m.group(2)
        for url in _LITRES_URL.findall(body):
            slug, number, kind = lookup(url.rstrip("/").lower())
            push(slug, number, kind, "footnote", body)
        for title_m in _INLINE_BOOK_TITLE.findall(body):
            slug, number, kind = lookup(title_m.lower().strip())
            push(slug, number, kind, "footnote", body)

    for url in _LITRES_URL.findall(md):
        slug, number, kind = lookup(url.rstrip("/").lower())
        push(slug, number, kind, "inline_url", url, url=url)

    for title_m in _INLINE_BOOK_TITLE.findall(md):
        slug, number, kind = lookup(title_m.lower().strip())
        push(slug, number, kind, "inline_title", title_m)

    for title_m in _EN_INLINE_BOOK_TITLE.findall(md):
        slug, number, kind = lookup(title_m.lower().strip())
        push(slug, number, kind, "inline_title", title_m)

    return refs


# ---------------------------------------------------------------------------
# dialogue label normalization
# ---------------------------------------------------------------------------

_DIALOGUE_PREFIXES = [
    "Панкратиус",
    "Светозар",
    "Светозар Gemini Flash 2.0",
    "Светозар DeepSeek",
    "Светозар ChatGPT",
    "Творец",
    "Бог",
    "Слово Творца",
    "Слово Бога",
    "Pankratius",
    "Svetozar",
    "Creator",
    "God",
    "Gemini",
    "DeepSeek",
    "ChatGPT",
]


def normalize_dialogue_labels(md: str) -> str:
    """Force speaker labels to a canonical `**Speaker:**\\n<body>` shape.
    Catches three failure modes from the source corpus:
      - `**Speaker:** body` joined on one line → split body to next paragraph
      - `**Speaker label**` (no colon) → add colon
      - `**Speaker: body**` (label and body inside same bold span) → split"""
    prefixes_sorted = sorted(_DIALOGUE_PREFIXES, key=len, reverse=True)
    pattern_inner = "|".join(re.escape(p) for p in prefixes_sorted)
    label_inside_bold = re.compile(
        rf"^\*\*({pattern_inner})\s*:\s*(.+?)\*\*\s*$"
    )
    label_then_body = re.compile(
        rf"^\*\*({pattern_inner})\s*:\*\*\s+(\S.*)$"
    )
    label_no_colon = re.compile(rf"^\*\*({pattern_inner})\*\*\s*$")

    def is_meaningful_body(s: str) -> bool:
        return bool(re.search(r"[\wЀ-ӿ]", s))

    out: list[str] = []
    for ln in md.splitlines():
        m = label_inside_bold.match(ln)
        if m:
            out.append(f"**{m.group(1)}:**")
            body = m.group(2).strip()
            if is_meaningful_body(body):
                out.append("")
                out.append(body)
            continue
        m = label_then_body.match(ln)
        if m:
            out.append(f"**{m.group(1)}:**")
            body = m.group(2).strip()
            if is_meaningful_body(body):
                out.append("")
                out.append(body)
            continue
        m = label_no_colon.match(ln)
        if m:
            out.append(f"**{m.group(1)}:**")
            continue
        out.append(ln)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# orchestrator
# ---------------------------------------------------------------------------

def process_markdown(
    md_raw: str,
    docx_paras: list[DocxParagraphMeta],
    book_slug: str,
    lang: str,
    image_root: Path,
    work_dir: Path,
    image_records: list[ImageRecord],
    writes: WorkWrites,
    image_counter_start: int,
    biblio_slug_lookup: dict[str, tuple[str, int | None, str | None]],
    cross_ref_title_index: dict[str, tuple[str, int | None, str | None]],
    own_ascii_slug: str,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], int]:
    md = md_raw
    md = scrub_rights_boilerplate(md)
    md = strip_toc(md)
    md = strip_ai_alt(md)
    md, bibliography = extract_bibliography(md, biblio_slug_lookup)
    md = strip_bibliography_sections(md)
    md, next_idx = rewrite_images(
        md, book_slug, lang, image_root, work_dir, image_records, writes, image_counter_start,
    )
    cross_refs = extract_cross_refs(md, own_ascii_slug, cross_ref_title_index)
    md = unwrap_spans_and_u(md)
    md = strip_formatting_artifacts(md)
    md = strip_bold_only_headings(md)
    md = strip_bibliography_sections(md)
    md = strip_empty_headings(md)
    md = normalize_dialogue_labels(md)
    md = normalize_docx_structural_blocks(md, docx_paras)
    md = strip_trailing_hardbreak_markers(md)
    md = collapse_blank_lines(md)
    return md, bibliography, cross_refs, next_idx


def convert_docx_to_md(
    docx: Path,
    book_slug: str,
    lang: str,
    work_dir: Path,
    image_records: list[ImageRecord],
    writes: WorkWrites,
    image_counter_start: int,
    biblio_slug_lookup: dict[str, tuple[str, int | None, str | None]],
    cross_ref_title_index: dict[str, tuple[str, int | None, str | None]],
    own_ascii_slug: str,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], int, str, dict[str, Any], list[list[str]]]:
    if not docx.exists():
        raise FileNotFoundError(docx)
    with tempfile.TemporaryDirectory(prefix=f"pancratius-{book_slug}-") as td:
        tdp = Path(td)
        out_md = tdp / "out.md"
        media_tmp = tdp / "media"
        warnings = _run_pandoc(docx, media_tmp, out_md)
        ast, json_warnings = _run_pandoc_json(docx)
        docx_paras = read_docx_paragraph_meta(docx)
        if json_warnings:
            warnings = "\n".join(w for w in (warnings, json_warnings) if w)
        md_raw = out_md.read_text(encoding="utf-8")
        structural_key_sequences = _docx_structural_key_sequences(docx_paras)
        md, biblio, refs, next_idx = process_markdown(
            md_raw=md_raw,
            docx_paras=docx_paras,
            book_slug=book_slug,
            lang=lang,
            image_root=tdp,
            work_dir=work_dir,
            image_records=image_records,
            writes=writes,
            image_counter_start=image_counter_start,
            biblio_slug_lookup=biblio_slug_lookup,
            cross_ref_title_index=cross_ref_title_index,
            own_ascii_slug=own_ascii_slug,
        )
    return md, biblio, refs, next_idx, warnings, ast, structural_key_sequences


# ---------------------------------------------------------------------------
# legacy path resolution + cover ingest
# ---------------------------------------------------------------------------

def _legacy_path(rel_url: str) -> Path:
    return LEGACY / unquote(rel_url)


def _ingest_cover(
    rel_url: str | None,
    book_slug: str,
    work_dir: Path,
    lang: str,
    image_records: list[ImageRecord],
    writes: WorkWrites,
    role: str,
) -> str | None:
    """Copy the cover into the work bundle as `cover.<lang>.<ext>`. Returns
    the relative frontmatter path (`./cover.<lang>.<ext>`)."""
    if not rel_url:
        return None
    src = _legacy_path(rel_url)
    if not src.exists() or not _is_image_path(src.name):
        return None
    ext = _normalize_ext(src.suffix)
    cover_name = f"cover.{lang}{ext}"
    dst = work_dir / cover_name
    work_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    writes.add(dst)
    image_records.append(ImageRecord(
        book_slug=book_slug,
        image_index=0,
        original_filename=f"{role}:{src.name}",
        media_hash=_hash_file(dst),
        ext=ext,
        bytes=dst.stat().st_size,
        role="cover",
    ))
    return f"./{cover_name}"


def _book_part_label(idx: int, total: int, lang: str) -> str | None:
    if total <= 1:
        return None
    return f"## Part {idx}" if lang == "en" else f"## Часть {idx}"


def _pick_en_files(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not files:
        return files
    docs = [f for f in files if f["format"] == "docx"]
    if not docs:
        return []
    cleans = [f for f in docs if Path(f["url"]).stem.endswith("-clean")]
    if cleans:
        return cleans
    non_drafts = [f for f in docs if not Path(f["url"]).stem.endswith("-pre-cleanup")
                  and not Path(f["url"]).stem.endswith("-pre-cleanup-v2")]
    return non_drafts or docs


# ---------------------------------------------------------------------------
# slug + title indexes
# ---------------------------------------------------------------------------

def _build_ascii_slug(book_or_poem: dict[str, Any], lang: str) -> str:
    """Map legacy Cyrillic-ish slug to ASCII per language. Books use the
    Russian title for both languages (the folder name is canonical RU-ASCII).
    Per-language route slugs are derived from each lang's title for the route
    layer; here we just need a stable, ASCII folder name."""
    cyr = book_or_poem.get("slug") or book_or_poem.get("number")
    if cyr is None:
        raise ValueError("missing slug/number")
    return to_ascii_slug(str(cyr))


def _build_slug_lookups(
    books: list[dict[str, Any]],
    poems: list[dict[str, Any]],
    projects: list[dict[str, Any]],
) -> dict[str, tuple[str, int | None, str | None]]:
    """Return a `{title_or_url → (ascii_slug, number, kind)}` index. Keys are
    lower-cased and whitespace-normalized. LitRes URLs are tagged too."""
    index: dict[str, tuple[str, int | None, str | None]] = {}

    def add(key: str | None, ascii_slug: str, number: int | None, kind: str) -> None:
        if not key:
            return
        norm = re.sub(r"\s+", " ", key.strip().lower())
        if norm and norm not in index:
            index[norm] = (ascii_slug, number, kind)

    for b in books:
        ascii_slug = _build_ascii_slug(b, "ru")
        number = b.get("number")
        add(b.get("title"), ascii_slug, number, "book")
        if number is not None and number in EN_TITLE_OVERRIDES_BY_NUMBER:
            add(EN_TITLE_OVERRIDES_BY_NUMBER[number], ascii_slug, number, "book")
        for lang_files in (b.get("languages") or {}).values():
            for f in lang_files:
                add(f.get("title"), ascii_slug, number, "book")
    for p in poems:
        ascii_slug = _build_ascii_slug(p, "ru")
        add(p.get("title"), ascii_slug, p.get("number"), "poem")
    for pr in projects:
        ascii_slug = to_ascii_slug(pr.get("slug") or "")
        number = PROJECT_NUMBERS.get(ascii_slug)
        title = pr.get("title")
        if isinstance(title, dict):
            for v in title.values():
                add(v, ascii_slug, number, "project")
        else:
            add(title, ascii_slug, number, "project")

    return index


# ---------------------------------------------------------------------------
# title-language heuristics
# ---------------------------------------------------------------------------

_LATIN_RUN = re.compile(r"[A-Za-z]")
_CYRILLIC_RUN = re.compile(r"[А-Яа-яЁё]")


def _is_majority_latin(s: str) -> bool:
    lat = len(_LATIN_RUN.findall(s))
    cyr = len(_CYRILLIC_RUN.findall(s))
    if lat + cyr == 0:
        return True
    return lat >= cyr


def _pick_en_title(
    book: dict[str, Any],
    files_titles: list[str],
) -> str:
    """Return the best available EN title, falling back to the RU title.

    A fallback title is an editorial state, not a separate frontmatter field.
    The page already carries `translation.source: ai` when the EN text came
    from a model.
    """
    override = EN_TITLE_OVERRIDES_BY_NUMBER.get(book["number"])
    if override:
        return override
    for candidate in files_titles:
        if candidate and _is_majority_latin(candidate):
            return candidate
    return book.get("title") or ""


def _file_title(f: dict[str, Any]) -> str:
    candidate = f.get("title") or ""
    for suffix in (".clean", ".pre-cleanup-v2", ".pre-cleanup"):
        if candidate.endswith(suffix):
            candidate = candidate[: -len(suffix)]
    return candidate.strip()


# ---------------------------------------------------------------------------
# conversion drivers
# ---------------------------------------------------------------------------

@dataclass
class ConverterContext:
    content_out: Path
    image_records: list[ImageRecord]
    biblio_slug_lookup: dict[str, tuple[str, int | None, str | None]]
    cross_ref_title_index: dict[str, tuple[str, int | None, str | None]]
    previous_works: dict[str, list[str]] = field(default_factory=dict)
    work_writes: dict[str, WorkWrites] = field(default_factory=dict)
    stale_removed: int = 0

    def writes_for(self, kind: str, slug: str, work_dir: Path) -> WorkWrites:
        key = f"{kind}/{slug}"
        if key not in self.work_writes:
            self.work_writes[key] = WorkWrites(kind=kind, slug=slug, work_dir=work_dir)
        return self.work_writes[key]

    def finish_work(self, writes: WorkWrites) -> None:
        self.stale_removed += _reconcile_stale(writes, self.previous_works)


def convert_book(book: dict[str, Any], ctx: ConverterContext) -> list[ConversionOutcome]:
    ascii_slug = to_ascii_slug(book["slug"])
    book_dir = ctx.content_out / "books" / ascii_slug
    book_dir.mkdir(parents=True, exist_ok=True)
    writes = ctx.writes_for("book", ascii_slug, book_dir)
    results: list[ConversionOutcome] = []
    image_idx = 1

    annotations = book.get("annotations") or {}
    # Why: ingest covers per-language on demand inside the lang loop, so books
    # without an EN translation don't leave an orphan cover.en.svg in the
    # bundle.
    cover_paths: dict[str, str | None] = {}

    meta: dict[str, Any] = {
        "number": book["number"],
        "slug": ascii_slug,
        "title": book["title"],
        "tags": book.get("tags") or [],
        "languages": {},
        "annotations": annotations,
        "cover": book.get("cover") or {},
        "sources": {},
    }

    per_lang_biblio: dict[str, list[dict[str, Any]]] = {}

    for lang in ("ru", "en"):
        files = (book.get("languages") or {}).get(lang) or []
        docs = [f for f in files if f["format"] == "docx"]
        if lang == "en":
            docs = _pick_en_files(docs)
        if not docs:
            continue
        cover_paths[lang] = _ingest_cover(
            (book.get("cover") or {}).get(lang),
            book_slug=ascii_slug,
            work_dir=book_dir,
            lang=lang,
            image_records=ctx.image_records,
            writes=writes,
            role=f"book-cover-{lang}",
        )
        bodies: list[str] = []
        originals: list[str] = []
        bibliography: list[dict[str, Any]] = []
        cross_refs: list[dict[str, Any]] = []
        file_titles = [_file_title(f) for f in docs]

        for i, f in enumerate(docs, start=1):
            docx_path = _legacy_path(f["url"])
            writes.add_source(lang, docx_path)
            body, biblio, refs, image_idx, warns, ast, structural_key_sequences = convert_docx_to_md(
                docx=docx_path,
                book_slug=ascii_slug,
                lang=lang,
                work_dir=book_dir,
                image_records=ctx.image_records,
                writes=writes,
                image_counter_start=image_idx,
                biblio_slug_lookup=ctx.biblio_slug_lookup,
                cross_ref_title_index=ctx.cross_ref_title_index,
                own_ascii_slug=ascii_slug,
            )
            body = demote_markdown_headings(body, 2 if len(docs) > 1 else 1)
            body = normalize_ast_verse_sections(body, ast)
            body = normalize_ast_lineated_runs(body, ast, structural_key_sequences)
            body = normalize_dedication_verse_sections(body)
            body = collapse_blank_lines(body)
            label = _book_part_label(i, len(docs), lang)
            if label:
                bodies.append(f"{label}\n\n{body}")
            else:
                bodies.append(body)
            originals.append(docx_path.name)
            bibliography.extend(biblio)
            cross_refs.extend(refs)
            if warns:
                print(f"  [{ascii_slug}/{lang}] pandoc: {warns}", file=sys.stderr)

        merged_override = TITLE_OVERRIDES_BY_NUMBER.get(book["number"]) or {}
        if lang == "ru":
            title_for_lang = merged_override.get("ru") or book["title"]
            if not merged_override and len(docs) == 1 and file_titles and file_titles[0]:
                title_for_lang = file_titles[0]
        else:
            if merged_override.get("en"):
                title_for_lang = merged_override["en"]
            else:
                title_for_lang = _pick_en_title(book, file_titles)

        cover_path = cover_paths.get(lang)
        cover_is_placeholder = False
        if not cover_path:
            cover_path = cover_paths.get("ru")
            cover_is_placeholder = True
        elif lang == "en" and cover_path and cover_path.endswith(".svg"):
            cover_is_placeholder = True

        description = (DESCRIPTION_OVERRIDES_BY_NUMBER.get(book["number"]) or {}).get(lang, "")
        if not description:
            description_source = annotations.get(lang) or annotations.get("ru") or {}
            description = (description_source.get("text") or "").strip()
        if not description:
            description = f"TODO: editorial description needed for book {book['number']}."

        fm: dict[str, Any] = {
            "kind": "book",
            "number": book["number"],
            "slug": ascii_slug,
            "title": title_for_lang,
            "lang": lang,
            "description": description,
            "tags": book.get("tags") or [],
            "cover": cover_path,
        }
        if cover_is_placeholder:
            fm["cover_is_placeholder"] = True
        if cross_refs:
            fm["cross_refs"] = _restructure_cross_refs(cross_refs)
        fm["translation"] = _infer_translation(book, lang)
        if bibliography:
            per_lang_biblio[lang] = _dedupe_bibliography(bibliography)

        md = _yaml_frontmatter(fm) + "\n\n".join(b.strip() for b in bodies) + "\n"
        out_path = book_dir / f"{lang}.md"
        out_path.write_text(md, encoding="utf-8")
        writes.add(out_path)
        meta["languages"][lang] = {"original_filenames": originals, "title": title_for_lang}
        meta["sources"][lang] = [f["url"] for f in files]
        results.append(ConversionOutcome(ascii_slug=ascii_slug, lang=lang, md_path=out_path))

    _write_bibliography_sidecar(book_dir, per_lang_biblio, writes)
    meta_path = book_dir / "meta.json"
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    writes.add(meta_path)
    ctx.finish_work(writes)
    return results


def _dedupe_bibliography(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for entry in entries:
        key = (entry.get("title", ""), entry.get("source_url", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(entry)
    return out


def _restructure_cross_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Why: already in {target, source, snippet} shape; just return.
    return refs


def _write_bibliography_sidecar(work_dir: Path, per_lang: dict[str, list[dict[str, Any]]], writes: WorkWrites) -> None:
    if not per_lang:
        return
    # Why: prefer the RU bibliography when both languages produced one; the EN
    # variant is usually a translation of the same catalog.
    primary_lang = "ru" if "ru" in per_lang else next(iter(per_lang))
    entries = per_lang[primary_lang]
    sidecar = {
        "kind": "catalog_snapshot",
        "lang": primary_lang,
        "source": "docx_endmatter",
        "entries": entries,
    }
    body = yaml.safe_dump(
        sidecar, allow_unicode=True, sort_keys=False, default_flow_style=False, width=10_000,
    )
    sidecar_path = work_dir / "bibliography.yaml"
    sidecar_path.write_text(body, encoding="utf-8")
    writes.add(sidecar_path)


def _infer_translation(
    book: dict[str, Any],
    lang: str,
) -> dict[str, Any]:
    if lang == "ru":
        return {"source": "original"}
    # Why: corpus contains no human literary translations of these books; every
    # English variant was generated. Mark accordingly so the UI badge can show
    # "AI translation". `model` is omitted when unrecorded.
    return {"source": "ai"}


def convert_poem(poem: dict[str, Any], ctx: ConverterContext) -> ConversionOutcome | None:
    ascii_slug = to_ascii_slug(poem["slug"])
    poem_dir = ctx.content_out / "poetry" / ascii_slug
    poem_dir.mkdir(parents=True, exist_ok=True)
    writes = ctx.writes_for("poem", ascii_slug, poem_dir)
    docs = [f for f in poem.get("files", []) if f["format"] == "docx"]
    if not docs:
        return None
    f = docs[0]
    docx_path = _legacy_path(f["url"])
    writes.add_source("ru", docx_path)
    body, refs, _, warns = convert_poem_docx_to_md(
        docx=docx_path,
        title=poem["title"],
        book_slug=f"poem-{ascii_slug}",
        work_dir=poem_dir,
        image_records=ctx.image_records,
        writes=writes,
        image_counter_start=1,
        cross_ref_title_index=ctx.cross_ref_title_index,
        own_ascii_slug=ascii_slug,
    )
    if warns:
        print(f"  [poem/{ascii_slug}] pandoc: {warns}", file=sys.stderr)
    cover_path = _ingest_cover(
        poem.get("cover"),
        book_slug=f"poem-{ascii_slug}",
        work_dir=poem_dir,
        lang="ru",
        image_records=ctx.image_records,
        writes=writes,
        role="poem-cover",
    )
    description = (poem.get("intro") or "").strip()
    if not description:
        description = f"Стихотворение №{poem['number']}: {poem.get('title', '').strip()}"
    fm: dict[str, Any] = {
        "kind": "poem",
        "number": poem["number"],
        "slug": ascii_slug,
        "title": poem["title"],
        "lang": "ru",
        "description": description,
        "cover": cover_path,
        "date": _normalize_date(poem.get("date")),
        "translation": {"source": "original"},
    }
    if refs:
        fm["cross_refs"] = _restructure_cross_refs(refs)
    md = _yaml_frontmatter(fm) + body
    out_path = poem_dir / "ru.md"
    out_path.write_text(md, encoding="utf-8")
    writes.add(out_path)
    ctx.finish_work(writes)
    return ConversionOutcome(ascii_slug=ascii_slug, lang="ru", md_path=out_path)


_DATE_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")


def _normalize_date(raw: Any) -> str | None:
    if not raw:
        return None
    s = str(raw).strip()
    m = _DATE_RE.search(s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})", s)
    if m:
        d, mo, y = m.group(1), m.group(2), m.group(3)
        if len(y) == 2:
            y = "20" + y
        return f"{y}-{int(mo):02d}-{int(d):02d}"
    months = {
        "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
        "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    }
    m = re.search(r"(?i)([a-z]+)\s+(\d{1,2}),\s+(\d{4})", s)
    if m and m.group(1).lower() in months:
        return f"{m.group(3)}-{months[m.group(1).lower()]:02d}-{int(m.group(2)):02d}"
    return None


def convert_project(project: dict[str, Any], ctx: ConverterContext) -> list[ConversionOutcome]:
    slug = project["slug"]
    ascii_slug = to_ascii_slug(slug)
    proj_dir = ctx.content_out / "projects" / ascii_slug
    proj_dir.mkdir(parents=True, exist_ok=True)
    writes = ctx.writes_for("project", ascii_slug, proj_dir)
    docx_rel = project.get("docx")
    if not docx_rel:
        return []
    docx_path = _legacy_path(docx_rel)
    if not docx_path.exists():
        print(f"  [project/{ascii_slug}] no docx at {docx_path}", file=sys.stderr)
        return []
    title_raw = project.get("title")
    titles: dict[str, str] = {}
    if isinstance(title_raw, dict):
        titles["ru"] = title_raw.get("ru", "")
        titles["en"] = title_raw.get("en", "")
    elif isinstance(title_raw, str):
        titles["ru"] = title_raw
        titles["en"] = title_raw
    number = PROJECT_NUMBERS.get(ascii_slug)
    if number is None:
        print(f"  [project/{ascii_slug}] no number assigned in PROJECT_NUMBERS", file=sys.stderr)
        return []

    body, biblio, refs, _, warns, ast, structural_key_sequences = convert_docx_to_md(
        docx=docx_path,
        book_slug=f"project-{ascii_slug}",
        lang="ru",
        work_dir=proj_dir,
        image_records=ctx.image_records,
        writes=writes,
        image_counter_start=1,
        biblio_slug_lookup=ctx.biblio_slug_lookup,
        cross_ref_title_index=ctx.cross_ref_title_index,
        own_ascii_slug=ascii_slug,
    )
    if warns:
        print(f"  [project/{ascii_slug}] pandoc: {warns}", file=sys.stderr)
    body = demote_markdown_headings(body, 1)
    body = normalize_ast_verse_sections(body, ast)
    body = normalize_ast_lineated_runs(body, ast, structural_key_sequences)
    cover_ru = _ingest_cover(
        project.get("cover"),
        book_slug=f"project-{ascii_slug}",
        work_dir=proj_dir,
        lang="ru",
        image_records=ctx.image_records,
        writes=writes,
        role="project-cover",
    )
    outcomes: list[ConversionOutcome] = []
    description_ru = (project.get("intro") or "").strip()
    if not description_ru:
        description_ru = titles.get("ru", "")
    # The current project entries have one Russian source document and two UI
    # locale pages. Register source provenance once, under RU; EN does not
    # imply an authored EN DOCX artifact exists.
    writes.add_source("ru", docx_path)
    for lang in ("ru", "en"):
        title_for_lang = titles.get(lang) or titles.get("ru", "")
        if not title_for_lang:
            continue
        fm: dict[str, Any] = {
            "kind": "project",
            "number": number,
            "slug": ascii_slug,
            "title": title_for_lang,
            "lang": lang,
            "description": description_ru,
            "cover": cover_ru,
            "translation": {"source": "original"} if lang == "ru" else {"source": "ai"},
        }
        if refs:
            fm["cross_refs"] = _restructure_cross_refs(refs)
        md = _yaml_frontmatter(fm) + body
        out_path = proj_dir / f"{lang}.md"
        out_path.write_text(md, encoding="utf-8")
        writes.add(out_path)
        outcomes.append(ConversionOutcome(ascii_slug=ascii_slug, lang=lang, md_path=out_path))
    if biblio:
        _write_bibliography_sidecar(proj_dir, {"ru": _dedupe_bibliography(biblio)}, writes)
    ctx.finish_work(writes)
    return outcomes


def _yaml_frontmatter(d: dict[str, Any]) -> str:
    body = yaml.safe_dump(
        d, allow_unicode=True, sort_keys=False, default_flow_style=False, width=10_000,
    ).strip()
    return f"---\n{body}\n---\n\n"


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------

_BOOK_SLUG_TO_WORK_KEY_PREFIX: dict[str, str] = {
    "poem-": "poem/",
    "project-": "project/",
}


def _image_book_slug_to_work_key(book_slug: str) -> str:
    # Why: book_slug is recorded on ImageRecord as either an ascii_slug (books)
    # or `poem-<slug>` / `project-<slug>` (poems and projects). Convert it to
    # the canonical `<kind>/<slug>` work key.
    for prefix, kind in _BOOK_SLUG_TO_WORK_KEY_PREFIX.items():
        if book_slug.startswith(prefix):
            return kind + book_slug[len(prefix):]
    return "book/" + book_slug


def write_manifest(
    records: list[ImageRecord],
    work_writes: dict[str, WorkWrites],
    previous_full: dict[str, Any],
    path: Path,
) -> dict[str, Any]:
    # Why: build by_work first (carried-forward + current run), then derive
    # by_hash and stats from the *merged* corpus view, so partial runs don't
    # leave conversion-manifest.json internally inconsistent.
    by_work_images: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        work_key = _image_book_slug_to_work_key(r.book_slug)
        by_work_images.setdefault(work_key, []).append({
            "image_index": r.image_index,
            "original_filename": r.original_filename,
            "hash": r.media_hash,
            "ext": r.ext,
            "role": r.role,
        })

    by_work: dict[str, dict[str, Any]] = {}
    converted_keys = set(work_writes.keys())
    prev_by_work = previous_full.get("by_work") or {}
    for key, entry in prev_by_work.items():
        if key not in converted_keys and isinstance(entry, dict):
            by_work[key] = entry
    for key, writes in work_writes.items():
        # Why: rewrite this owner's slot only; carry forward any other owner
        # slots (e.g. `docx_optimize`) from the previous manifest.
        prev_gp: dict[str, Any] = {}
        prev_entry = prev_by_work.get(key)
        if isinstance(prev_entry, dict):
            existing = prev_entry.get("generated_paths")
            if isinstance(existing, dict):
                prev_gp = {k: v for k, v in existing.items() if k != WORK_OWNER}
        prev_gp[WORK_OWNER] = sorted(writes.paths)
        by_work[key] = {
            "kind": writes.kind,
            "slug": writes.slug,
            "generated_paths": prev_gp,
            "sources": writes.sources,
            "images": by_work_images.get(key, []),
        }

    prev_by_hash = previous_full.get("by_hash") or {}
    by_hash: dict[str, dict[str, Any]] = {}
    role_totals: dict[str, int] = {}
    total_refs = 0
    total_bytes_pre = 0

    def _bytes_for(media_hash: str) -> int:
        # Why: ImageRecord carries bytes for this run; carried-forward entries
        # only know hash + ext + role. Look up bytes from the previous by_hash.
        return int((prev_by_hash.get(media_hash) or {}).get("bytes", 0))

    bytes_by_hash: dict[str, tuple[int, str]] = {}
    for r in records:
        bytes_by_hash[r.media_hash] = (r.bytes, r.ext)

    for work_key, entry in by_work.items():
        slug_for_appears = entry.get("slug") or work_key.split("/", 1)[-1]
        for img in entry.get("images") or []:
            media_hash = img.get("hash")
            ext = img.get("ext", "")
            role = img.get("role", "body")
            if media_hash in bytes_by_hash:
                size, ext_real = bytes_by_hash[media_hash]
            else:
                size = _bytes_for(media_hash)
                ext_real = ext
            rec = by_hash.setdefault(media_hash, {
                "hash": media_hash,
                "ext": ext_real,
                "bytes": size,
                "roles": set(),
                "appears_in": [],
            })
            rec["roles"].add(role)
            rec["appears_in"].append({
                "book_slug": slug_for_appears,
                "image_index": img.get("image_index", 0),
                "original_filename": img.get("original_filename", ""),
                "role": role,
            })
            role_totals[role] = role_totals.get(role, 0) + 1
            total_refs += 1
            total_bytes_pre += size

    for v in by_hash.values():
        v["roles"] = sorted(v["roles"])
    unique_bytes = sum(v["bytes"] for v in by_hash.values())

    manifest = {
        "stats": {
            "total_image_refs": total_refs,
            "unique_images": len(by_hash),
            "bytes_before_dedup": total_bytes_pre,
            "bytes_after_dedup": unique_bytes,
            "bytes_saved": total_bytes_pre - unique_bytes,
            "role_counts": role_totals,
        },
        "by_work": by_work,
        "by_hash": by_hash,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

TEST_BOOK_NUMBERS = [1, 33, 53, 3]
TEST_POEM_NUMBERS = [2]
WORK_KINDS = ("book", "poem")


def run(args: argparse.Namespace) -> None:
    if args.test:
        content_out = TEST_CONTENT_OUT if args.out_content is None else Path(args.out_content)
        manifest_path = TEST_MANIFEST_PATH if args.manifest is None else Path(args.manifest)
    else:
        content_out = CONTENT_OUT if args.out_content is None else Path(args.out_content)
        manifest_path = MANIFEST_PATH if args.manifest is None else Path(args.manifest)

    content_out.mkdir(parents=True, exist_ok=True)

    lib = load_library()
    poetry_data = load_poetry()

    books = lib["books"]
    poems = poetry_data["works"]

    biblio_slug_lookup = _build_slug_lookups(books, poems, [])

    image_records: list[ImageRecord] = []

    books_to_do: list[dict[str, Any]] = []
    poems_to_do: list[dict[str, Any]] = []

    selected_kinds = set(args.kind or [])

    if "book" in selected_kinds:
        books_to_do = books
    if "poem" in selected_kinds:
        poems_to_do = poems

    if args.test:
        books_to_do = [b for b in books if b["number"] in TEST_BOOK_NUMBERS]
        poems_to_do = [p for p in poems if p["number"] in TEST_POEM_NUMBERS]
        projects_to_do = []
    elif args.number is not None:
        if "book" in selected_kinds:
            books_to_do = [b for b in books if b["number"] == args.number]
            if not books_to_do:
                raise SystemExit(f"book not found: {args.number}")
        elif "poem" in selected_kinds:
            poems_to_do = [p for p in poems if p["number"] == args.number]
            if not poems_to_do:
                raise SystemExit(f"poem not found: {args.number}")
    print(f"books to convert: {len(books_to_do)}", file=sys.stderr)
    print(f"poems to convert: {len(poems_to_do)}", file=sys.stderr)

    # Why: --clean is the explicit destructive maintenance path. Without it the
    # rerun is additive: it only touches files the prior manifest says the
    # converter generated, and leaves unknown author-added neighbors alone.
    # Clean only selected work bundles; never delete whole kind directories.
    cleaned_work_keys: set[str] = set()
    if args.clean:
        clean_targets: list[tuple[str, str, Path]] = []
        clean_targets.extend(
            ("book", _build_ascii_slug(b, "ru"), content_out / "books" / _build_ascii_slug(b, "ru"))
            for b in books_to_do
        )
        clean_targets.extend(
            ("poem", _build_ascii_slug(p, "ru"), content_out / "poetry" / _build_ascii_slug(p, "ru"))
            for p in poems_to_do
        )
        for kind, slug, target in clean_targets:
            cleaned_work_keys.add(f"{kind}/{slug}")
            if target.exists():
                shutil.rmtree(target)

    previous_full: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            previous_full = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            previous_full = {}
    if cleaned_work_keys:
        by_work = previous_full.get("by_work")
        if isinstance(by_work, dict):
            for key in cleaned_work_keys:
                by_work.pop(key, None)
    previous_works = _load_previous_works(manifest_path)

    ctx = ConverterContext(
        content_out=content_out,
        image_records=image_records,
        biblio_slug_lookup=biblio_slug_lookup,
        cross_ref_title_index=biblio_slug_lookup,
        previous_works=previous_works,
    )

    for b in books_to_do:
        print(f"book {b['number']:>3} {b['slug']}", file=sys.stderr)
        convert_book(b, ctx)
    for p in poems_to_do:
        print(f"poem {p['number']:>3} {p['slug']}", file=sys.stderr)
        convert_poem(p, ctx)

    m = write_manifest(image_records, ctx.work_writes, previous_full, manifest_path)
    s = m["stats"]
    print(
        f"\nimages: {s['total_image_refs']} refs across docx → "
        f"{s['unique_images']} unique after dedup\n"
        f"size: {s['bytes_before_dedup'] / 1e6:.1f} MB before dedup → "
        f"{s['bytes_after_dedup'] / 1e6:.1f} MB on disk "
        f"(saved {s['bytes_saved'] / 1e6:.1f} MB)\n"
        f"roles: {s['role_counts']}\n"
        f"stale files removed: {ctx.stale_removed}",
        file=sys.stderr,
    )
    print(f"manifest written to {manifest_path}", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Convert selected Pancratius legacy DOCX sources to work-bundle Markdown.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument(
        "--kind",
        action="append",
        choices=WORK_KINDS,
        help="Work kind to convert. Repeat for multiple kinds.",
    )
    ap.add_argument(
        "--number",
        type=int,
        help="Convert one book or poem by corpus number. Requires exactly one --kind book or --kind poem.",
    )
    ap.add_argument(
        "--test",
        action="store_true",
        help="Convert the small test set into .cache/converter-test/ unless output paths are overridden.",
    )
    ap.add_argument(
        "--clean",
        action="store_true",
        help="Remove selected work folders before regenerating them.",
    )
    ap.add_argument("--out-content", default=None, help="Content output root.")
    ap.add_argument("--manifest", default=None, help="Conversion manifest output path.")
    return ap


def validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    args.kind = list(dict.fromkeys(args.kind or []))

    if args.test:
        if args.kind or args.number is not None:
            parser.error("--test cannot be combined with --kind or --number")
        return

    if not args.kind:
        parser.error("choose at least one --kind (book or poem), or use --test")

    if args.number is not None:
        if len(args.kind) != 1 or args.kind[0] not in {"book", "poem"}:
            parser.error("--number requires exactly one --kind book or --kind poem")


def main() -> None:
    ap = build_parser()
    args = ap.parse_args()
    validate_args(ap, args)
    if shutil.which("pandoc") is None:
        ap.error("pandoc not found on PATH; install with `brew install pandoc`")

    run(args)


if __name__ == "__main__":
    main()
