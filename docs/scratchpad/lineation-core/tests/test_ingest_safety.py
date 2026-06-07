# research-pure: human ingest REFUSES to promote an adjudication carrying any resolution fault.
"""Gates `recipes.ingest` symmetric with `recipes.panel`: a downloaded human adjudication that
resolves against the manifest with ANY fault (bad label, a missing key under complete-mode, an
unknown/unmapped key) raises `IngestRefused` and promotes NOTHING; a clean adjudication still
promotes. Fault DETECTION is unit-tested in test_responses (incl. text-drift); here we prove the
GATE fires and nothing leaks into committed truth."""
from __future__ import annotations

import json

import pytest

from lineation_core import store
from lineation_core.teacher import recipes, tasks


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


def _manifest(ann, st) -> tasks.TaskManifest:
    _, manifest_d = store.load_task_bundle("acq", annotations=ann, store=st)
    return tasks.TaskManifest.from_dict(manifest_d)


def _export(label_by_key: dict[str, str], manifest: tasks.TaskManifest) -> dict:
    """A fake adjudicate.html export: {responses: {region_id: {lines: {key: label}}}}."""
    out: dict[str, dict] = {}
    for key, label in label_by_key.items():
        region = manifest.item_by_key[key]
        out.setdefault(region, {"lines": {}})["lines"][key] = label
    return {"responses": out}


def test_clean_adjudication_promotes(tmp_path):
    r, ann, st = _build(tmp_path)
    manifest = _manifest(ann, st)
    store.save_human_responses("acq", _export({k: "prose" for k in manifest.by_key}, manifest),
                               annotations=ann)
    assert recipes.ingest(r, annotations=ann, teacher_store=st) == 5
    assert len(store.load_label_rows(annotations=ann)) == 5


def test_bad_label_refuses(tmp_path):
    r, ann, st = _build(tmp_path)
    manifest = _manifest(ann, st)
    by = {k: "prose" for k in manifest.by_key}
    by[sorted(by)[0]] = "maybe"                              # invalid label → BAD_LABEL
    store.save_human_responses("acq", _export(by, manifest), annotations=ann)
    with pytest.raises(recipes.IngestRefused, match="resolution"):
        recipes.ingest(r, annotations=ann, teacher_store=st)
    with pytest.raises(FileNotFoundError):                   # nothing promoted
        store.load_label_rows(annotations=ann)


def test_missing_key_refuses(tmp_path):
    r, ann, st = _build(tmp_path)
    manifest = _manifest(ann, st)
    by = {k: "prose" for k in sorted(manifest.by_key)[1:]}   # omit the first → MISSING_KEY (complete)
    store.save_human_responses("acq", _export(by, manifest), annotations=ann)
    with pytest.raises(recipes.IngestRefused, match="resolution"):
        recipes.ingest(r, annotations=ann, teacher_store=st)
    with pytest.raises(FileNotFoundError):
        store.load_label_rows(annotations=ann)


def test_unknown_key_refuses(tmp_path):
    r, ann, st = _build(tmp_path)
    manifest = _manifest(ann, st)
    export = _export({k: "prose" for k in manifest.by_key}, manifest)
    region = next(iter(export["responses"]))
    export["responses"][region]["lines"]["L999"] = "prose"  # a key no manifest item owns → UNMAPPED_KEY
    store.save_human_responses("acq", export, annotations=ann)
    with pytest.raises(recipes.IngestRefused, match="resolution"):
        recipes.ingest(r, annotations=ann, teacher_store=st)
    with pytest.raises(FileNotFoundError):
        store.load_label_rows(annotations=ann)
