"""Canonical routed-kind -> URL-segment mapping (Python side).

This mirrors ``src/lib/kinds.ts``. Python cannot import the TS module and the
config can't import Python, so the mapping necessarily exists once per language.
``audit/python/kind_segments.py`` is the cross-language guard: it asserts this
dict equals the ``SEGMENT_OF`` map in ``src/lib/kinds.ts``.
"""

from __future__ import annotations

from typing import Literal, TypeGuard

type RoutedKind = Literal["book", "poem", "project", "video", "message"]
type CorpusWorkKind = Literal["book", "poem"]
type RoutedSegment = Literal["books", "poetry", "projects", "videos", "messages"]

# Routed content kind -> structural-noun URL segment. Includes `project` (themed
# sections under /projects/) and `video` (catalogued YouTube/other-platform
# videos under /videos/) and `message` (dated posts under /messages/, labelled
# «Послания» / Epistles in the UI) — all route and appear in the sitemap.
# Routing breadth != convertible-work scope. Every segment is an English noun;
# the displayed section label is localized separately.
SEGMENT_OF: dict[RoutedKind, RoutedSegment] = {
    "book": "books",
    "poem": "poetry",
    "project": "projects",
    "video": "videos",
    "message": "messages",
}

# The kinds that are convertible/downloadable corpus works — the source of truth
# for "which kinds the import/converter pipeline handles and which kinds get a
# download matrix". Projects and videos route but are not works (no DOCX-import,
# no PDF/EPUB matrix), so they are intentionally NOT here. This tuple is a
# subset of SEGMENT_OF's keys.
CORPUS_WORK_KINDS: tuple[CorpusWorkKind, ...] = ("book", "poem")

# URL segment -> routed kind (inverse of SEGMENT_OF).
KIND_OF_SEGMENT: dict[RoutedSegment, RoutedKind] = {
    segment: kind for kind, segment in SEGMENT_OF.items()
}


def is_routed_kind(value: str) -> TypeGuard[RoutedKind]:
    """Return whether an untrusted string is a routed content kind."""
    return value in SEGMENT_OF


def is_corpus_work_kind(value: str) -> TypeGuard[CorpusWorkKind]:
    """Return whether an untrusted string is an importable/downloadable work kind."""
    return value in CORPUS_WORK_KINDS


def is_routed_segment(value: str) -> TypeGuard[RoutedSegment]:
    """Return whether an untrusted string is a configured URL segment."""
    return value in KIND_OF_SEGMENT
