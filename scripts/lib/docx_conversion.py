from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
import sys
from typing import Any

import yaml

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from lib import cross_refs, docx_adapter, ir_lower, ir_normalize, ooxml
from lib.writeplan import PlannedAsset


@dataclass
class ConvertedDocx:
    body: str
    bibliography: list[dict[str, Any]] = field(default_factory=list)
    cross_refs: list[dict[str, Any]] = field(default_factory=list)
    warnings: str = ""
    # Planned body images the conversion REFERENCES but does not copy. The
    # converter is pure w.r.t. the filesystem (it only reads the extracted
    # media); the importer turns these into writer `transform_asset` ops, so the
    # writer is the sole component that copies them into the bundle.
    assets: list[PlannedAsset] = field(default_factory=list)


# ---------------------------------------------------------------------------
# slug
# ---------------------------------------------------------------------------

# Why: the corpus uses Cyrillic ASCII-ish slugs from the legacy site
# (e.g. `тои` for `той`, `выи` for `вый`). We freeze a practical
# transliteration that matches that historical choice so existing slugs
# round-trip stably to ASCII without ё/й/ц collisions.
_CYR_TO_LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "zh", "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}

_SLUG_NONALNUM = re.compile(r"[^a-z0-9]+")
_SLUG_DASHES = re.compile(r"-+")


def to_ascii_slug(value: str) -> str:
    s = "".join(_CYR_TO_LAT.get(ch, ch) for ch in value.lower())
    s = unicodedata.normalize("NFKD", s)
    s = s.encode("ascii", "ignore").decode("ascii")
    s = _SLUG_NONALNUM.sub("-", s.lower())
    s = _SLUG_DASHES.sub("-", s).strip("-")
    return s


# ---------------------------------------------------------------------------
# poem source-duplicate-title strip (uses OOXML paragraph signals)
# ---------------------------------------------------------------------------


def _poem_title_key(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"^[#>*_`\s-]+|[*_`\s-]+$", "", s.strip())
    s = s.replace("…", "...")
    s = re.sub(r"[.,;:!?]+$", "", s)
    return re.sub(r"\s+", " ", s).casefold().strip()


def _first_nonempty_docx_paras(
    paras: list[ooxml.DocxParagraphMeta], limit: int = 2
) -> list[ooxml.DocxParagraphMeta]:
    out: list[ooxml.DocxParagraphMeta] = []
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
    docx_paras: list[ooxml.DocxParagraphMeta],
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


# ---------------------------------------------------------------------------
# bibliography sidecar
# ---------------------------------------------------------------------------


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


def convert_single_docx(
    docx: Path,
    *,
    kind: str,
    lang: str,
    work_key: str,
    title: str,
    work_dir: Path,
    title_index: dict[str, tuple[str, int | None, str | None]],
    media_out: Path,
) -> ConvertedDocx:
    """Convert one DOCX into author-facing Markdown body + sidecar data + the
    PLANNED body assets — through the typed-IR pipeline (adapter → normalize →
    lower).

    This is the converter ``import_docx`` calls. It deliberately does not read
    legacy catalogs or write manifests, and it does not copy media: pandoc
    extracts media into the caller-provided PERSISTENT ``media_out``, and the
    returned ``ConvertedDocx.assets`` reference those files. ``media_out`` must
    outlive this call until the writer copies the assets.

    The pipeline is pure after the adapter: ``ir_normalize``/``ir_lower`` perform
    no filesystem mutation; the adapter shells out to pandoc and extracts media
    only.
    """
    media_out.mkdir(parents=True, exist_ok=True)
    doc = docx_adapter.adapt(docx, media_out)

    if kind == "poem":
        # Poems are verse end-to-end: skip heading demotion, bibliography lift,
        # and structural/verse detection (the whole AST renders as verse). Light
        # cleanup still applies.
        doc.blocks = ir_normalize.drop_toc(doc.blocks)
        doc.blocks = ir_normalize.scrub_ai_alt(doc.blocks)
        doc.blocks = ir_normalize.thematic_breaks(doc.blocks)
        doc.blocks = ir_normalize.strip_formatting_artifacts(doc.blocks)
    else:
        ir_normalize.normalize(doc, demote_levels=1, slug_lookup=title_index)

    assets = ir_lower.assign_assets(doc, media_out, lang)
    body = ir_lower.lower(doc, lang, poem=(kind == "poem"))
    if kind == "poem":
        # Strip the source-duplicate title: when the DOCX itself starts with a
        # title paragraph (the masthead already renders that title) the first
        # stanza repeats the page title and must be dropped — the same drop the
        # DOCX stanza oracle (`poetry_stanzas.expected_groups`) applies. The
        # decision uses bold / line-break source signals, not a string guess.
        body = _strip_source_duplicate_poem_title(
            body, title, ooxml.read_docx_paragraph_meta(docx)
        )
    refs = cross_refs.extract_cross_refs(body, work_key, title_index)
    # Propagate pandoc warnings AND any SURFACED adapter/normalize diagnostic
    # (severity `warning`/`fatal`) to the caller — not just `import.pandoc-warn`.
    # The C1 fix added `import.align-unreconciled` (right-aligned source paragraphs
    # that no longer reconcile onto the AST); forwarding warnings here is what makes
    # the documented "fail loud" actually fire instead of being discarded.
    warning_messages = [
        d.message for d in doc.diagnostics if d.code == "import.pandoc-warn"
    ]
    warning_messages.extend(
        f"[{d.code}] {d.message}"
        for d in doc.diagnostics
        if d.severity in {"warning", "fatal"}
    )
    warnings = "\n".join(warning_messages)
    return ConvertedDocx(
        body=body,
        bibliography=_dedupe_bibliography(doc.bibliography),
        cross_refs=cross_refs.restructure_cross_refs(refs),
        warnings=warnings,
        assets=assets,
    )


def write_bibliography_sidecar(
    work_dir: Path,
    kind: str,
    lang: str,
    bibliography: list[dict[str, Any]],
) -> None:
    """Write the lifted endmatter bibliography to ``<work_dir>/bibliography.yaml``.

    A no-op when there is nothing to write. Called from the staging step in
    ``import_docx`` before the WritePlan is assembled.
    """
    if not bibliography:
        return
    sidecar = {
        "kind": "catalog_snapshot",
        "lang": lang,
        "source": "docx_endmatter",
        "entries": bibliography,
    }
    body = yaml.safe_dump(
        sidecar, allow_unicode=True, sort_keys=False, default_flow_style=False, width=10_000,
    )
    (work_dir / "bibliography.yaml").write_text(body, encoding="utf-8")
