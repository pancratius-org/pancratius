# research-pure: the choke point resolves L001→LineId and surfaces every anomaly, never silently.
"""`resolve_*` map task-local keys back to `LineId`s through the manifest. Locks the happy round-trip
(panel votes + human labels) and each `ResolveFault`: unmapped, dup, bad label, text drift, missing."""
from __future__ import annotations

import dataclasses

from lineation_core import store
from lineation_core.identity import text_hash
from lineation_core.labels import LabelSource
from lineation_core.teacher import responses, tasks
from lineation_core.teacher.responses import RawReaderResponse, RawReaderRow, ResolveFault
from lineation_core.teacher.tasks import ItemSpec


def _task(n: int = 5):
    recs = store.load_records("57")
    votable = [r for r in recs if r.votable][:n]
    spec = ItemSpec(region_id="b57-r0", vote_ids=tuple(r.id for r in votable))
    task = tasks.build_task(title="adj", instructions="i", specs=[spec], records={"57": recs})
    return task, {"57": recs}


def _resp(item_id, pairs, *, tag="grok", note=""):
    rows = tuple(RawReaderRow(key=k, label=lab, conf=c) for k, lab, c in pairs)
    return RawReaderResponse(item_id=item_id, tag=tag, rows=rows, note=note)


def _faults(r):
    return {(f.key, f.fault) for f in r.faults}


def test_resolve_panel_round_trips_keys_to_lineids():
    task, records = _task()
    keys = sorted(task.manifest.by_key)
    resp = _resp("b57-r0", [(k, "lineated", 0.8) for k in keys])
    r = responses.resolve_panel(task, [resp], records)
    assert (r.n_expected, r.n_resolved) == (5, 5) and not r.faults
    assert [v.id for v in r.votes] == [task.manifest.by_key[k] for k in keys]
    assert all(v.label == "lineated" and v.conf == 0.8 and v.tag == "grok" for v in r.votes)


def test_resolve_adjudication_makes_human_labels_with_lineage():
    task, records = _task()
    keys = sorted(task.manifest.by_key)
    resp = _resp("b57-r0", [(k, "prose", None) for k in keys], tag="human", note="all prose here")
    r = responses.resolve_adjudication(task, [resp], records)
    by_id = {rec.id: rec for rec in records["57"]}
    assert r.n_resolved == 5 and not r.faults and r.notes == {"b57-r0": "all prose here"}
    for lab, k in zip(r.labels, keys):
        assert lab.source is LabelSource.HUMAN and lab.confidence is None
        assert lab.provenance["task_key"] == k and lab.provenance["item_id"] == "b57-r0"
        assert lab.line_text_hash == by_id[lab.id].line_text_hash


def test_unmapped_and_bad_label_faults_exclude_the_row():
    task, records = _task()
    resp = _resp("b57-r0", [("L001", "lineated", None), ("L999", "prose", None),
                            ("L002", "maybe", None)])
    r = responses.resolve_panel(task, [resp], records)
    assert len(r.votes) == 1                                   # only L001 survives
    assert ("L999", ResolveFault.UNMAPPED_KEY) in _faults(r)
    assert ("L002", ResolveFault.BAD_LABEL) in _faults(r)


def test_duplicate_key_keeps_first_and_flags():
    task, records = _task()
    resp = _resp("b57-r0", [("L001", "prose", None), ("L001", "lineated", None)])
    r = responses.resolve_panel(task, [resp], records)
    assert len(r.votes) == 1 and r.votes[0].label == "prose"   # first kept
    assert ("L001", ResolveFault.DUP_KEY) in _faults(r)


def test_missing_keys_on_an_answered_item_are_a_coverage_gap():
    task, records = _task()
    resp = _resp("b57-r0", [("L001", "prose", None), ("L002", "prose", None)])
    r = responses.resolve_panel(task, [resp], records)
    assert r.n_resolved == 2 and r.n_expected == 5
    missing = {k for k, f in _faults(r) if f is ResolveFault.MISSING_KEY}
    assert missing == {"L003", "L004", "L005"}


def test_text_drift_fails_loud_and_excludes_the_row():
    task, records = _task()
    drifted = task.manifest.by_key["L001"]
    tampered = [dataclasses.replace(r, text=r.text + " X", line_text_hash=text_hash(r.text + " X"))
                if r.id == drifted else r for r in records["57"]]
    resp = _resp("b57-r0", [(k, "prose", None) for k in sorted(task.manifest.by_key)])
    r = responses.resolve_panel(task, [resp], {"57": tampered})
    assert ("L001", ResolveFault.TEXT_DRIFT) in _faults(r)
    assert drifted not in [v.id for v in r.votes]              # the drifted line is not persisted


def test_parse_ui_responses_reads_opaque_keys():
    data = {"responses": {"b57-r0": {"lines": {"L001": "prose", "L002": "lineated"},
                                     "note": "n"}}}
    out = responses.parse_ui_responses(data)
    assert len(out) == 1 and out[0].tag == "human" and out[0].note == "n"
    assert {r.key: r.label for r in out[0].rows} == {"L001": "prose", "L002": "lineated"}


def test_parse_reader_reply_tolerates_fences_and_keeps_conf_verbatim():
    raw = 'thinking...\n```json\n[{"key":"L001","label":"lineated","conf":0.9},' \
          '{"key":"L002","label":"prose"}]\n```'
    out = responses.parse_reader_reply("b57-r0", "grok", raw)
    rows = {r.key: r for r in out.rows}
    assert rows["L001"].conf == 0.9
    assert rows["L002"].conf is None                          # absent conf is None, NOT a default
    assert responses.parse_reader_reply("x", "grok", "no json here").rows == ()
