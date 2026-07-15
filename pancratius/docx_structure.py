# import-pure: compiler observations over one canonical DOCX source aggregate
"""Typed source-paragraph observations at stable production compiler seams."""

from __future__ import annotations

import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import assert_never

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


class ReviewReason(StrEnum):
    UNMAPPED_AT_ADAPTER = "unmapped_at_adapter"
    NON_UNIQUE_CLAIMS = "non_unique_claims"
    UNKNOWN_KIND = "unknown_kind"
    MERGED_BODY = "merged_body"


class ContextReason(StrEnum):
    STRUCTURAL_KIND = "structural_kind"
    DROPPED_BY_STRUCTURAL_PIPELINE = "dropped_by_structural_pipeline"


class ContextRole(StrEnum):
    CONTEXT = "context"
    HEADING = "heading"
    LIST = "list"
    THEMATIC = "thematic"


@dataclass(frozen=True, slots=True)
class BodyParagraph:
    pass


@dataclass(frozen=True, slots=True)
class ReviewParagraph:
    reason: ReviewReason


@dataclass(frozen=True, slots=True)
class ContextParagraph:
    reason: ContextReason
    role: ContextRole = ContextRole.CONTEXT


type ParagraphObservation = BodyParagraph | ReviewParagraph | ContextParagraph


@dataclass(frozen=True, slots=True)
class StructuralObservation:
    """One total, immutable classification of every readable source paragraph."""

    entries: tuple[tuple[docx_source.ParagraphOrdinal, ParagraphObservation], ...]

    def __post_init__(self) -> None:
        ordinals = [ordinal for ordinal, _ in self.entries]
        if len(ordinals) != len(set(ordinals)):
            raise ValueError("structural observation contains duplicate ordinals")

    @classmethod
    def complete(
        cls,
        source: docx_source.DocxSourceDocument,
        entries: tuple[tuple[docx_source.ParagraphOrdinal, ParagraphObservation], ...],
    ) -> StructuralObservation:
        observation = cls(entries)
        actual = {int(ordinal) for ordinal, _ in entries}
        if actual != source.content_ordinals:
            missing = sorted(source.content_ordinals - actual)
            extra = sorted(actual - source.content_ordinals)
            raise ValueError(
                f"structural observation is not total (missing={missing[:5]}, extra={extra[:5]})"
            )
        return observation

    @property
    def by_ordinal(self) -> dict[docx_source.ParagraphOrdinal, ParagraphObservation]:
        return dict(self.entries)

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


@dataclass(frozen=True, slots=True)
class BlockClaim:
    """One compiler block's individual claim on one or more source ordinals."""

    kind: CompilerBlockKind
    span: ir.SourceSpan
    path: tuple[int, ...]
    context: CompilerBlockKind | None = None

    @property
    def structural_kind(self) -> CompilerBlockKind:
        return self.context or self.kind


@dataclass(frozen=True, slots=True)
class SourceBlockHit:
    claims: tuple[BlockClaim, ...]

    @property
    def kinds(self) -> frozenset[CompilerBlockKind]:
        return frozenset(claim.structural_kind for claim in self.claims)

    @property
    def span(self) -> ir.SourceSpan:
        merged = ir.merge_source_spans(claim.span for claim in self.claims)
        assert merged is not None
        return merged


@dataclass(slots=True)
class _SourceBlockBuilder:
    eligible: frozenset[int]
    claims: dict[int, list[BlockClaim]] = field(default_factory=dict)

    def add(
        self,
        block: ir.Block,
        *,
        path: tuple[int, ...],
        context: CompilerBlockKind | None = None,
    ) -> None:
        """Record leaf block claims while retaining their enclosing product role."""
        kind = compiler_block_kind(block)
        match block:
            case ir.QuoteBlock(blocks=members) if members:
                outer = context or kind
                for index, member in enumerate(members):
                    self.add(member, path=(*path, index), context=outer)
                return
            case ir.ListBlock(items=items) if items:
                outer = context or kind
                for item_index, item in enumerate(items):
                    for member_index, member in enumerate(item):
                        self.add(
                            member,
                            path=(*path, item_index, member_index),
                            context=outer,
                        )
                return
            case _:
                pass

        if (span := block.source_span) is None:
            return
        claim = BlockClaim(kind=kind, span=span, path=path, context=context)
        for ordinal in range(span.start, span.end + 1):
            if ordinal in self.eligible:
                self.claims.setdefault(ordinal, []).append(claim)

    def build(self) -> dict[int, SourceBlockHit]:
        return {
            ordinal: SourceBlockHit(tuple(claims))
            for ordinal, claims in self.claims.items()
        }


_STRUCTURAL_KIND_ROLES = {
    CompilerBlockKind.HEADING: ContextRole.HEADING,
    CompilerBlockKind.THEMATIC: ContextRole.THEMATIC,
    CompilerBlockKind.LIST: ContextRole.LIST,
    CompilerBlockKind.TABLE: ContextRole.CONTEXT,
    CompilerBlockKind.SIGNATURE: ContextRole.CONTEXT,
    CompilerBlockKind.EPIGRAPH: ContextRole.CONTEXT,
    CompilerBlockKind.QUOTE: ContextRole.CONTEXT,
    CompilerBlockKind.IMAGE: ContextRole.CONTEXT,
    CompilerBlockKind.DIALOGUE_LABEL: ContextRole.CONTEXT,
    CompilerBlockKind.CODE: ContextRole.CONTEXT,
}
_BODY_KINDS = frozenset({CompilerBlockKind.PARAGRAPH, CompilerBlockKind.LINEATED})


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
    eligible_ordinals: Iterable[int],
) -> dict[int, SourceBlockHit]:
    """Preserve every individual compiler claim on each eligible source ordinal."""
    builder = _SourceBlockBuilder(frozenset(eligible_ordinals))
    for index, block in enumerate(blocks):
        builder.add(block, path=(index,))
    return builder.build()


def _observation(hit: SourceBlockHit) -> ParagraphObservation:
    if len(hit.claims) != 1:
        return ReviewParagraph(ReviewReason.NON_UNIQUE_CLAIMS)
    claim = hit.claims[0]
    kind = claim.structural_kind
    if kind is CompilerBlockKind.UNKNOWN:
        return ReviewParagraph(ReviewReason.UNKNOWN_KIND)
    if role := _STRUCTURAL_KIND_ROLES.get(kind):
        return ContextParagraph(ContextReason.STRUCTURAL_KIND, role)
    if claim.kind is CompilerBlockKind.PARAGRAPH and claim.span.end != claim.span.start:
        return ReviewParagraph(ReviewReason.MERGED_BODY)
    if claim.kind in _BODY_KINDS:
        return BodyParagraph()
    return ContextParagraph(ContextReason.STRUCTURAL_KIND)


def observe_structure(
    source: docx_source.DocxSourceDocument,
    *,
    lang: Locale,
) -> StructuralObservation:
    """Classify every content paragraph at the pre-lineation structural seam."""
    with tempfile.TemporaryDirectory(prefix="docx-structure-") as directory:
        adapted = da.adapt(source, Path(directory), [])
        at_adapter = frozenset(source_block_hits(adapted.blocks, source.content_ordinals))
        compiled = run(adapted, Context(lang=lang), until=PER_ORDINAL_SEAM)

    at_seam = source_block_hits(compiled.blocks, source.content_ordinals)

    entries: list[tuple[docx_source.ParagraphOrdinal, ParagraphObservation]] = []
    for ordinal in sorted(source.content_ordinals):
        source_ordinal = docx_source.ParagraphOrdinal(ordinal)
        if hit := at_seam.get(ordinal):
            observation = _observation(hit)
        elif ordinal in at_adapter:
            observation = ContextParagraph(ContextReason.DROPPED_BY_STRUCTURAL_PIPELINE)
        else:
            observation = ReviewParagraph(ReviewReason.UNMAPPED_AT_ADAPTER)
        entries.append((source_ordinal, observation))
    return StructuralObservation.complete(source, tuple(entries))


class FoldDisposition(StrEnum):
    """Whether one source paragraph remains flowing or enters a lineated block."""

    FLOWING = "flowing"
    FOLDED = "folded"


@dataclass(frozen=True, slots=True)
class FoldDecision:
    """A unanimous flow-bearing claim, retaining every compiler claim for audit."""

    disposition: FoldDisposition
    claims: tuple[BlockClaim, ...]


@dataclass(frozen=True, slots=True)
class FoldConflict:
    """The compiler produced both flowing and folded claims for one source paragraph."""

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
    """Covered source ordinals projected mechanically at the post-fold compiler seam."""

    entries: tuple[tuple[docx_source.ParagraphOrdinal, FoldResult], ...]

    @property
    def by_ordinal(self) -> dict[docx_source.ParagraphOrdinal, FoldResult]:
        return dict(self.entries)

    @property
    def decisions(self) -> tuple[tuple[docx_source.ParagraphOrdinal, FoldDecision], ...]:
        return tuple(
            (ordinal, result)
            for ordinal, result in self.entries
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

    hits = source_block_hits(doc.blocks, source.content_ordinals)
    entries: list[tuple[docx_source.ParagraphOrdinal, FoldResult]] = []
    for ordinal, hit in sorted(hits.items()):
        if result := _fold_result(hit):
            entries.append((docx_source.ParagraphOrdinal(ordinal), result))
    return FoldObservation(tuple(entries))


def fold_decisions(
    source: docx_source.DocxSourceDocument,
    *,
    lang: Locale,
    apply_overrides: bool = True,
) -> dict[int, bool]:
    """Boolean projection of the compiler-owned fold observation."""
    return {
        int(ordinal): decision.disposition is FoldDisposition.FOLDED
        for ordinal, decision in observe_fold(
            source,
            lang=lang,
            apply_overrides=apply_overrides,
        ).decisions
    }
