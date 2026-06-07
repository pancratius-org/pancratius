# research-pure: apples-to-apples student vs LLM-teacher comparison on shared labeled lines.
"""Compare the interpretable student to the LLM teachers HONESTLY.

The teacher numbers in prior work ("grok-text ≈0.88") are on a CONTESTED subset with their
own metric, so a bare 0.956-vs-0.88 is apples-to-oranges. This module removes the confound:
it scores every panel reader AND the student against the SAME labels, on the SAME lines, with
the SAME balanced metric.

Truth, the panel votes, and the student prediction are ALL keyed by `LineId` — the one
identity — so a reader's call joins to a label and to the student directly, with no
`(rid, idx, sub)` key anywhere. The student is scored on its OUT-OF-FOLD predictions (book held
out) — never on lines its fold trained on — so the comparison is fair to the teachers (who
never saw the labels).
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass

from . import labels, panel_votes, store, student
from .identity import LineId
from .records import LineRecord

_READERS = panel_votes.READERS


@dataclass(frozen=True)
class Metrics:
    """Two-class (prose/lineated) scores for one set of predictions against one truth."""

    acc: float
    balanced_acc: float
    macro_f1: float
    prose_recall: float
    lineated_recall: float


def balanced(y_true: list[str], y_pred: list[str]) -> Metrics:
    """Balanced accuracy + macro-F1 computed from scratch (no sklearn) on prose/lineated."""
    recalls: list[float] = []
    f1s: list[float] = []
    for c in ("prose", "lineated"):
        tp = sum(t == c and p == c for t, p in zip(y_true, y_pred))
        fn = sum(t == c and p != c for t, p in zip(y_true, y_pred))
        fp = sum(t != c and p == c for t, p in zip(y_true, y_pred))
        recalls.append(tp / (tp + fn) if tp + fn else 0.0)
        prec = tp / (tp + fp) if tp + fp else 0.0
        f1s.append(2 * prec * recalls[-1] / (prec + recalls[-1]) if prec + recalls[-1] else 0.0)
    acc = sum(t == p for t, p in zip(y_true, y_pred)) / len(y_true) if y_true else 0.0
    return Metrics(acc=acc, balanced_acc=sum(recalls) / 2, macro_f1=sum(f1s) / 2,
                   prose_recall=recalls[0], lineated_recall=recalls[1])


@dataclass(frozen=True)
class ReaderScore:
    """One row of the head-to-head: a reader and the student scored on the SAME shared lines."""

    reader: str
    n_shared: int
    label_dist: dict[str, int]
    reader_metrics: Metrics
    student_metrics: Metrics


def score_readers(
    truth: dict[LineId, str], student_pred: dict[LineId, str],
    panel: dict[str, dict[LineId, str]],
) -> list[ReaderScore]:
    """For each reader, the lines it shares with both `truth` and the student, scored both
    ways on that identical shared set — so every row is apples-to-apples. All joins are by
    `LineId`."""
    rows: list[ReaderScore] = []
    for tag in _READERS:
        reader = panel.get(tag, {})
        shared = [k for k in truth if k in reader and k in student_pred]
        if not shared:
            continue
        yt = [truth[k] for k in shared]
        rows.append(ReaderScore(
            reader=tag, n_shared=len(shared), label_dist=dict(Counter(yt)),
            reader_metrics=balanced(yt, [reader[k] for k in shared]),
            student_metrics=balanced(yt, [student_pred[k] for k in shared]),
        ))
    return rows


@dataclass(frozen=True)
class Comparison:
    rows: list[ReaderScore]
    n_labels_shared: int


def score(records: Mapping[str, list[LineRecord]], labelset: labels.LabelSet,
          votes: dict[str, dict[LineId, str]], *, alpha: float = 0.75) -> Comparison:
    ds = student.build_dataset(records, labelset)
    oof = student.oof_smoothed(ds, records, alpha=alpha)  # run-smoothed student (alpha=0 = i.i.d.)

    truth: dict[LineId, str] = {g.id: g.label for g in labelset.labels}
    student_pred: dict[LineId, str] = {
        g.id: oof[g.id].label for g in labelset.labels if g.id in oof}

    n_shared_any = len({k for tag in _READERS for k in votes.get(tag, {}) if k in truth})
    return Comparison(rows=score_readers(truth, student_pred, votes),
                      n_labels_shared=n_shared_any)


def format_row(row: ReaderScore) -> str:
    """One reader-vs-student table line: reader (balAcc/mF1/pRec) | student (same). Shared by
    `compare.score` and `contested.evaluate` so the head-to-head table has one renderer."""
    def triple(m: Metrics) -> str:
        return f"{m.balanced_acc:>7.3f} {m.macro_f1:>6.3f} {m.prose_recall:>6.3f}"
    return (f"{row.reader:9} {row.n_shared:>4} {str(row.label_dist):>22} | "
            f"{triple(row.reader_metrics)} | {triple(row.student_metrics)}")


if __name__ == "__main__":
    labelset = labels.load()
    records = store.load_records_many(sorted({g.id.book_id for g in labelset.labels}))
    cmp = score(records, labelset, panel_votes.by_reader())
    print(f"labeled lines covered by >=1 reader: {cmp.n_labels_shared}\n")
    print(f"{'reader':9} {'n':>4} {'labels':>22} | {'READER':^22} | {'STUDENT(OOF)':^22}")
    print(f"{'':9} {'':>4} {'':>22} | {'balAcc':>7} {'mF1':>6} {'pRec':>6} | "
          f"{'balAcc':>7} {'mF1':>6} {'pRec':>6}")
    for r in cmp.rows:
        print(format_row(r))
