# research-pure: the choke point resolves L001→LineId and surfaces every anomaly, never silently.
"""`resolve_*` map task-local keys back to `LineId`s through the MANIFEST (not the whole Task).
Locks the happy round-trip + each fault: unknown item, key/item mismatch, unmapped, dup, bad
label, text drift, and missing (partial vs complete mode)."""
from __future__ import annotations

import dataclasses

from lineation_core import store
from lineation_core.identity import text_hash
from lineation_core.annotations import LabelSource
from lineation_core.teacher import responses, tasks
from lineation_core.teacher.responses import RawReaderResponse, RawReaderRow, ResolveFault
from lineation_core.teacher.tasks import ItemSpec


def _task(n: int = 5):
    recs = store.load_records("57")
    votable = [r for r in recs if r.votable][:n]
    spec = ItemSpec.all_votable("b57-r0", [r.id for r in votable])
    return tasks.build_task(title="adj", instructions="i", specs=[spec], records={"57": recs}), \
        {"57": recs}


def _two_item_task():
    recs = store.load_records("57")
    v = [r for r in recs if r.votable][:6]
    a = ItemSpec.all_votable("b57-r0", [r.id for r in v[:3]])
    b = ItemSpec.all_votable("b57-r1", [r.id for r in v[3:6]])
    return tasks.build_task(title="adj", instructions="i", specs=[a, b], records={"57": recs}), \
        {"57": recs}


def _resp(item_id, pairs, *, tag="grok", note=""):
    rows = tuple(RawReaderRow(key=k, label=lab, conf=c) for k, lab, c in pairs)
    return RawReaderResponse(item_id=item_id, tag=tag, rows=rows, note=note)


def _faults(r):
    return {(f.key, f.fault) for f in r.faults}


def test_resolve_panel_round_trips_keys_to_lineids():
    task, records = _task()
    keys = sorted(task.manifest.by_key)
    resp = _resp("b57-r0", [(k, "lineated", 0.8) for k in keys])
    r = responses.resolve_panel(task.manifest, [resp], records)
    assert (r.n_expected, r.n_resolved) == (5, 5) and not r.faults
    assert [v.id for v in r.votes] == [task.manifest.by_key[k] for k in keys]
    assert all(v.label == "lineated" and v.conf == 0.8 and v.tag == "grok" for v in r.votes)


def test_resolve_adjudication_makes_human_labels_with_lineage():
    task, records = _task()
    keys = sorted(task.manifest.by_key)
    resp = _resp("b57-r0", [(k, "prose", None) for k in keys], tag="human", note="all prose")
    r = responses.resolve_adjudication(task.manifest, [resp], records, title="adj")
    by_id = {rec.id: rec for rec in records["57"]}
    assert r.n_resolved == 5 and not r.faults and r.notes == {"b57-r0": "all prose"}
    for lab, k in zip(r.labels, keys):
        assert lab.source is LabelSource.HUMAN and lab.confidence is None
        assert lab.provenance["task_key"] == k and lab.provenance["task"] == "adj"
        assert lab.line_text_hash == by_id[lab.id].line_text_hash


def test_unknown_item_is_faulted():
    task, records = _task()
    r = responses.resolve_panel(task.manifest, [_resp("nope", [("L001", "prose", None)])], records)
    assert not r.votes and ("", ResolveFault.UNKNOWN_ITEM) in _faults(r)


def test_key_belonging_to_another_item_is_faulted():
    task, records = _two_item_task()
    resp = _resp("b57-r0", [("L001", "prose", None), ("L004", "prose", None)])  # L004 ∈ b57-r1
    r = responses.resolve_panel(task.manifest, [resp], records)
    assert len(r.votes) == 1 and ("L004", ResolveFault.KEY_ITEM_MISMATCH) in _faults(r)


def test_unmapped_and_bad_label_faults_exclude_the_row():
    task, records = _task()
    resp = _resp("b57-r0", [("L001", "lineated", None), ("L999", "prose", None),
                            ("L002", "maybe", None)])
    r = responses.resolve_panel(task.manifest, [resp], records)
    assert len(r.votes) == 1
    assert ("L999", ResolveFault.UNMAPPED_KEY) in _faults(r)
    assert ("L002", ResolveFault.BAD_LABEL) in _faults(r)


def test_duplicate_key_keeps_first_and_flags():
    task, records = _task()
    resp = _resp("b57-r0", [("L001", "prose", None), ("L001", "lineated", None)])
    r = responses.resolve_panel(task.manifest, [resp], records)
    assert len(r.votes) == 1 and r.votes[0].label == "prose"
    assert ("L001", ResolveFault.DUP_KEY) in _faults(r)


def test_missing_keys_partial_vs_complete():
    task, records = _two_item_task()
    resp = _resp("b57-r0", [("L001", "prose", None), ("L002", "prose", None)])  # 2 of 3, item r0 only
    partial = responses.resolve_panel(task.manifest, [resp], records)            # complete=False
    assert {k for k, f in _faults(partial) if f is ResolveFault.MISSING_KEY} == {"L003"}
    full = responses.resolve_panel(task.manifest, [resp], records, complete=True)
    assert {k for k, f in _faults(full) if f is ResolveFault.MISSING_KEY} == \
        {"L003", "L004", "L005", "L006"}                       # + the whole unanswered item r1


def test_text_drift_fails_loud_and_excludes_the_row():
    task, records = _task()
    drifted = task.manifest.by_key["L001"]
    tampered = [dataclasses.replace(r, text=r.text + " X", line_text_hash=text_hash(r.text + " X"))
                if r.id == drifted else r for r in records["57"]]
    resp = _resp("b57-r0", [(k, "prose", None) for k in sorted(task.manifest.by_key)])
    r = responses.resolve_panel(task.manifest, [resp], {"57": tampered})
    assert ("L001", ResolveFault.TEXT_DRIFT) in _faults(r)
    assert drifted not in [v.id for v in r.votes]


def test_parse_ui_responses_reads_opaque_keys():
    data = {"responses": {"b57-r0": {"lines": {"L001": "prose", "L002": "lineated"}, "note": "n"}}}
    out = responses.parse_ui_responses(data)
    assert len(out) == 1 and out[0].tag == "human" and out[0].note == "n"
    assert {r.key: r.label for r in out[0].rows} == {"L001": "prose", "L002": "lineated"}


def test_parse_reader_reply_is_robust_and_range_checks_conf():
    raw = ('reasoning [not json]\n```json\n'
           '[{"key":"L001","label":"lineated","conf":0.9},'
           '{"key":"L002","label":"prose","conf":1.7},'
           '{"key":"L003","label":"prose"}]\n```')
    rows = {r.key: r for r in responses.parse_reader_reply("b57-r0", "grok", raw).rows}
    assert rows["L001"].conf == 0.9
    assert rows["L002"].conf is None        # out-of-range conf dropped, not persisted
    assert rows["L003"].conf is None        # absent conf is None
    assert responses.parse_reader_reply("x", "grok", "no json").rows == ()
