# research-pure: route() — the SETTLED decision policy wired into the live promote path.
"""Locks the live routing step end to end: the promoted panel votes for a task are restricted to that
task's lines, the legacy anchor-led gate is applied, ACCEPT lines become `gate` truth and HUMAN lines
are tiled into a `<task_id>-adjudication` sub-task. Proves the contract that makes the loop safe:

  - an ACCEPT promotes ONE `gate` label (anchor conf, auditable provenance), no human queue;
  - a HUMAN route promotes NOTHING and builds a sub-task holding EXACTLY the routed lines;
  - OPERATIONAL (coverage-gap) routes are counted as escalatable, TERMINAL (split) routes are not;
  - PRECEDENCE: a human/override label is never overwritten by the gate nor re-queued (this is also
    what keeps a frozen, human-adjudicated eval line out of the loop);
  - route is idempotent; an unrouted recipe / a roster reader outside the panel FAILS LOUD;
  - the whole loop closes: route → adjudicate.html export → ingest reaches committed truth.
"""
from __future__ import annotations

import pytest

from lineation_core import store
from lineation_core.annotations import LabelSource, LineLabel, PanelVote, load_labels
from lineation_core.identity import Label, LineId
from lineation_core.teacher import promote, recipes, tasks
from lineation_core.teacher.tasks import ItemSpec

# The settled live config: the legacy anchor-led gate (min_support=2, min_core_agree=2,
# conf_floor=0.7, tolerating one dissent) over a grok anchor + deepseek/gemini support.
_ROUTED = """
task_id = "rt"
title = "Route test"
instructions = "prose vs lineated"

[selection]
books = ["57"]

[[readers]]
tag = "grok"
model = "x/grok"
[[readers]]
tag = "deepseek"
model = "x/ds"
[[readers]]
tag = "gemini"
model = "x/gem"

[roster]
anchor = "grok"
support = ["deepseek", "gemini"]

[decision]
name = "legacy"
kind = "anchor_led"
  [decision.params]
  min_support = 2
  min_core_agree = 2
  conf_floor = 0.7
  require_no_split = false
"""

_PLAIN = """
task_id = "rt"
[selection]
books = ["57"]
[[readers]]
tag = "grok"
model = "x/grok"
"""


def _recipe() -> recipes.Recipe:
    return recipes.load_recipe(_ROUTED)


def _votable_ids(n: int) -> list[LineId]:
    return [r.id for r in store.load_records("57") if r.votable][:n]


def _build_task(ann, st, ids: list[LineId]) -> None:
    """Persist the campaign task bundle `route` reads — its manifest scopes the votes to these lines."""
    records = {"57": store.load_records("57")}
    spec = ItemSpec.all_votable("b57-r0", ids)
    task = tasks.build_task(title="Route test", instructions="prose vs lineated",
                            specs=[spec], records=records)
    store.save_task_bundle("rt", task.to_payload(), task.manifest.to_dict(),
                           annotations=ann, store=st)


def _v(lid: LineId, tag: str, label: Label, conf: float | None = None,
       task: str = "rt") -> PanelVote:
    return PanelVote(id=lid, tag=tag, label=label, conf=conf, task=task)


def _agree(lid: LineId, label: Label, conf: float = 0.9, task: str = "rt") -> list[PanelVote]:
    """Anchor + both support agree → the gate ACCEPTS."""
    return [_v(lid, "grok", label, conf, task), _v(lid, "deepseek", label, task=task),
            _v(lid, "gemini", label, task=task)]


def _split(lid: LineId, conf: float = 0.9, task: str = "rt") -> list[PanelVote]:
    """Anchor outvoted by both support → ANCHOR_PANEL_SPLIT, a TERMINAL human route."""
    return [_v(lid, "grok", "lineated", conf, task), _v(lid, "deepseek", "prose", task=task),
            _v(lid, "gemini", "prose", task=task)]


def _human_seed(lid: LineId, label: Label) -> LineLabel:
    return LineLabel(id=lid, label=label, source=LabelSource.HUMAN, confidence=None,
                     audit_status="adjudicated", notes="", provenance={}, line_text_hash=None)


def _labels(ann) -> dict[LineId, LineLabel]:
    return {g.id: g for g in load_labels(annotations=ann).labels}


# --- ACCEPT ------------------------------------------------------------------------------------

def test_accept_promotes_one_gate_label_and_queues_no_human(tmp_path):
    ann, st = tmp_path / "annotations", tmp_path / "_teacher"
    ids = _votable_ids(3)
    _build_task(ann, st, ids)
    promote.promote_votes([v for lid in ids for v in _agree(lid, "lineated")], annotations=ann)

    r = recipes.route(_recipe(), annotations=ann, teacher_store=st)
    assert (r.accepted, r.queued_human, r.adjudication_task_id) == (3, 0, None)

    ls = _labels(ann)
    assert set(ls) == set(ids)
    for lid in ids:
        g = ls[lid]
        assert g.label == "lineated" and g.source is LabelSource.GATE
        assert g.confidence == 0.9 and g.audit_status == recipes.GATE_AUDIT_STATUS
        assert g.provenance["policy"] == "legacy" and g.provenance["reason"] == "full_support"
        assert g.provenance["votes"] == {"grok": "lineated", "deepseek": "lineated",
                                         "gemini": "lineated"}


# --- HUMAN -------------------------------------------------------------------------------------

def test_human_route_promotes_nothing_and_builds_a_subtask_of_exactly_the_routed_lines(tmp_path):
    ann, st = tmp_path / "annotations", tmp_path / "_teacher"
    ids = _votable_ids(2)
    _build_task(ann, st, ids)
    promote.promote_votes([v for lid in ids for v in _split(lid)], annotations=ann)

    r = recipes.route(_recipe(), annotations=ann, teacher_store=st)
    assert r.accepted == 0 and r.queued_human == 2
    assert r.adjudication_task_id == "rt-adjudication"
    assert r.operational == 0 and r.by_reason == {"anchor_panel_split": 2}   # a split is terminal
    # the gate promoted nothing — and writes no empty truth file (missing ≠ empty for committed truth)
    with pytest.raises(FileNotFoundError):
        load_labels(annotations=ann)

    _, manifest_d = store.load_task_bundle("rt-adjudication", annotations=ann, store=st)
    queued = set(tasks.TaskManifest.from_dict(manifest_d).by_key.values())
    assert queued == set(ids)


def test_operational_coverage_gap_is_counted_as_escalatable(tmp_path):
    ann, st = tmp_path / "annotations", tmp_path / "_teacher"
    (lid,) = _votable_ids(1)
    _build_task(ann, st, [lid])
    promote.promote_votes([_v(lid, "grok", "lineated", 0.9)], annotations=ann)  # only the anchor voted

    r = recipes.route(_recipe(), annotations=ann, teacher_store=st)
    assert r.queued_human == 1 and r.operational == 1
    assert r.by_reason == {"insufficient_coverage": 1}


# --- PRECEDENCE --------------------------------------------------------------------------------

def test_human_label_is_neither_overwritten_nor_requeued(tmp_path):
    ann, st = tmp_path / "annotations", tmp_path / "_teacher"
    would_accept, would_route = _votable_ids(2)
    _build_task(ann, st, [would_accept, would_route])
    # both lines already settled by a human as `prose`
    promote.promote_labels([_human_seed(would_accept, "prose"), _human_seed(would_route, "prose")],
                           annotations=ann)
    promote.promote_votes(_agree(would_accept, "lineated") + _split(would_route), annotations=ann)

    r = recipes.route(_recipe(), annotations=ann, teacher_store=st)
    assert r.accepted == 0 and r.accepts_protected == 1
    assert r.queued_human == 0 and r.human_protected == 1 and r.adjudication_task_id is None

    ls = _labels(ann)
    assert ls[would_accept].source is LabelSource.HUMAN and ls[would_accept].label == "prose"


# --- ROBUSTNESS: idempotence + fail-loud -------------------------------------------------------

def test_route_is_idempotent(tmp_path):
    ann, st = tmp_path / "annotations", tmp_path / "_teacher"
    ids = _votable_ids(2)
    _build_task(ann, st, ids)
    promote.promote_votes([v for lid in ids for v in _agree(lid, "prose")], annotations=ann)

    recipes.route(_recipe(), annotations=ann, teacher_store=st)
    first = _labels(ann)
    recipes.route(_recipe(), annotations=ann, teacher_store=st)              # re-route the same votes
    again = _labels(ann)
    assert len(again) == 2 and set(again) == set(ids)                        # gate-over-gate, not doubled
    # not just the COUNT — the gate label is byte-for-byte the same (label, source, conf, provenance)
    for lid in ids:
        assert again[lid].to_dict() == first[lid].to_dict()


def test_reroute_for_a_changed_human_set_fails_loud(tmp_path):
    ann, st = tmp_path / "annotations", tmp_path / "_teacher"
    a, b = _votable_ids(2)
    _build_task(ann, st, [a, b])
    promote.promote_votes(_split(a) + _split(b), annotations=ann)            # both → human
    r1 = recipes.route(_recipe(), annotations=ann, teacher_store=st)
    assert r1.queued_human == 2

    # new votes flip `a` to an ACCEPT; the human queue would shrink to {b} and re-mint keys —
    # which would corrupt any responses already filed against the {a, b} manifest. Refuse it.
    promote.promote_votes(_agree(a, "lineated"), annotations=ann)
    with pytest.raises(ValueError, match="different line set"):
        recipes.route(_recipe(), annotations=ann, teacher_store=st)


def test_reroute_for_the_same_human_set_is_idempotent(tmp_path):
    ann, st = tmp_path / "annotations", tmp_path / "_teacher"
    ids = _votable_ids(2)
    _build_task(ann, st, ids)
    promote.promote_votes([v for lid in ids for v in _split(lid)], annotations=ann)
    recipes.route(_recipe(), annotations=ann, teacher_store=st)
    _, m1 = store.load_task_bundle("rt-adjudication", annotations=ann, store=st)
    recipes.route(_recipe(), annotations=ann, teacher_store=st)              # same set → safe re-mint
    _, m2 = store.load_task_bundle("rt-adjudication", annotations=ann, store=st)
    assert m1 == m2                                                          # keys stable across re-route


def test_route_refuses_partial_coverage_unless_allowed(tmp_path):
    ann, st = tmp_path / "annotations", tmp_path / "_teacher"
    voted, silent = _votable_ids(2)
    _build_task(ann, st, [voted, silent])
    promote.promote_votes(_agree(voted, "lineated"), annotations=ann)       # `silent` gets NO votes

    with pytest.raises(ValueError, match="no vote from this task"):         # refuse + write nothing
        recipes.route(_recipe(), annotations=ann, teacher_store=st)
    with pytest.raises(FileNotFoundError):
        load_labels(annotations=ann)

    r = recipes.route(_recipe(), allow_partial=True, annotations=ann, teacher_store=st)
    assert r.accepted == 1 and r.uncovered == 1                             # surfaced, not silently dropped


def test_route_ignores_votes_from_a_different_task(tmp_path):
    ann, st = tmp_path / "annotations", tmp_path / "_teacher"
    (lid,) = _votable_ids(1)
    _build_task(ann, st, [lid])
    promote.promote_votes(_agree(lid, "lineated", task="other"), annotations=ann)  # another campaign

    with pytest.raises(ValueError, match="no vote from this task"):         # not THIS task's evidence
        recipes.route(_recipe(), annotations=ann, teacher_store=st)


def test_only_roster_votes_decide_and_provenance_separates_diagnostics(tmp_path):
    ann, st = tmp_path / "annotations", tmp_path / "_teacher"
    (lid,) = _votable_ids(1)
    _build_task(ann, st, [lid])
    # roster all agree `lineated`; a diagnostic reader `owl` (not in the recipe) dissents `prose`
    promote.promote_votes(_agree(lid, "lineated") + [_v(lid, "owl", "prose")], annotations=ann)

    r = recipes.route(_recipe(), annotations=ann, teacher_store=st)
    assert r.accepted == 1                                                   # owl never decides
    g = _labels(ann)[lid]
    assert g.provenance["votes"] == {"grok": "lineated", "deepseek": "lineated",
                                     "gemini": "lineated"}                   # roster only
    assert g.provenance["diagnostic_votes"] == {"owl": "prose"}             # observed, labelled apart


def test_low_anchor_confidence_routes_to_human_not_gate(tmp_path):
    ann, st = tmp_path / "annotations", tmp_path / "_teacher"
    (lid,) = _votable_ids(1)
    _build_task(ann, st, [lid])
    promote.promote_votes(_agree(lid, "lineated", conf=0.5), annotations=ann)  # below the 0.7 floor

    r = recipes.route(_recipe(), annotations=ann, teacher_store=st)
    assert r.accepted == 0 and r.queued_human == 1
    assert r.by_reason == {"low_confidence": 1} and r.operational == 0       # a terminal route


def test_route_ignores_votes_for_lines_outside_the_task(tmp_path):
    ann, st = tmp_path / "annotations", tmp_path / "_teacher"
    in_task, out_task = _votable_ids(2)
    _build_task(ann, st, [in_task])                                          # task scopes to ONE line
    promote.promote_votes(_agree(in_task, "lineated") + _agree(out_task, "prose"), annotations=ann)

    r = recipes.route(_recipe(), annotations=ann, teacher_store=st)
    assert r.accepted == 1 and r.uncovered == 0                             # the out-of-task vote is ignored
    assert set(_labels(ann)) == {in_task}


def test_route_requires_a_routed_recipe(tmp_path):
    with pytest.raises(ValueError, match="not routed"):
        recipes.route(recipes.load_recipe(_PLAIN), annotations=tmp_path / "a",
                      teacher_store=tmp_path / "s")


def test_load_recipe_rejects_roster_reader_outside_the_panel():
    with pytest.raises(ValueError, match="not present in the recipe"):
        recipes.load_recipe(_ROUTED.replace('support = ["deepseek", "gemini"]',
                                             'support = ["deepseek", "owl"]'))


def test_load_recipe_requires_both_roster_and_decision():
    roster_only = _ROUTED.split("[decision]")[0]                             # drop the [decision] table
    with pytest.raises(ValueError, match="BOTH"):
        recipes.load_recipe(roster_only)


# --- the loop closes: route → adjudicate.html → ingest → committed truth ------------------------

def test_route_then_human_ingest_reach_committed_truth(tmp_path):
    ann, st = tmp_path / "annotations", tmp_path / "_teacher"
    ids = _votable_ids(4)
    accept_ids, human_ids = ids[:2], ids[2:]
    _build_task(ann, st, ids)
    votes = [v for lid in accept_ids for v in _agree(lid, "lineated")]
    votes += [v for lid in human_ids for v in _split(lid)]
    promote.promote_votes(votes, annotations=ann)

    r = recipes.route(_recipe(), annotations=ann, teacher_store=st)
    assert r.accepted == 2 and r.queued_human == 2 and r.adjudication_task_id == "rt-adjudication"

    # a human opens the sub-task in adjudicate.html and calls every queued line `prose`
    payload, _ = store.load_task_bundle("rt-adjudication", annotations=ann, store=st)
    export = {"responses": {it["id"]: {"lines": {ln["key"]: "prose" for ln in it["lines"]},
                                       "note": ""}
                            for it in payload["items"]}}
    store.save_human_responses("rt-adjudication", export, annotations=ann)
    n = recipes.ingest(_recipe(), task_id="rt-adjudication", annotations=ann, teacher_store=st)
    assert n == 2

    ls = _labels(ann)
    assert set(ls) == set(ids)
    assert all(ls[lid].source is LabelSource.GATE and ls[lid].label == "lineated"
               for lid in accept_ids)
    assert all(ls[lid].source is LabelSource.HUMAN and ls[lid].label == "prose"
               for lid in human_ids)
