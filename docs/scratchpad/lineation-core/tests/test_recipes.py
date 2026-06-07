# research-pure: tile_regions groups selected lines into whole-run regions, never splitting a unit.
"""Locks Q1: a task region accumulates WHOLE runs up to ~target votable lines without splitting one,
shows context neighbours un-keyed, covers every selected line exactly once, in document order."""
from __future__ import annotations

import json

import pytest

from lineation_core import sequence, store
from lineation_core.teacher import recipes
from lineation_core.teacher.tasks import Modality


def _first_votable(n: int):
    return [r for r in store.load_records("57") if r.votable][:n]


def test_tile_covers_every_selected_line_exactly_once():
    recs = store.load_records("57")
    selected = {r.id for r in _first_votable(20)}
    specs = recipes.tile_regions("57", recs, selected, target=8)
    covered = [lid for s in specs for lid in s.votable]
    assert sorted(covered) == sorted(selected) and len(covered) == len(set(covered))


def test_tile_never_splits_a_run_across_regions():
    recs = store.load_records("57")
    selected = {r.id for r in _first_votable(20)}
    specs = recipes.tile_regions("57", recs, selected, target=4)   # small target stresses splitting
    region_votables = [s.votable for s in specs]
    for run in sequence.runs(recs):
        run_sel = {recs[i].id for i in run} & selected
        if run_sel:
            homes = [rv for rv in region_votables if run_sel & rv]
            assert len(homes) == 1 and run_sel <= homes[0]         # the whole run's lines in ONE region


def test_tile_shows_context_unkeyed_and_is_a_deterministic_document_span():
    recs = store.load_records("57")
    mid = _first_votable(11)[10]
    specs = recipes.tile_regions("57", recs, {mid.id}, target=8, context_radius=2)
    assert len(specs) == 1
    s = specs[0]
    assert s.votable == frozenset({mid.id})           # only the selected line is votable
    assert mid.id in s.region and len(s.region) > 1   # context neighbours shown (un-keyed)
    assert s.region_id == "b57-r0"                     # deterministic id
    pos = {r.id: i for i, r in enumerate(recs)}
    idxs = [pos[lid] for lid in s.region]
    assert idxs == list(range(idxs[0], idxs[-1] + 1))  # a contiguous document-order span, not sorted apart


_FULL = """
task_id = "acq-1"
title = "Acquire"
instructions = "prose vs lineated"
reps = 3

[selection]
books = ["13", "37"]
selector = "eval_set:contested"
target = 8

[[readers]]
tag = "grok"
model = "x-ai/grok-4"
modality = "vision"

[[readers]]
tag = "deepseek"
model = "deepseek/chat"
"""


def test_load_recipe_parses_a_full_toml():
    r = recipes.load_recipe(_FULL)
    assert r.task_id == "acq-1" and r.books == ("13", "37") and r.reps == 3
    assert r.selector == "eval_set:contested" and r.target == 8 and r.lang == "ru"
    assert [x.tag for x in r.readers] == ["grok", "deepseek"]
    assert r.readers[0].modality is Modality.VISION and r.readers[1].modality is Modality.TEXT
    assert r.vision is True                              # a vision reader present


def test_load_recipe_rejects_empty_books_dupe_readers_and_bad_modality():
    with pytest.raises(ValueError):
        recipes.load_recipe('task_id="x"\n[selection]\nbooks=[]\n')
    with pytest.raises(ValueError):
        recipes.load_recipe('task_id="x"\n[selection]\nbooks=["13"]\n'
                            '[[readers]]\ntag="g"\nmodel="m"\n[[readers]]\ntag="g"\nmodel="m2"\n')
    with pytest.raises(ValueError):
        recipes.load_recipe('task_id="x"\n[selection]\nbooks=["13"]\n'
                            '[[readers]]\ntag="g"\nmodel="m"\nmodality="bogus"\n')


def _recipe(selector: str = "all", books: tuple = ("57",)) -> recipes.Recipe:
    return recipes.Recipe(task_id="t1", title="T", instructions="prose vs lineated",
                          books=books, selector=selector,
                          readers=(recipes.ReaderSpec("grok", "x/grok"),), target=8)


def test_select_lines_all_returns_every_votable_line():
    sel = recipes.select_lines(_recipe(selector="all"))
    recs = store.load_records("57")
    assert sel["57"] == {x.id for x in recs if x.votable}


def test_select_lines_from_a_committed_selection_file(tmp_path):
    ann = tmp_path / "annotations"
    picks = [x.id for x in store.load_records("57") if x.votable][:5]
    (ann / "selections").mkdir(parents=True)
    (ann / "selections" / "acq.json").write_text(json.dumps([lid.as_key() for lid in picks]))
    sel = recipes.select_lines(_recipe(selector="selection_file:acq"), annotations=ann)
    assert sel["57"] == set(picks)


def test_build_persists_a_task_bundle_with_the_selected_lines(tmp_path):
    ann, st = tmp_path / "annotations", tmp_path / "_teacher"
    task = recipes.build(_recipe(selector="all"), annotations=ann, teacher_store=st)
    assert (ann / "tasks" / "t1.manifest.json").is_file()      # manifest committed
    assert (st / "t1" / "payload.json").is_file()              # payload derived
    votable = {x.id for x in store.load_records("57") if x.votable}
    assert set(task.manifest.by_key.values()) == votable        # every votable line tiled in
    payload, _ = store.load_task_bundle("t1", annotations=ann, store=st)
    assert "manifest" not in payload and payload["items"]
    assert all("image" not in it for it in payload["items"])    # text recipe → no composites
