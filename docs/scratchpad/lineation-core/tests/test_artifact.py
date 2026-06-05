# research-pure: the on-disk artifact round-trips byte-identically and fails loud on drift.
"""Proves the SPEC product: emit a book's records + schema + manifest to disk, read them
back, and get byte-identical records (the artifact IS the substrate). Uses small book 57."""
from __future__ import annotations

from functools import lru_cache

import pytest
from lineation_core import artifact, identity, producer
from lineation_core.paths import BOOKS
from lineation_core.records import FeatureSchema

B57 = BOOKS / "57-ya-otdayushchii" / "ru.docx"


@lru_cache(maxsize=1)
def _recs():
    return producer.read_lines(B57, "ru", "57")


def test_emit_then_load_is_byte_identical(tmp_path):
    """Write → read round-trips to records EQUAL to the in-memory producer output. The
    artifact, not a live function call, is the substrate every consumer reads."""
    recs = list(_recs())
    docx_hash = identity.docx_package_hash(B57)
    artifact.emit(tmp_path, recs, lang="ru", book_id="57", docx_hash=docx_hash)
    loaded = artifact.load_artifact(tmp_path, live_docx_hash=docx_hash)
    assert loaded == recs
    # every standard file was written
    assert (tmp_path / artifact.RECORDS_FILE).is_file()
    assert (tmp_path / artifact.SCHEMA_FILE).is_file()
    assert (tmp_path / artifact.MANIFEST_FILE).is_file()


def test_loaded_artifact_fails_loud_on_wrong_docx(tmp_path):
    recs = list(_recs())
    real = identity.docx_package_hash(B57)
    artifact.emit(tmp_path, recs, lang="ru", book_id="57", docx_hash=real)
    with pytest.raises(artifact.HashMismatch):
        artifact.load_artifact(tmp_path, live_docx_hash="0" * 16)
    # migration=True relaxes ONLY the docx rail
    assert artifact.load_artifact(tmp_path, live_docx_hash="0" * 16, migration=True) == recs


def test_schema_lists_zero_support_features_explicitly(tmp_path):
    """A schema feature whose vector column is active on NO row appears in the schema and is
    reported as zero-support — it never vanishes from analysis (the speaker-label=0 lesson)."""
    recs = list(_recs())
    artifact.emit(tmp_path, recs, lang="ru", book_id="57",
                  docx_hash=identity.docx_package_hash(B57))
    import json
    sch = FeatureSchema.from_dict(json.loads((tmp_path / artifact.SCHEMA_FILE).read_text()))
    # the full column space is present, and at least one categorical level has zero support
    assert set(sch.feature_support) == set(producer.vector_columns())
    assert "align=center" in sch.feature_support  # rare/never in this book — still a column
    assert any(v == 0 for v in sch.feature_support.values())
    assert sch.zero_support()  # non-empty: some column is unobserved here


def test_build_then_load_is_the_substrate(tmp_path):
    """`build_records_artifact` emits once; `load_records_artifact` then LOADS from disk — the
    records a consumer reads are the artifact's, validated against the live docx, and EQUAL to
    the live producer output. Build and load are SEPARATE: load never re-emits."""
    live = list(_recs())
    built = artifact.build_records_artifact(B57, "ru", "57", store=tmp_path)
    assert built == live                       # the on-disk substrate == the producer output
    assert (tmp_path / "57-ru" / artifact.MANIFEST_FILE).is_file()
    loaded = artifact.load_records_artifact(B57, "ru", "57", store=tmp_path)  # from disk, no emit
    assert loaded == live


def test_load_records_artifact_fails_loud_on_missing_store(tmp_path):
    """A consumer load on an UN-built store FAILS LOUD — it does not silently rebuild (which
    would trigger a render). The empty store has no manifest, so the loader raises."""
    with pytest.raises((FileNotFoundError, artifact.HashMismatch)):
        artifact.load_records_artifact(B57, "ru", "57", store=tmp_path)
    # and nothing was emitted as a side effect — the store stays empty.
    assert not (tmp_path / "57-ru").exists()


def test_load_records_artifact_fails_loud_on_stale_version(tmp_path):
    """A store whose manifest pins an OLD schema version fails loud on load — never re-emits to
    'fix' it. Build, then corrupt the manifest version, then load must raise."""
    import json
    artifact.build_records_artifact(B57, "ru", "57", store=tmp_path)
    man = tmp_path / "57-ru" / artifact.MANIFEST_FILE
    d = json.loads(man.read_text())
    d["feature_schema_version"] = "phi-OLD"
    man.write_text(json.dumps(d))
    with pytest.raises(artifact.HashMismatch):
        artifact.load_records_artifact(B57, "ru", "57", store=tmp_path)
