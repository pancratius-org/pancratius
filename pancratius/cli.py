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
    from pancratius.import_docx import ImportRequest


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
