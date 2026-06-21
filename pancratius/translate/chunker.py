"""Group translatable units into bounded, structure-respecting chunks.

The whole book travels as cached reference, so a chunk only has to bound how much
*new* text the model generates per call. Two rules shape the cut:

- a maximal run of lineated/verse/scripture units (a stanza or a quoted passage)
  is one indivisible *atom* — never split a poem across two requests;
- atoms are packed greedily up to ``chunk_source_tokens``; an atom larger than the
  budget stands alone.

Chunks stay contiguous and in document order so each one's local neighbourhood is
coherent even before the global reference is consulted.
"""

from __future__ import annotations

from dataclasses import dataclass

from pancratius.translate.config import TranslateConfig
from pancratius.translate.document import Document, TextUnit, UnitId, UnitKind

# Units whose consecutive runs form an indivisible block.
_BLOCK_KINDS = frozenset({UnitKind.LINEATED, UnitKind.VERSE, UnitKind.SCRIPTURE})


@dataclass(frozen=True, slots=True)
class Chunk:
    """A contiguous slice of units translated in one model call."""

    index: int
    unit_ids: tuple[UnitId, ...]
    source_tokens: int


def _atoms(units: tuple[TextUnit, ...]) -> list[list[TextUnit]]:
    atoms: list[list[TextUnit]] = []
    run: list[TextUnit] = []
    for unit in units:
        if unit.kind in _BLOCK_KINDS:
            run.append(unit)
            continue
        if run:
            atoms.append(run)
            run = []
        atoms.append([unit])
    if run:
        atoms.append(run)
    return atoms


def plan_chunks(document: Document, config: TranslateConfig) -> list[Chunk]:
    budget = config.chunk_source_tokens
    chunks: list[Chunk] = []
    current: list[TextUnit] = []
    current_tokens = 0

    def flush() -> None:
        nonlocal current, current_tokens
        if not current:
            return
        chunks.append(
            Chunk(
                index=len(chunks),
                unit_ids=tuple(unit.id for unit in current),
                source_tokens=current_tokens,
            )
        )
        current = []
        current_tokens = 0

    max_units = config.chunk_max_units
    for atom in _atoms(document.units):
        # An atom is normally indivisible, but a pathologically large block run
        # (e.g. a 2000-line poem) would build one chunk whose JSON reply truncates
        # — no model returns that many units at once. Split such an atom at the unit
        # cap; a normal-sized atom passes through whole as a single piece.
        pieces = [atom[i : i + max_units] for i in range(0, len(atom), max_units)]
        for piece in pieces:
            piece_tokens = config.estimate_source_tokens(sum(len(u.source) for u in piece))
            over_tokens = current_tokens + piece_tokens > budget
            over_units = len(current) + len(piece) > max_units
            if current and (over_tokens or over_units):
                flush()
            current.extend(piece)
            current_tokens += piece_tokens
    flush()
    return chunks
