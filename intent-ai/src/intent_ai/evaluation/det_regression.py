# research-pure: scores the production importer's lineation verdict against the frozen truth sets.
"""The deterministic regression — the gate every converter-rule change must pass.

The production importer is the corpus's free tier-0 labeler (`docx_structure.fold_decisions`,
read per source line). Its verified asymmetry — when it says "lineated" it is essentially
never wrong; its error mass is verse it failed to detect — is the load-bearing beam of the
budget ladder, so any `pancratius/` change that could move these numbers re-runs this scoring
and must keep prose-recall at its floor while never regressing the easy sets.

Scores FROZEN memberships only (`det-gate` — the trainable truth as of the floors' measurement —
plus the three eval slices), so truth GROWTH never moves the gate: a new label changes nothing
here, while a converter change or a member's re-adjudication moves a floor and is investigated.
A member line with no verdict is counted `uncovered`, never guessed. Pure given the
truth + per-book verdict maps; `score_all` is the IO shell (labels + DOCX, no records).
`python -m intent_ai.evaluation.det_regression` prints the table;
`tests/test_det_regression.py` pins the floors."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache

from pancratius import docx_source

from .. import paths
from ..identity import BookId, Label, LineId
from ..truth import (
    IncompleteParagraphTruth,
    MixedParagraphTruth,
    UnanimousParagraphTruth,
    reduce_paragraph_truth,
)
from .datasets import eval_slice
from .metrics import Metrics, balanced

GATE_SLICES = ("det-gate", "reader_bench", "contested", "prompt_structural")


@cache   # ≤103 books, one-shot process; stale only if a docx changes mid-run
def _book_decisions(
    lang: str, book_id: str
) -> Mapping[docx_source.SourceLineCoordinate, bool]:
    from pancratius import docx_structure
    from pancratius.locales import is_locale

    if not is_locale(lang):
        raise ValueError(f"unsupported locale {lang!r}")
    source = docx_source.read(paths.book_docx(BookId(book_id), lang))
    return docx_structure.fold_decisions(source, lang=lang)


@cache
def _book_line_counts(lang: str, book_id: str) -> Mapping[int, int]:
    from pancratius import docx_source
    from pancratius.locales import is_locale

    if not is_locale(lang):
        raise ValueError(f"unsupported locale {lang!r}")
    source = docx_source.read(paths.book_docx(BookId(book_id), lang))
    return {
        int(paragraph.ordinal): len(paragraph.natural_lines)
        for paragraph in source.paragraphs
    }


@dataclass(frozen=True, slots=True)
class DetScore:
    """Paragraph-level fold truth scored only where line truth is representable."""

    name: str
    n: int
    n_uncovered: int
    n_mixed: int
    n_incomplete: int
    metrics: Metrics


def score_truth(name: str, truth: Mapping[LineId, Label]) -> DetScore:
    y_true: list[Label] = []
    y_pred: list[Label] = []
    by_ordinal: dict[tuple[str, BookId, int], dict[int, Label]] = {}
    for line_id, label in truth.items():
        key = (line_id.lang, line_id.book_id, line_id.src_ordinal)
        by_ordinal.setdefault(key, {})[line_id.sub] = label

    uncovered = 0
    mixed = 0
    incomplete = 0
    for (lang, book_id, ordinal), by_sub in sorted(by_ordinal.items()):
        expected = _book_line_counts(lang, book_id).get(ordinal)
        if expected is None or expected <= 0:
            uncovered += 1
            continue
        match reduce_paragraph_truth(by_sub, expected_subs=expected):
            case MixedParagraphTruth():
                mixed += 1
                continue
            case IncompleteParagraphTruth():
                incomplete += 1
                continue
            case UnanimousParagraphTruth(label=label):
                pass
        decisions = _book_decisions(lang, book_id)
        hits = [
            decisions.get(
                docx_source.SourceLineCoordinate(docx_source.ParagraphOrdinal(ordinal), sub)
            )
            for sub in range(expected)
        ]
        if any(hit is None for hit in hits):
            uncovered += 1
            continue
        dispositions = {hit for hit in hits if hit is not None}
        if len(dispositions) != 1:
            mixed += 1
            continue
        hit = dispositions.pop()
        y_true.append(label)
        y_pred.append("lineated" if hit else "prose")
    return DetScore(
        name=name,
        n=len(y_true),
        n_uncovered=uncovered,
        n_mixed=mixed,
        n_incomplete=incomplete,
        metrics=balanced(y_true, y_pred),
    )


def score_all() -> list[DetScore]:
    """The four frozen gate memberships, joined through the one truth store (`eval_slice` fails
    loud on a member with no label)."""
    return [score_truth(name, eval_slice(name).truth) for name in GATE_SLICES]


if __name__ == "__main__":
    print(f"{'set':>18} {'n':>6} {'uncov':>6} {'mixed':>6} {'incomp':>6} "
          f"{'balAcc':>7} {'acc':>7} "
          f"{'proseRec':>9} {'linRec':>7}")
    for s in score_all():
        m = s.metrics
        print(f"{s.name:>18} {s.n:>6} {s.n_uncovered:>6} {s.n_mixed:>6} "
              f"{s.n_incomplete:>6} {m.balanced_acc:>7.3f} "
              f"{m.acc:>7.3f} {m.prose_recall:>9.3f} {m.lineated_recall:>7.3f}")
