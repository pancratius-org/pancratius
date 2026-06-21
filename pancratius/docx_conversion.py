from __future__ import annotations

import re
import shutil
import unicodedata
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import yaml

from pancratius import (
    cross_refs,
    docx_adapter,
    footnotes,
    ir,
    lineation_overrides,
    lower,
    ooxml,
    scripture_overrides,
)
from pancratius.content_catalog import IndexHit, dump_frontmatter
from pancratius.kinds import CorpusWorkKind
from pancratius.locales import Locale
from pancratius.passes import assets
from pancratius.passes.pipeline import POEM_PASSES, Context, run
from pancratius.passes.register import RegisterModel, load_register_model
from pancratius.paths import CACHE_ROOT, REPO_ROOT
from pancratius.poem_chrome import PoemChrome, clean_poem_chrome
from pancratius.writeplan import AssetTransform, Diagnostic, PlannedAsset, Role, WriteOp, WritePlan
from pancratius.writer import WriteReport
from pancratius.writer import apply as apply_plan

# Longest-edge cap (px) for converted body rasters, applied by the writer as a
# `cap_raster` transform. One owner for the work importer and the sub-page scaffold;
# vector/animated formats copy untouched (docs/content-model.md asset policy).
BODY_IMAGE_MAX_LONG_EDGE = 1600

type BibliographyEntry = dict[str, object]
type DocxConversionKind = CorpusWorkKind | Literal["project"]


@dataclass
class ConvertedDocx:
    body: str
    bibliography: list[BibliographyEntry] = field(default_factory=list)
    cross_refs: list[dict[str, Any]] = field(default_factory=list)
    warnings: str = ""
    # Body images the conversion references but never copies — the converter only
    # reads the extracted media; the writer is the sole copier.
    assets: list[PlannedAsset] = field(default_factory=list)
    # Typed diagnostics straight from the IR (severity preserved) so a converter-side
    # FATAL rides into the WritePlan and blocks the write; flattening to `warnings`
    # would lose severity.
    diagnostics: list[ir.Diagnostic] = field(default_factory=list)
    # Metadata lifted from a poem body; None for non-poem kinds.
    poem_chrome: PoemChrome | None = None


# ---------------------------------------------------------------------------
# slug
# ---------------------------------------------------------------------------

# Frozen transliteration matching the legacy site's ASCII-ish Cyrillic slugs
# (e.g. `тои` for `той`), so existing slugs round-trip without ё/й/ц collisions.
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
    return _SLUG_DASHES.sub("-", s).strip("-")


# ---------------------------------------------------------------------------
# poem source-duplicate-title strip (uses OOXML paragraph signals)
# ---------------------------------------------------------------------------


def _poem_title_key(s: str) -> str:
    """A loose comparison key for a poem title: markup, a trailing style note, and
    edge punctuation removed, case-folded. Note-tolerant so a self-sufficient DOCX
    title line ("Весна (в духе Есенина)") keys equal to the frontmatter title
    ("Весна") — the note lives in frontmatter, the title carries it for display."""
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"\s*\(\s*в\s+(?:духе|стиле)\b[^)]*\)", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^[#>*_`\s-]+|[*_`\s-]+$", "", s.strip())
    s = s.replace("…", "...")
    s = re.sub(r"[.,;:!?]+$", "", s)
    return re.sub(r"\s+", " ", s).casefold().strip()


def _strip_source_duplicate_poem_title(
    body: str,
    title: str,
    docx_paras: list[ooxml.DocxParagraphMeta],
) -> str:
    """Drop the leading title paragraph from a poem body.

    One rule, shared with the stanza oracle: the leading DOCX paragraph is the title
    iff it is BOLD. An incipit poem (where the first verse line is the title) has no
    bold paragraph, so its first line is plain verse and is kept. The title-key match
    is a safety guard against a future messy import bolding a stray line.
    """
    key = _poem_title_key(title)
    first = next((para for para in docx_paras if not para.is_empty), None)
    if not key or first is None or not first.bold or _poem_title_key(first.text) != key:
        return body

    blocks = re.split(r"\n\s*\n", body.strip(), maxsplit=1)
    if not blocks or _poem_title_key(blocks[0]) != key:
        return body
    rest = blocks[1] if len(blocks) > 1 else ""
    return rest.lstrip() + ("\n" if rest and not rest.endswith("\n") else "")


# ---------------------------------------------------------------------------
# bibliography sidecar
# ---------------------------------------------------------------------------


def _normalized_bibliography_title(entry: BibliographyEntry) -> str:
    return re.sub(r"\s+", " ", str(entry.get("title") or "").casefold()).strip()


def _bibliography_dedupe_key(entry: BibliographyEntry) -> str:
    title_key = _normalized_bibliography_title(entry)
    return title_key or f"url:{entry.get('source_url', '')}"


def _dedupe_bibliography(entries: list[BibliographyEntry]) -> list[BibliographyEntry]:
    out: list[BibliographyEntry] = []
    by_title: dict[str, int] = {}
    i = 0
    while i < len(entries):
        entry = dict(entries[i])
        if i + 1 < len(entries) and _merge_adjacent_part_link(entry, entries[i + 1]):
            nxt = entries[i + 1]
            entry["source_url"] = nxt["source_url"]
            if nxt.get("target") and not entry.get("target"):
                entry["target"] = nxt["target"]
            i += 1
        key = _bibliography_dedupe_key(entry)
        if key not in by_title:
            by_title[key] = len(out)
            out.append(dict(entry))
            i += 1
            continue
        existing = out[by_title[key]]
        if entry.get("source_url") and not existing.get("source_url"):
            existing["source_url"] = entry["source_url"]
            # Prefer the store-link label over image alt text when they differ only
            # by case/punctuation normalization; it is the navigable catalog title.
            existing["title"] = entry.get("title", existing.get("title", ""))
        if entry.get("target") and not existing.get("target"):
            existing["target"] = entry["target"]
        i += 1
    return [_ordered_bibliography_entry(entry) for entry in out]


def _merge_adjacent_part_link(entry: BibliographyEntry, nxt: BibliographyEntry) -> bool:
    """Cover alt `Full Title. Part 1` followed by link text `Part 1` is one entry."""
    if entry.get("source_url") or not nxt.get("source_url"):
        return False
    title = _normalized_bibliography_title(entry)
    linked = _normalized_bibliography_title(nxt)
    if not title or not linked:
        return False
    if not re.fullmatch(r"(?:часть|part)\s+\d+", linked):
        return False
    return title.endswith(linked)


def _ordered_bibliography_entry(entry: BibliographyEntry) -> BibliographyEntry:
    """Keep generated YAML stable: title, URL, target, then any future fields."""
    ordered: BibliographyEntry = {"title": entry.get("title", "")}
    if entry.get("source_url"):
        ordered["source_url"] = entry["source_url"]
    if entry.get("target"):
        ordered["target"] = entry["target"]
    for key, value in entry.items():
        if key not in ordered:
            ordered[key] = value
    return ordered


# The committed register-model artifact. The composition point owns artifact
# location and injection; the artifact's own `langs` field bounds its validity.
_REGISTER_MODEL_PATH = REPO_ROOT / "data" / "models" / "verse_register_v1.json"


@cache
def load_register_model_for(lang: Locale) -> RegisterModel | None:
    """The committed artifact when it covers `lang`, loaded once per process."""
    model = load_register_model(_REGISTER_MODEL_PATH)
    if model is None or lang not in model.langs:
        return None
    return model


def convert_single_docx(
    docx: Path,
    *,
    kind: DocxConversionKind,
    lang: Locale,
    work_key: str,
    title: str,
    title_index: dict[str, IndexHit],
    media_out: Path,
) -> ConvertedDocx:
    """Convert one DOCX into author-facing Markdown body + sidecar data + planned
    body assets, through the typed-IR pipeline (adapter → passes → lower).

    Copies no media: pandoc extracts into the caller's persistent `media_out` and the
    returned `ConvertedDocx.assets` reference those files, so `media_out` must outlive
    this call until the writer copies them. Pure after the adapter.
    """
    media_out.mkdir(parents=True, exist_ok=True)
    # ONE diagnostics sink for the whole conversion: the adapter, every pass (via
    # `Context`), and the backend tail all append into it.
    diagnostics: ir.DiagnosticSink = []
    doc = docx_adapter.adapt(docx, media_out, diagnostics)

    if kind == "poem":
        # Verse end-to-end: skip heading demotion, bibliography lift, and verse
        # detection (the whole AST is verse); only light cleanup applies. A poem
        # has no lineation DECISIONS to correct, so a sidecar beside it is a
        # placement error — refuse rather than silently ignore it.
        if (stray := lineation_overrides.overrides_path(docx)).is_file():
            raise ValueError(f"poem import: {stray.name} found beside {docx.name}, but poems "
                             f"take no lineation overrides — remove it")
        if (stray := scripture_overrides.overrides_path(docx)).is_file():
            raise ValueError(f"poem import: {stray.name} found beside {docx.name}, but poems "
                             f"take no scripture pins — remove it")
        doc = run(doc, Context(lang=lang, diagnostics=diagnostics), POEM_PASSES)
    else:
        register_model = load_register_model_for(lang)
        if register_model is None and not _REGISTER_MODEL_PATH.exists():
            # An absent artifact FILE is a configuration state worth recording;
            # a lang outside the artifact's validity domain is not.
            diagnostics.append(ir.Diagnostic(
                "info", "register.model",
                f"no register model artifact at {_REGISTER_MODEL_PATH.name}; rules decide alone",
            ))
        doc = run(doc, Context(
            lang=lang,
            demote_levels=1,
            slug_lookup=title_index,
            register_model=register_model,
            lineation_overrides=lineation_overrides.load_overrides(docx),
            scripture_overrides=scripture_overrides.load_overrides(docx),
            diagnostics=diagnostics,
        ))

    doc, planned_assets = assets.plan_assets(doc, media_out, diagnostics)
    body = lower.lower(doc, lang, diagnostics, poem=(kind == "poem"))
    poem_chrome: PoemChrome | None = None
    if kind == "poem":
        # A poem DOCX that opens with a bold title paragraph repeats the masthead title
        # in its first stanza; drop that one bold paragraph (an incipit is plain verse).
        body = _strip_source_duplicate_poem_title(
            body, title, ooxml.read_docx_paragraph_meta(docx)
        )
        body, poem_chrome = clean_poem_chrome(body)
    refs = cross_refs.extract_cross_refs(body, work_key, title_index)
    # Forward pandoc warnings plus any surfaced warning/fatal diagnostic, so the
    # documented "fail loud" actually fires.
    warning_messages = [
        d.message for d in diagnostics if d.code == "import.pandoc-warn"
    ]
    warning_messages.extend(
        f"[{d.code}] {d.message}"
        for d in diagnostics
        if d.severity in {"warning", "fatal"}
    )
    warnings = "\n".join(warning_messages)
    return ConvertedDocx(
        body=body,
        bibliography=_dedupe_bibliography(doc.bibliography),
        cross_refs=refs,
        warnings=warnings,
        assets=planned_assets,
        diagnostics=list(diagnostics),
        poem_chrome=poem_chrome,
    )


def write_bibliography_sidecar(
    work_dir: Path,
    lang: Locale,
    bibliography: list[BibliographyEntry],
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


# ---------------------------------------------------------------------------
# shared plan assembly (one owner for the work importer + the sub-page scaffold)
# ---------------------------------------------------------------------------


def body_asset_ops(assets: list[PlannedAsset], scope: PurePosixPath) -> list[WriteOp]:
    """Turn the converter's planned body images into scope-relative `transform_asset`
    ops: a raster caps to `BODY_IMAGE_MAX_LONG_EDGE` (the writer is the only place PIL
    runs), vector/animated copy verbatim. The path is `images/<hash>.<ext>` exactly as
    the body references it, so filenames are unchanged."""
    ops: list[WriteOp] = []
    for asset in assets:
        transform = (
            AssetTransform(kind="cap_raster", max_long_edge=BODY_IMAGE_MAX_LONG_EDGE)
            if asset.is_raster
            else AssetTransform(kind="copy")
        )
        ops.append(
            WriteOp(
                kind="transform_asset",
                rel_path=scope / PurePosixPath(asset.rel_within),
                role="imported_asset",
                reason=f"body image {asset.rel_within}",
                source=asset.source,
                transform=transform,
            )
        )
    return ops


def plan_from_staged_bundle(
    *,
    stage_dir: Path,
    content_root: Path,
    scope: PurePosixPath,
    role_for: Callable[[PurePosixPath], Role],
    asset_ops: list[WriteOp],
    diagnostics: tuple[Diagnostic, ...],
    source_document: Path,
    replace: bool,
) -> WritePlan:
    """Build a WritePlan copying every staged bundle file into the target scope,
    alongside the body-image `transform_asset` ops (not staged — the writer copies/caps
    them from the persistent media dir). `role_for` maps each staged path to its
    ownership role (the caller owns that policy). The plan is the only thing handed to
    the writer. Shared by import_docx._plan_from_scratch and scaffold_subpage."""
    ops: list[WriteOp] = [
        WriteOp(
            kind="ensure_dir",
            rel_path=scope,
            role="canonical_source",
            reason="bundle directory",
        )
    ]
    for staged in sorted(stage_dir.rglob("*")):
        if not staged.is_file():
            continue
        rel_within = PurePosixPath(staged.relative_to(stage_dir).as_posix())
        ops.append(
            WriteOp(
                kind="copy",
                rel_path=scope / rel_within,
                role=role_for(rel_within),
                reason=f"copy {rel_within}",
                source=staged,
            )
        )
    ops.extend(asset_ops)
    return WritePlan(
        target_root=content_root,
        target_scope=scope,
        operations=tuple(ops),
        diagnostics=diagnostics,
        replace=replace,
        source_document=source_document,
    )


# ---------------------------------------------------------------------------
# project sub-page scaffold (`pancratius project page add`)
# ---------------------------------------------------------------------------


class ScaffoldError(Exception):
    """Invalid input to scaffold_subpage (missing/non-DOCX source)."""


def _subpage_role(rel: PurePosixPath) -> Role:
    """A sub-page bundle's role map: the lifted bibliography is a sidecar, the
    `<lang>.md` (and anything else staged) is converter-owned canonical source."""
    return "sidecar" if rel.name == "bibliography.yaml" else "canonical_source"


def scaffold_subpage(
    *,
    project: str,
    subpage_slug: str,
    docx: Path,
    lang: Locale,
    out_content: Path,
    dry_run: bool = False,
) -> WriteReport:
    """Scaffold a project sub-page draft from one DOCX — the deterministic slice only
    (docs/tooling.md "project page add scaffolds only").

    Converts the DOCX prose, co-locates its body images, and writes
    `projects/<project>/subpages/<subpage-slug>/<lang>.md` with mechanical frontmatter
    seeded and editorial fields left as `TODO` placeholders, so the draft fails
    `npm run check` until a human fills them (a failing draft beats a guessed register).

    Never reads or writes the project landing. Writes through the import writer (the
    writer is general and emits no provenance, so there is no import coupling). Raises
    `ScaffoldError` for bad input; a write refusal rides home in the returned report."""
    docx = Path(docx).expanduser().resolve()
    if not docx.is_file():
        raise ScaffoldError(f"DOCX not found: {docx}")
    if docx.suffix.lower() != ".docx":
        raise ScaffoldError(f"expected a .docx file: {docx}")

    # Do not create content_root: the writer is the only mutator and dry-run must
    # touch nothing.
    content_root = Path(out_content).expanduser().resolve()
    scope = PurePosixPath("projects") / project / "subpages" / subpage_slug

    # Stage into a disposable scratch root; the pandoc media dir is a sibling that must
    # live until the writer copies the body images, cleaned in `finally`.
    stage_root = CACHE_ROOT / "subpage-stage" / uuid.uuid4().hex
    stage_dir = stage_root / scope.name
    media_out = stage_root / "media"
    stage_dir.mkdir(parents=True, exist_ok=True)
    try:
        # Thread a non-work kind so the prose path runs (only `kind == "poem"` is
        # special-cased).
        converted = convert_single_docx(
            docx,
            kind="project",
            lang=lang,
            work_key=subpage_slug,
            title=subpage_slug,
            title_index={},
            media_out=media_out,
        )

        # Mechanical fields seeded; editorial fields are TODO placeholders. The
        # `weight` value is enum-invalid, so the draft fails `npm run check` until a
        # human sets the register.
        fm: dict[str, Any] = {
            "kind": "project_subpage",
            "parent": project,
            "slug": subpage_slug,
            "lang": lang,
            "title": "TODO: write the sub-page title",
            "description": "TODO: write the sub-page description",
            "weight": "TODO: set the register — one of essay|revelation|verse|practice|dialogue",
        }
        if converted.cross_refs:
            fm["cross_refs"] = converted.cross_refs

        (stage_dir / f"{lang}.md").write_text(
            dump_frontmatter(fm) + converted.body, encoding="utf-8"
        )
        write_bibliography_sidecar(stage_dir, lang, converted.bibliography)

        # Footnote-fatal / typed-diagnostic safety: an orphaned footnote reference or a
        # converter-side FATAL refuses the write.
        diagnostics: tuple[Diagnostic, ...] = tuple(
            Diagnostic(d.severity, d.code, d.message)
            for d in footnotes.analyze_footnotes(converted.body)
        ) + tuple(
            Diagnostic(d.severity, d.code, d.message) for d in converted.diagnostics
        )

        plan = plan_from_staged_bundle(
            stage_dir=stage_dir,
            content_root=content_root,
            scope=scope,
            role_for=_subpage_role,
            asset_ops=body_asset_ops(converted.assets, scope),
            diagnostics=diagnostics,
            source_document=docx,
            replace=False,
        )
        return apply_plan(plan, dry_run=dry_run)
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)
