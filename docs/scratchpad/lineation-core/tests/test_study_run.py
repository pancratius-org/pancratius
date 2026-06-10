# research-pure: the study run shell — a fake panel over a tiny eval set yields a scorecard, writes the
# three durable files, and re-runs at $0 (resume cache) byte-identically.
"""Locks `run_study`: one ReaderResult per reader per sweep point, the durable files land in the
experiment folder, and a SECOND run with a populated replies.jsonl makes ZERO completer calls and an
identical scorecard — the $0-on-re-run invariant."""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from lineation_core import store
from lineation_core.evaluation.reader_metrics import PriceTable
from lineation_core.evaluation.study import load_experiment, run_study
from lineation_core.teacher.panel import ChatReply, ReaderConfig


class CannedCompleter:
    """Answers exactly the keys it is shown, with a per-(model, contract) label so the two sweep points
    and two readers diverge. Counts its calls — the resume invariant asserts ZERO on a warm re-run."""

    def __init__(self):
        self.calls = 0

    def complete(self, *, model, messages, temperature, max_tokens, response_format=None):
        self.calls += 1
        listing = messages[0]["content"][0]["text"].split("Return ONLY")[0]
        keys = sorted(set(re.findall(r"\bL\d+\b", listing)))
        # deepseek says prose, grok says lineated — distinct so per-reader scores differ.
        label = "prose" if "deepseek" in model else "lineated"
        usage = {"prompt_tokens": 100, "completion_tokens": 20}
        return ChatReply(content=json.dumps([{"key": k, "lineation_label": label} for k in keys]),
                         finish_reason="stop", usage=usage)


def _frozen_eval_set(annotations):
    picks = [x.id for x in store.load_records("57") if x.votable][:4]
    rows = [{"id": lid.as_key(), "label": "lineated"} for lid in picks]
    (annotations / "eval_sets").mkdir(parents=True, exist_ok=True)
    (annotations / "eval_sets" / "tiny.json").write_text(json.dumps(rows))


_TOML = """
[experiment]
id = "exp-contract"
kind = "reader"
question = "array vs keyed_object — does the schema change faults or accuracy?"
seed = 0

[dataset]
source = "eval_set"
name = "tiny"

[selection]
target = 8
context_radius = 2

instructions = "decide prose vs lineated"

[sweep]
contract = ["json_array", "json_keyed"]

[[readers]]
tag = "grok"
model = "x-ai/grok-4.3"
modality = "text"

[[readers]]
tag = "ds"
model = "deepseek/deepseek-v4-flash"
modality = "text"
"""

_PRICES = PriceTable(version="test", models={"x-ai/grok-4.3": (1e-6, 2e-6),
                                             "deepseek/deepseek-v4-flash": (0.1e-6, 0.2e-6)})
_NOW = datetime(2026, 6, 9, 12, 0, 0, tzinfo=UTC)
_SHA = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"


def _run(tmp_path, completer):
    ann = tmp_path / "annotations"
    _frozen_eval_set(ann)
    exp = load_experiment(_TOML, annotations=ann)
    return run_study(exp, completer, _PRICES, now=_NOW, git_sha=_SHA,
                     experiments_dir=tmp_path / "experiments", annotations=ann), ann


def test_run_study_yields_one_result_per_reader_per_point(tmp_path):
    fake = CannedCompleter()
    scorecard, _ = _run(tmp_path, fake)
    assert set(scorecard.results) == {"json_array", "json_keyed"}
    for point, readers in scorecard.results.items():
        assert {r.tag for r in readers} == {"grok", "ds"}
    grok = next(r for r in scorecard.results["json_array"] if r.tag == "grok")
    ds = next(r for r in scorecard.results["json_array"] if r.tag == "ds")
    assert grok.quality.lineated_recall == 1.0          # grok says lineated; all truth lineated
    assert ds.quality.lineated_recall == 0.0            # ds says prose → every line a miss
    assert grok.cost.usd > 0 and grok.health.coverage == 1.0


def test_run_study_writes_the_three_durable_files(tmp_path):
    scorecard, _ = _run(tmp_path, CannedCompleter())
    folder = tmp_path / "experiments" / "exp-contract"
    assert (folder / "scorecard.json").is_file()
    assert (folder / "report.md").is_file()
    assert (folder / "manifest.json").is_file()
    report = (folder / "report.md").read_text()
    assert "array vs keyed_object" in report            # the question is echoed
    manifest = json.loads((folder / "manifest.json").read_text())
    assert manifest["git_sha"] == _SHA and manifest["timestamp"] == _NOW.isoformat()
    assert manifest["price_table_version"] == "test"


def test_run_study_writes_nothing_into_annotations(tmp_path):
    _, ann = _run(tmp_path, CannedCompleter())
    # only the eval_sets/ the test seeded — no labels.jsonl/votes.jsonl/tasks/ etc.
    written = sorted(p.name for p in ann.iterdir())
    assert written == ["eval_sets"]


class FaultyGrokCompleter:
    """grok emits an INVALID label (a per-reader BAD_LABEL fault); ds is clean. So the two readers'
    `ProtocolHealth.faults` MUST differ — the per-reader-resolve fix, not a smeared task-level dict."""

    def complete(self, *, model, messages, temperature, max_tokens, response_format=None):
        listing = messages[0]["content"][0]["text"].split("Return ONLY")[0]
        keys = sorted(set(re.findall(r"\bL\d+\b", listing)))
        label = "GARBAGE" if "grok" in model else "lineated"   # grok's label is not prose|lineated
        usage = {"prompt_tokens": 100, "completion_tokens": 20}
        return ChatReply(content=json.dumps([{"key": k, "lineation_label": label} for k in keys]),
                         finish_reason="stop", usage=usage)


def test_per_reader_faults_are_not_smeared_across_readers(tmp_path):
    # grok answers an invalid label (BAD_LABEL fault on every line); ds answers cleanly. With per-reader
    # resolution each reader's faults are ITS OWN — grok carries bad_label, ds carries none. A task-level
    # resolve (the bug) would give BOTH readers the same fault dict.
    scorecard, _ = _run(tmp_path, FaultyGrokCompleter())
    grok = next(r for r in scorecard.results["json_array"] if r.tag == "grok")
    ds = next(r for r in scorecard.results["json_array"] if r.tag == "ds")
    assert grok.health.faults.get("bad_label", 0) > 0      # grok's own invalid labels
    assert "bad_label" not in ds.health.faults             # ds is clean — NOT grok's fault
    assert grok.health.faults != ds.health.faults          # different profiles, not one smeared dict
    assert ds.health.coverage == 1.0 and grok.health.coverage == 0.0   # grok resolved zero votes


_TEMP_TOML = """
[experiment]
id = "exp-temp"
kind = "reader"
question = "does temperature 0.0 vs 0.5 change stability?"
seed = 0

[dataset]
source = "eval_set"
name = "tiny"

[selection]
target = 8
context_radius = 2

instructions = "decide prose vs lineated"

[sweep]
temperature = ["0.0", "0.5"]

[[readers]]
tag = "grok"
model = "x-ai/grok-4.3"
modality = "text"
"""


class TempSensitiveCompleter:
    """Replies depend on the temperature it is CALLED with — so the two sweep points must produce
    distinct replies (and distinct cache keys, since the fingerprint folds in temperature)."""

    def __init__(self):
        self.temps_seen = []

    def complete(self, *, model, messages, temperature, max_tokens, response_format=None):
        self.temps_seen.append(temperature)
        listing = messages[0]["content"][0]["text"].split("Return ONLY")[0]
        keys = sorted(set(re.findall(r"\bL\d+\b", listing)))
        label = "lineated" if temperature == 0.0 else "prose"   # diverge by sampling temperature
        usage = {"prompt_tokens": 100, "completion_tokens": 20}
        return ChatReply(content=json.dumps([{"key": k, "lineation_label": label} for k in keys]),
                         finish_reason="stop", usage=usage)


def test_temperature_sweep_uses_distinct_cache_keys_and_yields_distinct_results(tmp_path):
    ann = tmp_path / "annotations"
    _frozen_eval_set(ann)
    exp = load_experiment(_TEMP_TOML, annotations=ann)
    fake = TempSensitiveCompleter()
    scorecard = run_study(exp, fake, _PRICES, now=_NOW, git_sha=_SHA,
                          experiments_dir=tmp_path / "experiments", annotations=ann)
    assert set(scorecard.results) == {"0.0", "0.5"}
    assert {0.0, 0.5} <= set(fake.temps_seen)              # both temperatures actually hit the model

    # distinct CACHE KEYS: the persisted replies for the SAME (item, tag, rep, model) carry DIFFERENT
    # prompt_hash across the two temperatures — so no reply is reused across the sweep.
    replies = store.load_experiment_replies("exp-temp", experiments=tmp_path / "experiments")
    hashes_by_call: dict[tuple, set] = {}
    for row in replies:
        hashes_by_call.setdefault((row["item_id"], row["tag"], row["rep"], row["model"]),
                                  set()).add(row["prompt_hash"])
    assert hashes_by_call and all(len(h) == 2 for h in hashes_by_call.values())   # two distinct fps

    cold = next(r for r in scorecard.results["0.0"] if r.tag == "grok")
    warm = next(r for r in scorecard.results["0.5"] if r.tag == "grok")
    assert cold.quality.lineated_recall == 1.0            # temp 0.0 → all lineated (truth lineated)
    assert warm.quality.lineated_recall == 0.0            # temp 0.5 → all prose → every line a miss
    assert cold.quality != warm.quality                  # distinct ReaderResults across the sweep


def _stub_page_renderer():
    """A PageRenderer that writes one solid PNG (no LibreOffice), recording its (docx, lo, hi) args."""
    from PIL import Image

    calls = []

    def render_page(docx, lo, hi, out_png):
        calls.append((docx, lo, hi))
        p = out_png.parent / "page-0.png"
        Image.new("RGB", (60, 30), (200, 30, 30)).save(p)
        return [p]

    render_page.calls = calls  # type: ignore[attr-defined]
    return render_page


def test_vision_build_specs_splits_an_over_page_region_and_attaches_assets(tmp_path):
    # the first paid run is vision+sweep — exercise the VISION path (select → tile → page-size SPLIT)
    # and the per-page asset attachment with a STUB renderer, so the splitter + compositor run without
    # LibreOffice. The eval set spans far more than one page, so the over-page region MUST split.
    from pathlib import Path

    from lineation_core.evaluation import study
    from lineation_core.teacher import recipes, render
    from lineation_core.teacher.tasks import AssetKind, Modality

    ann = tmp_path / "annotations"
    # a dense block of votable lines spanning ~200 source paragraphs: a large target keeps them in one
    # tiled region wider than the 120-paragraph page cap, so _build_specs's page-size pass MUST split it.
    wide = [r.id for r in store.load_records("57")
            if r.votable and r.id.is_mapped and r.id.src_ordinal <= 200]
    (ann / "eval_sets").mkdir(parents=True, exist_ok=True)
    (ann / "eval_sets" / "wide.json").write_text(
        json.dumps([{"id": lid.as_key(), "label": "lineated"} for lid in wide]))

    recipe = recipes.Recipe(
        task_id="vstudy", title="V", instructions="prose vs lineated", books=("57",),
        selector=recipes.EvalSet("wide"),
        readers=(ReaderConfig("grok", "x-ai/grok-4.3", Modality.VISION),),
        target=1000, context_radius=2)
    assert recipe.vision is True                           # vision-ness derives from the READERS

    specs, records = study._build_specs(recipe, annotations=ann)
    assert len(specs) > 1                                  # the over-page region split into pages
    sub = [s for s in specs if "s" in s.region_id]
    assert sub                                             # at least one page-size sub-region (…sN)
    covered = {lid for s in specs for lid in s.votable}
    assert covered == set(wide)                            # the split kept every votable line
    for s in specs:                                        # every sub-region fits within one page
        ords = sorted(lid.src_ordinal for lid in s.votable if lid.is_mapped)
        assert not ords or ords[-1] - ords[0] <= 120

    # the per-page asset attachment runs over those specs with a STUB page renderer (no LibreOffice).
    rp = _stub_page_renderer()
    assets = render.make_compositor(
        rp, docx_for=lambda b, lang: Path(f"/nonexistent/{b}/{lang}.docx"))(specs)
    for s in specs:
        page_assets = assets[s.region_id]
        assert page_assets and all(a.kind is AssetKind.COMPOSITE for a in page_assets)
    assert rp.calls                                       # the stub renderer was actually driven


def test_second_run_resumes_at_zero_cost_and_is_byte_identical(tmp_path):
    first = CannedCompleter()
    sc1, ann = _run(tmp_path, first)
    assert first.calls > 0                              # the first run actually fetched
    folder = tmp_path / "experiments" / "exp-contract"
    assert (folder / "replies.jsonl").is_file()         # the resume cache was written
    scorecard1 = json.loads((folder / "scorecard.json").read_text())

    # a SECOND run over the SAME folder must reuse every reply → ZERO completer calls, AND — even at a
    # LATER wall-clock — write a byte-identical scorecard: the manifest timestamp records when the
    # evidence was FIRST produced, not the replay moment, so a $0 replay is fully idempotent.
    second = CannedCompleter()
    exp = load_experiment(_TOML, annotations=ann)
    later = datetime(2026, 6, 9, 18, 30, 0, tzinfo=UTC)   # a different wall-clock than the first run
    run_study(exp, second, _PRICES, now=later, git_sha=_SHA,
              experiments_dir=tmp_path / "experiments", annotations=ann)
    assert second.calls == 0                             # $0-on-re-run invariant
    scorecard2 = json.loads((folder / "scorecard.json").read_text())
    assert scorecard2 == scorecard1                     # byte-identical despite the later `now`
    assert scorecard2["manifest"]["timestamp"] == _NOW.isoformat()   # first-produced time preserved
