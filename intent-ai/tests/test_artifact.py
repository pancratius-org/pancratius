# research-pure: the on-disk artifact round-trips byte-identically and fails loud on drift.
"""Proves the SPEC product and its fail-loud provenance rails."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from intent_ai import artifact, identity, producer
from intent_ai.identity import BookId, BookKey, DocxPackageHash
from intent_ai.paths import BOOKS
from intent_ai.records import FeatureSchema

from tests.record_factory import sample_records

B57 = BOOKS / "57-ya-otdayushchii" / "ru.docx"
BOOK = BookId("57")
EDITION = BookKey("ru", BOOK)


def _source_file(tmp_path: Path) -> tuple[Path, DocxPackageHash]:
    source = tmp_path / "source.docx"
    source.write_bytes(b"synthetic DOCX provenance")
    return source, identity.docx_package_hash(source)


def test_emit_then_load_is_byte_identical(tmp_path):
    """Write → read round-trips to records EQUAL to the in-memory producer output. The
    artifact, not a live function call, is the substrate every consumer reads."""
    recs = sample_records()
    _, docx_hash = _source_file(tmp_path)
    artifact.emit(tmp_path, recs, lang="ru", book_id=BOOK, docx_hash=docx_hash)
    loaded = artifact.load_artifact(
        tmp_path, live_docx_hash=docx_hash, expected_book=EDITION
    )
    assert loaded == recs
    # every standard file was written
    assert (tmp_path / artifact.RECORDS_FILE).is_file()
    assert (tmp_path / artifact.SCHEMA_FILE).is_file()
    assert (tmp_path / artifact.MANIFEST_FILE).is_file()


def test_loaded_artifact_fails_loud_on_wrong_docx(tmp_path):
    recs = sample_records()
    _, real = _source_file(tmp_path)
    artifact.emit(tmp_path, recs, lang="ru", book_id=BOOK, docx_hash=real)
    with pytest.raises(artifact.HashMismatch):
        artifact.load_artifact(
            tmp_path, live_docx_hash=DocxPackageHash("0" * 16), expected_book=EDITION
        )
    # migration=True relaxes ONLY the docx rail
    assert artifact.load_artifact(
        tmp_path,
        live_docx_hash=DocxPackageHash("0" * 16),
        expected_book=EDITION,
        migration=True,
    ) == recs


def test_schema_lists_zero_support_features_explicitly(tmp_path):
    """A schema feature whose vector column is active on NO row appears in the schema and is
    reported as zero-support — it never vanishes from analysis (the speaker-label=0 lesson)."""
    recs = sample_records()
    _, docx_hash = _source_file(tmp_path)
    artifact.emit(tmp_path, recs, lang="ru", book_id=BOOK, docx_hash=docx_hash)
    sch = FeatureSchema.from_dict(json.loads((tmp_path / artifact.SCHEMA_FILE).read_text()))
    # the full column space is present, and at least one categorical level has zero support
    assert set(sch.feature_support) == set(producer.vector_columns())
    assert "align=center" in sch.feature_support  # rare/never in this book — still a column
    assert any(v == 0 for v in sch.feature_support.values())
    assert sch.zero_support()  # non-empty: some column is unobserved here


@pytest.mark.corpus_source
def test_build_then_load_is_the_substrate(tmp_path):
    """`build_records_artifact` emits once; `load_records_artifact` then LOADS from disk — the
    records a consumer reads are the artifact's, validated against the live docx, and EQUAL to
    the live producer output. Build and load are SEPARATE: load never re-emits."""
    live = producer.read_lines(B57, "ru", BOOK)
    built = artifact.build_records_artifact(B57, "ru", BOOK, store=tmp_path)
    assert built == live                       # the on-disk substrate == the producer output
    assert (tmp_path / "57-ru" / artifact.MANIFEST_FILE).is_file()
    loaded = artifact.load_records_artifact(B57, "ru", BOOK, store=tmp_path)  # from disk, no emit
    assert loaded == live


def test_load_records_artifact_fails_loud_on_missing_store(tmp_path):
    """A consumer load on an UN-built store FAILS LOUD — it does not silently rebuild (which
    would trigger a render). The empty store has no manifest, so the loader raises."""
    source, _ = _source_file(tmp_path)
    with pytest.raises((FileNotFoundError, artifact.HashMismatch)):
        artifact.load_records_artifact(source, "ru", BOOK, store=tmp_path)
    # and nothing was emitted as a side effect — the store stays empty.
    assert not (tmp_path / "57-ru").exists()


def test_load_records_artifact_fails_loud_on_stale_version(tmp_path):
    """A store whose manifest pins an OLD schema version fails loud on load — never re-emits to
    'fix' it. Build, then corrupt the manifest version, then load must raise."""
    source, docx_hash = _source_file(tmp_path)
    out = tmp_path / "57-ru"
    artifact.emit(out, sample_records(), lang="ru", book_id=BOOK, docx_hash=docx_hash)
    man = tmp_path / "57-ru" / artifact.MANIFEST_FILE
    d = json.loads(man.read_text())
    d["feature_schema_version"] = "phi-OLD"
    man.write_text(json.dumps(d))
    with pytest.raises(artifact.HashMismatch):
        artifact.load_records_artifact(source, "ru", BOOK, store=tmp_path)


def test_artifact_digests_reject_feature_and_schema_tampering(tmp_path):
    _, docx_hash = _source_file(tmp_path)
    artifact.emit(tmp_path, sample_records(), lang="ru", book_id=BOOK, docx_hash=docx_hash)
    records_path = tmp_path / artifact.RECORDS_FILE
    lines = records_path.read_text().splitlines()
    row = json.loads(lines[0])
    row["features"]["fill"] += 1.0
    lines[0] = json.dumps(row, ensure_ascii=False)
    records_path.write_text("\n".join(lines) + "\n")
    with pytest.raises(artifact.HashMismatch, match="line_records"):
        artifact.load_artifact(
            tmp_path, live_docx_hash=docx_hash, expected_book=EDITION
        )

    artifact.emit(tmp_path, sample_records(), lang="ru", book_id=BOOK, docx_hash=docx_hash)
    schema_path = tmp_path / artifact.SCHEMA_FILE
    schema_path.write_text(schema_path.read_text().replace('"fields"', '"changed"', 1))
    with pytest.raises(artifact.HashMismatch, match="feature_schema"):
        artifact.load_artifact(
            tmp_path, live_docx_hash=docx_hash, expected_book=EDITION
        )


def test_artifact_rejects_the_wrong_edition_identity(tmp_path):
    _, docx_hash = _source_file(tmp_path)
    artifact.emit(tmp_path, sample_records(), lang="ru", book_id=BOOK, docx_hash=docx_hash)
    with pytest.raises(artifact.HashMismatch, match="identity"):
        artifact.load_artifact(
            tmp_path, live_docx_hash=docx_hash, expected_book=BookKey("en", BOOK)
        )
