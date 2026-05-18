#!/usr/bin/env -S uv run --quiet
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "pyyaml>=6.0",
# ]
# ///
"""
docx_to_md.py — convert Sergey Orekhov's .docx corpus (books, poetry, projects)
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
  6. emit ASCII-slug work bundles under content/<kind>/<ascii-slug>/ with
     <lang>.md, cover.<lang>.<ext>, optional bibliography.yaml, meta.json,
     and images/. Frontmatter satisfies src/content.config.ts strict schema.

The converter is additive by default: every write is recorded in
data/conversion-manifest.json under by_work[<kind/slug>].generated_paths
(relative to the work folder), and reruns only delete stale entries the new
run does not reproduce. Unknown author-added neighbors survive. `--clean` is
the explicit destructive maintenance path; it wipes content/{books,poetry,
projects} and the manifest before regenerating.

Run:
    uv run scripts/docx_to_md.py --all
    uv run scripts/docx_to_md.py --book 33
    uv run scripts/docx_to_md.py --poem 2
    uv run scripts/docx_to_md.py --test
    uv run scripts/docx_to_md.py --all --clean
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import yaml

ROOT = Path(__file__).resolve().parent.parent
LEGACY = ROOT / "legacy"
DATA = LEGACY / "data"

CONTENT_OUT = ROOT / "content"
MANIFEST_PATH = ROOT / "data" / "conversion-manifest.json"

TEST_CONTENT_OUT = ROOT / ".cache" / "converter-test" / "content"
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

_IMG_MD = re.compile(r"!\[([^\]]*)\]\(([^)]+?)\)")
_IMG_HTML = re.compile(r"<img\s+([^>]*?)src\s*=\s*\"([^\"]+)\"([^>]*?)/?>", re.IGNORECASE)
_HTML_DIM_ATTR = re.compile(r"\s+(?:style|width|height)\s*=\s*\"[^\"]*\"")

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

    def add(self, p: Path) -> None:
        self.paths.add(p.relative_to(self.work_dir).as_posix())


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


def normalize_verse_body(md: str) -> str:
    """Verse contract: within a stanza, lines sit on adjacent source lines;
    blank line separates stanzas; no trailing `\\`, no two-space hard breaks.

    Pandoc writes one of two shapes when reading a poem DOCX:

    1. **Stanza-aware**: lines within a stanza use trailing `\\` hard breaks
       (the author pressed Shift+Enter); stanzas are separated by blank lines.
       Detected by any `\\\\\\n` in the pandoc output. We just strip the `\\`s
       and the resulting structure already matches the contract: each stanza
       is one paragraph of newline-joined lines, blank lines remain between
       stanzas.

    2. **Flat (paragraph-per-line)**: every line is its own Word paragraph
       and pandoc emits a blank line after each one. No `\\` hard breaks
       anywhere. There is no stanza signal — author intent is single stanza.
       Collapse `\\n\\n` to `\\n` so the poem renders without wide line gaps.
       Preserve `\\n{3,}` if any (rare) as a defensive stanza marker.

    Mixing both styles in one poem is rare; if any `\\` appears, the whole
    poem is treated as stanza-aware (safer: we never destroy a stanza break
    that pandoc made visible).
    """
    has_hard_breaks = "\\\n" in md
    lines = []
    for line in md.split("\n"):
        line = re.sub(r"[ \t]+$", "", line)
        line = re.sub(r"\\$", "", line).rstrip()
        lines.append(line)
    body = "\n".join(lines)
    if not has_hard_breaks:
        sentinel = "\x00STANZA\x00"
        body = re.sub(r"\n{3,}", sentinel, body)
        body = body.replace("\n\n", "\n")
        body = body.replace(sentinel, "\n\n")
    else:
        body = re.sub(r"\n{3,}", "\n\n", body)
    return body.strip() + "\n"


# ---------------------------------------------------------------------------
# image rewriting (pandoc temp → work-bundle `images/<hash>.<ext>`)
# ---------------------------------------------------------------------------

def rewrite_images(
    md: str,
    book_slug: str,
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
        alt = m.group(1)
        src = m.group(2).split(' "', 1)[0].strip()
        got = resolve(src)
        if not got:
            return m.group(0)
        h, ext = got
        record(src, h, ext, "body")
        return f"![{alt}](./images/{h}{ext})"

    def html_repl(m: re.Match) -> str:
        pre, src, post = m.group(1), m.group(2), m.group(3)
        got = resolve(src)
        if not got:
            return m.group(0)
        h, ext = got
        record(src, h, ext, "body")
        attrs = (pre + post)
        attrs = _HTML_DIM_ATTR.sub("", attrs)
        attrs = re.sub(r"\s+", " ", attrs).strip()
        if attrs:
            return f'<img {attrs} src="./images/{h}{ext}" />'
        return f'<img src="./images/{h}{ext}" />'

    md = _IMG_MD.sub(md_repl, md)
    md = _IMG_HTML.sub(html_repl, md)
    return md, next_idx


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
    book_slug: str,
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
    md, next_idx = rewrite_images(
        md, book_slug, image_root, work_dir, image_records, writes, image_counter_start,
    )
    cross_refs = extract_cross_refs(md, own_ascii_slug, cross_ref_title_index)
    md = unwrap_spans_and_u(md)
    md = strip_formatting_artifacts(md)
    md = strip_bold_only_headings(md)
    md = strip_empty_headings(md)
    md = normalize_dialogue_labels(md)
    md = collapse_blank_lines(md)
    return md, bibliography, cross_refs, next_idx


def convert_docx_to_md(
    docx: Path,
    book_slug: str,
    work_dir: Path,
    image_records: list[ImageRecord],
    writes: WorkWrites,
    image_counter_start: int,
    biblio_slug_lookup: dict[str, tuple[str, int | None, str | None]],
    cross_ref_title_index: dict[str, tuple[str, int | None, str | None]],
    own_ascii_slug: str,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], int, str]:
    if not docx.exists():
        raise FileNotFoundError(docx)
    with tempfile.TemporaryDirectory(prefix=f"pancratius-{book_slug}-") as td:
        tdp = Path(td)
        out_md = tdp / "out.md"
        media_tmp = tdp / "media"
        warnings = _run_pandoc(docx, media_tmp, out_md)
        md_raw = out_md.read_text(encoding="utf-8")
        md, biblio, refs, next_idx = process_markdown(
            md_raw=md_raw,
            book_slug=book_slug,
            image_root=tdp,
            work_dir=work_dir,
            image_records=image_records,
            writes=writes,
            image_counter_start=image_counter_start,
            biblio_slug_lookup=biblio_slug_lookup,
            cross_ref_title_index=cross_ref_title_index,
            own_ascii_slug=own_ascii_slug,
        )
    return md, biblio, refs, next_idx, warnings


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
) -> tuple[str, bool]:
    """Return (title, title_is_untranslated)."""
    override = EN_TITLE_OVERRIDES_BY_NUMBER.get(book["number"])
    if override:
        return override, False
    for candidate in files_titles:
        if candidate and _is_majority_latin(candidate):
            return candidate, False
    return book.get("title") or "", True


def body_is_majority_latin(md: str, sample_chars: int = 5000) -> bool:
    """Approximate language detection over the body. Skips YAML frontmatter
    if present. We use it to flag files that landed in `legacy/books/en/`
    but actually carry Russian content — in that case the en.md is not a
    real translation and we mark it as such (title untranslated, no AI
    badge claim)."""
    s = md
    if s.startswith("---"):
        end = s.find("\n---", 3)
        if end > 0:
            s = s[end + 4:]
    s = s[:sample_chars]
    lat = sum(1 for c in s if c.isascii() and c.isalpha())
    cyr = sum(1 for c in s if "Ѐ" <= c <= "ӿ")
    if lat + cyr < 100:
        return False
    return lat >= cyr


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
            body, biblio, refs, image_idx, warns = convert_docx_to_md(
                docx=docx_path,
                book_slug=ascii_slug,
                work_dir=book_dir,
                image_records=ctx.image_records,
                writes=writes,
                image_counter_start=image_idx,
                biblio_slug_lookup=ctx.biblio_slug_lookup,
                cross_ref_title_index=ctx.cross_ref_title_index,
                own_ascii_slug=ascii_slug,
            )
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
        body_text = "\n".join(bodies)
        if lang == "ru":
            title_for_lang = merged_override.get("ru") or book["title"]
            title_is_untranslated = False
            if not merged_override and len(docs) == 1 and file_titles and file_titles[0]:
                title_for_lang = file_titles[0]
        else:
            if merged_override.get("en"):
                title_for_lang = merged_override["en"]
                title_is_untranslated = False
            else:
                title_for_lang, title_is_untranslated = _pick_en_title(book, file_titles)
            if not body_is_majority_latin(body_text):
                title_is_untranslated = True

        cover_path = cover_paths.get(lang)
        cover_is_placeholder = False
        if not cover_path:
            cover_path = cover_paths.get("ru")
            cover_is_placeholder = True
        elif lang == "en" and cover_path and cover_path.endswith(".svg"):
            cover_is_placeholder = True

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
            "original_filenames": originals,
            "tags": book.get("tags") or [],
            "cover": cover_path,
        }
        if title_is_untranslated:
            fm["title_is_untranslated"] = True
        if cover_is_placeholder:
            fm["cover_is_placeholder"] = True
        if cross_refs:
            fm["cross_refs"] = _restructure_cross_refs(cross_refs)
        fm["translation"] = _infer_translation(book, lang, title_is_untranslated)
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
    title_is_untranslated: bool,
) -> dict[str, Any]:
    if lang == "ru":
        return {"source": "original"}
    # Why: corpus contains no human literary translations of these books; every
    # English variant was generated. Mark accordingly so the UI badge can show
    # "AI translation" while title_is_untranslated separately signals that the
    # frontmatter title is still Russian. `model` is omitted when unrecorded.
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
    body, biblio, refs, _, warns = convert_docx_to_md(
        docx=docx_path,
        book_slug=f"poem-{ascii_slug}",
        work_dir=poem_dir,
        image_records=ctx.image_records,
        writes=writes,
        image_counter_start=1,
        biblio_slug_lookup=ctx.biblio_slug_lookup,
        cross_ref_title_index=ctx.cross_ref_title_index,
        own_ascii_slug=ascii_slug,
    )
    body = normalize_verse_body(body)
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
        "original_filename": docx_path.name,
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
    if biblio:
        _write_bibliography_sidecar(poem_dir, {"ru": _dedupe_bibliography(biblio)}, writes)
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

    body, biblio, refs, _, warns = convert_docx_to_md(
        docx=docx_path,
        book_slug=f"project-{ascii_slug}",
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
            "original_filename": docx_path.name,
            "cover": cover_ru,
            "translation": {"source": "original"} if lang == "ru" else {"source": "ai"},
        }
        if lang == "en" and not titles.get("en"):
            fm["title_is_untranslated"] = True
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


def run(args: argparse.Namespace) -> None:
    if args.test:
        content_out = TEST_CONTENT_OUT
        manifest_path = TEST_MANIFEST_PATH
    else:
        content_out = CONTENT_OUT if args.out_content is None else Path(args.out_content)
        manifest_path = MANIFEST_PATH if args.manifest is None else Path(args.manifest)

    content_out.mkdir(parents=True, exist_ok=True)

    lib = load_library()
    poetry_data = load_poetry()
    projects_data = load_projects()

    books = lib["books"]
    poems = poetry_data["works"]
    projects = projects_data["projects"]

    biblio_slug_lookup = _build_slug_lookups(books, poems, projects)

    image_records: list[ImageRecord] = []

    books_to_do = books
    poems_to_do = poems
    projects_to_do = projects

    if args.test:
        books_to_do = [b for b in books if b["number"] in TEST_BOOK_NUMBERS]
        poems_to_do = [p for p in poems if p["number"] in TEST_POEM_NUMBERS]
        projects_to_do = []
    if args.book:
        books_to_do = [b for b in books if b["number"] == args.book]
        poems_to_do = []
        projects_to_do = []
    if args.poem:
        poems_to_do = [p for p in poems if p["number"] == args.poem]
        books_to_do = []
        projects_to_do = []
    if args.no_books:
        books_to_do = []
    if args.no_poems:
        poems_to_do = []
    if args.no_projects:
        projects_to_do = []

    print(f"books to convert: {len(books_to_do)}", file=sys.stderr)
    print(f"poems to convert: {len(poems_to_do)}", file=sys.stderr)
    print(f"projects to convert: {len(projects_to_do)}", file=sys.stderr)

    # Why: --clean is the explicit destructive maintenance path. Without it the
    # rerun is additive: it only touches files the prior manifest says the
    # converter generated, and leaves unknown author-added neighbors alone.
    if args.clean:
        for sub in ("books", "poetry", "projects"):
            sub_path = content_out / sub
            if sub_path.exists():
                shutil.rmtree(sub_path)
            sub_path.mkdir(parents=True, exist_ok=True)
        if manifest_path.exists():
            manifest_path.unlink()

    previous_full: dict[str, Any] = {}
    if manifest_path.exists() and not args.clean:
        try:
            previous_full = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            previous_full = {}
    previous_works = _load_previous_works(manifest_path) if not args.clean else {}

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
    for pr in projects_to_do:
        print(f"project {pr['slug']}", file=sys.stderr)
        convert_project(pr, ctx)

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


def main() -> None:
    ap = argparse.ArgumentParser(description="Convert Pancratius .docx corpus to Markdown.")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--book", type=int)
    ap.add_argument("--poem", type=int)
    ap.add_argument("--no-books", action="store_true")
    ap.add_argument("--no-poems", action="store_true")
    ap.add_argument("--no-projects", action="store_true")
    ap.add_argument(
        "--clean",
        action="store_true",
        help="Destructive: wipe content/books, content/poetry, content/projects, and the manifest before regenerating. Default is additive: preserve unknown author-added files in each work bundle and remove only files the prior manifest recorded as generated.",
    )
    ap.add_argument("--out-content", default=None)
    ap.add_argument("--manifest", default=None)
    args = ap.parse_args()

    if not any([args.all, args.test, args.book, args.poem]):
        ap.error("specify --all, --test, --book N, or --poem N")
    if shutil.which("pandoc") is None:
        ap.error("pandoc not found on PATH; install with `brew install pandoc`")

    run(args)


if __name__ == "__main__":
    main()
