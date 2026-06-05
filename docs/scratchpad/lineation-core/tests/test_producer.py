# research-pure: tests for the producer + views, on REAL corpus DOCX (read-only).
"""Proves the parity / no-leakage / identity / single-physics obligations against real
books. Uses small book 57 (ru) for speed and book 64 (ru+en) for bilingual parity.
read_lines is cached per-docx so the suite builds each view once."""
from __future__ import annotations

import dataclasses
import re
from functools import lru_cache
from pathlib import Path

import pytest
from lineation_core import physics, producer, source_view
from lineation_core.paths import BOOKS
from lineation_core.records import LineRecord, Role, SourceFate, feature_field_names

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

def test_lineid_unique_within_book_real(recs57):
    ids = [r.id for r in recs57]
    assert len(ids) == len(set(ids)), "LineId collided on real book 57"


def test_votable_only_body_and_mapped(recs57):
    for r in recs57:
        if r.votable:
            assert r.role == Role.BODY
            assert r.source_fate in (SourceFate.NORMAL, SourceFate.MIXED)
            assert r.id.is_mapped


def test_unmapped_lines_flagged_not_silently_body(recs57):
    for r in recs57:
        if not r.id.is_mapped:
            assert r.source_fate == SourceFate.UNMAPPED
            assert not r.votable
            assert r.meta.src_ordinal is None


# --- single physics source: record fill == per-LINE fill, NOT the per-paragraph recompute ---

def test_record_fill_is_per_line_not_per_paragraph():
    """The H2 double-compute bug: a per-paragraph fill is computed on the JOINED text. Our
    record reads it per source LINE. For a multi-line (<w:br>) paragraph these MUST differ;
    we assert our record matches the per-line value and is <= the joined-paragraph value."""
    paras = source_view.read_view(B37)
    geom = physics.page_geom(B37)
    recs = records(B37, "ru", "37")
    by_key = {(r.meta.src_ordinal, r.id.sub): r for r in recs if r.meta.src_ordinal is not None}
    multis = [p for p in paras
              if p.role == source_view.Role.BODY and len(p.lines) >= 2 and p.src_start is not None]
    assert multis, "need a multi-<w:br> body paragraph in book 37 to exercise the bug"
    checked = 0
    for p in multis[:20]:
        joined_fill = physics.wrap_stat(p.text, geom).fill  # the WRONG per-paragraph value
        for li, ln in enumerate(p.lines):
            rec = by_key.get((p.src_start, li))
            if rec is None:
                continue
            assert abs(rec.features.fill - ln.fill) < 1e-9
            assert rec.features.fill <= joined_fill + 1e-9
            checked += 1
    assert checked >= 2


# --- parity: listing φ and vector φ are the SAME record (perturbation) ---

def test_parity_listing_and_vector_share_one_feature_object(recs57):
    body = next(r for r in recs57 if r.votable)
    base_vec = producer.to_vector(body.features)
    base_listing = producer.render_listing([body], with_features=True)
    perturbed_feats = dataclasses.replace(body.features, fill=body.features.fill + 0.5,
                                          wraps=not body.features.wraps)
    perturbed = dataclasses.replace(body, features=perturbed_feats)
    pv = producer.to_vector(perturbed.features)
    pl = producer.render_listing([perturbed], with_features=True)
    assert pv["fill"] == pytest.approx(base_vec["fill"] + 0.5)
    assert pv["wraps"] != base_vec["wraps"]
    assert pl != base_listing
    assert f"fill={perturbed_feats.fill:.2f}" in pl
    assert ("WRAP" in pl) == perturbed_feats.wraps


def test_listing_feature_tokens_equal_vector_values(recs57):
    for r in list(recs57)[:200]:
        if r.role != Role.BODY:
            continue
        vec = producer.to_vector(r.features)
        tokens = producer._feature_tokens(r.features)
        assert f"fill={vec['fill']:.2f}" in tokens
        ep_col = next(c for c in vec if c.startswith("end_punct="))
        assert f"end={ep_col.split('=', 1)[1]}" in tokens


# --- no leakage: φ has NO label/prediction input (structural + perturbation) ---

def test_read_lines_signature_has_no_label_input():
    sig = set(producer.read_lines.__code__.co_varnames[:producer.read_lines.__code__.co_argcount])
    assert sig == {"docx", "lang", "book_id"}
    for name in feature_field_names():
        assert not re.search(r"label|gold|predict|class", name)


def test_features_deterministic_same_docx(recs57):
    again = tuple(producer.read_lines(B57, "ru", "57"))
    assert [r.features for r in again] == [r.features for r in recs57]
    assert [r.id for r in again] == [r.id for r in recs57]


def test_vector_columns_fixed_and_include_zero_support(recs57):
    cols = producer.vector_columns()
    assert len(cols) == len(set(cols))
    for r in list(recs57)[:50]:
        v = producer.vectorize_fixed(r.features)
        assert set(v.keys()) == set(cols)
    assert "align=center" in cols


# --- golden snapshot (regression lock) on a known region ---

def test_golden_snapshot_book57_first_body_lines(recs57):
    """Regression lock: the producer's output on a known region is frozen. Captured from a
    verified read on 2026-06; if the substrate or φ logic shifts these values, this fails."""
    body = [r for r in recs57 if r.role == Role.BODY][:5]
    snap = [(r.id.src_ordinal, r.id.sub, r.text, round(r.features.fill, 3),
             r.features.wraps, r.features.end_punct.value) for r in body]
    expected = [
        (1, 0, "Рождение книги 2", 0.198, False, "none"),
        (2, 0, "КНИГА ОТДАЧИ 7", 0.219, False, "none"),
        (3, 0, "(Начало) 7", 0.116, False, "none"),
        (4, 0, "Глава 1. Не десятину 8", 0.249, False, "none"),
        (5, 0, "Глава 2. Всё 9", 0.153, False, "none"),
    ]
    assert snap == expected


def test_golden_total_counts_book57(recs57):
    """Lock the gross shape too — count drift is the cheapest regression signal."""
    from collections import Counter
    assert len(recs57) == 560
    assert sum(r.votable for r in recs57) == 469
    fates = Counter(r.source_fate.value for r in recs57)
    assert fates == {"normal": 476, "mixed": 24, "unmapped": 60}
    assert all(not r.votable for r in recs57 if r.source_fate == SourceFate.UNMAPPED)
    assert sum(r.votable for r in recs57 if r.source_fate == SourceFate.MIXED) == 24
