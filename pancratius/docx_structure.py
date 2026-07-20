# import-pure: compiler observations over one canonical DOCX source aggregate
"""Typed source-line observations at stable production compiler seams.

Source coordinates live on IR leaves, so an observation is a leaf walk: each
source line is classified by the leaf block projected from that same canonical
source unit, with the enclosing container kind carried as a separate fact.
Policy — which (kind, enclosure) pairs are in scope for a task — belongs to
consumers, not to this module.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal, assert_never

from pancratius import docx_adapter as da
from pancratius import docx_source, ir
from pancratius.locales import Locale
from pancratius.passes.pipeline import (
    PER_ORDINAL_SEAM,
    POST_FOLD_SEAM,
    Context,
    LineationCorrections,
    run,
)


class CompilerBlockKind(StrEnum):
    """Closed vocabulary of shipping compiler block outcomes."""

    HEADING = "Heading"
    PARAGRAPH = "Paragraph"
    LINEATED = "LineatedBlock"
    SIGNATURE = "Signature"
    EPIGRAPH = "Epigraph"
    DIALOGUE_LABEL = "DialogueLabel"
    THEMATIC = "ThematicBreak"
    QUOTE = "BlockQuote"
    LIST = "ListBlock"
    CODE = "CodeBlock"
    TABLE = "Table"
    IMAGE = "ImageBlock"
    UNKNOWN = "UnknownBlock"


_BODY_KINDS = frozenset({CompilerBlockKind.PARAGRAPH, CompilerBlockKind.LINEATED})

# The only containers the claim walk descends into — the only kinds an
# enclosure can carry, so the scope policy can match them exhaustively.
type EnclosureKind = Literal[CompilerBlockKind.QUOTE, CompilerBlockKind.LIST]


@dataclass(frozen=True, slots=True)
class BlockClaim:
    """One leaf block's claim on one or more source ordinals. `enclosure` is the
    nearest container the leaf renders inside, when nested. `register` is the
    independent display axis carried by a lineated leaf."""

    kind: CompilerBlockKind
    span: ir.SourceSpan
    path: tuple[int, ...]
    enclosure: EnclosureKind | None = None
    register: ir.Register | None = None

    @property
    def is_verse(self) -> bool:
        return self.kind is CompilerBlockKind.LINEATED and self.register is ir.Register.VERSE


@dataclass(frozen=True, slots=True)
class SourceBlockHit:
    claims: tuple[BlockClaim, ...]

    @property
    def kinds(self) -> frozenset[CompilerBlockKind]:
        return frozenset(claim.kind for claim in self.claims)

    @property
    def span(self) -> ir.SourceSpan:
        merged = ir.merge_source_spans(claim.span for claim in self.claims)
        assert merged is not None
        return merged


@dataclass(frozen=True, slots=True)
class _LeafBlock:
    block: ir.Block
    path: tuple[int, ...]
    enclosure: EnclosureKind | None


def _leaf_blocks(
    blocks: Iterable[ir.Block],
    *,
    path: tuple[int, ...] = (),
    enclosure: EnclosureKind | None = None,
) -> Iterator[_LeafBlock]:
    """Walk renderable leaves once; claim projections choose their identity grain."""
    for index, block in enumerate(blocks):
        block_path = (*path, index)
        match block:
            case ir.QuoteBlock(blocks=members) if members:
                yield from _leaf_blocks(
                    members,
                    path=block_path,
                    enclosure=CompilerBlockKind.QUOTE,
                )
            case ir.ListBlock(items=items) if items:
                for item_index, item in enumerate(items):
                    yield from _leaf_blocks(
                        item,
                        path=(*block_path, item_index),
                        enclosure=CompilerBlockKind.LIST,
                    )
            case _:
                yield _LeafBlock(block, block_path, enclosure)


def compiler_block_kind(block: ir.Block) -> CompilerBlockKind:
    match block:
        case ir.Heading():
            return CompilerBlockKind.HEADING
        case ir.Paragraph():
            return CompilerBlockKind.PARAGRAPH
        case ir.LineatedBlock():
            return CompilerBlockKind.LINEATED
        case ir.Signature():
            return CompilerBlockKind.SIGNATURE
        case ir.Epigraph():
            return CompilerBlockKind.EPIGRAPH
        case ir.DialogueLabel():
            return CompilerBlockKind.DIALOGUE_LABEL
        case ir.ThematicBreak():
            return CompilerBlockKind.THEMATIC
        case ir.QuoteBlock():
            return CompilerBlockKind.QUOTE
        case ir.ListBlock():
            return CompilerBlockKind.LIST
        case ir.CodeBlock():
            return CompilerBlockKind.CODE
        case ir.Table():
            return CompilerBlockKind.TABLE
        case ir.ImageBlock():
            return CompilerBlockKind.IMAGE
        case ir.UnknownBlock():
            return CompilerBlockKind.UNKNOWN
    assert_never(block)


def source_block_hits(
    blocks: Iterable[ir.Block],
    eligible_ordinals: Iterable[docx_source.SourceOrdinal],
) -> dict[docx_source.SourceOrdinal, SourceBlockHit]:
    """Every leaf block's claim on each eligible source ordinal."""
    eligible = frozenset(eligible_ordinals)
    claims: dict[docx_source.SourceOrdinal, list[BlockClaim]] = {}
    for leaf in _leaf_blocks(blocks):
        if (span := leaf.block.source_span) is None:
            continue
        claim = BlockClaim(
            compiler_block_kind(leaf.block),
            span,
            leaf.path,
            leaf.enclosure,
            register=(
                leaf.block.register if isinstance(leaf.block, ir.LineatedBlock) else None
            ),
        )
        for ordinal in range(span.start, span.end + 1):
            if ordinal in eligible:
                claims.setdefault(ordinal, []).append(claim)
    return {ordinal: SourceBlockHit(tuple(hits)) for ordinal, hits in claims.items()}


def source_line_hits(
    blocks: Iterable[ir.Block],
) -> dict[docx_source.SourceLineCoordinate, SourceBlockHit]:
    """Every leaf block's claim on each exact natural-line coordinate."""
    claims: dict[docx_source.SourceLineCoordinate, list[BlockClaim]] = {}
    for leaf in _leaf_blocks(blocks):
        kind = compiler_block_kind(leaf.block)
        match leaf.block:
            case ir.Paragraph() if leaf.block.empty:
                # Empty source rows retain identity but render no exact source
                # line. The ordinal ledger keeps them; this projection does not.
                continue
            case ir.LineatedBlock(stanzas=stanzas):
                for stanza_index, stanza in enumerate(stanzas):
                    for line_index, line in enumerate(stanza):
                        if line.span is None:
                            continue
                        claim = BlockClaim(
                            kind,
                            line.span,
                            (*leaf.path, stanza_index, line_index),
                            leaf.enclosure,
                            register=leaf.block.register,
                        )
                        for coordinate in line.span.lines:
                            claims.setdefault(coordinate, []).append(claim)
                continue
            case _:
                pass

        if (span := leaf.block.source_span) is None:
            continue
        claim = BlockClaim(
            kind,
            span,
            leaf.path,
            leaf.enclosure,
            register=(
                leaf.block.register if isinstance(leaf.block, ir.LineatedBlock) else None
            ),
        )
        for coordinate in span.lines:
            claims.setdefault(coordinate, []).append(claim)
    return {
        coordinate: SourceBlockHit(tuple(hits))
        for coordinate, hits in claims.items()
    }


@dataclass(frozen=True, slots=True)
class SourceLineObservation:
    """One source line's compiler classification: the leaf kind that
    renders it, plus the NEAREST enclosing container kind when nested (a list
    item stays a list item even inside a quote). Reduction over multiple claims
    is body-first — a line claimed by e.g. DialogueLabel + Paragraph is
    classified by its body leaf."""

    kind: CompilerBlockKind
    enclosure: EnclosureKind | None = None

    @property
    def is_body_kind(self) -> bool:
        return self.kind in _BODY_KINDS


def _observe(hit: SourceBlockHit) -> SourceLineObservation:
    """Reduce one source line's claim set to one observation, order-independently.

    Body-first: DialogueLabel + Paragraph is the one named valid decomposition,
    so context-kind co-claims never taint a body line. Contested body kinds
    (both PARAGRAPH and LINEATED claim — never observed) report the flowing
    kind; the contest itself is typed at the fold seam as `FoldConflict` and
    lands in the census as uncovered. Disagreeing enclosures reduce to
    unanimous-or-None — None keeps the line in scope rather than letting
    walk order decide its exclusion. Without a body claim, identical structural
    claims collapse and genuinely conflicting ones fail loud — never a
    walk-order pick."""
    body = [claim for claim in hit.claims if claim.kind in _BODY_KINDS]
    if not body:
        distinct = {(claim.kind, claim.enclosure) for claim in hit.claims}
        if len(distinct) > 1:
            raise ValueError(
                f"conflicting structural claims {sorted((k.value, e and e.value) for k, e in distinct)} "
                f"on one source line — a compiler invariant broke"
            )
        kind, enclosure = next(iter(distinct))
        return SourceLineObservation(kind=kind, enclosure=enclosure)
    kinds = {claim.kind for claim in body}
    enclosures = {claim.enclosure for claim in body}
    return SourceLineObservation(
        kind=CompilerBlockKind.PARAGRAPH
        if CompilerBlockKind.PARAGRAPH in kinds
        else CompilerBlockKind.LINEATED,
        enclosure=next(iter(enclosures)) if len(enclosures) == 1 else None,
    )


@dataclass(frozen=True, slots=True)
class StructuralObservation:
    """One source document's total, locale-specific source-line classification.

    Two distinct absences, never conflated: `removed` — a named compiler pass
    deliberately took the paragraph out (ToC, rights boilerplate; legitimate
    structure); `lost` — no block claimed it even at the adapter, so its
    IDENTITY leaked out of the import (the text itself may or may not ship —
    an importer defect the research census keeps visible)."""

    source: docx_source.DocxSourceDocument
    lang: Locale
    entries: tuple[tuple[docx_source.SourceLineCoordinate, SourceLineObservation], ...]
    removed: frozenset[docx_source.SourceOrdinal] = frozenset()
    lost: frozenset[docx_source.SourceOrdinal] = frozenset()

    def __post_init__(self) -> None:
        if self.removed & self.lost:
            raise ValueError("removed and lost paragraph identities overlap")
        absent = self.removed | self.lost
        if not absent <= self.source.content_ordinals:
            extra = sorted(absent - self.source.content_ordinals)
            raise ValueError(
                f"structural observation has unknown absent paragraphs {extra[:5]}"
            )
        coordinates = [coordinate for coordinate, _ in self.entries]
        if len(coordinates) != len(set(coordinates)):
            raise ValueError("structural observation contains duplicate source lines")
        expected_lines = {
            coordinate
            for paragraph in self.source.paragraphs
            if (
                int(paragraph.ordinal) in self.source.content_ordinals
                and int(paragraph.ordinal) not in absent
            )
            for coordinate in paragraph.line_coordinates
        }
        if set(coordinates) != expected_lines:
            missing = sorted(expected_lines - set(coordinates))
            extra = sorted(set(coordinates) - expected_lines)
            raise ValueError(
                "structural line observation is not total "
                f"(missing={missing[:5]}, extra={extra[:5]})"
            )

    @property
    def by_line(self) -> dict[docx_source.SourceLineCoordinate, SourceLineObservation]:
        return dict(self.entries)


def observe_structure(
    source: docx_source.DocxSourceDocument,
    *,
    lang: Locale,
) -> StructuralObservation:
    """Classify every natural source line at the pre-lineation structural seam."""
    with tempfile.TemporaryDirectory(prefix="docx-structure-") as directory:
        adapted = da.adapt(source, Path(directory), [])
        at_adapter = frozenset(source_block_hits(adapted.blocks, source.content_ordinals))
        compiled = run(adapted, Context(lang=lang), until=PER_ORDINAL_SEAM)

    line_hits = source_line_hits(compiled.blocks)
    rendered_ordinals = {int(coordinate.ordinal) for coordinate in line_hits}
    absent = source.content_ordinals - rendered_ordinals
    return StructuralObservation(
        source,
        lang,
        tuple(
            (coordinate, _observe(hit))
            for coordinate, hit in sorted(line_hits.items())
        ),
        removed=frozenset(absent & at_adapter),
        lost=frozenset(absent - at_adapter),
    )


class FoldDisposition(StrEnum):
    """Whether one source line remains flowing or enters a lineated block."""

    FLOWING = "flowing"
    FOLDED = "folded"


@dataclass(frozen=True, slots=True)
class FoldDecision:
    """A unanimous flow-bearing claim, retaining every compiler claim for audit."""

    disposition: FoldDisposition
    claims: tuple[BlockClaim, ...]


@dataclass(frozen=True, slots=True)
class FoldConflict:
    """The compiler produced both flowing and folded claims for one source line."""

    claims: tuple[BlockClaim, ...]


type FoldResult = FoldDecision | FoldConflict


def _fold_result(hit: SourceBlockHit) -> FoldResult | None:
    dispositions = {
        FoldDisposition.FOLDED
        if claim.kind is CompilerBlockKind.LINEATED
        else FoldDisposition.FLOWING
        for claim in hit.claims
        if claim.kind in _BODY_KINDS
    }
    if not dispositions:
        return None
    if len(dispositions) == 1:
        return FoldDecision(next(iter(dispositions)), hit.claims)
    return FoldConflict(hit.claims)


@dataclass(frozen=True, slots=True)
class FoldObservation:
    """Exact source-line fates at the post-fold compiler seam."""

    entries: tuple[tuple[docx_source.SourceLineCoordinate, FoldResult], ...]

    @property
    def by_line(self) -> dict[docx_source.SourceLineCoordinate, FoldResult]:
        return dict(self.entries)

    @property
    def decisions(self) -> tuple[tuple[docx_source.SourceLineCoordinate, FoldDecision], ...]:
        return tuple(
            (coordinate, result)
            for coordinate, result in self.entries
            if isinstance(result, FoldDecision)
        )


def observe_fold(
    source: docx_source.DocxSourceDocument,
    *,
    lang: Locale,
    apply_overrides: bool = True,
) -> FoldObservation:
    """Project compiler-owned Q1 disposition before any Q2 register transformation."""
    from pancratius.lineation_overrides import load_overrides

    with tempfile.TemporaryDirectory(prefix="docx-fold-") as directory:
        doc = da.adapt(source, Path(directory), [])
        doc = run(doc, Context(
            lang=lang,
            lineation=LineationCorrections(load_overrides(source) if apply_overrides else {}),
        ), until=POST_FOLD_SEAM)

    hits = source_line_hits(doc.blocks)
    entries: list[tuple[docx_source.SourceLineCoordinate, FoldResult]] = []
    for coordinate, hit in sorted(hits.items()):
        if result := _fold_result(hit):
            entries.append((coordinate, result))
    return FoldObservation(tuple(entries))


def fold_decisions(
    source: docx_source.DocxSourceDocument,
    *,
    lang: Locale,
    apply_overrides: bool = True,
) -> dict[docx_source.SourceLineCoordinate, bool]:
    """Boolean projection of the compiler-owned fold observation: source line → folded."""
    return {
        coordinate: decision.disposition is FoldDisposition.FOLDED
        for coordinate, decision in observe_fold(
            source,
            lang=lang,
            apply_overrides=apply_overrides,
        ).decisions
    }
