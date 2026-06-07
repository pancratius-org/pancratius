# research-pure: THE records build command — DOCX -> line_records cache. Re-runnable, not a consumer.
"""Rebuild the record cache (`_artifacts/<book>-<lang>/line_records.jsonl` + schema + manifest)
from the committed source DOCX. This is the ONE place records are (re)generated; consumers only
load. Records are DERIVED — deterministic from the DOCX — so re-running is safe and gitignored,
unlike the committed annotation truth in `annotations/`, which has no rebuilder.

Builds records for every book referenced by the committed annotations (labels ∪ panel votes ∪
contested), so the load side always finds the records its truth needs.
"""
from __future__ import annotations

from . import artifact, labels, panel_votes, paths
from .evaluation.contested import load_contested
from .identity import BookId


def annotation_books() -> list[BookId]:
    """The books any committed annotation refers to — the set whose records must exist."""
    books: set[BookId] = {g.id.book_id for g in labels.load().labels}
    books |= {v.id.book_id for v in panel_votes.load()}
    books |= {lid.book_id for lid in load_contested()}
    return sorted(books)


def build(*, lang: str = "ru") -> list[BookId]:
    books = annotation_books()
    for book_id in books:
        artifact.build_records_artifact(
            paths.book_docx(book_id, lang), lang, book_id, store=paths.ARTIFACT_STORE)
    return books


if __name__ == "__main__":
    built = build()
    print(f"built records for {len(built)} books: {built}")
