# research-pure: the resolution CHOKE POINT — task-local keys → LineId, before anything persists.
"""Readers (LLM or human) answer with task-local opaque keys (`L001`); this is the ONE place those
keys become `LineId`s. `parse_*` turn a raw model reply / UI export into typed rows; `resolve_*`
map each row through the task manifest into a `PanelVote` (panel) or `LineLabel` (human
adjudication).

Every anomaly is surfaced as a `ResolveFault`, never silently dropped: an unknown key, a duplicate,
a bad label, a line whose text DRIFTED since the task was minted (the teacher-loop analogue of the
docx-hash rail), or a manifest key no one answered. Structural/drift faults EXCLUDE that row (fail
loud — a manifest↔response mismatch is a replay/version bug); a missing answer is a coverage gap
(kept as a warning, the rest still resolves)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum

from ..identity import Label, LineId, ReaderTag
from ..labels import LabelSource, LineLabel
from ..panel_votes import PanelVote
from ..records import RecordsByBook
from .tasks import RegionId, Task, TaskKey

_LABELS: frozenset[Label] = frozenset({"prose", "lineated"})


@dataclass(frozen=True, slots=True)
class RawReaderRow:
    """What a reader/adjudicator returns for one line: an OPAQUE key + verdict. `conf` is optional
    (a human emits none; a model's is stored verbatim, never a fabricated default)."""
    key: TaskKey
    label: Label
    conf: float | None = None


@dataclass(frozen=True, slots=True)
class RawReaderResponse:
    """One reader's verdicts on one task item. `tag` is the panel reader, or "human" for an
    adjudication; `note` is the human's per-item free text."""
    item_id: RegionId
    tag: ReaderTag
    rows: tuple[RawReaderRow, ...]
    note: str = ""


class ResolveFault(StrEnum):
    UNMAPPED_KEY = "unmapped_key"   # key not in the task manifest — a replay/version bug
    DUP_KEY = "dup_key"             # same key answered twice in one response (first kept)
    BAD_LABEL = "bad_label"         # not prose|lineated
    TEXT_DRIFT = "text_drift"       # the line's text changed since the task was minted
    MISSING_KEY = "missing_key"     # a manifest key for an answered item that no one answered


@dataclass(frozen=True, slots=True)
class ResolveFaultRow:
    item_id: RegionId
    key: TaskKey
    fault: ResolveFault
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ResolvedVotes:
    """Panel resolution: LineId-keyed `PanelVote`s ready for `votes.jsonl`, plus every fault."""
    votes: tuple[PanelVote, ...]
    faults: tuple[ResolveFaultRow, ...]
    n_expected: int
    n_resolved: int


@dataclass(frozen=True, slots=True)
class ResolvedLabels:
    """Human-adjudication resolution: LineId-keyed `LineLabel`s for `labels.jsonl`/`eval_sets`."""
    labels: tuple[LineLabel, ...]
    faults: tuple[ResolveFaultRow, ...]
    notes: dict[RegionId, str]
    n_expected: int
    n_resolved: int


@dataclass(frozen=True, slots=True)
class _Resolved:
    item_id: RegionId
    tag: ReaderTag
    id: LineId
    row: RawReaderRow


def _resolve(
    task: Task, responses: list[RawReaderResponse], records: RecordsByBook,
) -> tuple[list[_Resolved], list[ResolveFaultRow], int]:
    """The choke point: map each response row through `task.manifest` to a `LineId`, flagging every
    anomaly. Returns the resolved rows, the faults, and the expected-key count for answered items."""
    by_key = task.manifest.by_key
    mint_hash = task.manifest.text_hash_by_key
    cur_hash = {r.id: r.line_text_hash for book in records.values() for r in book}
    expected = {it.id: {ln.key for ln in it.lines} for it in task.items}

    resolved: list[_Resolved] = []
    faults: list[ResolveFaultRow] = []
    answered: dict[RegionId, set[TaskKey]] = {}
    for resp in responses:
        seen: set[TaskKey] = set()
        for row in resp.rows:
            if row.key not in by_key:
                faults.append(ResolveFaultRow(resp.item_id, row.key, ResolveFault.UNMAPPED_KEY))
                continue
            if row.key in seen:
                faults.append(ResolveFaultRow(resp.item_id, row.key, ResolveFault.DUP_KEY))
                continue
            seen.add(row.key)
            if row.label not in _LABELS:
                faults.append(ResolveFaultRow(resp.item_id, row.key, ResolveFault.BAD_LABEL,
                                              row.label))
                continue
            lid = by_key[row.key]
            if cur_hash.get(lid) != mint_hash.get(row.key):
                faults.append(ResolveFaultRow(resp.item_id, row.key, ResolveFault.TEXT_DRIFT))
                continue
            resolved.append(_Resolved(resp.item_id, resp.tag, lid, row))
        answered.setdefault(resp.item_id, set()).update(seen)

    n_expected = 0
    for item_id, keys in answered.items():
        exp = expected.get(item_id, set())
        n_expected += len(exp)
        for k in sorted(exp - keys):                 # coverage gaps on answered items
            faults.append(ResolveFaultRow(item_id, k, ResolveFault.MISSING_KEY))
    return resolved, faults, n_expected


def resolve_panel(
    task: Task, responses: list[RawReaderResponse], records: RecordsByBook,
) -> ResolvedVotes:
    """Resolve panel responses into LineId-keyed `PanelVote`s (one per resolved row; rep
    aggregation is a later, separate step). `conf` is the reader's verbatim self-report."""
    resolved, faults, n_expected = _resolve(task, responses, records)
    votes = tuple(PanelVote(id=r.id, tag=r.tag, label=r.row.label, conf=r.row.conf)
                  for r in resolved)
    return ResolvedVotes(votes=votes, faults=tuple(faults),
                         n_expected=n_expected, n_resolved=len(votes))


def resolve_adjudication(
    task: Task, responses: list[RawReaderResponse], records: RecordsByBook, *,
    source: LabelSource = LabelSource.HUMAN, audit_status: str = "adjudicated",
) -> ResolvedLabels:
    """Resolve human adjudications into LineId-keyed `LineLabel`s. `confidence` is `None` (a human
    emits no probability); provenance carries the task-local key + item + task title as lineage."""
    resolved, faults, n_expected = _resolve(task, responses, records)
    by_id = {r.id: r for book in records.values() for r in book}
    labels = tuple(
        LineLabel(id=r.id, label=r.row.label, source=source, confidence=None,
                  audit_status=audit_status, notes="",
                  provenance={"task_key": r.row.key, "item_id": r.item_id, "task": task.title},
                  line_text_hash=by_id[r.id].line_text_hash)
        for r in resolved)
    notes = {resp.item_id: resp.note for resp in responses if resp.note}
    return ResolvedLabels(labels=labels, faults=tuple(faults), notes=notes,
                          n_expected=n_expected, n_resolved=len(labels))


# --- parsers: raw wire → typed rows (resolution is separate) -----------------------------------

def parse_ui_responses(data: dict, *, tag: ReaderTag = "human") -> list[RawReaderResponse]:
    """`adjudicate.html` export → typed responses. The UI shape is
    `{"responses": {item_id: {"lines": {key: label}, "note": str}}}` — opaque keys throughout."""
    out: list[RawReaderResponse] = []
    for item_id, rec in data.get("responses", {}).items():
        rows = tuple(RawReaderRow(key=k, label=v) for k, v in rec.get("lines", {}).items())
        out.append(RawReaderResponse(item_id=item_id, tag=tag,
                                     rows=rows, note=rec.get("note", "")))
    return out


def parse_reader_reply(item_id: RegionId, tag: ReaderTag, raw_text: str) -> RawReaderResponse:
    """An LLM reply (a JSON array of `{key, label, conf}`, tolerating code fences / leading prose)
    → typed rows. `conf` is kept verbatim, `None` when absent — never a fabricated default."""
    rows = tuple(
        RawReaderRow(key=str(o["key"]), label=o.get("label", ""), conf=_conf(o.get("conf")))
        for o in _json_array(raw_text) if isinstance(o, dict) and "key" in o)
    return RawReaderResponse(item_id=item_id, tag=tag, rows=rows)


def _conf(v: object) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _json_array(text: str) -> list:
    """The outermost JSON array in a model reply (ignores ``` fences and surrounding prose)."""
    lo, hi = text.find("["), text.rfind("]")
    if lo == -1 or hi <= lo:
        return []
    try:
        data = json.loads(text[lo:hi + 1])
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []
