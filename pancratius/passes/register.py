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
(`Context.register_model`), the geometry ladder otherwise. A verse run is
then segmented (`segment_lineated`): scaffold sub-runs (equations, dash
enumerations) split out as `ORDINARY` fragments with honest line-derived
spans. The feature producer and the model codec live here too — extraction,
training, and this pass read one φ.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from pancratius import ir
from pancratius.ir.inlines import inline_plain
from pancratius.passes.lineation import (
    CODA_PSEUDO_HEADING_RE,
    VERSE_SHORT_LINE_MAX,
    is_compact_coda,
    is_lineated_line,
    skip_empty_paragraphs,
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

_REGISTER_BY_BORDER: dict[ir.BorderKind, ir.Register] = {
    "box": ir.Register.SCRIPTURE,
    "rule": ir.Register.INSET,
}


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


def fold_quote_registers(blocks: list[ir.Block]) -> list[ir.Block]:
    """Wrap contiguous contrastively-bordered paragraph runs into quote blocks.

    A run is a maximal sequence of paragraphs sharing one border kind; interior
    empty paragraphs continue it (Word merges adjacent same-border paragraphs
    into one visual frame across blanks) but never open or close it.
    """
    rates = _border_rates(blocks)
    contrastive = {
        kind for kind in _REGISTER_BY_BORDER
        if 0.0 < rates.get(kind, 0.0) < _BASELINE_RATE
    }
    if not contrastive:
        return blocks

    out: list[ir.Block] = []
    i = 0
    n = len(blocks)
    while i < n:
        first = blocks[i]
        if not (
            isinstance(first, ir.Paragraph)
            and not first.empty
            and first.border in contrastive
        ):
            out.append(first)
            i += 1
            continue
        register = _REGISTER_BY_BORDER[first.border]
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
        out.append(ir.QuoteBlock(
            blocks=list(members),
            register=register,
            source_span=ir.merge_source_spans(
                p.source_span for p in members if not p.empty
            ),
        ))
        out.extend(pending)  # trailing empties stay outside the wrapper
        i = j
    return out


# ---------------------------------------------------------------------------
# Q2c: unfenced canonical quotations -> scripture quote blocks
# ---------------------------------------------------------------------------

# Citation tokens: bible-book abbreviations with chapter:verse, sura/ayat refs.
# The body-text sibling of structure._SCRIPTURE_REF_RE (which anchors whole
# epigraph lines; this one finds the token inside running prose).
_SCRIPTURE_CITE_RE = re.compile(
    r"(?:\b(?:Ин|Иоанн?а?|Мф|Матфе[яй]|Мк|Марк[а]?|Лк|Лук[аи]|Откр(?:овение)?|"
    r"Быт(?:ие)?|Пс(?:ал(?:ом|тирь)?)?|Кор(?:инфянам)?|Рим(?:лянам)?|Евр(?:еям)?|"
    r"Ис(?:а[ий]я)?|Иер(?:еми[яи])?|Втор(?:озаконие)?|Исх(?:од)?|Дан(?:иил)?|"
    r"Деян(?:ия)?|Гал(?:атам)?|Еф(?:есянам)?|Флп|Кол(?:оссянам)?|Иак(?:ов)?|Пет(?:р)?)"
    r"\.?\s*\d{1,3}\s*[:.,]\s*\d{1,3}"
    r"|\bсур[аеыу]\b|\bая[тм]\w*\b|\bКоран\w*\b)",
    re.IGNORECASE,
)
# A speech-introduction formula opening the quotation itself: «Иисус сказал: …»,
# «И сказал Бог Моисею: …» — the logion/canonical-narration shape. The narrated
# scene is third-person; a first/second-person participant («говорит мне: …»)
# is the author's own dictation experience, not canon.
_SPEECH_ANCHOR_RE = re.compile(
    r"^[«\"„“](?![^«»\":]{0,40}?\b(?:мне|нам|тебе|вам|мной|нами|тобой|вами)\b)"
    r"[^«»\":]{0,40}?"
    r"(?:сказал[аио]?|говорит|говорил[аи]?|спросил[аи]?|ответил[аи]?)"
    r"(?![^«»\":]{0,40}?\b(?:мне|нам|тебе|вам|мной|нами|тобой|вами)\b)"
    r"[^«»\":]{0,40}:",
)
# A paragraph led by a citation, then the quoted text: `— Откр. 19:11: «И увидел…»`.
# The quote must CLOSE the paragraph (checked by the caller): a quote followed
# by the book's own words is the book's dialogue citing scripture, not a
# canonical block.
_REF_LED_QUOTE_RE = re.compile(
    r"^[—–-]?\s*\(?(?:[1-3]\s*)?[А-ЯЁ][А-Яа-яЁё.]{1,16}\.?\s*\d{1,3}\s*[:.,]\s*\d{1,3}"
    r"(?:\s*[–—-]\s*\d{1,3})?\)?\s*[:.]?\s*(?=[«\"„“])",
)
_TRAILING_CITE_RE = re.compile(r"\(([^()]{2,60})\)\s*$")
# A paragraph that IS a citation: `Сура 4:157–158 (ан-Ниса)`, `Матфея 3:16–17`,
# `Откровение 6:1–2`, `Коран, сура 41:53`.
_BARE_CITE_RE = re.compile(
    r"^[—–-]?\s*\(?(?:(?:[1-3]\s*)?[А-ЯЁ][А-Яа-яЁё.]{1,16}\.?|Коран,?\s*сура|Сура|Коран)\s*"
    r"\d{1,3}\s*[:.,]\s*\d{1,3}(?:\s*[–—-]\s*\d{1,3})?\)?\s*(?:\([^)]{1,40}\))?\s*[:.]?$"
)
_QUOTE_TRAIL = ".,;:!?…—– "


def _split_trailing_cite(text: str) -> tuple[str, str]:
    """Split a trailing parenthetical citation: `«…» (Ин. 4:23).` ->
    (`«…»`, `Ин. 4:23`); no parenthetical -> (text, "")."""
    text = text.strip()
    body = text.rstrip(_QUOTE_TRAIL)
    m = _TRAILING_CITE_RE.search(body)
    if not m:
        return text, ""
    return body[: m.start()].rstrip(_QUOTE_TRAIL), m.group(1)


def _is_whole_quote(text: str) -> bool:
    """The text is ONE quotation: opens with «, and that same quote closes at
    the very end (guillemet depth never returns to zero before the last char)."""
    text = text.strip().rstrip(_QUOTE_TRAIL)
    if len(text) < 3 or text[0] != "«" or text[-1] != "»":
        return False
    depth = 0
    for i, ch in enumerate(text):
        if ch == "«":
            depth += 1
        elif ch == "»":
            depth -= 1
            if depth == 0 and i < len(text) - 1:
                return False
    return depth == 0


def is_scripture_quote(text: str) -> bool:
    """One prose paragraph's scripture verdict (teacher-validated channels).

    A paragraph is a canonical quotation when it is a whole-paragraph quote
    that names its own provenance: a speech-introduction formula inside the
    quote (logion shape), a citation token (in the quote or as a trailing
    parenthetical), or a leading citation introducing the quote. Bare quotes,
    bold, and leading numbers are NOT evidence (refuted: rhetorical/inner
    speech and numbered sub-headings dominate those shapes)."""
    text = text.strip()
    if not text:
        return False
    body, cite = _split_trailing_cite(text)
    if _is_whole_quote(body):
        if _SPEECH_ANCHOR_RE.match(body):
            return True
        if _SCRIPTURE_CITE_RE.search(body) or _SCRIPTURE_CITE_RE.search(cite):
            return True
    if (m := _REF_LED_QUOTE_RE.match(text)) is not None:
        led_body, _led_cite = _split_trailing_cite(text[m.end():])
        if _is_whole_quote(led_body):
            return True
    return False


def _is_bare_cite(text: str) -> bool:
    """The paragraph IS a citation line (`Сура 4:157–158 (ан-Ниса)`)."""
    text = text.strip()
    return bool(_BARE_CITE_RE.match(text)) and bool(_SCRIPTURE_CITE_RE.search(text))


def _scripture_verdicts(blocks: list[ir.Block]) -> set[int]:
    """Block indexes that lower as scripture: per-paragraph verdicts, plus the
    cite-adjacency channel — a whole-paragraph quote whose neighboring
    paragraph (across transparent blanks) is a bare citation line names its
    provenance just as surely as a trailing parenthetical; the citation line
    belongs to the quotation apparatus and is wrapped with it."""
    runs: list[list[int]] = []  # maximal body-paragraph runs (blanks transparent)
    current: list[int] = []
    for i, b in enumerate(blocks):
        if isinstance(b, ir.Paragraph) and b.empty:
            continue
        if isinstance(b, ir.Paragraph):
            current.append(i)
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)

    texts: dict[int, str] = {}
    for run in runs:
        for i in run:
            block = blocks[i]
            assert isinstance(block, ir.Paragraph)
            texts[i] = inline_plain(block.inlines)
    verdicts = {i for i in texts if is_scripture_quote(texts[i])}
    cites = {i for i in texts if _is_bare_cite(texts[i])}
    quotes = {i for i in texts if _is_whole_quote(_split_trailing_cite(texts[i])[0])}
    for run in runs:
        for pos, i in enumerate(run):
            if i not in quotes:
                continue
            neighbors = [run[p] for p in (pos - 1, pos + 1) if 0 <= p < len(run)]
            if any(n in cites for n in neighbors):
                verdicts.add(i)
                verdicts.update(n for n in neighbors if n in cites)
    return verdicts


def wrap_scripture(blocks: list[ir.Block]) -> list[ir.Block]:
    """Wrap contiguous scripture-verdict prose runs into scripture quote blocks.

    The unfenced-recall sibling of `fold_quote_registers`: same run shape
    (interior empties continue a run, never open or close it), applied to
    body paragraphs whose own text carries canonical-quotation evidence.
    Runs after lineation, so Q1 verdicts and verse decisions are untouched;
    per-ordinal observers keep coverage through the wrapper (members are
    claimed recursively)."""
    verdicts = _scripture_verdicts(blocks)
    if not verdicts:
        return blocks
    out: list[ir.Block] = []
    i = 0
    n = len(blocks)
    while i < n:
        first = blocks[i]
        if i not in verdicts:
            out.append(first)
            i += 1
            continue
        assert isinstance(first, ir.Paragraph)
        members: list[ir.Paragraph] = [first]
        j = i + 1
        pending: list[ir.Paragraph] = []
        while j < n:
            nxt = blocks[j]
            if not isinstance(nxt, ir.Paragraph):
                break
            if nxt.empty:
                pending.append(nxt)
                j += 1
                continue
            if j not in verdicts:
                break
            members.extend(pending)
            pending.clear()
            members.append(nxt)
            j += 1
        out.append(ir.QuoteBlock(
            blocks=list(members),
            register=ir.Register.SCRIPTURE,
            source_span=ir.merge_source_spans(
                p.source_span for p in members if not p.empty
            ),
        ))
        out.extend(pending)
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
    elevated verse register. Strict: a PARTIALLY dash-led run is kept, because
    anaphoric litanies inside oracle passages mix dash lines with framing
    verse lines."""
    body = lines[1:] if lines and lines[0].rstrip().endswith(":") else lines
    return len(body) >= 2 and all(_DASH_LINE_RE.match(line) for line in body)


# ---------------------------------------------------------------------------
# mixed-run segmentation: split one lineated run at register boundaries
# ---------------------------------------------------------------------------


class SpanLabel(StrEnum):
    """A per-line register class inside one lineated run — the segmentation
    vocabulary. `VERSE` keeps the run's register; `SCAFFOLD` resolves to
    `ORDINARY` (math/enumeration is never the verse register)."""

    VERSE = "verse"
    SCAFFOLD = "scaffold"


_REGISTER_BY_LABEL: dict[SpanLabel, ir.Register] = {
    SpanLabel.VERSE: ir.Register.VERSE,
    SpanLabel.SCAFFOLD: ir.Register.ORDINARY,
}

# A scaffold sub-run must reach this many contiguous lines (or wholly own its
# stanza) to split out; a single dash line inside a litany is verse texture
# (see `is_dash_scaffold`).
_SCAFFOLD_SUBRUN_MIN = 2


def scaffold_line_labeler(block: ir.LineatedBlock) -> Callable[[ir.Line], SpanLabel]:
    """The rules-only line classifier for `block`, from the two scaffold
    classes that already exist as run predicates.

    Equation lines are scaffold on their own (math is never the verse
    register). Dash lines are scaffold only when their WHOLE stanza is a dash
    scaffold (`is_dash_scaffold`, colon opener included): a dash line mixed
    with verse lines inside one stanza is litany/dialogue texture, the exact
    shape the looser per-line dash demotion was refuted on."""
    dash_stanza_lines: set[int] = set()
    for stanza in block.stanzas:
        texts = [t for line in stanza if (t := inline_plain(line.inlines))]
        if is_dash_scaffold(texts):
            dash_stanza_lines.update(id(line) for line in stanza)

    def label(line: ir.Line) -> SpanLabel:
        text = inline_plain(line.inlines)
        if text and _is_equation_line(text):
            return SpanLabel.SCAFFOLD
        if id(line) in dash_stanza_lines:
            return SpanLabel.SCAFFOLD
        return SpanLabel.VERSE

    return label


def _segment_labels(
    block: ir.LineatedBlock,
    label_of: Callable[[ir.Line], SpanLabel],
) -> list[SpanLabel]:
    """Per-line labels over the flattened run, islands resolved: a scaffold
    sub-run below `_SCAFFOLD_SUBRUN_MIN` lines that is not itself a whole
    stanza rejoins the run's verse label."""
    stanza_of = [si for si, stanza in enumerate(block.stanzas) for _ in stanza]
    labels = [label_of(line) for stanza in block.stanzas for line in stanza]
    stanza_sizes = [len(stanza) for stanza in block.stanzas]
    i = 0
    while i < len(labels):
        if labels[i] is not SpanLabel.SCAFFOLD:
            i += 1
            continue
        j = i
        while j < len(labels) and labels[j] is SpanLabel.SCAFFOLD:
            j += 1
        whole_stanza = j - i == 1 and stanza_sizes[stanza_of[i]] == 1
        if j - i < _SCAFFOLD_SUBRUN_MIN and not whole_stanza:
            labels[i:j] = [SpanLabel.VERSE] * (j - i)
        i = j
    return labels


def _segment_fragment(
    parent: ir.LineatedBlock,
    members: list[tuple[int, ir.Line]],
    label: SpanLabel,
) -> ir.LineatedBlock:
    """One fragment of a split run: member lines regrouped into stanzas at the
    parent's stanza boundaries, evidence copied (all fragments share the Q1
    fold), span merged from member line spans."""
    stanzas: ir.LineatedStanzas = []
    current: ir.Stanza = []
    current_si: int | None = None
    for si, line in members:
        if current and si != current_si:
            stanzas.append(current)
            current = []
        current_si = si
        current.append(line)
    if current:
        stanzas.append(current)
    return ir.LineatedBlock(
        stanzas=stanzas,
        register=_REGISTER_BY_LABEL[label],
        evidence=parent.evidence,
        source_span=ir.merge_source_spans(line.span for _, line in members),
    )


def segment_lineated(
    block: ir.LineatedBlock,
    label_of: Callable[[ir.Line], SpanLabel],
) -> list[ir.Block]:
    """Split one lineated run at register boundaries.

    Classifies every line, groups maximal contiguous same-label runs (a stanza
    splits mid-stanza only where labels differ inside it), and returns one
    `LineatedBlock` per run: register from the run's label, evidence copied
    from the parent, `source_span` merged from member `Line.span`s. A
    label-uniform run keeps the whole block (and its fold-derived span), only
    resolving its register.

    Fragments tile the parent's span: a fragment extends through the source
    gap rows up to the next fragment's start (the fold's trailing-empties
    convention), so the per-ordinal surfaces keep the parent's coverage
    through the split.
    """
    indexed = [
        (si, line) for si, stanza in enumerate(block.stanzas) for line in stanza
    ]
    labels = _segment_labels(block, label_of)
    if len(set(labels)) <= 1:
        register = _REGISTER_BY_LABEL[labels[0]] if labels else block.register
        return [
            block if register is block.register
            else replace(block, register=register)
        ]
    fragments: list[ir.LineatedBlock] = []
    start = 0
    for end in range(1, len(indexed) + 1):
        if end == len(indexed) or labels[end] is not labels[start]:
            fragments.append(
                _segment_fragment(block, indexed[start:end], labels[start])
            )
            start = end
    return list(_tile_fragment_spans(fragments))


def _tile_fragment_spans(
    fragments: list[ir.LineatedBlock],
) -> Iterator[ir.LineatedBlock]:
    """Extend each fragment's span through the gap rows before the next
    fragment with proven provenance; spanless fragments stay spanless."""
    starts = [f.source_span.start if f.source_span else None for f in fragments]
    for i, fragment in enumerate(fragments):
        span = fragment.source_span
        next_start = next(
            (s for s in starts[i + 1:] if s is not None), None,
        )
        if span is None or next_start is None or next_start <= span.end + 1:
            yield fragment
            continue
        yield replace(
            fragment, source_span=ir.SourceSpan(span.start, next_start - 1),
        )


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
    lineated = sum(1 for b in blocks if isinstance(b, ir.LineatedBlock))
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


def lineated_lines(block: ir.LineatedBlock) -> list[str]:
    """The block's non-empty plain display lines."""
    return [
        text
        for stanza in block.stanzas
        for line in stanza
        if (text := inline_plain(line.inlines))
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
        size for st in stanzas if (size := sum(1 for line in st if inline_plain(line.inlines)))
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
    """The exported model artifact, or ``None`` when not shipped.

    Validates eagerly so a malformed artifact fails at the load site with a
    contextual error, never later inside ``probability``."""
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        features = tuple(raw["features"])
        mean = tuple(float(x) for x in raw["mean"])
        std = tuple(float(x) for x in raw["std"])
        coef = tuple(float(x) for x in raw["coef"])
        model = RegisterModel(
            version=int(raw.get("version", 0)),
            langs=tuple(raw.get("langs", ())),
            features=features,
            mean=mean,
            std=std,
            coef=coef,
            intercept=float(raw["intercept"]),
            threshold=float(raw["threshold"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"malformed register model artifact {path}: {exc}") from exc
    if model.features != FEATURE_NAMES:
        raise ValueError(
            f"register model artifact {path}: feature schema drifted from the producer"
        )
    if not (len(model.mean) == len(model.std) == len(model.coef) == len(model.features)):
        raise ValueError(f"register model artifact {path}: vector lengths disagree")
    if any(sd <= 0 for sd in model.std):
        raise ValueError(f"register model artifact {path}: non-positive feature std")
    return model


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
    decided = _promote(doc.blocks, model, feats_ctx, stats)
    if model is not None:
        # The rules-only re-run exists for the diagnostic below; ~2x this pass's
        # cost, accepted for batch CLI (coda merges depend on verdicts, so a
        # cheaper per-block comparison would miscount).
        def verse_count(blocks: list[ir.Block]) -> int:
            return sum(
                1 for b in blocks
                if isinstance(b, ir.LineatedBlock) and b.register is ir.Register.VERSE
            )

        with_model = verse_count(decided)
        rules_only = verse_count(_promote(doc.blocks, None, {}, None))
        if with_model != rules_only:
            ctx.diagnostics.append(ir.Diagnostic(
                "info", "register.model",
                f"register model v{model.version}: {with_model} verse blocks "
                f"(rules alone: {rules_only})",
            ))
    return replace(doc, blocks=decided)


def promote_verse_register(blocks: list[ir.Block]) -> list[ir.Block]:
    """Ladder-only promotion: the rule policy with no model injected."""
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
        if isinstance(b, ir.LineatedBlock) and b.register is ir.Register.VERSE:
            if (segment := _lineated_coda_segment(blocks, i + 1, b)) is not None:
                verse, next_i = segment
                out.extend(segment_lineated(verse, scaffold_line_labeler(verse)))
                ctx = _NEUTRAL_CONTEXT
                i = next_i
                continue
            ctx = _NEUTRAL_CONTEXT
            out.extend(segment_lineated(b, scaffold_line_labeler(b)))
            i += 1
            continue
        if isinstance(b, ir.LineatedBlock) and b.register is ir.Register.ORDINARY:
            if _verdict(b, ctx, model, feats_ctx, stats):
                verse = replace(b, register=ir.Register.VERSE)
                if (segment := _lineated_coda_segment(blocks, i + 1, verse)) is not None:
                    verse, next_i = segment
                    out.extend(segment_lineated(verse, scaffold_line_labeler(verse)))
                    ctx = _NEUTRAL_CONTEXT
                    i = next_i
                    continue
                out.extend(segment_lineated(verse, scaffold_line_labeler(verse)))
            else:
                out.append(b)
            ctx = _NEUTRAL_CONTEXT
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
    lines = lineated_lines(block)
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
    i, saw_gap = skip_empty_paragraphs(blocks, i)
    if not saw_gap:
        return None

    first = blocks[i] if i < len(blocks) else None
    if not isinstance(first, ir.LineatedBlock) or first.register is not ir.Register.ORDINARY:
        return None
    i += 1

    coda_lines = lineated_lines(first)
    if not is_compact_coda(coda_lines):
        return None
    if any(CODA_PSEUDO_HEADING_RE.match(line) for line in coda_lines):
        return None

    boundary_i, _saw_trailing_gap = skip_empty_paragraphs(blocks, i)
    boundary = blocks[boundary_i] if boundary_i < len(blocks) else None
    if not isinstance(boundary, (ir.Heading, ir.ThematicBreak)):
        return None

    return first, boundary_i


def _lineated_coda_segment(
    blocks: list[ir.Block],
    i: int,
    prev: ir.LineatedBlock,
) -> tuple[ir.LineatedBlock, int] | None:
    """`prev` extended with the coda segment found at `i`, or `None`."""
    candidate = _lineated_coda_candidate(blocks, i)
    if candidate is None:
        return None
    coda, next_i = candidate
    merged = replace(
        prev,
        stanzas=[*prev.stanzas, *coda.stanzas],
        source_span=ir.merge_source_spans((prev.source_span, coda.source_span)),
    )
    return merged, next_i


def _kind_for_lines(
    lines: list[str],
    evidence: ir.LineationEvidence,
    ctx: _PrecedingContext,
) -> ir.Register | None:
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
        return ir.Register.VERSE if _passes(150) else None
    if ctx.separator and len(lines) <= 32:
        return ir.Register.VERSE if _passes(110, 160) else None
    if ctx.heading and len(lines) <= 32:
        return ir.Register.VERSE if _passes(95, 150) else None
    if evidence.compact_callout:
        return None
    if evidence.hard_break:
        return ir.Register.VERSE
    if evidence.stanza_break and len(lines) >= 3 and _passes(120):
        return ir.Register.VERSE
    if evidence.inferred_source_rows and len(lines) >= 3 and _passes(95, 120):
        return ir.Register.VERSE
    return None
