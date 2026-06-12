# import-pure: no filesystem mutation
"""Display-register pass: lift authored set-apart gestures into typed blocks.

The author sets passages apart from the running body with paragraph borders
(`w:pBdr`): a full four-side box frames quoted canonical text, and a left rule
bars an inset passage in another voice — dictation, revelation quotes,
reflection, or commentary. Pandoc drops the gesture entirely; the adapter
mines its kind onto `Paragraph.border`, and this pass groups contiguous
bordered paragraph runs into `BlockQuote(role="scripture" | "inset")`.

Border evidence is within-book contrastive: a kind covering a large share of
the book's text paragraphs is the book's own frame (a template/baseline
choice), not a set-apart gesture, and is left alone. The pass runs after
`structural_blocks` and `dialogue_labels` (right-aligned signatures/epigraphs
and speaker labels win first — a bordered bare `**Speaker:**` paragraph
becomes a label, not a one-line quote) and before lineation folding, so the
wrapped runs keep their source paragraphs intact and later passes treat each
wrapper as one opaque unit.
"""

from __future__ import annotations

from pancratius import ir

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


def display_register_blocks(blocks: list[ir.Block]) -> list[ir.Block]:
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
