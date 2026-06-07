# research-pure: scratchpad lineation package. Imports `pancratius`, never imported BY it.
"""Per-source-line lineation (`prose` vs `lineated`) for bilingual DOCX.

ONE canonical per-line feature artifact (`LineRecord`), produced once and consumed
identically by teacher annotation, an interpretable distilled student, and serve-time.

Layout (one home per concern):
    identity     LineId + content hashes + the shared domain vocabulary (Label, ReaderTag, …)
    records      LineRecord + LineFeatures + Role/SourceFate + the feature schema
    physics      the per-line LibreOffice wrap simulator (the one primitive production lacks)
    source_view  the per-<w:br>-line structural view — one production pipeline pass
    producer     read_lines (the ONE feature producer) + to_vector / render_listing (thin views)
    artifact     the on-disk record artifact: build once, load many, fail loud on drift
    store        the single IO edge — committed truth + record cache, joined by LineId
    labels       per-line truth labels (with provenance), loaded via the store edge
    panel_votes  the LLM panel's per-line votes, loaded via the store edge
    student      the interpretable per-line student + book-grouped CV
    sequence     the sequence-shaped prediction API (predict_document) + run smoothing
    teacher/     PRODUCES annotations — panel votes + human page-adjudications (task-local ids)
    evaluation/  JUDGES quality — student vs LLM readers on shared + contested lines

The committed TRUTH (`annotations/`: labels + votes + the contested eval set) and the derived
record CACHE (`_artifacts/`) are reached through the `store` edge by `LineId`; every consumer
LOADS and fails loud on a missing/stale store — nothing in the package rebuilds them.

This package imports `from pancratius import …` (the production library) but NOTHING in
`pancratius` imports it — the scratchpad→production boundary is one-directional.
"""
from __future__ import annotations
