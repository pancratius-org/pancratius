# QA — gold_pipeline rebuild (round 2)

Verifies the rebuilt `scripts/gold_pipeline.py` + `scripts/ir_view.py` against the
prior criticals, then hunts for rebuild-introduced bugs. Method: re-derived every
number from the IR; rendered and read the hard-case PNGs; joined `regions.json`
against `anchors_reconciled.jsonl` on the actual scoring key; reproduced all 12
regions from scratch (render-slice stubbed) and diffed against the committed file.

Files: `scripts/gold_pipeline.py`, `scripts/ir_view.py`,
`data/gold_lineation/regions.json`, `.../anchors_reconciled.jsonl`,
`.../png/*.png`. Index spaces involved:
- ir-idx = `iv.Para.index` (one per IR block we keep). Gold `idx` lives here.
- src-ordinal = `SourceSpan.start/end`, the raw body `<w:p>` ordinal, set in
  `docx_adapter.read_w_jc` (`source_index`, nodes.py SourceSpan). render-slice's
  `_ordered_paragraphs(body)[lo:hi+1]` indexes this same space (table-skipping,
  sdt-recursing walk identical to `read_rows`). **These three walks increment
  identically** (every `<w:p>` incl. list items; never `<w:tbl>`) — verified by
  reading docx_adapter.py:349-394, docx_inspect.py:173-238, docx_render.py:57-74.

---

## SCOREBOARD

| Fix | Claim | Verdict |
|-----|-------|---------|
| C1 | unified index space (render src == structure src) | **CONFIRMED** |
| C2 | regions from gold ir-idx ranges, no multi-match | **CONFIRMED** (1 caveat, MED) |
| H1 | book02 renders directly via render-slice | **CONFIRMED for index/structure; INCOMPLETE for the visual image** |
| H2 | 2-way label space aligns on (book,idx,sub) | **CONFIRMED** |
| M1 | no inferred-role / answer leakage | **CONFIRMED** |

**No CRITICAL or HIGH defects. The rebuild fixes every prior critical.**
One MED (H1 image renders as blank gap), three LOW/INFO. **Sound to run the
reader pilot** — with one reader-instruction note (see H1/MED-1).

---

## Per-fix evidence

### C1 — index spaces unified — CONFIRMED
The render covers `min(src_start)..max(src_end)` over the region's IR paras
(gold_pipeline.py:74-86); structure `lines[]` come from the same paras. Proven on
the hardest divergence case **r03_b05** where ir≠src by +6:
- gold idx 46..57 → src ordinals 52..63 (ir_view: idx46.src_start=52, idx49.src=55…).
- Rendered `png/r03_b05-1.png` **starts at** "Панкратиус, хочешь, я продолжу…"
  (= ir idx46) and the structure's first body line is `idx46 sub0` with the same
  text. Every subsequent rendered line matches the structure line-for-line through
  the 8-line stanza at idx54.
- r07_b27 (ir≠src by −2): src[169..174] renders idx171→176, matches structure.
- Reproduced all 12 regions from scratch: src ranges and line sets **byte-identical**
  to committed regions.json (0 mismatches).
- Latent divergence proof: book02 ir==src for idx 0..1407, then **+2 from idx1408**
  (16 trailing paras) — IR collapses some source `<w:p>`. None of the 12 regions
  touch that zone, but it confirms the SourceSpan bridge is load-bearing, not
  cosmetic; a naive `idx==src` render would silently misalign there.

### C2 — unique ir-idx ranges, body set == gold set — CONFIRMED (1 caveat)
`_regions_from_gold` (gold_pipeline.py:128-134) groups gold by (book,region) and
takes `min..max(idx)` — a unique, gold-defined range; no substring `--around`
matching survives in the build path (`_ir_range_for` exists but is unused by main).
Global join across all 12 regions:
- **0** gold rows lack a body line to score (every (book,idx,sub) of all 202 gold
  rows maps to exactly one body line).
- **0** body lines duplicated across regions.
- See MED-1 below for the only asymmetry (extra unlabeled body lines).

### H1 — book02 — CONFIRMED (index/structure) / INCOMPLETE (visual image)
- No `render_clean` call anywhere in the build path (only in the docstring).
  book02 goes straight through render-slice; index offset is 0 in the gold zone.
- Structure is faithful: `r00_b02` places `BREAK[image]` at **idx1022**, exactly
  between "Анфиса замерла." (idx1021) and "— А это не опасно?" (idx1023) — the
  scene-beat boundary the gold expects. Confirmed para[1022] is an image-only
  `<w:p>` (1 `<w:drawing>`, empty text) → routed to ROLE_IMAGE (ir_view.py:169-175).
- **INCOMPLETE:** the rendered PNG does **not show the image** — see MED-1. The
  boundary is correct in the structure but invisible (a blank gap) in the page.

### H2 — label space — CONFIRMED
Gold is 2-way {flowing, lineated} keyed (book,idx,sub); structure body lines carry
(book,idx,sub). Sub-line counts match exactly on every multi-`<w:br>` paragraph
checked (book02 idx1015/1019, book05 idx49-57, book27 idx171-176). Types match
(book:str, idx:int, sub:int). No drop, no dup (see C2 join).

### M1 — contamination — CONFIRMED
- `_HARD_CTX` covers every hard role; the **only** roles that fall through to the
  neutral `"bold-line"` marker are `pseudo_header` and `speaker_label` (verified by
  enumerating iv ROLE_* against the map). Example: book05 idx48 "Ответ от Творца:"
  is inferred `speaker_label` in the IR → emitted as `BREAK[bold-line]`, hiding the
  harness's guess.
- regions.json contains **no** `lineation`/`conf`/`reason`/`role` fields on any
  line. Break markers used: {heading, thematic-break(none here), image, blank,
  bold-line}. Body emph ∈ {"", bold, italic} only.
- Top-level `label` is the gold region anchor phrase (e.g. "Анфиса замерла"), a
  grouping name — NOT the flowing/lineated answer; it does not bias per-line calls.

---

## NEW-bug hunt findings

### MED-1 — book02/book27 images render as a blank gap, not a visible image
`docx_render.slice_docx` strips every `<w:drawing>` (docx_render.py:104-106) to keep
slices small. So image-only paragraphs (idx1022 in r00_b02; idx848 in r02_b02)
render as **empty whitespace**. The structure marks them `image`, but a reader
looking only at the PNG sees a large gap visually indistinguishable from a stanza/
section break (also rendered as a gap from `blank` paras). Ground truth is "the
rendered page", and the rendered page no longer shows the illustration that the
gold's scene-beat boundary depends on.
- Impact: a reader cannot use the *image* as a cue from the PNG; they can only see
  "there is a gap here." For the 2-way flowing/lineated call this is low-stakes
  (the surrounding text is unambiguous), so it does not block the pilot.
- Recommendation: either (a) instruct readers that the structure's `image`/`blank`
  markers — not the PNG gaps — are authoritative for boundaries, or (b) have
  render-slice replace stripped drawings with a visible placeholder rectangle so
  the page shows where the image sat. Do NOT keep full drawings (the prior book02
  "image distortion / explode into image pages" problem).

### MED-2 — `span-cov X/Y` print is a paragraph-vs-line artifact (misleading, not a bug)
gold_pipeline.py:146-150 prints `(span-cov {cov}/{nb})` where `cov` counts body
**paragraphs** with src_start and `nb` counts body **lines** (sub-lines). For book05
this prints "11/49", which looks like 78% missing coverage but is actually 11/11
paragraphs covered, expanded to 49 sub-lines. Re-derived: **every body paragraph in
all 12 regions has a src_start** (with_src == body_paras everywhere). Fix the print
to compare like units (`cov`/`#body-paras`) so a future operator does not chase a
phantom coverage hole.

### LOW-1 — `_src_ordinal` text-match fallback is dead here but has a duplicate-text trap
gold_pipeline.py:52-72 builds `tindex` with `setdefault` (keeps the FIRST ordinal
for a whitespace-stripped text key); on duplicate paragraph text a later paragraph
would resolve to the WRONG ordinal. **Never exercised** in this run (SourceSpan
present on 100% of body paras), but it is a latent landmine at scale where some
block legitimately lacks a span. Recommend: on fallback, either fail loudly, or
disambiguate by nearest-ordinal-within-region rather than global-first-match.

### LOW-2 — edge image/boundary para would clip the render at scale
The render span is `min/max` over paras **that have a src ordinal**. Hard-boundary
paras with `src_start=None` (notably ROLE_IMAGE — ImageBlock carries no SourceSpan)
do NOT extend the span. Here every region's first/last para has a src ordinal
(verified: no edge-clip in the 12), so it is benign. But if a future region begins
or ends on an image/other boundary, that boundary would be silently dropped from
the PNG. Worth a guard (extend src span to include edge boundary paras, or assert
the edge paras carry spans) before scaling to auto-selected regions.

### INFO — gold is 202 rows, not 201
The brief said 201; the file has 202 valid JSON lines (no blanks, no dup
(book,idx,sub)). Does not affect any verdict; flagging for bookkeeping.

---

## Verification story
- Tests reviewed: no unit tests for this scratch pipeline (research-pure scratch
  code); verified instead by full reproduction + render-and-read.
- Build verified: reproduced all 12 region packages from the IR with render-slice
  stubbed — `lines[]` and src ranges byte-identical to committed regions.json.
- Renders read: r00_b02, r02_b02, r03_b05 (pg1), r07_b27 — all match their
  structure `lines[]` line-for-line; the only discrepancy is the blank-gap image
  rendering (MED-1).
- Index integrity attacked across all three walks; the ir≠src divergence
  (book05 +6, book27 −2, book02 +2 tail) is correctly bridged by SourceSpan.
- No mutation of src/content; no commit.

## Bottom line
The rebuild is real and complete against C1/C2/H2/M1. H1's index/structure half is
correct; its visual half (the PNG showing the image) is not — but that is MED, not
blocking. **Run the reader pilot.** Before scaling region selection beyond these 12
hand-curated ranges, close LOW-1 (fallback duplicate trap) and LOW-2 (edge-boundary
clip), and either fix the image-as-gap render or instruct readers to treat the
structure markers as the boundary authority (MED-1).
