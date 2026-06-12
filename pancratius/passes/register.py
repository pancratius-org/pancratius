# import-pure: no filesystem mutation
"""Display-register passes (Q2) — the one home for register decisions.

`fold_quote_registers` lifts authored set-apart gestures into typed quote
blocks: the author marks passages with paragraph borders (`w:pBdr`) — a full
four-side box frames quoted canonical text, a left rule bars an inset passage
in another voice. Border evidence is within-book contrastive: a kind covering
a large share of the book's text paragraphs is the book's own frame, not a
set-apart gesture.

`assign_register` decides the verse register over lineated blocks: hard
editorial guards first (named verse sections promote, scaffold shapes never
do), then the trained register model where the composition point injected one
(`Context.register_model`), the geometry ladder otherwise. The feature
producer and the model codec live here too — extraction, training, and this
pass read one φ.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pancratius import ir
from pancratius.ir.inlines import inline_plain
from pancratius.passes.lineation import (
    _CODA_PSEUDO_HEADING_RE,
    VERSE_SHORT_LINE_MAX,
    _is_compact_coda,
    _skip_empty_paragraphs,
    is_lineated_line,
)

if TYPE_CHECKING:
    from pancratius.passes.pipeline import Context

# ---------------------------------------------------------------------------
# Q2a: bordered set-apart runs -> quote blocks
# ---------------------------------------------------------------------------

# A border kind claiming at least this share of a book's text paragraphs is the
# book's baseline frame, not a per-block set-apart gesture. The corpus maximum
# for an intentional gesture is book 19's left rule at ~21%; the only known
# baseline case (book 10's 95% template box) sat far above.
_BASELINE_RATE = 0.30

_ROLE_BY_BORDER: dict[ir.BorderKind, str] = {"box": "scripture", "rule": "inset"}


def _border_rates(blocks: list[ir.Block]) -> dict[ir.BorderKind, float]:
    """Each border kind's share of the book's non-empty paragraphs."""
    text_paras = [
        b for b in blocks if isinstance(b, ir.Paragraph) and not b.empty
    ]
    if not text_paras:
        return {}
    counts: dict[ir.BorderKind, int] = {}
    for p in text_paras:
        if p.border:
            counts[p.border] = counts.get(p.border, 0) + 1
    return {kind: n / len(text_paras) for kind, n in counts.items()}


def _run_role(blocks: list[ir.Block], i: int, contrastive: set[ir.BorderKind]) -> str | None:
    b = blocks[i]
    if not isinstance(b, ir.Paragraph) or b.empty:
        return None
    if b.border not in contrastive:
        return None
    return _ROLE_BY_BORDER[b.border]


def fold_quote_registers(blocks: list[ir.Block]) -> list[ir.Block]:
    """Wrap contiguous contrastively-bordered paragraph runs into quote blocks.

    A run is a maximal sequence of paragraphs sharing one border kind; interior
    empty paragraphs continue it (Word merges adjacent same-border paragraphs
    into one visual frame across blanks) but never open or close it.
    """
    rates = _border_rates(blocks)
    contrastive = {
        kind for kind in _ROLE_BY_BORDER
        if 0.0 < rates.get(kind, 0.0) < _BASELINE_RATE
    }
    if not contrastive:
        return blocks

    out: list[ir.Block] = []
    i = 0
    n = len(blocks)
    while i < n:
        role = _run_role(blocks, i, contrastive)
        if role is None:
            out.append(blocks[i])
            i += 1
            continue
        first = blocks[i]
        assert isinstance(first, ir.Paragraph)
        kind = first.border
        members: list[ir.Paragraph] = [first]
        j = i + 1
        pending: list[ir.Paragraph] = []  # interior empties not yet committed
        while j < n:
            nxt = blocks[j]
            if not isinstance(nxt, ir.Paragraph):
                break
            if nxt.empty:
                pending.append(nxt)
                j += 1
                continue
            if nxt.border != kind:
                break
            members.extend(pending)
            pending.clear()
            members.append(nxt)
            j += 1
        out.append(ir.BlockQuote(
            blocks=list(members),
            role=role,
            source_span=ir.merge_source_spans(
                p.source_span for p in members if not p.empty
            ),
        ))
        out.extend(pending)  # trailing empties stay outside the wrapper
        i = j
    return out


# ---------------------------------------------------------------------------
# section-title vocab + scaffold guards (shared by ladder and model paths)
# ---------------------------------------------------------------------------

_VERSE_SECTION_TITLE_RE = re.compile(
    r"^(?:posвящение|посвящение|dedication|"
    r"предисловие\s+от\s+творца|preface\s+(?:from|by)\s+the\s+creator|"
    r"слово\s+творца|the\s+word\s+of\s+the\s+creator|creator'?s\s+word|"
    r"голос\s+творца|voice\s+of\s+the\s+creator|"
    r"ответ\s+творца|creator'?s\s+answer|"
    r"пояснение\s+творца|annotation\s+from\s+the\s+creator|"
    r"благословляющее\s+слово\s+творца|"
    r"молитва|prayer|псалом|psalm)\b",
    re.IGNORECASE,
)


def is_verse_section_title(t: str) -> bool:
    return bool(_VERSE_SECTION_TITLE_RE.match(re.sub(r"\s+", " ", t.strip().lower())))


_DASH_LINE_RE = re.compile(r"^[—–-]\s")
_MATH_CHARS_RE = re.compile(r"[0-9=+×*²³√:.,()\s—–-]")


def _is_equation_line(line: str) -> bool:
    """A numerology/equation line (`153 = 9 × 17`): contains `=`/`×` and is
    mostly digits and operators. Math is never the verse register."""
    if "=" not in line and "×" not in line:
        return False
    return len(_MATH_CHARS_RE.findall(line)) / len(line) >= 0.6


def is_equation_scaffold(lines: list[str]) -> bool:
    return all(_is_equation_line(line) for line in lines)


def is_dash_scaffold(lines: list[str]) -> bool:
    """A pure dash-led enumeration («— возражения…», optionally after a colon
    opener): list scaffolding that keeps its line structure but is never the
    elevated verse register. Deliberately strict — a PARTIALLY dash-led run is
    kept, because anaphoric litanies inside oracle passages mix dash lines
    with framing verse lines."""
    body = lines[1:] if lines and lines[0].rstrip().endswith(":") else lines
    return len(body) >= 2 and all(_DASH_LINE_RE.match(line) for line in body)


# ---------------------------------------------------------------------------
# the feature producer (one φ for extraction, training, and this pass)
# ---------------------------------------------------------------------------

_TERM_RE = re.compile(r"[.!?…]\s*$")
_Q2P_RE = re.compile(r"\b(ты|тебя|тебе|тобой|твой|твоя|твоё|твои)\b", re.IGNORECASE)
_DIVINE_RE = re.compile(r"\b(Я|Меня|Мне|Мной|Мой|Моя|Моё|Мои)\b")
_QUOTE_OPEN_RE = re.compile(r"^[«\"„]")
_NUM_LEAD_RE = re.compile(r"^\d{1,4}[.:)\s]")

# The exported feature order; the model artifact pins the same list and the
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
    """Yield every block with its register context — the one walker the teacher
    extraction and this pass both read."""
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
        "dash_rate": rate(lambda x: bool(_DASH_LINE_RE.match(x))),
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


# ---------------------------------------------------------------------------
# the register model (committed JSON artifact; dot-product scoring)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RegisterModel:
    """A standardized-logistic register model: ``p(verse | features)``."""

    version: int
    langs: tuple[str, ...]
    features: tuple[str, ...]
    mean: tuple[float, ...]
    std: tuple[float, ...]
    coef: tuple[float, ...]
    intercept: float
    threshold: float

    def probability(self, feats: dict[str, float]) -> float:
        z = self.intercept
        for name, mu, sd, w in zip(self.features, self.mean, self.std, self.coef, strict=True):
            z += w * ((feats[name] - mu) / sd)
        return 1.0 / (1.0 + math.exp(-z))


def load_register_model(path: Path) -> RegisterModel | None:
    """The exported model artifact, or ``None`` when not shipped."""
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if tuple(raw["features"]) != FEATURE_NAMES:
        raise ValueError(
            "register model artifact feature schema drifted from the producer"
        )
    return RegisterModel(
        version=int(raw.get("version", 0)),
        langs=tuple(raw.get("langs", ())),
        features=tuple(raw["features"]),
        mean=tuple(raw["mean"]),
        std=tuple(raw["std"]),
        coef=tuple(raw["coef"]),
        intercept=float(raw["intercept"]),
        threshold=float(raw["threshold"]),
    )


# ---------------------------------------------------------------------------
# Q2b: the verse-register decision over lineated blocks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _PrecedingContext:
    """Q2 register context preceding an already-lineated block, as the ladder
    reads it (blank rows RESET it — unlike `RegisterContext`, whose transparent
    blanks the model was trained on)."""

    named: bool = False
    heading: bool = False
    separator: bool = False


_NEUTRAL_CONTEXT = _PrecedingContext()


def assign_register(doc: ir.Document, ctx: Context) -> ir.Document:
    """The Q2 pass: decide the verse register for every lineated block."""
    model = ctx.register_model
    feats_ctx: dict[int, RegisterContext] = {}
    stats: BookStats | None = None
    if model is not None:
        stats = book_stats(doc.blocks)
        feats_ctx = {id(b): c for b, c in iter_with_register_context(doc.blocks)}
    doc.blocks = _promote(doc.blocks, model, feats_ctx, stats)
    return doc


def promote_verse_register(blocks: list[ir.Block]) -> list[ir.Block]:
    """The ladder-only promotion (no model) — the rule policy and compat name."""
    return _promote(blocks, None, {}, None)


def _promote(
    blocks: list[ir.Block],
    model: RegisterModel | None,
    feats_ctx: dict[int, RegisterContext],
    stats: BookStats | None,
) -> list[ir.Block]:
    out: list[ir.Block] = []
    i = 0
    ctx = _NEUTRAL_CONTEXT

    while i < len(blocks):
        b = blocks[i]
        if isinstance(b, ir.Heading):
            title = inline_plain(b.inlines)
            ctx = _PrecedingContext(
                named=is_verse_section_title(title),
                heading=True,
            )
            out.append(b)
            i += 1
            continue
        if isinstance(b, ir.ThematicBreak):
            ctx = _PrecedingContext(separator=True)
            out.append(b)
            i += 1
            continue
        if isinstance(b, ir.LineatedBlock):
            if _verdict(b, ctx, model, feats_ctx, stats):
                verse = ir.VerseBlock(
                    stanzas=b.stanzas, evidence=b.evidence,
                    source_span=b.source_span,
                )
                if (segment := _lineated_coda_segment(blocks, i + 1, verse)) is not None:
                    verse, next_i = segment
                    out.append(verse)
                    ctx = _NEUTRAL_CONTEXT
                    i = next_i
                    continue
                out.append(verse)
            else:
                out.append(b)
            ctx = _NEUTRAL_CONTEXT
            i += 1
            continue
        if isinstance(b, ir.VerseBlock):
            if (segment := _existing_verse_coda_segment(blocks, i)) is not None:
                verse, next_i = segment
                out.append(verse)
                ctx = _NEUTRAL_CONTEXT
                i = next_i
                continue
            ctx = _NEUTRAL_CONTEXT
            out.append(b)
            i += 1
            continue
        ctx = _NEUTRAL_CONTEXT
        out.append(b)
        i += 1
    return out


def _verdict(
    block: ir.LineatedBlock,
    ctx: _PrecedingContext,
    model: RegisterModel | None,
    feats_ctx: dict[int, RegisterContext],
    stats: BookStats | None,
) -> bool:
    """One verse decision: hard guards, then the model where injected, the
    geometry ladder otherwise. Named verse sections always take the ladder —
    the structural prior outranks the model in both directions."""
    lines = _lineated_block_lines(block)
    if len(lines) < 2 or not all(is_lineated_line(line) for line in lines):
        return False
    if is_dash_scaffold(lines) or is_equation_scaffold(lines):
        return False
    if model is None or stats is None or ctx.named:
        return _kind_for_lines(lines, block.evidence, ctx) is not None
    p = model.probability(verse_register_features(
        lines, block.stanzas, block.evidence,
        ctx=feats_ctx.get(id(block), RegisterContext()), book=stats,
    ))
    return p >= model.threshold


def _lineated_coda_candidate(
    blocks: list[ir.Block],
    i: int,
) -> tuple[ir.LineatedBlock, int] | None:
    """A local coda segment after a verse run.

    Shape: one or more empty paragraphs, an exact two-line lineated
    candidate, optional empty paragraphs, then a heading/thematic boundary. The
    candidate must be compact; this keeps prose previews before the next heading in
    prose without naming their words.
    """
    i, saw_gap = _skip_empty_paragraphs(blocks, i)
    if not saw_gap:
        return None

    first = blocks[i] if i < len(blocks) else None
    if not isinstance(first, ir.LineatedBlock):
        return None
    i += 1

    coda_lines = _lineated_block_lines(first)
    if not _is_compact_coda(coda_lines):
        return None
    if any(_CODA_PSEUDO_HEADING_RE.match(line) for line in coda_lines):
        return None

    boundary_i, _saw_trailing_gap = _skip_empty_paragraphs(blocks, i)
    boundary = blocks[boundary_i] if boundary_i < len(blocks) else None
    if not isinstance(boundary, (ir.Heading, ir.ThematicBreak)):
        return None

    return first, boundary_i


def _append_coda_copy(prev: ir.VerseBlock, coda: ir.LineatedBlock) -> ir.VerseBlock:
    return ir.VerseBlock(
        stanzas=[*prev.stanzas, *coda.stanzas],
        role=prev.role,
        evidence=prev.evidence,
        source_span=ir.merge_source_spans((prev.source_span, coda.source_span)),
    )


def _lineated_coda_segment(
    blocks: list[ir.Block],
    i: int,
    prev: ir.VerseBlock,
) -> tuple[ir.VerseBlock, int] | None:
    candidate = _lineated_coda_candidate(blocks, i)
    if candidate is None:
        return None
    coda, next_i = candidate
    return _append_coda_copy(prev, coda), next_i


def _existing_verse_coda_segment(
    blocks: list[ir.Block],
    i: int,
) -> tuple[ir.VerseBlock, int] | None:
    prev = blocks[i]
    assert isinstance(prev, ir.VerseBlock)
    return _lineated_coda_segment(blocks, i + 1, prev)


def _lineated_block_lines(block: ir.LineatedBlock) -> list[str]:
    return [
        inline_plain(line)
        for stanza in block.stanzas
        for line in stanza
        if inline_plain(line)
    ]


def _kind_for_lines(
    lines: list[str],
    evidence: ir.LineationEvidence,
    ctx: _PrecedingContext,
) -> ir.VerseRole | None:
    """The geometry ladder. Callers have already applied the shared guards."""
    def _passes(avg_max: float, line_max: int | None = None) -> bool:
        """The run's mean line length is within `avg_max` and (when given) every line
        is within `line_max`. The `(avg_max, line_max)` pair is all that varies across
        the ladder below."""
        return avg <= avg_max and (line_max is None or max(lengths) <= line_max)

    lengths = [len(line) for line in lines]
    if evidence.hard_break and max(lengths) > VERSE_SHORT_LINE_MAX:
        return None
    avg = sum(lengths) / len(lengths)
    if ctx.named:
        return "verse" if _passes(150) else None
    if ctx.separator and len(lines) <= 32:
        return "verse" if _passes(110, 160) else None
    if ctx.heading and len(lines) <= 32:
        return "verse" if _passes(95, 150) else None
    if evidence.compact_callout:
        return None
    if evidence.hard_break:
        return "verse"
    if evidence.stanza_break and len(lines) >= 3 and _passes(120):
        return "verse"
    if evidence.inferred_source_rows and len(lines) >= 3 and _passes(95, 120):
        return "verse"
    return None
