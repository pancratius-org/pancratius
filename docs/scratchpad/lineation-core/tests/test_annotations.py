# research-pure: proves the loaded per-line labels are LineId-keyed, trainable, lineage-kept.
"""`annotations.load_labels()` reads the committed `labels.jsonl` truth and is the package's ONLY
truth path — no migration, no source-shard read. It REJECTS unmapped-line labels at the boundary
(surfaced count)."""
from __future__ import annotations

import pytest
from lineation_core import identity, paths, producer
from lineation_core.annotations import LabelSource, load_labels


@pytest.fixture(scope="module")
def labelset():
    return load_labels()


# --- the loaded set is the trainable truth, unmapped rejected ---

def test_locked_trainable_count(labelset):
    """620 trainable mapped labels; the 2 unmapped-line labels are REJECTED at the boundary and
    surfaced, not silently dropped."""
    assert len(labelset.labels) == 620
    assert labelset.n_rejected_unmapped == 2


def test_class_balance_locked(labelset):
    from collections import Counter
    assert dict(Counter(g.label for g in labelset.labels)) == {"lineated": 530, "prose": 90}


def test_every_loaded_label_is_mapped(labelset):
    """The unmapped band is rejected at load, so every trainable label has a real src_ordinal."""
    assert all(g.id.is_mapped for g in labelset.labels)


def test_loaded_labels_are_two_class(labelset):
    assert set(g.label for g in labelset.labels) <= {"prose", "lineated"}


# --- identity of the loaded keys ---

def test_loaded_ids_unique(labelset):
    ids = [g.id for g in labelset.labels]
    assert len(ids) == len(set(ids))


def test_all_labels_are_ru(labelset):
    assert all(g.id.lang == "ru" for g in labelset.labels)


def test_lineage_preserved_with_provenance(labelset):
    for g in labelset.labels:
        assert {"rid", "idx", "sub", "shard"} <= set(g.provenance.keys())
        assert g.source == LabelSource.HUMAN
        assert g.line_text_hash is not None


def test_idx_to_src_mapping_is_real_for_every_g05_line(labelset):
    """Each loaded label's text hash matches its record, and the lineage idx is the record's
    block_index (the join the one-shot migration did, re-validated here on read). g05_b37 is a
    hardbreak region (a human noted an IR render bug there) — exactly where a mis-join would be
    silent and dangerous. We verify the FULL set, reading the labels FROM the loaded artifact."""
    g05 = [g for g in labelset.labels if g.provenance.get("rid") == "g05_b37"]
    assert len(g05) >= 5, "expected the g05_b37 hardbreak labels"
    recs = {r.id: r for r in producer.read_lines(paths.book_docx("37"), "ru", "37")}
    for g in g05:
        rec = recs[g.id]
        assert identity.text_hash(rec.text) == g.line_text_hash
        assert rec.meta.block_index == g.provenance["idx"]
        assert rec.id.sub == g.provenance["sub"]
        assert g.label in ("prose", "lineated")
