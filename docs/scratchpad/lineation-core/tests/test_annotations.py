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

def _human(labelset):
    """The irreplaceable human migration cohort — the labels these locks guard. The E1 gate
    cohort (`source=gate`, bilingual, machine-promoted) coexists and grows; it has its own
    invariants (holdout split, leak test) and must not be folded into the human-cohort locks."""
    return [g for g in labelset.labels if g.source == LabelSource.HUMAN]


def test_locked_human_label_counts(labelset):
    """The human cohort = 702 mapped labels = 620 trainable + 82 holdout (the homed contested-only
    human adjudications — eval-only truth); the 2 unmapped-line labels are REJECTED at the boundary
    and surfaced, not silently dropped. Stable regardless of how many gate labels E1+ adds."""
    human = _human(labelset)
    assert len(human) == 702
    assert sum(not g.holdout for g in human) == 620
    assert sum(g.holdout for g in human) == 82
    assert labelset.n_rejected_unmapped == 2


def test_human_class_balance_locked(labelset):
    """The 21 once-conflicted labels are re-adjudicated on the FIXED render (f80ff63) — the
    recency resolution had kept verdicts made while the old prose render mangled multi-line
    content into one paragraph. 17 flipped back to prose (12 trainable + 5 holdout); 4 confirmed
    (b41:2247 stays lineated on the human's bug-independent section-convention tiebreak)."""
    from collections import Counter
    human = _human(labelset)
    trainable = [g for g in human if not g.holdout]
    assert dict(Counter(g.label for g in trainable)) == {"lineated": 528, "prose": 92}
    assert dict(Counter(g.label for g in human)) == {"lineated": 588, "prose": 114}


def test_every_loaded_label_is_mapped(labelset):
    """The unmapped band is rejected at load, so every trainable label has a real src_ordinal."""
    assert all(g.id.is_mapped for g in labelset.labels)


def test_loaded_labels_are_two_class(labelset):
    assert set(g.label for g in labelset.labels) <= {"prose", "lineated"}


# --- identity of the loaded keys ---

def test_loaded_ids_unique(labelset):
    ids = [g.id for g in labelset.labels]
    assert len(ids) == len(set(ids))


def test_human_cohort_is_ru_only(labelset):
    """The human migration cohort is ru-only (the original study labeled the ru corpus). EN truth
    arrives later as gate/transfer labels, NOT in this cohort."""
    assert all(g.id.lang == "ru" for g in _human(labelset))


def test_human_lineage_preserved_with_provenance(labelset):
    """Every HUMAN-cohort label keeps its lineage: the original migration cohort carries the legacy
    shard key (rid/idx/sub/shard); the homed contested-only cohort points at the legacy human
    adjudication export that produced it (adjudication/rid/key). Gate labels carry their own
    `anchor`/`task`/`votes` provenance, checked where the gate is tested."""
    for g in _human(labelset):
        if g.holdout:
            assert {"adjudication", "rid", "key"} <= set(g.provenance.keys())
        else:
            assert {"rid", "idx", "sub", "shard"} <= set(g.provenance.keys())
        assert g.line_text_hash is not None


def test_idx_to_src_mapping_is_real_for_every_g05_line(labelset):
    """Each loaded label's text hash matches its record, and the lineage idx is the record's
    block_index (the join the one-shot migration did, re-validated here on read). g05_b37 is a
    hardbreak region (a human noted an IR render bug there) — exactly where a mis-join would be
    silent and dangerous. We verify the FULL set, reading the labels FROM the loaded artifact."""
    g05 = [g for g in labelset.trainable if g.provenance.get("rid") == "g05_b37"]
    assert len(g05) >= 5, "expected the g05_b37 hardbreak labels"
    recs = {r.id: r for r in producer.read_lines(paths.book_docx("37"), "ru", "37")}
    for g in g05:
        rec = recs[g.id]
        assert identity.text_hash(rec.text) == g.line_text_hash
        assert rec.meta.block_index == g.provenance["idx"]
        assert rec.id.sub == g.provenance["sub"]
        assert g.label in ("prose", "lineated")


def test_frozen_instrument_labels_are_always_holdout():
    """The e1 frozen acceptance half is eval-only BY CONSTRUCTION: any label that ever lands on a
    member must carry `holdout=True` (route/ingest stamp it from the recipe's `holdout_eval_set`).
    A non-holdout member label means the acceptance set leaked into training — fail loud."""
    from lineation_core import store
    from lineation_core.identity import LineId

    frozen = {LineId.from_key(k) for k in store.load_eval_set("e1-instrument-frozen")}
    leaked = [g.id for g in load_labels().labels if g.id in frozen and not g.holdout]
    assert not leaked, f"frozen-instrument labels leaked into training: {leaked[:5]}"
