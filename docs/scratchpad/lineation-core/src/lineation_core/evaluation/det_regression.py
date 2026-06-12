# research-pure: scores the production importer's lineation verdict against every committed truth.
"""The deterministic regression — the gate every converter-rule change must pass.

The production importer is the corpus's free tier-0 labeler (`docx_inspect.lineation_decisions`,
read per source ordinal). Its verified asymmetry — when it says "lineated" it is essentially
never wrong; its error mass is verse it failed to detect — is the load-bearing beam of the
budget ladder, so any `pancratius/` change that could move these numbers re-runs this scoring
and must keep prose-recall at its floor while never regressing the easy sets.

Scores against the four committed truth sets: the trainable labels plus the three frozen eval
slices. A truth line whose ordinal has no verdict is counted `uncovered`, never guessed.
Pure given the truth + per-book verdict maps; `score_all` is the IO shell (labels + DOCX, no
records). `python -m lineation_core.evaluation.det_regression` prints the table;
`tests/test_det_regression.py` pins the floors."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache

from .. import paths
from ..annotations import load_labels
from ..identity import Label, LineId
from .datasets import eval_slice
from .metrics import Metrics, balanced

EVAL_SLICES = ("reader_bench", "contested", "prompt_structural")


@lru_cache(maxsize=None)
def _book_decisions(lang: str, book_id: str) -> Mapping[int, bool]:
    from pancratius.docx_inspect import lineation_decisions

    return lineation_decisions(paths.book_docx(book_id, lang))


@dataclass(frozen=True, slots=True)
class DetScore:
    """The importer verdict scored against one truth set. `n` counts the scored (covered)
    lines; `n_uncovered` the truth lines the importer has no verdict for."""

    name: str
    n: int
    n_uncovered: int
    metrics: Metrics


def score_truth(name: str, truth: Mapping[LineId, Label]) -> DetScore:
    y_true: list[Label] = []
    y_pred: list[Label] = []
    uncovered = 0
    for lid, label in sorted(truth.items()):
        hit = _book_decisions(lid.lang, lid.book_id).get(lid.src_ordinal)
        if hit is None:
            uncovered += 1
            continue
        y_true.append(label)
        y_pred.append("lineated" if hit else "prose")
    return DetScore(name=name, n=len(y_true), n_uncovered=uncovered,
                    metrics=balanced(y_true, y_pred))


def score_all() -> list[DetScore]:
    """All four truth sets, trainable labels first. The eval slices join through the one truth
    store (`eval_slice` fails loud on a member with no label)."""
    sets: dict[str, Mapping[LineId, Label]] = {
        "trainable-gold": {g.id: g.label for g in load_labels().trainable},
    }
    for name in EVAL_SLICES:
        sets[name] = eval_slice(name).truth
    return [score_truth(n, t) for n, t in sets.items()]


if __name__ == "__main__":
    print(f"{'set':>18} {'n':>6} {'uncov':>6} {'balAcc':>7} {'acc':>7} "
          f"{'proseRec':>9} {'linRec':>7}")
    for s in score_all():
        m = s.metrics
        print(f"{s.name:>18} {s.n:>6} {s.n_uncovered:>6} {m.balanced_acc:>7.3f} "
              f"{m.acc:>7.3f} {m.prose_recall:>9.3f} {m.lineated_recall:>7.3f}")
