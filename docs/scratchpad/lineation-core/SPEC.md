# Lineation features — Target Architecture (LOCKED SPEC)

Status: contract for implementation. Scratchpad prototype; the artifact contract is designed to
graduate to `pancratius/` later. NO production code imports the scratchpad.

## Purpose
Per-line lineation recovery (`prose` vs `lineated`) for bilingual DOCX. ONE canonical feature
artifact consumed identically by: teacher annotation, student distillation, serve-time
conversion. The split that triggered this rewrite (a feature living in the LLM listing but not the
student feature set) must become *structurally impossible*.

## Invariants (violating any is a bug)
1. **One feature producer.** A feature is computed exactly once. No consumer recomputes features.
2. **The SOURCE LINE is the unit** — a `<w:p>` segment split by *explicit* `<w:br>`. NOT a browser
   visual wrap (a source line may itself visually wrap; `fill/wraps` describe that). The paragraph
   is a grouping key for run-context, never a label slot.
3. **Language-agnostic features.** Helpers use punctuation / geometry / case only — identical code ru+en.
4. **Shared contract = the ARTIFACT (LineRecord), not identical sensory input.** Teacher gets
   privileged extra evidence (page image + listing); student/serve get `to_vector(features)`. This is
   LUPI — say it honestly.
5. **Context features are SOURCE-ONLY.** `run_len/run_pos/prev_next_structural/fill_pctile_in_book`
   derive from source geometry/structure — NEVER from predicted lineation or gate labels.
   Document-normalization is allowed at serve (whole book available) but must be explicit.
6. **role / votable / source_fate are first-class** — reproducible, auditable, train/test-visible.
   Structural masking is not hidden in debug metadata.

## Identity & validation
```
LineId(lang, book_id, src_ordinal, sub)
  src_ordinal  = source <w:p> ordinal — THE join key
  sub          = explicit-<w:br> segment index within the paragraph
```
Every record + annotation artifact also carries validation fields:
`docx_package_hash`, `paragraph_text_hash`, `line_text_hash`, `feature_schema_version`,
`producer_version`. **Hashes are safety rails:** on docx change the loader FAILS LOUD or enters an
explicit migration mode. `src_ordinal` alone is never trusted as durable truth.

## The canonical record
```
LineRecord:
  id: LineId
  text: str
  inlines: [InlineRun]            # runs with emphasis (bold/italic/…)
  role: body|heading|list|table|blank|thematic|signature|epigraph|blockquote|image|context|other
  votable: bool                   # true ONLY for body decision units
  source_fate: normal|dropped_toc|unmapped|mixed|…   # reproducible structural fate
  features: LineFeatures          # the only thing models vectorize
  meta: {style_id, raw_spans, diagnostics}           # NOT a feature source
```
```
LineFeatures:
  text-length / physics (FIRST-CLASS — among the strongest): fill, wraps, char_len, word_count
  boundary:   end_punct, starts_lower, next_line_lower, enjambs, colon_opens
  layout (within-book DIRECTIONED): align, indent_vs_book, spacing_after_vs_book,
            align_is_book_default, numbered, sub, n_subs
  context (SOURCE-ONLY): run_len, run_pos, prev_structural, next_structural, fill_pctile_in_book
  NOT a feature: raw book_id, raw style_id (→ meta). [style-DERIVED geometry may become one later if proven.]
```

## Artifacts (the product; functions are VIEWS over these)
```
line_records.jsonl       all LineRecords for (book, lang)
feature_schema.json      feature schema + feature_schema_version + feature_support
                         (feature_support MUST list ZERO-support features explicitly — they may
                          not vanish from analysis; the speaker-label=0 lesson)
line_labels.jsonl        LineLabel records WITH provenance (the human per-line truth)
panel_votes.jsonl        the LLM panel's per-line votes (reader tag + label + conf)
contested_labels.jsonl   human page-grounded labels on the re-adjudicated contested lines
manifest.json            producer_version, schema_version, hashes, langs, counts
```
```
LineLabel:
  id: LineId
  label: prose|lineated
  source: human|gate|panel|override
  confidence: float|null
  audit_status: …
  notes: str
  provenance: …        # opaque lineage — so corrections (e.g. g05) remain reasoned about
```
Training projects to `{LineId: label}`, but **stored truth keeps lineage.** Every annotation
artifact is `LineId`-keyed; consumers join by `LineId` — there is no structural-view key.

## Producer (exactly one)
```
read_lines(docx, lang) -> [LineRecord]
```
- prototype: MAY reuse `intent-classifier/scripts/ir_view.py` + `pancratius/docx_inspect.py`.
- target: reads the canonical production SourceView/IR + ParaRow (in `pancratius/`). We are NOT
  building the "one true producer" permanently on a transient scratchpad renderer; note the seam.
- physics read PER SOURCE LINE from the IR line (NOT recomputed on joined paragraph text — the H2
  double-compute bug). layout joined from ParaRow by `src_ordinal`. within-book norms per book.
  boundary per line, source-only. role/votable/source_fate computed here, once.

## Consumers (thin views; none recompute features)
```
teacher_input = page/render evidence + render_listing(records, keyed, with_features=True)   # LUPI
student_input = to_vector(features)
serve_input   = to_vector(features)
render_listing(records, keyed, with_phi)   # the ONE listing builder (replaces the 3 today)
```

## Prediction API (sequence-shaped, not i.i.d.)
```
predict_document(records) -> [LineDecision]
```
First implementation MAY be a per-line interpretable model (coefficient array / shallow tree), but
the API must allow smoothing / run-level priors / region decisions / CRF later — region coherence
is real (the human adjudications show it).

## Data policy
- Truth = per-line `prose/lineated` labels, `LineId`-keyed (the one-time structural-view-index →
  `src_ordinal` remap that built them was applied once at build time, lineage preserved in each
  label's `provenance`; the converter is retired). The package only LOADS the artifacts.
- Legacy `verse/prose/struct` (per-paragraph): **BARRED from supervised lineation training.**
  Permitted only as sampling / weak-prior / negative-control, clearly marked as non-truth.
- Taxonomy: `prose` vs `lineated` (2-class). Structural lines are `votable=False`, masked, not
  predicted.

## Build plan — single vertical slice, test-driven, in THIS folder
- **① Identity & artifact:** `LineId` + hashes + artifact schema + loaders (fail-loud on hash
  mismatch). PROVEN by tests before anything else.
- **② One producer + views:** `read_lines` + `render_listing` + `to_vector`. Parity proven: the
  teacher listing's features and the student vector's features derive from the SAME record — no recompute path
  exists.
- **③ Real task:** load the `LineId`-keyed per-line labels (with lineage) + train an INTERPRETABLE
  student on `prose/lineated`, book-grouped CV → a real number on the real task. Compare to the
  LLM teacher.

## Proof obligations (every step really proven, not self-reported)
- helper unit tests (`end_punct`, `starts_lower`, within-book norms, boundary) on crafted + REAL
  corpus cases, ru AND en.
- golden snapshot: `read_lines(known_docx)` == expected records (regression-locked).
- **parity invariant:** there is exactly one feature definition; a test asserts the listing and the
  vector read the same record (and ideally: no second code path can compute the features).
- **no-leakage invariant:** features are independent of labels/predictions (test by perturbation).
- **identity invariant:** `LineId` unique within (book,lang); hash mismatch → loud failure (tested).
- **label migration (one-shot, retired):** was lossless; lineage preserved in each label's
  `provenance`; 0 collisions — now witnessed by the committed artifacts + load-time hash rails.
- **zero-support reporting:** a schema feature with no rows appears in `feature_support` (tested).
- **bilingual parity:** one ru + one en fixture per line-kind → identical feature *semantics* (tested).
