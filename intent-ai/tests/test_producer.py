# research-pure: tests for the producer + views, on REAL corpus DOCX (read-only).
"""Proves the parity / no-leakage / identity / single-physics obligations against real
books. Uses small book 57 (ru) for speed and book 64 (ru+en) for bilingual parity.
read_lines is cached per-docx so the suite builds each view once."""
from __future__ import annotations

import dataclasses
from functools import lru_cache
from pathlib import Path

import pytest
from intent_ai import physics, producer, source_view
from intent_ai import records as record_model
from intent_ai.paths import BOOKS
from intent_ai.records import LineRecord

from pancratius import docx_source, docx_structure
from tests.record_factory import sample_records

B57 = BOOKS / "57-ya-otdayushchii" / "ru.docx"
B37 = BOOKS / "37-evangelie-ot-kolobka" / "ru.docx"   # multi-<w:br> body paras
B64_RU = BOOKS / "64-kniga-svyatogo-dukha" / "ru.docx"

@lru_cache(maxsize=8)
def records(docx: Path, lang: str, book: str) -> tuple[LineRecord, ...]:
    return tuple(producer.read_lines(docx, lang, book))


@pytest.fixture(scope="module")
def recs57():
    return records(B57, "ru", "57")


# --- identity on real data ---

@pytest.mark.corpus_source
def test_lineid_unique_within_book_real(recs57):
    ids = [r.id for r in recs57]
    assert len(ids) == len(set(ids)), "LineId collided on real book 57"


@pytest.mark.corpus_source
def test_votable_only_body(recs57):
    for r in recs57:
        if r.votable:
            assert r.role.is_body


# --- single physics source: record fill == per-LINE fill, NOT the per-paragraph recompute ---

@pytest.mark.corpus_source
def test_record_fill_is_per_line_not_per_paragraph():
    """The H2 double-compute bug: a per-paragraph fill is computed on the JOINED text. Our
    record reads it per source LINE. For a multi-line (<w:br>) paragraph these MUST differ;
    we assert our record matches the per-line value and is <= the joined-paragraph value."""
    source = docx_source.read(B37)
    geom = physics.page_geom(source.layout)
    observation = docx_structure.observe_structure(source, lang="ru")
    paras = source_view.read_view(observation)
    recs = records(B37, "ru", "37")
    by_key = {(r.id.src_ordinal, r.id.sub): r for r in recs}
    multis = [p for p in paras
              if p.role.is_body and len(p.lines) >= 2]
    assert multis, "need a multi-<w:br> body paragraph in book 37 to exercise the bug"
    checked = 0
    for p in multis[:20]:
        joined_fill = physics.wrap_stat(p.text, geom).fill  # the WRONG per-paragraph value
        for li, ln in enumerate(p.lines):
            rec = by_key.get((int(p.source.ordinal), li))
            if rec is None:
                continue
            assert abs(rec.features.fill - ln.fill) < 1e-9
            assert rec.features.fill <= joined_fill + 1e-9
            checked += 1
    assert checked >= 2


# --- parity: listing φ and vector φ are the SAME record (perturbation) ---

def test_parity_listing_and_vector_share_one_feature_object():
    body = sample_records()[0]
    base_vec = producer.to_vector(body.features)
    keys = {body.id: "L001"}
    base_listing = producer.render_listing([body], keys=keys,
                                           with_features=True)
    perturbed_feats = dataclasses.replace(body.features, fill=body.features.fill + 0.5,
                                          wraps=not body.features.wraps)
    perturbed = dataclasses.replace(body, features=perturbed_feats)
    pv = producer.to_vector(perturbed.features)
    pl = producer.render_listing([perturbed], keys=keys, with_features=True)
    assert pv["fill"] == pytest.approx(base_vec["fill"] + 0.5)
    assert pv["wraps"] != base_vec["wraps"]
    assert pl != base_listing
    assert f"fill={perturbed_feats.fill:.2f}" in pl
    assert ("WRAP" in pl) == perturbed_feats.wraps


def test_listing_feature_tokens_equal_vector_values():
    for r in sample_records():
        if not r.votable:
            continue
        vec = producer.to_vector(r.features)
        tokens = producer._feature_tokens(r.features)
        assert f"fill={vec['fill']:.2f}" in tokens
        ep_col = next(c for c in vec if c.startswith("end_punct="))
        assert f"end={ep_col.split('=', 1)[1]}" in tokens


# --- no leakage: φ has NO label/prediction input (structural + perturbation) ---

@pytest.mark.corpus_source
def test_features_deterministic_same_docx(recs57):
    again = tuple(producer.read_lines(B57, "ru", "57"))
    assert [r.features for r in again] == [r.features for r in recs57]
    assert [r.id for r in again] == [r.id for r in recs57]


def test_vector_columns_fixed_and_include_zero_support():
    cols = producer.vector_columns()
    assert len(cols) == len(set(cols))
    for r in sample_records():
        v = producer.vectorize_fixed(r.features)
        assert set(v.keys()) == set(cols)
    assert "align=center" in cols


def test_vector_projects_run_boundaries_without_duplicate_storage():
    middle = dataclasses.replace(
        sample_records()[0].features, run_len=3, run_pos=1
    )
    first = dataclasses.replace(middle, run_pos=0)
    last = dataclasses.replace(middle, run_pos=2)

    assert "prev_structural" not in middle.to_dict()
    assert "next_structural" not in middle.to_dict()
    assert producer.to_vector(middle)["prev_structural"] == 0.0
    assert producer.to_vector(middle)["next_structural"] == 0.0
    assert producer.to_vector(first)["prev_structural"] == 1.0
    assert producer.to_vector(last)["next_structural"] == 1.0


# --- golden snapshot (regression lock) on a known region ---

@pytest.mark.corpus_source
def test_golden_snapshot_book57_first_body_lines(recs57):
    """Regression lock: the producer's output on a known region is frozen; if the substrate or
    φ logic shifts these values, this fails."""
    body = [r for r in recs57 if r.votable][:5]
    snap = [(r.id.src_ordinal, r.id.sub, r.text[:40], round(r.features.fill, 3),
             r.features.wraps, r.features.end_punct.value) for r in body]
    # Compiler-dropped ToC paragraphs are context; the first candidate is real body content.
    expected = [
        (24, 0, "Панкратиус: Отец, сегодня 30 января 2026", 23.493, True, "sentence"),
        (26, 0, "Ты увидел, и это было Мной.", 0.322, False, "sentence"),
        (27, 0, "Ты различил четыре формы — и не ошибся, ", 0.867, False, "sentence"),
        (28, 0, "Но послушай:", 0.153, False, "colon"),
        (29, 0, "Это не лестница, по которой поднимаются.", 1.286, True, "sentence"),
    ]
    assert snap == expected


@pytest.mark.corpus_source
def test_golden_total_counts_book57(recs57):
    """Lock the gross shape, including substantive source rows absent from compiler output."""
    assert len(recs57) == 504
    assert sum(r.votable for r in recs57) == 459


@pytest.mark.corpus_source
def test_unmapped_book57_source_body_remains_visible_for_review(recs57):
    """Canonical source owns Q2 candidates even when shipping Q1 has no block claim."""
    recovered = [r for r in recs57 if 30 <= r.id.src_ordinal <= 39]
    assert [r.id.src_ordinal for r in recovered] == list(range(30, 40))
    assert all(r.role is record_model.Role.BODY_REVIEW for r in recovered)
    assert all(r.votable and r.requires_review and r.text for r in recovered)


def test_persisted_runs_match_producer_features():
    sample = sample_records(run_lengths=(3, 4))
    for run in record_model.runs(sample):
        members = [sample[index] for index in run]
        assert members[0].features.prev_structural
        assert members[-1].features.next_structural
        assert [record.features.run_pos for record in members] == list(range(len(run)))
        assert {record.features.run_len for record in members} == {len(run)}
