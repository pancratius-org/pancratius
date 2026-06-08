# research-pure: the resolution CHOKE POINT — task-local keys → LineId, before anything persists.
"""Readers (LLM or human) answer with task-local opaque keys (`L001`); this is the ONE place those
keys become `LineId`s. `parse_*` turn a raw model reply / UI export into typed rows; `resolve_*`
map each row through the task MANIFEST (the only thing needed — not the whole `Task`) into a
`PanelVote` (panel) or `LineLabel` (human adjudication).

Every anomaly is surfaced as a `ResolveFault`, never silently dropped: a response under an unknown
item, a key belonging to a different item, an unknown key, a duplicate, a bad label, a line whose
text DRIFTED since the task was minted (the teacher-loop analogue of the docx-hash rail), or a
manifest key no one answered. Structural/drift faults EXCLUDE that row (fail loud — a
manifest↔response mismatch is a replay/version bug); a missing answer is a coverage gap (kept as a
warning, the rest still resolves). `complete=True` flags every unanswered item (a full run);
`complete=False` flags only answered items (a partial batch)."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from enum import StrEnum

from ..annotations import LabelSource, LineLabel, PanelVote
from ..identity import JsonObject, Label, LineId, LineTextHash, ReaderTag, to_label
from ..records import RecordsByBook
from .tasks import RegionId, TaskKey, TaskManifest


ADJUDICATED_AUDIT_STATUS = "adjudicated"   # the `audit_status` a human-adjudicated label carries


@dataclass(frozen=True, slots=True)
class RawReaderRow:
    """What a reader/adjudicator returns for one line: an OPAQUE key + verdict. `conf` is optional
    (a human emits none; a model's is stored verbatim, never a fabricated default)."""
    key: TaskKey
    label: str          # the RAW reader output — validated to a `Label` only at resolution
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
    UNKNOWN_ITEM = "unknown_item"        # the response's item_id is not in the task
    KEY_ITEM_MISMATCH = "key_item_mismatch"  # the key belongs to a different item
    UNMAPPED_KEY = "unmapped_key"        # key not in the task manifest — a replay/version bug
    DUP_KEY = "dup_key"                  # same key answered twice in one response (first kept)
    BAD_LABEL = "bad_label"              # not prose|lineated
    TEXT_DRIFT = "text_drift"            # the line's text changed since the task was minted
    MISSING_KEY = "missing_key"          # a manifest key no one answered


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
    key: TaskKey
    id: LineId
    label: Label        # validated at the choke point via `to_label`
    conf: float | None


def _resolve(
    manifest: TaskManifest, responses: list[RawReaderResponse], records: RecordsByBook, *,
    complete: bool,
) -> tuple[list[_Resolved], list[ResolveFaultRow], int]:
    """The choke point: map each response row through `manifest` to a `LineId`, flagging every
    anomaly. Returns the resolved rows, the faults, and the expected-key count."""
    by_key, mint_hash, item_by_key = manifest.by_key, manifest.text_hash_by_key, manifest.item_by_key
    items = manifest.item_ids
    cur_hash = {r.id: r.line_text_hash for book in records.values() for r in book}

    resolved: list[_Resolved] = []
    faults: list[ResolveFaultRow] = []
    answered: dict[RegionId, set[TaskKey]] = {}
    for resp in responses:
        if resp.item_id not in items:
            faults.append(ResolveFaultRow(resp.item_id, "", ResolveFault.UNKNOWN_ITEM))
            continue
        seen: set[TaskKey] = set()
        for row in resp.rows:
            if row.key not in by_key:
                faults.append(ResolveFaultRow(resp.item_id, row.key, ResolveFault.UNMAPPED_KEY))
                continue
            if item_by_key[row.key] != resp.item_id:
                faults.append(ResolveFaultRow(resp.item_id, row.key, ResolveFault.KEY_ITEM_MISMATCH,
                                              item_by_key[row.key]))
                continue
            if row.key in seen:
                faults.append(ResolveFaultRow(resp.item_id, row.key, ResolveFault.DUP_KEY))
                continue
            seen.add(row.key)
            try:
                label = to_label(row.label)
            except ValueError:
                faults.append(ResolveFaultRow(resp.item_id, row.key, ResolveFault.BAD_LABEL,
                                              row.label))
                continue
            lid = by_key[row.key]
            if cur_hash.get(lid) != mint_hash.get(row.key):
                faults.append(ResolveFaultRow(resp.item_id, row.key, ResolveFault.TEXT_DRIFT))
                continue
            resolved.append(_Resolved(resp.item_id, resp.tag, row.key, lid, label, row.conf))
        answered.setdefault(resp.item_id, set()).update(seen)

    to_check = items if complete else answered.keys()      # full run flags every item; else answered
    n_expected = 0
    for item_id in to_check:
        exp = manifest.keys_for_item(item_id)
        n_expected += len(exp)
        for k in sorted(exp - answered.get(item_id, set())):
            faults.append(ResolveFaultRow(item_id, k, ResolveFault.MISSING_KEY))
    return resolved, faults, n_expected


def resolve_panel(
    manifest: TaskManifest, responses: list[RawReaderResponse], records: RecordsByBook, *,
    complete: bool = False,
) -> ResolvedVotes:
    """Resolve panel responses into LineId-keyed `PanelVote`s (one per resolved row; rep
    aggregation is a later, separate step). `conf` is the reader's verbatim self-report."""
    resolved, faults, n_expected = _resolve(manifest, responses, records, complete=complete)
    votes = tuple(PanelVote(id=r.id, tag=r.tag, label=r.label, conf=r.conf)
                  for r in resolved)
    return ResolvedVotes(votes=votes, faults=tuple(faults),
                         n_expected=n_expected, n_resolved=len(votes))


def resolve_adjudication(
    manifest: TaskManifest, responses: list[RawReaderResponse], records: RecordsByBook, *,
    title: str = "", complete: bool = False,
    source: LabelSource = LabelSource.HUMAN, audit_status: str = ADJUDICATED_AUDIT_STATUS,
) -> ResolvedLabels:
    """Resolve human adjudications into LineId-keyed `LineLabel`s. `confidence` is `None` (a human
    emits no probability); provenance carries the task-local key + item + task title as lineage."""
    resolved, faults, n_expected = _resolve(manifest, responses, records, complete=complete)
    by_id = {r.id: r for book in records.values() for r in book}
    labels = tuple(
        LineLabel(id=r.id, label=r.label, source=source, confidence=None,
                  audit_status=audit_status, notes="",
                  provenance={"task_key": r.key, "item_id": r.item_id, "task": title},
                  line_text_hash=by_id[r.id].line_text_hash)
        for r in resolved)
    notes = {resp.item_id: resp.note for resp in responses if resp.note}
    return ResolvedLabels(labels=labels, faults=tuple(faults), notes=notes,
                          n_expected=n_expected, n_resolved=len(labels))


# --- parsers: raw wire → typed rows (resolution is separate) -----------------------------------

def parse_ui_responses(data: JsonObject, *, tag: ReaderTag = "human") -> list[RawReaderResponse]:
    """`adjudicate.html` export → typed responses. The UI shape is
    `{"responses": {item_id: {"lines": {key: label}, "note": str}}}` — opaque keys throughout."""
    out: list[RawReaderResponse] = []
    for item_id, rec in data.get("responses", {}).items():
        rows = tuple(RawReaderRow(key=k, label=v) for k, v in rec.get("lines", {}).items())
        out.append(RawReaderResponse(item_id=item_id, tag=tag,
                                     rows=rows, note=rec.get("note", "")))
    return out


def parse_reader_reply(item_id: RegionId, tag: ReaderTag, raw_text: str) -> RawReaderResponse:
    """An LLM reply (a JSON array of `{key, label, conf}`, tolerating code fences / leading prose /
    stray brackets) → typed rows. `conf` is kept verbatim, `None` when absent or out of range."""
    rows = tuple(
        RawReaderRow(key=str(o["key"]), label=o.get("label", ""), conf=_conf(o.get("conf")))
        for o in _json_array(raw_text) if isinstance(o, dict) and "key" in o)
    return RawReaderResponse(item_id=item_id, tag=tag, rows=rows)


def _conf(v: object) -> float | None:
    """A reader's self-reported confidence: a finite probability, or `None`. Never a fabricated
    default; an out-of-range or NaN value is dropped to `None` rather than persisted."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    c = float(v)
    return c if math.isfinite(c) and 0.0 <= c <= 1.0 else None


def _json_array(text: str) -> list:
    """The LAST balanced JSON array of `key`-bearing objects in a model reply. Scans bracket depth
    (so a stray `[` in reasoning can't truncate it) and ignores ``` fences and leading arrays."""
    best: list = []
    depth = start = 0
    for i, ch in enumerate(text):
        if ch == "[":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "]" and depth > 0:
            depth -= 1
            if depth == 0:
                try:
                    data = json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    continue
                if isinstance(data, list) and any(isinstance(o, dict) and "key" in o for o in data):
                    best = data
    return best
