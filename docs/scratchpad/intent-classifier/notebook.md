# Lab notebook — prose-vs-verse intent classifier

Running log of hypotheses, experiments, what was refuted, what survived. Newest
entries appended. Honesty over optimism.

## Target (reframed from grounding)

Per-paragraph intent ∈ {prose, verse-line}, then group contiguous verse-lines into
verse-blocks bounded by deliberate breaks (empty para, heading, `***`, right-align).
Operational adjudication question, decided from the rendered page + the site goal:

> Should this paragraph render as a **discrete line in a tight block** (verse-line),
> or as a **flowing prose paragraph** (prose)?

## What I KNOW after grounding (2026-05-29)

1. **Wrapping is the keystone physical signal and it is page-visible.** Built
   `scripts/wrap.py` (LibreOffice's own LiberationSerif via PIL, real reading column
   from `sectPr`: ~292pt / 4.06in, 12pt body). Predicted rendered line-counts match
   the actual #71 render EXACTLY (440→3, 441→1, 443→2, 448→2, 449→4, 461→1, 462→2).
   - `fill` = natural single-line advance / column width. >~1.0 ⇒ wraps ⇒ provable
     PROSE. The author cannot fake this.
2. **The uniform paragraph spacing is noise, confirmed on the page.** #71 renders the
   litany (453–461) and the short prose enumeration (441–447) with *identical* gaps
   (uniform `sa160`). The page does NOT separate them — only wrapping does. Anything
   keying on spacing (the heuristic's `lineation_group`/`ctx.visual`) is keying on
   incidental styling — the prior cardinal sin.
3. **The ambiguous middle is precisely located.** Among SHORT NON-WRAPPING lines,
   physics is silent. #71: litany 453–461 (fill .26–.93) vs enumeration 441–447
   (fill .30–.92) are physically identical. Discriminator is semantic/structural
   (run length, anaphora/parallelism, interrogative rhythm), not physical. A
   wrapping line *inside* a run (443, fill 1.92) splits it — honest to physics.

## The heuristic to beat (`pancratius/ir/normalize.py`)

`verse_blocks`/`_run_kind`/`_para_lineated`. Signals it uses: hard `<w:br/>`,
heading/`***`/named-section context, empty-paragraph stanza breaks, char-length
thresholds (`VERSE_SHORT_LINE_MAX=120`, avg-length ladders), AND — critically —
`lineation_group` (`ctx.visual`), which is the contextual-spacing visual grouping
from `docx_adapter._assign_lineation_groups`. That last one is the incidental-styling
key the brief warns against; it is the prime suspect for #02-vs-#71 inconsistency.
NO real wrapping simulation — only the 120-char proxy. **This is the lever.**

## Anchors

- #25 `vzglyad` — clean free verse, already grouped (style `ad`, ctxSp). Positive.
- #71 litany idx 453–461 — should group, currently left as gappy prose. The miss.
- #71 enumeration 441–447 — ambiguous-middle exemplar.
- #13 narrative novel — justified/indented prose; wraps. Negative anchor.
- src/content/poetry/* — near-pure verse. src/content/pages/mission/* — verse.

## Plan

- [x] Ground on renders; build + validate wrapping sim.
- [ ] Corpus feature table: every body paragraph of all 75 books × {wrap stats,
      deliberate signals, line-length, run context, lexical/parallelism cues}.
      EXCLUDE incidental styling (spacing, jc=both, ind, contextualSpacing) except
      as negative-control columns.
- [ ] Label acquisition: SILVER (weak supervision from physics+deliberate signals)
      + GOLD (visual adjudication, stratified, focused on the ambiguous middle).
      Validate any VLM auto-adjudicator vs a human-anchored seed set; measure agreement.
- [ ] Models: interpretable baseline (LogReg / GBT on engineered features, by-BOOK
      CV) → escalate to sequence model (CRF / line-transformer) only if justified.
- [ ] Adversarial red-team every claim (leakage, label noise, book that breaks it).
- [ ] Distill to a deployable rule/model; integration recommendation for normalize.py.
- [ ] Report + reproducible scripts/seeds.

## Log

### 2026-05-29 — foundation
- Read inspect/render/adapter/normalize/book_verse. soffice+pdftoppm+pandoc present.
  sklearn 1.8 / numpy 2.4 available via `uv run --with scikit-learn`.
- Built `scripts/wrap.py`, validated vs #71 render (exact). Keystone secured.
- Launched background lit-research agent (SOTA verse/prose detection, Russian
  encoders, weak supervision, VLM labeling, by-group eval).
- KEY DECISION: invert default per brief — short non-wrapping run = lineated by
  default; PROSE is the provable exception (wraps). Verse is the residual.

### 2026-05-29 — lit research (background agent) digest
- Architecture: HSLN / **BiLSTM-CRF over line embeddings** is canonical SSC; CRF
  models verse/prose as contiguous runs (matches run-grouping). sklearn-crfsuite is
  the lightweight, deployable analogue (linear-chain CRF on engineered features).
- Russian encoders: ru-en-RoSBERTa (best classification), mE5-large (easy), rubert-
  tiny2 (fast). Escalation only.
- **Differentiated lever: Russian meter/rhyme** (RPST/RIFMA — syllabo-tonic+rhyme).
  CAVEAT I add: this author's HARD cases are free verse + litany (anaphora, not
  metered/rhymed). Meter helps metered poetry/psalms, NOT the litany boundary. Still
  compute syllable-count uniformity + stress regularity as features.
- Weak supervision: Snorkel LFs + cleanlab (corpus is explicitly noisy-labeled).
- Eval: by-document/by-book CV + bootstrap CIs + temperature/`CalibratedClassifierCV`.
- VLM-as-judge: validate vs human-anchored gold with Cohen's κ; verification beats
  confidence-prompting. I am the adjudicator (vision+text).
- TENSION held: SOTA neural = research ceiling; SHIP form likely GBT/CRF on
  engineered features (normalize.py prizes one editable rule). Report tradeoff.

### 2026-05-29 — corpus characterization + gold methodology
- 437,506 paragraphs (75 books + 43 poems). BOOKS: 87% non-empty; **only 11.3%
  wrap** (provable prose); **84% short non-wrapping** (candidate space); ~72% of
  short lines sit in runs ≥12 (confidently lineated). Ambiguous middle is SMALL:
  short runs (run_len 1–6 ≈ 41k) + boundary fill (0.85–1.05 ≈ 8k).
- Two-regime corpus: prose-heavy (#16 85% wrap, #75 90%, #72 79%) vs lineated-heavy
  (#25 1.8%, #54 0.7%, #08 0.4%). #13 narrative 47%. Wrap% is a strong per-book prior.
- Heuristic baseline (live normalize, all 75): labels 64% verse — it DOES group
  styled verse (via lineation_group). Its miss is runs WITHOUT styling cues: confirms
  it labels BOTH #71 litany (453-461) AND enumeration (441-447) as prose. The lever.
- GOLD methodology: rubric.md (register+structure beats length; wrap=strong prose
  prior; inversion default; fiction/dialogue=prose; anaphora/litany/parallel-list=
  verse even if mildly wrapping). Inviolate human-anchored seed: 166 labels / 9 books
  (several rendered & viewed). **Auto-adjudicator validated: Cohen's κ=0.97** (3-way
  & binary) vs seed; 3 disagreements all on med-conf wrap-edge cases. Trust granted.
- Stratified sampler: 323 windows (hard_run 128, random 66, isolated 65, boundary
  64), ±8 context, de-duped, across all 75 books.

### 2026-05-29 — gold + first model (GBT, by-book CV)
- Scaled adjudication: 16 parallel judges → 5,459 gold labels (2,254 verse / 1,772
  prose / 1,433 struct), all 75 books. Seed overrides 5 judge calls.
- **GBT (22 clean engineered features, GroupKFold by book): macroF1 0.814 vs
  heuristic 0.725. Δ=+0.089, bootstrap 95% CI [+0.045,+0.132], P(Δ>0)=1.00.**
- Errors concentrate in the ambiguous middle (the brief's bar): hi-conf gold
  acc=0.934 (heur 0.852); med/lo-conf acc=0.728 (heur 0.638). Wins in BOTH.
- More than a length threshold: best single char-len cutoff=0.776, length-only
  feats=0.764, +register/anaphora=0.814. (Heuristic 0.725 is WORSE than a one-line
  length rule — its incidental-styling keys actively hurt.)
- Per-stratum model/heur: random .858/.826, hard_run .810/.708, isolated .786/.709,
  boundary .808/.684. Biggest wins on hard_run (litany) + boundary (wrap-edge).
- **Corpus-reweighted UNBIASED acc: model 0.863 vs heuristic 0.794** (+6.9pp).
- NEGATIVE CONTROL: adding incidental-styling feats → 0.803 (drops). They don't help;
  exclusion validated empirically.
- Top features: char_len ≫ run_len > word_count > fill > has_proper_name > br_count
  > anaphora_next > ends_colon > anaphora_prev > has_2nd_person. Length axis dominant,
  register/anaphora add the decisive +0.05 on the hard cases.

### 2026-05-29 — escalation tests + red-team
- CRF (sklearn-crfsuite, windowed sequences, by-book): macroF1 0.806 ≈ GBT 0.814 (TIE).
  Engineered neighbour features already capture run structure → sequence model NOT
  justified. Strong argument for the deployable per-paragraph model.
- Circularity check: model vs HUMAN seed (my direct render adjudication, by-book OOF)
  acc 0.876 (heur 0.798).
- [RT3] STRICT holdout — train on judge labels with ALL 8 seed books fully excluded,
  test on 129 human-seed labels: acc 0.876, macroF1 0.875. Different label source +
  held-out books ⇒ circularity worry substantially defeated.
- [RT2] cleanlab: 443 suspected label issues (11%), 400/443 in lo/med conf ⇒ "errors
  = ambiguity." (Cleaned-gold 0.916 is circular; report the conf-distribution, not it.)
- [RT1] regime: model beats heuristic in BOTH — prose-heavy (wrap>.45) 0.663 vs 0.524,
  lineated 0.830 vs 0.716. Per-book ACCURACY: model>heur 46, heur>model 21, tie 8.
  The earlier "20/74 macroF1 losses" was small-n noise.
- RESIDUAL RISK (deployment): verse-false-positive on true prose = 14% (prose-heavy)
  / 29% (lineated). The model sometimes groups short prose tight. Threshold knob.
- SIGNATURE/right-align (rare, 0.06% = 269 paras): heuristic maps them Epigraph 167 /
  Signature 10 / **VerseBlock 60** (signature→verse drift) / Paragraph 28 / DLG 4.
  4 of 9 gold right-aligned are genuinely VERSE ⇒ right-align is a deliberate marker
  but not purely signature. Out of scope for the prose/verse classifier (handled by the
  trusted w:jc rule), but the heuristic's destination-resolution is imperfect — flag.

### 2026-05-29 — distillation + embedding ceiling → diminishing returns
- Accuracy↔complexity (by-book macroF1): heuristic .725 | 1-rule char_len .769 |
  tree d2 .791 | **tree d3 .795 (79% of gain, transcribable)** | logreg .802 | GBT .814.
- Depth-3 tree rules: run_len≤2 → prose unless anaphoric&!wrap; run_len>2 →
  char_len≤45 verse unless proper_name(→prose, the #13 fiction case); 45–58 verse; >58 prose.
- EMBEDDING CEILING: multilingual MiniLM (384d)+logreg by-book = .743 (emb-only) /
  .782 (emb+engineered) — BOTH < engineered GBT .814. Generic encoder loses to
  physics+register; combo overfits by-book. ⇒ DIMINISHING RETURNS confirmed (with CRF
  tie + cleanlab-noise-in-ambiguity). Residual is irreducible ambiguity, not capacity.
  Caveat: logreg not GBT, not a fine-tuned ru encoder.
- Report written: reports/report.md. Integration rec: port wrap.py into normalize.py,
  replace lineation_group/ctx.visual with wrapping predicate; per-paragraph classify
  (ship depth-3 tree or GBT) + deterministic run-grouping on deliberate breaks; NO
  sequence model needed; conservative verse threshold for prose-heavy books; guard
  right-align→VerseBlock drift.

### 2026-05-29 — adversarial red-team CHANGED the conclusions (verified myself)
- Skeptic subagent + my re-verification. Per-stratum bootstrap (by book):
  hard_run Δ+0.104 P=1.00, boundary Δ+0.131 P=0.999 (SIGNIFICANT); isolated Δ+0.075
  P=0.97; **random (representative) Δ+0.024 CI[-0.055,+0.104] P=0.71 — NOT significant.**
- ⇒ The +0.089 headline is inflated by ambiguity-enriched sampling. Win is real &
  significant ON HARD CASES (the commissioned problem); corpus-level within noise.
- Crude rule (fill<1 & char_len<120): 0.757 vs gold; model agrees with it 81% (model
  vs gold 0.817). Model is ~81% the crude wrap+length rule + a SIGNIFICANT +0.05
  register edge (CI[+0.025,+0.077]).
- Circularity NOT defeated: judge↔human agree 0.964 by construction (judge tuned to
  κ0.97 vs seed); strict holdout rides on that. Reverse holdout (train human→test
  judge) only 0.749. Independent ground truth = physics + 4 brief anchors only.
- Claims 2/3/4 survive; 1 weakened (corpus-level n.s.); 5 refuted as circularity defense.
- Report revised for honesty (§1,§6,§7,§12). DIMINISHING RETURNS on modeling axis;
  next gain requires an INDEPENDENT human test set (can't self-produce) — STOP here.
- DELIVERABLE = the STRUCTURAL fix (wrapping physics + run structure, not incidental
  styling), which even a tiny rule delivers; learned model adds a measured hard-case edge.

### 2026-05-29 — FRAMING REFRAME (user-prompted; corrects "irreducible ambiguity")
- Real-surface check: rendered #71 in ACTUAL Astro CSS (renders/site_71_compare.png),
  current gappy <p> vs wrapping-grouped verse-block. Litany grouping clearly looks
  better; short-prose-enumeration over-grouping is the visible "vomit" risk. The DOCX
  render was a PROXY; the Astro HTML is the true surface.
- GROUPING-framing experiment: 3 independent judges, WIDE context (±28), segmentation
  task. mean Cohen κ=0.936 (3-way) / **0.971 (binary)**; all-3-agree 93.8%; lo-conf <4%.
  ⇒ With full context, block BOUNDARIES are mostly OBVIOUS; genuinely-fuzzy ≈ **6%**,
  NOT the ~57% med/lo the per-paragraph/narrow-window framing implied.
- My per-paragraph GBT vs wide-context grouping-consensus: 86.4% overall, **85.9% even
  on the OBVIOUS (3-agree) set** ⇒ the per-paragraph framing LEAVES ~14% of
  context-obvious cases on the table. Headroom is real; CRF "tie" was a narrow-feature
  artifact, not a ceiling. RETRACT "diminishing returns / irreducible ambiguity."
- FRAMING SPACE (the meta-lesson — I locked into F1 too early):
  F1 per-paragraph binary (done) — inflates apparent ambiguity, leaves context on table.
  F2 wide-context SEGMENTATION/grouping (judges = an F2 oracle; ~94% ceiling) — best next.
  F3 noisy-channel DECODING: P(intended Shift+Enter | pressed Enter, context); intent as
     latent var; its posterior is itself a feature. (user's example)
  F4 RENDER-IN-THE-LOOP: objective = "looks good on Astro HTML"; features/reward from
     BOTH docx source AND rendered HTML; VLM judges the output.
- Next wins: F2 (recover the 14%) via an LLM segmenter distilled to wide-context
  features; F4 is the truest objective (matches the real surface). See report §8,§14.

### 2026-05-30 — F4 modality gate RESULT (59 cells, Sonnet+Opus)
- anchor-agreement: both(png+markup) 1.00 | markup 0.95 | png-only 0.74.
- SUBTLE method pairs: both 1.00 | markup 0.90 | png-only 0.56 (≈chance).
- png-only transcripts have Cyrillic OCR errors (Анфиса->Алфея, кивнул->закурил).
- position bias ~0.50 (unbiased). One cell failed StructuredOutput (dropped).
- VERDICT (confirms user hypothesis): judge on PNG+MARKUP together; NEVER png-only.
  markup-only is a cheap, near-perfect fallback. PNG alone degrades content reading.

### 2026-05-30 — F4 method bracket on the REAL surface (54 pairwise, Sonnet+Opus, markup)
- Win rates: **LLM(wide-context) 0.78 (21-4-2)** ≫ heuristic 0.52 (14-12) > wrap 0.37 > gbt 0.37.
- REORDERS the label-based ranking: GBT won on label-F1 (0.814) but LOSES on the page
  (0.37) — per-paragraph contiguity errors barely move F1 but look terrible rendered
  (italicized dialogue, "Ответ:" labels as verse, single answers split verse/prose).
- wrap also 0.37: over-groups narrative dialogue into italic verse (the "vomit").
- heuristic middling: under-groups (orphans litany as gappy <p>) but avoids gross
  over-grouping.
- LESSON (confirmed twice): per-paragraph F1 was the WRONG objective. The render-
  in-the-loop reranks methods. Wide-context segmentation (F2) wins on the F4 surface.
- Caveat: r06 had 1 byte-identical heuristic==llm pair (tie). Judges' reasons cluster
  on two flaws: dialogue-as-verse (over-group) and gappy-litany (under-group).

### 2026-05-30 — INDEPENDENT HUMAN verdict (user, 6 neutral side-by-side cases)
- Decoded: #02 heuristic>wrap, #02 heuristic>llm, #10 llm>heuristic, #08 llm>allprose,
  #04 heuristic>gbt, #07 llm>wrap.
- Validation: my anchors agree 5/5 (1 had no anchor); LLM panel agrees 3/3 on sampled
  pairs +1 tie (#02 heur-vs-llm: panel split 1-1, user decisively heuristic). No reversal.
  ⇒ κ-validated pipeline survives a REAL human (small n, but first external truth).
- Pattern: GROUP in verse/litany/Q&A (#10,#08,#07→llm); DON'T group narrative/expository
  (#02,#04→heuristic). Per-REGIME preference = the two-regime corpus (wrap% prior).
- Mild panel pro-grouping bias confirmed: panel tied where user said a clear "no".
- **USER'S KEY INSIGHT (next target): verse options are penalized for WRONG BOUNDARIES,
  not wrong prose/verse calls. Classification is ~solved (κ.97); BOUNDARY PRECISION is
  the bottleneck.** Explains F1-looks-fine-but-page-looks-wrong: F1 rewards mostly-right;
  a block with one wrong line at each end READS broken (low-freq, high-visibility).
  ⇒ Reframe F2 metric to BOUNDARY-level (exact start/end, span-F1 / WindowDiff/Pk),
  not per-paragraph accuracy. That is the next experiment.

### 2026-05-30 — OBJECTIVE FLIP (post CSS fix) + redesign
- CONFIRMED prose.css changed (indent 1.4em, gap .35em, flush-after-break). Verified.
- NEW OBJECTIVE: detect GENUINE VERSE REGISTER (italic/lineated gear-shift); PROSE is
  safe default. Error economics INVERT: under-group now cheap (reads as clean indented
  prose), over-group now the expensive/visible error. Brief's "inversion" flips BACK:
  precision-first, prove verse not prose.
- Reprices prior work: wrapping = strongest NEGATIVE evidence (wraps⇒not verse);
  register feats (proper-name/speech→prose) MORE valuable (guard dominant error); the
  ambiguous middle stops mattering (when unsure→prose, looks fine); per-paragraph GBT
  value drops (a conservative high-precision rule likely matches it); BOUNDARY precision
  MORE important on the smaller true-verse set.
- F4 bracket result is STALE (judged under old gappy CSS where llm-segmenter won partly
  by rescuing gappy litanies — a virtue that evaporated). Must re-judge under new CSS.
- QA-ANSWERS corpus-wide: 24/75 books, 2609 question-markers; #01 (883q≈927vb), #10
  (737q≈765vb) almost entirely QA. NOT a book-30 quirk — governs a huge share of corpus
  verse-blocks. #30 render (new CSS): answer "Задумайся:/Разве…/Разве…/Как может…" reads
  BETTER as verse (anaphoric invocation); prose-indent chops the meditation rhythm.
  NUANCE: benefits because answer CONTENT is genuine verse register, NOT because QA.
  ⇒ QA-answer = useful PRIOR (nudge to verse) confirmed on register; not a blanket rule.
- THREE THREADS: A re-judge bracket under new CSS + asymmetric cost; B QA-register
  corpus-wide; C precision-first detector + inverted audit (verse-block must be earned).

### 2026-05-30 — REASSESS under the lineation/register frame (read verse-lineation-plan.md)
- Frame ACCEPTED: lineation (structural, <w:br>-anchored, 2-space encoding) vs register
  (editorial, .verse-block class), verse⊂lineated, decisions at import, downstream
  mechanical. #02 multipart GONE (one DOCX). Phase 0 encoding verified by other agent.
- DATA CHECK of the plan's load-bearing empirical claims (364,559 content paras):
  * <w:br> free ground truth = 2.6% (63/75 books) — rare, rarer than frame leans on.
  * wraps⇒certain prose = 9.1%.
  * short-nonwrap AMBIGUOUS middle = 88.3% (321,798) — NOT a thin middle; the "only
    hard stage" is hard for most paragraphs. Correct the plan's optimism here.
- BUT difficulty is concentrated, and the frame de-risks it:
  * of the ambiguous 88%, only 2.2% are ISOLATED short lines amid prose (the truly
    hard case); 97.8% sit in RUNS, mass in LONG runs (rl>=20 = 187K) = confidently
    lineated by any sane rule.
  * lineation easy at extremes (wraps=prose; long short-runs=lineated; <w:br>=anchor);
    genuinely hard only for the sliver = isolated + short runs (rl 2-4).
  * AND that sliver is now LOW-STAKES: flowing-para vs lineated-prose is cosmetic
    (indent+tiny-gap vs stacked breaks — both read as stacked short lines); the only
    structural fact (does a break exist) is preserved by emitting it. Register error =
    cosmetic by the frame's separation property.
- IMPLICATION for my discipline (data/validation): the hard, measurable problem shrinks
  to (1) the wrapping cut (physics, validated) + (2) REGISTER PRECISION on lineated runs
  (the verse-vs-lineated-prose call) + (3) the isolated-short-line sliver. Recall is no
  longer an axis. Per-paragraph F1 was wrong (boundary precision + register precision
  are the metrics). All consistent with the earlier F4/boundary findings.
- VALIDATION ASSETS I already have that transfer: wrap.py (the physics cut, render-
  validated), the gold (4026 labels) — but gold must be RE-PURPOSED: relabel as
  {flowing / lineated-prose / verse} 3-way, and the metric becomes register-precision +
  lineation-fidelity, not prose/verse accuracy. The old binary gold maps: verse->verse,
  prose-that-wraps->flowing, prose-short-run->lineated-prose (NEW middle class).
- Google-Docs-export oracle: untested, potentially high-value for the lineation cut.
  Worth one measurement (does GDocs' <w:p>-vs-line inference beat ours on a book?).

### 2026-05-30 — P0 calibration CAUGHT a blocker (representation is lossy) — user review
The calibration-first gate worked: user review of 13 cards found my render artifact
(and thus any gold built on it) is LOSSIER than the source. Root causes, all confirmed:
1. EMPHASIS not captured: ParaRow has no bold/italic. **Панкратиус:**, **Ответ от Творца:**,
   italic Surah quote, bold pseudo-headers all invisible. Golden set built on flattened
   text is INVALID — reviewer agreement is on a degraded artifact, not the work.
2. BOLD PSEUDO-HEADERS undetected: #68 "Место в литературе","Кому эта книга нужна" are
   heading=False style 'a' (bold paras used as section headers). They leak INTO verse
   runs → verse that starts AND ends with a header (absurd). No signal for them.
3. <w:br> GROUND TRUTH destroyed: #27 idx170 = ONE row br_count=3, four hard-break lines
   joined by spaces into one .text → merged into a single <p>. The most reliable
   lineation signal is being thrown away at read time.
4. BOUNDARY bugs: runs starting mid-sentence (#71 "Третья —…", #02 "которая открывается").
   User's point: obvious-to-a-human that a mid-sentence line can't open a block →
   "not sure? leave prose" (precision) would avoid it.
5. right-align present in ParaRow.align but not shown in render (— Панкратиус, signature).
USER PRINCIPLE (critical): a golden set is not golden if annotators saw the same plain
text the model did. Annotation artifact MUST preserve emphasis, <w:br> lines, headers,
alignment — i.e. be faithful to the DOCX, ideally the LibreOffice render itself.
⇒ P0 BLOCKED on representation. Fix before any 3-way gold:
  (a) split <w:br> into explicit lines (don't join); carry br as line boundaries.
  (b) capture emphasis (bold/italic) per line/run.
  (c) detect bold-pseudo-headers (short bold standalone para, style-as-header) as struct.
  (d) calibration artifact = the faithful docx_render (LibreOffice) page, NOT my
      reconstructed HTML — or reconstruct from the IR which already has emphasis/br.

### 2026-05-30 — external critic launched + eval scaffolding
- Launched independent first-principles ML critic (background, doubt-driven-development
  + api-and-interface-design skills) BEFORE building fixes — so it audits the
  representation/frame cold, can attack the frame itself, not review my homework.
  Deliverable: audit_external.md. Given character+territory, NOT a checklist (per user:
  don't over-constrain the thinker).
- Built scripts/metrics3.py (representation-INDEPENDENT, so audit-proof): macro-F1,
  run-boundary F1, WindowDiff, Pk, asymmetric verse P/R, by-book bootstrap CI. Self-test
  passes. This is the eval stack for the 3-way benchmark.
- HOLDING representation-dependent work (ir_view.py, calibration regen, 3-way gold)
  until the audit lands — don't rework what's under audit; let findings shape it.
- IR confirmed to carry what ParaRow dropped: Paragraph(inlines, align, empty, italic,
  lineation_group) + Emphasis(kind∈strong/em) + LineBreak. So per-line emphasis +
  hard-break lines + bold-header/speaker detection are all reconstructable from the IR.

### 2026-05-30 — EXTERNAL AUDIT + user's header point: ONE root cause (substrate is structure-blind)
Two findings, same disease — my research substrate (ParaRow/features) drops the
author's STRUCTURAL signals:

A) USER'S POINT (repeated 3-4x): real headers (#..######) + ***/<hr> are HARD
   BOUNDARIES; a run/verse-block may never cross or start/end on one.
   - features.py run-grouping DOES treat heading/*** as is_break ✓.
   - BUT sample.py windows are ±8 RAW paragraphs — NOT bounded by headers ⇒ calibration
     windows straddled boundaries (#71 preamble pulled in; #68 "verse started AND ended
     with a header"). THE BUG I kept showing. Mandatory fix: windows clip to nearest
     header/*** boundary.
   - AND di heading detector is narrow (Word styles only). Source reality: #27 has 1
     markdown header + 1 ***; #68 has 3; #30 has 246+136. Where the author used few real
     headers, the hard skeleton is sparse → bold-pseudo-headers are the only structural
     signal there (#27: 152 candidates). Priority: HARD boundaries (trusted) FIRST,
     pseudo-headers (inferred) SECOND.

B) EXTERNAL CRITIC (audit_external.md) — the keystone is INVERTED, not just dropped:
   - F1 CRITICAL: docx_adapter._paragraph_text (docx_adapter.py:296) turns <w:br> into a
     SPACE; ParaRow.text + features.py fill/wraps computed on the merged string. A 7-line
     <w:br> stanza → one row fill>2 wraps=True. Rubric says "wraps ⇒ PROVABLE prose" ⇒
     cleanest verse fed to judge AS PROOF OF PROSE. 5,098/5,526 (92%) of br>=2 rows
     flagged wraps=True (#27 56%, #05 60%, #07 46%, #21 31%).
   - F2 CRITICAL: gold + "inviolate" human seed contaminated — annotators saw the same
     flattened text+wraps flag. ~100-150 gold labels confidently WRONG, all one direction
     (verse->prose), on the cleanest cases. 8/8 reconstructed br>=3 prose labels were
     actually verse. Seed labels #05 idx47-62 (multi-br stanzas) HI-conf prose.
   - F3 HIGH: κ=0.97 and strata inherit the lie (br-verse can't enter hard_run/easyverse,
     lands in easyprose). So 0.814-vs-0.725, bootstrap, "diminishing returns" = poisoned
     substrate. DO NOT ship label-derived rule or rest claims on current gold.
   - F4 HIGH: zero bold/italic in rows ⇒ speaker-labels/pseudo-headers unrepresentable.
   - F5 MED: PRODUCTION adapt->normalize does NOT have the flatten bug (pandoc keeps
     LineBreak; #27 idx15 -> VerseBlock); but _para_lineated's 120-char/numbered gate still
     drops authored <w:br> stanzas in shipped ru.md (#05 idx58 joined to prose). Layer
     conflation in my notebook.

COMBINED FIX ORDER (substrate before any relabel/model/claim):
  1. Build IR-faithful substrate (ir_view.py): per <w:br> LINES (never joined) +
     emphasis (bold/italic) + alignment, from inline_lines(soft_break=False).
  2. Boundary skeleton FIRST-CLASS: hard = real headings (#..######) + ***/<hr> +
     table/list + right-align sig/epigraph; soft = bold-pseudo-header. Runs + sample
     windows clip to hard boundaries; never cross.
  3. Recompute features (fill/wraps/run_len) PER-LINE on the de-flattened text.
  4. DISCARD contaminated gold/seed/strata; re-derive on faithful substrate; re-label
     calibration round 2 on a faithful (emphasis+breaks+headers visible) artifact.
  5. Only then: 3-way benchmark with metrics3.py. Retract prior quantitative claims.

### 2026-05-30 — ir_view.py built (IR-faithful substrate) + verified
- Built scripts/ir_view.py: per-paragraph view from the typed IR (same path as
  production adapt). Preserves: per-<w:br> LINES (never joined), per-LINE emphasis
  (bold/italic) + wrap stat, alignment, and the BOUNDARY SKELETON.
- Boundary skeleton (the user's repeated point, now first-class):
  HARD = real Heading (any level) | ThematicBreak (***/<hr>) | Table | List |
         right-aligned Signature/Epigraph. A run NEVER crosses one.
  SOFT (inferred, only where author left no real headings) = bold-pseudo-header,
  bold-speaker-label. `segments()` clips runs to these; empties = stanza separators.
- VERIFIED keystone fix (#27 para172): "Так будет… / Оно уже началось. / Не через
  меч. / А через Узнавание." now = 4 separate bold non-wrapping lines, NOT one merged
  wraps=True prose row. CORPUS: of 5,231 multi-<w:br> paragraphs, 4,814 (92%) have ALL
  lines non-wrapping (clean lineated) — exact mirror of the audit's 92%-inverted claim.
  The keystone is recovered corpus-wide.
- VERIFIED boundary skeleton (#68): "Место в литературе" / "Кому эта книга нужна"
  detected as pseudo_header boundaries; the "Тем, кто…" list is a separate run after the
  header, not fused across it (the bug the user flagged 3-4x). A run no longer starts/
  ends on a header.
- Launched adversarial QA (Sonnet, different family vs circularity) -> qa_ir_view.md.
- NEXT (after QA): recompute features per-line on ir_view; rebuild calibration round 2
  on a faithful (emphasis+breaks+headers visible) artifact; DISCARD contaminated gold;
  3-way benchmark with metrics3.py.

### 2026-05-30 — adversarial QA of ir_view (agent planned only; lead executed)
- QA agent wrote a good plan (tasks/plan.md) but didn't run it. Lead executed the
  high-value checks. Two real defects found + FIXED:
  1) BlockQuote fell through case _ → ROLE_BODY (empty body candidate); 65/20-book
     sample. Now ROLE_BLOCKQUOTE (hard boundary); case _ → ROLE_OTHER (loud, hard
     boundary) — never silently body. (Exactly the audit's feared bug class.)
  2) ghost body paras (empty **  ** emphasis husks) → now ROLE_EMPTY.
- Verified across #27/#68/#30/#02/#13: 0 ghosts, dense index, 0 boundary-in-run,
  keystone per-line, mixed-bold→bold_all=False, line<=br+1. Findings in qa_ir_view.md.
- OPEN: pseudo-header/speaker-label PRECISION vs rendered pages (soft boundary, so
  recoverable) — measure in calibration round 2.

### 2026-05-30 — faithful calibration round 2 + Светозар (immersion annotator)
- Built calib3_faithful.py: cards from ir_view — <w:br> lines shown separately,
  **bold**/*italic* visible, boundary skeleton labelled, ↩W wrap flag. 12 cards. THIS
  is what annotators should see (round 1 was flattened → invalid).
- SVETOZAR agent: grounded in author's own #07 (autobiography) + #46 (testimony), then
  marked the 12 faithful cards as "разметка, как говорит Свет." 164 body lines. Forced
  3-way vocab for scoring. His principle: "does the break carry WEIGHT?" (not form/length).
- Светозар vs my form-analytic PROPOSED: agree 8/12; 4 diagnostic disagreements:
  * cf09 #05 numbered: I said lineated-prose, HE said verse — HE'S RIGHT. Faithful view
    shows each "1. …" is a bold opener + <w:br> FREE-VERSE STANZA. My wrong prior came
    from the FLATTENED rep that hid the stanza. Immersion beat form-analysis. ★
  * cf08 #02 dialogue: I said flowing, he said lineated-prose — HE'S more right; the
    turns carry intentional <w:br> enjambment ("Есть вещи… / А есть… / но они делают
    больше"). Lineated, prose-voice.
  * cf00 #13, cf11 #31: split — the genuine middle (litany/QA register), low conf both.
- KEY: a different ANNOTATOR PRIOR (sensory/weight-of-break) corrected the lead's
  form-prior on a real case. Validates the user's hypothesis: immersion annotator adds
  signal, not noise. Светозар becomes a 3rd independent annotator for the gold; his
  uncertainties map exactly onto the rubric's open questions (#71/#34/#02 register).
- GATE STATUS: rubric3 mostly SURVIVES on the faithful artifact; the round-1 "bugs" were
  representation, not rubric. Remaining rubric calls to FREEZE (need user/Светозар/lead
  consensus): litany-questions (#71) verse-or-lineated-prose; dialogue-enjambment
  (#02) confirm lineated-prose; numbered-<w:br>-stanza (#05) = verse (ruling: a bold
  opener + br-stanza is verse, not a numbered list).

### 2026-05-30 — OnlyOffice export = promising reference ORACLE
- ../pancratius-misc/legacy/onlyoffice_export/ has #02 (55MB w/ images), #27, #30.
- UNLIKE GDocs (two-space EVERY line, no disambiguation), OnlyOffice DISTINGUISHES
  <w:p> from <w:br>: continuation lines (soft line within a hard-break paragraph) get a
  leading \xa0 (nbsp); a NEW paragraph starts without it. #27: ~3754 non-nbsp (≈ our IR
  3600 body paras) vs 8168 nbsp continuation. So nbsp-prefix ≈ the <w:p>-vs-line oracle.
- Also PRESERVES emphasis (**bold**, *italic*) and HEADERS (#30: 246 headers, levels 2-3,
  incl. the "### N. <question>" QA structure). Keeps verse-y answers as separate lines.
- CAVEAT: messy — `** **` artifacts at break points, stray emphasis splits, nbsp is a
  quirky channel. Not drop-in. But a strong CALIBRATION ORACLE for the lineation cut
  (Stage 1) and a cross-check for headers/emphasis. Worth a fuzzy-alignment eval later.

### 2026-05-30 — LibreOffice headless HAS a native Markdown filter — and it's CLEAN
- `soffice --headless --convert-to md` (LibreOffice 26.2) works, seconds per book, FREE,
  deterministic, local, no LLM.
- Keystone #27 'Так будет…': <w:br> lines → TWO-SPACE breaks (the repo's verified
  encoding!); <w:p> paragraph boundaries → BLANK lines. So it DISAMBIGUATES <w:p> vs
  <w:br> cleanly — WITHOUT OnlyOffice's nbsp hack. 8165 two-space breaks, 0 backslashes.
  Emphasis preserved **bold**/*italic*.
- This is the 88%-hard LINEATION problem potentially SOLVED by a free tool. If it holds
  corpus-wide, Stage 1 collapses to "run LibreOffice + clean artifacts," and the only
  real ML work left is Stage 2 (register: lineated-prose vs verse), which is the small
  editorial call.
- CAVEATS to verify: (a) headers — only 1 in #27 (matches source reality, but check #30
  which has 246); (b) messy `**` emphasis at block edges (если ты — настоящий.** ); (c)
  does the <w:p>-vs-<w:br> blank-line distinction hold vs our IR <w:br> ground truth
  corpus-wide? (d) does it drop/alter any text (fidelity)?
- Launching agent: full-corpus LibreOffice md export + rigorous eval vs IR <w:br> ground
  truth + vs OnlyOffice, + fidelity check. If clean → reorders the whole plan.

### 2026-05-30 — Experiment B (Светозар e2e) result: FAITHFUL + correct emphasis
- INDEPENDENTLY verified fidelity vs FULL IR text (not the truncated card — my first
  check had a truncated-card bug, fixed): 12/12 PASS, zero added/dropped/altered words.
  e2e generation is SAFE here (the make-or-break gate for golden records).
- Markup QUALITY (e.g. #27 keystone): emphasis encoded CORRECTLY per-line
  (**…**␣␣ — open/close on own line, two-space OUTSIDE the span), so e2e does NOT
  have LibreOffice's `.**`-spanning-break bug. Lineation right (two-space breaks, blank
  stanza breaks, verse-block div w/ blank-after-<div>). Register thoughtful.
- NOTE: calib3_faithful.json `card` field is TRUNCATED (~600 chars); e2e reconstructed
  the FULL region faithfully anyway (passes vs full IR). Must un-truncate cards before
  using them as source-of-truth for scaled work / cascade comparison.
- EMERGING TRADE-OFF: LibreOffice = free/deterministic, lineation right, emphasis BROKEN
  (.** ), needs cleanup. e2e-Светозар = faithful, lineation right, emphasis CORRECT,
  register thoughtful, but LLM-cost + non-deterministic. Cascade (exp A) pending.

### 2026-05-30 — Experiment A (cascade) result + A-vs-B head-to-head
- Cascade: 12/12 FAITHFUL (verified vs full IR). Labels 50% verse / 34% flowing / 15%
  lineated-prose. Its uncertainties (#30 citation, #71 enumeration, #02 dialogue) = the
  same rubric open-questions.
- A-vs-B verse decision (% words in verse-block), divergences:
  * cf09 #05: cascade 0% verse vs e2e 78% verse. DECISIVE: identical LINEATION (same
    <w:br> breaks, bold openers, stanza blanks); differ ONLY on the verse-block wrapper.
    e2e is RIGHT — it's a numbered cosmogony/creation-litany in verse register; cascade's
    per-line classifier kept the "numbered=list" prior and missed it. = the holistic-
    context advantage of e2e the user hypothesized.
  * cf04 #30 (cascade100/e2e55), cf07 #02 (57/100), cf11 #31 (58/0), cf03 #71 (17/0),
    cf00 #13 (15/0): all REGISTER disagreements; LINEATION agrees.
- THE KEY FINDING across all 3 experiments: LINEATION is the agreed/solved substrate
  (LibreOffice, cascade, e2e all produce the same breaks). REGISTER (verse-block or not)
  is the ONLY thing in dispute, and it's the small editorial call. Confirms the frame:
  Stage 1 ≈ solved (even free via LibreOffice, modulo emphasis cleanup); Stage 2 is the
  real (small, precision-weighted) ML/editorial problem.
- e2e advantage: reads whole passage → catches register a per-line cascade misses (#05).
  cascade advantage: deterministic given labels, debuggable, cheap to assemble.

### 2026-05-30 — LibreOffice oracle VERIFIED at corpus scale → architecture reorders
Independently re-verified the 3 load-bearing claims (don't trust glowing agent reports):
- TEXT FIDELITY 99.96% (73 books, 381,893 IR lines); the 0.04% are image-alt/form-noise
  or cases LO is MORE faithful than our IR (preserves <w:br> the IR merged, e.g. #05 «аккорде»).
- LINEATION: 99.91% paragraph-boundary agreement; **ZERO merged <w:p> boundaries
  corpus-wide** (the failure that would kill an oracle — never happened). 345 splits, 344
  inside blockquote where OUR ir_view over-collapses. Spot-checked #27 (LO 3707 vs IR
  3597 paras) / #30 (3236 vs 3365) myself: same order, splits-not-merges. CONFIRMED.
- SILENT-FAILURE TRAP CONFIRMED: #02 + #46 produce ZERO md but soffice EXITS 0. A naive
  corpus run would skip them and report success. Root: abnormal ~2MB webSettings.xml;
  LO loader rejects the file (every filter). RECOVERABLE via pandoc DOCX→DOCX re-serialize
  pre-pass (our IR/pandoc path loads both fine). MUST check for output file, not exit code.
- Headings EXACT (#30 246/246, #71 323/323, #64 2411/2411). Beats OnlyOffice decisively.
- RESIDUALS: (1) emphasis `**`-at-break artifact systematic (1.19% lines, up to 23% in
  bold-dialogue books) — corrupts the BOLD channel not lineation; strip ** for reading
  text, re-derive emphasis from IR. (2) right-align/title lost (no md alignment) — overlay
  from IR. (3) base64 images bloat — strip data:image lines.

REORDERED ARCHITECTURE (the pivot):
  LINEATION (the hard 88%) = LibreOffice md (FREE, deterministic, corpus-wide, 99.9%).
  EMPHASIS + alignment/signature/title skeleton = IR overlay (LO loses these).
  REGISTER (verse-block or not) = the ONLY real ML/editorial problem left (Stage 2).
This collapses Stage 1 from "the long pole" to "run LibreOffice + IR overlay + clean."
e2e-Светозар (faithful 12/12, holistic register) = golden-record generator for Stage-2
training/eval. cascade/feature-model = distillation target. Cards must be un-truncated.

NEXT: (a) robust LO export wrapper (pandoc pre-pass for #02/#46; check output-file not
exit-0; strip data:image); (b) IR-overlay (emphasis re-pair fixing .**, alignment,
headings); (c) Stage-2 register benchmark on the faithful substrate w/ metrics3.py.

### 2026-05-30 — CORRECTION: LibreOffice does NOT solve the hard lineation problem
Re-examined what LibreOffice's md ACTUALLY does (don't trust my own prior summary):
- #25 (clean free verse): every line is its own <w:p> (br_count=0, 19807/19808 single-
  line) → LibreOffice BLANK-LINE-separates them ALL. It does NOT join into blocks.
- #27 (stanzas): each is one <w:p> with <w:br> (80% multi-line) → LibreOffice TWO-SPACEs.
- ⇒ LibreOffice's two-space-vs-blank IS EXACTLY the <w:br>-vs-<w:p> distinction. It
  faithfully preserves <w:br> (which our IR ALSO does) and renders <w:p>→paragraph.
- The 99.91%/zero-merge agreement is high because BOTH LO and IR are just TRANSCRIBING
  the same OOXML structure — neither INFERS. They agree by both being faithful.
- MY EARLIER CLAIM "lineation is a free solved problem" WAS WRONG. LibreOffice solved
  the EASY half (don't lose <w:br>); the IR already did that. The HARD 88% — "a run of
  single-line <w:p>: flowing-prose or intended-lineation?" — LibreOffice does NOT touch
  (it blank-lines #25's verse identically to how it'd blank-line short prose).
- WHAT LIBREOFFICE IS GOOD FOR (still real): a faithful, free, cross-checked READER
  (text fidelity 99.96%, headings exact, emphasis modulo the ** bug). A redundant
  ground-truth vs our IR — useful for validation, NOT a lineation oracle. OnlyOffice's
  nbsp channel was the only one that even ATTEMPTED <w:p>-vs-line inference, and it was
  worse/sparse.
- CONSEQUENCE for the ML flex: there is NO free lineation teacher to distill. The
  distillation target is therefore the SAME single problem as register, reframed:
  for a run of single-line <w:p>, classify flowing / lineated-prose / verse. The
  "teacher" for distillation must be e2e-Светозар / adjudicated gold (LLM/human), NOT
  LibreOffice. The honest ML story: distill an LLM annotator (or the F4 render-judge)
  into a 300ns model — NOT distill LibreOffice.

### 2026-05-30 — legacy/exports/25-{lo,oo}.md: OnlyOffice DOES group, LibreOffice doesn't
- Same #25 free-verse region, decisive contrast:
  * LibreOffice: blank line between EVERY single-<w:p> line (no grouping; faithful <w:p>
    →paragraph + <w:br> preserve only).
  * OnlyOffice: TWO-SPACE within a stanza, blank line ONLY at the author's empty
    paragraphs. It GROUPS consecutive single-<w:p> lines into stanzas, using empties as
    separators. IR confirms: #25 is runs of 1-line body paras split by empty paras —
    OnlyOffice read that structure; LibreOffice flattened it.
- ⇒ CORRECTION-TO-THE-CORRECTION: OnlyOffice ATTEMPTS the hard lineation grouping (run
  of single-<w:p> = one lineated unit); LibreOffice does not. My earlier dismissal of
  OnlyOffice (nbsp sparse/messy) judged the wrong axis — on grouping it's the better tool.
- OPEN QUESTION (decides if OnlyOffice is a usable lineation TEACHER): does it group only
  GENUINE lineation, or also two-space short PROSE runs (over-group)? If it groups
  everything short, it's the old over-detection sin in a new tool. Must test on a prose
  region (#13/#16) + the litany/enumeration ambiguous cases.
- The two 25-libreoffice files are identical except inside a base64 image blob (re-export).

### 2026-05-30 — FINAL reconciliation: neither exporter infers; they make OPPOSITE fixed assumptions
- Tested OnlyOffice on #30 PROSE: it two-spaces LONG WRAPPING PROSE sentences too
  ("Христиане верят, что в Иисусе… Сам Бог стал человеком…" → two-space). So OnlyOffice
  does NOT infer lineation — it two-spaces EVERY <w:p>, breaking paragraphs only at
  EMPTY paragraphs.
- The two tools = OPPOSITE fixed heuristics, neither is inference:
  * LibreOffice: every <w:p> → paragraph (blank line); <w:br> → two-space. ("Enter = para")
  * OnlyOffice: every <w:p> → line (two-space); empty para → para break. ("Enter = line")
- On #25 verse OnlyOffice is right BY ACCIDENT (it IS lineated); on #30 prose OnlyOffice
  is WRONG (flowing prose two-spaced). LibreOffice is the mirror: right on prose, wrong
  on verse-as-single-<w:p>. The TRUTH is per-run and NO exporter decides it.
- THEREFORE: there is still no free lineation teacher. BUT the two exporters BRACKET the
  answer: where LO and OO AGREE there's no decision to make (a <w:br> line, or an
  empty-bounded paragraph). Where they DISAGREE (a run of single-<w:p> lines: LO=N paras,
  OO=1 lineated block) = EXACTLY the ambiguous set we must infer. That's a cheap,
  corpus-wide AMBIGUITY DETECTOR / candidate-region finder for free — not a label, but a
  precise spotlight on the 88% that needs the model. Useful.
- Distillation teacher remains e2e-Светозар / adjudicated gold (NOT an exporter).

### 2026-05-30 — DIARY / quotable observation (LinkedIn hook for the distillation post)
Two office suites, same DOCX, opposite wrong answers — and that's WHY the ML is needed:
  - LibreOffice assumes "every Enter = a new paragraph" → right on prose, FLATTENS verse.
  - OnlyOffice assumes "every Enter = a line, blank = paragraph" → right on verse, RUNS
    prose together.
Neither infers; each hard-codes one half of the author's ambiguity. Where they AGREE
there's nothing to decide; where they DISAGREE is exactly the 88% a model must resolve.
The free tools don't solve the problem — they BRACKET it. (Hook: "I had two $0
deterministic oracles that were each 50% wrong in opposite directions. Their
disagreement was my training-set spotlight.")
This is also the justification for distillation: the only thing that gets lineation
RIGHT per-run is an LLM's judgment — too heavy for CI — so we bootstrap heavy→cheap→
distill into a sub-ms model. The disagreement set tells us where the model has to earn
its keep.

### 2026-05-30 — cheap-vs-heavy LINEATION gate: 0.756/κ.48 — but BOTH sides have problems
- DeepSeek v4-flash labeled 278 lines for $0.0048 (clean, resumable, production code in
  deepseek/). Distribution 35% flowing / 65% lineated.
- Per-paragraph agreement vs Светозар-heavy: 0.756, κ=0.48. Confusion is PERFECTLY one-
  directional: 40/40 disagreements are heavy=lineated, cheap=flowing (cheap NEVER calls
  flowing→lineated; it only under-fires lineated). Cheap's self-worry (over-firing) was
  BACKWARDS.
- SPLIT by paragraph type reveals BOTH sides are contaminated:
  * MULTI-LINE (<w:br>, TRIVIALLY lineated): 6/34 DeepSeek called flowing — a real CHEAP
    BUG, but deterministically fixable (<w:br> ⟹ lineated; never ask the model). Don't
    send these to any LLM.
  * SINGLE-LINE misses (35): many are #13 NARRATIVE DIALOGUE ("Олег медленно сел." /
    "— Система? — предположила Александра.") which on the PAGE (#13 = justified narrative
    novel) is FLOWING prose. Here DeepSeek is arguably RIGHT and the HEAVY reference
    (Светозар) is WRONG — he over-fired lineated on dialogue (reasoned register-first in
    calibration). So the gate ran on a CONTAMINATED reference.
- LESSON (again): verify the reference before trusting the comparison. The 0.756 conflates
  (a) a fixable cheap <w:br> bug and (b) heavy-reference noise on dialogue. Neither is the
  true cheap-model lineation quality.
- CORRECTED CHAIN for lineation:
  1. <w:br> paragraphs ⟹ lineated DETERMINISTICALLY (free, ~2.6% but trivial). Remove
     from the LLM's job entirely.
  2. The LLM (heavy or cheap) only decides SINGLE-LINE <w:p> runs: flowing vs lineated.
  3. The heavy reference must be RE-ADJUDICATED clean on lineation specifically (Светозар's
     calibration was register-first; need a lineation-first pass, or my visual adjudication
     on the page). THEN re-run the cheap gate.
- Also: per-paragraph is the right grain for lineation (a paragraph is flowing or lineated
  as a unit); DeepSeek's per-(idx,sub) collapses cleanly via "any sub lineated / multi-line
  ⟹ lineated."

### 2026-05-30 — goal-driven prompting WINS; and it exposes the heavy reference as the bad artifact
- Goal-driven sweep (NO mechanical rules, just the end-goal + author context):
  base(over-specified) all=0.780/κ.51 single=0.731
  g1_minimal           all=0.774       single=0.723
  **g2_goal (pure goal) all=0.811/κ.55 single=0.769  ← BEST**
  g3_goal_cue          all=0.805       single=0.762  (adding the 'wraps' cue HURT slightly)
  ⇒ user was right: describe the END, trust the model. Pure-goal beats over-specified AND
    goal+cue. 2026 prompting.
- The multi-line "<w:br> bug" was NEVER a cheap bug: all variants 0.971 on multi-line; the
  6 earlier "misses" were my per-paragraph collapse artifact. (Checked; corrected.)
- DECISIVE: inspected g2-vs-heavy single-line DISAGREEMENTS on the page. The CHEAP model
  is mostly RIGHT, the HEAVY reference WRONG:
  * #13 "Олег медленно сел." / "— Система? — предположила Александра." = narrative
    dialogue = FLOWING (it's the justified narrative novel). g2=flowing correct.
  * #02 "Мальчик молчал." / "— Ну?" = dialogue scene = FLOWING. g2 correct.
  * #30 "Задумайся:" / "Дальше." = framing/continuation markers we RULED non-verse.
    g2=flowing correct.
  * only ~2 reverse cases (#16, #31) are genuinely arguable.
- ROOT: Светозар's per-line CALIBRATION labels (variant A → my heavy ref) over-fired
  "lineated" on narrative because he reasoned REGISTER/voice, not LINEATION. His e2e
  MARKUP was excellent; his per-line labels are the wrong artifact for a lineation ref.
  ⇒ THE HEAVY REFERENCE IS CONTAMINATED, not the cheap model. 0.811 UNDERSTATES g2.
- CONSEQUENCE: the cheap-vs-heavy gate is currently measuring against a bad ruler. Must
  re-establish a clean LINEATION heavy reference (lineation-specific prompt, page-grounded,
  multi-line=lineated deterministic) BEFORE the gate means anything. Likely the cheap g2
  model is already good enough — but prove it against a clean ref.

### 2026-05-30 — multi-model LINEATION bench (g2 goal prompt, single-line cut, sample n=85)
Models: deepseek-v4-flash, qwen3.7-max, gpt-4o-2024-11-20, openrouter/owl-alpha(free),
moonshotai/kimi-k2.6; + gpt-4o with Светозар-roleplay prompt.
- Cross-family CONSENSUS: 76% unanimous across the 4 neutral g2 models; pairwise κ
  0.65–0.81. The lineation cut is learnable + agreed with a goal prompt (signal
  independent of the contaminated Светозар ref).
- Agreement with 4-model g2 consensus (single-line): **deepseek 0.988** (the CHEAPEST is
  the consensus CENTROID), owl 0.918, kimi 0.949(partial), qwen 0.882, gpt4o 0.847,
  gpt4o_svet 0.812.
- ⇒ DeepSeek-v4-flash is not just cheap-enough — it's the MOST consensus-central labeler.
  Ideal for the bootstrap: the corpus-labeler is also the most representative. owl-alpha
  (free, but provider logs) is a strong cross-check.
- Светozar ROLEPLAY on gpt-4o: WORST on lineation (0.812, lowest κ everywhere). It mis-saw
  #25 CLEAN free verse as 'flowing' — over-immersion adds aesthetic interpretation where
  the structural cut just needs judgment. HONEST result: roleplay (the original co-author
  model) does NOT help the lineation sub-task. (It may still help REGISTER later — untested.)
- Kimi-k2.6 unreliable for strict JSON-schema (3/8 cards returned null content) —
  disqualified on engineering, not judgment.
- DECISION: DeepSeek-v4-flash + g2 goal prompt is the corpus lineation labeler. STILL need
  a CLEAN heavy reference (not Светозар per-line) to report a final gate number — but the
  cross-family consensus already shows cheap ≈ the field. Next: clean ref OR proceed to
  label corpus + distill (consensus is strong enough to bootstrap).

### 2026-05-30 — VERIFIED: calib3.py lineated-prose rendering bug (agent report TRUE)
- calib3.py:111 labels short non-wrap lines "prose" in BOTH modes; gen_candidates
  render_html only knows verse→verse-block else→<p>. So "lineatedprose" mode rendered
  NORMAL PARAGRAPHS, not two-space hard-break lineated prose. The round-1 A/B was
  verse-block vs <p>, NEVER lineated-prose vs verse. The middle class was never rendered.
- SCOPE of impact (checked):
  * round-1 calib3 cards: affected — but ALREADY discarded (flattened-emphasis bug).
    No new damage.
  * calib3_faithful.py (round-2): NOT affected — it's a TEXT card (faithful source shown,
    annotator labels), no verse-vs-lineated HTML A/B. Dodges the bug.
  * lineation bench (DeepSeek/Qwen/gpt4o/owl): NOT affected — ran on lineation_task.json
    (ir_view per-line + wrap flag), never the buggy renders. κ/consensus stand.
- REAL gap it exposes: gen_candidates render path CANNOT express lineated-prose (only
  verse | <p>). Must fix before any HTML-rendered 3-way rubric calibration. = same gap
  as the IR question below.

### IR design Q: add a neutral LineatedBlock (lineated-prose) alongside VerseBlock?
- Current IR (nodes.py:213): VerseBlock{stanzas, role} only; lineated-prose has NO
  first-class node — it'd be emitted as... bare paragraphs? (the render gap above).
- VERDICT: YES, worth it. The frame now has THREE outputs (flowing / lineated-prose /
  verse), verse⊂lineated. A first-class lineated block makes the encoding transform
  uniform + obvious: "lines within a LineatedBlock → each line + two trailing spaces +
  emphasis; flowing → normal <p>." Without it, lineated-prose either (a) reuses
  VerseBlock with a role flag, or (b) is faked as <p>s (the bug). 
- Cleanest design: a generic lineated block carrying the SAME stanza/line structure as
  VerseBlock + an optional REGISTER class: e.g. LineatedBlock{stanzas, register∈
  {verse-block, lineated-prose, None}}. VerseBlock becomes register="verse-block".
  This matches the lineation/register SEPARATION (the plan's core): lineation = the
  block + its line structure (high-stakes, where the two-space breaks live); register =
  the class attribute (low-stakes, cosmetic, an LLM error here can't corrupt lineation).
  An empty/None register = lineated-prose (bare two-space breaks, no wrapper).

### 2026-05-30 — markdown-mode vs structured-output (DeepSeek, lineation, sample n=85)
Tested user hypothesis: does WRITING MARKDOWN elicit better lineation intuition than
per-line JSON labels? Same model (deepseek-v4-flash), same goal, same sample.
- md-mode alignment coverage 100% (every body line mapped to an output block via
  hard-break-presence). struct vs md κ=0.600, agree=0.800.
- vs 4-family consensus (ds/qw/4o/owl): struct 0.988, **md 0.812**. md-mode diverges ~19%.
  CAVEAT: the consensus IS the structured panel → biased toward structured by construction;
  fair read = "md-mode takes layout liberties," not "structured is ground truth."
- md-mode errors are bidirectional LAYOUT liberties:
  * #02 narrative ("Анфиса замерла." / "— А это не опасно?") → md wrongly LINEATED
    (got poetic when laying out). 
  * #71 enumeration ("Одна — древняя.") → md MERGED into flowing (ran prose together).
- CONCLUSION: for the NARROW lineation cut, structured per-line is cleaner/more consistent
  than free markdown; markdown medium added interpretive layout liberties. Hypothesis NOT
  confirmed for lineation. BUT: (a) yardstick is struct-biased; (b) this is lineation-only —
  md/e2e likely still wins for full REGISTER markup (Светозар-e2e beat cascade on #05),
  where holistic layout IS the task. So: structured for the lineation labeling chain;
  revisit md/e2e for register.

### 2026-05-30 — IMAGE boundary DROPPED (user catch) — two compounding bugs
User: "Анфиса замерла." then a PICTURE then "— А это не опасно?" in the DOCX — they're
NOT a contiguous run. Verified:
- #02 raw document.xml has 44 <w:drawing>; pandoc emits 0 Image nodes for #02 → the IR
  (adapt) has 0 ImageBlock/ImageInline. The image — a HARD boundary — is GONE before
  ir_view sees it. Between "Анфиса замерла." and "— А это не опасно?" sits 1 <w:drawing>
  (raw), shown in IR as just an empty paragraph.
- NOT uniform: #01 keeps all 73 images, #02 DROPS all 44, #12 drops 1. So pandoc's docx
  reader fails on a SPECIFIC drawing type (#02's anchored/floating or non-<a:blip>
  drawings) — not all images. #02 (our narrative/dialogue anchor) is the one that loses them.
- TWO compounding bugs:
  1. UPSTREAM: pandoc drops certain <w:drawing> → boundary lost before any of our code.
  2. OURS: ir_view never modeled IMAGE as a boundary role anyway (no IMAGE in
     HARD_BOUNDARY_ROLES) — same under-representation as headers. Even when pandoc KEEPS
     an image (#01: 73), our segments() wouldn't fence on it (ImageBlock → ROLE_OTHER now
     does fence; ImageInline inside a paragraph does NOT).
- IMPACT on findings:
  * The #02 "md-mode wrongly lineated narrative ('Анфиса замерла.' / '— А это не опасно?')"
    conclusion is CONTAMINATED — that region was missing a hard image-boundary, so the
    model saw a corrupted segmentation. Can't attribute the error to md-mode there.
  * Other #02-based calls (dialogue cards cf07/cf08) likely also affected wherever an
    image fell inside the window. The lineation bench AGGREGATE (κ/consensus across many
    cards) is mostly intact, but #02 cards specifically are suspect.
- FIX (before trusting any #02-touching result):
  1. recover dropped images: read <w:drawing> positions from raw OOXML and inject an
     IMAGE boundary at the right paragraph index (pandoc won't; do it from the zip like
     docx_inspect already reads document.xml). 
  2. add ROLE_IMAGE to ir_view HARD_BOUNDARY_ROLES; fence segments on it.
  3. re-derive any #02/#12-touching calibration + re-check the md-vs-struct #02 cards.

### 2026-05-30 — CORRECTION (user was right): committed md HAS the image; CURRENT adapt drops it
- User: site renders the image (localhost .../#часть-2) ⇒ it's in the markdown. TRUE:
  committed ru.md has `![Иллюстрация](./images/5534dc21bfd7.jpg)` between "Анфиса замерла."
  and "— А это не опасно?" — all 44 #02 images present.
- My pandoc test was right for the WRONG framing. Settled by running the REAL current
  adapter: `adapt()` on #02 → ImageInline=0, ImageBlock=0, 0 media extracted. pandoc emits
  0 Image AND 0 Figure nodes for #02's drawing type even with --extract-media.
- ⇒ The COMMITTED ru.md was produced by an EARLIER/different import path that kept these
  images; the CURRENT adapt() DROPS them. A re-import today would lose 44 images in #02
  (and #12's 1). This is a real PRODUCTION REGRESSION in the import pipeline, independent
  of our classifier — worth flagging to whoever owns import.
- For OUR work: ir_view is built on current adapt → it never saw the image boundary. So:
  (1) the #02 md-mode "wrongly lineated narrative" example is CONTAMINATED (missing image
      fence) — retract that specific reading; the aggregate bench stands.
  (2) STILL must add ROLE_IMAGE to ir_view boundary skeleton AND recover dropped <w:drawing>
      from raw OOXML (the committed md proves the positions are recoverable), so the
      research substrate matches the real (committed) document structure.
- Meta-lesson reinforced (3rd time: headers, <w:br>, images): enumerate the FULL hard-
  boundary set from raw OOXML once; stop discovering omissions one bug at a time.

### 2026-05-30 — IMAGE boundary bug (user caught) — FIXED in ir_view + invalidates md-mode test
- USER CAUGHT: #02 "Анфиса замерла." → [IMAGE] → "— А это не опасно?". An image-only
  Paragraph (idx1022, one inline ImageInline, no reading text) was lumped into ROLE_EMPTY
  and SILENTLY DROPPED. So the scene beats had no boundary between them.
- TWO bugs:
  1. ir_view: image-only paragraph → ROLE_EMPTY (a soft stanza break) instead of a HARD
     image boundary. FIXED: new ROLE_IMAGE (+ handle ir.ImageBlock); image-only paragraph
     and ImageBlock are hard boundaries a run cannot cross. Verified on #02 (image now
     between the beats); 27 image boundaries recovered in a 12-book sample (were empties).
  2. md_mode_bench: STRIPPED all structural (non-body) lines before sending to the model,
     so even detected boundaries never reached it. ⇒ md-mode fused "Анфиса замерла." +
     "— А это не опасно?" because it was never shown the image. 
- ⇒ The md-mode-vs-structured comparison was UNFAIR (md starved of boundary context the
  structured run had via the card). The md-mode "layout liberties" finding is therefore
  PARTLY an artifact of my harness, not the model. Must RE-RUN md-mode WITH structural
  boundaries (image/heading/***) shown as markers before concluding md vs structured.
- Note: structured run used lineation_task.json which DID include role!=body context
  lines — but did IT carry image boundaries? lineation_task built image-only paras as
  role from ir_view BEFORE this fix → they were "empty" there too. So BOTH runs likely
  missed images; need to rebuild lineation_task.json from fixed ir_view and re-bench.

### 2026-05-30 — md_mode test was DOUBLY invalid (user: structural markers ARE the attention signal)
- card_to_source (md_mode_bench.py) does `if ln["role"]=="body"` → SKIPS every structural
  line. It DID emit **bold**/*italic* (good) but DROPPED headings, ***, images, speaker-
  labels — i.e. the markdown STRUCTURAL MARKERS.
- USER'S POINT (correct, and deep): those markers (#, ***, image syntax, blank-line para
  boundaries) are NOT decoration — they are the tokens a markdown-trained model's attention
  learned to key on (heading resets context, *** separates, image breaks a scene). Stripping
  them feeds FLAT text with no structural anchors, then asks for layout — starving exactly
  the md-native intuition the test was meant to elicit.
- WORSE than just unfair: the STRUCTURED run got role tags ([ctx:heading],[ctx:empty]) as
  explicit context; md-mode got LESS structure while doing the MORE holistic task. Backwards.
- ⇒ The "structured > markdown-mode" conclusion is INVALID on two counts: (1) images dropped
  (earlier), (2) all structural markers stripped. RETRACT it. A fair md-mode test must give
  the model FULL markdown structure (real # headings, ***, image placeholders, blank-line
  paragraph boundaries, emphasis) and let it lay out — i.e. feed it markdown, get markdown.
- This strengthens the case to RE-RUN md-mode properly: it may well WIN once it can see the
  structure its attention expects.

### 2026-05-30 — ADVERSARIAL QA #2 scoreboard (verified by lead) + fixes
Independent Sonnet QA; lead VERIFIED the high-impact claims before accepting. Full
report: qa_adversarial_2.md. Verdicts:
- F1 CRITICAL (verified): consensus was CIRCULAR. Leave-one-out: DS 0.859, owl 0.871,
  4o 0.835, qw 0.812. ⇒ RETRACT "DeepSeek is the consensus centroid (0.988)". Honest:
  DS & owl near-tied top-2; owl marginally ahead. Choosing cheap DS still defensible
  (cheapest, top-2), but the 0.988 evidence was self-inflated by ~13pp.
- F2 CRITICAL (already known, NOT yet fixed): lineation_task.json + calib3_faithful.json
  are STALE (built pre-image-fix; book02 index shifted; idx roles wrong; no image role).
  Confirmed: no "image" role in task. MUST rebuild from fixed ir_view. Blocks all bench
  numbers on image/heading-bearing books.
- F3 CRITICAL (extends my retraction): md-mode unfair via TWO mechanical causes —
  (1) ANY-subline=lineated vs structured's majority-vote; (2) structural markers stripped
  (already logged) + role tags given to structured only. The 0.837-vs-0.731 gap is
  mechanical, not quality. RETRACT "structured > md-mode".
- F4 HIGH (verified, FIXED): ellipsis … (+ ; ,) missing from pseudo-header terminal
  filter → 14 misfires/30 books ("5. Панкратиус…"). Fixed; now 0.
- F5 HIGH (verified, FIXED): segments(soft_boundaries=False) was a NO-OP (inner loop
  hardcoded BODY/EMPTY, ignored `bounds`). Fixed to consult bounds; flag now works.
- F6 HIGH: multi-line override inflates all-key agreement 2-4pp → report SINGLE-LINE
  numbers as headline (already my practice in later analyses; make it consistent).
- F7 HIGH: cross-subset compare (DS n=164 vs 4o n=104) invalid; on same 8 cards DS=0.731
  < 4o 0.769. ⇒ only compare on identical key sets. RETRACT any cross-n claim.
- F8/F10 MED (F10 FIXED): #27 idx176 2-line-flowing forced lineated by override (a real
  override limitation — multi-line ≠ always lineated if a line wraps); speaker_label on
  multi-line first line → FIXED (single-line only).
- F9 MED: partial-bold section heads (one bold word among plain) never detected — soft
  boundary under-fire; defer (soft, recoverable).
- F11 MED: heavy_ref ← gpt4o-Светозар and pred_gpt4o_svetozar ← same model; anti-circular
  in practice (sv scores lower) but disclose.
- F12-F14 LOW: dead code (md_mode:108), module-global mutation, shallow nested emphasis.

RETRACTIONS (conclusions that DO NOT stand): "DS consensus centroid 0.988"; "structured
> markdown-mode"; any cross-different-n model comparison. SURVIVES: cross-family
consensus is real (LOO 0.81-0.87); cheap≈expensive (Qwen not better); Светozar-roleplay
worst on lineation; goal-prompt > over-specified.

REBUILD CHECKLIST (batched, now that QA is in):
  1. [done] ir_view F4/F5/F10 fixes.
  2. rebuild lineation_task.json + calib3_faithful.json from fixed ir_view (image role,
     correct indices, full structural markers incl. headings/***/image).
  3. re-run bench FAIRLY: same key set, single-line headline, same collapse rule both
     modes, md-mode gets FULL markdown structure. owl included.
  4. clean heavy reference (page-grounded lineation, not Светozar register-first labels).
  5. F8: fix the multi-line override (multi-line ⇒ lineated UNLESS a line wraps).

### 2026-05-31 — substrate rebuilt + fair re-bench launched + reader-LLM experiment
- ir_view fixes (F4 ellipsis, F5 soft-boundary no-op, F10 speaker-label, +image role)
  applied & verified. Rebuilt calib3_faithful (13 cards now incl. cf12 #02 Анфиса which
  SPANS an [IMAGE] boundary — validates the image fix end-to-end) + lineation_task.json
  (308 body lines, image role present). build_lineation_task.py persists the rebuild.
- region() now clips at SECTION boundaries (heading/***/table/list/sig/epigraph) but
  SPANS image/quote (shown as marker) — so a calibration window can display an image
  mid-region instead of truncating at it.
- FAIR re-bench harness rebench.py: 4 INPUT FRAMINGS × models, all with full structural
  skeleton, same key set, same collapse rule, single-line headline:
  perline (control) | oo_dense (de-lineate OnlyOffice's all-2-space prior) |
  lo_sparse (re-lineate LibreOffice's all-paragraph prior) | mdmode (free markdown).
  Models: flash, pro (deepseek-v4-pro, cheap), owl (free). [qwen/gpt4o reuse earlier.]
  Matrix running in background (~12 cells). Scorer: score_rebench.py (LOO consensus,
  single-line, same collapse — addresses QA F1/F6/F7).
- READER-LLM experiment (reader_llm.py): label-free quality signal — an LLM READS a
  rendered candidate and reports friction (where rhythm breaks / litany run together /
  flowing line chopped), overall∈{glides,minor,jarring}. To run on mdmode outputs.
- NEXT after matrix: score fairly; run reader-LLM; then ADVERSARIAL QA against the new
  harness + outputs (per user) before trusting any number.

### 2026-05-31 — BUG (user caught): oo_dense/lo_sparse framings snitched JSON, not markdown
- The "correct this markdown" framings (oo_dense de-lineate, lo_sparse re-lineate) FEED
  markdown but ASK FOR per-line JSON labels (parse_struct, structured=True). That is NOT
  the user's hypothesis (edit markdown → get markdown); it's the per-line JSON task with a
  markdown-flavored prompt. INVALID for the framing comparison.
- It also CAUSED the key-fidelity garbage (lo_sparse_flash 318>308, lo_sparse_owl 291<308):
  forcing a fixed key list out while reasoning over markdown makes the model lose keys.
- Only mdmode actually outputs markdown + reads lineation back from structure (read_back_md).
- FIX: oo_dense/lo_sparse must OUTPUT EDITED MARKDOWN (add/remove two-space breaks), then
  read lineation back by diffing input-vs-output structure — same read_back_md path.
  Rerun just those cells. The mdmode + perline cells are valid; keep them.

### 2026-05-31 — md-in/md-out (mdio.py) WORKS — user hypothesis validated
- Built mdio.py: TRUE markdown-in→markdown-out (no JSON). Model edits the markdown
  (oo_dense: remove prose breaks; lo_sparse: join lineated runs; free: lay out), lineation
  read back from OUTPUT STRUCTURE (block has 2-space break → lineated).
- TEST lo_sparse/owl: 308 lines, unaligned=1, mean fidelity=0.977. vs the JSON-costume
  version's broken key counts (291/318). 
- TWO wins over JSON-mode: (1) KEY-STABLE — no keys to invent/drop, lineation IS the
  structure; (2) FAITHFUL — 0.977, model barely altered words.
- QUALITATIVE (owl #13 lo_sparse): correctly RE-lineated the bold oracle-stanza ("«Вы
  стали каналом. / Вас было трое. …") into hard-break lines while leaving narrative +
  dialogue ("Олег медленно сел." / "— Система? — предположила Александра.") as separate
  flowing paragraphs. The exact judgment the JSON-costume couldn't elicit.
- ⇒ md-out sidesteps the key-fidelity failure mode ENTIRELY (no schema to violate) AND
  gives the model its native medium. Strong support for the user's framing. Next: run
  mdio across oo_dense/lo_sparse/free × flash+owl, compare to per-line JSON fairly.

### 2026-05-31 — rebench matrix scored (11 cells) — read with caveats
- CAVEAT: common single-line keys=ONLY 26 (the broken oo/lo JSON cells with 291/318
  mangled keys shrank the intersection). Numbers are on a thin slice; oo/lo here are the
  INVALID JSON-costume versions. Treat as directional, not final.
- ROBUST signals:
  * perline (per-line JSON): flash vs owl κ=1.000, LOO 1.000 — extremely consistent
    across models. The control is stable.
  * mdmode (free markdown): cross-model κ 0.68–0.84, LOO 0.92 — also consistent.
  * mdmode vs perline agree: flash κ=0.755, owl κ=0.920 — the two VALID framings concur.
  * oo_dense/lo_sparse JSON-costume: κ 0.23–0.32 — GARBAGE, confirms they're broken.
- ⇒ The real oo/lo comparison needs the MDIO (true md-in/md-out) versions, NOT these.
  mdio lo_sparse/owl already gave 308 lines / 0.977 fidelity (clean). Run mdio across
  oo_dense/lo_sparse/free × flash+owl, then compare on the FULL key set.

### 2026-05-31 — READER-LLM experiment WORKS (label-free quality critic)
- reader_llm.py on mdmode/flash: 13 regions → 3 glides / 10 minor / 0 jarring, 49
  friction points. It READS the rendered markdown and reports where it snags.
- It catches REAL grouping errors independently of any gold:
  * cf07 #02: "диалог и реплики слиты в сплошной абзац" — caught mdmode-flash MERGING
    dialogue turns into one paragraph (a real lineation error).
  * cf04 #30: "Цитата сплошным блоком" + "Задумайся: слишком короткое/резкое" —
    independently rediscovered our manual rubric rulings (Surah=prose, markers≠verse).
  * cf02 #25: flagged the dense bold name-list ("Абсолют. Бог. Творец. Источник.").
- BONUS: surfaces things label-eval CAN'T — a source GRAMMAR error (cf09 "волна(ж.р.)—
  несущее(ср.р.)") and a MARKDOWN bug (cf11 stray "###" level switch).
- ⇒ Validates the user's idea: a reader-LLM is a useful label-free critic; its friction
  correlates with lineation/register defects AND adds orthogonal signal (rhythm, grammar,
  markup). Candidate as a CI quality-gate / candidate-ranker (the F4 render-judge, lighter).
  CAVEAT: temperature 0.3, single reader; would need calibration (does "minor" vs "jarring"
  track real defect density?) + cross-reader agreement before trusting as a metric.

### 2026-05-31 — FULL framing comparison (135 single-line keys, all valid cells)
Fixed mdio post() (empty-choices IndexError; owl rate-limit flakiness — works w/ spacing).
All 6 mdio cells + perline + mdmode scored on FULL common key set (135 single-line, vs 26
before). Same collapse rule, same keys.
- CROSS-MODEL consistency (reproducibility of a framing across flash/owl[/pro]):
  RB_perline κ=0.832 (best) ≈ MDIO_lo_sparse κ=0.803 >> MDIO_oo_dense .489 ≈ RB_mdmode
  .496 >> MDIO_free .348 (worst). ⇒ per-line-JSON and "re-lineate sparse" are the most
  REPRODUCIBLE; free-form generation is the least.
- CROSS-FRAMING agreement is LOW (κ 0.38–0.71): the SAME model gives materially different
  lineation depending on input framing. Framing is NOT cosmetic — it changes the answer.
  * MDIO_lo_sparse ≈ RB_perline (0.713) ≈ RB_mdmode (0.691): "correct the sparse prior"
    converges with the per-line control.
  * MDIO_free is the OUTLIER (κ .38–.46 vs all; cross-model .348): free "lay out from
    scratch" diverges most + least stable → takes the most liberties.
- lineated_RATE swings 0.41 (free) → 0.64 (mdmode): framing shifts ~23pts of the call.
- HONEST REVISION: the earlier md-mode enthusiasm is COMPLICATED — free-md is the least
  reproducible framing. The STABLE, convergent pair is per-line-JSON and lo_sparse-correct
  (model corrects LibreOffice's sparse prior). That pairing (LO export → LLM re-lineate)
  is also the most deployable: LO gives the sparse baseline free, LLM only fixes it.
- fidelity (md-out): oo_dense .960, lo_sparse .948–.977, free .960 — all high; md-out is
  faithful regardless of framing.
- NOTE: still no clean external GOLD; this is cross-method CONSISTENCY, not accuracy. A
  framing can be consistently WRONG. Need the page-grounded reference to call a winner.

### 2026-05-31 — adversarial QA #3 scoreboard (verified) + the GROUND-TRUTH gap
QA #3 (qa_adversarial_3.md). Verified high-impact claims myself:
- S2 (CLEARED, the deepest worry): JSON read-back vs md read-back give IDENTICAL results
  (κ=1.000) → the framing-κ differences are REAL framing effects, NOT scoring-machinery
  artifacts. The comparison is valid on that axis.
- S1: my 5 framing κ values (.348/.489/.496/.803/.832) independently reproduced — exact.
- F1 CRITICAL (verified): oo/lo JSON-costume cells returned 0-based sequential idx, not
  absolute → only 18/26 "common" keys real. The "κ GARBAGE" call was right but on a
  near-accidental sample. (mdio is the fix; those cells are dead.)
- F2 CRITICAL (verified): models ECHOED THE PROMPT into md output (free_flash cf12 starts
  with the full English prompt) + truncations. 15/78 cards <0.95 fidelity. My mdio prints
  only the MEAN, hiding per-card failures → corrupts both fidelity claim AND read-back
  labels for those cards. Need per-card fidelity gate + strip echoed prompt.
- F3 CRITICAL (verified earlier): the F8 fix (multi-line⇒lineated UNLESS a line wraps) was
  documented but NEVER applied to score_rebench.py:34 — 6 narrative-dialogue pars (book02
  par1019, 7 lines all wraps=False all-flowing votes) force-labeled lineated. Real bug.
- F5/F6/F7/F8 (verified plausible): lineated-rate span is 33pp not 23; "***" body line
  permanently mislabeled; rebench-mdmode vs mdio-free differ on 4 cards' bold rendering;
  [IMAGE]+2space false-lineated structurally.
- F9: reader-LLM uncalibrated (known).

THE CORE GAP (user's point, now front-and-center): everything above is CROSS-MODEL/CROSS-
FRAMING CONSISTENCY. A framing can be consistently WRONG. We have NO accuracy/precision/
recall because we have NO trusted ground truth. κ=0.83 means models agree, not that
they're right. MUST build a page-grounded gold (render-slice) to measure accuracy — that
is what we optimize against. Prompt confound RULED OUT: all framings share the same
goal-driven GOAL string (perline is NOT over-specified); only output-format + the explicit
WRAPS token differ.

### 2026-05-31 — PAGE-GROUNDED GOLD built (202 lines) + lead adjudicated the disagreements
- Agent built gold_lineation/anchors.jsonl: 202 body lines, 116 lineated/86 flowing, 12
  anchor regions, rendered via `pancratius docx render-slice`. notes.md per region.
  (book02 docx won't load in LO — agent added render_clean.py read-only helper to rebuild
  a pristine document.xml for a faithful page; book02 paras carry no align/indent so faithful.)
- vs my earlier human SEED: 74% agree (32/43 shared). Disagreements clustered → I RENDERED
  & judged each on the PAGE myself:
  * #05 idx49-57 (numbered cosmogony): PAGE shows clear short broken lines + stanza gaps =
    LINEATED. New-gold RIGHT; my old seed (flowing) WRONG — seed predates the faithful
    substrate that exposed the <w:br> stanzas. (Matches Светозар-e2e's earlier catch.)
  * #13 idx737-741 (the "Если придёт момент" vow): PAGE shows only the 3 italic indented
    quoted lines (739-741) are lineated; 737-738 ("Он был заложен…/Он содержал три строки…")
    are JUSTIFIED FLOWING PROSE. Old seed RIGHT; new-gold WRONG (agent over-extended the
    vow into surrounding prose — a boundary error).
- LESSON: neither the old seed nor the agent-gold is authoritative alone; THE PAGE IS.
  Both had errors in opposite directions; rendering resolved each. ⇒ reconcile: take
  new-gold but CORRECT #13 737-738→flowing (and audit other agent boundary calls similarly).
- This is the real GROUND TRUTH foundation. Once reconciled, score every framing×model
  against it for ACCURACY/PRECISION/RECALL — the thing we actually optimize against.

### 2026-05-31 — ACCURACY vs page-grounded gold (130 lines, 63 lineated) — THE answer
First real accuracy/P/R/F1 (not consistency). Single-line, vs anchors_reconciled.
  RB_perline_owl    acc .920 macroF1 .920 lin-P/R .90/.92  ← BEST
  RB_perline_pro    acc .909 macroF1 .908
  MDIO_lo_sparse_flash .852  (best md-based)
  RB_perline_flash  .841
  RB_mdmode_pro     .830
  MDIO_free_flash   .818 (lin-R only .72)
  MDIO_lo_sparse_owl .784
  RB_mdmode_flash   .727 (lin-R .97, P .62 — over-fires)
  MDIO_oo_dense_flash .682
  MDIO_free_owl / RB_mdmode_owl .60 (R 1.0, P .53 — call everything lineated)
  MDIO_oo_dense_owl .545
- WINNER: PER-LINE JSON classification (owl/pro), ~90-92%, BALANCED P/R. Only framing >90%.
- REVERSALS (consistency did NOT predict accuracy — user was right):
  * md-mode/free-markdown is NOT better — mid-to-bad (60-82%). Earlier md enthusiasm
    overturned by ground truth.
  * the "most cross-model-consistent" framings (perline κ.83, lo_sparse κ.80) — perline IS
    also most ACCURATE (.92), but lo_sparse is consistent yet only .78-.85: consistency ≠
    accuracy. A framing can agree-across-models and still be wrong.
  * mdmode/owl: 100% recall, 53% precision = calls everything lineated. Over-fires.
- DEPLOYABLE ANSWER: per-line JSON, owl (free!) or pro, ~90%. owl free-tier is rate-limit
  flaky but cheap-to-free + most accurate here. 
- CAVEAT: gold n=130 single-line over 12 anchor regions (stratified but small, anchor-
  chosen not random); owl's .92 vs pro's .91 within noise. Need a larger RANDOM-stratified
  gold for a tight CI + by-book. But the framing verdict (per-line >> md-out/free) is robust.
- This is why ground truth mattered: 6 turns of consistency benchmarking pointed at md-mode;
  one page-grounded gold set flipped it. Optimize against the PAGE, not model agreement.

### 2026-05-31 — gold-pipeline QA (BEFORE running, per user) — caught it FUNDAMENTALLY broken
QA-before-run vindicated: the page-reader harness would have produced GARBAGE gold.
render-slice itself is FINE (works on book27); render_clean.py is a workaround ONLY for
book02 (whose DOCX won't load in LibreOffice — the abnormal webSettings.xml, same silent
exit-0 issue). But the packaging was broken:
- C1 CRITICAL: PNG (render-slice, raw-OOXML/read_rows index space) and structure (ir_view,
  post-adapt IR-block space) are in DIFFERENT INDEX SPACES, drift by region-dependent
  offset (b13 736 vs 739; b27 172 vs 170). Reader sees page of span A, labels line-list B.
  Invalidates every label.
- C2 CRITICAL: multi-match anchors — "Олег медленно сел" 2 hits, "Тем, кто" 7; render
  spanned 7 pages/140 paras vs a 25-line structure. Anchor substring is not unique.
- H1: render_clean drops w:drawing → book02 image (idx1022 hard boundary) vanishes,
  fuses "Анфиса замерла."+"— А это не опасно?" — page contradicts structure.
- H2: label-space mismatch — gold 2-way {flowing,lineated} vs reader prompt 3-way
  {flowing,lineated-prose,verse} vs seed {verse,prose,struct}. No collapse → κ poison.
- M1: structure leaks harness's OWN inferred role (pseudo_header/speaker_label) → biases
  reader. M2: structure() silent-midpoint fallback vs render raises — asymmetric.
- SOUND (verified): gold KEYS byte-for-byte aligned to ir_view space (18/18); <w:br>/emph/
  wraps faithful. The GOLD is fine; the reader-INPUT packaging was broken.
REDESIGN (before any reader runs):
  1. ONE index space: drive BOTH the render and the structure from ir_view's block order
     (render the exact ir_view [lo,hi] block range, not render-slice's read_rows --around).
  2. Unique region anchors (use idx range, not substring; or first-unique).
  3. book02: re-serialize its DOCX (pandoc DOCX→DOCX, proven) so render-slice loads it —
     drop render_clean. Keep the image visible.
  4. Reader labels 2-way {flowing,lineated} to match gold (collapse verse→lineated).
  5. Strip inferred roles from the reader's structure (show only hard OOXML: heading/***/
     image/empty/<w:br>); don't leak the harness's guesses.

### 2026-05-31 — index divergence ROOT CAUSE settled (user + QA both partly right)
Tested directly: ir_view idx vs read_rows(render-slice) idx for same paragraph text:
  #25 SAME (1=1), #71 SAME (453=453), #27 DIFFER +2 (170 vs 172), #13 DIFFER -3 (739 vs 736).
- ROOT CAUSE: ir_view indexes by position in adapt()'s PANDOC-AST block list; render-slice
  indexes by RAW body <w:p> ordinal. Pandoc coalesces (multi-<w:p> blockquote→1 block,
  heading, list) and normalize/adapt drops (TOC, empty husks). So they align ONLY in pure-
  paragraph stretches; diverge by a running ±offset after any coalesce/drop.
- So BOTH diagnoses hold: (a) effectively different index spaces [QA's C1, the consequence];
  (b) render-slice --around ALSO renders one continuous slice across MULTIPLE matches
  [user's point: "Олег" 2 hits→[1276..1415] 7 pages; "Тем,кто" 7 hits]. Two distinct bugs.
- DECISION: don't reconcile two spaces. Gold is already in ir_view space (byte-aligned).
  So the RENDER must be driven from ir_view: map ir_view's [lo,hi] block range → the source
  <w:p> ordinals → render-slice --range those. And anchors must be UNIQUE (idx range, not
  substring). Build a careful ir_view-block→source-<w:p> map; re-QA before running.
- (Aside: render-slice --around should refuse multi-match / render per-hit — a real CLI
  bug, but we sidestep it by using --range from the mapping.)

### 2026-05-31 — index fix RESOLVED (verified) + book02/multi-match fixed upstream
Verified the other agent's two claims TRUE:
- book02 DOCX source is now fixed → `render-slice --book 2` renders directly. DROP
  render_clean.py (and its image-distortion bug H1). 
- render-slice --around now REFUSES multi-match ("Олег…" → error: matched 2 paragraphs,
  use --range). C2 fixed upstream.
- IR carries NO source-<w:p> provenance (Paragraph has no ordinal field). The agent's
  "propagate source spans through the adapter/IR" = real PRODUCTION work (adapter change,
  library boundary). DEFER it — right eventual fix when lineation/register ship, but NOT
  needed for gold + premature for a research artifact.
- VERIFIED LIGHT FIX (scratch-only, no adapter change): ir_view BODY paragraph → source
  <w:p> ordinal by TEXT MATCH is 100% (13: 2324/2324, 27: 3168/3169, 05: 234/234). So:
  gold pipeline maps ir_view region [lo,hi] → source <w:p> ordinals → `render-slice
  --range LO:HI`. Same DOCX paragraphs the structure lists; one source of truth; robust.
FINAL gold-pipeline design (rebuild + re-QA before any reader):
  1. region = ir_view block-idx RANGE (unique, not substring).
  2. structure = ir_view lines for that range (gold space, byte-aligned).
  3. render = map those body paras → source <w:p> ordinals (text match) → render-slice
     --range (book02 included now; no render_clean). PNG ⟺ structure same paragraphs.
  4. reader labels 2-way {flowing,lineated} (matches gold); strip inferred roles
     (show only hard OOXML structure: heading/***/image/empty/<w:br>); no model preds.
  5. validate readers vs page-verified gold (κ gate ≥0.8) BEFORE scaling.

### 2026-05-31 — SourceSpan exists (other agent) — RIGHT solution but PARTIALLY populated
- SourceSpan(start,end) now on Paragraph/Heading IR; populated with REAL source <w:p>
  ordinals matching render-slice space (verified #13 739..739, #27 170..170, #02 1021..1021).
- BUT coverage is INCOMPLETE: #13/#27/#02 ~99-100%, **#05 only 3% (10/267)** — ordinary
  numbered paragraphs (block49 "1. В начале…") have span=None. The agent's SourceSpan
  populates some paths (verse-merges, the #30 smoke case) but NOT the plain-paragraph path
  in #05. So it's not yet a complete render↔structure bridge.
- FLAG to other agent: SourceSpan needs to cover the ordinary-Paragraph adapter path
  (#05 numbered points get None). Until then it's sparse.
- DECISION: hybrid mapping in the gold pipeline — use block.source_span when present
  (authoritative), fall back to ir_view-text → source-<w:p>-ordinal match (verified 100%)
  when None. Report per-region coverage + which method. Robust to the partial rollout.
- ir_view now exposes p.src_start/src_end (from SourceSpan); pipeline renders
  render-slice --range over the region's min(src_start)..max(src_end), text-match-filling
  the None paras first.

### 2026-05-31 — SourceSpan FIXED by other agent — re-measured fresh, now solid
Other agent fixed the populator (regen invalidated my stale numbers). FRESH measurement:
  #05 98% (was 3%), #02 97%, #13/#25/#27/#30/#34/#68/#71 = 100%. ZERO over-broad spans in
  any gold book (the "ir=9..288" over-broad bug they mention doesn't touch our regions).
- VERIFIED spans are CORRECT (not just present): span.start == render-slice ordinal —
  #05 55..55, #02 1021..1021, #27 170..170, #13 739..739, all ✓MATCH.
- ⇒ The render↔structure bridge is SOLID: ir_view.src_start/src_end → render-slice
  --range. ~98-100% via SourceSpan; text-match fallback only for the ~2-3% stragglers
  (#05/#02 merged-verse). ir_view already exposes src_start/src_end.
- Gold pipeline UNBLOCKED to rebuild on this verified bridge. Then re-QA before any reader.

### 2026-05-31 — gold-pipeline RE-QA: CLEAN, cleared for reader pilot
Rebuilt gold_pipeline on the verified SourceSpan bridge + gold-derived ir-idx ranges.
Independent re-QA (code-reviewer agent): ALL 5 prior criticals CONFIRMED fixed; NO new
CRITICAL/HIGH; explicit "sound to run the reader pilot."
- C1 proven on hardest case: 3 index walks increment identically; r03_b05 ir≠src by +6
  (gold idx46=src ord52), PNG starts exactly at idx46, matches structure line-for-line.
  Full reproduction: all 12 packages byte-identical rebuilt. SourceSpan bridge load-bearing.
- C2/H2/M1 confirmed: regions match gold lines exactly; 202 gold rows → 1 body line each,
  zero cross-region dupes; only soft roles collapse to neutral "bold-line"; no answer leak.
- OPEN (none block pilot): MED-1 images render as blank GAP (slice_docx strips w:drawing)
  → indistinguishable from blank on page; structure marker is correct. FIX: tell reader
  structure markers are boundary authority. MED-2 "span-cov 11/49" is paras-vs-lines units
  artifact (every body para HAS a src_start). LOW-1 text-match fallback first-match-on-dup
  (dead now, 100% SourceSpan; harden before scale). INFO gold=202 rows not 201.

### 2026-05-31 — page-reader PILOT: did NOT cleanly pass — and exposed a TASK-DEFINITION flaw
3 Sonnet page-readers (PNG+structure) vs page-verified gold, 130 lines:
  reader A κ=0.725 (agree .862), B κ=0.574, C κ=0.544. Majority-vote κ=0.544.
  inter-reader B-C κ=0.927 (readers AGREE with each other), A diverges → readers are
  CONSISTENT, just consistently different from MY gold.
- Systematic DIRECTIONAL bias: readers over-fire "lineated" — near-0 false-neg on verse
  (catch 62-63/63) but 18-30 false-POS (gold=flowing → reader=lineated). OPPOSITE of the
  text-only DeepSeek run (which under-fired). Seeing the PAGE makes short lines look
  list-like → readers call them lineated.
- Concentrated on: #02 narrative-dialogue/meditation (nearly all 21 flowing lines), #71
  enumeration (441-447), #13 737-738 (the lines I hand-corrected to flowing). Excluding
  the 2 known-ambiguous regions only reaches .825/κ.64 — still not clean. Over-fires even
  on HI-conf gold (#02 dialogue: 13 hi-conf flowing lines → readers lineated).
- ROOT CAUSE (honest, looked at #02 page myself): this is NOT a reader-quality failure —
  it's a TASK-DEFINITION flaw. The #02 dialogue IS short lines, one per sentence, with
  stanza gaps + spare cadence on the page — stylized narrative: PROSE VOICE but LINEATED
  LAYOUT. The 2-way {flowing,lineated} forces "is the break intended?" (readers: yes,
  correct) to collide with "is it verse?" (me: no). The readers answered LINEATION right;
  I answered REGISTER. This is exactly the `lineated-prose` MIDDLE CLASS we designed in
  3-way and then collapsed for the 2-way lineation gate.
- IMPLICATION: don't blame readers / don't rubber-stamp my gold. The binary lineation cut
  is itself ill-posed for stylized-narrative. Two fixes to weigh:
  (a) define lineation strictly as "did the author intend a break here" (physical/
      structural) — then #02 dialogue is LINEATED (readers right), and verse-vs-prose is
      the SEPARATE register stage. This matches the lineation/register frame!
  (b) keep 3-way and don't collapse.
  Either way: my gold mislabeled stylized-narrative lineation as "flowing" by conflating
  it with register. The readers may be MORE right than my gold on the lineation question.

### 2026-05-31 — KEY REFRAMING (user forced the 2-step separation): lineation is ~visually moot here
Re-rendered ambiguous cases as LINEATION-ONLY (② = hard breaks kept, PLAIN <p> style, NO
verse register — only difference from ① is lines-joined-and-wrapped vs lines-kept-separate).
- #02 and #71: the two columns are NEARLY IDENTICAL. Because this author writes ONE <w:p>
  per line (Enter per line), and the new indented-prose CSS already renders each <w:p> as
  its own indented line — so "flowing" vs "lineated" changes rendering ONLY for the rare
  multi-<w:br> paragraph (join-or-not, e.g. #02 "А есть—которых нет,/но они делают больше").
- ⇒ For single-<w:p>-per-line content (the vast majority), the flowing/lineated LABEL
  barely changes the rendered page. The pilot's κ "failure" was largely measuring a
  distinction that's near-invisible on the page. Lineation matters structurally (exports,
  and gating register) but is NOT the lever for "looks good."
- The thing that ACTUALLY changes the page is REGISTER (verse-block gear-shift) = Stage 2.
  That's where reader disagreement is real and consequential.
- CONSEQUENCE: stop pouring effort into the flowing-vs-lineated gold/pilot as if it were
  the quality driver. Re-aim at: (1) lineation = mostly "preserve the <w:br> breaks +
  decide join for multi-line paras" (a near-deterministic, low-stakes call), (2) REGISTER
  = the real classification problem (verse vs lineated-prose), judged on the rendered page
  where it visibly matters. The 2-step frame the user insisted on is vindicated: Stage 1
  is small/structural; Stage 2 is the substance.
- (Caveat: lineation still matters where the author DID use <w:br> — #27/#05 stanzas,
  #34 — there join-vs-split is real. But that's the minority + it's the high-confidence
  easy case anyway.)

---

## 2026-05-31 — wrapper contract + verse-has-no-signal + the page↔candidate preview

**Contract changed (user + other agent).** Lineation is now an EXPLICIT wrapper, not an
implicit per-line double-space:
  prose      → flowing `<p>`
  lineated   → `<div class="lineated">` (stanza `<p>`s, lines joined by `<br>`)
  verse      → `<div class="lineated verse">` (additive italic + left-rule register)
This resolves the `<p>…<br>…</p>` collision (in-paragraph w:br vs lineated block looked
identical) and PRESERVES verse ⊂ lineated as a pure "+register" subset (both rungs share
stanza capability; only register differs). Double-space EOL is kept inside `.lineated`
for `<br>`/export, but boundaries are now carried by the div, so a lineated block can span
stanzas — which implicit double-spaces structurally could not.

**Corrected terminology (locked):** prose / lineated / verse. (Not flowing / lineated-prose.)

**md-out is NOT ruled out — it's better now.** Earlier I said the wrapper "kills md-out."
Wrong, and the user caught it. Explicit `<div class="lineated">` fences are *more*
expressive than per-line double-spaces (can group across stanzas), and the judge's job
becomes "wrap runs in fences, don't rewrite text" — which makes the fidelity check trivial
(output text == input text modulo fences) and removes the drop/add-words failure mode that
plagued mdio. Div-fenced markup and structured-span JSON are now near-isomorphic; choose on
reliability, not representability.

**Verse has NO docx signal (investigated, verdict C).** `promote_verse_register()` in
`pancratius/ir/normalize.py` decides verse AFTER lineation, from section-title regex
(`_VERSE_SECTION_TITLE_RE`: Посвящение / Молитва / Псалом / Предисловие от Творца / …) +
line-length thresholds. No w:pStyle, no systematic italic, no spacing marks verse. ~21.8k
verse blocks in committed md, 98/104 files — all heuristic. So:
  → The STRUCTURAL classifier targets TWO classes only: {prose, lineated}. Recoverable.
  → Verse stays a SEPARATE downstream editorial pass (S7), driven by title-semantics +
    thematic context — NOT a third learnable class for an IR-feature model.

**The page↔candidate preview helper** (`scripts/astro_preview.py` + `html_shot.mjs`).
Renders, in one image: the DOCX page (LibreOffice via render-slice, ground truth) next to
the SAME passage laid out as Astro would under each candidate class — using the REAL site
CSS (src/styles/{tokens,global,prose}.css), light theme, via headless Chromium. Presets:
prose + lineated; `--candidates JSON` for explicit block typings (e.g. gold / a model's).
SourceSpan bridge makes the docx span and the candidate structure cover the same paragraphs.
  Usage: uv run --with pillow python astro_preview.py --book 02 --around "Ты странный" --ctx 14

**What it immediately showed (reverses the earlier "visually moot" finding):** under the
OLD bare-`<p>`+double-space encoding, prose vs lineated rendered ~identically — that was an
artifact of the wrong CSS (wrong because the lineation MODEL was wrong: a cascading failure
that only surfaced because someone said "I can't tell the difference" instead of coding
blind). Under the `.lineated` wrapper they are DECISIVELY different:
  - #02 ir[836..864] («Ты странный»): clearly PROSE — sentences that merely break at the
    author's Enter; the lineated column over-segments into ugly one-line-per-sentence.
  - #49 ir[0..22] («Предисловие от Светозара»): the inverse — PROSE flattens a liturgical
    preface into a wrong paragraph; LINEATED restores the lines; VERSE adds the voice.
So lineation (prose vs lineated) is a real, visible, consequential decision again — the
reader-pilot κ "failure" was measuring a real distinction with a real over-firing problem,
not an invisible one.

**Metric decision:** gate on BLOCK-level (boundary-F1 + exact-block-match), not per-line
accuracy. One misplaced boundary is visually catastrophic but barely dents per-line acc
("9/10 lines ok = 90%" hides an ugly orphan line). Per-line κ stays as diagnostic only.

**Bedrock gold is SUSPECT.** `anchors_reconciled.jsonl` (202 lines / 9 books) was labeled
under the old per-line flowing/lineated assumption (and was once contaminated). Do NOT
trust it at block grain / new terminology — re-audit region-by-region with the preview
helper (docx page is truth; read the page when text+picture is ambiguous) before it anchors
anything.

---

## 2026-05-31 (cont.) — from-scratch BLOCK gold builder + adversarial QA + pilot

Built a fresh page-grounded gold pipeline (scripts/gold_build.py, gold_merge.py) — NOT
inherited from the old per-line anchors. Two classes only: prose / lineated (verse is a
downstream editorial pass, no docx signal). Steps: frame (enumerate all 10,643 hard-bounded
body runs over 75 books on ir_view) → sample (stratified, oversample the ambiguous middle) →
package (preview composite: docx page beside prose+lineated candidates + per-line structure)
→ reader panel → merge (unanimous gold + needs_human + audit queue).

**Frame strata (honest):** physics resolves only two corners — wrap_prose (1103) and
hardbreak (510). The ambiguous MAJORITY is short non-wrapping lines: mid_gap 3740 +
mid_flat 4990 = ~82%. That ~82% is *why* a content model is needed — wrapping physics alone
punts on it. mid_gap median run = 46 lines (long, gap-separated).

**Adversarial QA review (independent agent, evidence-led) — found and FIXED:**
- C1 (CRITICAL): block boundary-F1 was inflated to ~1.0 by all-prose regions (zero internal
  boundaries → vacuous 1.0). Fix: compute F1 only over boundary-bearing regions + report the
  count. Pilot now shows boundary-F1=0.333 over 3 boundary-bearing regions — the honest hard
  signal it was hiding.
- C2 (CRITICAL): sample silently dropped runs >26 body paras → excised 70% of mid_gap (the
  longest, most-likely-lineated passages — the costly class to miss). Fix: keep all runs,
  WINDOW long ones at package time (centered), log the windowing burden per stratum.
- H1 (HIGH): panel circularity — shared Claude-reader bias enters unanimous gold and never
  surfaces (needs_human holds only disagreements). Fix: emit a deterministic 15% audit
  sample of UNANIMOUS regions (audit_queue.json) for human spot-check.
- H2 (HIGH): κ uninterpretable under prevalence skew (19/20 agree → κ≈0 paradox; zero-variance
  → κ=1.0). Fix: report PABAK = 2·po−1 as primary, flag κ degenerate when pe≥0.99.
- M2: exact-block-match was documented but unimplemented → implemented (15/17 in pilot).
- L4: frame/gold files now end with newline.
- DEFERRED (acknowledged, not blocking): M1 list/table text dropped from reader CONTEXT
  (ir_view gives those Paras no lines) — affects adjacent context, not label targets;
  M3 partial in-line emphasis collapses to whole-line. Fix in the full build.

**Pilot result (2 readers: careful + rhythm Sonnets, 277 lines / 17 regions / 17 books):**
- per-line unanimous 262/277 = 94.6%; κ=0.887, PABAK=0.892. Verified key coverage 100%
  (no silently-dropped disagreements — the merge math is sound; agent SELF-REPORTS of
  per-region counts were inaccurate, the JSONL is the truth).
- unanimous by stratum: hardbreak 100%, mid_flat 100%, mid_gap 88.4%, wrap_prose 90.7%.
- exact-block-match 15/17; the only genuine 2-reader splits:
    g02_b71 (4 lines): closing BOLD rhetorical questions — careful=lineated (parallel
      self-examination list), rhythm=prose (they WRAP, read expository). Crux: does a bold
      parallel question-series count as lineated?
    g13_b43 (11 lines): the WRAPPING anaphoric parable LEADS ("Царствие Небесное подобно
      X…") — careful=prose (topic sentences), rhythm=lineated (litany stanza-openers). They
      AGREE the non-wrapping inner lines are lineated. Crux: anaphoric litany whose lead
      lines happen to wrap.
- These two crux types ARE the hard boundary of the task: wrapping + parallel/anaphoric.
  Everything else the panel nails.

---

## 2026-05-31 (cont.) — bug fixes (M1/M3) + QA round 2 + 5-model panel

Per user: fixed the deferred bugs (don't carry suspicion) and added two NON-Claude vision
readers to break panel circularity.

**M1/M3 fixed in ir_view.py:**
- M3: inline_md / inline_html preserve PARTIAL and nested emphasis (a line with one bold
  word no longer renders all-bold or all-plain). Line gains .md (Markdown, for the listing)
  and .html (escaped HTML, for the candidate render). Verified 0 word-content mismatches
  over 31,481 real body lines; strike/sup/sub mapped (M-A).
- M1: ROLE_LIST/ROLE_TABLE Paras now carry their item/row text (were empty) → reader sees
  list/table CONTENT as context; astro_preview renders real <ul>/<table>.

**QA round 2 (independent adversarial agent) — found and FIXED before any panel spend:**
- C-A (CRITICAL): gold_merge emitted a SINGLE surviving vote as "unanimous" gold — once
  external readers drop lines, garbage enters gold. Fix: min_votes = max(2, n_readers−1);
  a line needs that many votes AND agreement to be gold; unanimous-but-thin → needs_human
  (reason "thin-votes"); disagreement → "split". Regions with any non-gold line excluded
  from the audit sample.
- H-A (HIGH): openrouter_reader greedy `\[.*\]` dropped ALL labels if the reply had any
  prior array (reasoning). Fix: scan all balanced top-level arrays, take the largest valid
  list-of-dicts. Verified on fenced/prefixed replies.
- H-B (HIGH): composite downscaled to 1600px left Cyrillic ~530px/column — harder than the
  Claude readers' full-res. Fix: max_w=2600 (~870px/col).
- H-C (HIGH): block metrics compared readers over different key sets when coverage differed
  (a skipped key bridged a block → fake disagreement). Fix: partition both readers over the
  shared-key INTERSECTION.
- Also fixed: package() now catches SystemExit (ap._src_span raises it for span-less stale
  regions like the replaced book 46) so one bad region doesn't abort the batch.
- Verified SOUND (not just claimed): M3 word-invariant, M1 population, C2 windowing math,
  PABAK/κ guard, C1 boundary-bearing filter, M2 exact-block-match, audit determinism,
  key never logged.

**Panel models:** Claude Sonnet ×2 (careful, rhythm) + Claude Opus (me) + x-ai/grok-4.3 +
google/gemini-3.1-pro-preview (gemini-pro-latest is not a valid OpenRouter id; 3.1-pro is
the current Pro). Grok + Gemini read the same composite image + structure as the Claude
readers — external models can't share Claude's bias, the strongest guard for H1.

---

## 2026-05-31 (cont.) — 5-model panel result (the diverse-panel payoff)

Panel: careful, rhythm (Claude Sonnet), opus (Claude), grok (x-ai/grok-4.3), gemini
(google/gemini-3.1-pro-preview). All 5 labeled all 277 lines (gemini needed max_tokens
8192 — at 4096 it truncated and dropped 3 whole regions; raw replies now saved to
data/gold_block/raw/).

**The headline finding — shared-bias was real.** Adding two NON-Claude readers collapsed
the agreement the 2-Sonnet run reported:
  2 Sonnets:  94.6% unanimous, κ=0.89   (inflated — shared Claude bias, the H1 risk)
  5 diverse:  66.8% unanimous (5-0)      (honest inter-annotator agreement)
Pairwise κ: Claude–Claude clusters high (careful~rhythm .85, rhythm~opus .83, grok~opus
.74) but Gemini is the outlier (careful~gemini .36) — it over-lineates (188 lineated vs
careful's 148). Grok sits between. This is exactly why the external models were worth
adding: they expose the task's real ambiguity instead of echoing Claude.

**Vote distribution:** 5-0 = 185 · 4-1 = 57 · 3-2 = 35.
  - Majority gold (≥4/5 agree, decisive): 242/277 = 87.4%. (gold_merge --min-agree 4)
  - The 4-1 dissents are systematic and correctly overridden by majority: careful (too
    conservative) is the lone prose dissenter on the g13 litany (21 lines); gemini (too
    lineated) is the lone dissenter on g08 prose (11 lines).
  - Genuine 3-2 ties = 35 lines in FOUR regions — the irreducible crux:
      g07 numbered logia (14), g10 dash-bullet enumerations (13), g05 exegetical
      parallels (4), g02 bold rhetorical questions (4).

**hardbreak only 71% unanimous** — explicit <w:br> is NOT a clean lineation signal: the
author uses shift+enter for prose bullets/mixed discourse too (g10, g17). So even the
"high-confidence" physics corner needs the content model.

**Block boundary-F1 low (0.09–0.41 pairwise)** — readers agree on per-line labels far more
than on WHERE blocks begin. Boundary placement is the hard part of the structural task,
exactly as the block-grain gate was designed to expose.

**Pilot gold artifacts (data/gold_block/):** gold_block.jsonl (242 majority lines, each with
margin + recorded dissenter), needs_human.json (35 tie lines, 4 regions, with every vote +
composite path), audit_queue.json (2 fully-gold regions for human spot-check of consensus).

**Ceiling implication:** the distilled model's realistic ceiling on THIS task is ~the
majority-consensus rate; the 35-line 3-2 core is genuinely ambiguous even to 5 strong
diverse readers and should be treated as a soft/abstain zone, not forced. Precision-first
(prose default) remains right.

---

## 2026-06-01 — 9-model panel + adjudication app

Panel grown to 9: careful, rhythm, opus (Claude); grok, gemini, mimo (xiaomi/mimo-v2.5),
minimax (minimax/minimax-m3) — all VISION; owl, deepseek (deepseek-v4-flash) — TEXT-ONLY
(read the structure listing, no image; closer to what the distilled model will have).
Coverage ~100% (owl 274, minimax 276, rest 277). Reader balances span a real spectrum:
prose-pole mimo 136p / minimax 120p / careful 129p ↔ lineated-pole gemini 188L / deepseek
186L / opus 183L. Gemini & mimo are the κ outliers (opposite poles).

Strict merge (gold_merge --min-agree 7, min_votes 8): gold only if ≥8 voted AND ≥7/9 agree;
5-4/6-3/thin → human (NOT majority-to-gold, per user).
  236/277 = 85.2% consensus gold (77 prose / 159 lineated).
  by stratum: wrap_prose 100%, mid_flat 96%, hardbreak 81%, mid_gap 77%.
  9-vote distribution: 9-0=163, 8-1=28, 7-2=43, 6-3=24, 5-4=16.
  Contested (<7 agree) = 40 lines in 5 regions: g07 logia(14), g10 dash-bullets(13),
  g13 parable-leads(8), g17(3), g05(2). The irreducible hard core across 9 diverse models.

CORRECTION logged earlier: <w:br> IS a clean "keep the break"=lineated signal (deliberate,
non-habitual). 100% of hardbreak disagreements sit on <w:br> paras whose CONTENT is
prose-ish (g10 bullets, g17 exposition); readers split because the brief made them judge
register ("is it verse?") not break-preservation. Fix the criterion + auto-label <w:br>
(not yet done — pending).

Adjudication app (adjudicate/adjudicate.html, built by a frontend agent): self-contained,
file://-safe, per-line + single-choice modes, hints hidden by default, exports
responses.json. Verified end-to-end via Playwright (task loads, renders, no console errors).
build_adjudication.py emits assessment_task.json from the contested panel lines (composite
image as data-URI, contested lines as rows, 9-vote tally as hidden hint). Current task:
40 contested lines / 5 regions.

NEXT: human adjudicates → responses.json = ground truth → rank all 9 readers by accuracy/
precision/recall vs the human calls (answers "which LLM is closest to the book"); the 236
consensus + the 40 resolved = the pilot gold anchor.

---

## 2026-06-01 — Codex review, rule fix, human adjudication, reader ranking

Codex review of the run — ALL claims verified TRUE:
- needs_human was 41 = 40 split + 1 THIN (g17:150, 7-0 all prose, owl+minimax absent).
- coverage 274×9 / 2×8 / 1×7 votes (owl missed 3, minimax 1) — "near-complete", not complete.
- my prose vote-shape summary dropped the missing-vote margins (8-0,7-1,7-0).
- <w:br> auto-label must be PER-PARAGRAPH (br_count>0), NOT stratum-level (stratum only
  means the region contains a hardbreak somewhere). Correct and important.

RULE FIX (real bug Codex surfaced): gold_merge had a separate min_votes=8 gate that fired
BEFORE the agreement check, shunting the 7-0 unanimous-thin line to "thin" though zero
readers dissented. Fixed: min_agree is now also the coverage floor (need >= min_agree votes;
with min_agree=7 that already prevents 1-vote gold). Re-merge: 237 gold + 40 split, matching
the adjudication task exactly.

HUMAN ADJUDICATION (user, via adjudicate.html): all 40 contested → LINEATED. Criterion the
user actually applied: does the candidate match the docx page + stay readable. Notes: prose
rendering MANGLES numbered/dash lists into one wrapped line (g07/g10) → must be lineated;
g05 lineated visually closer to docx. (Side note logged: g17 "Запрос:"/"Ответ от Творца:"
pseudo-headers render with too big a gap vs docx — possible lineation/CSS issue, low pri.)

READER RANKING vs truth (rank_readers.py; truth = 237 consensus + 40 human-lineated):
  reader     contested-acc  overall-acc  lin-P  lin-R
  gemini        87.5%         88.1%       0.94   0.89   (lineated pole: best recall, worst prec)
  deepseek      67.5%         95.3%       1.00   0.93   (BEST all-rounder; cheap TEXT-ONLY target)
  opus          60.0%         94.2%       1.00   0.92
  grok          60.0%         92.4%       0.99   0.90
  owl           42.5%         91.2%       0.99   0.88
  mimo          40.0%         77.6%       0.99   0.70
  minimax       27.5%         84.4%       1.00   0.78
  rhythm        25.0%         86.3%       0.98   0.83
  careful        5.0%         78.7%       0.97   0.72   (prose pole)

KEY CAVEAT: the 40 human-truth lines are ALL lineated (disagreements were one-directional —
conservatives under-lineating). So contested-acc = lineated-recall and rewards lineated bias;
overall-acc + precision are the unbiased signals (deepseek best). SCALE-UP REQUIREMENT: the
human gold MUST include contested cases resolving to PROSE, else ranking + ceiling are
one-sided. DeepSeek-flash result (95.3%, P=1.00, text-only) is the strong distillation lead.

OPEN: (1) <w:br> per-paragraph auto-label (br_count>0 → lineated) — deferred, do after we
balance the gold; (2) scale-up sampling must capture contested-prose; (3) g17 pseudo-header
gap CSS note.

---

## 2026-06-01 — SCALE gold (gold_block2), 7-reader panel

Big-change batch (dropped the <w:br> auto-rule entirely after a 3rd leak found by an unprimed
verifier — medium-fill non-wrapping prose was still auto-mislabeled and auto bypassed the
audit; everything is now VOTED), --set required on all tools (a stray 2-reader merge had
clobbered the pilot gold_block to 229/2-0 — restored to 237), per-region try/except in the
reader (one bad PNG no longer aborts a whole model run), tile long runs (cap 6/run), TOC
stratum (23 real TOCs corpus-wide, 0 false positives), comment cleanup (no ticket-IDs/history).
Four adversarial QA passes total this session; each found a real bug.

Scale run on gold_block2: frame (10,639 runs/75 books) → sample --n 40 (35 books, balanced
toward prose-risk) → package (62 regions after tiling, 1057 polled lines).

Panel: 7 readers — careful (Sonnet) + grok, gemini, owl, deepseek, mimo, minimax (OpenRouter;
4 vision, owl+deepseek text-only). rhythm (2nd Sonnet) DROPPED — writing ~1057 JSONL lines in
one response twice hit the 32k output-token cap (careful squeaked under). Coverage ~96-100%
each (mimo lowest at 957). Spread: prose pole careful 418P / minimax 438P / mimo 368P ↔
lineated pole grok 833L / owl 805L / deepseek 801L / gemini 796L.

Merge (--min-agree 5, ≥5/7): 824/1057 = 78.0% consensus gold; 233 → needs_human (204 split +
29 thin-votes) over 32 regions; audit_queue = 4 fully-gold regions. Adjudication task built:
adjudicate/assessment_gold_block2.json (32 regions, 233 lines, 13.1 MB).

NEXT: human adjudicates the 233 (or a prioritized subset) → rank_readers on gold_block2
(needs --set + the 7 tags; scale truth is NOT all-lineated, so it's a balanced rank unlike
the pilot) → then distill.

---

## 2026-06-02 — scale adjudication results + two systemic findings

Human adjudicated all 235 (204 splits + 31 audit) with 23 rich notes. Labels: 197 lineated /
38 prose (84% lineated). Notes saved: adjudicate/responses-lineation-adjudication-gold-block2-
contested-lines.json.

FINDING 1 — AUDIT FAILED (61%). Blind re-label of 4 consensus-gold regions: human disagreed
with panel consensus on 12/31 lines, and ALL 12 were consensus=prose → human=lineated
(g24_b28, g31_b13, g33_b66). The panel SYSTEMATICALLY UNDER-LINEATES the visually-dense band.
So the 824 consensus gold's PROSE labels in wrap_prose/dense regions are unreliable. Human
notes explain: "technically prose (indents+wrapping) but dense by intent → lineated more
defensible"; "prose render mangled it into one <p>". The audit earned its keep — caught a
systematic consensus bias, not random noise.

FINDING 2 — RANKING is one-directional again; grok dominates. contested-acc: grok 92.8%,
deepseek 56.8, gemini 51.3, owl 50.6, mimo 50.5, careful 48.9, minimax 33.6. overall: grok
94.8, deepseek 89.0. lin-P: careful/minimax/mimo 1.00 but recall 0.72-0.77 (they UNDER-
lineate); grok lin-P 0.96 recall 0.98. The truth is 84% lineated, disagreements are
conservatives under-lineating → the aggressive lineator (grok) is closest. The prose-risk
sampling did NOT yield a balanced truth: for THIS author the corpus is heavily lineated and
under-lineation is the dominant error.

META: "prose is the safe default" looks BACKWARDS for this corpus. Lineated is the norm;
prose is the rarer exception (genuine flowing wrapping narrative). Human also names "LINEATED
PROSE" (g29_b69) — kept breaks, prose register — i.e. the old 3-class (prose/lineated/verse)
nuance resurfacing WITHIN "lineated"; structurally still lineated (keep breaks), register is
the downstream layer.

RENDER BUGS the human flagged (likely biased the panel toward prose): prose candidate mangles
bold pseudo-headers / list items into one <p>; author INDENTS not reproduced in candidates
(a real intent signal); *** near a header renders wrong / too-weak boundary; bold-line split
from its body. → two agents dispatched: RCA (reproduce render bugs) + editorial (ontology:
lineated-prose, indents/density rules, flip the default, fix the brief).

### CORRECTION (Codex) to the two findings above
NOT "all lineated": 197 lineated / 38 prose, and ALL 38 prose sit in 3 wrap_prose regions
(g09_b16_t2:24, g23_b17:13, g10_b19:1) — the human DOES call genuine flowing-wrapping prose
"prose". And this is an AMBIGUITY-ENRICHED sample, NOT a random corpus prior, so do NOT claim
"the corpus leans lineated." Defensible claims only: (a) in this hard set, UNDER-lineation is
the dominant MODEL error; (b) consensus-prose is unreliable in dense/wrap_prose regions;
(c) grok best-matches the human-resolved hard set (recall 0.92 on human-labels-only, not 0.98;
contested-acc mixes the 204 splits + 31 audit, not pure split accuracy). The brief fix is
intent-first + the INDENT-SHAPE discriminator (first-line indent + wrapping = prose; dense /
per-line, no indent = lineated), NOT a blanket flip to lineated.

RENDER BUGS FIXED + verified (astro_preview/ir_view): Bug1 prose-wall → one <p> per source
paragraph (restores per-paragraph indents, Bug2, for free); Bug3 literal "***" body para →
ROLE_THEMATIC (renders as separator, not glued to header); Bug4 trailing-dash lines excluded
from pseudo-header (em-dash couplets no longer split). Bug3 real fix (normalize.py *** →
ThematicBreak) deferred (converter not ready). The CONSENSUS (824) was built on these broken
candidates → unreliable; the human's 235 stand (decided from the docx page). → re-run the
panel with fixed renders + corrected brief.
