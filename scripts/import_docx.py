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
import zipfile
from dataclasses import dataclass
from pathlib import Path
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
from lib.locales import LOCALES


ROOT = SCRIPT_DIR.parent
DEFAULT_CONTENT_ROOT = ROOT / "src" / "content"
TODO_DESCRIPTION = "TODO: write the editorial description for this work."

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".tiff"}
LANGS = tuple(LOCALES)

# Forward cap for NEWLY-IMPORTED body images. Raster masters extracted from a
# DOCX are bounded to this longest edge at import time so future masters stay
# reasonable, mirroring the bounded `/assets/` rendition the site serves. This
# only applies to images written by this import run; it never re-encodes the
# existing committed corpus. Vector (svg) and animated (gif) formats are left
# untouched. The committed body-image filenames are content hashes, but the
# converted Markdown references those exact names, so we cap in place and keep
# the filename rather than re-hashing.
IMPORT_MAX_LONGEST_EDGE = 1600
RASTER_CAP_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".avif"}


def _cap_imported_body_images(images_dir: Path, skip: set[Path]) -> None:
    """Resize newly-imported raster body images down to the longest-edge cap.

    `skip` holds image paths that already existed before this import so the
    pre-existing corpus is never touched. Only down-scaling happens; small
    images are left as-is. Any per-file failure is non-fatal — the original
    bytes simply remain.
    """
    if not images_dir.is_dir():
        return
    from PIL import Image

    for path in sorted(images_dir.rglob("*")):
        if not path.is_file() or path in skip:
            continue
        if path.suffix.lower() not in RASTER_CAP_EXTS:
            continue
        try:
            with Image.open(path) as img:
                img.load()
                width, height = img.size
                if max(width, height) <= IMPORT_MAX_LONGEST_EDGE:
                    continue
                fmt = img.format
                resized = img.copy()
            resized.thumbnail(
                (IMPORT_MAX_LONGEST_EDGE, IMPORT_MAX_LONGEST_EDGE),
                Image.LANCZOS,
            )
            save_kwargs: dict[str, Any] = {}
            if fmt in {"JPEG", "WEBP"}:
                save_kwargs["quality"] = 82 if fmt == "JPEG" else 80
            resized.save(path, format=fmt, **save_kwargs)
            print(f"capped {path.name}: {width}x{height} -> {resized.size[0]}x{resized.size[1]}")
        except Exception as exc:  # pragma: no cover - one bad image must not fail import
            print(f"warning: could not cap {path}: {exc}", file=sys.stderr)

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


def _frontmatter_cover_exists(work_dir: Path, cover: Any) -> bool:
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
    work_dir: Path,
    lang: str,
    existing_lang: CatalogEntry | None,
    reference: CatalogEntry | None,
) -> tuple[str | None, bool]:
    if cover_arg:
        src = Path(cover_arg).expanduser().resolve()
        if not src.is_file():
            raise SystemExit(f"cover not found: {src}")
        if src.suffix.lower() not in IMAGE_EXTS:
            raise SystemExit(f"unsupported cover image extension: {src.suffix}")
        ext = ".jpg" if src.suffix.lower() in {".jpeg", ".jpe"} else src.suffix.lower()
        dst = work_dir / f"cover.{lang}{ext}"
        _copy_if_needed(src, dst)
        return f"./{dst.name}", False

    if existing_lang and _frontmatter_cover_exists(work_dir, existing_lang.frontmatter.get("cover")):
        return str(existing_lang.frontmatter["cover"]), bool(existing_lang.frontmatter.get("cover_is_placeholder"))

    found, placeholder = _find_cover(work_dir, lang)
    if found:
        return found, placeholder

    if reference and _frontmatter_cover_exists(work_dir, reference.frontmatter.get("cover")):
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

    if kind == "project" and "tagline" not in fm and reference:
        tagline = reference.frontmatter.get("tagline")
        if tagline:
            fm["tagline"] = tagline

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


def run(args: argparse.Namespace) -> ImportResult:
    docx = Path(args.docx).expanduser().resolve()
    if not docx.is_file():
        raise SystemExit(f"DOCX not found: {docx}")
    if docx.suffix.lower() != ".docx":
        raise SystemExit(f"expected a .docx file: {docx}")

    content_root = Path(args.out_content).expanduser().resolve()
    content_root.mkdir(parents=True, exist_ok=True)
    entries = scan_catalog(content_root)

    kind, work_key, number, slug, work_entries = _resolve_target(args, entries, docx, content_root)
    work_dir = content_root / KIND_DIRS[kind] / work_key
    work_dir.mkdir(parents=True, exist_ok=True)

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

    # Snapshot pre-existing body images so the forward cap only touches images
    # written by this import run, never the already-committed corpus.
    images_dir = work_dir / "images"
    pre_existing_images = (
        {p for p in images_dir.rglob("*") if p.is_file()} if images_dir.is_dir() else set()
    )

    title_index = build_title_index(entries)
    converted = convert_single_docx(
        docx,
        kind=kind,
        lang=args.lang,
        work_key=work_key,
        title=title,
        work_dir=work_dir,
        title_index=title_index,
    )

    _cap_imported_body_images(images_dir, skip=pre_existing_images)

    cover, cover_is_placeholder = _prepare_cover(
        cover_arg=args.cover,
        work_dir=work_dir,
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

    md_path = work_dir / f"{args.lang}.md"
    md_path.write_text(dump_frontmatter(fm) + converted.body, encoding="utf-8")

    docx_out = work_dir / f"{args.lang}.docx"
    _write_docx_artifact(docx, docx_out)

    write_bibliography_sidecar(work_dir, kind, args.lang, converted.bibliography)

    print(f"imported {kind}/{work_key} ({args.lang})")
    print(f"markdown: {md_path}")
    print(f"docx: {docx_out}")
    if converted.warnings:
        print(f"pandoc warnings:\n{converted.warnings}", file=sys.stderr)
    return ImportResult(kind=kind, work_key=work_key, md_path=md_path, docx_path=docx_out, warnings=converted.warnings)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Import one DOCX into a Pancratius work bundle using Markdown frontmatter as the catalog.",
    )
    ap.add_argument("docx", help="Source .docx file to import.")
    ap.add_argument("--kind", choices=sorted(KIND_DIRS), help="Required for a new work; optional with --into when the bundle is unique.")
    ap.add_argument("--lang", choices=LANGS, required=True)
    ap.add_argument("--into", help="Existing work bundle key or frontmatter slug to update.")
    ap.add_argument("--out-content", default=str(DEFAULT_CONTENT_ROOT), help="Content root; defaults to src/content.")
    ap.add_argument("--title", help="Override frontmatter title.")
    ap.add_argument("--number", type=int, help="Override work number; defaults to next number for new works or existing number with --into.")
    ap.add_argument("--slug", help="Override frontmatter/work slug. Without a numeric prefix, the work number is prepended.")
    ap.add_argument("--description", help="Override frontmatter description.")
    ap.add_argument("--cover", help="Optional cover image to copy as cover.<lang>.<ext>.")
    ap.add_argument("--translation-source", choices=["original", "literary", "ai"], help="Override translation.source.")
    return ap


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if shutil.which("pandoc") is None:
        parser.error("pandoc not found on PATH; install with `brew install pandoc`")
    run(args)


if __name__ == "__main__":
    main(sys.argv[1:])
