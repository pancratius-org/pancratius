# Production wiring design — correction set → importer (DoD #4)

Status: DESIGN (vetted seams, not yet implemented). Implement as its own gated block once E3's
first error classes confirm the override semantics; the 3 already-adjudicated corrections
(ru:17:140, ru:17:141, ru:24:1522 — det says lineated, human says prose) are the first content.

## What exists

- Truth lives in `lineation-core/annotations/labels.jsonl` (scratchpad). The production importer
  must NOT read scratchpad (the boundary is one-directional), so corrections are **exported** as
  production data, not read in place.
- `ir.LineatedBlock` carries only a block-level `SourceSpan` — no per-line ordinals — so a
  post-`verse_blocks` surgery pass cannot reliably map a corrected ordinal back to its lines.
  The override must apply **at the fold decision**, before block assembly.

## Design

1. **Correction file (production, committed):** `src/content/books/<NN>-slug/lineation.json` —
   per-book sidecar, content-model citizen (like structured bibliography):
   `{"<src_ordinal>": {"register": "prose"|"lineated", "text_sha": "<line-text hash>"}}`.
   The hash is the drift rail: on mismatch the importer FAILS LOUD (the docx changed under the
   correction), never silently applies or skips. Requires a content-model.md section (contract
   change, same PR).
2. **Exporter (lineation-core):** a small command that projects `labels.jsonl` rows whose label
   CONTRADICTS the current tier-0 verdict (det≠truth, human/override-sourced) into the per-book
   sidecars. Truth stays single-store; the sidecar is a derived-but-committed projection (like
   en.md from en.docx: docx+md move together — labels+sidecar move together).
3. **Importer seam (pancratius/ir/normalize.py):** `verse_blocks`/`lineated_blocks` take an
   optional `overrides: Mapping[int, Register]`;
   - `→prose`: the row is excluded from every fold path (source-row folding, hard-break
     promotion, coda/sub-unit folds) — it lowers as a plain `Paragraph`. A `<w:br>` inside it
     keeps its display breaks (existing `hard_break_prose` semantics already render that as
     prose register).
   - `→lineated`: the row is force-eligible for folding; if no neighbour block absorbs it, it
     becomes a single-line `LineatedBlock` with `LineationEvidence(corrected=True)`.
   `adapt`→`normalize` callers thread the sidecar; `lineation_decisions` then reflects the
   corrected fate automatically (it reads block fates), so recon/E4 see the corrected system
   with zero extra plumbing.
4. **Gates:** `tests/test_det_regression.py` floors ratchet UP in the same change (the 3 known
   corrections flip det errors → floors improve); pancratius goldens regenerate for books 17,
   24; `npm run verify` green; the E0 recon re-run shrinks the suspect slice (the feedback loop
   the strategy mandates).

## Why not alternatives

- **Post-pass block surgery**: needs per-line provenance LineatedBlock doesn't have; adding it
  bloats IR for one consumer.
- **md patching after import**: violates "md is derived, docx+md move together"; corrections
  would be lost on re-import.
- **Central corrections file in data/**: per-book sidecar keeps content with its book (asset
  naming precedent), and partial re-imports stay self-contained.
