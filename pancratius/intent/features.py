"""The verse-register feature producer: the one feature/context source the
teacher extraction, training, and the enrichment pass all read."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

from pancratius import ir
from pancratius.ir.normalize import inline_plain, is_verse_section_title

_TERM_RE = re.compile(r"[.!?…]\s*$")
_DASH_RE = re.compile(r"^[—–-]\s")
_Q2P_RE = re.compile(r"\b(ты|тебя|тебе|тобой|твой|твоя|твоё|твои)\b", re.IGNORECASE)
_DIVINE_RE = re.compile(r"\b(Я|Меня|Мне|Мной|Мой|Моя|Моё|Мои)\b")
_QUOTE_OPEN_RE = re.compile(r"^[«\"„]")
_NUM_LEAD_RE = re.compile(r"^\d{1,4}[.:)\s]")

# The exported feature order; the student artifact pins the same list and the
# loader refuses a mismatch (fail loud on drift).
FEATURE_NAMES = (
    "n_lines", "mean_len", "max_len", "cv_len", "term_rate", "dash_rate",
    "q2p_rate", "divine_rate", "quote_open_rate", "num_lead_rate",
    "question_rate", "comma_end_rate", "lower_start_rate", "n_stanzas",
    "multi_line_stanzas", "ev_hard_break", "ev_inferred", "ev_stanza_break",
    "ev_compact_callout", "ctx_heading", "ctx_named", "ctx_separator",
    "len_vs_book", "book_lineated_frac",
)


@dataclass(frozen=True)
class BookStats:
    """Within-book baselines for contrastive features."""

    mean_para_len: float
    lineated_frac: float


def book_stats(blocks: list[ir.Block]) -> BookStats:
    para_lens = [
        len(inline_plain(b.inlines))
        for b in blocks
        if isinstance(b, ir.Paragraph) and not b.empty
    ]
    lineated = sum(1 for b in blocks if isinstance(b, (ir.LineatedBlock, ir.VerseBlock)))
    total = len(para_lens) + lineated
    return BookStats(
        mean_para_len=sum(para_lens) / len(para_lens) if para_lens else 0.0,
        lineated_frac=lineated / total if total else 0.0,
    )


@dataclass(frozen=True)
class RegisterContext:
    """What precedes a candidate block. Empty rows are TRANSPARENT: a heading
    followed by a blank row still heads the next content block."""

    heading: bool = False
    named: bool = False
    separator: bool = False


def iter_with_register_context(
    blocks: list[ir.Block],
) -> Iterator[tuple[ir.Block, RegisterContext]]:
    """Yield every block with its register context — the one walker both the
    teacher extraction and the enrichment pass read."""
    ctx = RegisterContext()
    for b in blocks:
        yield b, ctx
        if isinstance(b, ir.Heading):
            ctx = RegisterContext(heading=True, named=is_verse_section_title(inline_plain(b.inlines)))
        elif isinstance(b, ir.ThematicBreak):
            ctx = RegisterContext(separator=True)
        elif isinstance(b, ir.Paragraph) and b.empty:
            pass  # blank rows are transparent
        else:
            ctx = RegisterContext()


def lineated_lines(block: ir.LineatedBlock | ir.VerseBlock) -> list[str]:
    return [
        inline_plain(line)
        for stanza in block.stanzas
        for line in stanza
        if inline_plain(line)
    ]


def verse_register_features(
    lines: list[str],
    stanzas: ir.LineatedStanzas,
    evidence: ir.LineationEvidence,
    *,
    ctx: RegisterContext,
    book: BookStats,
) -> dict[str, float]:
    """The block's register feature vector (keys = ``FEATURE_NAMES``)."""
    n = len(lines)
    lens = [len(x) for x in lines] or [0]
    mean_len = sum(lens) / len(lens)
    stanza_sizes = [
        size for st in stanzas if (size := sum(1 for line in st if inline_plain(line)))
    ]
    rate = (lambda pred: sum(1 for x in lines if pred(x)) / n) if n else (lambda _pred: 0.0)
    return {
        "n_lines": float(n),
        "mean_len": mean_len,
        "max_len": float(max(lens)),
        "cv_len": (
            (sum((x - mean_len) ** 2 for x in lens) / len(lens)) ** 0.5 / mean_len
            if mean_len else 0.0
        ),
        "term_rate": rate(lambda x: bool(_TERM_RE.search(x))),
        "dash_rate": rate(lambda x: bool(_DASH_RE.match(x))),
        "q2p_rate": rate(lambda x: bool(_Q2P_RE.search(x))),
        "divine_rate": rate(lambda x: bool(_DIVINE_RE.search(x))),
        "quote_open_rate": rate(lambda x: bool(_QUOTE_OPEN_RE.match(x))),
        "num_lead_rate": rate(lambda x: bool(_NUM_LEAD_RE.match(x))),
        "question_rate": rate(lambda x: "?" in x),
        "comma_end_rate": rate(lambda x: x.rstrip().endswith((",", "—", "–"))),
        "lower_start_rate": rate(lambda x: x[:1].islower()),
        "n_stanzas": float(len(stanza_sizes)),
        "multi_line_stanzas": float(sum(1 for s in stanza_sizes if s > 1)),
        "ev_hard_break": float(evidence.hard_break),
        "ev_inferred": float(evidence.inferred_source_rows),
        "ev_stanza_break": float(evidence.stanza_break),
        "ev_compact_callout": float(evidence.compact_callout),
        "ctx_heading": float(ctx.heading),
        "ctx_named": float(ctx.named),
        "ctx_separator": float(ctx.separator),
        "len_vs_book": mean_len / book.mean_para_len if book.mean_para_len else 0.0,
        "book_lineated_frac": book.lineated_frac,
    }
