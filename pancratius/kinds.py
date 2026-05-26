"""Canonical work-kind -> URL-segment mapping (Python side).

This mirrors ``src/lib/kinds.ts``. Python cannot import the TS module and the
config can't import Python, so the mapping necessarily exists once per language.
``audit/python/kind_segments.py`` is the cross-language guard: it asserts this
dict equals the ``SEGMENT_OF`` map in ``src/lib/kinds.ts``.
"""

from __future__ import annotations

# Work kind -> structural-noun URL segment (also the content-collection name).
# Deliberately still includes `project`: projects are themed sections that route
# under /projects/ and appear in the sitemap, so the kind->segment mapping must
# cover them. (Routing breadth != convertible-work scope.)
SEGMENT_OF: dict[str, str] = {
    "book": "books",
    "poem": "poetry",
    "project": "projects",
}

# The kinds that are convertible/downloadable corpus works — the source of truth
# for "which kinds the import/converter pipeline handles and which kinds get a
# download matrix". Projects are themed sections, not works: they have no
# convert/download matrix, so `project` is intentionally NOT here (even though it
# stays in SEGMENT_OF for routing). This tuple is a subset of SEGMENT_OF's keys.
CORPUS_WORK_KINDS: tuple[str, ...] = ("book", "poem")

# URL segment -> work kind (inverse of SEGMENT_OF).
KIND_OF_SEGMENT: dict[str, str] = {segment: kind for kind, segment in SEGMENT_OF.items()}
