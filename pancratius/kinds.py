"""Canonical routed-kind -> URL-segment mapping (Python side).

This mirrors ``src/lib/kinds.ts``. Python cannot import the TS module and the
config can't import Python, so the mapping necessarily exists once per language.
``audit/python/kind_segments.py`` is the cross-language guard: it asserts this
dict equals the ``SEGMENT_OF`` map in ``src/lib/kinds.ts``.
"""

from __future__ import annotations

# Routed content kind -> structural-noun URL segment (also the content-collection
# name). Includes `project` (themed sections under /projects/) and `video`
# (catalogued YouTube/other-platform videos under /videos/) — both route and
# appear in the sitemap. Routing breadth != convertible-work scope.
SEGMENT_OF: dict[str, str] = {
    "book": "books",
    "poem": "poetry",
    "project": "projects",
    "video": "videos",
}

# The kinds that are convertible/downloadable corpus works — the source of truth
# for "which kinds the import/converter pipeline handles and which kinds get a
# download matrix". Projects and videos route but are not works (no DOCX-import,
# no PDF/EPUB matrix), so they are intentionally NOT here. This tuple is a
# subset of SEGMENT_OF's keys.
CORPUS_WORK_KINDS: tuple[str, ...] = ("book", "poem")

# URL segment -> routed kind (inverse of SEGMENT_OF).
KIND_OF_SEGMENT: dict[str, str] = {segment: kind for kind, segment in SEGMENT_OF.items()}
