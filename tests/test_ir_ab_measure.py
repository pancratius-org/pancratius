"""Unit tests for the A/B oracle's content-loss measure (pure string logic).

The corpus gate's verdict hinges on these helpers; they have subtle rules
(emphasis markers glue, block markers separate, the character multiset is immune
to word-boundary artifacts the word multiset is sensitive to). These run without
pandoc — they only test the measurement functions in ``scripts/audit/ir_ab_corpus``.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "scripts" / "audit"
SCRIPTS = ROOT / "scripts"
for p in (str(SCRIPTS), str(AUDIT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import ir_ab_corpus as ab  # noqa: E402


def test_inline_emphasis_markers_do_not_fragment_words() -> None:
    # A stray Word bold on a word's first letter (`**р**ождённых`) is the SAME word
    # as `рождённых` once unwrapped — the word multiset must not see two tokens.
    assert ab.prose_tokens("**р**ождённых")["рождённых"] == 1
    assert "р" not in ab.prose_tokens("**р**ождённых")


def test_block_markers_are_separators() -> None:
    toks = ab.prose_tokens("# Heading\n| a | b |")
    assert toks["heading"] == 1 and toks["a"] == 1 and toks["b"] == 1


def test_footnote_markers_and_link_targets_dropped() -> None:
    toks = ab.prose_tokens("text[^1] and [label](http://x/y) more")
    assert toks["text"] == 1 and toks["label"] == 1 and toks["more"] == 1
    assert "http" not in toks and "1" not in toks  # markers/targets are not reading content


def test_reading_chars_immune_to_subscript_markup_choice() -> None:
    # HTML <sup> vs `^…^` are the SAME reading content; the char multiset agrees.
    html_form = ab.reading_chars("2<sup>x0</sup>")
    md_form = ab.reading_chars("2^x0^")
    assert html_form == md_form
    assert (html_form - md_form) == ab.Counter() and (md_form - html_form) == ab.Counter()


def test_line_similarity_ignores_trailing_whitespace() -> None:
    # Trailing whitespace (Pandoc leaves it on verse lines) is never structural.
    assert ab.line_similarity("a\nb\n", "a   \nb\t\n") == 1.0


# ---------------------------------------------------------------------------
# structural-parity (I3): the typed-structure counts the char multiset is blind to
# ---------------------------------------------------------------------------


def test_structural_counts_counts_each_marker() -> None:
    body = (
        '<p class="signature">x</p>\n'
        '<blockquote class="epigraph">y</blockquote>\n'
        '<div class="verse-block">v</div>\n'
        '<div class="answer-block">a</div>\n'
        '<span dir="rtl">ש</span> and <span dir="ltr">x</span>'
    )
    c = ab.structural_counts(body)
    assert c == {
        "signature": 1, "epigraph": 1, "verse-block": 1, "answer-block": 1, "rtl": 2,
    }


def _result(gfm: dict[str, int], ir: dict[str, int]) -> ab.WorkResult:
    work = ab.Work(docx=Path("legacy/books/ru/x.docx"), kind="book", lang="ru")
    return ab.WorkResult(
        work=work, gfm_lines=0, ir_lines=0, gfm_words=0, ir_words=0,
        gfm_struct={k: gfm.get(k, 0) for k in ab.STRUCT_KEYS},
        ir_struct={k: ir.get(k, 0) for k in ab.STRUCT_KEYS},
    )


def test_signature_net_loss_is_a_regression() -> None:
    r = _result({"signature": 2}, {"signature": 1})
    assert r.struct_regressions == {"signature": -1}
    assert not r.tracked_deltas


def test_rtl_net_loss_is_a_regression() -> None:
    r = _result({"rtl": 5}, {"rtl": 4})
    assert r.struct_regressions == {"rtl": -1}


def test_extra_signature_is_not_a_regression() -> None:
    # The IR emitting MORE signatures than GFM is not a loss (the gate is one-sided).
    r = _result({"signature": 1}, {"signature": 2})
    assert r.struct_regressions == {}


def test_verse_block_delta_is_tracked_not_regression() -> None:
    # verse/answer-block deltas (deferred I4) are reported, never blocking.
    r = _result({"verse-block": 25, "answer-block": 0}, {"verse-block": 26, "answer-block": 1})
    assert r.struct_regressions == {}  # not a regression class
    assert r.tracked_deltas == {"verse-block": 1, "answer-block": 1}
