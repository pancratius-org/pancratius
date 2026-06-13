# research-pure: scratchpad lineation package. Imports `pancratius`, never imported BY it.
"""Per-source-line lineation (`prose` vs `lineated`) for bilingual DOCX.

ONE canonical per-line feature artifact (`LineRecord`), produced once and consumed
identically by teacher annotation, an interpretable distilled student, and serve-time.

Layout (one home per concern):
    identity     LineId + content hashes + domain vocab (Label, ReaderTag, BookKey, …) — pure
    records      LineRecord + LineFeatures + Role/SourceFate + the feature schema — pure
    physics      the per-line LibreOffice wrap simulator — reads the DOCX (input adapter)
    source_view  the per-<w:br>-line structural view — reads the DOCX via pandoc (input adapter)
    producer     read_lines (the ONE feature producer) + to_vector / render_listing (thin views)
    annotations  per-line truth labels + the LLM panel's votes, loaded via the store edge
    selection    acquisition — which unlabelled lines to send the panel (writes selections/)
    student      the interpretable per-line student + (lang, book)-grouped CV
    sequence     the sequence-shaped prediction API (predict_document) + run smoothing
    recon        corpus census (det ⋈ student over every book) — the E0 denominator + router input
    corrections  projects committed truth → per-book lineation.<lang>.json importer sidecars
    artifact     the record-cache IO (_artifacts/): build once, load many, fail on drift
    store        truth/evidence disk (annotations/ _teacher/), over the artifact cache
    build_records  the cache (re)build entry point — DOCX → _artifacts
    teacher/     PRODUCES annotations — panel votes + human page-adjudications (task-local ids)
    evaluation/  JUDGES quality — student vs LLM readers on shared + contested lines

Disk writes live in three modules — `artifact` (cache), `store` (truth/evidence), `corrections`
(the one write into production content) — enforced by `tests/test_io_boundary.py`. Everything else
reads only; consumers LOAD via `store` and fail loud on a missing/stale cache (nothing rebuilds here).

This package imports `from pancratius import …` (the production library) but NOTHING in
`pancratius` imports it — the scratchpad→production boundary is one-directional.
"""
from __future__ import annotations
