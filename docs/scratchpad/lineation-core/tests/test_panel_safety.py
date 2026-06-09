# research-pure: the live panel REFUSES to promote an unclean run, and RESUMES from saved calls.
"""Gates `recipes.panel` for a paid live run, all with a fake completer (no network): a truncated
rep, an empty/zero-row rep, and a resolution-fault rep each REFUSE (raise `PanelRefused`, promote
nothing); a clean run still promotes; a re-run RESUMES from the persisted raw calls instead of
re-calling, and the raw reply of even a malformed rep survives in the evidence."""
from __future__ import annotations

import json
import re

import pytest

from lineation_core import store
from lineation_core.teacher import recipes
from lineation_core.teacher.panel import ChatReply


def _build(tmp_path, n: int = 5):
    """A built 1-reader text task over the first `n` votable lines of book 57, in tmp dirs."""
    ann, st = tmp_path / "annotations", tmp_path / "_teacher"
    picks = [x.id for x in store.load_records("57") if x.votable][:n]
    (ann / "selections").mkdir(parents=True)
    (ann / "selections" / "acq.json").write_text(json.dumps([lid.as_key() for lid in picks]))
    r = recipes.Recipe(task_id="acq", title="A", instructions="prose vs lineated", books=("57",),
                       selector="selection_file:acq",
                       readers=(recipes.ReaderSpec("grok", "x/grok"),), target=8)
    recipes.build(r, annotations=ann, teacher_store=st)
    return r, ann, st


def _keys(messages) -> list[str]:
    listing = messages[0]["content"][0]["text"].split("Return ONLY")[0]   # not the example block
    return sorted(set(re.findall(r"\bL\d+\b", listing)))


class _Answer:
    """Answers exactly the keys it is shown, with a tunable label / finish_reason / override."""

    def __init__(self, *, label: str = "lineated", finish_reason=None, content=None):
        self.label, self.finish_reason, self.content_override = label, finish_reason, content
        self.calls = 0

    def complete(self, *, model, messages, temperature, max_tokens, response_format=None):
        self.calls += 1
        if self.content_override is not None:
            return ChatReply(content=self.content_override, finish_reason=self.finish_reason)
        body = json.dumps([{"key": k, "label": self.label} for k in _keys(messages)])
        return ChatReply(content=body, finish_reason=self.finish_reason)


def test_clean_run_promotes(tmp_path):
    r, ann, st = _build(tmp_path)
    assert recipes.panel(r, _Answer(), annotations=ann, teacher_store=st) == 5
    assert len(store.load_vote_rows(annotations=ann)) == 5


def test_truncated_rep_refuses_and_promotes_nothing(tmp_path):
    r, ann, st = _build(tmp_path)
    with pytest.raises(recipes.PanelRefused, match="truncated"):
        recipes.panel(r, _Answer(finish_reason="length"), annotations=ann, teacher_store=st)
    with pytest.raises(FileNotFoundError):                 # nothing promoted to committed truth
        store.load_vote_rows(annotations=ann)


def test_empty_zero_row_rep_refuses(tmp_path):
    r, ann, st = _build(tmp_path)
    with pytest.raises(recipes.PanelRefused, match="ZERO parsed rows"):
        recipes.panel(r, _Answer(content="I cannot answer."), annotations=ann, teacher_store=st)
    with pytest.raises(FileNotFoundError):
        store.load_vote_rows(annotations=ann)


def test_resolve_fault_rep_refuses(tmp_path):
    r, ann, st = _build(tmp_path)
    # rows parse (so the zero-row gate passes) but the label is invalid → BAD_LABEL resolution fault
    with pytest.raises(recipes.PanelRefused, match="resolution"):
        recipes.panel(r, _Answer(label="maybe"), annotations=ann, teacher_store=st)
    with pytest.raises(FileNotFoundError):
        store.load_vote_rows(annotations=ann)


def test_missing_key_refuses(tmp_path):
    r, ann, st = _build(tmp_path)
    # answer only the FIRST key shown → every other manifest key is a MISSING_KEY fault under complete=True
    class _Partial:
        def complete(self, *, model, messages, temperature, max_tokens, response_format=None):
            k = _keys(messages)[0]
            return ChatReply(content=json.dumps([{"key": k, "label": "prose"}]))

    with pytest.raises(recipes.PanelRefused, match="resolution"):
        recipes.panel(r, _Partial(), annotations=ann, teacher_store=st)


def test_raw_content_survives_in_evidence_even_when_unparseable(tmp_path):
    r, ann, st = _build(tmp_path)
    with pytest.raises(recipes.PanelRefused):
        recipes.panel(r, _Answer(content="refusing to comply"), annotations=ann, teacher_store=st)
    rows = store.load_panel_reps("acq", annotations=ann)              # per-rep evidence persisted
    raw = [row for row in rows if row.get("kind") == "raw"]
    assert raw and all(row["content"] == "refusing to comply" for row in raw)   # raw reply kept


def test_rerun_resumes_from_saved_calls_without_recalling(tmp_path):
    r, ann, st = _build(tmp_path)
    first = _Answer()                                       # a clean run: saves every call's reply
    assert recipes.panel(r, first, annotations=ann, teacher_store=st) == 5
    assert first.calls >= 1                                 # one call per (item, reader, rep)
    saved = store.load_panel_calls("acq", store=st)
    assert len(saved) == first.calls                        # every call persisted to the resume log

    class _Boom:
        """Any call is a test failure — a resumed run must reuse the saved replies, not re-call."""
        def complete(self, *, model, messages, temperature, max_tokens, response_format=None):
            raise AssertionError("resumed run must not re-call the completer")

    assert recipes.panel(r, _Boom(), annotations=ann, teacher_store=st) == 5   # served from cache


def test_resume_recalls_when_the_reader_model_changed(tmp_path):
    r, ann, st = _build(tmp_path)
    payload, _ = store.load_task_bundle("acq", annotations=ann, store=st)
    n_items = len(payload["items"])
    # seed a saved call under a DIFFERENT model than the recipe's reader (x/grok) — a stale reply
    store.save_panel_call("acq", {"item_id": payload["items"][0]["id"], "tag": "grok", "rep": 0,
                                  "model": "x/OLD-MODEL", "content": "[]", "finish_reason": None},
                          store=st)
    ans = _Answer()
    recipes.panel(r, ans, annotations=ann, teacher_store=st)
    assert ans.calls == n_items   # the stale-MODEL reply was NOT reused — every item re-called fresh


def test_partial_per_reader_coverage_refuses(tmp_path):
    """A reader that OMITS a key another reader covered must REFUSE: the pooled MISSING_KEY check
    counts the key 'answered' (the other reader supplied it), so without a per-reader gate a thinned
    support vote would silently flip the gate's routing."""
    ann, st = tmp_path / "annotations", tmp_path / "_teacher"
    picks = [x.id for x in store.load_records("57") if x.votable][:5]
    (ann / "selections").mkdir(parents=True)
    (ann / "selections" / "acq.json").write_text(json.dumps([lid.as_key() for lid in picks]))
    r = recipes.Recipe(task_id="acq", title="A", instructions="prose vs lineated", books=("57",),
                       selector="selection_file:acq",
                       readers=(recipes.ReaderSpec("grok", "x/grok"),
                                recipes.ReaderSpec("deepseek", "x/ds")), target=8)
    recipes.build(r, annotations=ann, teacher_store=st)

    class _OneOmits:
        def complete(self, *, model, messages, temperature, max_tokens, response_format=None):
            keys = _keys(messages)
            if model == "x/ds":
                keys = keys[:-1]                       # deepseek silently drops one shown key
            return ChatReply(content=json.dumps([{"key": k, "label": "prose"} for k in keys]))

    with pytest.raises(recipes.PanelRefused, match="votes missing"):
        recipes.panel(r, _OneOmits(), annotations=ann, teacher_store=st)
