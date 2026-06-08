# research-pure: tests for identity, records, and the artifact. Stdlib only, scratch I/O.
"""Proves the identity + hash + record + schema obligations on crafted data (no DOCX
needed — identity must hold independent of the producer)."""
from __future__ import annotations

import json

import pytest
from lineation_core import artifact, identity
from lineation_core.identity import LineId
from lineation_core.annotations import LabelSource, LineLabel
from lineation_core.records import (
    EndPunct,
    FeatureSchema,
    IndentVsBook,
    InlineRun,
    LineFeatures,
    LineMeta,
    LineRecord,
    Role,
    SourceFate,
    SpacingVsBook,
    feature_field_names,
)


def _feat(**over):
    base = dict(
        fill=0.42, wraps=False, char_len=10, word_count=2, end_punct=EndPunct.SENTENCE,
        starts_lower=False, next_line_lower=False, enjambs=False, colon_opens=False,
        align="left", indent_vs_book=IndentVsBook.DEFAULT,
        spacing_after_vs_book=SpacingVsBook.TYPICAL, align_is_book_default=True,
        numbered=False, sub=0, n_subs=1, run_len=1, run_pos=0, prev_structural=False,
        next_structural=False, fill_pctile_in_book=0.5,
    )
    base.update(over)
    return LineFeatures(**base)


def _rec(lang="ru", book="64", ordn=10, sub=0, text="Hello world."):
    return LineRecord(
        id=LineId(lang, book, ordn, sub), text=text,
        inlines=(InlineRun(text, ""),), role=Role.BODY, votable=True,
        source_fate=SourceFate.NORMAL, features=_feat(),
        paragraph_text_hash=identity.text_hash(text), line_text_hash=identity.text_hash(text),
        meta=LineMeta(style_id="", block_index=ordn, src_ordinal=ordn),
    )


# --- LineId identity ---

def test_lineid_validates_lang():
    with pytest.raises(ValueError):
        LineId("de", "64", 1, 0)


def test_lineid_validates_bookid_padded():
    with pytest.raises(ValueError):
        LineId("ru", "64x", 1, 0)
    with pytest.raises(ValueError):
        LineId("ru", "5", 1, 0)  # not zero-padded


def test_lineid_key_roundtrip():
    lid = LineId("en", "01", 8103, 2)
    assert LineId.from_key(lid.as_key()) == lid
    assert LineId.from_key(json.loads(json.dumps(lid.as_key()))) == lid


def test_lineid_is_hashable_and_orders_document_order():
    a = LineId("ru", "64", 10, 0)
    b = LineId("ru", "64", 10, 1)
    c = LineId("ru", "64", 11, 0)
    assert {a, b, c} == {a, b, c}
    assert sorted([c, b, a]) == [a, b, c]


def test_lineid_mapped_vs_unmapped_band():
    m = LineId.mapped("ru", "64", 10, 0)
    assert m.is_mapped and m.src_ordinal == 10
    u = LineId.unmapped("ru", "64", 5, 0)
    assert not u.is_mapped
    assert u.src_ordinal > m.src_ordinal
    with pytest.raises(ValueError):
        LineId.mapped("ru", "64", 9_000_001, 0)
    assert LineId.unmapped("ru", "64", 5, 0) != LineId.unmapped("ru", "64", 6, 0)


def test_linemeta_typed_roundtrip():
    m = LineMeta(style_id="Normal", block_index=42, src_ordinal=40)
    assert LineMeta.from_dict(m.to_dict()) == m
    u = LineMeta(style_id="", block_index=7, src_ordinal=None)
    assert LineMeta.from_dict(u.to_dict()).src_ordinal is None


# --- hash safety rails ---

def test_text_hash_nfc_stable_but_content_sensitive():
    import unicodedata
    decomposed = unicodedata.normalize("NFD", "é")
    composed = unicodedata.normalize("NFC", "é")
    assert decomposed != composed
    assert identity.text_hash(decomposed) == identity.text_hash(composed)
    assert identity.text_hash("é") != identity.text_hash("e")


def test_record_roundtrip_through_dict():
    r = _rec()
    r2 = LineRecord.from_dict(json.loads(json.dumps(r.to_dict())))
    assert r2 == r


def test_loader_fails_loud_on_docx_hash_mismatch(tmp_path):
    r = _rec()
    recs = tmp_path / artifact.RECORDS_FILE
    artifact.write_jsonl(recs, [r.to_dict()])
    man = tmp_path / artifact.MANIFEST_FILE
    man.write_text(json.dumps(artifact.Manifest(
        artifact.PRODUCER_VERSION, artifact.FEATURE_SCHEMA_VERSION, "deadbeefdeadbeef",
        "ru", "64", 1).to_dict()))
    with pytest.raises(artifact.HashMismatch):
        artifact.load_records(recs, man, live_docx_hash="0000000000000000")
    got = artifact.load_records(recs, man, live_docx_hash="0000000000000000", migration=True)
    assert got == [r]


def test_loader_always_fatal_on_schema_version_skew(tmp_path):
    r = _rec()
    recs = tmp_path / "r.jsonl"
    artifact.write_jsonl(recs, [r.to_dict()])
    man = tmp_path / "m.json"
    man.write_text(json.dumps(artifact.Manifest(
        artifact.PRODUCER_VERSION, "phi-OLD", "h", "ru", "64", 1).to_dict()))
    with pytest.raises(artifact.HashMismatch):
        artifact.load_records(recs, man, live_docx_hash="h", migration=True)


def test_loader_detects_corrupt_line_text_hash(tmp_path):
    r = _rec(text="Hello world.")
    d = r.to_dict()
    d["text"] = "Tampered."
    recs = tmp_path / "r.jsonl"
    artifact.write_jsonl(recs, [d])
    man = tmp_path / "m.json"
    man.write_text(json.dumps(artifact.Manifest(
        artifact.PRODUCER_VERSION, artifact.FEATURE_SCHEMA_VERSION, "h", "ru", "64", 1).to_dict()))
    with pytest.raises(artifact.HashMismatch):
        artifact.load_records(recs, man, live_docx_hash="h")


def test_loader_detects_duplicate_lineid(tmp_path):
    r = _rec()
    recs = tmp_path / "r.jsonl"
    artifact.write_jsonl(recs, [r.to_dict(), r.to_dict()])
    man = tmp_path / "m.json"
    man.write_text(json.dumps(artifact.Manifest(
        artifact.PRODUCER_VERSION, artifact.FEATURE_SCHEMA_VERSION, "h", "ru", "64", 2).to_dict()))
    with pytest.raises(artifact.HashMismatch):
        artifact.load_records(recs, man, live_docx_hash="h")


def test_loader_detects_count_mismatch(tmp_path):
    r = _rec()
    recs = tmp_path / "r.jsonl"
    artifact.write_jsonl(recs, [r.to_dict()])
    man = tmp_path / "m.json"
    man.write_text(json.dumps(artifact.Manifest(
        artifact.PRODUCER_VERSION, artifact.FEATURE_SCHEMA_VERSION, "h", "ru", "64", 99).to_dict()))
    with pytest.raises(artifact.HashMismatch):
        artifact.load_records(recs, man, live_docx_hash="h")


# --- label lineage ---

def test_label_constrained_to_two_classes():
    with pytest.raises(ValueError):
        LineLabel(LineId("ru", "64", 1, 0), "verse", LabelSource.HUMAN, None, "", "", {})


def test_label_roundtrip_preserves_lineage():
    g = LineLabel(
        LineId("ru", "37", 388, 0), "lineated", LabelSource.HUMAN, 0.9,
        "ingested", "IR pipeline bug note: 1. Вода mangled in prose render",
        {"rid": "g05_b37", "idx": 388, "sub": 0}, line_text_hash=identity.text_hash("1. Вода"),
    )
    g2 = LineLabel.from_dict(json.loads(json.dumps(g.to_dict())))
    assert g2 == g
    assert g2.provenance == {"rid": "g05_b37", "idx": 388, "sub": 0}
    assert "bug" in g2.notes


# --- feature schema + zero-support rail ---

def test_feature_field_names_match_dataclass_and_vector_order():
    names = feature_field_names()
    assert names[0] == "fill" and "fill_pctile_in_book" in names
    assert len(names) == len(set(names))
    assert set(_feat().to_dict().keys()) == set(names)


def test_zero_support_feature_is_reported_not_dropped():
    fields = feature_field_names()
    support = {f: 5 for f in fields}
    support["colon_opens"] = 0
    sch = FeatureSchema(artifact.FEATURE_SCHEMA_VERSION, artifact.PRODUCER_VERSION, fields, support)
    assert "colon_opens" in sch.zero_support()
    assert "colon_opens" in sch.fields
