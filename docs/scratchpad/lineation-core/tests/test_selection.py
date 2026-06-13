"""`selection.py`: the acquisition queue — ranking + the no-leakage invariants (audit is labeled
disagreements scored out-of-fold; acquire is unlabeled, least-confident)."""
from __future__ import annotations

from collections import Counter
from types import SimpleNamespace

from lineation_core import selection, store, student
from lineation_core.identity import BookKey, LineId


def _qitem(book: str, margin: float) -> selection.QueueItem:
    return selection.QueueItem(
        id=SimpleNamespace(book_id=book, book_key=book), text="", posterior=0.5, margin=margin,
        kind="acquire", existing_label=None, student_label="prose", context=())


def test_cap_per_book_bounds_each_book_and_keeps_order():
    items = [_qitem("A", 0.01), _qitem("A", 0.02), _qitem("A", 0.03),
             _qitem("B", 0.04), _qitem("C", 0.05)]                 # already least-confident first
    out = selection._cap_per_book(items, top=4, per_book_cap=2)
    per = Counter(it.id.book_id for it in out)
    assert per["A"] == 2 and len(out) == 4                        # A capped at 2; filled from B, C
    assert [it.margin for it in out] == sorted(it.margin for it in out)   # input order preserved
    assert selection._cap_per_book(items, top=4, per_book_cap=None) == items[:4]   # None = plain head


def _stub(src_ordinal: int, sub: int, text: str):
    return SimpleNamespace(id=SimpleNamespace(src_ordinal=src_ordinal, sub=sub), text=text)


def test_label_boundary():
    assert selection._label(0.5) == "lineated"
    assert selection._label(0.49) == "prose"
    assert selection._label(0.99) == "lineated"


def test_context_marks_the_line_and_respects_radius():
    body = [_stub(10 + i, 0, f"line{i}") for i in range(6)]
    ctx = selection._context(body, 3, radius=2)
    assert len(ctx) == 5                      # 1..5
    assert ctx[2].startswith("-> ")           # the focal line is marked
    assert all(not c.startswith("-> ") for i, c in enumerate(ctx) if i != 2)
    assert "line3" in ctx[2]
    edge = selection._context(body, 0, radius=2)  # clamps at the start
    assert len(edge) == 3 and edge[0].startswith("-> ")


def test_queue_invariants_on_real_data(corpus):
    records, labelset = corpus
    label_ids = {g.id for g in labelset.labels}
    q = selection.build_queue(records, labelset, top_acquire=25,
                              books=[BookKey("ru", "37"), BookKey("ru", "16")])

    # AUDIT: every item is a LABELED line whose student (out-of-fold) call disagrees with truth.
    for it in q.audit:
        assert it.id in label_ids
        assert it.existing_label is not None
        assert it.student_label != it.existing_label
        assert abs(it.posterior - 0.5) == it.margin
    # most-confident disagreement first
    assert [x.margin for x in q.audit] == sorted((x.margin for x in q.audit), reverse=True)

    # ACQUIRE: every item is UNLABELED, least-confident first, capped.
    for it in q.acquire:
        assert it.id not in label_ids
        assert it.existing_label is None
    assert [x.margin for x in q.acquire] == sorted(x.margin for x in q.acquire)
    assert len(q.acquire) <= 25
    assert 0 <= q.prose_leaning_acquire <= len(q.acquire)
    assert q.n_votable > 0


def test_fit_full_posteriors_batch_matches_single(corpus):
    records, labelset = corpus
    ds = student.build_dataset(records, labelset)
    model = student.fit_full(ds)
    recs = store.load_records("37")
    feats = [r.features for r in recs if r.votable][:20]
    batch = model.posteriors(feats)
    singles = [model.posterior(f) for f in feats]
    assert all(abs(a - b) < 1e-9 for a, b in zip(batch, singles, strict=True))


def _restrict(ds: student.Dataset, books: list[str], *, flip: str | None = None) -> student.Dataset:
    """A `Dataset` over only `books`; if `flip` is given, every label of that book is inverted —
    so a leakage test can fit on the others and prove the flipped book's held-out predictions
    don't move."""
    keep = [i for i, g in enumerate(ds.groups) if g in books]
    def y(i):
        lab = ds.y[i]
        if flip and ds.groups[i] == flip:
            return "prose" if lab == "lineated" else "lineated"
        return lab
    return student.Dataset(
        X=[ds.X[i] for i in keep], y=[y(i) for i in keep], groups=[ds.groups[i] for i in keep],
        columns=ds.columns, feature_support=ds.feature_support, ids=[ds.ids[i] for i in keep],
        n_joined=len(keep), n_skipped_unmapped=0,
    )


def test_oof_smoothed_no_leakage(corpus):
    """The book-held-out smoothed prediction of a line must NOT depend on that line's own book's
    labels (the model that judges book B was fit on the other books). Flip ALL of one book's
    labels and assert its own predictions are byte-identical."""
    records, labelset = corpus
    ds = student.build_dataset(records, labelset)
    books = sorted(set(ds.groups))[:3]              # a small, fast slice
    target = books[0]
    before = student.oof_smoothed(_restrict(ds, books), records, alpha=0.75)
    after = student.oof_smoothed(_restrict(ds, books, flip=target), records, alpha=0.75)
    target_ids = [lid for lid in before if lid.book_key == target]
    assert target_ids, "expected predictions for the target book"
    for lid in target_ids:                          # target book never entered its own fit
        assert before[lid].label == after[lid].label, f"leakage: {lid} moved when its book flipped"
    # sanity: flipping DID change the OTHER books' predictions (target was in their training fit)
    assert any(before[lid].label != after[lid].label
               for lid in before if lid.book_key != target)


# --- the random instrument sampler (pure halves) ------------------------------------------------


def _ids(book: str, n: int, *, lang: str = "ru") -> list[LineId]:
    return [LineId(lang, book, 10 + i, 0) for i in range(n)]


def test_stratified_sample_is_proportional_exact_and_deterministic():
    strata = {"ru:01:prose": _ids("01", 600), "ru:02:lineated": _ids("02", 300),
              "en:75:lineated": _ids("75", 100, lang="en")}
    out = selection.stratified_sample(strata, 100, seed=0)
    assert {k: len(v) for k, v in out.items()} == {
        "ru:01:prose": 60, "ru:02:lineated": 30, "en:75:lineated": 10}
    flat = [lid for v in out.values() for lid in v]
    assert len(set(flat)) == 100
    for k, lines in out.items():
        assert set(lines) <= set(strata[k])
    assert selection.stratified_sample(strata, 100, seed=0) == out      # deterministic
    assert selection.stratified_sample(strata, 100, seed=1) != out      # seed matters


def test_stratified_sample_largest_remainder_sums_exactly():
    strata = {"a": _ids("01", 11), "b": _ids("02", 7), "c": _ids("03", 5)}
    out = selection.stratified_sample(strata, 10, seed=0)
    assert sum(len(v) for v in out.values()) == 10
    assert all(len(out.get(k, [])) <= len(v) for k, v in strata.items())


def test_split_halves_balanced_within_every_stratum_and_globally():
    # many odd strata: the surplus must not pile onto one half
    strata = {f"ru:{i:02d}:prose": _ids(f"{i:02d}", 3) for i in range(1, 41)}
    sampled = selection.stratified_sample(strata, 120, seed=0)
    frozen, working = selection.split_halves(sampled, seed=0)
    assert not set(frozen) & set(working)
    assert sorted([*frozen, *working]) == sorted(lid for v in sampled.values() for lid in v)
    for k, lines in sampled.items():
        nf = sum(1 for lid in frozen if lid in set(lines))
        assert abs(nf - (len(lines) - nf)) <= 1, k
    assert abs(len(frozen) - len(working)) <= 1


def test_stratified_sample_gives_every_cell_nonzero_inclusion_probability():
    # 1 leftover; the tiny cell's fraction is small but must win it under SOME seed —
    # a deterministic largest-remainder would exclude it with certainty
    strata = {"big": _ids("01", 199), "tiny": _ids("02", 4)}
    n = 29  # big: 28.43..., tiny: 0.57...
    tiny_hits = sum(bool(selection.stratified_sample(strata, n, seed=s).get("tiny"))
                    for s in range(40))
    assert 0 < tiny_hits < 40
