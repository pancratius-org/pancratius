# research-pure: scratchpad lineation package. Imports `pancratius`, never imported BY it.
"""Per-source-line lineation (`prose` vs `lineated`) for bilingual DOCX.

ONE canonical per-line feature artifact (`LineRecord`), produced once and consumed
identically by teacher annotation, an interpretable distilled student, and serve-time.

Layout (one home per concern):
    identity     LineId + content hashes (the join key and safety rails)
    records      LineRecord + LineFeatures + Role/SourceFate + the feature schema
    physics      the per-line LibreOffice wrap simulator (the one primitive production lacks)
    source_view  the per-<w:br>-line structural view — one production pipeline pass
    producer     read_lines (the ONE feature producer) + to_vector / render_listing (thin views)
    artifact     the on-disk artifact: build once, load many, fail loud on drift
    labels       per-line truth labels (with provenance), loaded from the artifact
    panel_votes  the LLM panel's per-line votes, loaded from the artifact
    student      the interpretable per-line student + book-grouped CV
    sequence     the sequence-shaped prediction API (predict_document) + run smoothing
    compare      student vs LLM readers on shared labeled lines
    contested    student vs LLM readers on the human-adjudicated contested lines

The canonical artifacts (records + line_labels + panel_votes + contested_labels) are committed
data; every consumer here LOADS them by `LineId` and fails loud on a missing/stale store —
nothing in the package rebuilds them.

This package imports `from pancratius import …` (the production library) but NOTHING in
`pancratius` imports it — the scratchpad→production boundary is one-directional.
"""
from __future__ import annotations
