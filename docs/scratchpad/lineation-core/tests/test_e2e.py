# research-pure: end-to-end — a task flows through the fake panel AND a human into committed truth.
"""The whole new teacher path, proven before legacy is evicted: build a task → persist its bundle →
RELOAD the manifest from disk → run a fake panel → resolve → promote votes; then a fake human
export → parse → resolve → promote labels; and confirm the committed truth is exactly what the
eval-half loaders read. No network, no real corpus writes (tmp dirs). The integration guard."""
from __future__ import annotations

import json

import pytest

from lineation_core import store
from lineation_core.annotations import PanelVote, load_labels
from lineation_core.identity import LineId
from lineation_core.teacher import panel, promote, responses, tasks
from lineation_core.teacher.panel import ChatReply, PanelConfig, ReaderConfig
from lineation_core.teacher.tasks import ItemSpec, Modality


class _Canned:
    """A ChatCompleter that always returns the same reply — the network, stubbed."""

    def __init__(self, content: str):
        self.content = content

    def complete(self, *, model, messages, temperature, max_tokens, response_format=None):
        return ChatReply(content=self.content)


def _grok():
    return PanelConfig(readers=(ReaderConfig("grok", "x/grok", Modality.TEXT),))


def test_create_task_then_fake_panel_and_human_reach_committed_truth(tmp_path):
    ann, st = tmp_path / "annotations", tmp_path / "_teacher"
    recs = store.load_records("57")
    records = {"57": recs}
    votable = [r for r in recs if r.votable][:4]
    spec = ItemSpec.all_votable("b57-r0", [r.id for r in votable])
    task = tasks.build_task(title="acquire-test", instructions="prose vs lineated",
                            specs=[spec], records=records)

    # persist: manifest committed, payload derived — then resolve against the manifest RELOADED FROM DISK
    store.save_task_bundle("t1", task.to_payload(), task.manifest.to_dict(),
                           annotations=ann, store=st)
    _, manifest_d = store.load_task_bundle("t1", annotations=ann, store=st)
    manifest = tasks.TaskManifest.from_dict(manifest_d)
    keys = sorted(manifest.by_key)

    # panel path: fake reply → reps → resolve → promote votes
    reply = json.dumps([{"key": k, "lineation_label": "lineated", "confidence": 0.8} for k in keys])
    reps = panel.run_panel(task, _grok(), _Canned(reply))
    rv = responses.resolve_panel(manifest, [r.response for r in reps], records, complete=True)
    assert not rv.faults and len(rv.votes) == 4
    assert promote.promote_votes(rv.votes, annotations=ann) == 4
    assert len(store.load_vote_rows(annotations=ann)) == 4

    # human path: fake UI export → parse → resolve → promote labels
    human = {"responses": {"b57-r0": {"lines": {k: "prose" for k in keys}, "note": "all prose"}}}
    store.save_human_responses("t1", human, annotations=ann)
    parsed = responses.parse_ui_responses(store.load_human_responses("t1", annotations=ann))
    rl = responses.resolve_adjudication(manifest, parsed, records, title="acquire-test",
                                        complete=True)
    assert not rl.faults and len(rl.labels) == 4
    assert promote.promote_labels(rl.labels, annotations=ann) == 4

    # the committed truth is exactly what the eval-half loader reads
    ls = load_labels(annotations=ann)
    assert len(ls.labels) == 4
    assert all(g.label == "prose" and g.source.value == "human" for g in ls.labels)
    assert {g.id for g in ls.labels} == set(manifest.by_key.values())


def test_promote_is_idempotent(tmp_path):
    ann = tmp_path / "annotations"
    recs = store.load_records("57")
    records = {"57": recs}
    votable = [r for r in recs if r.votable][:3]
    spec = ItemSpec.all_votable("b57-r0", [r.id for r in votable])
    task = tasks.build_task(title="t", instructions="i", specs=[spec], records=records)
    reply = json.dumps([{"key": k, "lineation_label": "prose"} for k in sorted(task.manifest.by_key)])
    reps = panel.run_panel(task, _grok(), _Canned(reply))
    rv = responses.resolve_panel(task.manifest, [r.response for r in reps], records, complete=True)
    promote.promote_votes(rv.votes, annotations=ann)
    promote.promote_votes(rv.votes, annotations=ann)            # re-promote the same task
    assert len(store.load_vote_rows(annotations=ann)) == 3      # merged by (id, tag), not doubled


def test_promote_votes_rejects_raw_multi_rep(tmp_path):
    lid = LineId.mapped("ru", "57", 5, 0)
    dup = [PanelVote(id=lid, tag="grok", label="prose", conf=None),
           PanelVote(id=lid, tag="grok", label="lineated", conf=None)]   # two reps, same (id, tag)
    with pytest.raises(ValueError):
        promote.promote_votes(dup, annotations=tmp_path / "a")           # must not silently collapse
