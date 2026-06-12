# import-pure: no filesystem mutation
"""Compat facade: every pass name this module previously exposed is re-exported
from its `pancratius.passes` home."""

from __future__ import annotations

from pancratius import ir
from pancratius.ir.inlines import inline_lines, inline_plain
from pancratius.passes.endmatter import (
    _resolve_target,  # noqa: F401  re-export
    _SlugLookup,
    lift_bibliography,
    strip_bare_bibliography_heading,
    strip_endmatter_sections,
)
from pancratius.passes.lineation import VERSE_SHORT_LINE_MAX, is_lineated_line
from pancratius.passes.lineation import fold_lineation as lineated_blocks
from pancratius.passes.register import (
    fold_quote_registers as display_register_blocks,
)
from pancratius.passes.register import (
    is_dash_scaffold,
    is_equation_scaffold,
    is_verse_section_title,
    promote_verse_register,
)
from pancratius.passes.scrub import (
    AI_ALT_FRAGMENTS,
    RIGHTS_PATTERNS,
    demote_headings,
    drop_empty_headings,
    drop_toc,
    scrub_ai_alt,
    scrub_rights,
    strip_formatting_artifacts,
    thematic_breaks,
)
from pancratius.passes.structure import (
    _DIALOGUE_PREFIXES,  # noqa: F401  re-export
    dialogue_labels,
)
from pancratius.passes.structure import fold_right_aligned as structural_blocks

__all__ = (
    "AI_ALT_FRAGMENTS",
    "RIGHTS_PATTERNS",
    "VERSE_SHORT_LINE_MAX",
    "demote_headings",
    "dialogue_labels",
    "display_register_blocks",
    "drop_empty_headings",
    "drop_toc",
    "inline_lines",
    "inline_plain",
    "is_dash_scaffold",
    "is_equation_scaffold",
    "is_lineated_line",
    "is_verse_section_title",
    "lift_bibliography",
    "lineated_blocks",
    "normalize",
    "promote_verse_register",
    "scrub_ai_alt",
    "scrub_rights",
    "strip_bare_bibliography_heading",
    "strip_endmatter_sections",
    "strip_formatting_artifacts",
    "structural_blocks",
    "thematic_breaks",
    "verse_blocks",
)


def verse_blocks(blocks: list[ir.Block]) -> list[ir.Block]:
    """Q1 lineation, then Q2 verse-register promotion (rule policy)."""
    return promote_verse_register(lineated_blocks(blocks))


def normalize(
    doc: ir.Document,
    *,
    demote_levels: int = 1,
    slug_lookup: _SlugLookup | None = None,
) -> ir.Document:
    """Run the full book pass pipeline over `doc` (a shim over `passes.pipeline.run`).

    Callers that need a partial run observe at a named seam via
    `passes.pipeline.run(..., until=...)` instead.
    """
    # Function-level import: `passes.pipeline` wraps this module's pass functions.
    from pancratius.passes.pipeline import Context, run

    # No pass behind this shim reads `lang`; composition points that know the
    # language build their own Context and call `run` directly.
    return run(doc, Context(lang="", demote_levels=demote_levels, slug_lookup=slug_lookup))
