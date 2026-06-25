# import-pure: no filesystem mutation
"""Display-register passes (Q2) — the one home for register decisions.

`fold_quote_registers` lifts authored set-apart gestures into typed quote
blocks: the author marks passages with paragraph borders (`w:pBdr`) — a full
four-side box frames quoted canonical text, a left rule bars an inset passage
in another voice. Border evidence is within-book contrastive: a kind covering
a large share of the book's text paragraphs is the book's own frame, not a
set-apart gesture.

`assign_register` decides the verse register over lineated blocks through the
production intent-inference policy seam. This pass collects candidates,
materializes returned decisions, and segments verse runs (`segment_lineated`):
scaffold sub-runs (equations, dash enumerations) split out as `ORDINARY`
fragments with honest line-derived spans.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING

from pancratius import ir
from pancratius.intent_inference.decisions import (
    DecisionOutcome,
    DisplayRegisterLabel,
    IntentDiagnostic,
    RegisterDecision,
    RegisterDecisionReason,
)
from pancratius.intent_inference.observations import (
    RegisterCandidate,
    RegisterDocumentContext,
    RegisterModelContext,
    RegisterObservation,
    RegisterRuleContext,
    RegisterRuleEvaluation,
    lineated_plain_lines,
    register_book_stats,
    stable_register_candidate_id,
    stanza_line_counts,
)
from pancratius.intent_inference.policies import RulesOnlyRegisterPolicy
from pancratius.ir.inlines import inline_plain
from pancratius.locales import Locale
from pancratius.passes.lineation import (
    CODA_PSEUDO_HEADING_RE,
    VERSE_SHORT_LINE_MAX,
    is_compact_coda,
    is_verse_candidate_line,
    skip_empty_paragraphs,
)

if TYPE_CHECKING:
    from pancratius.passes.context import Context

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


@dataclass(frozen=True, slots=True)
class TrailingCitationSplit:
    body: str
    citation: str


def _split_trailing_cite(text: str) -> TrailingCitationSplit:
    """Split a trailing parenthetical citation from the quote body."""
    text = text.strip()
    body = text.rstrip(_QUOTE_TRAIL)
    m = _TRAILING_CITE_RE.search(body)
    if not m:
        return TrailingCitationSplit(body=text, citation="")
    return TrailingCitationSplit(
        body=body[: m.start()].rstrip(_QUOTE_TRAIL),
        citation=m.group(1),
    )


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
    split = _split_trailing_cite(text)
    if _is_whole_quote(split.body):
        if _SPEECH_ANCHOR_RE.match(split.body):
            return True
        if _SCRIPTURE_CITE_RE.search(split.body) or _SCRIPTURE_CITE_RE.search(split.citation):
            return True
    if (m := _REF_LED_QUOTE_RE.match(text)) is not None:
        led_split = _split_trailing_cite(text[m.end():])
        if _is_whole_quote(led_split.body):
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
    quotes = {i for i in texts if _is_whole_quote(_split_trailing_cite(texts[i]).body)}
    for run in runs:
        for pos, i in enumerate(run):
            if i not in quotes:
                continue
            neighbors = [run[p] for p in (pos - 1, pos + 1) if 0 <= p < len(run)]
            if any(n in cites for n in neighbors):
                verdicts.add(i)
                verdicts.update(n for n in neighbors if n in cites)
    return verdicts


def _in_verse_pin_ordinals(blocks: list[ir.Block]) -> set[int]:
    """Pin ordinals already wrapped as scripture INSIDE a lineated run — the
    per-line scripture quotes `segment_lineated` split out as scripture
    `QuoteBlock`s whose member is a `LineatedBlock`. Those pins were honored
    upstream (`assign_register`), so they are not stray for this prose pass."""
    ordinals: set[int] = set()
    for b in blocks:
        if not (isinstance(b, ir.QuoteBlock) and b.register is ir.Register.SCRIPTURE):
            continue
        for member in b.blocks:
            if isinstance(member, ir.LineatedBlock):
                for stanza in member.stanzas:
                    for line in stanza:
                        ordinals.update(_line_ordinals(line))
    return ordinals


def _pinned_verdicts(blocks: list[ir.Block], pinned: Mapping[int, str]) -> set[int]:
    """Block indexes claimed by sidecar pins: a top-level non-empty paragraph
    whose source span covers a pinned ordinal. A pin no PROSE ordinal claims and
    no in-verse scripture quote already wraps FAILS LOUD — the pinned paragraph
    moved out of prose (merged, dropped, or onto an unexpected substrate), so the
    adjudicated verdict no longer lands where it was made. A pin whose line was
    split out as an in-verse scripture quote is already honored upstream, not
    stray."""
    verdicts: set[int] = set()
    claimed: set[int] = set()
    for i, b in enumerate(blocks):
        if not (isinstance(b, ir.Paragraph) and not b.empty and b.source_span):
            continue
        # A top-level Paragraph owns one ordinal today; the range covers the
        # unexpected-merge case so a pin inside a fused span still claims it.
        hits = {o for o in range(b.source_span.start, b.source_span.end + 1) if o in pinned}
        if hits:
            verdicts.add(i)
            claimed.update(hits)
    claimed |= _in_verse_pin_ordinals(blocks) & set(pinned)
    if stray := set(pinned) - claimed:
        raise ValueError(
            f"scripture pins {sorted(stray)} claim no scripture paragraph or in-verse "
            f"quote — the source or the pipeline moved under the sidecar; re-adjudicate"
        )
    return verdicts


def wrap_scripture(
    blocks: list[ir.Block],
    pinned: Mapping[int, str] | None = None,
) -> list[ir.Block]:
    """Wrap contiguous scripture-verdict prose runs into scripture quote blocks.

    The unfenced-recall sibling of `fold_quote_registers`: same run shape
    (interior empties continue a run, never open or close it), applied to
    body paragraphs whose own text carries canonical-quotation evidence — plus
    the sidecar-pinned paragraphs (`scripture.<lang>.json`): unmarked canonical
    quotations adjudicated by source knowledge, which no text rule can carry.
    Runs after lineation, so Q1 verdicts and verse decisions are untouched;
    per-ordinal observers keep coverage through the wrapper (members are
    claimed recursively)."""
    verdicts = _scripture_verdicts(blocks)
    if pinned:
        verdicts |= _pinned_verdicts(blocks, pinned)
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
    `ORDINARY` (math/enumeration is never the verse register); `SCRIPTURE`
    splits a contiguous run of canonical-quote lines out as a scripture quote
    block (the same wrapper `wrap_scripture` produces for prose, applied at
    line grain inside a lineated run)."""

    VERSE = "verse"
    SCAFFOLD = "scaffold"
    SCRIPTURE = "scripture"


# A scaffold sub-run must reach this many contiguous lines (or wholly own its
# stanza) to split out; a single dash line inside a litany is verse texture
# (see `is_dash_scaffold`).
_SCAFFOLD_SUBRUN_MIN = 2


def _line_ordinals(line: ir.Line) -> range:
    """The source `w:p` ordinals a line covers (empty when spanless)."""
    if line.span is None:
        return range(0, 0)
    return range(line.span.start, line.span.end + 1)


def _is_cited_quote_line(text: str) -> bool:
    """A single display line that IS a whole canonical quotation naming its own
    provenance — the `is_scripture_quote` channels applied at line grain (a
    speech-introduction logion, a citation token in the quote or a trailing
    parenthetical, a leading citation). The recall sibling of `wrap_scripture`'s
    prose channel for a quote line standing inside a lineated run; bare «…»
    lines (no citation, no source-naming) are NOT evidence here either."""
    return is_scripture_quote(text)


def _is_ref_line(text: str) -> bool:
    """A bare scripture-citation line (`Сура 4:157`, `Ин. 4:23`) standing as its
    own display line inside the run — the line-grain `_is_bare_cite`."""
    return _is_bare_cite(text)


def _whole_quote_line_indices(texts: list[str]) -> set[int]:
    """Flat-line indices whose text is one whole «…» quotation (no citation
    required) — the cite-adjacency channel's candidate lines."""
    return {
        i for i, t in enumerate(texts)
        if t and _is_whole_quote(_split_trailing_cite(t).body)
    }


type _LinePosition = tuple[int, int]
type _LineLabeler = Callable[[int, int, ir.Line], SpanLabel]


def _scripture_line_positions(
    block: ir.LineatedBlock,
    pinned: frozenset[int],
) -> set[_LinePosition]:
    """Positions of every line that lowers as scripture inside `block`.

    Three channels, mirroring the prose `wrap_scripture`:
    * a pinned source ordinal (sidecar canon-knowledge — the LUPI rail no text
      rule can carry; an in-verse quote line whose ordinal the round adjudicated);
    * a cited/anchored whole-quote line (`is_scripture_quote` at line grain);
    * cite-adjacency — a whole «…» quote line whose neighboring display line
      (an equation/blank line is transparent to neither; immediate neighbor) is
      a bare citation line."""
    flat = [
        (si, li, line)
        for si, stanza in enumerate(block.stanzas)
        for li, line in enumerate(stanza)
    ]
    texts = [inline_plain(line.inlines) for _si, _li, line in flat]
    positions: set[_LinePosition] = set()
    for (si, li, line), text in zip(flat, texts, strict=True):
        if pinned and any(o in pinned for o in _line_ordinals(line)):
            positions.add((si, li))
        elif text and _is_cited_quote_line(text):
            positions.add((si, li))
    quotes = _whole_quote_line_indices(texts)
    refs = {i for i, t in enumerate(texts) if t and _is_ref_line(t)}
    for i in quotes:
        if any(0 <= j < len(flat) and j in refs for j in (i - 1, i + 1)):
            positions.add((flat[i][0], flat[i][1]))
            positions.update(
                (flat[j][0], flat[j][1]) for j in (i - 1, i + 1) if j in refs
            )
    return positions


def scaffold_line_labeler(
    block: ir.LineatedBlock, scripture_pins: frozenset[int] = frozenset(),
) -> _LineLabeler:
    """The rules-only line classifier for `block`, from the scaffold and
    scripture line-classes that already exist as run/prose predicates.

    Equation lines are scaffold on their own (math is never the verse
    register). Dash lines are scaffold only when their WHOLE stanza is a dash
    scaffold (`is_dash_scaffold`, colon opener included): a dash line mixed
    with verse lines inside one stanza is litany/dialogue texture, the exact
    shape the looser per-line dash demotion was refuted on. Scaffold wins over
    scripture (an equation line is never a canonical quote). `scripture_pins`
    are source ordinals adjudicated as in-verse canon; with the line-grain
    citation channels they mark scripture quote lines."""
    dash_stanza_lines: set[_LinePosition] = set()
    for si, stanza in enumerate(block.stanzas):
        texts = [t for line in stanza if (t := inline_plain(line.inlines))]
        if is_dash_scaffold(texts):
            dash_stanza_lines.update((si, li) for li, _line in enumerate(stanza))
    scripture_lines = _scripture_line_positions(block, scripture_pins)

    def label(stanza_index: int, line_index: int, line: ir.Line) -> SpanLabel:
        text = inline_plain(line.inlines)
        if text and _is_equation_line(text):
            return SpanLabel.SCAFFOLD
        if (stanza_index, line_index) in dash_stanza_lines:
            return SpanLabel.SCAFFOLD
        if (stanza_index, line_index) in scripture_lines:
            return SpanLabel.SCRIPTURE
        return SpanLabel.VERSE

    return label


def _segment_labels(
    block: ir.LineatedBlock,
    label_of: _LineLabeler,
) -> list[SpanLabel]:
    """Per-line labels over the flattened run, islands resolved: a scaffold
    sub-run below `_SCAFFOLD_SUBRUN_MIN` lines that is not itself a whole
    stanza rejoins the run's verse label."""
    stanza_of = [si for si, stanza in enumerate(block.stanzas) for _ in stanza]
    labels = [
        label_of(si, li, line)
        for si, stanza in enumerate(block.stanzas)
        for li, line in enumerate(stanza)
    ]
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


def _fragment_stanzas(members: list[tuple[int, ir.Line]]) -> ir.LineatedStanzas:
    """Member lines regrouped into stanzas at the parent's stanza boundaries."""
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
    return stanzas


# A segmentation fragment carries proven provenance: a verse/scaffold
# `LineatedBlock` or a scripture `QuoteBlock`. Both expose `source_span`, so the
# span-tiler stays total over the fragment kinds.
type _Fragment = ir.LineatedBlock | ir.QuoteBlock


def _segment_fragment(
    parent: ir.LineatedBlock,
    members: list[tuple[int, ir.Line]],
    label: SpanLabel,
) -> _Fragment:
    """One fragment of a split run: member lines regrouped into stanzas at the
    parent's stanza boundaries, evidence copied (all fragments share the Q1
    fold), span merged from member line spans.

    A `SCRIPTURE` fragment lowers as a scripture quote — the same `QuoteBlock`
    `wrap_scripture` emits for prose — wrapping the quote lines as a base
    `lineated` member so authored line breaks survive inside the apparatus. A
    `VERSE` fragment KEEPS the parent's register (verse stays verse; an ordinary
    run whose only split was a scripture line stays ordinary), a `SCAFFOLD`
    fragment demotes to `ORDINARY` — math/enumeration is never elevated."""
    stanzas = _fragment_stanzas(members)
    span = ir.merge_source_spans(line.span for _, line in members)
    if label is SpanLabel.SCRIPTURE:
        member = ir.LineatedBlock(
            stanzas=stanzas, register=ir.Register.ORDINARY,
            evidence=parent.evidence, source_span=span,
        )
        return ir.QuoteBlock(
            blocks=[member], register=ir.Register.SCRIPTURE, source_span=span,
        )
    register = parent.register if label is SpanLabel.VERSE else ir.Register.ORDINARY
    return ir.LineatedBlock(
        stanzas=stanzas,
        register=register,
        evidence=parent.evidence,
        source_span=span,
    )


def segment_lineated(
    block: ir.LineatedBlock,
    label_of: _LineLabeler,
) -> list[ir.Block]:
    """Split one lineated run at register boundaries.

    Classifies every line, groups maximal contiguous same-label runs (a stanza
    splits mid-stanza only where labels differ inside it), and returns one
    fragment per run: a `LineatedBlock` (register from the run's label) for a
    verse/scaffold run, a scripture `QuoteBlock` for a scripture run; evidence
    copied from the parent, `source_span` merged from member `Line.span`s. A
    uniform verse/scaffold run keeps the whole block (and its fold-derived
    span), only resolving its register; a uniform scripture run still rebuilds
    as a quote block.

    Fragments tile the parent's span: a fragment extends through the source
    gap rows up to the next fragment's start (the fold's trailing-empties
    convention), so the per-ordinal surfaces keep the parent's coverage
    through the split.
    """
    indexed = [
        (si, line) for si, stanza in enumerate(block.stanzas) for line in stanza
    ]
    labels = _segment_labels(block, label_of)
    if len(set(labels)) <= 1 and (not labels or labels[0] is not SpanLabel.SCRIPTURE):
        # A uniform run keeps its block. `VERSE` keeps the parent's register;
        # `SCAFFOLD` demotes to ORDINARY (math/enumeration is never elevated).
        register = (
            block.register
            if not labels or labels[0] is SpanLabel.VERSE
            else ir.Register.ORDINARY
        )
        return [
            block if register is block.register
            else replace(block, register=register)
        ]
    fragments: list[_Fragment] = []
    start = 0
    for end in range(1, len(indexed) + 1):
        if end == len(indexed) or labels[end] is not labels[start]:
            fragments.append(
                _segment_fragment(block, indexed[start:end], labels[start])
            )
            start = end
    return list(_tile_fragment_spans(fragments))


def _tile_fragment_spans(
    fragments: list[_Fragment],
) -> Iterator[_Fragment]:
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


_NUM_LEAD_RE = re.compile(r"^\d{1,4}[.:)\s]")


def iter_with_register_context(
    blocks: list[ir.Block],
) -> Iterator[tuple[int, ir.Block, RegisterModelContext]]:
    """Yield every block with its register context — the one walker the teacher
    extraction and this pass both read."""
    ctx = RegisterModelContext()
    for i, b in enumerate(blocks):
        yield i, b, ctx
        if isinstance(b, ir.Heading):
            ctx = RegisterModelContext(
                heading=True,
                named=is_verse_section_title(inline_plain(b.inlines)),
            )
        elif isinstance(b, ir.ThematicBreak):
            ctx = RegisterModelContext(separator=True)
        elif isinstance(b, ir.Paragraph) and b.empty:
            pass  # blank rows are transparent
        else:
            ctx = RegisterModelContext()


def lineated_lines(block: ir.LineatedBlock) -> list[str]:
    """The block's non-empty plain display lines."""
    return list(lineated_plain_lines(block))


# ---------------------------------------------------------------------------
# Q2b: the verse-register decision over lineated blocks
# ---------------------------------------------------------------------------


_NEUTRAL_CONTEXT = RegisterRuleContext()


def assign_register(doc: ir.Document, ctx: Context) -> ir.Document:
    """The Q2 pass: decide the verse register for every lineated block."""
    pins = frozenset(ctx.scripture.by_ordinal)
    candidates = _register_candidates(doc.blocks, ctx.lang, pins)
    document_context = RegisterDocumentContext(lang=ctx.lang)
    decisions = ctx.register_policy.decide_document(candidates, document_context)
    ctx.diagnostics.extend(
        _intent_diagnostic_to_ir(diagnostic)
        for decision in decisions
        for diagnostic in decision.diagnostics
    )
    decided = _promote(doc.blocks, _decision_plan(candidates, decisions), pins)
    if ctx.register_policy.reports_model_delta:
        # The rules-only re-run exists for the diagnostic below; ~2x this pass's
        # cost, accepted for batch CLI (coda merges depend on verdicts, so a
        # cheaper per-block comparison would miscount).
        def verse_count(blocks: list[ir.Block]) -> int:
            return sum(
                1 for b in blocks
                if isinstance(b, ir.LineatedBlock) and b.register is ir.Register.VERSE
            )

        with_model = verse_count(decided)
        rules_decisions = RulesOnlyRegisterPolicy().decide_document(
            candidates,
            document_context,
        )
        rules_only = verse_count(_promote(
            doc.blocks,
            _decision_plan(candidates, rules_decisions),
            pins,
        ))
        if with_model != rules_only:
            model_version = ctx.register_policy.model_version
            if model_version is None:
                raise ValueError("register policy requested model diagnostics without a model version")
            ctx.diagnostics.append(ir.Diagnostic(
                "info", "register.model",
                f"register model v{model_version}: {with_model} verse blocks "
                f"(rules alone: {rules_only})",
            ))
    return replace(doc, blocks=decided)


def _intent_diagnostic_to_ir(diagnostic: IntentDiagnostic) -> ir.Diagnostic:
    return ir.Diagnostic(
        diagnostic.severity.value,
        f"register.{diagnostic.code.value}",
        diagnostic.message,
    )


def promote_verse_register(blocks: list[ir.Block]) -> list[ir.Block]:
    """Ladder-only promotion: the rule policy with no model injected."""
    policy = RulesOnlyRegisterPolicy()
    candidates = _register_candidates(blocks, "ru", frozenset())
    decisions = policy.decide_document(candidates, RegisterDocumentContext(lang="ru"))
    return _promote(blocks, _decision_plan(candidates, decisions), frozenset())


def _block_pins(block: ir.LineatedBlock, pins: frozenset[int]) -> frozenset[int]:
    """The subset of `pins` whose ordinal a line of `block` covers — the
    in-verse canon pins this block owns. Empty when none, so the labeler's
    scripture channels run only where a pin or a citation could match."""
    if not pins:
        return frozenset()
    covered = {
        o
        for stanza in block.stanzas
        for line in stanza
        for o in _line_ordinals(line)
    }
    return frozenset(pins & covered)


def _segment(block: ir.LineatedBlock, pins: frozenset[int]) -> list[ir.Block]:
    """Segment a lineated run with the pin-aware scripture labeler."""
    return segment_lineated(block, scaffold_line_labeler(block, _block_pins(block, pins)))


def _has_scripture_line(block: ir.LineatedBlock, pins: frozenset[int]) -> bool:
    """Whether `block` carries any scripture quote line (pin or citation
    channel) — the gate for splitting an un-promoted ordinary run."""
    return bool(_scripture_line_positions(block, _block_pins(block, pins)))


def _register_candidates(
    blocks: list[ir.Block],
    lang: Locale,
    pins: frozenset[int],
) -> tuple[RegisterCandidate, ...]:
    book = register_book_stats(blocks)
    model_context_by_index = {
        i: register_ctx for i, _block, register_ctx in iter_with_register_context(blocks)
    }
    out: list[RegisterCandidate] = []
    rule_ctx = _NEUTRAL_CONTEXT
    candidate_ordinal = 0
    for i, block in enumerate(blocks):
        if isinstance(block, ir.Heading):
            title = inline_plain(block.inlines)
            rule_ctx = RegisterRuleContext(
                named=is_verse_section_title(title),
                heading=True,
            )
            continue
        if isinstance(block, ir.ThematicBreak):
            rule_ctx = RegisterRuleContext(separator=True)
            continue
        if isinstance(block, ir.LineatedBlock) and block.register is ir.Register.ORDINARY:
            label_of = scaffold_line_labeler(block, _block_pins(block, pins))
            view = _verse_candidate_view(block, label_of)
            candidate_id = stable_register_candidate_id(
                block,
                source_block_index=i,
                candidate_ordinal=candidate_ordinal,
            )
            out.append(RegisterCandidate(
                candidate_id=candidate_id,
                source_block_index=i,
                source_span=block.source_span,
                observation=RegisterObservation(
                    candidate_id=candidate_id,
                    lang=lang,
                    lines=lineated_plain_lines(view.view),
                    stanza_line_counts=stanza_line_counts(view.view.stanzas),
                    evidence=view.view.evidence,
                    model_context=model_context_by_index.get(i, RegisterModelContext()),
                    book=book,
                ),
                rules=_rule_evaluation(view, rule_ctx),
            ))
            candidate_ordinal += 1
            rule_ctx = _NEUTRAL_CONTEXT
            continue
        rule_ctx = _NEUTRAL_CONTEXT
    return tuple(out)


def _decision_plan(
    candidates: tuple[RegisterCandidate, ...],
    decisions: tuple[RegisterDecision, ...],
) -> dict[int, RegisterDecision]:
    by_subject = {decision.subject: decision for decision in decisions}
    if len(by_subject) != len(decisions):
        raise ValueError("register policy returned duplicate candidate decisions")
    missing = [candidate.candidate_id for candidate in candidates if candidate.candidate_id not in by_subject]
    if missing:
        raise ValueError(f"register policy returned no decision for {missing[0]}")
    extra = [subject for subject in by_subject if subject not in {c.candidate_id for c in candidates}]
    if extra:
        raise ValueError(f"register policy returned unknown decision {extra[0]}")
    return {
        candidate.source_block_index: by_subject[candidate.candidate_id]
        for candidate in candidates
    }


def _materialized_label(decision: RegisterDecision) -> DisplayRegisterLabel:
    if decision.outcome is DecisionOutcome.REFUSE_CONTRACT:
        raise ValueError(f"register policy refused contract for {decision.subject}")
    if decision.label is not None:
        return decision.label
    if decision.fallback_label is not None:
        return decision.fallback_label
    raise ValueError(f"register policy returned no materializable label for {decision.subject}")


def _promote(
    blocks: list[ir.Block],
    decisions: Mapping[int, RegisterDecision],
    pins: frozenset[int],
) -> list[ir.Block]:
    out: list[ir.Block] = []
    i = 0

    while i < len(blocks):
        b = blocks[i]
        if isinstance(b, ir.Heading):
            out.append(b)
            i += 1
            continue
        if isinstance(b, ir.ThematicBreak):
            out.append(b)
            i += 1
            continue
        if isinstance(b, ir.LineatedBlock) and b.register is ir.Register.VERSE:
            if (segment := _lineated_coda_segment(blocks, i + 1, b)) is not None:
                verse, next_i = segment
                out.extend(_segment(verse, pins))
                i = next_i
                continue
            out.extend(_segment(b, pins))
            i += 1
            continue
        if isinstance(b, ir.LineatedBlock) and b.register is ir.Register.ORDINARY:
            # The verse decision and the segmentation share ONE line labeling:
            # judge the run on the lines that will actually be verse (scaffold
            # islands dropped), then let `segment_lineated` split those islands
            # back out. A numbered/equation island no longer poisons the verdict
            # of the verse body it is embedded in.
            label_of = scaffold_line_labeler(b, _block_pins(b, pins))
            if _materialized_label(decisions[i]) is DisplayRegisterLabel.VERSE:
                verse = replace(b, register=ir.Register.VERSE)
                if (segment := _lineated_coda_segment(blocks, i + 1, verse)) is not None:
                    verse, next_i = segment
                    out.extend(_segment(verse, pins))
                    i = next_i
                    continue
                out.extend(segment_lineated(verse, label_of))
            elif _has_scripture_line(b, pins):
                # An un-promoted ordinary lineated run still splits its scripture
                # quote lines out (the rest stays base lineated): canon recall is
                # independent of the verse verdict.
                out.extend(_segment(b, pins))
            else:
                out.append(b)
            i += 1
            continue
        out.append(b)
        i += 1
    return out


@dataclass(frozen=True)
class _CandidateView:
    """A block's verse-candidate view after scaffold lines were dropped."""

    view: ir.LineatedBlock


def _verse_candidate_view(
    block: ir.LineatedBlock, label_of: _LineLabeler,
) -> _CandidateView:
    """The verse-candidate view of `block`: the same run with its scaffold
    islands removed, so the verdict reads only the lines that would actually be
    verse. `label_of` is the one labeling `segment_lineated` will split on, so
    the view and the split agree by construction. Empty stanzas are dropped."""
    stanzas = [
        kept
        for si, stanza in enumerate(block.stanzas)
        if (
            kept := [
                line
                for li, line in enumerate(stanza)
                if label_of(si, li, line) is not SpanLabel.SCAFFOLD
            ]
        )
    ]
    view = replace(block, stanzas=stanzas)
    return _CandidateView(view=view)


def _rule_evaluation(
    candidate: _CandidateView,
    ctx: RegisterRuleContext,
) -> RegisterRuleEvaluation:
    block = candidate.view
    lines = lineated_lines(block)
    if len(lines) < 2 or not all(is_verse_candidate_line(line) for line in lines):
        return RegisterRuleEvaluation(
            label=DisplayRegisterLabel.ORDINARY,
            reason=RegisterDecisionReason.HARD_GUARD,
            model_allowed=False,
        )
    if is_dash_scaffold(lines) or is_equation_scaffold(lines):
        return RegisterRuleEvaluation(
            label=DisplayRegisterLabel.ORDINARY,
            reason=RegisterDecisionReason.HARD_GUARD,
            model_allowed=False,
        )
    rules_label = (
        DisplayRegisterLabel.VERSE
        if _kind_for_lines(lines, block.evidence, ctx) is not None
        else DisplayRegisterLabel.ORDINARY
    )
    return RegisterRuleEvaluation(
        label=rules_label,
        reason=RegisterDecisionReason.RULES,
        model_allowed=not ctx.named,
    )


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
    scan = skip_empty_paragraphs(blocks, i)
    if not scan.saw_gap:
        return None

    i = scan.next_index
    first = blocks[i] if i < len(blocks) else None
    if not isinstance(first, ir.LineatedBlock) or first.register is not ir.Register.ORDINARY:
        return None
    i += 1

    coda_lines = lineated_lines(first)
    if not is_compact_coda(coda_lines):
        return None
    if any(CODA_PSEUDO_HEADING_RE.match(line) for line in coda_lines):
        return None

    boundary_i = skip_empty_paragraphs(blocks, i).next_index
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
    ctx: RegisterRuleContext,
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
