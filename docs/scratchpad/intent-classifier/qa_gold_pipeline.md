# QA — gold_pipeline.py (ground-truth packaging harness)

Adversarial review before scale-up. Ground truth = rendered page + OOXML/typed-IR.
All evidence re-derived by running the harness; nothing trusted from self-reports.

Files under review:
- `docs/scratchpad/intent-classifier/scripts/gold_pipeline.py`
- `docs/scratchpad/intent-classifier/scripts/render_clean.py`
- `docs/scratchpad/intent-classifier/scripts/ir_view.py`
- `pancratius/docx_render.py` (the productized `render-slice`)
- gold: `data/gold_lineation/anchors_reconciled.jsonl` (201 lines)
- existing artifact: `data/gold_lineation/regions.json` (12 regions, written 2026-05-31 12:50 — a buggy run)

---

## BLUNT SCOREBOARD

| Axis | Verdict |
|---|---|
| Your Bug #1 (book02 render args) | CONFIRMED — worse than stated: even fixing flag names won't work (`--docx` needs a path, not `--book`) |
| Your Bug #2 (context drift r10/r11) | CONFIRMED — r10_b05 80 body lines vs 48 gold; r11_b27 95 vs 19 |
| **PNG ↔ structure span match** | **BROKEN (CRITICAL, NEW)** — render uses read_rows index space, structure uses ir_view index space. They diverge by a growing offset; for multi-match anchors the PNG is a 7-page render against a 25-line structure |
| Alignment of structure keys ↔ gold keys | SOUND — gold lives in ir_view space; (book,idx,sub) keys match exactly where checked (b02 "Ты странный", b13, b25, b27) |
| Label-space (κ comparison) | BROKEN (HIGH, NEW) — gold is 2-way, reader prompt is 3-way; no collapse defined |
| Contamination | PARTIAL LEAK (MED, NEW) — `role` carries the harness's own inferred classification (image/heading/pseudo_header/speaker_label) |
| render_clean faithfulness (book02) | DISTORTS (HIGH, NEW) — drops the inline image at idx 1022, fusing two scene beats across a boundary the structure marks as hard |
| emph / wraps / br fields vs OOXML | SOUND — spot-checked b27 idx172 and b71 litany; faithful |
| Sampling honesty | NOTED — 12 hand-picked anchors, not random (your own concern; out of immediate scope) |

**VERDICT: REQUEST CHANGES. Do not run at scale.** The PNG/structure index-space
mismatch (CRITICAL) silently shows the reader a page of a *different* span than the
line list it labels. Combined with Bug #1 (a quarter of the pilot has no page at all)
and the render_clean image-fusion, the κ validation gate would be measuring corrupted
input, not the reader.

---

## CRITICAL

### C1 — render and structure use TWO DIFFERENT index spaces; the PNG does not match the structure list
`gold_pipeline.py:73` calls `structure()` (uses `ir_view.read_view`, indices over
**IR blocks after `adapt`+`normalize`**) while `gold_pipeline.py:72`→`render()`
calls `pancratius docx render-slice` (uses `docx_render.resolve_range`→
`docx_inspect.read_rows`, indices over **raw OOXML `w:p`**). These two index spaces
diverge by a region-dependent, monotonically growing offset (the converter merges
epigraph/signature/blockquote/verse blocks and drops husks, so IR-block index <
OOXML-paragraph index).

Re-derived evidence (anchor "Если придёт момент", book 13):
- ir_view center = idx **736**; structure window = idx **724..748**.
- read_rows center = idx **739** (`uv run pancratius docx inspect --book 13 --around "Если придёт момент"`); render window = **[727..751]** ("rendered paragraphs [727..751]").
- Offset = **+3** here. For book 27 "Так будет в Моём": read_rows **170** vs ir_view **172** (+2). The offset is not constant.

Consequence: the structure JSON keys lines in ir_view space (which matches the gold),
but the PNG shows OOXML-space paragraphs — a different, offset span with different
edge paragraphs. The reader is asked to look at a PNG of span A while labeling line
list B. The two overlap but are not the same region; edges differ by several
paragraphs and the offset grows down-document.

### C2 — multi-match anchors make the PNG explode to 7 pages while structure stays ~25 lines
`docx_render.resolve_range` spans `hits[0]-ctx .. hits[-1]+ctx` (ALL matches), but
`gold_pipeline.structure` centers on the FIRST match only (`next(...)`,
`ir_view.py:53` / `gold_pipeline.py:53`).

Re-derived evidence:
- "Олег медленно сел" (book 13) matches read_rows idx **1288 and 1403** → render-slice
  emitted **"rendered paragraphs [1276..1415] -> 7 page(s)"** (140 paragraphs). The
  committed `regions.json` r00_b13 has **png=7, body=21**. The reader gets a 7-page,
  two-region PNG against a 21-line list.
- "Тем, кто" (book 68) matches **7** ir_view positions (30,31,32,36,37,38,…); same
  hits[0]..hits[-1] inflation on the render side.

### C3 — book02 renders 0 PNG pages (your Bug #1, CONFIRMED + deeper)
`gold_pipeline.py:42-43` invokes `render_clean.py` with `--book` and `--context`.
`render_clean.py:152-156` defines only `--docx`, `--around`, `--range`, `--ctx`,
`--out`. argparse exits 2; `capture_output=True` swallows it → no PNG.

Deeper than the flag rename: `render_clean` takes `--docx <path>`, not `--book <n>`,
so the fix must also resolve book→`ru.docx` path (e.g. via `features.book_dirs()`),
and pass `--ctx` not `--context`. Also note `render_clean` resolves its own range via
`r.resolve_range` (read_rows space) → for "Анфиса замерла" it rendered read_rows
**1009:1033**, independent of the ir_view structure window — i.e. fixing the args
still leaves the C1 span mismatch for book02 too.

Committed `regions.json` proof: r02_b02 **png=0**, r03_b02 **png=0**, r04_b02 **png=0**
— **48 of 201 gold lines (24%) have no page modality**. The pipeline's own docstring
says the PNG is required ("PNG alone fails on dense Cyrillic" — and here it's
structure-alone).

---

## HIGH

### H1 — render_clean DROPS the inline image at book02 idx 1022, fusing two scene beats
`render_clean._para_xml` (`render_clean.py:71-88`) emits only `w:t`/`w:br`/`w:tab`
runs; it has no handling for `w:drawing`/images. ir_view marks book02 idx **1022** as
`role=image`, a HARD boundary (ir_view.py:160-172 even documents this exact paragraph
as the bug it fixed). On the render_clean page the image vanishes, so "Анфиса замерла."
(1021) and "— А это не опасно?" (1023) render adjacent with no boundary. The structure
tells the reader "hard image boundary at 1022"; the PNG contradicts it. For a harness
whose whole point is fidelity of the page, the render silently drops a boundary the
structure asserts. (Rendered: `/tmp/qa_b02_anfisa-1.png` vs `ir_view --book 02 --around
"Анфиса замерла"`.)

### H2 — label-space mismatch: gold is 2-way, reader is 3-way; no collapse defined
Gold `anchors_reconciled.jsonl` uses `{flowing, lineated}`. Reader prompt
`prompts/svetozar.md` Variant A emits `{flowing, lineated-prose, verse}`. The κ gate
requires a documented mapping (verse + lineated-prose → lineated). gold_pipeline.py
emits neither the mapping nor the comparator, and the seed (`seed_gold.py`) uses a
THIRD vocabulary `{verse, prose, struct}`. Three label spaces in one pipeline with no
canonical collapse is a silent κ-poisoning hazard. Decide and document the collapse
before computing κ.

### H3 — over-packaging: reader judges a much larger region than was human-verified (your Bug #2, CONFIRMED)
`structure()` uses a ctx=12 window over **paragraphs**, but multi-`<w:br>` paragraphs
each expand to many sub-lines, so body-line counts balloon past the gold's hand-picked
span. Re-derived from committed `regions.json` (body lines vs gold keys for that book):

| region | body lines | gold keys covered | extra unverified lines |
|---|---|---|---|
| r10_b05 | 80 | 48 | 32 (40%) |
| r11_b27 | 95 | 19 | 76 (80%) |
| r07_b30 | 22 | 15 | 7 |
| r08_b34 | 15 | 6 | 9 |
| r09_b68 | 17 | 9 | 8 (idx 18-26, 42 outside gold) |

The reader labels 95 lines for r11 when only 19 are scorable. Those 76 extra lines
(a) bias/distract the reader, (b) require the downstream comparator to inner-join on
(idx,sub) or κ is undefined. The packaging should be bounded by the gold's verified
[lo,hi], not a fixed paragraph ctx.

---

## MEDIUM

### M1 — `role` field leaks the harness's own classification (contamination)
Each line in `regions.json` carries `role ∈ {body, empty, heading, image,
pseudo_header, speaker_label, …}`. `heading`/`image`/`thematic` are defensible neutral
structure, but `pseudo_header` and `speaker_label` are **inferred, lower-confidence**
classifications (ir_view.py:110-130) that pre-judge a line as boundary/struct — biasing
the reader toward the answer. The user's spec said the reader sees "page + neutral
structure." `role` is the harness's opinion, not neutral. At minimum collapse soft roles
to `body` for the reader, or expose only hard-boundary markers.

### M2 — silent midpoint fallback on anchor miss
`structure()` (`gold_pipeline.py:53`): `next((k ... if around in p.text), len(paras)//2)`.
If the anchor substring is ever not found (e.g. a punctuation/normalization difference
between gold `region` text and ir_view text), it silently centers on the **document
midpoint** and packages a completely unrelated region with no error. Meanwhile
render-slice's `resolve_range` *raises* on a miss — so the two halves disagree on the
failure mode. This is precisely the "stale index / wrong region, silent" class the
project has been bitten by. Make a miss fatal in both paths.

### M3 — `structure()` and `render()` recompute center independently; no shared range contract
The two code paths never share a resolved [lo,hi]. Even after fixing index spaces, any
future drift in either resolver silently re-desyncs the PNG from the structure. The
range should be resolved ONCE (in one index space) and both render and structure should
consume it, with the rendered page's paragraph list asserted equal to the structure's
idx list.

---

## LOW

### L1 — stale `regions.json` committed in scratch
The 97KB `regions.json` is from a buggy run (png=0 for book02, 7-page r00). It is under
`docs/scratchpad/` (transient per CLAUDE.md) so not a repo-integrity issue, but if any
downstream step already consumed it, those labels/κ are invalid. Regenerate after fixes.

### L2 — `main()` dead branch
`gold_pipeline.py:102`: `regions = ANCHORS if args.anchors else ANCHORS` — the
`--anchors` flag is a no-op; both branches are identical. Harmless, but misleading.

### L3 — sampling honesty (your own note)
The 12 anchors are hand-picked, book-clustered (b02 and b05 each = 48 of 201 gold
lines). A κ computed on this set is a calibration check, not an unbiased estimate of
reader accuracy on the corpus. Fine as a gate; do not report its κ as the scaled number.

---

## What is SOUND (verified, not assumed)
- **Structure-key ↔ gold-key alignment** is correct: gold was reconciled into the
  ir_view index space (NOT the seed_gold.py read_rows space — those idx differ, e.g.
  b25 gold idx 1..13 vs seed 6035), and `structure()` uses ir_view, so (book,idx,sub)
  match exactly. Verified byte-for-byte on r02_b02 "Ты странный" (18/18 keys identical),
  and by text on b13, b25, b27. No off-by-one in the key space itself.
- **`<w:br>` sub-line splitting** is faithful: b27 idx172 has 3 `<w:br>`→4 subs, all
  bold — matches raw OOXML (`run_bold=[True,True,True,True]`, `brs=3`).
- **`wraps`/`fill`** track the real reading column correctly: b71 litany lines
  (453-460) wraps=False/fill<1; surrounding prose (449-451) wraps=True/fill 3-5.
- **emph** (bold/italic) is computed from IR `Emphasis` kinds, matches source.

## Recommended sequencing before any scale run
1. Fix C1/C2/M3: resolve ONE range in ONE index space; render and structure consume it;
   assert the rendered paragraph list == the structure idx list (fail loudly otherwise).
2. Fix C3: call render_clean with `--docx <path> --ctx`, resolved in the same range space.
3. Fix H1: render the image (or insert a visible boundary marker) so the page matches
   the structure's hard boundary at book02 idx 1022.
4. Fix H3: bound packaging by the gold's verified [lo,hi], not a fixed paragraph ctx.
5. Fix H2: document and apply the 3-way→2-way collapse in the comparator.
6. Fix M1/M2: neutralize soft `role`s for the reader; make anchor-miss fatal both sides.
7. Regenerate regions.json; re-run the κ gate.
