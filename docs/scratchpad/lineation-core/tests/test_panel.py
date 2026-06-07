# research-pure: the panel runner queries an injected completer and shows opaque keys, never idx.
"""`run_panel` drives a fake `ChatCompleter` (no network) and parses per-rep responses;
`build_prompt` shows the opaque-keyed listing + (vision) the composite image, asking for keys ONLY."""
from __future__ import annotations

from lineation_core import store
from lineation_core.teacher import panel, tasks
from lineation_core.teacher.panel import ChatReply, PanelConfig, ReaderConfig
from lineation_core.teacher.tasks import (AssetKind, EvidenceAsset, ItemSpec, Modality, TaskItem,
                                          TaskLine)


class FakeCompleter:
    """Records its calls and returns a canned reply — the injected network boundary, in tests."""

    def __init__(self, content: str):
        self.content, self.calls = content, []

    def complete(self, *, model, messages, temperature, max_tokens):
        self.calls.append((model, messages))
        return ChatReply(content=self.content)


def _task(n: int = 3):
    recs = store.load_records("57")
    votable = [r for r in recs if r.votable][:n]
    spec = ItemSpec(region_id="b57-r0", vote_ids=tuple(r.id for r in votable))
    return tasks.build_task(title="t", instructions="decide prose vs lineated",
                            specs=[spec], records={"57": recs})


def _vision_item():
    return TaskItem(id="r", modality=Modality.VISION, context="  L001  | x",
                    lines=(TaskLine(key="L001", text="x"),),
                    assets=(EvidenceAsset(kind=AssetKind.COMPOSITE,
                                          data_uri="data:image/png;base64,AAAA"),))


def test_run_panel_parses_each_rep_from_the_completer():
    task = _task()
    reply = ('[{"key":"L001","label":"prose","conf":0.7},{"key":"L002","label":"lineated"},'
             '{"key":"L003","label":"prose","conf":0.9}]')
    fake = FakeCompleter(reply)
    cfg = PanelConfig(readers=(ReaderConfig("grok", "x/grok", Modality.TEXT),), reps=2)
    reps = panel.run_panel(task, cfg, fake)
    assert len(reps) == 2 and [r.rep for r in reps] == [0, 1]      # 1 reader × 2 reps × 1 item
    assert all(len(r.response.rows) == 3 for r in reps)
    assert reps[0].response.rows[0].conf == 0.7 and len(fake.calls) == 2


def test_build_prompt_shows_opaque_keys_and_the_instructions():
    task = _task()
    msgs = panel.build_prompt(task.items[0], ReaderConfig("grok", "x/grok", Modality.TEXT),
                              task.instructions)
    text = msgs[0]["content"][0]["text"]
    assert "L001" in text and "decide prose vs lineated" in text   # opaque key + the instructions
    assert all(p["type"] == "text" for p in msgs[0]["content"])    # text reader: no image part
    # (the no-src_ordinal-leak invariant on item.context is rigorously locked in test_tasks)


def test_vision_reader_gets_the_composite_image():
    msgs = panel.build_prompt(_vision_item(), ReaderConfig("grok", "x/grok", Modality.VISION), "b")
    img = next((p for p in msgs[0]["content"] if p["type"] == "image_url"), None)
    assert img is not None and img["image_url"]["url"].startswith("data:image/png")


def test_text_reader_ignores_the_image_even_on_a_vision_item():
    msgs = panel.build_prompt(_vision_item(), ReaderConfig("d", "x/d", Modality.TEXT), "b")
    assert all(p["type"] == "text" for p in msgs[0]["content"])    # modality is the reader's choice
