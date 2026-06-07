# research-pure: build_task mints opaque L001 keys and NEVER leaks a src_ordinal to the wire.
"""Locks the load-bearing teacher invariant: `build_task → to_payload` carries task-local keys
only; the `LineId` lives solely in the private manifest, which `to_payload` never emits."""
from __future__ import annotations

import json
import re

from lineation_core import store
from lineation_core.teacher import tasks
from lineation_core.teacher.tasks import ItemSpec


def _votable(book: str = "57", n: int = 7):
    return [r for r in store.load_records(book) if r.votable][:n]


def _one_region(recs, n_vote: int = 5):
    vote = tuple(r.id for r in recs[:n_vote])
    ctx = tuple(r.id for r in recs[n_vote:])
    return ItemSpec(region_id="b57-r0", vote_ids=vote, context_ids=ctx), recs


def test_build_task_mints_contiguous_opaque_keys():
    recs = _votable()
    spec, _ = _one_region(recs)
    task = tasks.build_task(title="t", instructions="i", specs=[spec],
                            records={"57": store.load_records("57")})
    keys = [ln.key for it in task.items for ln in it.lines]
    assert keys == ["L001", "L002", "L003", "L004", "L005"]      # per-task, contiguous, opaque
    assert set(task.manifest.by_key) == set(keys)                # every wire key resolves
    assert all(re.fullmatch(r"L\d{3,}", k) for k in keys)


def test_payload_omits_manifest_and_never_leaks_a_lineid():
    recs = _votable()
    spec, _ = _one_region(recs)
    task = tasks.build_task(title="t", instructions="i", specs=[spec],
                            records={"57": store.load_records("57")})
    payload = task.to_payload()
    blob = json.dumps(payload, ensure_ascii=False)

    assert "manifest" not in payload and "by_key" not in blob
    for it in payload["items"]:
        assert all(re.fullmatch(r"L\d{3,}", ln["key"]) for ln in it["lines"])
    # the invariant: no LineId serialization (with its src_ordinal) appears anywhere in the wire
    for lid in task.manifest.by_key.values():
        assert json.dumps(lid.as_key()) not in blob


def test_context_listing_uses_opaque_keys():
    recs = _votable()
    spec, _ = _one_region(recs)
    task = tasks.build_task(title="t", instructions="i", specs=[spec],
                            records={"57": store.load_records("57")})
    context = task.items[0].context
    assert "L001" in context                 # votable lines keyed opaquely
    assert "·" in context                    # the 2 context lines shown un-keyed for orientation
    first_vote = task.manifest.by_key["L001"]
    assert f"{first_vote.src_ordinal}.{first_vote.sub}" not in context   # no src-ordinal key


def test_manifest_roundtrips_and_captures_text_hash():
    recs = _votable()
    spec, _ = _one_region(recs)
    records = {"57": store.load_records("57")}
    task = tasks.build_task(title="t", instructions="i", specs=[spec], records=records)
    back = tasks.TaskManifest.from_dict(task.manifest.to_dict())
    assert back.by_key == task.manifest.by_key
    by_id = {r.id: r for r in records["57"]}
    for key, lid in task.manifest.by_key.items():
        assert task.manifest.text_hash_by_key[key] == by_id[lid].line_text_hash


def test_keys_continue_across_items_not_reset_per_region():
    recs = _votable(n=8)
    a = ItemSpec(region_id="b57-r0", vote_ids=tuple(r.id for r in recs[:3]))
    b = ItemSpec(region_id="b57-r1", vote_ids=tuple(r.id for r in recs[3:6]))
    task = tasks.build_task(title="t", instructions="i", specs=[a, b],
                            records={"57": store.load_records("57")})
    assert [ln.key for ln in task.items[0].lines] == ["L001", "L002", "L003"]
    assert [ln.key for ln in task.items[1].lines] == ["L004", "L005", "L006"]   # not reset
