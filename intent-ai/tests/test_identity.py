# research-pure: tests for identity, records, and the artifact. Stdlib only, scratch I/O.
"""Proves the identity + hash + record + schema obligations on crafted data (no DOCX
needed — identity must hold independent of the producer)."""
from __future__ import annotations

import json

import pytest
from intent_ai import artifact, identity
from intent_ai.annotations import LabelSource, LineLabel
from intent_ai.identity import BookId, BookKey, LegacyLineId, LineId
from intent_ai.records import (
    EndPunct,
    FeatureSchema,
    IndentVsBook,
    LineFeatures,
    LineRecord,
    Role,
    SpacingVsBook,
    feature_field_names,
)


def _feat(**over):
    base = dict(
        fill=0.42, wraps=False, char_len=10, word_count=2, end_punct=EndPunct.SENTENCE,
        starts_lower=False, next_line_lower=False, enjambs=False, colon_opens=False,
        align="left", indent_vs_book=IndentVsBook.DEFAULT,
        spacing_after_vs_book=SpacingVsBook.TYPICAL, align_is_book_default=True,
        sub=0, n_subs=1, run_len=1, run_pos=0, fill_pctile_in_book=0.5,
    )
    base.update(over)
    return LineFeatures(**base)


def _rec(lang="ru", book="64", ordn=10, sub=0, text="Hello world."):
    return LineRecord(
        id=LineId(lang, book, ordn, sub), text=text,
        role=Role.BODY, features=_feat(), line_text_hash=identity.text_hash(text),
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


def test_book_id_is_a_validated_value_object() -> None:
    assert BookId("01") == "01"
    with pytest.raises(ValueError):
        BookId("1")


def test_legacy_and_canonical_line_ids_cannot_compare_equal() -> None:
    key = ["ru", "64", 10, 0]
    assert LegacyLineId.from_key(key).as_key() == LineId.from_key(key).as_key()
    assert LegacyLineId.from_key(key) != LineId.from_key(key)


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


def test_record_rejects_truthy_non_boolean_wire_values() -> None:
    raw = _rec().to_dict()
    raw["features"]["wraps"] = "false"
    with pytest.raises(ValueError, match="JSON boolean"):
        LineRecord.from_dict(raw)


def test_feature_coordinates_make_boundaries_derived() -> None:
    middle = _feat(run_len=3, run_pos=1)
    assert not middle.prev_structural and not middle.next_structural
    assert _feat(run_len=3, run_pos=0).prev_structural
    assert _feat(run_len=3, run_pos=2).next_structural
    with pytest.raises(ValueError):
        _feat(run_len=2, run_pos=2)


def test_record_rejects_stale_hash_and_sub_index() -> None:
    with pytest.raises(ValueError, match="stale"):
        LineRecord(
            id=LineId("ru", "64", 1, 0),
            text="truth",
            role=Role.BODY,
            features=_feat(),
            line_text_hash=identity.text_hash("other"),
        )
    with pytest.raises(ValueError, match="sub-index"):
        LineRecord(
            id=LineId("ru", "64", 1, 1),
            text="truth",
            role=Role.BODY,
            features=_feat(),
            line_text_hash=identity.text_hash("truth"),
        )


def _load(tmp_path, *, live_hash="h", migration=False):
    return artifact.load_artifact(
        tmp_path,
        live_docx_hash=live_hash,
        expected_book=BookKey("ru", "64"),
        migration=migration,
    )


def test_loader_fails_loud_on_docx_hash_mismatch(tmp_path):
    record = _rec()
    artifact.emit(tmp_path, [record], lang="ru", book_id="64", docx_hash="deadbeefdeadbeef")
    with pytest.raises(artifact.HashMismatch):
        _load(tmp_path, live_hash="0000000000000000")
    assert _load(tmp_path, live_hash="0000000000000000", migration=True) == [record]


def test_loader_always_fatal_on_schema_version_skew(tmp_path):
    artifact.emit(tmp_path, [_rec()], lang="ru", book_id="64", docx_hash="h")
    manifest = tmp_path / artifact.MANIFEST_FILE
    payload = json.loads(manifest.read_text())
    payload["feature_schema_version"] = "phi-OLD"
    manifest.write_text(json.dumps(payload))
    with pytest.raises(artifact.HashMismatch):
        _load(tmp_path, migration=True)


def test_loader_detects_corrupt_line_text_hash(tmp_path):
    artifact.emit(tmp_path, [_rec(text="Hello world.")], lang="ru", book_id="64", docx_hash="h")
    records = tmp_path / artifact.RECORDS_FILE
    records.write_text(records.read_text().replace("Hello world.", "Tampered."))
    with pytest.raises(artifact.HashMismatch):
        _load(tmp_path)


def test_loader_detects_duplicate_lineid(tmp_path):
    artifact.emit(tmp_path, [_rec()], lang="ru", book_id="64", docx_hash="h")
    records = tmp_path / artifact.RECORDS_FILE
    records.write_text(records.read_text() * 2)
    with pytest.raises(artifact.HashMismatch):
        _load(tmp_path)


def test_loader_detects_count_mismatch(tmp_path):
    artifact.emit(tmp_path, [_rec()], lang="ru", book_id="64", docx_hash="h")
    manifest = tmp_path / artifact.MANIFEST_FILE
    payload = json.loads(manifest.read_text())
    payload["n_records"] = 99
    manifest.write_text(json.dumps(payload))
    with pytest.raises(artifact.HashMismatch):
        _load(tmp_path)


# --- label lineage ---

def test_label_constrained_to_two_classes():
    with pytest.raises(ValueError):
        LineLabel(LineId("ru", "64", 1, 0), "verse", LabelSource.HUMAN, None, "", "", {}, "h")


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

def test_feature_field_names_add_derived_boundaries_without_storing_them():
    names = feature_field_names()
    assert names[0] == "fill" and "fill_pctile_in_book" in names
    assert len(names) == len(set(names))
    stored = set(_feat().to_dict())
    assert stored == set(names) - {"prev_structural", "next_structural"}
    assert names.index("run_pos") < names.index("prev_structural")
    assert names.index("next_structural") < names.index("fill_pctile_in_book")


def test_zero_support_feature_is_reported_not_dropped():
    fields = feature_field_names()
    support = {f: 5 for f in fields}
    support["colon_opens"] = 0
    sch = FeatureSchema(artifact.FEATURE_SCHEMA_VERSION, artifact.PRODUCER_VERSION, fields, support)
    assert "colon_opens" in sch.zero_support()
    assert "colon_opens" in sch.fields
