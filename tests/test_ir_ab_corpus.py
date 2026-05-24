"""Corpus-wide A/B no-content-loss GATE — the IR migration safety oracle.

For EVERY book + poem with a source DOCX under ``legacy/``, this runs both the
LIVE typed-IR pipeline (``convert_single_docx``) and the legacy GFM engine kept
as the ORACLE (``convert_single_docx_gfm``) and asserts the HARD GATE: the IR
loses NO reading content vs the GFM oracle. This is local-only (it converts the
real corpus), so it is skipped when pandoc/pillow are absent — exactly like
``test_import_docx`` / ``test_golden_import``.

The measurement + report live in ``scripts/audit/ir_ab_corpus.py`` (also runnable
as a CLI: ``uv run scripts/audit/ir_ab_corpus.py --full``); this module is the
thin pytest binding that fails the suite if any work loses content. It is NOT a CI
gate (CI never imports). Post-6.2-cutover it guards the LIVE IR path against the
retained GFM oracle (deleted in Phase 7).
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
AUDIT = SCRIPTS / "audit"
for p in (str(SCRIPTS), str(AUDIT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import ir_ab_corpus as ab  # noqa: E402

pytestmark = pytest.mark.skipif(
    shutil.which("pandoc") is None or importlib.util.find_spec("PIL") is None,
    reason="pandoc and pillow are required (local-only corpus A/B)",
)


def test_ir_loses_no_content_corpus_wide() -> None:
    """No book or poem loses reading content under the IR pipeline vs the GFM
    engine. A non-zero ``lost_chars`` for any work is a BLOCKER — the IR dropped
    reading content the live path keeps. (Footnote-duplication artifacts GFM
    produces are classified as IR improvements and excluded from the gate.)"""
    results = ab.run_corpus()
    assert results, "no source DOCX found under legacy/ — the corpus A/B has nothing to test"

    errors = [r for r in results if r.error]
    assert not errors, "works failed to convert under one engine:\n" + "\n".join(
        f"  {r.work.rel}: {r.error}" for r in errors
    )

    losers = [r for r in results if r.lost_total > 0]
    assert not losers, (
        "IR content loss vs GFM (the hard gate must be 0):\n"
        + "\n".join(
            f"  {r.work.rel}: {r.lost_chars} reading char(s) dropped; "
            f"sample words: " + ", ".join(f"{w}×{n}" for w, n in r.lost.most_common(8))
            for r in losers
        )
    )


def test_ir_no_signature_epigraph_rtl_regression_corpus_wide() -> None:
    """STRUCTURAL PARITY (the gap that hid C1): the IR must lose no signature,
    epigraph, or rtl bidi span vs the GFM oracle. A net loss in any of these is the
    C1/I2 bug class and BLOCKS. (verse-block/answer-block deltas are the DEFERRED
    I4 — tracked by ``test_ir_verse_block_deltas_are_tracked`` below, not blocking.)"""
    results = ab.run_corpus()
    assert results, "no source DOCX found under legacy/ — the corpus A/B has nothing to test"
    regressors = [r for r in results if not r.error and r.struct_regressions]
    assert not regressors, (
        "IR structural regression vs GFM (signature/epigraph/rtl must not net-lose):\n"
        + "\n".join(
            f"  {r.work.rel}: "
            + ", ".join(
                f"{k} {r.gfm_struct.get(k, 0)}→{r.ir_struct.get(k, 0)} ({d:+d})"
                for k, d in r.struct_regressions.items()
            )
            for r in regressors
        )
    )


def test_ir_verse_block_deltas_are_tracked() -> None:
    """The DEFERRED I4: verse-block / answer-block deltas are computed and kept
    VISIBLE (this test surfaces them) but do NOT block — they belong to the later
    verse-detection TDD session. This asserts only that the tracking machinery is
    wired (every result carries the structural counts), printing any deltas for the
    record so I4 stays observable."""
    results = ab.run_corpus()
    tracked = [r for r in results if not r.error and r.tracked_deltas]
    # Always assert the counts are populated (the tracking is live), and print the
    # deltas so a `-s` run shows the I4 surface; never fail on them.
    for r in results:
        if not r.error:
            assert set(r.ir_struct) >= set(ab.STRUCT_KEYS)
    if tracked:
        print("\nI4 (deferred) verse/answer-block deltas — tracked, not blocking:")
        for r in tracked:
            print("  " + r.work.rel + ": " + ", ".join(
                f"{k} {r.gfm_struct.get(k, 0)}→{r.ir_struct.get(k, 0)} ({d:+d})"
                for k, d in r.tracked_deltas.items()
            ))


def test_corpus_enumeration_covers_books_and_poems() -> None:
    """The A/B enumeration finds both kinds (so the gate above is non-vacuous)."""
    works = ab.enumerate_corpus()
    kinds = {w.kind for w in works}
    assert "book" in kinds and "poem" in kinds, f"expected books and poems, got {kinds}"
