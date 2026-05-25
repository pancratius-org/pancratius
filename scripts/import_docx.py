#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import uuid
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, TypedDict
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
    body_asset_ops,
    convert_single_docx,
    plan_from_staged_bundle,
    to_ascii_slug,
    write_bibliography_sidecar,
)
from lib import footnotes
from lib.kinds import WORK_KINDS
from lib.locales import LOCALES
from lib.writeplan import Diagnostic, Role, WriteOp, WritePlan
from lib.writer import WriteReport, apply as apply_plan


ROOT = SCRIPT_DIR.parent
DEFAULT_CONTENT_ROOT = ROOT / "src" / "content"
# Disposable conversion scratch; never src/content.
STAGE_ROOT = ROOT / ".cache" / "import-stage"
TODO_DESCRIPTION = "TODO: write the editorial description for this work."

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".tiff"}
LANGS = tuple(LOCALES)
# Imports corpus WORKS only (lib.kinds.WORK_KINDS); the catalog scan ignores project
# entries, so `--into <project>` simply finds no work.

_LEADING_NUMBER_RE = re.compile(r"^\s*\d+\s*[-._ ]+\s*")
_LATIN_RE = re.compile(r"[A-Za-z]")
_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")


class ImportError(Exception):
    """Invalid import input or an unresolvable target.

    Raised for bad input (missing/non-DOCX file) and `_resolve_target` failures (no
    such work, ambiguous `--into`, `--kind`-less new work, existing bundle dir). A
    write refusal is NOT this: `import_work` returns the refused `WriteReport` (with
    its fatal diagnostics) instead. The CLI maps this to `parser.error`."""


@dataclass(frozen=True)
class ImportRequest:
    """The frozen input contract `import_work` consumes, decoupled from argparse so a
    caller builds a request by name. Field names mirror the CLI flags;
    `request_from_namespace` adapts a parsed namespace into one."""

    docx: Path
    lang: str
    out_content: Path
    kind: str | None = None
    into: str | None = None
    title: str | None = None
    number: int | None = None
    slug: str | None = None
    description: str | None = None
    cover: Path | None = None
    translation_source: str | None = None
    dry_run: bool = False
    replace: bool = False


@dataclass(frozen=True)
class ImportResult:
    kind: str
    work_key: str
    md_path: Path
    docx_path: Path
    warnings: str = ""


def _scratch_role(rel: PurePosixPath, lang: str) -> Role:
    """Map a staged bundle file to its WritePlan ownership role."""
    name = rel.name
    if name == f"{lang}.md":
        return "canonical_source"
    if name == f"{lang}.docx":
        return "source_artifact"
    if name.startswith("cover."):
        return "cover"
    if name == "bibliography.yaml":
        return "sidecar"
    return "imported_asset"  # images/** and any other body asset


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
    """Build the import WritePlan via shared `plan_from_staged_bundle`, binding `lang`
    into the role map. The plan is the only thing import_docx hands the writer."""
    return plan_from_staged_bundle(
        stage_dir=stage_work_dir,
        content_root=content_root,
        scope=scope,
        role_for=lambda rel: _scratch_role(rel, lang),
        asset_ops=asset_ops,
        diagnostics=diagnostics,
        source_document=source_document,
        replace=replace,
    )


def _imports_dir(content_root: Path) -> Path:
    """Where the out-of-bundle provenance manifest lands, derived from the content root
    (`<root>/src/content` → `<root>/data/imports`) so a temp `--out-content` sandboxes
    to its own tree, never the real repo. A path too shallow to have a grandparent is
    rejected as bad input rather than crashing with IndexError."""
    if len(content_root.parents) < 2:
        raise ImportError(
            f"--out-content {content_root} is too shallow to locate data/imports; "
            "pass a path shaped like '<root>/src/content'."
        )
    return content_root.parents[1] / "data" / "imports"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class _ManifestOp(TypedDict):
    kind: str
    rel_path: str
    role: str
    reason: str


class _Manifest(TypedDict):
    generated_at: str
    target_scope: str
    replace: bool
    source_document: str | None
    source_sha256: str | None
    operations: list[_ManifestOp]


def _write_manifest(plan: WritePlan, *, imports_dir: Path) -> Path:
    """Write the volatile out-of-bundle provenance manifest under data/imports/.

    It never feeds the committed bundle, so re-import stays byte-identical. The
    filename is the full scope so kinds sharing a number (books/01-x vs poetry/01-x)
    cannot collide. The importer writes it AFTER the writer applies — the writer is
    general and emits no manifest."""
    source = plan.source_document
    manifest: _Manifest = {
        "generated_at": datetime.now(UTC).isoformat(),
        "target_scope": str(plan.target_scope),
        "replace": plan.replace,
        "source_document": str(source) if source is not None else None,
        "source_sha256": _sha256(source) if source is not None and source.is_file() else None,
        "operations": [
            {
                "kind": op.kind,
                "rel_path": str(op.rel_path),
                "role": op.role,
                "reason": op.reason,
            }
            for op in plan.operations
        ],
    }
    imports_dir.mkdir(parents=True, exist_ok=True)
    manifest_name = str(plan.target_scope).replace("/", "-") + ".json"
    manifest_path = imports_dir / manifest_name
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest_path


def print_report(report: WriteReport, *, dry_run: bool) -> None:
    """The shared dry-run/write summary formatter (buckets to stdout, diagnostics to
    stderr). Reused by the `work import` / `project page add` door."""
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
        raise ImportError(f"work not found in Markdown catalog: {work_ref}")
    if len(groups) > 1:
        choices = ", ".join(f"{kind}/{work_key}" for kind, work_key in sorted(groups))
        raise ImportError(f"--into is ambiguous ({choices}); pass --kind")
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
    """Write the cleaned/optimized downloadable `<lang>.docx` artifact for the bundle."""
    from docx_optimize import optimize_docx

    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        optimize_docx(src, dst)
    except Exception as exc:  # pragma: no cover - preserves error clarity
        raise ImportError(f"failed to optimize DOCX {src} -> {dst}: {exc}") from exc


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
    """Resolve the bundle cover: existing covers are read from `read_dir` (the real
    bundle, for additive --into), a new --cover is written into `write_dir` (the scratch
    stage), so the writer stays the only thing that touches the real target."""
    if cover_arg:
        src = Path(cover_arg).expanduser().resolve()
        if not src.is_file():
            raise ImportError(f"cover not found: {src}")
        if src.suffix.lower() not in IMAGE_EXTS:
            raise ImportError(f"unsupported cover image extension: {src.suffix}")
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
    request: ImportRequest,
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

    # Per-kind frontmatter defaults; other work kinds need none, hence `case _`.
    match kind:
        case "book":
            tags = fm.get("tags")
            if not isinstance(tags, list):
                tags = reference.frontmatter.get("tags") if reference else []
            fm["tags"] = tags if isinstance(tags, list) else []
        case "poem":
            if "date" not in fm:
                fm["date"] = reference.frontmatter.get("date") if reference else None
        case _:
            pass

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

    fm["translation"] = {"source": _translation_source(existing_lang, lang, request.translation_source)}
    return fm


def _resolve_target(
    request: ImportRequest,
    entries: list[CatalogEntry],
    docx: Path,
    content_root: Path,
) -> tuple[str, str, int, str, list[CatalogEntry]]:
    if request.into:
        matches = find_work_entries(entries, request.into, request.kind)
        kind, work_key, work_entries = _existing_group(matches, request.into)
        reference = _preferred_entry(work_entries, request.lang)
        number = request.number or reference.number
        slug = _slug_with_number(request.slug, number) if request.slug else reference.slug
        return kind, work_key, number, slug, work_entries

    if not request.kind:
        raise ImportError("--kind is required when importing a new work")
    kind = request.kind
    number = request.number or next_number(entries, kind)
    title = request.title or infer_title(docx)
    work_key = _slug_with_number(request.slug or title, number)
    slug = work_key
    work_dir = content_root / KIND_DIRS[kind] / work_key
    if work_dir.exists():
        raise ImportError(f"work bundle already exists: {work_dir}; use --into {work_key} to update it")
    return kind, work_key, number, slug, []


def _apply(request: ImportRequest) -> tuple[ImportResult, WriteReport]:
    """The side-effect-free import core: convert + plan + apply through the writer,
    returning both the `ImportResult` and the writer's `WriteReport`.

    Emits no stdout/stderr and never raises on a write refusal — a refusal rides home
    in `WriteReport.refused` with its fatal diagnostics. Raises `ImportError` only for
    invalid input or an unresolvable target. The writer is the only mutator."""
    docx = Path(request.docx).expanduser().resolve()
    if not docx.is_file():
        raise ImportError(f"DOCX not found: {docx}")
    if docx.suffix.lower() != ".docx":
        raise ImportError(f"expected a .docx file: {docx}")

    content_root = Path(request.out_content).expanduser().resolve()
    # Do not create content_root: the writer is the only mutator and dry-run must touch
    # nothing; scan_catalog tolerates a missing root.
    entries = [e for e in scan_catalog(content_root) if e.kind in WORK_KINDS]

    kind, work_key, number, slug, work_entries = _resolve_target(request, entries, docx, content_root)
    real_work_dir = content_root / KIND_DIRS[kind] / work_key
    scope = PurePosixPath(KIND_DIRS[kind]) / work_key

    existing_lang = _existing_lang_entry(work_entries, request.lang)
    reference = _preferred_entry(work_entries, request.lang) if work_entries else None
    inferred_title = infer_title(docx)
    if request.title:
        title = request.title
    elif existing_lang:
        title = existing_lang.title
    elif request.into and reference and (request.lang == reference.lang or not _majority_latin(inferred_title)):
        title = reference.title
    else:
        title = inferred_title

    if request.description:
        description = request.description
    elif existing_lang and existing_lang.description:
        description = existing_lang.description
    else:
        description = TODO_DESCRIPTION

    # Stage into a disposable scratch root; the pandoc media dir is a sibling holding
    # the extracted body images, which must live until the writer copies them. Body
    # images reach src/content only via the writer, never the stage.
    stage_root = STAGE_ROOT / uuid.uuid4().hex
    stage_dir = stage_root / KIND_DIRS[kind] / work_key
    media_out = stage_root / "media"
    stage_dir.mkdir(parents=True, exist_ok=True)
    try:
        title_index = build_title_index(entries)
        converted = convert_single_docx(
            docx,
            kind=kind,
            lang=request.lang,
            work_key=work_key,
            title=title,
            work_dir=stage_dir,
            title_index=title_index,
            media_out=media_out,
        )

        cover, cover_is_placeholder = _prepare_cover(
            cover_arg=str(request.cover) if request.cover is not None else None,
            read_dir=real_work_dir,
            write_dir=stage_dir,
            lang=request.lang,
            existing_lang=existing_lang,
            reference=reference,
        )
        fm = _frontmatter_for_import(
            request=request,
            kind=kind,
            number=number,
            slug=slug,
            title=title,
            description=description,
            lang=request.lang,
            cover=cover,
            cover_is_placeholder=cover_is_placeholder,
            existing_lang=existing_lang,
            reference=reference,
            converted=converted,
        )

        (stage_dir / f"{request.lang}.md").write_text(
            dump_frontmatter(fm) + converted.body, encoding="utf-8"
        )
        _write_docx_artifact(docx, stage_dir / f"{request.lang}.docx")
        write_bibliography_sidecar(stage_dir, kind, request.lang, converted.bibliography)

        # Footnote-fatal safety: an orphaned `[^id]` reference rides into the plan as a
        # FATAL the writer refuses on; unused/duplicate defs are non-blocking warnings.
        # The converter's typed diagnostics carry severity too, so a converter-side
        # FATAL (e.g. an unresolvable local image) also blocks. Both share the IR
        # `Diagnostic` shape; re-wrap to the plan's type at this boundary.
        diagnostics: tuple[Diagnostic, ...] = tuple(
            Diagnostic(d.severity, d.code, d.message)
            for d in footnotes.analyze_footnotes(converted.body)
        )
        diagnostics += tuple(
            Diagnostic(d.severity, d.code, d.message) for d in converted.diagnostics
        )
        plan = _plan_from_scratch(
            stage_work_dir=stage_dir,
            content_root=content_root,
            scope=scope,
            lang=request.lang,
            replace=bool(request.replace),
            diagnostics=diagnostics,
            source_document=docx,
            asset_ops=body_asset_ops(converted.assets, scope),
        )
        report = apply_plan(plan, dry_run=bool(request.dry_run))
        # Relay provenance only on a real apply that wrote — never a dry-run or refusal;
        # the writer emits no manifest.
        if not request.dry_run and not report.refused:
            _write_manifest(plan, imports_dir=_imports_dir(content_root))
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)

    result = ImportResult(
        kind=kind,
        work_key=work_key,
        md_path=real_work_dir / f"{request.lang}.md",
        docx_path=real_work_dir / f"{request.lang}.docx",
        warnings=converted.warnings,
    )
    return result, report


def import_work(request: ImportRequest) -> WriteReport:
    """The side-effect-free importer entry the `pancratius work import` CLI dispatches
    to. Returns the writer's `WriteReport` — including on a refusal, where
    `report.refused` carries the fatal diagnostics (it does not raise). Raises
    `ImportError` only for invalid input or an unresolvable target. The CLI owns all
    side effects."""
    return _apply(request)[1]


def run(args: argparse.Namespace) -> ImportResult:
    """Namespace -> `ImportRequest` -> `import_work` adapter that also emits the CLI
    summary and raises `SystemExit` on a refusal, returning the `ImportResult`. Kept so
    the tests and golden harness (`run(build_parser().parse_args(...))`) stay unchanged."""
    request = request_from_namespace(args)
    result, report = _apply(request)
    _emit_cli_report(request, result, report)
    if report.refused:
        raise SystemExit(
            f"refused to write {result.kind}/{result.work_key} ({request.lang}): "
            + "; ".join(d.message for d in report.diagnostics if d.severity == "fatal")
        )
    return result


def _emit_cli_report(request: ImportRequest, result: ImportResult, report: WriteReport) -> None:
    """The CLI's side effects: the dry-run/write summary, the `imported …`
    confirmation, and pandoc warnings. Kept out of `import_work` so the entry is silent."""
    print_report(report, dry_run=request.dry_run)
    if not report.refused and not request.dry_run:
        print(f"imported {result.kind}/{result.work_key} ({request.lang})")
        print(f"markdown: {result.md_path}")
        print(f"docx: {result.docx_path}")
    if result.warnings:
        print(f"pandoc warnings:\n{result.warnings}", file=sys.stderr)


def request_from_namespace(ns: argparse.Namespace) -> ImportRequest:
    """Adapt a parsed namespace into the typed `ImportRequest` — the single owner of
    the flag→field mapping, shared by the standalone CLI and the door."""
    return ImportRequest(
        docx=Path(ns.docx),
        lang=ns.lang,
        out_content=Path(ns.out_content),
        kind=ns.kind,
        into=ns.into,
        title=ns.title,
        number=ns.number,
        slug=ns.slug,
        description=ns.description,
        cover=Path(ns.cover) if ns.cover else None,
        translation_source=ns.translation_source,
        dry_run=bool(ns.dry_run),
        replace=bool(ns.replace),
    )


def add_import_arguments(ap: argparse.ArgumentParser) -> None:
    """Declare the import flags on `ap`, shared by `build_parser` and the
    `pancratius work import` door. `--kind` is declared here with `choices=WORK_KINDS`
    so the kind boundary is owned in one place (PAN017) and the door never redeclares it."""
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


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Import one DOCX into a Pancratius work bundle using Markdown frontmatter as the catalog.",
    )
    add_import_arguments(ap)
    return ap


def main(argv: list[str] | None = None) -> int:
    """The CLI: parse, dispatch, then own side effects — print the summary + pandoc
    warnings and exit nonzero on a refusal. Invalid input / an unresolvable target
    maps `ImportError` to `parser.error` (the argparse usage exit)."""
    parser = build_parser()
    ns = parser.parse_args(argv)
    if shutil.which("pandoc") is None:
        parser.error("pandoc not found on PATH; install with `brew install pandoc`")
    request = request_from_namespace(ns)
    try:
        result, report = _apply(request)
    except ImportError as exc:
        parser.error(str(exc))
    _emit_cli_report(request, result, report)
    return 1 if report.refused else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
