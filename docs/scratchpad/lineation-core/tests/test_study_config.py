# research-pure: the study config layer — pure parse of an experiment toml, ONE sweep axis, fail-loud.
"""Locks `load_experiment`/`sweep_recipes`: a valid experiment parses (books derived from the eval set,
panel half via load_recipe), a 2-key sweep / unknown axis / unknown metric / bad dataset source all
fail loud, and `sweep_recipes` yields one Recipe per point varying the right axis."""
from __future__ import annotations

import json

import pytest

from lineation_core import store
from lineation_core.evaluation.study import load_experiment, sweep_recipes
from lineation_core.teacher.panel import ResponseContract


def _frozen_eval_set(annotations, name="tiny"):
    """Freeze a 4-line synthetic eval set (books 57 + 13) into a tmp annotations dir."""
    picks_57 = [x.id for x in store.load_records("57") if x.votable][:3]
    picks_13 = [x.id for x in store.load_records("13") if x.votable][:1]
    rows = ([{"id": lid.as_key(), "label": "lineated"} for lid in picks_57]
            + [{"id": lid.as_key(), "label": "prose"} for lid in picks_13])
    (annotations / "eval_sets").mkdir(parents=True, exist_ok=True)
    (annotations / "eval_sets" / f"{name}.json").write_text(json.dumps(rows))
    return picks_57 + picks_13


_TOML = """
[experiment]
id = "exp-1"
kind = "reader"
question = "does the keyed_object contract cut faults vs the array schema?"
round = 0
seed = 0

[dataset]
source = "eval_set"
name = "tiny"

[selection]
target = 8
context_radius = 2

[sweep]
contract = ["array", "keyed_object"]

[metrics]
report = ["balanced_acc", "prose_recall", "usd"]

[[readers]]
tag = "grok"
model = "x-ai/grok-4.3"
modality = "text"

[[readers]]
tag = "ds"
model = "deepseek/deepseek-v4-flash"
modality = "text"
"""


def test_load_experiment_parses_and_derives_books(tmp_path):
    ann = tmp_path / "annotations"
    _frozen_eval_set(ann)
    exp = load_experiment(_TOML, annotations=ann)
    assert exp.meta.id == "exp-1" and exp.meta.kind == "reader"
    assert "keyed_object" in exp.meta.question
    assert exp.dataset_name == "tiny"
    assert exp.base.books == ("13", "57")                  # DERIVED from the eval set, sorted
    assert exp.base.selector == "eval_set:tiny"
    assert [r.tag for r in exp.base.readers] == ["grok", "ds"]
    assert exp.sweep.axis == "contract" and exp.sweep.points == ("array", "keyed_object")
    assert exp.metrics == ("balanced_acc", "prose_recall", "usd")


def test_sweep_recipes_yields_one_recipe_per_contract_point(tmp_path):
    ann = tmp_path / "annotations"
    _frozen_eval_set(ann)
    exp = load_experiment(_TOML, annotations=ann)
    recs = sweep_recipes(exp)
    assert [label for label, _ in recs] == ["array", "keyed_object"]
    assert recs[0][1].contract is ResponseContract.ARRAY
    assert recs[1][1].contract is ResponseContract.KEYED_OBJECT


def test_sweep_two_keys_fails_loud(tmp_path):
    ann = tmp_path / "annotations"
    _frozen_eval_set(ann)
    bad = _TOML.replace("[sweep]\ncontract = [\"array\", \"keyed_object\"]",
                        "[sweep]\ncontract = [\"array\"]\ntemperature = [0.0, 0.5]")
    with pytest.raises(ValueError, match="ONE axis"):
        load_experiment(bad, annotations=ann)


def test_unknown_sweep_axis_fails_loud(tmp_path):
    ann = tmp_path / "annotations"
    _frozen_eval_set(ann)
    bad = _TOML.replace('[sweep]\ncontract = ["array", "keyed_object"]',
                        '[sweep]\nbogus = ["a", "b"]')
    with pytest.raises(ValueError, match="unknown sweep axis"):
        load_experiment(bad, annotations=ann)


def test_unknown_metric_fails_loud(tmp_path):
    ann = tmp_path / "annotations"
    _frozen_eval_set(ann)
    bad = _TOML.replace('report = ["balanced_acc", "prose_recall", "usd"]',
                        'report = ["balanced_acc", "made_up_metric"]')
    with pytest.raises(ValueError, match="unknown metric"):
        load_experiment(bad, annotations=ann)


def test_unknown_dataset_source_fails_loud(tmp_path):
    ann = tmp_path / "annotations"
    _frozen_eval_set(ann)
    bad = _TOML.replace('source = "eval_set"', 'source = "live_corpus"')
    with pytest.raises(ValueError, match="dataset source"):
        load_experiment(bad, annotations=ann)


def test_contract_sweep_holds_a_configured_base_temperature(tmp_path):
    # a contract sweep must run at the experiment's base temperature (not a hardcoded 0.0), so the
    # schemas are compared at one realistic sampling temp and the manifest records it.
    from lineation_core.evaluation.study import _base_temperature, _readers_at
    ann = tmp_path / "annotations"
    _frozen_eval_set(ann)
    exp = load_experiment("temperature = 0.5\n" + _TOML, annotations=ann)
    assert exp.base_temperature == 0.5
    assert _base_temperature(exp) == 0.5                         # stamped into the manifest
    readers = _readers_at(exp.base, "array", exp.sweep.axis, exp.base_temperature)
    assert {r.temperature for r in readers} == {0.5}            # every reader at the base temp
