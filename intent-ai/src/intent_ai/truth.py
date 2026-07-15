# research-pure: binds annotation truth to the canonical source records it addresses.
"""One total truth join shared by every consumer of active annotations."""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from .annotations import LabelSet, LineLabel
from .identity import Label, LineId
from .records import LineRecord, RecordsByBook


class TruthJoinFault(StrEnum):
    MISSING_RECORD = "missing_record"
    NON_VOTABLE_RECORD = "non_votable_record"
    TEXT_HASH_DRIFT = "text_hash_drift"


@dataclass(frozen=True, slots=True)
class TruthJoinIssue:
    id: LineId
    fault: TruthJoinFault


class TruthJoinError(ValueError):
    def __init__(self, issues: list[TruthJoinIssue]) -> None:
        self.issues = tuple(issues)
        counts = Counter(issue.fault.value for issue in issues)
        summary = ", ".join(f"{name}={count}" for name, count in sorted(counts.items()))
        super().__init__(f"annotation truth does not totally join canonical records ({summary})")


@dataclass(frozen=True, slots=True)
class TruthBinding:
    truth: LineLabel
    record: LineRecord


@dataclass(frozen=True, slots=True)
class JoinedTruth:
    """Every active truth row bound to its canonical votable record."""

    entries: tuple[TruthBinding, ...]

    @property
    def training(self) -> tuple[TruthBinding, ...]:
        return tuple(binding for binding in self.entries if not binding.truth.holdout)


@dataclass(frozen=True, slots=True)
class UnanimousParagraphTruth:
    label: Label


@dataclass(frozen=True, slots=True)
class MixedParagraphTruth:
    by_sub: tuple[tuple[int, Label], ...]


@dataclass(frozen=True, slots=True)
class IncompleteParagraphTruth:
    by_sub: tuple[tuple[int, Label], ...]
    expected_subs: int


type ParagraphTruth = (
    UnanimousParagraphTruth | MixedParagraphTruth | IncompleteParagraphTruth
)


def reduce_paragraph_truth(
    by_sub: Mapping[int, Label],
    *,
    expected_subs: int,
) -> ParagraphTruth:
    """Reduce line truth only when it can represent one paragraph-level fold decision."""
    if expected_subs <= 0:
        raise ValueError("paragraph truth requires at least one readable source line")
    ordered = tuple(sorted(by_sub.items()))
    if set(by_sub) != set(range(expected_subs)):
        return IncompleteParagraphTruth(ordered, expected_subs)
    labels = {label for _sub, label in ordered}
    if len(labels) != 1:
        return MixedParagraphTruth(ordered)
    return UnanimousParagraphTruth(next(iter(labels)))


def join_truth(records: RecordsByBook, labelset: LabelSet) -> JoinedTruth:
    """Total-bind all active truth before any training, evaluation, or correction projection."""
    by_id: dict[LineId, LineRecord] = {}
    for book_key, book_records in records.items():
        for record in book_records:
            if record.id.book_key != book_key:
                raise ValueError(f"record {record.id} stored under wrong book {book_key}")
            if record.id in by_id:
                raise ValueError(f"duplicate canonical record identity: {record.id}")
            by_id[record.id] = record

    issues: list[TruthJoinIssue] = []
    bindings: list[TruthBinding] = []
    for truth in labelset.labels:
        record = by_id.get(truth.id)
        if record is None:
            issues.append(TruthJoinIssue(truth.id, TruthJoinFault.MISSING_RECORD))
        elif not record.votable:
            issues.append(TruthJoinIssue(truth.id, TruthJoinFault.NON_VOTABLE_RECORD))
        elif truth.line_text_hash != record.line_text_hash:
            issues.append(TruthJoinIssue(truth.id, TruthJoinFault.TEXT_HASH_DRIFT))
        else:
            bindings.append(TruthBinding(truth, record))
    if issues:
        raise TruthJoinError(issues)
    return JoinedTruth(tuple(bindings))
