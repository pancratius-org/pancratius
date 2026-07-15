# research-pure: THE records build command — DOCX -> line_records cache. Re-runnable, not a consumer.
"""Rebuild the record cache (`_artifacts/<book>-<lang>/line_records.jsonl` + schema + manifest)
from the committed source DOCX. This is the ONE place records are (re)generated; consumers only
load. Records are DERIVED — deterministic from the DOCX — so re-running is safe and gitignored,
unlike the committed annotation truth in `annotations/`, which has no rebuilder.

Builds records for every book referenced by the committed annotations (labels ∪ panel votes ∪
contested), so the load side always finds the records its truth needs.
"""
from __future__ import annotations

from pancratius.locales import LOCALES, Locale

from . import artifact, paths
from .annotations import load_labels, load_votes
from .evaluation.contested import load_contested
from .identity import BookId, BookKey


def annotation_books() -> list[BookKey]:
    """The book EDITIONS any committed annotation refers to — the set whose records must exist.
    Keyed by `BookKey`, not bare `book_id`: the truth is bilingual (ru:NN and en:NN are different
    books), so a lang-stripped set would silently skip one language's editions on rebuild."""
    books: set[BookKey] = {g.id.book_key for g in load_labels().labels}
    books |= {v.id.book_key for v in load_votes()}
    books |= {lid.book_key for lid in load_contested()}
    return sorted(books)


def build() -> list[BookKey]:
    """Rebuild every annotated edition in its OWN language — bilingual by construction."""
    books = annotation_books()
    _build_pairs([(book.book_id, book.lang) for book in books])
    return books


def _build_one(book_id: BookId, lang: Locale) -> tuple[BookId, Locale, int]:
    """Pool worker: build one (book, lang) artifact; returns its record count."""
    recs = artifact.build_records_artifact(
        paths.book_docx(book_id, lang), lang, book_id, store=paths.ARTIFACT_STORE)
    return book_id, lang, len(recs)


def _build_pairs(
    pairs: list[tuple[BookId, Locale]],
) -> list[tuple[BookId, Locale, int]]:
    """Build independent editions concurrently; each worker owns one artifact directory."""
    from concurrent.futures import ProcessPoolExecutor, as_completed

    out: list[tuple[BookId, Locale, int]] = []
    with ProcessPoolExecutor() as pool:
        futures = [pool.submit(_build_one, book_id, lang) for book_id, lang in pairs]
        for index, future in enumerate(as_completed(futures), 1):
            book_id, lang, count = future.result()
            out.append((book_id, lang, count))
            print(f"[{index}/{len(pairs)}] built {book_id}-{lang}: {count} records", flush=True)
    return sorted(out)


def build_corpus() -> list[tuple[BookId, Locale, int]]:
    """Rebuild the record cache for EVERY committed DOCX (both languages), in parallel —
    the substrate a corpus-wide scan (`recon`) loads. Idempotent like `build`."""
    pairs = [(b, lang) for lang in LOCALES for b in paths.corpus_books(lang)]
    return _build_pairs(pairs)


if __name__ == "__main__":
    import sys

    if "--corpus" in sys.argv:
        built_all = build_corpus()
        print(f"built records for {len(built_all)} (book, lang) pairs, "
              f"{sum(n for _, _, n in built_all)} records")
    else:
        built = build()
        print(f"built records for {len(built)} books: {built}")
