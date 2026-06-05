# research-pure: φ language-agnosticism — crafted ru/en pairs + a real en book read.
"""The φ helpers use punctuation / case / geometry only, so a ru line and an en line of the
SAME line-kind get identical φ semantics. Proven two ways: (1) crafted ru/en pairs through
the boundary helpers, (2) a real en.docx (book 64) reads into well-formed records with the
same schema as ru."""
from __future__ import annotations

from functools import lru_cache

import pytest
from lineation_core import producer
from lineation_core.paths import BOOKS
from lineation_core.records import EndPunct

B64_EN = BOOKS / "64-kniga-svyatogo-dukha" / "en.docx"


@pytest.mark.parametrize("ru,en,expect", [
    ("Он ушёл.", "He left.", EndPunct.SENTENCE),
    ("Это правда!", "This is true!", EndPunct.SENTENCE),
    ("Кто там?", "Who is there?", EndPunct.SENTENCE),
    ("Слова растворились…", "The words dissolved…", EndPunct.SENTENCE),
    ("Он сказал:", "He said:", EndPunct.COLON),
    ("и тогда,", "and then,", EndPunct.COMMA),
    ("впервые —", "for the first time —", EndPunct.DASH),
    ("письмо без адреса", "a letter with no address", EndPunct.NONE),
    ("«Я знал Тебя.»", "\"I knew You.\"", EndPunct.SENTENCE),
])
def test_end_punct_language_agnostic(ru, en, expect):
    assert producer._end_punct(ru) == expect
    assert producer._end_punct(en) == expect
    assert producer._end_punct(ru) == producer._end_punct(en)


@pytest.mark.parametrize("ru,en,lower", [
    ("почему сердце стучало", "why the heart was beating", True),
    ("Она села.", "She sat down.", False),
    ("«Большой текст»", "\"Big text\"", False),
    ("— и впервые", "— and for the first", True),
])
def test_starts_lower_language_agnostic(ru, en, lower):
    assert producer._starts_lower(ru) == lower
    assert producer._starts_lower(en) == lower
    assert producer._starts_lower(ru) == producer._starts_lower(en)


@lru_cache(maxsize=2)
def _en_records():
    return tuple(producer.read_lines(B64_EN, "en", "64"))


def test_en_book_reads_with_same_schema_as_ru():
    recs = _en_records()
    assert len(recs) > 1000
    ids = [r.id for r in recs]
    assert len(ids) == len(set(ids))
    for r in recs[:300]:
        assert r.id.lang == "en"
        v = producer.vectorize_fixed(r.features)
        assert set(v.keys()) == set(producer.vector_columns())


def test_ru_en_same_kind_line_identical_phi_semantics():
    """A crafted matched pair through the language-sensitive φ fields agrees. fill is
    font-metric and not expected identical across glyph widths, so it is excluded — that
    exclusion is the honest LUPI/geometry boundary."""
    ru, en = "Он сказал:", "He said:"
    assert producer._end_punct(ru) == producer._end_punct(en) == EndPunct.COLON
    assert producer._starts_lower(ru) == producer._starts_lower(en) is False
    import re
    w = re.compile(r"\w+", re.UNICODE)
    assert len(w.findall(ru)) == len(w.findall(en)) == 2
