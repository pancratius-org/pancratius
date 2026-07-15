# research-pure: the evidence-not-truth rail — a study never imports the promote path nor writes truth.
"""Locks the experiment invariant: `evaluation/*` does not import `teacher.promote` (only a teacher
recipe makes labels), and a study run leaves the real committed `annotations/` byte-unchanged — a study
produces EVIDENCE (a scorecard in its own folder), never truth."""
from __future__ import annotations

import ast
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from intent_ai import paths, store
from intent_ai.evaluation import study
from intent_ai.evaluation.reader_metrics import PriceTable
from intent_ai.evaluation.study import load_experiment, run_study
from intent_ai.teacher.panel import ChatReply

from tests.record_factory import sample_records

_EVAL_DIR = Path(study.__file__).parent
pytestmark = pytest.mark.usefixtures("sample_record_store")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                names.add(f"{node.module}.{alias.name}")
            names.add(node.module)
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
    return names


def test_evaluation_never_imports_the_promote_path():
    for py in _EVAL_DIR.rglob("*.py"):
        imports = _imports(py)
        assert not any("teacher.promote" in i or i.endswith(".promote") for i in imports), \
            f"{py.name} imports the promote path — a study must not make truth"


class _Canned:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, *, model, messages, temperature, max_tokens, response_format=None):
        self.calls += 1
        listing = messages[0]["content"][0]["text"].split("Return ONLY")[0]
        keys = sorted(set(re.findall(r"\bL\d+\b", listing)))
        return ChatReply(content=json.dumps([{"key": k, "lineation_label": "lineated"} for k in keys]),
                         finish_reason="stop", usage={"prompt_tokens": 10, "completion_tokens": 5})


_TOML = """
[experiment]
id = "exp-rail"
[dataset]
source = "eval_set"
name = "tiny"
instructions = "decide"
[sweep]
contract = ["json_array"]
[[readers]]
tag = "ds"
model = "deepseek/deepseek-v4-flash"
modality = "text"
"""


def _fingerprint_annotations() -> dict[str, str]:
    """Every committed annotation file's sha256 — the rail to prove a study touched none of it."""
    return {str(p.relative_to(paths.ANNOTATIONS)): store.sha256_file(p)
            for p in paths.ANNOTATIONS.rglob("*") if p.is_file()}


def test_run_study_leaves_real_annotations_byte_unchanged(tmp_path):
    before = _fingerprint_annotations()
    ann = tmp_path / "annotations"
    (ann / "eval_sets").mkdir(parents=True)
    picks = [record for record in sample_records() if record.votable][:3]
    (ann / "eval_sets" / "tiny.json").write_text(
        json.dumps([record.id.as_key() for record in picks])
    )
    (ann / "labels.jsonl").write_text("".join(
        json.dumps({
            "id": record.id.as_key(),
            "label": "lineated",
            "source": "human",
            "line_text_hash": record.line_text_hash,
        }) + "\n"
        for record in picks
    ))
    exp = load_experiment(_TOML, annotations=ann)
    completer = _Canned()
    scorecard = run_study(exp, completer, PriceTable(version="t", models={"deepseek/deepseek-v4-flash": (1e-6, 1e-6)}),
              now=datetime(2026, 6, 9, tzinfo=UTC), git_sha="abc",
              experiments_dir=tmp_path / "experiments", annotations=ann)
    assert completer.calls > 0 and scorecard.results
    assert _fingerprint_annotations() == before          # real committed truth untouched
