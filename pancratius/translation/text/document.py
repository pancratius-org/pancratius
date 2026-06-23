"""Structure-preserving segmentation of generated corpus Markdown.

The body of every ``ru.md`` is *generated* Markdown with one uniform shape
(docs/content-model.md): prose paragraphs are single physical lines, lineated
lines end in two trailing spaces, and set-apart runs live inside raw wrappers
(``<div class="lineated">``, ``<div class="lineated verse">``,
``<blockquote class="scripture">``). To translate without ever corrupting that
structure we split the body into two kinds of piece:

- ``Verbatim`` — scaffolding emitted byte-for-byte: blank lines, wrapper tags,
  thematic breaks, image links, list markers, heading hashes, the trailing
  ``␣␣`` hard breaks that carry lineation.
- ``Slot`` — a span of translatable text, addressed by a stable ``UnitId``.

The translation model only ever sees and returns ``Slot`` text; it cannot touch
a single scaffolding byte. So lineation (the two-space breaks the PAN006B audit
guards) and every wrapper survive **by construction**, not by post-hoc repair.

The load-bearing invariant, exercised across the whole corpus in the tests:

    parse_document(body).render({}) == body            # exact, byte-for-byte

``render`` with a real ``UnitId -> translated text`` mapping is the only place a
translation enters the document, so the reconstruction is structurally identical
to the source no matter what the model returns.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

type UnitId = str
# A finished translation: every unit id mapped to its English text. Threaded
# through the schema parser, the draft/revise loop and the checks.
type Translations = Mapping[UnitId, str]


class UnitKind(StrEnum):
    """Why a translatable span exists — drives prompt phrasing and the
    deterministic checks (e.g. SCRIPTURE wants canonical-faithful wording, a
    HEADING is a title fragment, a VERSE line keeps its line identity)."""

    PROSE = "prose"
    LINEATED = "lineated"
    VERSE = "verse"
    SCRIPTURE = "scripture"
    HEADING = "heading"
    LIST_ITEM = "list_item"
    IMAGE_ALT = "image_alt"


@dataclass(frozen=True, slots=True)
class TextUnit:
    """One translatable span. ``source`` keeps inline Markdown (``**bold**``,
    ``*em*``, links, guillemets) — the model preserves that markup and renders
    English typography; the surrounding structure is never part of it."""

    id: UnitId
    kind: UnitKind
    source: str


@dataclass(frozen=True, slots=True)
class Verbatim:
    """Scaffolding emitted unchanged."""

    text: str


@dataclass(frozen=True, slots=True)
class Slot:
    """A hole filled by ``unit_id``'s translation (or its source under identity)."""

    unit_id: UnitId


type Piece = Verbatim | Slot


@dataclass(frozen=True, slots=True)
class Document:
    """An ordered piece list plus the units those slots address. Reconstruction
    is ``"".join`` over the pieces, so it is exact for free."""

    pieces: tuple[Piece, ...]
    units: tuple[TextUnit, ...]

    def unit_index(self) -> dict[UnitId, TextUnit]:
        return {unit.id: unit for unit in self.units}

    def render(self, translations: Translations) -> str:
        index = self.unit_index()
        out: list[str] = []
        for piece in self.pieces:
            match piece:
                case Verbatim(text):
                    out.append(text)
                case Slot(unit_id):
                    out.append(translations.get(unit_id, index[unit_id].source))
        return "".join(out)

    def source_text(self) -> str:
        """The original body, reconstructed (the round-trip identity target)."""
        return self.render({})


# --- line classification ------------------------------------------------------
# Register-bearing wrappers. Class is matched loosely (substring) because the
# importer may add register modifiers (``lineated verse``).
_WRAPPER_OPEN_RE = re.compile(r"^\s*<(div|blockquote)\b[^>]*>\s*$")
_WRAPPER_CLOSE_RE = re.compile(r"^\s*</(div|blockquote)>\s*$")
_HEADING_RE = re.compile(r"^(\s*#{1,6}\s+)(.*)$")
_THEMATIC_BREAK_RE = re.compile(r"^\s*\*(\s?\*){2,}\s*$|^\s*\*\*\*\s*$")
# An image-only line: the ``![`` … ``](path)`` is scaffolding (the path is never
# translated); only the alt text between the brackets is a translatable Slot.
_IMAGE_LINE_RE = re.compile(r"^(\s*!\[)([^\]]*)(\]\([^)]*\)\s*)$")
_WORD_RE = re.compile(r"\w", re.UNICODE)
_BARE_QUOTE_RE = re.compile(r"^\s*>\s*$")
_PARAGRAPH_TAG_RE = re.compile(r"^(\s*<p\b[^>]*>)(.*?)(</p>\s*)$")
# Leading scaffolding on a text line: blockquote markers, then a list marker,
# captured as a prefix so only the text after it becomes a Slot.
_LINE_PREFIX_RE = re.compile(r"^(\s*(?:>\s?)*(?:(?:[-*+]|\d+[.)])\s+)?)(.*)$")


def _register_of(open_tag: str) -> UnitKind:
    if "scripture" in open_tag:
        return UnitKind.SCRIPTURE
    if "verse" in open_tag:
        return UnitKind.VERSE
    if "lineated" in open_tag:
        return UnitKind.LINEATED
    return UnitKind.PROSE


def _is_structural(line: str) -> bool:
    stripped = line.strip()
    return bool(
        _WRAPPER_OPEN_RE.match(line)
        or _WRAPPER_CLOSE_RE.match(line)
        or _THEMATIC_BREAK_RE.match(line)
        or (stripped.startswith("<!--") and stripped.endswith("-->"))
        or _BARE_QUOTE_RE.match(line)
    )


@dataclass(frozen=True, slots=True)
class _Decomposed:
    """A text line split into kept prefix, translatable text, kept suffix."""

    prefix: str
    text: str
    suffix: str
    has_list_marker: bool


def _decompose_text_line(line: str) -> _Decomposed | None:
    """Split a non-structural line into ``prefix + text + suffix`` such that the
    three concatenate back to ``line`` exactly. Returns ``None`` when nothing
    translatable remains (the line is then kept verbatim)."""
    image = _IMAGE_LINE_RE.match(line)
    if image:
        prefix, alt, suffix = image.group(1), image.group(2), image.group(3)
        if not _WORD_RE.search(alt):
            return None
        lead_ws = alt[: len(alt) - len(alt.lstrip())]
        trail_ws = alt[len(alt.rstrip()) :]
        return _Decomposed(prefix + lead_ws, alt.strip(), trail_ws + suffix, has_list_marker=False)

    para = _PARAGRAPH_TAG_RE.match(line)
    if para:
        prefix, inner, suffix = para.group(1), para.group(2), para.group(3)
        if not inner.strip():
            return None
        lead_ws = inner[: len(inner) - len(inner.lstrip())]
        trail_ws = inner[len(inner.rstrip()) :]
        return _Decomposed(prefix + lead_ws, inner.strip(), trail_ws + suffix, has_list_marker=False)

    heading = _HEADING_RE.match(line)
    if heading:
        prefix, rest = heading.group(1), heading.group(2)
        trail_ws = rest[len(rest.rstrip()) :]
        text = rest.rstrip()
        if not text:
            return None
        return _Decomposed(prefix, text, trail_ws, has_list_marker=False)

    prefix_match = _LINE_PREFIX_RE.match(line)
    # _LINE_PREFIX_RE always matches; the assert documents that for the reader.
    assert prefix_match is not None
    prefix, rest = prefix_match.group(1), prefix_match.group(2)
    if not rest.strip():
        return None
    trail_ws = rest[len(rest.rstrip()) :]
    text = rest.rstrip()
    has_list_marker = bool(re.search(r"(?:[-*+]|\d+[.)])\s+$", prefix))
    return _Decomposed(prefix, text, trail_ws, has_list_marker=has_list_marker)


def _unit_kind(register: UnitKind, decomposed: _Decomposed, line: str) -> UnitKind:
    if _IMAGE_LINE_RE.match(line):
        return UnitKind.IMAGE_ALT
    if _HEADING_RE.match(line):
        return UnitKind.HEADING
    if decomposed.has_list_marker:
        return UnitKind.LIST_ITEM
    if register is not UnitKind.PROSE:
        return register
    # A two-space hard break outside any wrapper still signals a lineated line.
    if line.endswith("  "):
        return UnitKind.LINEATED
    return UnitKind.PROSE


def parse_document(body: str) -> Document:
    """Segment a Markdown body into a :class:`Document`. The wrapper register is
    tracked on a stack so lines inside ``<div class="lineated verse">`` know they
    are verse without re-reading the open tag."""
    pieces: list[Piece] = []
    units: list[TextUnit] = []
    register_stack: list[UnitKind] = []
    lines = body.split("\n")
    for index, line in enumerate(lines):
        if index > 0:
            pieces.append(Verbatim("\n"))
        if not line.strip() or _is_structural(line):
            pieces.append(Verbatim(line))
            open_match = _WRAPPER_OPEN_RE.match(line)
            if open_match:
                register_stack.append(_register_of(line))
            elif _WRAPPER_CLOSE_RE.match(line) and register_stack:
                register_stack.pop()
            continue
        decomposed = _decompose_text_line(line)
        if decomposed is None:
            pieces.append(Verbatim(line))
            continue
        register = register_stack[-1] if register_stack else UnitKind.PROSE
        unit = TextUnit(
            id=f"u{len(units):04d}",
            kind=_unit_kind(register, decomposed, line),
            source=decomposed.text,
        )
        units.append(unit)
        pieces.append(Verbatim(decomposed.prefix))
        pieces.append(Slot(unit.id))
        pieces.append(Verbatim(decomposed.suffix))
    return Document(pieces=tuple(pieces), units=tuple(units))
