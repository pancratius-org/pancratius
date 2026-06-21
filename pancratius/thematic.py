# import-pure: no filesystem mutation
"""Shared recognition for source divider paragraphs.

The canonical Markdown emitter always writes ``***``. Import-side readers may see
other visual divider glyphs from DOCX source; normalize them before Markdown
lowering so ``---`` / ``===`` can never become setext headings.
"""

from __future__ import annotations

import re

_SPACE_RE = re.compile(r"\s+")


def is_thematic_marker(text: str) -> bool:
    """True for a standalone visual divider paragraph.

    ``---`` and ``===`` are included even though only the former is CommonMark's
    thematic-break syntax: both are setext-heading underlines if emitted after a
    paragraph, so a DOCX paragraph containing only those glyphs is a divider
    signal that must normalize to the canonical ``***`` path.
    """

    stripped = text.strip()
    if stripped == r"\*\*\*":
        return True
    compact = _SPACE_RE.sub("", stripped)
    return (
        len(compact) >= 3
        and len(set(compact)) == 1
        and compact[0] in {"*", "-", "_", "="}
    )


def is_setext_underline_marker(text: str) -> bool:
    """True for a CommonMark setext heading underline line.

    Unlike thematic breaks, setext underlines are one or more ``-`` or ``=``
    characters with optional surrounding whitespace. A one- or two-character line
    is not a visual divider paragraph, but it can still promote the preceding
    paragraph to a heading.
    """

    stripped = text.strip()
    return bool(stripped) and len(set(stripped)) == 1 and stripped[0] in {"-", "="}
