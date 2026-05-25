from __future__ import annotations

import re
import shutil
import unicodedata
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
import sys
from typing import Any

import yaml

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from lib import cross_refs, docx_adapter, footnotes, ir, ir_lower, ir_normalize, ooxml
from lib.content_catalog import dump_frontmatter
from lib.writeplan import AssetTransform, Diagnostic, PlannedAsset, Role, WriteOp, WritePlan
from lib.writer import WriteReport, apply as apply_plan

# Repo root: scripts/lib/ -> scripts/ -> repo root. Used to anchor the disposable
# conversion scratch dir under `.cache/` (never src/content).
_REPO_ROOT = SCRIPTS_DIR.parent

# The longest-edge cap (px) for NEWLY-CONVERTED body images, applied by the writer
# as a `cap_raster` transform at copy time. ONE owner for both the work importer and
# the project sub-page scaffold (docs/content-model.md asset policy): a raster master
# extracted from a DOCX is bounded to this edge; vector/animated formats are copied
# untouched. The committed filenames are content hashes the Markdown already
# references, so the cap keeps the name rather than re-hashing.
BODY_IMAGE_MAX_LONG_EDGE = 1600

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
    # TYPED diagnostics carried straight from the IR document (severity preserved),
    # so a converter-side FATAL (e.g. an unresolvable local image) can ride into the
    # WritePlan and block the write. Flattening these into `warnings` (a string) lost
    # severity, letting a fatal slip through as a non-blocking warning. `warnings` is
    # still produced for the human summary.
    diagnostics: list[ir.Diagnostic] = field(default_factory=list)


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

    # Neutralize unsafe link/image URL schemes BEFORE the asset pass reads/hashes
    # any image, so an unsafe-scheme image src never reaches asset resolution (and
    # the kept link text survives). `lower` runs it again idempotently.
    ir_lower.sanitize_urls(doc)
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
        cross_refs=refs,
        warnings=warnings,
        assets=assets,
        # Carry EVERY typed diagnostic (severity preserved) so the importer can let a
        # converter-side FATAL block the write — not just the flattened warning string.
        diagnostics=list(doc.diagnostics),
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


# ---------------------------------------------------------------------------
# shared plan assembly (one owner for the work importer + the sub-page scaffold)
# ---------------------------------------------------------------------------


def body_asset_ops(assets: list[PlannedAsset], scope: PurePosixPath) -> list[WriteOp]:
    """Turn the converter's planned body images into `transform_asset` WriteOps,
    scope-relative: a raster gets a `cap_raster` cap (`BODY_IMAGE_MAX_LONG_EDGE`,
    applied by the writer — the only place PIL runs); vector/animated assets get a
    plain `copy`. The bundle-relative path is `images/<hash>.<ext>` exactly as the
    Markdown body references it, so filenames are unchanged. Shared by the work
    importer and the sub-page scaffold so the cap policy has ONE owner."""
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
    """Build a WritePlan that copies every staged bundle file into the target scope,
    alongside the body-image `transform_asset` ops (which are NOT staged — the writer
    copies/caps them from the persistent media dir). `role_for` maps each staged
    bundle-relative path to its ownership role (the caller owns that policy; the
    importer keys on filename via _scratch_role, the scaffold on a simpler split).
    The plan is the ONLY thing handed to the writer; no caller mutates content_root
    directly. Shared by import_docx._plan_from_scratch and scaffold_subpage."""
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
    lang: str,
    out_content: Path,
    dry_run: bool = False,
) -> WriteReport:
    """Scaffold a project sub-page draft from one DOCX — the deterministic slice
    only (docs/tooling.md "project page add scaffolds only").

    Converts the DOCX prose to a draft body, co-locates its body images, and
    writes `projects/<project>/subpages/<subpage-slug>/<lang>.md` with the
    MECHANICAL frontmatter (`kind`, `parent`, `slug`, `lang`) seeded and the
    EDITORIAL fields (`title`, `description`, `weight`) left as explicit `TODO`
    placeholders — so the draft fails `npm run check` until a human fills them
    (the safe outcome: a failing draft beats a guessed register that ships wrong).

    It NEVER reads or writes the project landing. It writes through the import
    writer (atomic, scoped, no-clobber, dry-run-safe) — the writer is general and
    emits no provenance, so this reuses it with no import coupling. Raises
    `ScaffoldError` for bad input (missing/non-DOCX source); a write refusal rides
    home in the returned report (it does not raise)."""
    docx = Path(docx).expanduser().resolve()
    if not docx.is_file():
        raise ScaffoldError(f"DOCX not found: {docx}")
    if docx.suffix.lower() != ".docx":
        raise ScaffoldError(f"expected a .docx file: {docx}")

    # Do NOT create content_root: the writer is the only mutator and dry-run must
    # touch nothing.
    content_root = Path(out_content).expanduser().resolve()
    scope = PurePosixPath("projects") / project / "subpages" / subpage_slug

    # Stage the whole conversion into a disposable scratch root under .cache/ (never
    # src/content), mirroring import_docx. The pandoc media dir is a sibling of the
    # staged bundle so the planned `transform_asset` ops can copy/cap the extracted
    # body images; it must live until the writer runs and is cleaned in `finally`.
    stage_root = _REPO_ROOT / ".cache" / "subpage-stage" / uuid.uuid4().hex
    stage_dir = stage_root / scope.name
    media_out = stage_root / "media"
    stage_dir.mkdir(parents=True, exist_ok=True)
    try:
        # Convert PROSE: thread a NON-work kind so the prose path runs (the converter
        # only special-cases `kind == "poem"`). `project` is that non-work kind.
        converted = convert_single_docx(
            docx,
            kind="project",
            lang=lang,
            work_key=subpage_slug,
            title=subpage_slug,
            work_dir=stage_dir,
            title_index={},
            media_out=media_out,
        )

        # Draft frontmatter: mechanical fields seeded, editorial fields left as
        # explicit TODO placeholders (src/content.config.ts `projectSubpage`). The
        # `weight` TODO is an enum-invalid value on purpose, so the draft FAILS
        # `npm run check` until a human sets the register.
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

        # Stage the <lang>.md and bibliography into the scratch bundle; the shared
        # plan builder copies them in via the writer (exactly like import_docx),
        # reusing the writer's role classification / SVG-sanitize boundary unchanged.
        (stage_dir / f"{lang}.md").write_text(
            dump_frontmatter(fm) + converted.body, encoding="utf-8"
        )
        write_bibliography_sidecar(stage_dir, "project", lang, converted.bibliography)

        # Same fatal-footnote / typed-diagnostic safety as import: an orphaned
        # footnote reference or a converter-side FATAL refuses the write.
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
