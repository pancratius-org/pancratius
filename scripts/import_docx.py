#!/usr/bin/env -S uv run --quiet
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "pillow>=10.4",
#   "pyyaml>=6.0",
# ]
# ///
from __future__ import annotations

import argparse
import re
import shutil
import sys
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
import xml.etree.ElementTree as ET

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from lib.content_catalog import (
    CatalogEntry,
    KIND_DIRS,
    build_title_index,
    dump_frontmatter,
    find_work_entries,
    next_number,
    scan_catalog,
)
from lib.docx_conversion import (
    ConvertedDocx,
    convert_single_docx,
    to_ascii_slug,
    write_bibliography_sidecar,
)
from lib import footnotes
from lib.kinds import WORK_KINDS
from lib.locales import LOCALES
from lib.writeplan import AssetTransform, Diagnostic, PlannedAsset, Role, WriteOp, WritePlan
from lib.writer import WriteReport, apply as apply_plan


ROOT = SCRIPT_DIR.parent
DEFAULT_CONTENT_ROOT = ROOT / "src" / "content"
# Scratch staging root for conversion (.cache/ is disposable; never src/content).
STAGE_ROOT = ROOT / ".cache" / "import-stage"
TODO_DESCRIPTION = "TODO: write the editorial description for this work."

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".tiff"}
LANGS = tuple(LOCALES)
# import_docx imports corpus WORKS only (lib.kinds.WORK_KINDS). Projects are
# authored themed sections under src/content/projects/ (not converter output), so
# `project` is not an importable kind and the catalog scan below ignores project
# entries — a `--into <project>` simply finds no work, like any other unknown
# bundle.

# Forward cap for NEWLY-IMPORTED body images. Raster masters extracted from a
# DOCX are bounded to this longest edge at import time so future masters stay
# reasonable, mirroring the bounded `/assets/` rendition the site serves. This
# only applies to images written by this import run; it never re-encodes the
# existing committed corpus. Vector (svg) and animated (gif) formats are left
# untouched. The committed body-image filenames are content hashes, but the
# converted Markdown references those exact names, so the cap keeps the filename
# rather than re-hashing — it is a writer `transform_asset` (cap_raster) op now,
# not a post-conversion in-place pass.
IMPORT_MAX_LONGEST_EDGE = 1600

_LEADING_NUMBER_RE = re.compile(r"^\s*\d+\s*[-._ ]+\s*")
_LATIN_RE = re.compile(r"[A-Za-z]")
_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")


@dataclass(frozen=True)
class ImportResult:
    kind: str
    work_key: str
    md_path: Path
    docx_path: Path
    warnings: str = ""


def _scratch_role(rel: PurePosixPath, lang: str) -> Role:
    """Map a staged bundle file to its WritePlan role (the writer/audit boundary
    cares only about ownership classes, not file specifics)."""
    name = rel.name
    if name == f"{lang}.md":
        return "canonical_source"
    if name == f"{lang}.docx":
        return "source_artifact"
    if name.startswith("cover."):
        return "cover"
    if name == "bibliography.yaml":
        return "sidecar"
    # Any remaining staged file (images/** and any other body asset) is an
    # imported asset — the only other class the converter produces.
    return "imported_asset"


def _asset_ops(assets: list[PlannedAsset], scope: PurePosixPath) -> list[WriteOp]:
    """Turn the converter's planned body images into `transform_asset` WriteOps.

    Rasters get a `cap_raster` transform (longest-edge cap, applied by the writer
    — the only place PIL runs); vector/animated assets get a plain `copy`. The
    bundle-relative path is `images/<hash>.<ext>` exactly as the Markdown body
    references it, so filenames are unchanged."""
    ops: list[WriteOp] = []
    for asset in assets:
        transform = (
            AssetTransform(kind="cap_raster", max_long_edge=IMPORT_MAX_LONGEST_EDGE)
            if asset.is_raster
            else AssetTransform(kind="copy")
        )
        ops.append(
            WriteOp(
                kind="transform_asset",
                rel_path=scope / PurePosixPath(asset.rel_within),
                role="imported_asset",
                reason=f"import {asset.rel_within}",
                source=asset.source,
                transform=transform,
            )
        )
    return ops


def _plan_from_scratch(
    *,
    stage_work_dir: Path,
    content_root: Path,
    scope: PurePosixPath,
    lang: str,
    replace: bool,
    diagnostics: tuple[Diagnostic, ...],
    source_document: Path,
    asset_ops: list[WriteOp],
) -> WritePlan:
    """Build a WritePlan that copies every staged bundle file into the real target
    scope, alongside the planned body-image `transform_asset` ops (which are NOT
    staged into the scratch bundle — the writer copies/caps them from the
    persistent pandoc media dir). The plan is the ONLY thing import_docx hands the
    writer; import_docx itself never writes to content_root."""
    ops: list[WriteOp] = [
        WriteOp(
            kind="ensure_dir",
            rel_path=scope,
            role="canonical_source",
            reason="bundle directory",
        )
    ]
    for staged in sorted(stage_work_dir.rglob("*")):
        if not staged.is_file():
            continue
        rel_within = PurePosixPath(staged.relative_to(stage_work_dir).as_posix())
        rel_path = scope / rel_within
        ops.append(
            WriteOp(
                kind="copy",
                rel_path=rel_path,
                role=_scratch_role(rel_within, lang),
                reason=f"import {rel_within}",
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


def _print_report(report: WriteReport, *, dry_run: bool) -> None:
    """Human/agent-facing dry-run + write summary (the review gate)."""
    label = "DRY RUN — planned write-set (nothing written):" if dry_run else "write summary:"
    print(label)
    for bucket, paths in (
        ("create", report.created),
        ("change", report.changed),
        ("skip", report.skipped),
        ("REFUSE", report.refused),
    ):
        for rel in paths:
            print(f"  {bucket}: {rel}")
    for diag in report.diagnostics:
        print(f"  [{diag.severity}] {diag.code}: {diag.message}", file=sys.stderr)


def _docx_core_title(docx: Path) -> str | None:
    try:
        with zipfile.ZipFile(docx) as zf:
            data = zf.read("docProps/core.xml")
    except (KeyError, OSError, zipfile.BadZipFile):
        return None
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None
    ns = {"dc": "http://purl.org/dc/elements/1.1/"}
    title = root.findtext("dc:title", namespaces=ns)
    title = (title or "").strip()
    return title or None


def infer_title(docx: Path) -> str:
    title = _docx_core_title(docx)
    if title:
        return title
    stem = _LEADING_NUMBER_RE.sub("", docx.stem)
    stem = re.sub(r"[-_]+", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip()
    if not stem:
        return "Untitled work"
    return stem[:1].upper() + stem[1:]


def _majority_latin(value: str) -> bool:
    latin = len(_LATIN_RE.findall(value))
    cyrillic = len(_CYRILLIC_RE.findall(value))
    if latin + cyrillic == 0:
        return True
    return latin >= cyrillic


def _slug_with_number(raw_slug: str, number: int) -> str:
    slug = to_ascii_slug(raw_slug)
    if not slug:
        slug = f"work-{number}"
    if re.match(r"^\d{1,4}-", slug):
        return slug
    return f"{number:02d}-{slug}"


def _existing_group(matches: list[CatalogEntry], work_ref: str) -> tuple[str, str, list[CatalogEntry]]:
    groups: dict[tuple[str, str], list[CatalogEntry]] = {}
    for entry in matches:
        groups.setdefault((entry.kind, entry.work_key), []).append(entry)
    if not groups:
        raise SystemExit(f"work not found in Markdown catalog: {work_ref}")
    if len(groups) > 1:
        choices = ", ".join(f"{kind}/{work_key}" for kind, work_key in sorted(groups))
        raise SystemExit(f"--into is ambiguous ({choices}); pass --kind")
    (kind, work_key), entries = next(iter(groups.items()))
    return kind, work_key, entries


def _preferred_entry(entries: list[CatalogEntry], lang: str) -> CatalogEntry:
    same_lang = [entry for entry in entries if entry.lang == lang]
    if same_lang:
        return same_lang[0]
    ru = [entry for entry in entries if entry.lang == "ru"]
    if ru:
        return ru[0]
    return entries[0]


def _existing_lang_entry(entries: list[CatalogEntry], lang: str) -> CatalogEntry | None:
    return next((entry for entry in entries if entry.lang == lang), None)


def _copy_if_needed(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and src.resolve() == dst.resolve():
        return
    shutil.copyfile(src, dst)


def _write_docx_artifact(src: Path, dst: Path) -> None:
    """Write the bundle DOCX artifact.

    Imported DOCX files do not go through `legacy/`. The author supplies a
    source file; the bundle receives the cleaned/optimized downloadable artifact
    at `<lang>.docx`.
    """
    from docx_optimize import optimize_docx

    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        optimize_docx(src, dst)
    except Exception as exc:  # pragma: no cover - preserves CLI error clarity
        raise SystemExit(f"failed to optimize DOCX {src} -> {dst}: {exc}") from exc


def _frontmatter_cover_exists(work_dir: Path, cover: object) -> bool:
    if not isinstance(cover, str) or not cover.startswith("./"):
        return False
    return (work_dir / cover[2:]).is_file()


def _find_cover(work_dir: Path, lang: str) -> tuple[str | None, bool]:
    same_lang = sorted(work_dir.glob(f"cover.{lang}.*"))
    for path in same_lang:
        if path.suffix.lower() in IMAGE_EXTS:
            return f"./{path.name}", False
    ru_cover = sorted(work_dir.glob("cover.ru.*"))
    for path in ru_cover:
        if path.suffix.lower() in IMAGE_EXTS:
            return f"./{path.name}", lang != "ru"
    return None, False


def _prepare_cover(
    *,
    cover_arg: str | None,
    read_dir: Path,
    write_dir: Path,
    lang: str,
    existing_lang: CatalogEntry | None,
    reference: CatalogEntry | None,
) -> tuple[str | None, bool]:
    """Resolve the bundle cover. Existing committed covers are READ from
    ``read_dir`` (the real bundle, for the additive --into case); a new --cover is
    WRITTEN into ``write_dir`` (the scratch stage), so the writer remains the only
    thing that touches the real target."""
    if cover_arg:
        src = Path(cover_arg).expanduser().resolve()
        if not src.is_file():
            raise SystemExit(f"cover not found: {src}")
        if src.suffix.lower() not in IMAGE_EXTS:
            raise SystemExit(f"unsupported cover image extension: {src.suffix}")
        ext = ".jpg" if src.suffix.lower() in {".jpeg", ".jpe"} else src.suffix.lower()
        dst = write_dir / f"cover.{lang}{ext}"
        _copy_if_needed(src, dst)
        return f"./{dst.name}", False

    if existing_lang and _frontmatter_cover_exists(read_dir, existing_lang.frontmatter.get("cover")):
        return str(existing_lang.frontmatter["cover"]), bool(existing_lang.frontmatter.get("cover_is_placeholder"))

    found, placeholder = _find_cover(read_dir, lang)
    if found:
        return found, placeholder

    if reference and _frontmatter_cover_exists(read_dir, reference.frontmatter.get("cover")):
        cover = str(reference.frontmatter["cover"])
        return cover, lang != reference.lang

    return None, False


def _translation_source(existing_lang: CatalogEntry | None, lang: str, override: str | None) -> str:
    if override:
        return override
    if existing_lang:
        raw = existing_lang.frontmatter.get("translation")
        if isinstance(raw, dict) and raw.get("source"):
            return str(raw["source"])
    return "original" if lang == "ru" else "ai"


def _merge_cross_refs(existing_lang: CatalogEntry | None, converted: ConvertedDocx) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    if existing_lang:
        raw = existing_lang.frontmatter.get("cross_refs")
        if isinstance(raw, list):
            refs.extend(ref for ref in raw if isinstance(ref, dict) and ref.get("source") == "editorial")
    refs.extend(converted.cross_refs)

    seen: set[tuple[str, int]] = set()
    deduped: list[dict[str, Any]] = []
    for ref in refs:
        target = ref.get("target")
        if not isinstance(target, dict):
            deduped.append(ref)
            continue
        try:
            key = (str(target["kind"]), int(target["number"]))
        except (KeyError, TypeError, ValueError):
            deduped.append(ref)
            continue
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ref)
    return deduped


def _frontmatter_for_import(
    *,
    args: argparse.Namespace,
    kind: str,
    number: int,
    slug: str,
    title: str,
    description: str,
    lang: str,
    cover: str | None,
    cover_is_placeholder: bool,
    existing_lang: CatalogEntry | None,
    reference: CatalogEntry | None,
    converted: ConvertedDocx,
) -> dict[str, Any]:
    fm: dict[str, Any] = dict(existing_lang.frontmatter) if existing_lang else {}
    fm.update({
        "kind": kind,
        "number": number,
        "slug": slug,
        "title": title,
        "lang": lang,
        "description": description,
    })

    if kind == "book":
        tags = fm.get("tags")
        if not isinstance(tags, list):
            tags = reference.frontmatter.get("tags") if reference else []
        fm["tags"] = tags if isinstance(tags, list) else []
    elif kind == "poem":
        if "date" not in fm:
            fm["date"] = reference.frontmatter.get("date") if reference else None

    fm["cover"] = cover
    if cover_is_placeholder:
        fm["cover_is_placeholder"] = True
    else:
        fm.pop("cover_is_placeholder", None)

    cross_refs = _merge_cross_refs(existing_lang, converted)
    if cross_refs:
        fm["cross_refs"] = cross_refs
    else:
        fm.pop("cross_refs", None)

    fm["translation"] = {"source": _translation_source(existing_lang, lang, args.translation_source)}
    return fm


def _resolve_target(
    args: argparse.Namespace,
    entries: list[CatalogEntry],
    docx: Path,
    content_root: Path,
) -> tuple[str, str, int, str, list[CatalogEntry]]:
    if args.into:
        matches = find_work_entries(entries, args.into, args.kind)
        kind, work_key, work_entries = _existing_group(matches, args.into)
        reference = _preferred_entry(work_entries, args.lang)
        number = args.number or reference.number
        slug = _slug_with_number(args.slug, number) if args.slug else reference.slug
        return kind, work_key, number, slug, work_entries

    if not args.kind:
        raise SystemExit("--kind is required when importing a new work")
    kind = args.kind
    number = args.number or next_number(entries, kind)
    title = args.title or infer_title(docx)
    work_key = _slug_with_number(args.slug or title, number)
    slug = work_key
    work_dir = content_root / KIND_DIRS[kind] / work_key
    if work_dir.exists():
        raise SystemExit(f"work bundle already exists: {work_dir}; use --into {work_key} to update it")
    return kind, work_key, number, slug, []


def _run(args: argparse.Namespace) -> tuple[ImportResult, WriteReport]:
    docx = Path(args.docx).expanduser().resolve()
    if not docx.is_file():
        raise SystemExit(f"DOCX not found: {docx}")
    if docx.suffix.lower() != ".docx":
        raise SystemExit(f"expected a .docx file: {docx}")

    content_root = Path(args.out_content).expanduser().resolve()
    # Do NOT create content_root here: the writer is the only component that
    # mutates the content tree, and --dry-run must touch nothing. `scan_catalog`
    # tolerates a missing root, and the writer's `ensure_dir` creates the bundle
    # (and its parents) when the plan is actually applied.
    entries = [e for e in scan_catalog(content_root) if e.kind in WORK_KINDS]

    kind, work_key, number, slug, work_entries = _resolve_target(args, entries, docx, content_root)
    real_work_dir = content_root / KIND_DIRS[kind] / work_key
    scope = PurePosixPath(KIND_DIRS[kind]) / work_key

    existing_lang = _existing_lang_entry(work_entries, args.lang)
    reference = _preferred_entry(work_entries, args.lang) if work_entries else None
    inferred_title = infer_title(docx)
    if args.title:
        title = args.title
    elif existing_lang:
        title = existing_lang.title
    elif args.into and reference and (args.lang == reference.lang or not _majority_latin(inferred_title)):
        title = reference.title
    else:
        title = inferred_title

    if args.description:
        description = args.description
    elif existing_lang and existing_lang.description:
        description = existing_lang.description
    else:
        description = TODO_DESCRIPTION

    # Stage the whole conversion into a disposable scratch root under .cache/.
    # NOTHING here touches the real target — the writer is the only mutator of
    # src/content. The pandoc media dir is a sibling of the staged bundle under the
    # same scratch root: it holds the extracted body images the planned
    # `transform_asset` ops copy/cap, so it must live until the writer runs and is
    # cleaned in the `finally` with the rest of the stage. Body images are NOT
    # staged into the bundle — they reach src/content only via the writer.
    stage_root = STAGE_ROOT / uuid.uuid4().hex
    stage_dir = stage_root / KIND_DIRS[kind] / work_key
    media_out = stage_root / "media"
    stage_dir.mkdir(parents=True, exist_ok=True)
    try:
        title_index = build_title_index(entries)
        converted = convert_single_docx(
            docx,
            kind=kind,
            lang=args.lang,
            work_key=work_key,
            title=title,
            work_dir=stage_dir,
            title_index=title_index,
            media_out=media_out,
        )

        cover, cover_is_placeholder = _prepare_cover(
            cover_arg=args.cover,
            read_dir=real_work_dir,
            write_dir=stage_dir,
            lang=args.lang,
            existing_lang=existing_lang,
            reference=reference,
        )
        fm = _frontmatter_for_import(
            args=args,
            kind=kind,
            number=number,
            slug=slug,
            title=title,
            description=description,
            lang=args.lang,
            cover=cover,
            cover_is_placeholder=cover_is_placeholder,
            existing_lang=existing_lang,
            reference=reference,
            converted=converted,
        )

        (stage_dir / f"{args.lang}.md").write_text(
            dump_frontmatter(fm) + converted.body, encoding="utf-8"
        )
        _write_docx_artifact(docx, stage_dir / f"{args.lang}.docx")
        write_bibliography_sidecar(stage_dir, kind, args.lang, converted.bibliography)

        # Footnote integrity is a first-class plan diagnostic, not bespoke writer
        # logic: an orphaned `[^id]` reference (no matching `[^id]:` definition)
        # is FATAL and rides into `WritePlan.diagnostics`, where the writer's
        # has_fatal machinery refuses the write. After the Phase-4 fix valid books
        # resolve and this never fires; it is the safety net that makes the
        # orphaned-marker bug class impossible to ship again. Unused/duplicate
        # definitions surface as non-blocking warnings.
        diagnostics: tuple[Diagnostic, ...] = tuple(
            Diagnostic(d.severity, d.code, d.message)
            for d in footnotes.analyze_footnotes(converted.body)
        )
        if converted.warnings:
            diagnostics += (Diagnostic("warning", "import.pandoc", converted.warnings),)
        plan = _plan_from_scratch(
            stage_work_dir=stage_dir,
            content_root=content_root,
            scope=scope,
            lang=args.lang,
            replace=bool(args.replace),
            diagnostics=diagnostics,
            source_document=docx,
            asset_ops=_asset_ops(converted.assets, scope),
        )
        report = apply_plan(plan, dry_run=bool(args.dry_run))
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)

    _print_report(report, dry_run=bool(args.dry_run))
    if report.refused:
        raise SystemExit(
            f"refused to write {kind}/{work_key} ({args.lang}): "
            + "; ".join(d.message for d in report.diagnostics if d.severity == "fatal")
        )

    md_path = real_work_dir / f"{args.lang}.md"
    docx_out = real_work_dir / f"{args.lang}.docx"
    if not args.dry_run:
        print(f"imported {kind}/{work_key} ({args.lang})")
        print(f"markdown: {md_path}")
        print(f"docx: {docx_out}")
    if converted.warnings:
        print(f"pandoc warnings:\n{converted.warnings}", file=sys.stderr)
    result = ImportResult(
        kind=kind, work_key=work_key, md_path=md_path, docx_path=docx_out, warnings=converted.warnings
    )
    return result, report


def run(args: argparse.Namespace) -> ImportResult:
    """Build the WritePlan, apply it through the writer, and return the legacy
    `ImportResult`. The single mutating surface for src/content is the writer."""
    return _run(args)[0]


def import_work(args: argparse.Namespace) -> WriteReport:
    """Stable importer entry the future `pancratius work import` CLI dispatches to.

    Returns the writer's `WriteReport` (the planned/applied write-set + diagnostics)
    rather than the legacy `ImportResult`. Shares the same plan→writer tail as
    `run`; this is the contract surface that returns the report directly.
    """
    return _run(args)[1]


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Import one DOCX into a Pancratius work bundle using Markdown frontmatter as the catalog.",
    )
    ap.add_argument("docx", help="Source .docx file to import.")
    ap.add_argument("--kind", choices=WORK_KINDS, help="Required for a new work; optional with --into when the bundle is unique.")
    ap.add_argument("--lang", choices=LANGS, required=True)
    ap.add_argument("--into", help="Existing work bundle key or frontmatter slug to update.")
    ap.add_argument("--out-content", default=str(DEFAULT_CONTENT_ROOT), help="Content root; defaults to src/content.")
    ap.add_argument("--title", help="Override frontmatter title.")
    ap.add_argument("--number", type=int, help="Override work number; defaults to next number for new works or existing number with --into.")
    ap.add_argument("--slug", help="Override frontmatter/work slug. Without a numeric prefix, the work number is prepended.")
    ap.add_argument("--description", help="Override frontmatter description.")
    ap.add_argument("--cover", help="Optional cover image to copy as cover.<lang>.<ext>.")
    ap.add_argument("--translation-source", choices=["original", "literary", "ai"], help="Override translation.source.")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the full planned write-set + diagnostics and write NOTHING (the review gate).",
    )
    ap.add_argument(
        "--replace",
        action="store_true",
        help="Permit overwriting an existing converter-owned <lang>.md; without it, re-importing an existing language is refused.",
    )
    return ap


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if shutil.which("pandoc") is None:
        parser.error("pandoc not found on PATH; install with `brew install pandoc`")
    run(args)


if __name__ == "__main__":
    main(sys.argv[1:])
