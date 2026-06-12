# research-pure: tile_regions groups selected lines into whole-run regions, never splitting a unit.
"""Locks Q1: a task region accumulates WHOLE runs up to ~target votable lines without splitting one,
shows context neighbours un-keyed, covers every selected line exactly once, in document order."""
from __future__ import annotations

import json

import pytest

from lineation_core import records, store
from lineation_core.teacher import recipes
from lineation_core.teacher.panel import ReaderConfig
from lineation_core.teacher.tasks import AssetKind, EvidenceAsset, Modality


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
    for run in records.runs(recs):
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
    assert r.selector == recipes.EvalSet("contested") and r.target == 8 and r.lang == "ru"
    assert [x.tag for x in r.readers] == ["grok", "deepseek"]
    assert r.readers[0].modality is Modality.VISION and r.readers[1].modality is Modality.TEXT
    assert r.vision is True                              # a vision reader present


def test_load_recipe_defaults_contract_to_array_and_parses_an_override():
    from lineation_core.teacher.panel import ResponseContract
    assert recipes.load_recipe(_FULL).contract is ResponseContract.JSON_ARRAY   # absent ⇒ array default
    keyed = ('task_id = "x"\ncontract = "json_keyed"\n[selection]\nbooks = ["13"]\n'
             '[[readers]]\ntag = "g"\nmodel = "m"\n')
    assert recipes.load_recipe(keyed).contract is ResponseContract.JSON_KEYED
    with pytest.raises(ValueError):                                         # an unknown contract fails loud
        recipes.load_recipe('task_id="x"\ncontract="bogus"\n[selection]\nbooks=["13"]\n')


def test_load_recipe_parses_sampling_with_per_reader_override():
    # a recipe can AUTHOR sampling: top-level temperature/max_tokens inherit into every reader; a
    # per-reader value overrides. Absent both, the panel defaults apply.
    toml = ('task_id = "x"\ntemperature = 0.5\nmax_tokens = 4096\n[selection]\nbooks = ["13"]\n'
            '[[readers]]\ntag = "g"\nmodel = "m"\n'
            '[[readers]]\ntag = "h"\nmodel = "m2"\ntemperature = 0.0\nmax_tokens = 1024\n')
    r = recipes.load_recipe(toml)
    assert (r.readers[0].temperature, r.readers[0].max_tokens) == (0.5, 4096)   # inherited
    assert (r.readers[1].temperature, r.readers[1].max_tokens) == (0.0, 1024)   # per-reader override
    plain = recipes.load_recipe(_FULL)
    assert all((x.temperature, x.max_tokens) == (0.0, 8192) for x in plain.readers)  # the defaults


def test_parse_selector_grammar_and_unknown_fails_loud():
    # the string is AUTHOR syntax only — parsed once at the toml edge into the closed ADT.
    assert recipes.parse_selector("all") == recipes.AllVotable()
    assert recipes.parse_selector("eval_set:contested") == recipes.EvalSet("contested")
    assert recipes.parse_selector("selection_file:acq") == recipes.SelectionFile("acq")
    for bad in ("bogus", "eval_set:", "all:extra"):
        with pytest.raises(ValueError, match="unknown selector"):
            recipes.parse_selector(bad)


def test_load_recipe_rejects_empty_books_dupe_readers_and_bad_modality():
    with pytest.raises(ValueError):
        recipes.load_recipe('task_id="x"\n[selection]\nbooks=[]\n')
    with pytest.raises(ValueError):
        recipes.load_recipe('task_id="x"\n[selection]\nbooks=["13"]\n'
                            '[[readers]]\ntag="g"\nmodel="m"\n[[readers]]\ntag="g"\nmodel="m2"\n')
    with pytest.raises(ValueError):
        recipes.load_recipe('task_id="x"\n[selection]\nbooks=["13"]\n'
                            '[[readers]]\ntag="g"\nmodel="m"\nmodality="bogus"\n')


def _recipe(selector: recipes.Selector = recipes.AllVotable(), books: tuple = ("57",)) -> recipes.Recipe:
    return recipes.Recipe(task_id="t1", title="T", instructions="prose vs lineated",
                          books=books, selector=selector,
                          readers=(ReaderConfig("grok", "x/grok"),), target=8)


def test_select_lines_all_returns_every_votable_line():
    sel = recipes.select_lines(_recipe(selector=recipes.AllVotable()))
    recs = store.load_records("57")
    assert sel["57"] == {x.id for x in recs if x.votable}


def test_select_lines_from_a_committed_selection_file(tmp_path):
    ann = tmp_path / "annotations"
    picks = [x.id for x in store.load_records("57") if x.votable][:5]
    (ann / "selections").mkdir(parents=True)
    (ann / "selections" / "acq.json").write_text(json.dumps([lid.as_key() for lid in picks]))
    sel = recipes.select_lines(_recipe(selector=recipes.SelectionFile("acq")), annotations=ann)
    assert sel["57"] == set(picks)


def test_build_persists_a_task_bundle_with_the_selected_lines(tmp_path):
    ann, st = tmp_path / "annotations", tmp_path / "_teacher"
    task = recipes.build(_recipe(selector=recipes.AllVotable()), annotations=ann, teacher_store=st)
    assert (ann / "tasks" / "t1.manifest.json").is_file()      # manifest committed
    assert (st / "t1" / "payload.json").is_file()              # payload derived
    votable = {x.id for x in store.load_records("57") if x.votable}
    assert set(task.manifest.by_key.values()) == votable        # every votable line tiled in
    payload, _ = store.load_task_bundle("t1", annotations=ann, store=st)
    assert "manifest" not in payload and payload["items"]
    assert all("images" not in it for it in payload["items"])   # text recipe → no composites


def _vision_recipe() -> recipes.Recipe:
    return recipes.Recipe(task_id="v", title="T", instructions="i", books=("57",),
                          selector=recipes.AllVotable(),
                          readers=(ReaderConfig("grok", "x/grok", Modality.VISION),), target=8)


def test_build_vision_without_render_fails_loud(tmp_path):
    with pytest.raises(ValueError):                            # no silent text fallback
        recipes.build(_vision_recipe(), annotations=tmp_path / "a", teacher_store=tmp_path / "s")


def test_build_vision_with_render_attaches_composites_to_every_item(tmp_path):
    def render(specs):
        return {s.region_id: (EvidenceAsset(kind=AssetKind.COMPOSITE,
                                            data_uri="data:image/png;base64,AA"),) for s in specs}
    recipes.build(_vision_recipe(), annotations=tmp_path / "a", teacher_store=tmp_path / "s",
                  render=render)
    payload, _ = store.load_task_bundle("v", annotations=tmp_path / "a", store=tmp_path / "s")
    assert payload["items"] and all(it.get("images") for it in payload["items"])


def test_build_vision_render_missing_a_composite_fails_loud(tmp_path):
    with pytest.raises(ValueError):
        recipes.build(_vision_recipe(), annotations=tmp_path / "a", teacher_store=tmp_path / "s",
                      render=lambda specs: {})                 # renders nothing


def test_tile_distant_runs_become_separate_regions():
    recs = store.load_records("57")
    rns = [run for run in records.runs(recs) if any(recs[i].votable for i in run)]
    far = next((run for run in rns[1:] if run[0] - rns[0][-1] - 1 > 8), None)
    assert far is not None                                     # the book has a run > max_gap away
    first = next(recs[i].id for i in rns[0] if recs[i].votable)
    distant = next(recs[i].id for i in far if recs[i].votable)
    specs = recipes.tile_regions("57", recs, {first, distant}, target=10, max_gap=8)
    assert len(specs) == 2 and all(len(s.votable) == 1 for s in specs)   # not one giant span


def test_select_lines_raises_on_out_of_scope_book(tmp_path):
    ann = tmp_path / "annotations"
    picks = [x.id for x in store.load_records("57") if x.votable][:3]
    (ann / "selections").mkdir(parents=True)
    (ann / "selections" / "acq.json").write_text(json.dumps([lid.as_key() for lid in picks]))
    r = recipes.Recipe(task_id="t", title="T", instructions="i", books=("13",),  # 13, not 57
                       selector=recipes.SelectionFile("acq"), readers=(ReaderConfig("g", "m"),))
    with pytest.raises(ValueError):
        recipes.select_lines(r, annotations=ann)


def test_recipe_panel_and_ingest_reach_committed_truth(tmp_path):
    import re

    from lineation_core.annotations import load_labels
    from lineation_core.teacher.panel import ChatReply

    ann, st = tmp_path / "annotations", tmp_path / "_teacher"
    picks = [x.id for x in store.load_records("57") if x.votable][:5]
    (ann / "selections").mkdir(parents=True)
    (ann / "selections" / "acq.json").write_text(json.dumps([lid.as_key() for lid in picks]))
    r = recipes.Recipe(task_id="acq", title="A", instructions="prose vs lineated", books=("57",),
                       selector=recipes.SelectionFile("acq"),
                       readers=(ReaderConfig("grok", "x/grok"),), target=8)
    recipes.build(r, annotations=ann, teacher_store=st)

    class Echo:                                   # answers exactly the keys it is shown
        def complete(self, *, model, messages, temperature, max_tokens, response_format=None):
            listing = messages[0]["content"][0]["text"].split("Return ONLY")[0]   # not the example
            keys = sorted(set(re.findall(r"\bL\d+\b", listing)))
            return ChatReply(content=json.dumps([{"key": k, "lineation_label": "lineated"} for k in keys]))

    assert recipes.panel(r, Echo(), annotations=ann, teacher_store=st) == 5
    assert len(store.load_vote_rows(annotations=ann)) == 5
    assert store.load_panel_reps("acq", annotations=ann)        # per-rep evidence kept

    payload, _ = store.load_task_bundle("acq", annotations=ann, store=st)
    human = {"responses": {it["id"]: {"lines": {ln["key"]: "prose" for ln in it["lines"]}}
                           for it in payload["items"]}}
    store.save_human_responses("acq", human, annotations=ann)
    assert recipes.ingest(r, annotations=ann, teacher_store=st) == 5
    ls = load_labels(annotations=ann)
    assert len(ls.labels) == 5 and all(g.label == "prose" for g in ls.labels)


def test_load_recipe_reads_per_modality_prompt_files(tmp_path):
    (tmp_path / "vis.md").write_text("PAGE-AUTHORITY PROMPT")
    (tmp_path / "txt.md").write_text("LISTING-AUTHORITY PROMPT")
    toml = ('task_id = "t"\n[prompts]\nvision = "vis.md"\ntext = "txt.md"\n'
            '[selection]\nbooks = ["57"]\n[[readers]]\ntag = "grok"\nmodel = "x/g"\nmodality = "vision"\n')
    r = recipes.load_recipe(toml, prompts_dir=tmp_path)
    assert r.prompts[Modality.VISION] == "PAGE-AUTHORITY PROMPT"
    assert r.prompts[Modality.TEXT] == "LISTING-AUTHORITY PROMPT"
    assert r.instructions == "PAGE-AUTHORITY PROMPT"          # vision = the default (human + fallback)


def test_load_recipe_reads_an_explicit_human_prompt(tmp_path):
    # an explicit `human` key names the adjudicator's prompt; modality keys stay reader prompts.
    (tmp_path / "vis.md").write_text("PAGE-AUTHORITY PROMPT")
    (tmp_path / "adj.md").write_text("ADJUDICATOR PROMPT")
    toml = ('task_id = "t"\n[prompts]\nvision = "vis.md"\nhuman = "adj.md"\n'
            '[selection]\nbooks = ["57"]\n[[readers]]\ntag = "g"\nmodel = "m"\nmodality = "vision"\n')
    r = recipes.load_recipe(toml, prompts_dir=tmp_path)
    assert r.instructions == "ADJUDICATOR PROMPT"             # the human's own prompt
    assert r.prompts == {Modality.VISION: "PAGE-AUTHORITY PROMPT"}   # human is NOT a reader modality


def test_load_recipe_rejects_both_prompts_and_inline_instructions(tmp_path):
    (tmp_path / "v.md").write_text("V")
    toml = ('task_id = "t"\ninstructions = "inline"\n[prompts]\nvision = "v.md"\n'
            '[selection]\nbooks = ["57"]\n')
    with pytest.raises(ValueError, match="both"):
        recipes.load_recipe(toml, prompts_dir=tmp_path)


# --- page-sizing post-pass: split an over-page region while keeping every votable line --------

from dataclasses import dataclass as _dataclass  # noqa: E402

from lineation_core.identity import LineId  # noqa: E402
from lineation_core.teacher.tasks import ItemSpec  # noqa: E402


@_dataclass(frozen=True)
class _Rec:
    """A minimal record stand-in — `page_size_regions` reads only `.id` (and the LineId's ordinal)."""
    id: LineId


def _consecutive_records(n: int):
    return [_Rec(LineId.mapped("ru", "57", i, 0)) for i in range(n)]


def test_page_size_splits_an_over_page_region_keeping_every_votable_line():
    recs = _consecutive_records(300)                       # ordinals 0..299
    votable = [recs[i].id for i in range(0, 300, 2)]       # 150 votable, span 0..298 > 120
    spec = ItemSpec(region_id="big", region=tuple(r.id for r in recs),
                    votable=frozenset(votable))
    out = recipes.page_size_regions([spec], recs, max_span=120, context_radius=2)
    assert len(out) > 1                                    # split into page-sized sub-regions
    covered = [lid for s in out for lid in s.votable]
    assert sorted(covered) == sorted(votable)              # every votable line kept, none dup'd
    assert len(covered) == len(set(covered))
    for s in out:                                          # each cluster fits within one page
        ords = sorted(lid.src_ordinal for lid in s.votable)
        assert ords[-1] - ords[0] <= 120


def test_page_size_passes_an_in_page_region_through_unchanged():
    recs = _consecutive_records(40)
    spec = ItemSpec(region_id="small", region=tuple(r.id for r in recs),
                    votable=frozenset(r.id for r in recs[:20]))   # span 0..19 ≤ 120
    out = recipes.page_size_regions([spec], recs, max_span=120, context_radius=2)
    assert out == [spec]                                   # identical object, untouched


def test_page_size_carries_unmapped_votables_through_a_split():
    # a mixed mapped+unmapped over-page region: the mapped votables span > max_span (so it splits) and
    # the region also holds UNMAPPED votables (§14-P1 span-drops). The split must keep EVERY votable —
    # union of sub-region votables == spec.votable — not silently drop the unmapped ones.
    mapped = [LineId.mapped("ru", "57", o, 0) for o in range(0, 300, 2)]       # span 0..298 > 120
    unmapped = [LineId.unmapped("ru", "57", d, 0) for d in (10, 150, 290)]     # no source ordinal
    # records carry both kinds so each id has a document position to bin the unmapped lines by.
    ids = sorted([*mapped, *unmapped], key=lambda x: x.src_ordinal)
    recs = [_Rec(lid) for lid in ids]
    spec = ItemSpec(region_id="mixed", region=tuple(r.id for r in recs),
                    votable=frozenset([*mapped, *unmapped]))
    out = recipes.page_size_regions([spec], recs, max_span=120, context_radius=2)
    assert len(out) > 1                                    # the mapped span forces a split
    covered = [lid for s in out for lid in s.votable]
    assert sorted(covered) == sorted(spec.votable)         # every votable kept, none dup'd
    assert len(covered) == len(set(covered))
    assert set(unmapped) <= set(covered)                   # the unmapped span-drops survived the split


def test_select_lines_refuses_a_wrong_language_selection(tmp_path):
    ann = tmp_path / "annotations"
    picks = [x.id for x in store.load_records("57") if x.votable][:3]
    keys = [["en", lid.book_id, lid.src_ordinal, lid.sub] for lid in picks]  # en ids, ru recipe
    (ann / "selections").mkdir(parents=True)
    (ann / "selections" / "acq.json").write_text(json.dumps(keys))
    with pytest.raises(ValueError, match="lang"):
        recipes.select_lines(_recipe(selector=recipes.SelectionFile("acq")), annotations=ann)


def test_route_and_ingest_stamp_holdout_from_the_recipe_membership():
    from lineation_core.annotations import LabelSource, LineLabel
    from lineation_core.identity import LineId
    from lineation_core.teacher.recipes import _holdout_members
    from dataclasses import replace as drep

    lid = LineId("ru", "57", 5, 0)
    rec = _recipe(selector=recipes.AllVotable())
    assert _holdout_members(rec, annotations=None) == frozenset()   # no membership declared

    rec2 = drep(rec, holdout_eval_set="frozen-x")
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as td:
        ann = pathlib.Path(td)
        (ann / "eval_sets").mkdir()
        (ann / "eval_sets" / "frozen-x.json").write_text(json.dumps([lid.as_key()]))
        members = _holdout_members(rec2, annotations=ann)
    assert members == frozenset({lid})


def test_holdout_eval_set_parses_from_the_toml():
    d = {"task_id": "t", "selection": {"books": ["57"]}, "holdout_eval_set": "e1-frozen"}
    assert recipes.recipe_from_dict(d).holdout_eval_set == "e1-frozen"
    d2 = {"task_id": "t", "selection": {"books": ["57"]}}
    assert recipes.recipe_from_dict(d2).holdout_eval_set is None


def test_build_page_sizes_oversized_runs_for_text_recipes_too(tmp_path):
    # a selection spanning a giant run must not produce a region wider than the page cap
    recs = store.load_records("57")
    from lineation_core.teacher.tasks import PAGE_SPAN_CAP
    votable = [r for r in recs if r.votable]
    picks = {votable[0].id, votable[-1].id}
    tiled = recipes.tile_regions("57", recs, picks, target=10, context_radius=1, max_gap=10**9)
    assert any(s.region[-1].src_ordinal - s.region[0].src_ordinal > PAGE_SPAN_CAP for s in tiled)
    sized = recipes.page_size_regions(tiled, recs, context_radius=1)
    for s in sized:
        mapped = [x for x in s.votable if x.is_mapped]
        assert max(x.src_ordinal for x in mapped) - min(x.src_ordinal for x in mapped) <= PAGE_SPAN_CAP
    assert {x for s in sized for x in s.votable} == picks
