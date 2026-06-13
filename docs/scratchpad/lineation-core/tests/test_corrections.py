# research-pure: proofs for the correction exporter — what crosses the boundary and what is withheld.
"""The exporter writes the TOTAL projection of exportable truth: non-holdout human/override
labels contradicting the sidecar-FREE baseline, prose direction only. Holdout truth is withheld
(exporting an eval item's answer makes that eval circular); the lineated direction is withheld
(unappliable); a sidecar whose corrections are gone is deleted, so a retraction propagates."""
from __future__ import annotations

import json

import pytest

from lineation_core import corrections, paths
from lineation_core.annotations import LabelSet, LabelSource, LineLabel
from lineation_core.identity import LineId


def _label(book, ordn, label, *, source=LabelSource.HUMAN, holdout=False, lang="ru"):
    return LineLabel(id=LineId(lang, book, ordn, 0), label=label, source=source,
                     confidence=None, audit_status="", notes="", provenance={}, holdout=holdout)


@pytest.fixture
def fake_world(tmp_path, monkeypatch):
    books = tmp_path / "books"
    for b in ("17", "24"):
        (books / f"{b}-x").mkdir(parents=True)
        (books / f"{b}-x" / "ru.docx").touch()
    monkeypatch.setattr(paths, "BOOKS", books)
    monkeypatch.setattr(paths, "book_docx",
                        lambda book_id, lang="ru": books / f"{book_id}-x" / f"{lang}.docx")
    corrections._baseline_decisions.cache_clear()
    corrections._row_texts.cache_clear()
    # baseline: the importer lineates every ordinal here (the weak-direction errors live there)
    monkeypatch.setattr(corrections, "_baseline_decisions",
                        lambda lang, book_id: {140: True, 141: True, 1522: True})
    monkeypatch.setattr(corrections, "_row_texts",
                        lambda lang, book_id: {140: "сто сорок", 141: "сто сорок один",
                                               1522: "тысяча"})
    return books


def _with_labels(monkeypatch, labels):
    monkeypatch.setattr(corrections, "load_labels",
                        lambda: LabelSet(labels=labels, n_rejected_unmapped=0))


def test_export_writes_prose_withholds_holdout_and_lineated(fake_world, monkeypatch):
    _with_labels(monkeypatch, [
        _label("17", 140, "prose"),                      # exportable contradiction
        _label("17", 141, "prose"),                      # exportable contradiction
        _label("24", 1522, "prose", holdout=True),       # withheld: eval-only truth
        _label("17", 1522, "lineated"),                  # agrees with baseline — no row
    ])
    report = corrections.export()
    assert report.n_prose_corrections == 2
    assert report.n_holdout_withheld == 1
    assert report.n_lineated_pending == 0
    (path,) = report.written
    assert path.name == "lineation.ru.json" and path.parent.name == "17-x"
    entries = json.loads(path.read_text())
    assert sorted(entries) == ["140", "141"]
    assert all(e["register"] == "prose" and e["text_sha"] for e in entries.values())


def test_retracted_label_deletes_its_sidecar(fake_world, monkeypatch):
    _with_labels(monkeypatch, [_label("17", 140, "prose")])
    (first,) = corrections.export().written
    assert first.is_file()
    _with_labels(monkeypatch, [])                        # the label was retracted
    report = corrections.export()
    assert not report.written
    assert report.deleted == (first,)
    assert not first.is_file()


def test_export_is_idempotent_because_the_baseline_ignores_sidecars(fake_world, monkeypatch):
    _with_labels(monkeypatch, [_label("17", 140, "prose")])
    r1 = corrections.export()
    r2 = corrections.export()                            # sidecar exists; projection unchanged
    assert r1.n_prose_corrections == r2.n_prose_corrections == 1
    assert list(r1.written) == list(r2.written)


def test_gate_labels_never_export(fake_world, monkeypatch):
    _with_labels(monkeypatch, [_label("17", 140, "prose", source=LabelSource.GATE)])
    report = corrections.export()
    assert report.n_prose_corrections == 0 and not report.written


def test_conflicting_sub_truth_is_skipped_and_surfaced(fake_world, monkeypatch):
    a = _label("17", 140, "prose")
    b = LineLabel(id=LineId("ru", "17", 140, 1), label="lineated", source=LabelSource.HUMAN,
                  confidence=None, audit_status="", notes="", provenance={})
    _with_labels(monkeypatch, [a, b])
    report = corrections.export()
    assert report.n_conflicting_ordinals == 1
    assert not report.written
