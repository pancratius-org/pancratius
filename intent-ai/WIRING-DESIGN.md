# Production wiring design — correction set → importer (DoD #4)

Status: IMPLEMENTED for the `prose` direction (2026-06-12) — `pancratius/lineation_overrides.py`
(sidecar + rails), eligibility seam + post-fold fate assertion in `ir/normalize.py`, consumers
wired (import, `classify_blocks`, `lineation_decisions(apply_overrides=)`), exporter
`lineation_core/corrections.py` (total projection: holdout truth WITHHELD until its eval is
scored — exporting an eval item's answer would make that eval circular; deletes retracted
sidecars; diffs against the sidecar-free baseline so it is idempotent and drift-recoverable),
contract in `docs/content-model.md`, the 2 non-holdout corrections committed; gate floors
ratcheted. The `lineated` direction (force-fold) stays fail-loud-unimplemented until E3's error
classes fix its semantics — 27 non-holdout contradictions pending, converter-RCA first; 74 more
are holdout-withheld. Sidecar name is per-language: `lineation.<lang>.json`.

Known collateral, pinned by test: a mid-unit prose correction can demote its whole decision
unit (the remnant evidence re-qualifies alone) — adjudicate dense regions region-wise in E3.

## Corpus md regen — BLOCKED on two discovered divergences (2026-06-12)

Regenerating book md against the committed importer (the IR rework never regenerated books;
the site renders pre-rework lineation) was attempted and verified: 101 books change; 67 are
plain-text identical (pure lineation re-shaping — the intended catch-up). It cannot land yet:

1. ~~**5 ru books lose all images**~~ **FIXED (2026-06-12, commit 180a776)**: root cause was
   pandoc 3.9 dropping images from DOCX with generic namespace prefixes (9 books corpus-wide);
   the adapter now canonicalizes prefixes before pandoc. Image recovery is strictly additive,
   golden-protected (book27).
2. ~~**EN typography reverts**~~ **FIXED (2026-06-12)**: root cause was the importer itself
   lowering every double-`Quoted` to `«»` regardless of language; `lower.py` is now locale-aware
   (EN → American curly doubles, RU → guillemets) and normalizes literal EN guillemets too. So a
   regen no longer reverts `3e2e73e` — the rule lives in the producer.

Both blockers cleared. The remaining gate on the en (and ru) md regen is **lineation
validation** — a regen also carries the IR-rework's lineation reshaping (e.g. book-30 Surah
citations de-lineated), which is exactly what E1's en stratum + E3 validate. The typography and
image fixes are committed independently of that content regen; the regen itself runs once the
lineation surface is trusted (post-E1/E3), not before.

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
