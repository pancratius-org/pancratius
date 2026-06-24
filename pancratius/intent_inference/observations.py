"""Register observations produced by the import compiler for policy/scoring."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from pancratius import ir
from pancratius.intent_inference.decisions import (
    CandidateId,
    DisplayRegisterLabel,
    RegisterDecisionReason,
)
from pancratius.ir.inlines import inline_plain
from pancratius.locales import Locale

_PASS_SEAM = "assign_register"


@dataclass(frozen=True, slots=True)
class RegisterBookStats:
    mean_para_len: float
    lineated_frac: float


@dataclass(frozen=True, slots=True)
class RegisterModelContext:
    heading: bool = False
    named: bool = False
    separator: bool = False


@dataclass(frozen=True, slots=True)
class RegisterRuleContext:
    named: bool = False
    heading: bool = False
    separator: bool = False


@dataclass(frozen=True, slots=True)
class RegisterRuleEvaluation:
    label: DisplayRegisterLabel
    reason: RegisterDecisionReason
    model_allowed: bool


@dataclass(frozen=True, slots=True)
class RegisterObservation:
    candidate_id: CandidateId
    lang: Locale
    lines: tuple[str, ...]
    stanza_line_counts: tuple[int, ...]
    evidence: ir.LineationEvidence
    model_context: RegisterModelContext
    book: RegisterBookStats


@dataclass(frozen=True, slots=True)
class RegisterCandidate:
    candidate_id: CandidateId
    source_block_index: int
    source_span: ir.SourceSpan | None
    observation: RegisterObservation
    rules: RegisterRuleEvaluation


@dataclass(frozen=True, slots=True)
class RegisterDocumentContext:
    lang: Locale


def register_book_stats(blocks: list[ir.Block]) -> RegisterBookStats:
    para_lens = [
        len(inline_plain(b.inlines))
        for b in blocks
        if isinstance(b, ir.Paragraph) and not b.empty
    ]
    lineated = sum(1 for b in blocks if isinstance(b, ir.LineatedBlock))
    total = len(para_lens) + lineated
    return RegisterBookStats(
        mean_para_len=sum(para_lens) / len(para_lens) if para_lens else 0.0,
        lineated_frac=lineated / total if total else 0.0,
    )


def lineated_plain_lines(block: ir.LineatedBlock) -> tuple[str, ...]:
    return tuple(
        text
        for stanza in block.stanzas
        for line in stanza
        if (text := inline_plain(line.inlines))
    )


def stanza_line_counts(stanzas: ir.LineatedStanzas) -> tuple[int, ...]:
    return tuple(
        size
        for stanza in stanzas
        if (size := sum(1 for line in stanza if inline_plain(line.inlines)))
    )


def stable_register_candidate_id(
    block: ir.LineatedBlock,
    *,
    source_block_index: int,
    candidate_ordinal: int,
) -> CandidateId:
    if block.source_span is not None:
        return CandidateId(
            f"{_PASS_SEAM}:source:{block.source_span.start}-{block.source_span.end}"
        )
    payload_parts = [
        *lineated_plain_lines(block),
        f"stanzas={','.join(str(count) for count in stanza_line_counts(block.stanzas))}",
        (
            "evidence="
            f"{int(block.evidence.pandoc_line_block)},"
            f"{int(block.evidence.hard_break)},"
            f"{int(block.evidence.inferred_source_rows)},"
            f"{int(block.evidence.stanza_break)},"
            f"{int(block.evidence.compact_callout)}"
        ),
    ]
    payload = "\n".join(payload_parts).encode("utf-8")
    source = f"spanless:{hashlib.sha256(payload).hexdigest()[:16]}"
    return CandidateId(
        f"{_PASS_SEAM}:{source}:block:{source_block_index}:candidate:{candidate_ordinal}"
    )
