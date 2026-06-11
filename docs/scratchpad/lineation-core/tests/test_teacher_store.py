# research-pure: the teacher store edge round-trips task bundles/responses and promotes truth.
"""All teacher IO goes through `store`: the manifest is committed source, the payload is derived,
and promotion writes back exactly the committed shapes the eval half loads. Uses tmp dirs — never
the real annotations."""
from __future__ import annotations

import pytest
from lineation_core import store


def test_task_bundle_splits_committed_manifest_from_derived_payload(tmp_path):
    ann, st = tmp_path / "annotations", tmp_path / "_teacher"
    payload = {"title": "t", "items": [{"id": "b57-r0", "lines": [{"key": "L001", "text": "x"}]}]}
    manifest = {"by_key": {"L001": ["ru", "57", 1, 0]}, "text_hash_by_key": {"L001": "abc123"}}
    store.save_task_bundle("task1", payload, manifest, annotations=ann, store=st)
    assert (ann / "tasks" / "task1.manifest.json").is_file()        # manifest committed
    assert (st / "task1" / "payload.json").is_file()                # payload derived
    assert not (st / "task1" / "manifest.json").exists()            # manifest is NOT in the derived store
    p2, m2 = store.load_task_bundle("task1", annotations=ann, store=st)
    assert p2 == payload and m2 == manifest


def test_human_responses_round_trip(tmp_path):
    ann = tmp_path / "annotations"
    data = {"responses": {"b57-r0": {"lines": {"L001": "prose"}, "note": "n"}}}
    store.save_human_responses("task1", data, annotations=ann)
    assert store.load_human_responses("task1", annotations=ann) == data


def test_panel_reps_round_trip(tmp_path):
    ann = tmp_path / "annotations"
    rows = [{"id": ["ru", "57", 1, 0], "tag": "grok", "label": "lineated", "conf": 0.9, "rep": 0}]
    store.save_panel_reps("run1", rows, annotations=ann)
    assert store.load_panel_reps("run1", annotations=ann) == rows


def test_promotion_writes_the_committed_truth_shapes(tmp_path):
    ann = tmp_path / "annotations"
    labels = [{"id": ["ru", "57", 1, 0], "label": "prose", "source": "human"}]
    votes = [{"id": ["ru", "57", 1, 0], "tag": "grok", "label": "lineated", "conf": 0.8}]
    eset = [["ru", "57", 1, 0]]    # membership only — the label lives in labels.jsonl
    store.write_label_rows(labels, annotations=ann)
    store.write_vote_rows(votes, annotations=ann)
    store.write_eval_set("contested", eset, annotations=ann)
    assert store.load_label_rows(annotations=ann) == labels
    assert store.load_vote_rows(annotations=ann) == votes
    assert store.load_eval_set("contested", annotations=ann) == eset


def test_load_task_bundle_fails_loud_when_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        store.load_task_bundle("nope", annotations=tmp_path / "a", store=tmp_path / "s")
