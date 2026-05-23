"""Canonical work-kind -> URL-segment mapping (Python side).

This mirrors ``src/lib/kinds.ts``. Python cannot import the TS module and the
config can't import Python, so the mapping necessarily exists once per language.
``scripts/audit/python/kind_segments.py`` is the cross-language guard: it asserts this
dict equals the ``SEGMENT_OF`` map in ``src/lib/kinds.ts``.
"""

from __future__ import annotations

# Work kind -> structural-noun URL segment (also the content-collection name).
# Deliberately still includes `project`: projects are themed sections that route
# under /projects/ and appear in the sitemap, so the kind->segment mapping must
# cover them. (Routing breadth != convertible-work scope — see WORK_KINDS below.)
SEGMENT_OF: dict[str, str] = {
    "book": "books",
    "poem": "poetry",
    "project": "projects",
}

# The kinds that are convertible/downloadable WORKS — the single source of truth
# for "which kinds the import/converter pipeline handles and which kinds get a
# download matrix". Projects are themed sections, not works: they have no
# convert/download matrix, so `project` is intentionally NOT here (even though it
# stays in SEGMENT_OF for routing). WORK_KINDS is a subset of SEGMENT_OF's keys.
#
# DELIBERATE DIVERGENCE from `src/lib/kinds.ts`: the TS side's `WORK_KINDS`
# carries routing breadth (it includes `project`); this Python `WORK_KINDS` is
# converter/download scope (book/poem only). Same name, different membership — on
# purpose. Only `SEGMENT_OF` is mirrored across the two files (guarded by
# PAN003-kind-segment-parity); do NOT "sync" this tuple to the TS one. PAN017
# independently asserts `"project" not in WORK_KINDS`, so the harmful outcome of a
# wrong sync (re-admitting project to the importer) fails the audit, not silently.
WORK_KINDS: tuple[str, ...] = ("book", "poem")

# URL segment -> work kind (inverse of SEGMENT_OF).
KIND_OF_SEGMENT: dict[str, str] = {segment: kind for kind, segment in SEGMENT_OF.items()}
