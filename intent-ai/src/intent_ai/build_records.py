# research-pure: THE records build command — DOCX -> line_records cache. Re-runnable, not a consumer.
"""Rebuild the record cache (`_artifacts/<book>-<lang>/line_records.jsonl` + schema + manifest)
from the committed source DOCX. This is the ONE place records are (re)generated; consumers only
load. Records are DERIVED — deterministic from the DOCX — so re-running is safe and gitignored,
unlike the committed annotation truth in `annotations/`, which has no rebuilder.

Builds records for every book referenced by the committed annotations (labels ∪ panel votes ∪
contested), so the load side always finds the records its truth needs.
"""
from __future__ import annotations

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
    for bk in books:
        artifact.build_records_artifact(
            paths.book_docx(bk.book_id, bk.lang), bk.lang, bk.book_id, store=paths.ARTIFACT_STORE)
    return books


def _build_one(book_id: BookId, lang: str) -> tuple[BookId, str, int]:
    """Pool worker: build one (book, lang) artifact; returns its record count."""
    recs = artifact.build_records_artifact(
        paths.book_docx(book_id, lang), lang, book_id, store=paths.ARTIFACT_STORE)
    return book_id, lang, len(recs)


def build_corpus() -> list[tuple[BookId, str, int]]:
    """Rebuild the record cache for EVERY committed DOCX (both languages), in parallel —
    the substrate a corpus-wide scan (`recon`) loads. Idempotent like `build`."""
    from concurrent.futures import ProcessPoolExecutor, as_completed

    pairs = [(b, lang) for lang in ("ru", "en") for b in paths.corpus_books(lang)]
    out: list[tuple[BookId, str, int]] = []
    with ProcessPoolExecutor() as pool:
        futures = [pool.submit(_build_one, b, lang) for b, lang in pairs]
        for i, fut in enumerate(as_completed(futures), 1):
            book_id, lang, n = fut.result()
            out.append((book_id, lang, n))
            print(f"[{i}/{len(pairs)}] built {book_id}-{lang}: {n} records", flush=True)
    return sorted(out)


if __name__ == "__main__":
    import sys

    if "--corpus" in sys.argv:
        built_all = build_corpus()
        print(f"built records for {len(built_all)} (book, lang) pairs, "
              f"{sum(n for _, _, n in built_all)} records")
    else:
        built = build()
        print(f"built records for {len(built)} books: {built}")
