"""pancratius — the library door (docs/tooling.md).

A noun-first argparse dispatcher invoked as ``uv run pancratius <group> <verb> …``.
The verb space *teaches the corpus ontology*: domain (noun) first, so ``--help`` at
each level is a navigable map of what the library can do.

The door calls **library functions, not other CLIs**, and owns ONE uniform output
contract:

    exit 0  success
    exit 1  refusal or failure
    exit 2  usage error

Human-readable summaries go to stdout; diagnostics go to stderr. It makes no
editorial/domain decisions and runs no verification — that is ``npm run audit``.

This door dispatches to importable ``pancratius`` package modules. The heavy
conceptosphere owners are imported lazily inside their handlers, so the light
core never imports the graph/embed stacks just to print ``--help``.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pancratius.cover.models import CoverResult
    from pancratius.import_docx import ImportRequest
    from pancratius.translate import TranslationReport


def _missing_extra(extra: str, exc: ImportError) -> int:
    """A heavy owner failed to import: print the install hint to stderr and fail
    (exit 1) instead of dumping a traceback (docs/tooling.md "Dependency model").

    The message HEDGES rather than asserting the extra is uninstalled — the same
    `ImportError` also fires if the extra IS installed but its stack (or the owner)
    has an import-time fault. Either way the actual missing module is shown (`exc`)
    and the actionable remedy is the same."""
    print(
        f"error: could not load the '{extra}' stack ({exc}).",
        file=sys.stderr,
    )
    print(f"if its optional dependencies are not installed, run: uv sync --extra {extra}", file=sys.stderr)
    return 1


def _fail(msg: str | Exception, code: int = 1) -> int:
    """Report a CLI-level error to stderr and return the exit code.

    Used for terminal failures paired with a non-zero exit (`return _fail(...)`);
    mid-work non-fatal problems go through `logger.warning` / `logger.error`."""
    print(f"error: {msg}", file=sys.stderr)
    return code


def _require_pandoc() -> int | None:
    """Shared precheck for the conversion verbs (`work import`, `project page add`):
    return 1 if pandoc is absent, else None to proceed."""
    if shutil.which("pandoc") is None:
        return _fail("pandoc not found on PATH; install with `brew install pandoc`.")
    return None


def _require_subcommand(parser: argparse.ArgumentParser) -> Callable[[argparse.Namespace], int]:
    """A `func` default for every non-leaf parser: running a bare group/noun with no
    verb prints THAT level's help to stderr and signals a usage error (exit 2),
    instead of relying on argparse's brittle required-subparser handling."""

    def handler(_args: argparse.Namespace) -> int:
        parser.print_help(sys.stderr)
        return 2

    return handler


# --- handlers (work group) ----------------------------------------------------
def _import_request_from_args(args: argparse.Namespace) -> ImportRequest:
    from pancratius import import_docx

    return import_docx.ImportRequest(
        docx=Path(args.docx),
        lang=args.lang,
        out_content=Path(args.out_content),
        kind=args.kind,
        into=args.into,
        title=args.title,
        number=args.number,
        slug=args.slug,
        description=args.description,
        cover=Path(args.cover) if args.cover else None,
        translation_source=args.translation_source,
        dry_run=bool(args.dry_run),
        replace=bool(args.replace),
    )


def _work_import(args: argparse.Namespace) -> int:
    """`work import <docx> --kind book|poem` — import a corpus work bundle.

    Builds an `ImportRequest` from the parsed flags and dispatches to `import_work`,
    which is silent and returns the writer's report (or raises on bad input / an
    unresolvable target). The door owns all output: it prints the report (the
    `--dry-run` review gate) and maps a write refusal to a failure exit."""
    from pancratius import import_docx

    if (rc := _require_pandoc()) is not None:
        return rc
    request = _import_request_from_args(args)
    try:
        report = import_docx.import_work(request)
    except import_docx.ImportWorkError as exc:
        # Bad input / an unresolvable target is a usage error (exit 2), matching
        # the door's contract.
        return _fail(exc, 2)
    import_docx.print_report(report, dry_run=request.dry_run)
    return 1 if report.refused else 0


def _print_translate_report(report: TranslationReport) -> float:
    """Print one book's outcome and return its USD cost contribution (estimate
    for --dry-run, real billed cost for a live run)."""
    if report.dry_run and report.estimate is not None:
        est = report.estimate
        print(
            f"  {report.book_key}: {report.units} units, {report.chunks} chunks, "
            f"~{est.source_tokens / 1000:.1f}k src tok  est ${est.total_usd:.4f} "
            f"(draft ${est.draft_cost_usd:.4f} + revise ${est.revise_cost_usd:.4f} "
            f"+ profile ${est.profile_cost_usd:.4f})"
        )
        return est.total_usd
    cost = report.usage.cost_usd or 0.0
    findings = ", ".join(
        f"{sum(1 for f in report.findings if f.severity == sev)}×{sev.name.lower()}"
        for sev in sorted({f.severity for f in report.findings}, reverse=True)
    )
    where = report.written_path.name if report.written_path else "(not written)"
    cache_note = f"; {report.cached_chunks} chunks from cache" if report.cached_chunks else ""
    print(
        f"  wrote {report.book_key}/{where}: {report.units} units, {report.chunks} chunks; "
        f"cost ${cost:.4f}; cached {report.usage.cached_tokens} tok"
        + cache_note
        + (f"; findings {findings}" if findings else "")
    )
    for line in report.digest:
        print(line)
    return cost


def _work_translate(args: argparse.Namespace) -> int:
    """`work translate [--book N]` — draft an ``en.md`` from a book's ``ru.md``.

    Mechanical draft only (docs/tooling.md): writes ``translation.source: ai`` and
    leaves ``reviewed_by`` for a human. ``--dry-run`` prints the plan and a
    live-priced cost estimate without an API key or any generative call."""
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from datetime import date

    from pancratius import translate as xlate
    from pancratius.content_catalog import CatalogEntry, scan_catalog

    content_root = Path(args.out_content)
    catalog = scan_catalog(content_root)
    if args.book is not None:
        targets = [
            e for e in catalog if e.kind == args.kind and e.number == args.book and e.lang == "ru"
        ]
        if not targets:
            return _fail(f"no {args.kind} ru source with number {args.book}", 2)
    else:
        targets = xlate.find_untranslated(catalog, kind=args.kind)
        if args.limit:
            targets = targets[: args.limit]
    if not targets:
        print("nothing to translate (every work already has a translation).")
        return 0

    config = xlate.TranslateConfig(
        models=xlate.StageModels(
            profile=args.profile_model or args.model,
            draft=args.model,
            revise=args.revise_model or args.model,
        ),
        chunk_source_tokens=args.chunk_tokens,
        build_profile=not args.no_profile,
        revise=not args.no_revise,
        reconcile=not args.no_reconcile,
    )
    tag_glossary = Path(__file__).resolve().parents[1] / "data" / "tag-glossary.json"
    try:
        glossary = xlate.load_glossary(Path(args.glossary)) if args.glossary else ()
        tag_labels = xlate.load_tag_labels(tag_glossary)
        client = (
            xlate.OpenRouterClient(api_key="") if args.dry_run else xlate.OpenRouterClient.from_env()
        )
    except (xlate.OpenRouterError, ValueError, OSError) as exc:
        return _fail(exc)

    today = date.today().isoformat()
    cache_dir: Path | None = None
    if not args.dry_run and not args.no_cache:
        cache_dir = Path(".cache") / "translate"
    workers = max(1, args.workers)
    verb = "estimating" if args.dry_run else "translating"
    print(f"{verb} {len(targets)} {args.kind}(s) with {args.model} ({workers} workers):", flush=True)
    total = 0.0
    failures = 0
    lock = threading.Lock()
    stop = threading.Event()

    def run_one(entry: CatalogEntry) -> xlate.TranslationReport | None:
        # Each book is independent and keeps its chunks sequential, so its prompt
        # cache stays warm; the pool only parallelizes across books.
        if stop.is_set():
            return None
        return xlate.translate_book(
            client, config, entry=entry, catalog=catalog, glossary=glossary,
            generated_at=today, dry_run=args.dry_run, replace=args.replace,
            cache_dir=cache_dir, tag_labels=tag_labels,
        )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run_one, e): e for e in targets}
        for future in as_completed(futures):
            entry = futures[future]
            try:
                report = future.result()
            except Exception as exc:  # noqa: BLE001 — isolate one book's failure from the batch
                with lock:
                    failures += 1
                print(f"  skip {entry.work_key}: {exc!r}", file=sys.stderr, flush=True)
                continue
            if report is None:
                continue
            with lock:
                total += _print_translate_report(report)
                sys.stdout.flush()
                if args.max_cost and not args.dry_run and total > args.max_cost:
                    if not stop.is_set():
                        print(f"stopping: billed ${total:.2f} exceeded --max-cost "
                              f"${args.max_cost:.2f}", file=sys.stderr, flush=True)
                    stop.set()
                    for pending in futures:
                        pending.cancel()
    label = "estimated total" if args.dry_run else "billed total"
    print(f"{label}: ${total:.2f}; {failures} failed/skipped")
    return 1 if failures or stop.is_set() else 0


# --- handlers (work cover) ---------------------------------------------------
def _print_cover_result(result: CoverResult) -> None:
    """Print a single cover result to stdout."""
    from pancratius.cover.models import QaVerdict

    tag = "OK  " if result.ok else "FAIL"
    attempts_used = len([a for a in result.attempts if a.attempt > 0])
    skipped = any(a.attempt == 0 for a in result.attempts)
    cost = f"${result.total_cost_usd:.5f}"
    title_tag = f"[{result.title.source}]" if result.title.is_pinned else "[model]"

    if skipped:
        print(f"  {tag} {result.book_key:8s} {cost}  {title_tag}  (existing cover passed QA)")
        return

    verdict = result.attempts[-1].qa.verdict if result.attempts else "?"
    qa_label = "PASS" if verdict == QaVerdict.PASS else f"FAIL({attempts_used}atts)"
    where = str(result.final_path) if result.final_path else "(none)"
    print(f"  {tag} {result.book_key:8s} {cost}  {title_tag}  QA:{qa_label}  {where}")
    if result.error and not result.ok:
        print(f"      unresolved: {result.error}")


def _work_cover(args: argparse.Namespace) -> int:
    """`work cover [book-XX …]` — translate Russian book covers to English.

    Each cover goes through: (1) vision recon to identify displayed text,
    (2) fused image-edit generation with a pinned EN title and overrides,
    (3) vision QA against both images. On QA failure the discrepancies are
    folded into a steering addendum for the next attempt (up to 3 attempts).
    If an .en.png already exists it is QA-d first; PASS → done, no regeneration.
    """
    import re

    from pancratius.cover.client import api_key_from_env
    from pancratius.cover.pipeline import (
        CoverTranslateConfig,
        discover_books,
        translate_cover,
    )

    try:
        api_key = api_key_from_env()
    except ValueError as exc:
        return _fail(exc)

    output_dir = Path(args.output_dir)

    # Resolve book keys from positional arguments or discover all
    if args.books:
        # Each arg can be "book-50", "50", or "book-50 book-51" (split)
        raw_keys: list[str] = []
        for tok in args.books:
            for part in tok.split():
                raw_keys.append(part)
        book_keys: list[str] = []
        for raw in raw_keys:
            if re.fullmatch(r"\d+", raw):
                book_keys.append(f"book-{int(raw):02d}")
            elif re.fullmatch(r"book-\d+", raw):
                # normalise to book-NN (two-digit)
                num = int(raw.split("-")[1])
                book_keys.append(f"book-{num:02d}")
            else:
                return _fail(f"unrecognised book key {raw!r} (use 'book-50' or '50')", 2)
    else:
        from pancratius.cover.pipeline import DEFAULT_COVERS_DIR
        covers_dir = Path(args.covers_dir) if args.covers_dir else DEFAULT_COVERS_DIR
        book_keys = discover_books(covers_dir)
        if not book_keys:
            print("no source covers found.")
            return 0

    from pancratius.cover.pipeline import (
        DEFAULT_BOOKS_ROOT,
        DEFAULT_COVERS_DIR,
        DEFAULT_QUEUE_MD,
        DEFAULT_SEED_PATH,
    )

    config = CoverTranslateConfig(
        output_dir=output_dir,
        covers_dir=Path(args.covers_dir) if args.covers_dir else DEFAULT_COVERS_DIR,
        queue_md=Path(args.queue_md) if args.queue_md else DEFAULT_QUEUE_MD,
        books_root=Path(args.books_root) if args.books_root else DEFAULT_BOOKS_ROOT,
        seed_path=Path(args.seed) if args.seed else DEFAULT_SEED_PATH,
        max_attempts=args.max_attempts,
    )

    print(f"cover translate: {len(book_keys)} book(s) → {output_dir}")
    print(f"books: {', '.join(book_keys)}\n")

    results = []
    total = 0.0
    for book_key in book_keys:
        print(f"[{book_key}]")
        try:
            result = translate_cover(book_key, config, api_key)
        except Exception as exc:  # noqa: BLE001 — one bad cover must not kill the batch
            print(f"  FAIL {book_key}: {exc}")
            continue
        _print_cover_result(result)
        total += result.total_cost_usd
        results.append(result)

    failed = [r for r in results if not r.ok]
    print(f"\ntotal spent: ${total:.5f}")
    print(f"succeeded: {sum(1 for r in results if r.ok)}/{len(results)}")
    if failed:
        print("failed:")
        for r in failed:
            print(f"  {r.book_key}: {r.error}")
    return 1 if failed else 0


# --- handlers (project group) -------------------------------------------------
def _project_page_add(args: argparse.Namespace) -> int:
    """`project page add <project> <subpage-slug> <docx>` — scaffold a draft
    sub-page (the deterministic slice only; docs/tooling.md).

    Dispatches to `scaffold_subpage`, which converts the DOCX, co-locates images,
    and writes the draft `<lang>.md` with editorial fields left `TODO` through the
    general writer (no provenance manifest). After a REAL apply the door prints the
    suggested landing `subpages:` entry to STDOUT for a human to place — it never
    edits the landing. Bad input (missing/non-DOCX) is a usage error (exit 2); a
    write refusal is a failure (exit 1)."""
    from pancratius import import_docx  # for print_report (shared report formatter) — light, no ML
    from pancratius.docx_conversion import ScaffoldError, scaffold_subpage

    if (rc := _require_pandoc()) is not None:
        return rc
    try:
        report = scaffold_subpage(
            project=args.project,
            subpage_slug=args.subpage_slug,
            docx=Path(args.docx),
            lang=args.lang,
            out_content=Path(args.out_content),
            dry_run=args.dry_run,
        )
    except ScaffoldError as exc:
        return _fail(exc, 2)
    import_docx.print_report(report, dry_run=args.dry_run)
    if not args.dry_run and not report.refused:
        # Print the suggested landing entry only after a REAL write — it is a
        # post-write next step, not part of the --dry-run preview (nothing was
        # written to place it against). The human places it; the landing is NEVER
        # edited by the tool.
        print(
            f"\nadd this entry to projects/{args.project}/{args.lang}.md  subpages:  "
            "(place it yourself — the landing is never edited):"
        )
        print(f"  - slug: {args.subpage_slug}")
        print('    label: "TODO: short landing label"')
        print('    weight: "TODO: essay|revelation|verse|practice|dialogue"')
    return 1 if report.refused else 0


# --- handlers (downloads / docx groups) ---------------------------------------
def _downloads_render(args: argparse.Namespace) -> int:
    """`downloads render [--book N]` — render local PDF/EPUB release artifacts.
    Pass-through to the render owner, which prints its own progress/summary."""
    from pancratius.render_downloads import DownloadRenderError, render

    try:
        render(
            book=args.book,
            poem=args.poem,
            lang=args.lang,
            skip_pdf=args.skip_pdf,
            skip_epub=args.skip_epub,
            force=args.force,
        )
    except DownloadRenderError as exc:
        return _fail(exc)
    return 0


def _docx_optimize(args: argparse.Namespace) -> int:
    """`docx optimize [paths…]` — in-place source DOCX cleanup. Pass-through to the
    optimize owner (which has its own `--dry-run`)."""
    from pancratius.docx_optimize import optimize

    summary = optimize(
        paths=[Path(p) for p in args.paths],
        force=args.force,
        verbose=args.verbose,
        dry_run=args.dry_run,
    )
    return 1 if summary.failed else 0


def _docx_merge(args: argparse.Namespace) -> int:
    """`docx merge` — merge multipart source DOCX files and validate the package."""
    from pancratius.docx_merge import DocxMergeError, DocxMergeUsageError, merge_docx
    from pancratius.docx_outline import DocxOutlineError, parse_part_spec

    try:
        parts = tuple(parse_part_spec(raw) for raw in args.part)
        summary = merge_docx(
            tuple(Path(src) for src in args.inputs),
            Path(args.out),
            parts=parts,
        )
    except DocxOutlineError as exc:
        return _fail(exc, 2)
    except DocxMergeUsageError as exc:
        return _fail(exc, 2)
    except DocxMergeError as exc:
        return _fail(exc, 1)
    outline = ""
    if summary.outline is not None:
        outline = (
            f"; inserted {summary.outline.inserted_parts} part headings; "
            f"demoted {summary.outline.demoted_headings} headings"
        )
    print(
        f"merged {len(summary.inputs)} DOCX file(s) -> {summary.output} "
        f"({summary.validation.package_parts} package parts; "
        f"{summary.validation.relationships} relationships; "
        f"{summary.validation.media_parts} media parts{outline})"
    )
    return 0


def _docx_inspect(args: argparse.Namespace) -> int:
    """`docx inspect` — read-only source DOCX/importer signal diagnostics."""
    from pancratius.docx_inspect import (
        DocxInspectError,
        InspectOptions,
        inspect_docx,
        parse_index_range,
        render_inspection,
        resolve_book_docx,
    )

    try:
        options = InspectOptions(
            contains=args.contains,
            around=args.around,
            context=args.context,
            index_range=parse_index_range(args.range),
            verse_only=args.verse_only,
            lineated_only=args.lineated_only,
        )
        docx = (
            resolve_book_docx(args.book, lang=args.lang, content_root=Path(args.content_root))
            if args.book is not None
            else Path(args.docx)
        )
        result = inspect_docx(docx, options)
    except DocxInspectError as exc:
        return _fail(exc, 2)
    print(render_inspection(result))
    return 0


def _docx_render_slice(args: argparse.Namespace) -> int:
    """`docx render-slice` — render a diagnostic paragraph slice via LibreOffice."""
    from pancratius.docx_inspect import DocxInspectError, parse_index_range, resolve_book_docx
    from pancratius.docx_render import DocxRenderError, range_key, render, resolve_range

    try:
        docx = (
            resolve_book_docx(args.book, lang=args.lang, content_root=Path(args.content_root))
            if args.book is not None
            else Path(args.docx)
        )
        lo, hi, rows = resolve_range(
            docx,
            around=args.around,
            context=args.context,
            index_range=parse_index_range(args.range),
        )
        pages = render(docx, lo, hi, Path(args.out))
    except (DocxInspectError, DocxRenderError) as exc:
        return _fail(exc, 2)
    print(f"rendered paragraphs [{lo}..{hi}] of {docx.name} -> {len(pages)} page(s)")
    for page in pages:
        print(f"  {page}")
    print("\nparagraph index -> text (correlate with `docx inspect`):")
    for line in range_key(rows, lo, hi):
        print(f"  {line}")
    return 0


# --- handlers (conceptosphere group) ------------------------------------------
def _conceptosphere_graph_generate(args: argparse.Namespace) -> int:
    """`conceptosphere graph generate [--only concepts|books]` — regenerate the
    committed concept/book graph projections into data/ (heavy — graph extra)."""
    try:
        from pancratius.conceptosphere import GraphConfig, GraphGenerationError, generate_graph
    except ImportError as exc:
        return _missing_extra("graph", exc)
    config = GraphConfig(
        top=args.top,
        window=args.window,
        min_degree=args.min_degree,
        min_weight=args.min_weight,
        min_freq=args.min_freq,
        edges_per_node=args.edges_per_node,
        min_npmi=args.min_npmi,
        books_edges_per_node=args.books_edges_per_node,
        books_min_cosine=args.books_min_cosine,
    )
    try:
        generate_graph(
            only=args.only,
            config=config,
            concepts_out=args.concepts_out,
            books_out=args.books_out,
            quiet=args.quiet,
        )
    except GraphGenerationError as exc:
        return _fail(exc)
    return 0


def _conceptosphere_embed_generate(args: argparse.Namespace) -> int:
    """`conceptosphere embed generate` — regenerate committed semantic embeddings
    into data/ (heavy — embed extra)."""
    try:
        from pancratius.conceptosphere_embed import generate_embeddings
    except ImportError as exc:
        return _missing_extra("embed", exc)
    generate_embeddings(
        model=args.model,
        rebuild=args.rebuild,
        batch_size=args.batch_size,
        max_length=args.max_length,
        out=args.out,
        limit=args.limit,
    )
    return 0


# --- parser assembly ----------------------------------------------------------
# Each group is built by its own function so later phases add groups/verbs locally.
def _add_work_group(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    from pancratius import import_docx  # light (no ML); owns DEFAULT_CONTENT_ROOT
    from pancratius.kinds import CORPUS_WORK_KINDS
    from pancratius.locales import LOCALES
    from pancratius.translate.config import DEFAULT_MODEL as DEFAULT_TRANSLATION_MODEL

    work = sub.add_parser("work", help="Import corpus works (a book or a poem).")
    work.set_defaults(func=_require_subcommand(work))
    work_sub = work.add_subparsers(dest="noun", metavar="<noun>")
    work_import = work_sub.add_parser(
        "import", help="Import one DOCX into a work bundle (--kind book|poem; --into to add a translation)."
    )
    work_import.add_argument("docx", help="Source .docx file to import.")
    work_import.add_argument(
        "--kind",
        choices=tuple(CORPUS_WORK_KINDS),
        help="Required for a new work; optional with --into when the bundle is unique.",
    )
    work_import.add_argument("--lang", choices=tuple(LOCALES), required=True)
    work_import.add_argument("--into", help="Existing work bundle key or frontmatter slug to update.")
    work_import.add_argument(
        "--out-content", default=str(import_docx.DEFAULT_CONTENT_ROOT), help="Content root; defaults to src/content."
    )
    work_import.add_argument("--title", help="Override frontmatter title.")
    work_import.add_argument(
        "--number", type=int, help="Override work number; defaults to next number for new works or existing number with --into."
    )
    work_import.add_argument(
        "--slug", help="Override frontmatter/work slug. Without a numeric prefix, the work number is prepended."
    )
    work_import.add_argument("--description", help="Override frontmatter description.")
    work_import.add_argument("--cover", help="Optional cover image to copy as cover.<lang>.<ext>.")
    work_import.add_argument(
        "--translation-source", choices=["original", "literary", "ai"], help="Override translation.source."
    )
    work_import.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the full planned write-set + diagnostics and write NOTHING (the review gate).",
    )
    work_import.add_argument(
        "--replace",
        action="store_true",
        help="Permit overwriting an existing converter-owned <lang>.md; without it, re-importing an existing language is refused.",
    )
    work_import.set_defaults(func=_work_import)

    work_translate = work_sub.add_parser(
        "translate",
        help="Draft an en.md translation for a book.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    work_translate.add_argument(
        "--book", type=int, help="Translate only this book number (default: every untranslated book)."
    )
    work_translate.add_argument(
        "--kind", choices=tuple(CORPUS_WORK_KINDS), default="book", help="Work kind to translate."
    )
    work_translate.add_argument(
        "--out-content", default=str(import_docx.DEFAULT_CONTENT_ROOT), help="Content root; defaults to src/content."
    )
    work_translate.add_argument(
        "--model", default=DEFAULT_TRANSLATION_MODEL, help="OpenRouter model for the draft translation."
    )
    work_translate.add_argument("--profile-model", help="Override the model for the brief pre-pass.")
    work_translate.add_argument("--revise-model", help="Override the model for the revise pass.")
    work_translate.add_argument(
        "--chunk-tokens", type=int, default=3000, help="Target source tokens generated per chunk."
    )
    work_translate.add_argument("--glossary", help="YAML glossary of locked source→target terms.")
    work_translate.add_argument("--no-profile", action="store_true", help="Skip the per-book brief pre-pass.")
    work_translate.add_argument("--no-revise", action="store_true", help="Skip the source-aware revise pass.")
    work_translate.add_argument(
        "--no-reconcile", action="store_true", help="Skip the cross-seam reconcile pass (flagged boundaries only)."
    )
    work_translate.add_argument(
        "--limit", type=int, default=0, help="Translate only the first N untranslated works (smoke test)."
    )
    work_translate.add_argument(
        "--max-cost", type=float, default=0.0, help="Abort once billed cost exceeds this many USD (0 = no cap)."
    )
    work_translate.add_argument(
        "--workers", type=int, default=12, help="Books translated concurrently (each book stays sequential internally)."
    )
    work_translate.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan and a cost estimate; write nothing (no API key needed).",
    )
    work_translate.add_argument(
        "--replace", action="store_true", help="Overwrite an existing en.md (otherwise refused)."
    )
    work_translate.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass the on-disk chunk cache; always call the API and never write cached results.",
    )
    work_translate.set_defaults(func=_work_translate)

    work_cover = work_sub.add_parser(
        "cover",
        help="Translate Russian book covers to English via image editing (recon→generate→QA loop).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    work_cover.add_argument(
        "books",
        nargs="*",
        metavar="book-XX",
        help="Book key(s) to translate (e.g. book-50 or 50). Omit to translate all discovered covers.",
    )
    work_cover.add_argument(
        "--output-dir",
        default="cover-out",
        help="Directory for .raw.png and .en.png outputs (default: cover-out/ relative to cwd).",
    )
    work_cover.add_argument("--covers-dir", help="Override the source cover directory.")
    work_cover.add_argument("--queue-md", help="Override QUEUE.md path.")
    work_cover.add_argument("--books-root", help="Override src/content/books root.")
    work_cover.add_argument("--seed", help="Override seed.json path.")
    work_cover.add_argument(
        "--max-attempts",
        type=int,
        default=3,
        help="Maximum generation+QA attempts per cover before giving up.",
    )
    work_cover.set_defaults(func=_work_cover)


def _add_project_group(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    from pancratius import import_docx  # light (no ML); owns DEFAULT_CONTENT_ROOT
    from pancratius.locales import LOCALES

    project = sub.add_parser("project", help="Scaffold project material (a themed section).")
    project.set_defaults(func=_require_subcommand(project))
    project_sub = project.add_subparsers(dest="noun", metavar="<noun>")
    page = project_sub.add_parser("page", help="Project sub-pages.")
    page.set_defaults(func=_require_subcommand(page))
    page_sub = page.add_subparsers(dest="verb", metavar="<verb>")
    page_add = page_sub.add_parser(
        "add", help="Scaffold a draft sub-page from a DOCX (editorial fields left TODO; landing never edited)."
    )
    page_add.add_argument("project", help="Owning project slug (the landing's slug).")
    page_add.add_argument("subpage_slug", metavar="<subpage-slug>", help="Sub-page slug under the project.")
    page_add.add_argument("docx", help="Source .docx file to convert into the draft body.")
    page_add.add_argument("--lang", choices=tuple(LOCALES), required=True)
    page_add.add_argument(
        "--out-content", default=str(import_docx.DEFAULT_CONTENT_ROOT), help="Content root; defaults to src/content."
    )
    page_add.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the full planned write-set + diagnostics and write NOTHING (the review gate).",
    )
    page_add.set_defaults(func=_project_page_add)


def _add_downloads_group(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    from pancratius.locales import LOCALES

    downloads = sub.add_parser("downloads", help="Render local release artifacts (PDF/EPUB).")
    downloads.set_defaults(func=_require_subcommand(downloads))
    downloads_sub = downloads.add_subparsers(dest="noun", metavar="<noun>")
    render = downloads_sub.add_parser("render", help="Render release artifacts (never CI).")
    render.add_argument("--book", type=int, help="Render only this book number.")
    render.add_argument("--poem", type=int, help="Render only this poem number.")
    render.add_argument("--lang", choices=tuple(LOCALES), help="Restrict to one language.")
    render.add_argument("--skip-pdf", action="store_true", help="Skip PDF rendering.")
    render.add_argument("--skip-epub", action="store_true", help="Skip EPUB rendering.")
    render.add_argument("--force", action="store_true", help="Re-render even if output is newer than source.")
    render.set_defaults(func=_downloads_render)


def _add_docx_group(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    from pancratius import import_docx  # light (no ML); owns DEFAULT_CONTENT_ROOT
    from pancratius.locales import LOCALES

    docx = sub.add_parser("docx", help="Maintain source DOCX artifacts.")
    docx.set_defaults(func=_require_subcommand(docx))
    docx_sub = docx.add_subparsers(dest="noun", metavar="<noun>")
    optimize = docx_sub.add_parser("optimize", help="In-place source DOCX cleanup (image cap, scrub).")
    optimize.add_argument(
        "paths", nargs="*", help="Specific .docx files or directories. Defaults to the corpus source roots."
    )
    optimize.add_argument("--force", action="store_true", help="Re-process even if dst is newer than src.")
    optimize.add_argument("--verbose", "-v", action="store_true")
    optimize.add_argument("--dry-run", action="store_true", help="Print what would be done; write nothing.")
    optimize.set_defaults(func=_docx_optimize)

    merge = docx_sub.add_parser(
        "merge",
        help="Merge one or more source DOCX files into one package-validated DOCX.",
    )
    merge.add_argument("inputs", nargs="+", help="Input .docx files, in source order.")
    merge.add_argument("--out", required=True, help="Output .docx path.")
    merge.add_argument(
        "--part",
        action="append",
        default=[],
        help="Optional part spec shaped as 'Part title::first heading prefix'. Repeat in source order.",
    )
    merge.set_defaults(func=_docx_merge)

    inspect = docx_sub.add_parser(
        "inspect",
        help="Inspect read-only OOXML/importer paragraph signals in a source DOCX.",
    )
    inspect_src = inspect.add_mutually_exclusive_group(required=True)
    inspect_src.add_argument("docx", nargs="?", help="Source .docx file to inspect.")
    inspect_src.add_argument("--book", type=int, help="Committed book number; inspects its <lang>.docx.")
    inspect.add_argument("--lang", choices=tuple(LOCALES), default="ru", help="Language for --book.")
    inspect.add_argument(
        "--content-root",
        default=str(import_docx.DEFAULT_CONTENT_ROOT),
        help="Content root for --book lookup; defaults to src/content.",
    )
    inspect_filter = inspect.add_mutually_exclusive_group()
    inspect_filter.add_argument("--contains", help="Show only rows whose source text contains this substring.")
    inspect_filter.add_argument(
        "--around",
        help="Show rows around paragraphs whose source text contains this substring.",
    )
    inspect_filter.add_argument("--range", help="Show row index range LO:HI (inclusive).")
    inspect_filter.add_argument(
        "--verse-only",
        action="store_true",
        help="Show only rows the importer promoted to verse register.",
    )
    inspect_filter.add_argument(
        "--lineated-only",
        action="store_true",
        help="Show only rows the importer folded into a lineated-prose block.",
    )
    inspect.add_argument("--context", type=int, default=6, help="Rows of context for --around.")
    inspect.set_defaults(func=_docx_inspect)

    render_slice = docx_sub.add_parser(
        "render-slice",
        help="Render a diagnostic DOCX paragraph slice to PNG via LibreOffice.",
    )
    render_src = render_slice.add_mutually_exclusive_group(required=True)
    render_src.add_argument("docx", nargs="?", help="Source .docx file to render.")
    render_src.add_argument("--book", type=int, help="Committed book number; renders its <lang>.docx.")
    render_slice.add_argument("--lang", choices=tuple(LOCALES), default="ru", help="Language for --book.")
    render_slice.add_argument(
        "--content-root",
        default=str(import_docx.DEFAULT_CONTENT_ROOT),
        help="Content root for --book lookup; defaults to src/content.",
    )
    render_filter = render_slice.add_mutually_exclusive_group(required=True)
    render_filter.add_argument("--around", help="Render rows around paragraphs containing this text.")
    render_filter.add_argument("--range", help="Render row index range LO:HI (inclusive).")
    render_slice.add_argument("--context", type=int, default=10, help="Rows of context for --around.")
    render_slice.add_argument("--out", required=True, help="Output PNG path; multi-page slices add suffixes.")
    render_slice.set_defaults(func=_docx_render_slice)


def _video_sync(args: argparse.Namespace) -> int:
    """`video sync` — poll configured YouTube channels, scaffold drafts for new
    videos. Mechanical only; commentary in the body is editorial.

    Re-runs are idempotent: known IDs are skipped, editor edits preserved."""
    from pancratius import video_scan

    dry_run: bool = args.dry_run
    try:
        result = video_scan.scan(channel_key=args.channel, dry_run=dry_run)
    except video_scan.VideoScanError as exc:
        return _fail(exc)
    video_scan.print_result(result, dry_run=dry_run)
    return 0


def _add_video_group(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    video = sub.add_parser("video", help="Catalogue and sync YouTube/mirror videos.")
    video.set_defaults(func=_require_subcommand(video))
    video_sub = video.add_subparsers(dest="noun", metavar="<noun>")
    sync = video_sub.add_parser(
        "sync",
        help="Poll configured channels and scaffold draft entries for new videos.",
    )
    sync.add_argument(
        "--channel",
        help="Restrict to a single channel key (from src/content/videos/channels.yaml).",
    )
    sync.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned actions; write nothing.",
    )
    sync.set_defaults(func=_video_sync)


def _add_conceptosphere_group(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    conceptosphere = sub.add_parser("conceptosphere", help="Generate committed concept graph data.")
    conceptosphere.set_defaults(func=_require_subcommand(conceptosphere))
    concept_sub = conceptosphere.add_subparsers(dest="noun", metavar="<noun>")

    graph = concept_sub.add_parser("graph", help="Concept/book graphs (heavy — uv sync --extra graph).")
    graph.set_defaults(func=_require_subcommand(graph))
    graph_sub = graph.add_subparsers(dest="verb", metavar="<verb>")
    graph_generate = graph_sub.add_parser(
        "generate",
        help="Regenerate BOTH graph projections into data/ (--only for one).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    graph_generate.add_argument(
        "--only", choices=("concepts", "books"), default=None,
        help="Regenerate only this projection (default: both, off one corpus scan).",
    )
    graph_generate.add_argument("--top", type=int, default=420, help="[concepts] Node cap after pruning.")
    graph_generate.add_argument("--window", type=int, default=4, help="[concepts] Co-occurrence window.")
    graph_generate.add_argument("--min-degree", type=int, default=3, help="[concepts] Minimum node degree.")
    graph_generate.add_argument("--min-weight", type=int, default=6, help="[concepts] Minimum raw edge weight.")
    graph_generate.add_argument("--min-freq", type=int, default=14, help="Minimum lemma corpus frequency.")
    graph_generate.add_argument("--edges-per-node", type=int, default=10, help="[concepts] Backbone edges per node.")
    graph_generate.add_argument("--min-npmi", type=float, default=0.18, help="[concepts] Minimum NPMI.")
    graph_generate.add_argument("--books-edges-per-node", type=int, default=5, help="[books] Neighbor cap per book.")
    graph_generate.add_argument("--books-min-cosine", type=float, default=0.10, help="[books] Minimum cosine floor.")
    graph_generate.add_argument("--concepts-out", type=Path, default=None, help="Override concepts graph output path.")
    graph_generate.add_argument("--books-out", type=Path, default=None, help="Override books graph output path.")
    graph_generate.add_argument("--quiet", action="store_true", help="Suppress progress output.")
    graph_generate.set_defaults(func=_conceptosphere_graph_generate)

    embed = concept_sub.add_parser("embed", help="Semantic embeddings (heavy — uv sync --extra embed).")
    embed.set_defaults(func=_require_subcommand(embed))
    embed_sub = embed.add_subparsers(dest="verb", metavar="<verb>")
    embed_generate = embed_sub.add_parser(
        "generate",
        help="Regenerate embeddings into data/.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    embed_generate.add_argument(
        "--model",
        default="Qwen/Qwen3-Embedding-0.6B",
        help="MLX-loadable embedding model.",
    )
    embed_generate.add_argument("--rebuild", action="store_true", help="Ignore cache and re-embed every chunk.")
    embed_generate.add_argument("--batch-size", type=int, default=8, help="Embedding batch size.")
    embed_generate.add_argument("--max-length", type=int, default=512, help="Maximum tokens per chunk at encode time.")
    embed_generate.add_argument("--out", type=Path, default=Path("data/conceptosphere-embed.json"))
    embed_generate.add_argument("--limit", type=int, default=0, help="Process only first N documents for smoke tests.")
    embed_generate.set_defaults(func=_conceptosphere_embed_generate)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pancratius",
        description=(
            "The Pancratius library door — change the corpus and build inputs "
            "(docs/tooling.md). Verification lives in `npm run audit`."
        ),
    )
    parser.set_defaults(func=_require_subcommand(parser))
    sub = parser.add_subparsers(dest="group", metavar="<group>")
    _add_work_group(sub)
    _add_project_group(sub)
    _add_video_group(sub)
    _add_downloads_group(sub)
    _add_docx_group(sub)
    _add_conceptosphere_group(sub)
    return parser


def _configure_logging() -> None:
    """Attach a stderr handler to the `pancratius` package logger. Library
    modules emit through `logging.getLogger(__name__)`; the CLI owns the sink.
    `propagate = False` keeps third-party loggers (e.g. googleapiclient) from
    routing through this handler."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    pkg_logger = logging.getLogger("pancratius")
    pkg_logger.handlers.clear()
    pkg_logger.addHandler(handler)
    pkg_logger.setLevel(logging.INFO)
    pkg_logger.propagate = False


def main(argv: list[str] | None = None) -> int:
    """Parse and dispatch. Every parser level carries a `func` default, so a bare
    group/noun prints help + returns 2 while a leaf verb returns its handler's
    code. argparse raises SystemExit(2) for genuine usage errors."""
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging()
    handler: Callable[[argparse.Namespace], int] = args.func
    return handler(args)
