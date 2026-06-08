# research-pure: student vs LLM-teacher on the HUMAN-adjudicated contested lines.
"""The fairest head-to-head — on the CONTESTED set.

`compare.py` scores everyone on the full labels (which include easy strata). This module
restricts to the CONTESTED lines: the ones a human RE-ADJUDICATED on the page (the committed
`eval_sets/contested.json` slice — an EVAL truth, never folded into training), exactly the
discriminator the prior ranking used. On these lines:

  TRUTH    = the human's page-grounded label (NOT the consensus label — on contested lines
             the human revised it, and that revision is the better truth).
  STUDENT  = its OUT-OF-FOLD prediction (book held out — never trained on this line).
  TEACHER  = the reader's label.

All three are compared on the SAME lines with the SAME balanced metric, joined by `LineId`.
This is the honest comparison; `compare.py`'s numbers are on an easier population.

"Contested" here means LABEL DISAGREEMENT (a human re-judged the line), distinct from model
UNCERTAINTY (a low-margin posterior); see `student` for that distinction.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .. import store, student
from ..annotations import LabelSet, by_reader, load_labels
from .compare import ReaderScore, score_readers
from .metrics import Metrics, balanced
from ..identity import Label, LabelByLine, LineId, PanelVotes, to_label
from ..records import RecordsByBook


def load_contested(*, annotations: Path | None = None) -> LabelByLine:
    """The contested eval slice (`eval_sets/contested.json`): `{LineId: human label}` on the hard
    re-adjudicated lines — an EVAL-only truth (not training); the student is scored against it on
    the lines it can predict."""
    return {LineId.from_key(d["id"]): to_label(d["label"])
            for d in store.load_eval_set("contested", annotations=annotations)}


@dataclass(frozen=True)
class ContestedResult:
    n_contested: int
    n_with_student: int
    n_revised_vs_consensus: int   # contested lines where human != consensus label
    label_dist: dict[Label, int]
    student: Metrics              # student over ALL scorable contested lines
    rows: list[ReaderScore]       # per-reader, on the lines shared with that reader


def evaluate(records: RecordsByBook, labelset: LabelSet,
             votes: PanelVotes, contested: LabelByLine, *,
             alpha: float = 0.75) -> ContestedResult:
    """Score the student on the contested hard lines. `contested` is the EVAL truth `{LineId:
    label}` (the human re-adjudication); the student is scored against it on the lines it can
    predict. `alpha` is the run-smoothing weight (0 = i.i.d.)."""
    by_id = {g.id: g for g in labelset.labels}
    ds = student.build_dataset(records, labelset)
    oof = student.oof_smoothed(ds, records, alpha=alpha)  # book-held-out, run-smoothed (alpha=0 == i.i.d.)

    student_pred: LabelByLine = {
        g.id: oof[g.id].label for g in labelset.labels if g.id in oof}

    scorable = [k for k in contested if k in student_pred]
    n_revised = sum(1 for k in scorable if by_id[k].label != contested[k])
    yt = [contested[k] for k in scorable]

    return ContestedResult(
        n_contested=len(contested), n_with_student=len(scorable),
        n_revised_vs_consensus=n_revised, label_dist=dict(Counter(yt)),
        student=balanced(yt, [student_pred[k] for k in scorable]),
        rows=score_readers(contested, student_pred, votes),
    )


if __name__ == "__main__":
    from .compare import format_row

    labelset = load_labels()
    records = store.load_records_many(sorted({g.id.book_id for g in labelset.labels}))
    votes = by_reader()
    contested = load_contested()
    base = evaluate(records, labelset, votes, contested, alpha=0.0)    # i.i.d. per-line baseline
    r = evaluate(records, labelset, votes, contested, alpha=0.75)      # run-smoothed default
    print(f"human-adjudicated contested lines: {r.n_contested}")
    print(f"  scorable by the student: {r.n_with_student}")
    print(f"  of those, REVISED vs consensus (human != consensus): {r.n_revised_vs_consensus}")
    print(f"  label dist (human truth): {r.label_dist}\n")
    print("STUDENT on the contested hard subset — i.i.d. (alpha=0) vs run-smoothed (alpha=0.75):")
    print(f"    {'metric':16} {'iid':>8} {'smoothed':>9}")
    for name in ("balanced_acc", "macro_f1", "prose_recall", "lineated_recall", "acc"):
        print(f"    {name:16} {getattr(base.student, name):>8.3f} {getattr(r.student, name):>9.3f}")
    # raw basis, so a rate like 1.000 is read with its small-n in view (M1)
    n_pr = r.label_dist.get("prose", 0)
    n_li = r.label_dist.get("lineated", 0)
    print(f"    basis: prose {round(base.student.prose_recall * n_pr)}/{n_pr} → "
          f"{round(r.student.prose_recall * n_pr)}/{n_pr} correct  |  "
          f"lineated {round(base.student.lineated_recall * n_li)}/{n_li} → "
          f"{round(r.student.lineated_recall * n_li)}/{n_li}  "
          f"(scorable {r.n_with_student} of {r.n_contested} contested)")
    print("\napples-to-apples on lines shared with each reader (truth = HUMAN; student=smoothed):")
    print(f"{'reader':9} {'n':>4} {'labels':>22} | {'READER':^22} | {'STUDENT':^22}")
    for row in r.rows:
        print(format_row(row))
