# research-pure: proves the loaded per-line labels are LineId-keyed, trainable, lineage-kept.
"""`annotations.load_labels()` reads committed canonical truth through the package's only path."""
from __future__ import annotations

import pytest
from intent_ai import identity, paths, producer
from intent_ai.annotations import LabelSource, load_labels


@pytest.fixture(scope="module")
def labelset():
    return load_labels()


# --- the loaded set is canonical truth ---

def _migration(labelset):
    """The irreplaceable human migration cohort — the labels these locks guard, identified by its
    legacy lineage (`rid` provenance), NOT by `source=human`: live adjudication keeps minting new
    human-source labels (task-keyed provenance), and the E1 gate cohort (`source=gate`, bilingual,
    machine-promoted) coexists and grows. Each newer cohort has its own invariants (holdout split,
    leak test, task lineage) and must not be folded into the migration-cohort locks."""
    return [g for g in labelset.labels
            if g.source == LabelSource.HUMAN and "rid" in g.provenance]


def _adjudication(labelset):
    """The live human-adjudication cohort: human-source labels minted by the studio loop
    (`ingest`), carrying task lineage instead of legacy `rid` lineage. Grows with every
    adjudicated queue — invariants, never count locks."""
    return [g for g in labelset.labels
            if g.source == LabelSource.HUMAN and "rid" not in g.provenance]


def test_locked_human_label_counts(labelset):
    """The migration cohort includes the two legacy span-dropped labels now resolved onto
    canonical source lines. Stable regardless of later gate or live-adjudication growth."""
    human = _migration(labelset)
    assert len(human) == 704
    assert sum(not g.holdout for g in human) == 622
    assert sum(g.holdout for g in human) == 82


def test_human_class_balance_locked(labelset):
    """The 21 once-conflicted labels are re-adjudicated on the FIXED render (f80ff63) — the
    recency resolution had kept verdicts made while the old prose render mangled multi-line
    content into one paragraph. 17 flipped back to prose (12 trainable + 5 holdout); 4 confirmed
    (b41:2247 stays lineated on the human's bug-independent section-convention tiebreak)."""
    from collections import Counter
    human = _migration(labelset)
    trainable = [g for g in human if not g.holdout]
    assert dict(Counter(g.label for g in trainable)) == {"lineated": 530, "prose": 92}
    assert dict(Counter(g.label for g in human)) == {"lineated": 590, "prose": 114}


def test_loaded_labels_are_two_class(labelset):
    assert set(g.label for g in labelset.labels) <= {"prose", "lineated"}


# --- identity of the loaded keys ---

def test_loaded_ids_unique(labelset):
    ids = [g.id for g in labelset.labels]
    assert len(ids) == len(set(ids))


def test_migration_cohort_is_ru_only(labelset):
    """The human migration cohort is ru-only (the original study labeled the ru corpus). EN truth
    arrives later as gate/adjudication labels, NOT in this cohort."""
    assert all(g.id.lang == "ru" for g in _migration(labelset))


def test_migration_lineage_preserved_with_provenance(labelset):
    """Every migration-cohort label keeps its lineage: the original migration cohort carries the
    legacy shard key (rid/idx/sub/shard); the homed contested-only cohort points at the legacy
    human adjudication export that produced it (adjudication/rid/key). Gate labels carry their own
    `anchor`/`task`/`votes` provenance, checked where the gate is tested."""
    for g in _migration(labelset):
        if g.holdout:
            assert {"adjudication", "rid", "key"} <= set(g.provenance.keys())
        else:
            assert {"rid", "idx", "sub", "shard"} <= set(g.provenance.keys())
        assert g.line_text_hash is not None


def test_every_legacy_adjudication_source_is_preserved(labelset):
    """The retired classifier keeps only raw files still named by active truth provenance."""
    referenced = {
        source
        for label in labelset.labels
        if (source := label.provenance.get("adjudication")) is not None
    }
    archive = paths.ANNOTATIONS / "history" / "legacy-classifier" / "raw"
    preserved = {path.name for path in archive.iterdir() if path.is_file()}
    assert preserved == referenced


def test_adjudication_cohort_carries_task_lineage(labelset):
    """Every live-adjudication label traces to the studio task that produced it: the task-local
    key, the region, and the task title — the manifest is the committed resolver, so this lineage
    makes each verdict replayable to its exact rendered context. Hash-railed like everything."""
    adj = _adjudication(labelset)
    assert adj, "the E1 adjudication cohort exists (en ingested 2026-06-12)"
    for g in adj:
        assert {"task_key", "item_id", "task"} <= set(g.provenance.keys())
        assert g.line_text_hash is not None
        assert g.audit_status == "adjudicated"


@pytest.mark.corpus_source
def test_every_g05_label_matches_canonical_source_text(labelset):
    """g05_b37 is a hard-break region where the old IR reader once misjoined lines. Every active
    label must hash-match the canonical source record; the migration ledger separately proves
    how legacy identities reached these keys."""
    g05 = [g for g in labelset.trainable if g.provenance.get("rid") == "g05_b37"]
    assert len(g05) >= 5, "expected the g05_b37 hardbreak labels"
    recs = {r.id: r for r in producer.read_lines(paths.book_docx("37"), "ru", "37")}
    for g in g05:
        rec = recs[g.id]
        assert identity.text_hash(rec.text) == g.line_text_hash
        assert rec.id.sub == g.provenance["sub"]
        assert g.label in ("prose", "lineated")


def test_frozen_instrument_labels_are_always_holdout():
    """The e1 frozen acceptance half is eval-only BY CONSTRUCTION: any label that ever lands on a
    member must carry `holdout=True` (route/ingest stamp it from the recipe's `holdout_eval_set`).
    A non-holdout member label means the acceptance set leaked into training — fail loud."""
    from intent_ai import store
    from intent_ai.identity import LineId

    frozen = {LineId.from_key(k) for k in store.load_eval_set("e1-v2-frozen")}
    leaked = [g.id for g in load_labels().labels if g.id in frozen and not g.holdout]
    assert not leaked, f"frozen-instrument labels leaked into training: {leaked[:5]}"
