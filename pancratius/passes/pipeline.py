# import-pure: no filesystem mutation
"""The pass pipeline as data: `Context`, the per-kind pass tuples, named seams, `run`.

A seam is a position between named passes, expressed as `until=<name>`; external
observers (`docx_inspect`) run the pipeline up to a seam instead of flagging the
orchestrator.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from pancratius import ir
from pancratius.content_catalog import IndexHit
from pancratius.passes import endmatter, lineation, register, sanitize, scrub, structure

if TYPE_CHECKING:
    from pancratius.passes.register import RegisterModel

@dataclass(frozen=True)
class Context:
    """Pass parameters, injected by the composition point."""

    lang: str
    demote_levels: int = 1
    slug_lookup: Mapping[str, IndexHit] | None = None
    register_model: RegisterModel | None = None
    diagnostics: ir.DiagnosticSink = field(default_factory=list)


type PassFn = Callable[[ir.Document, Context], ir.Document]
type Pass = tuple[str, PassFn]  # the name is the seam vocabulary


def _blocks(fn: Callable[[list[ir.Block]], list[ir.Block]]) -> PassFn:
    """Adapt a block-list transform to the Document-level pass signature."""

    def pass_fn(doc: ir.Document, _ctx: Context) -> ir.Document:
        return replace(doc, blocks=fn(doc.blocks))

    return pass_fn


def _lift_bibliography(doc: ir.Document, ctx: Context) -> ir.Document:
    return endmatter.lift_bibliography(doc, ctx.slug_lookup, ctx.diagnostics)


def _demote_headings(doc: ir.Document, ctx: Context) -> ir.Document:
    return replace(doc, blocks=scrub.demote_headings(doc.blocks, ctx.demote_levels))


def _sanitize_urls(doc: ir.Document, ctx: Context) -> ir.Document:
    return sanitize.sanitize_urls(doc, ctx.diagnostics)


BOOK_PASSES: tuple[Pass, ...] = (
    ("drop_toc", _blocks(scrub.drop_toc)),
    ("scrub_rights", _blocks(scrub.scrub_rights)),
    ("scrub_ai_alt", _blocks(scrub.scrub_ai_alt)),
    ("lift_bibliography", _lift_bibliography),
    ("strip_endmatter", _blocks(endmatter.strip_endmatter_sections)),
    ("strip_bare_biblio_heading", _blocks(endmatter.strip_bare_bibliography_heading)),
    ("thematic_breaks", _blocks(scrub.thematic_breaks)),
    ("drop_empty_headings", _blocks(scrub.drop_empty_headings)),
    ("demote_headings", _demote_headings),
    ("strip_artifacts", _blocks(scrub.strip_formatting_artifacts)),
    ("fold_right_aligned", _blocks(structure.fold_right_aligned)),
    ("dialogue_labels", _blocks(structure.dialogue_labels)),
    ("fold_quote_registers", _blocks(register.fold_quote_registers)),  # ← PER_ORDINAL_SEAM
    ("fold_lineation", _blocks(lineation.fold_lineation)),
    ("assign_register", register.assign_register),
    ("sanitize_urls", _sanitize_urls),
)

POEM_PASSES: tuple[Pass, ...] = (
    ("drop_toc", _blocks(scrub.drop_toc)),
    ("scrub_ai_alt", _blocks(scrub.scrub_ai_alt)),
    ("thematic_breaks", _blocks(scrub.thematic_breaks)),
    ("strip_artifacts", _blocks(scrub.strip_formatting_artifacts)),
    ("sanitize_urls", _sanitize_urls),
)

# The first span-merging pass: before it, every body row is still an addressable
# `Paragraph` with its own ordinal — the seam the per-ordinal observers need.
PER_ORDINAL_SEAM = "fold_quote_registers"


def run(
    doc: ir.Document,
    ctx: Context,
    pipeline: tuple[Pass, ...] = BOOK_PASSES,
    *,
    until: str | None = None,
) -> ir.Document:
    """Run passes in order; `until` names the first pass NOT run.

    Unknown names are errors; names are asserted unique."""
    names = [name for name, _fn in pipeline]
    assert len(set(names)) == len(names), f"duplicate pass names: {names}"
    if until is not None and until not in names:
        raise ValueError(f"unknown pass name {until!r}; expected one of {names}")
    for name, fn in pipeline:
        if name == until:
            break
        doc = fn(doc, ctx)
    return doc
