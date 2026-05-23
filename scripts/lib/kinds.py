"""Canonical work-kind -> URL-segment mapping (Python side).

This mirrors ``src/lib/kinds.ts``. Python cannot import the TS module and the
config can't import Python, so the mapping necessarily exists once per language.
``scripts/audit/python/kind_segments.py`` is the cross-language guard: it asserts this
dict equals the ``SEGMENT_OF`` map in ``src/lib/kinds.ts``.
"""

from __future__ import annotations

# Work kind -> structural-noun URL segment (also the content-collection name).
SEGMENT_OF: dict[str, str] = {
    "book": "books",
    "poem": "poetry",
    "project": "projects",
}

# URL segment -> work kind (inverse of SEGMENT_OF).
KIND_OF_SEGMENT: dict[str, str] = {segment: kind for kind, segment in SEGMENT_OF.items()}
