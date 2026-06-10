# research-pure: the panel runner queries an injected completer and shows opaque keys, never idx.
"""`run_panel` drives a fake `ChatCompleter` (no network) and parses per-rep responses;
`build_prompt` shows the opaque-keyed listing + (vision) the composite image, asking for keys ONLY."""
from __future__ import annotations

import threading

import pytest

from lineation_core import store
from lineation_core.annotations import PanelVote
from lineation_core.identity import LineId
from lineation_core.teacher import panel, tasks
from lineation_core.teacher.panel import ChatReply, PanelConfig, ReaderConfig
from lineation_core.teacher.tasks import (AssetKind, EvidenceAsset, ItemSpec, Modality, TaskItem,
                                          TaskLine)


class FakeCompleter:
    """Records its calls and returns a canned reply — the injected network boundary, in tests."""

    def __init__(self, content: str):
        self.content, self.calls = content, []

    def complete(self, *, model, messages, temperature, max_tokens, response_format=None):
        self.calls.append((model, messages, response_format))
        return ChatReply(content=self.content)


def _task(n: int = 3):
    recs = store.load_records("57")
    votable = [r for r in recs if r.votable][:n]
    spec = ItemSpec.all_votable("b57-r0", [r.id for r in votable])
    return tasks.build_task(title="t", instructions="decide prose vs lineated",
                            specs=[spec], records={"57": recs})


def _vision_item():
    return TaskItem(id="r", context="  L001  | x",
                    lines=(TaskLine(key="L001", text="x"),),
                    assets=(EvidenceAsset(kind=AssetKind.COMPOSITE,
                                          data_uri="data:image/png;base64,AAAA"),))


def test_run_panel_parses_each_rep_from_the_completer():
    task = _task()
    reply = ('[{"key":"L001","lineation_label":"prose","confidence":0.7},{"key":"L002","lineation_label":"lineated"},'
             '{"key":"L003","lineation_label":"prose","confidence":0.9}]')
    fake = FakeCompleter(reply)
    cfg = PanelConfig(readers=(ReaderConfig("grok", "x/grok", Modality.TEXT),), reps=2)
    reps = panel.run_panel(task, cfg, fake)
    assert len(reps) == 2 and [r.rep for r in reps] == [0, 1]      # 1 reader × 2 reps × 1 item
    assert all(len(r.response.rows) == 3 for r in reps)
    assert reps[0].response.rows[0].conf == 0.7 and len(fake.calls) == 2


def test_run_panel_concurrent_is_deterministic_and_persists_every_call_once():
    # max_workers>1 fans the fetches across threads, but results must assemble in reader×rep×item order
    # regardless of completion order, and on_call (the resume log) must fire exactly once per call.
    task = _task()
    reply = '[{"key":"L001","lineation_label":"prose"},{"key":"L002","lineation_label":"lineated"},{"key":"L003","lineation_label":"prose"}]'

    class ThreadSafeFake:
        def __init__(self):
            self.n, self._lock = 0, threading.Lock()

        def complete(self, *, model, messages, temperature, max_tokens, response_format=None):
            with self._lock:
                self.n += 1
            return ChatReply(content=reply)

    fake = ThreadSafeFake()
    saved: list = []
    cfg = PanelConfig(readers=(ReaderConfig("grok", "x/grok", Modality.TEXT),), reps=5)
    reps = panel.run_panel(task, cfg, fake, on_call=lambda req, r: saved.append(req.cache_key),
                           max_workers=4)
    assert [r.rep for r in reps] == [0, 1, 2, 3, 4]          # ordered despite concurrent completion
    assert fake.n == 5 and len(saved) == 5 == len(set(saved))  # every call made + persisted once
    assert all(len(r.response.rows) == 3 for r in reps)


def test_run_panel_concurrent_reuses_cache_without_refetching():
    # a fully-cached run under the pool must make ZERO network calls and still assemble every rep.
    task = _task()
    cfg = PanelConfig(readers=(ReaderConfig("grok", "x/grok", Modality.TEXT),), reps=3)
    warm = FakeCompleter('[{"key":"L001","lineation_label":"prose"}]')
    cache: dict = {}
    panel.run_panel(task, cfg, warm, on_call=lambda req, r: cache.__setitem__(req.cache_key, r),
                    max_workers=4)

    class Boom:
        def complete(self, **kw):
            raise AssertionError("must not fetch a cached call")

    reps = panel.run_panel(task, cfg, Boom(), cached=cache, max_workers=4)
    assert len(reps) == 3 and all(r.response.rows for r in reps)


def test_build_prompt_shows_opaque_keys_and_the_instructions():
    task = _task()
    msgs = panel.build_prompt(task.items[0], ReaderConfig("grok", "x/grok", Modality.TEXT),
                              task.instructions, panel.ResponseContract.JSON_ARRAY)
    text = msgs[0]["content"][0]["text"]
    assert "L001" in text and "decide prose vs lineated" in text   # opaque key + the instructions
    assert all(p["type"] == "text" for p in msgs[0]["content"])    # text reader: no image part
    # (the no-src_ordinal-leak invariant on item.context is rigorously locked in test_tasks)


def test_vision_reader_gets_the_composite_image():
    msgs = panel.build_prompt(_vision_item(), ReaderConfig("grok", "x/grok", Modality.VISION), "b",
                              panel.ResponseContract.JSON_ARRAY)
    img = next((p for p in msgs[0]["content"] if p["type"] == "image_url"), None)
    assert img is not None and img["image_url"]["url"].startswith("data:image/png")


def test_vision_reader_gets_every_page_image_in_order():
    # a multi-page region carries one COMPOSITE asset per page; the prompt attaches them ALL, in order,
    # so a per-image-budget reader sees each page at full resolution (not a downsampled stack).
    item = TaskItem(id="r", context="  L001 | x",
                    lines=(TaskLine(key="L001", text="x"),),
                    assets=tuple(EvidenceAsset(kind=AssetKind.COMPOSITE,
                                               data_uri=f"data:image/png;base64,P{n}") for n in range(3)))
    msgs = panel.build_prompt(item, ReaderConfig("grok", "x/grok", Modality.VISION), "b",
                              panel.ResponseContract.JSON_ARRAY)
    urls = [p["image_url"]["url"] for p in msgs[0]["content"] if p["type"] == "image_url"]
    assert urls == ["data:image/png;base64,P0", "data:image/png;base64,P1", "data:image/png;base64,P2"]


def test_text_reader_ignores_the_image_even_on_a_vision_item():
    msgs = panel.build_prompt(_vision_item(), ReaderConfig("d", "x/d", Modality.TEXT), "b",
                              panel.ResponseContract.JSON_ARRAY)
    assert all(p["type"] == "text" for p in msgs[0]["content"])    # modality is the reader's choice


def test_run_panel_reuses_same_prompt_but_recalls_when_the_prompt_changes():
    """The resume cache is keyed by the PROMPT fingerprint: same prompt → reuse the paid reply;
    an EDITED prompt (same items, different instructions) → re-call, never silently reuse a reply
    made under the old prompt."""
    recs = store.load_records("57")
    spec = ItemSpec.all_votable("b57-r0", [r.id for r in recs if r.votable][:3])
    cfg = PanelConfig(readers=(ReaderConfig("grok", "x/grok", Modality.TEXT),), reps=1)
    fake = FakeCompleter('[{"key":"L001","lineation_label":"prose","confidence":0.7}]')
    cache: dict = {}

    task_a = tasks.build_task(title="t", instructions="CRITERIA A", specs=[spec], records={"57": recs})
    panel.run_panel(task_a, cfg, fake, cached={},
                    on_call=lambda req, r: cache.__setitem__(req.cache_key, r))
    assert len(fake.calls) == 1                                  # one fresh call, persisted to cache
    panel.run_panel(task_a, cfg, fake, cached=cache)
    assert len(fake.calls) == 1                                  # same prompt → served from cache

    task_b = tasks.build_task(title="t", instructions="CRITERIA B — different",
                              specs=[spec], records={"57": recs})
    panel.run_panel(task_b, cfg, fake, cached=cache)
    assert len(fake.calls) == 2                                  # edited prompt → re-called, not reused


def test_no_contract_instruction_carries_a_real_key():
    """No contract's format example may show a literal L-key — it collides with item 1's key and
    primes readers to echo / continue the sequence (the observed key_item_mismatch faults). Every
    instruction carries the explicit don't-invent guard, and the prompt ends with it."""
    import re

    from lineation_core.teacher import contracts
    task = _task()
    for contract in panel.ResponseContract:
        spec = contracts.spec_for(contract)
        assert not re.search(r"\bL\d+\b", spec.instruction), contract
        assert "do NOT invent" in spec.instruction, contract
        msgs = panel.build_prompt(task.items[0], ReaderConfig("g", "x/g", Modality.TEXT),
                                  "criteria", contract)
        assert msgs[0]["content"][0]["text"].endswith(spec.instruction)   # ASK is wired per contract


def test_run_panel_uses_per_modality_instructions():
    """A vision reader gets the page-authority prompt; a text reader gets the listing/structure prompt
    — a text reader must not be handed a page-authority prompt for a page it never receives."""
    task = _task()
    cfg = PanelConfig(readers=(ReaderConfig("grok", "x/g", Modality.VISION),
                               ReaderConfig("ds", "x/d", Modality.TEXT)), reps=1)
    fake = FakeCompleter('[{"key":"L001","lineation_label":"prose","confidence":0.5}]')
    panel.run_panel(task, cfg, fake, instructions_by_modality={Modality.VISION: "PAGE-AUTHORITY",
                                                               Modality.TEXT: "LISTING-AUTHORITY"})
    sent = {model: " ".join(p.get("text", "") for p in msgs[0]["content"] if isinstance(p, dict))
            for model, msgs, _ in fake.calls}
    assert "PAGE-AUTHORITY" in sent["x/g"] and "LISTING-AUTHORITY" not in sent["x/g"]
    assert "LISTING-AUTHORITY" in sent["x/d"] and "PAGE-AUTHORITY" not in sent["x/d"]


def _fp(messages, *, temperature=0.0, max_tokens=8192, contract=panel.ResponseContract.JSON_ARRAY):
    """The request fingerprint, via the type that owns it (no item needed for the hash)."""
    return panel.CompletionRequest(item_id="r", tag="t", rep=0, model="m",
                                   messages=tuple(messages), temperature=temperature,
                                   max_tokens=max_tokens, contract=contract).fingerprint


def test_prompt_fingerprint_changes_when_the_image_changes():
    """A vision reply must NOT be reused after the rendered page changes — the fingerprint hashes the
    image data-URI, not only the text (render slices can change under a fixed task/instructions)."""
    def msg(img):
        return [{"role": "user", "content": [{"type": "text", "text": "same listing"},
                                             {"type": "image_url", "image_url": {"url": img}}]}]
    a, b = _fp(msg("data:image/png;base64,AAAA")), _fp(msg("data:image/png;base64,BBBB"))
    assert a != b                                           # different image ⇒ different cache key
    assert _fp(msg("data:image/png;base64,AAAA")) == a      # stable for same prompt


def test_prompt_fingerprint_changes_when_the_sampling_config_changes():
    """A reply sampled at one temperature/max_tokens must NOT be reused under another — the fingerprint
    folds the sampling config in, so a temp/token change re-calls instead of returning a stale reply."""
    msgs = [{"role": "user", "content": [{"type": "text", "text": "same prompt"}]}]
    base = _fp(msgs, temperature=0.0, max_tokens=8192)
    assert _fp(msgs, temperature=0.5, max_tokens=8192) != base   # temp matters
    assert _fp(msgs, temperature=0.0, max_tokens=4096) != base   # max_tokens too
    assert _fp(msgs, temperature=0.0, max_tokens=8192) == base   # stable otherwise


def test_prompt_fingerprint_changes_when_the_contract_changes():
    """A reply shaped under one response contract must NOT be reused under another — the fingerprint
    folds the contract in, so an ARRAY→KEYED_OBJECT switch re-calls instead of reusing a stale reply."""
    msgs = [{"role": "user", "content": [{"type": "text", "text": "same prompt"}]}]
    assert _fp(msgs, contract=panel.ResponseContract.JSON_ARRAY) \
        != _fp(msgs, contract=panel.ResponseContract.JSON_KEYED)


def test_verdict_schema_constrains_key_to_the_shown_keys():
    """The structured-output schema makes an invented/continued key STRUCTURALLY impossible: `key` is
    an enum of exactly the shown keys, `lineation_label` is the 2-class enum, extras are forbidden."""
    items = panel.verdict_schema(panel.ResponseContract.JSON_ARRAY, ["L005", "L006", "L007"]) \
        ["json_schema"]["schema"]["properties"]["verdicts"]["items"]
    assert items["properties"]["key"]["enum"] == ["L005", "L006", "L007"]
    assert items["properties"]["lineation_label"]["enum"] == ["prose", "lineated"]
    assert items["additionalProperties"] is False


def test_verdict_schema_keyed_object_makes_keys_the_schema():
    """The JSON_KEYED contract makes the shown keys themselves the schema: one REQUIRED property per
    key (so none can be invented/missed/dup'd), each value a `{label, conf}` object — the keys are the
    fault-proofing, the value carries the same confidence the array contract does."""
    schema = panel.verdict_schema(panel.ResponseContract.JSON_KEYED, ["L001", "L002"]) \
        ["json_schema"]["schema"]
    assert set(schema["properties"]) == {"L001", "L002"}
    assert schema["required"] == ["L001", "L002"]
    val = schema["properties"]["L001"]
    assert val["properties"]["lineation_label"]["enum"] == ["prose", "lineated"]   # value={label,conf}
    assert val["properties"]["confidence"]["type"] == "number"
    assert val["required"] == ["lineation_label", "confidence"]
    assert val["additionalProperties"] is False and schema["additionalProperties"] is False


def test_run_panel_passes_response_format_scoped_to_this_items_keys():
    """`run_panel` sends each item a `response_format` whose `key` enum is EXACTLY that item's keys —
    so a reader cannot return a key from another item or one past the end."""
    task = _task(3)                       # item b57-r0 → keys L001..L003
    fake = FakeCompleter('{"verdicts": [{"key":"L001","lineation_label":"prose","confidence":0.7}]}')
    cfg = PanelConfig(readers=(ReaderConfig("grok", "x/grok", Modality.TEXT),), reps=1)
    panel.run_panel(task, cfg, fake)
    _, _, rformat = fake.calls[0]
    enum = rformat["json_schema"]["schema"]["properties"]["verdicts"]["items"]["properties"]["key"]["enum"]
    assert enum == ["L001", "L002", "L003"]


def test_tsv_contract_is_schemaless_so_the_instruction_is_the_sole_constraint():
    """TSV has no structured-output schema: `verdict_schema` is None (the adapter sends no
    `response_format`) and the run still parses the tab rows the instruction asked for."""
    assert panel.verdict_schema(panel.ResponseContract.TSV, ["L001", "L002"]) is None
    task = _task()
    fake = FakeCompleter("L001\tprose\t0.7\nL002\tlineated\nL003\tprose\t0.9")
    cfg = PanelConfig(readers=(ReaderConfig("grok", "x/grok", Modality.TEXT),), reps=1,
                      contract=panel.ResponseContract.TSV)
    reps = panel.run_panel(task, cfg, fake)
    assert fake.calls[0][2] is None                      # no response_format on the wire
    rows = {r.key: r for r in reps[0].response.rows}
    assert {k: r.label for k, r in rows.items()} == {"L001": "prose", "L002": "lineated",
                                                     "L003": "prose"}
    assert rows["L001"].conf == 0.7 and rows["L002"].conf is None


def test_adapter_renames_schema_to_the_sdk_alias_at_the_boundary():
    """The pure core emits standard `"schema"`; the OpenRouter SDK's pydantic model spells it
    `schema_` — the rename happens ONLY in the adapter, never in the contract schemas."""
    from lineation_core.teacher.openrouter import _sdk_response_format
    rf = panel.verdict_schema(panel.ResponseContract.JSON_ARRAY, ["L001"])
    out = _sdk_response_format(rf)
    assert "schema" not in out["json_schema"] and \
        out["json_schema"]["schema_"] == rf["json_schema"]["schema"]
    assert _sdk_response_format({"type": "json_object"}) == {"type": "json_object"}  # pass-through


def test_aggregate_reps_strict_majority_and_abstain_on_tie():
    lid = LineId.mapped("ru", "57", 1, 0)

    def v(tag, label, conf=None):
        return PanelVote(id=lid, tag=tag, label=label, conf=conf)

    per_rep = [[v("grok", "prose", 0.8), v("deepseek", "prose", 0.6)],
               [v("grok", "prose", 0.7), v("deepseek", "lineated", 0.9)],
               [v("grok", "lineated", 0.5)]]
    out = {x.tag: x for x in panel.aggregate_reps(per_rep)}
    assert out["grok"].label == "prose"                        # 2 of 3 reps → strict majority
    assert out["grok"].conf == pytest.approx((0.8 + 0.7) / 2)  # mean of the AGREEING (prose) reps
    assert "deepseek" not in out                               # 1-1 tie → abstains (no vote)


def test_aggregate_reps_single_rep_passes_through_without_duplicates():
    a, b = LineId.mapped("ru", "57", 1, 0), LineId.mapped("ru", "57", 2, 0)
    per_rep = [[PanelVote(id=a, tag="grok", label="prose", conf=None),
                PanelVote(id=b, tag="grok", label="lineated", conf=0.9)]]
    out = panel.aggregate_reps(per_rep)
    assert {(x.tag, x.id) for x in out} == {("grok", a), ("grok", b)} and len(out) == 2
