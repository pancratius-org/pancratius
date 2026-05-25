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

The owning logic lives under ``scripts/`` (the same modules the ``npm`` prebuild
steps run). This door reproduces those scripts' ``sys.path`` bootstrap so
``from lib.* import …`` and ``import <owner>`` resolve, then dispatches to one entry
per owner. Owner modules are imported **lazily inside each handler** so the light
core never imports a heavy (graph/embed) stack just to print ``--help``.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

# --- bootstrap ----------------------------------------------------------------
# Make scripts/ importable, mirroring each scripts/<owner>.py's own
# `sys.path.insert(0, SCRIPT_DIR)`. uv installs the project root editable, so
# __file__ resolves into the source tree and parents[1] is the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _REPO_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


# --- output contract ----------------------------------------------------------
def _ok(owner_rc: int) -> int:
    """Map an owner entry's return code onto the door's contract: 0 stays 0; any
    nonzero collapses to 1 (failure). Exit 2 is reserved for argparse usage errors,
    so an owner's own nonzero code never masquerades as a usage error."""
    return 0 if owner_rc == 0 else 1


def _missing_extra(extra: str, exc: ImportError) -> int:
    """A heavy verb was invoked without its optional-dependency stack: print the
    install hint to stderr and fail (exit 1) instead of dumping a traceback
    (docs/tooling.md "Dependency model"). The light core never imports a heavy
    module, so this is the only place a missing extra surfaces."""
    print(f"error: the '{extra}' extra is not installed ({exc}).", file=sys.stderr)
    print(f"run: uv sync --extra {extra}", file=sys.stderr)
    return 1


def _require_subcommand(parser: argparse.ArgumentParser) -> Callable[[argparse.Namespace], int]:
    """A `func` default for every non-leaf parser: running a bare group/noun with no
    verb prints THAT level's help to stderr and signals a usage error (exit 2),
    instead of relying on argparse's brittle required-subparser handling."""

    def handler(_args: argparse.Namespace) -> int:
        parser.print_help(sys.stderr)
        return 2

    return handler


# --- handlers (work group) ----------------------------------------------------
def _work_import(args: argparse.Namespace) -> int:
    """`work import <docx> --kind book|poem` — import a corpus work bundle.

    Builds an `ImportRequest` from the parsed flags (the same `add_import_arguments`
    declaration and `request_from_namespace` adapter the standalone CLI uses) and
    dispatches to `import_work`, which is silent and returns the writer's report (or
    raises on bad input / an unresolvable target). The door owns all output: it
    prints the report (the `--dry-run` review gate) and maps a write refusal to a
    failure exit."""
    import import_docx

    if shutil.which("pandoc") is None:
        print("error: pandoc not found on PATH; install with `brew install pandoc`.", file=sys.stderr)
        return 1
    request = import_docx.request_from_namespace(args)
    try:
        report = import_docx.import_work(request)
    except import_docx.ImportError as exc:
        # `import_docx.ImportError` is the importer's OWN input/target error class
        # (it shadows the builtin). Accessed qualified, so the door's namespace keeps
        # the real builtin ImportError that the extras gate catches. Bad input / an
        # unresolvable target is a usage error (exit 2), matching the standalone CLI.
        print(f"error: {exc}", file=sys.stderr)
        return 2
    import_docx.print_report(report, dry_run=request.dry_run)
    return 1 if report.refused else 0


# --- handlers (downloads / docx groups) ---------------------------------------
def _downloads_render(args: argparse.Namespace) -> int:
    """`downloads render [--book N]` — render local PDF/EPUB/DOCX release artifacts.
    Pass-through to the render owner, which prints its own progress/summary."""
    from render_downloads import render

    return _ok(
        render(
            book=args.book,
            poem=args.poem,
            lang=args.lang,
            skip_pdf=args.skip_pdf,
            skip_epub=args.skip_epub,
            docx=args.docx,
            force=args.force,
        )
    )


def _docx_optimize(args: argparse.Namespace) -> int:
    """`docx optimize [paths…]` — in-place source DOCX cleanup. Pass-through to the
    optimize owner (which has its own `--dry-run`)."""
    from docx_optimize import optimize

    return _ok(
        optimize(
            paths=[Path(p) for p in args.paths],
            force=args.force,
            verbose=args.verbose,
            dry_run=args.dry_run,
        )
    )


# --- handlers (data group) ----------------------------------------------------
def _data_slug_map_refresh(_args: argparse.Namespace) -> int:
    """`data slug-map refresh` — regenerate the sitemap slug-map. Thin alias over
    the one owner the npm `prebuild:slug-map` step also runs."""
    import build_slug_map

    return _ok(build_slug_map.generate_slug_map())


def _data_bulk_refresh(_args: argparse.Namespace) -> int:
    """`data bulk refresh` — rebuild all-md.zip. The one cross-language verb: the
    bulk-archive owner is Node, so the door shells to it (same owner as the npm
    `prebuild:bulk-archives` step)."""
    script = _SCRIPTS / "build_bulk_archives.ts"
    proc = subprocess.run(["node", "--experimental-strip-types", str(script)])
    return _ok(proc.returncode)


def _data_graph_generate(args: argparse.Namespace) -> int:
    """`data graph generate [--only concepts|books]` — regenerate the concept/book
    graph projections into data/ (heavy — needs the `graph` extra). Lazy-imports the
    owner so the light core never pulls networkx/igraph/leidenalg. Distinct from the
    CI-safe npm `prebuild:graph-payloads`, which only COPIES data/→public/data/."""
    try:
        from conceptosphere import generate_graph
    except ImportError as exc:
        return _missing_extra("graph", exc)
    return _ok(generate_graph(only=args.only))


def _data_embed_generate(_args: argparse.Namespace) -> int:
    """`data embed generate` — regenerate semantic embeddings into data/ (heavy —
    needs the `embed` MLX extra). Lazy-imports the owner so the light core stays light."""
    try:
        from conceptosphere_embed import generate_embeddings
    except ImportError as exc:
        return _missing_extra("embed", exc)
    return _ok(generate_embeddings())


# --- parser assembly ----------------------------------------------------------
# Each group is built by its own function so later phases add groups/verbs locally.
def _add_work_group(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    import import_docx  # light (no ML); owns the import flag declaration

    work = sub.add_parser("work", help="Import corpus works (a book or a poem).")
    work.set_defaults(func=_require_subcommand(work))
    work_sub = work.add_subparsers(dest="noun", metavar="<noun>")
    work_import = work_sub.add_parser(
        "import", help="Import one DOCX into a work bundle (--kind book|poem; --into to add a translation)."
    )
    import_docx.add_import_arguments(work_import)  # door does not redeclare --kind (PAN017-owned)
    work_import.set_defaults(func=_work_import)


def _add_downloads_group(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    from lib.locales import LOCALES

    downloads = sub.add_parser("downloads", help="Render local release artifacts (PDF/EPUB/DOCX).")
    downloads.set_defaults(func=_require_subcommand(downloads))
    downloads_sub = downloads.add_subparsers(dest="noun", metavar="<noun>")
    render = downloads_sub.add_parser("render", help="Render release artifacts (never CI).")
    render.add_argument("--book", type=int, help="Render only this book number.")
    render.add_argument("--poem", type=int, help="Render only this poem number.")
    render.add_argument("--lang", choices=tuple(LOCALES), help="Restrict to one language.")
    render.add_argument("--skip-pdf", action="store_true", help="Skip PDF rendering.")
    render.add_argument("--skip-epub", action="store_true", help="Skip EPUB rendering.")
    render.add_argument("--docx", action="store_true", help="Also render merged DOCX for multi-part works.")
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


def _add_data_group(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    data = sub.add_parser("data", help="Generate corpus data products.")
    data.set_defaults(func=_require_subcommand(data))
    data_sub = data.add_subparsers(dest="noun", metavar="<noun>")

    slug_map = data_sub.add_parser("slug-map", help="Sitemap slug-map.")
    slug_map.set_defaults(func=_require_subcommand(slug_map))
    slug_map_sub = slug_map.add_subparsers(dest="verb", metavar="<verb>")
    sm_refresh = slug_map_sub.add_parser(
        "refresh", help="Regenerate the slug-map (same owner as prebuild:slug-map)."
    )
    sm_refresh.set_defaults(func=_data_slug_map_refresh)

    bulk = data_sub.add_parser("bulk", help="Bulk Markdown archive.")
    bulk.set_defaults(func=_require_subcommand(bulk))
    bulk_sub = bulk.add_subparsers(dest="verb", metavar="<verb>")
    bulk_refresh = bulk_sub.add_parser(
        "refresh", help="Rebuild all-md.zip (same owner as prebuild:bulk-archives)."
    )
    bulk_refresh.set_defaults(func=_data_bulk_refresh)

    graph = data_sub.add_parser("graph", help="Concept/book graphs (heavy — uv sync --extra graph).")
    graph.set_defaults(func=_require_subcommand(graph))
    graph_sub = graph.add_subparsers(dest="verb", metavar="<verb>")
    graph_generate = graph_sub.add_parser(
        "generate", help="Regenerate BOTH graph projections into data/ (--only for one)."
    )
    graph_generate.add_argument(
        "--only", choices=("concepts", "books"), default=None,
        help="Regenerate only this projection (default: both, off one corpus scan).",
    )
    graph_generate.set_defaults(func=_data_graph_generate)

    embed = data_sub.add_parser("embed", help="Semantic embeddings (heavy — uv sync --extra embed).")
    embed.set_defaults(func=_require_subcommand(embed))
    embed_sub = embed.add_subparsers(dest="verb", metavar="<verb>")
    embed_generate = embed_sub.add_parser("generate", help="Regenerate embeddings into data/.")
    embed_generate.set_defaults(func=_data_embed_generate)


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
    _add_downloads_group(sub)
    _add_docx_group(sub)
    _add_data_group(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse and dispatch. Every parser level carries a `func` default, so a bare
    group/noun prints help + returns 2 while a leaf verb returns its handler's
    code. argparse raises SystemExit(2) for genuine usage errors."""
    parser = build_parser()
    args = parser.parse_args(argv)
    handler: Callable[[argparse.Namespace], int] = args.func
    return handler(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
