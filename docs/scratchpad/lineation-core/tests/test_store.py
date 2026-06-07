# research-pure: the I/O edge — annotation rows + record cache load through ONE boundary, fail-loud.
"""`store` is the only module that opens annotation/record files. These prove it reads the
committed truth and the record cache, and FAILS LOUD on a missing file rather than rebuilding."""
from __future__ import annotations

import pytest
from lineation_core import artifact, store


def test_annotation_rows_load_from_committed_store():
    labels = store.load_label_rows()
    votes = store.load_vote_rows()
    contested = store.load_eval_set("contested")
    assert labels and votes and contested              # the committed truth is present
    assert all("id" in r and "label" in r for r in labels)
    assert all("id" in r and "tag" in r and "label" in r for r in votes)
    assert all("id" in r and "label" in r for r in contested)   # {id, label} eval rows


def test_annotation_load_fails_loud_on_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        store.load_label_rows(annotations=tmp_path)    # empty dir -> loud, never a rebuild


def test_records_load_through_the_edge():
    recs = store.load_records("57")                    # from the real cache, hash-validated
    assert recs and all(r.id.book_id == "57" for r in recs)


def test_records_load_fails_loud_on_missing_cache(tmp_path):
    with pytest.raises((FileNotFoundError, artifact.HashMismatch)):
        store.load_records("57", store=tmp_path)        # empty cache -> loud, no re-emit
