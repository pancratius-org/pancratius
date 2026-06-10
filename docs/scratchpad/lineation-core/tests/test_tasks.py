# research-pure: build_task mints opaque L001 keys and NEVER leaks a src_ordinal to the wire.
"""Locks the load-bearing teacher invariant: `build_task → to_payload` carries task-local keys
only; the `LineId` lives solely in the private manifest, which `to_payload` never emits."""
from __future__ import annotations

import json
import re

from lineation_core import store
from lineation_core.teacher import tasks
from lineation_core.teacher.tasks import AssetKind, EvidenceAsset, ItemSpec


def _votable(book: str = "57", n: int = 7):
    return [r for r in store.load_records(book) if r.votable][:n]


def _one_region(recs, n_vote: int = 5):
    region = tuple(r.id for r in recs)                  # all shown, in order
    votable = frozenset(r.id for r in recs[:n_vote])    # first n_vote polled; the rest are context
    return ItemSpec(region_id="b57-r0", region=region, votable=votable), recs


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
    assert back.item_by_key == task.manifest.item_by_key
    assert all(item == "b57-r0" for item in task.manifest.item_by_key.values())
    by_id = {r.id: r for r in records["57"]}
    for key, lid in task.manifest.by_key.items():
        assert task.manifest.text_hash_by_key[key] == by_id[lid].line_text_hash


def test_keys_continue_across_items_not_reset_per_region():
    recs = _votable(n=8)
    a = ItemSpec.all_votable("b57-r0", [r.id for r in recs[:3]])
    b = ItemSpec.all_votable("b57-r1", [r.id for r in recs[3:6]])
    task = tasks.build_task(title="t", instructions="i", specs=[a, b],
                            records={"57": store.load_records("57")})
    assert [ln.key for ln in task.items[0].lines] == ["L001", "L002", "L003"]
    assert [ln.key for ln in task.items[1].lines] == ["L004", "L005", "L006"]   # not reset


def test_region_is_rendered_in_caller_order_never_sorted():
    recs = _votable(n=3)
    rev = list(reversed(recs))                          # deliberately NOT document order
    spec = ItemSpec.all_votable("b57-r0", [r.id for r in rev])
    task = tasks.build_task(title="t", instructions="i", specs=[spec],
                            records={"57": store.load_records("57")})
    assert task.manifest.by_key["L001"] == rev[0].id    # L001 = the FIRST id the caller gave...
    assert task.manifest.by_key["L003"] == rev[2].id    # ...not the document-first (no re-sort)
    ctx = task.items[0].context
    assert ctx.index("L001") < ctx.index("L002") < ctx.index("L003")   # rendered in caller order


def test_multipage_images_survive_the_payload_roundtrip_in_order():
    # a vision region renders one image per page; to_payload → from_bundle must carry EVERY page, in
    # order, so a re-run/resume attaches the full evidence (not just page 1).
    recs = _votable(n=3)
    spec = ItemSpec.all_votable("b57-r0", [r.id for r in recs])
    pages = tuple(EvidenceAsset(kind=AssetKind.COMPOSITE, data_uri=f"data:image/png;base64,P{n}")
                  for n in range(3))
    task = tasks.build_task(title="t", instructions="i", specs=[spec],
                            records={"57": store.load_records("57")}, assets={"b57-r0": pages})
    payload = task.to_payload()
    assert payload["items"][0]["images"] == [a.data_uri for a in pages]      # all pages, in order
    back = tasks.Task.from_bundle(payload, task.manifest.to_dict())
    item = back.items[0]
    assert item.assets and all(a.kind is AssetKind.COMPOSITE for a in item.assets)
    assert [a.data_uri for a in item.assets] == [a.data_uri for a in pages]  # reconstructed in order
