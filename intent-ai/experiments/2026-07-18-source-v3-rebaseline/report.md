# Source-v3 re-baseline — student, truth, and run/section failure modes

Substrate: 151 editions (77 ru / 74 en, books 76-78 new), 788,644 body lines, 0 uncovered,
2 importer-lost. Truth: 2,159 active labels (1,408 gate / 751 human; 808 holdout of which 109
human); 1,351 trainable. Migration audit re-checked all 38 unresolved ledger rows against the
CURRENT substrate: one retirement (ru:22:84:0) was provably stale (same identity + same text hash
exist today) and was restored across every surface; en:27:117:1's unique hash match still inverts
document order and stays quarantined; the remaining 21 `needs_adjudication` rows stay ambiguous
(2-372 candidates each) and are REPORTED, not guessed.

## Student re-baseline (no capacity change)

- (lang, book)-grouped leave-one-book-out CV on the 1,351 trainable labels (gate+human mix):
  balanced accuracy **0.919**, macro-F1 0.902, prose-F1 0.828 (the suite's locked number).
- HUMAN holdout (contested, 424 lines): iid 0.860 -> run-smoothed alpha=0.75 **0.898** balanced
  accuracy; prose recall 0.959 (93/97), lineated recall 0.838 (274/327). On the reader-shared
  contested slice the smoothed student (0.970) beats every panel reader including the grok anchor
  (0.940).
- GATE-marked slices (panel-derived, never claimed as ground truth): full-labels head-to-head has
  grok on top (0.974 vs student 0.913) — gate truth is grok-anchored, so that ordering is circular
  in grok's favor and is reported only as context. The working-half readout holds at
  P(lineated | det=prose) = 40/90 = **0.444** (human subset 4/5) and
  P(prose | det=lineated) = 0.025 (en 0.050 vs ru 0.014).

## Run/section failure census (frozen half EXCLUDED — E4 is score-once)

Over 103 truth-homogeneous labeled runs: det splits **19** units (3 interior); the iid student
splits 16 (9 interior); run-smoothing (alpha=0.75) cuts that to **6** (5 interior). Of 1,433
scored labeled lines the smoothed student mislabels 95; only 13 are unit-splitting minority calls,
and they live in the low-margin band: a margin<0.10 abstain would defer 12/13 at 95.7% coverage,
margin<0.20 all 13 at 90.3% (n=13 — direction, not calibration).

Examples (position matters, not row counts):
- ru:06 ords 165-169 (formula block, truth lineated): det ships it SPLIT today — prose lead-ins
  around lineated formula lines break one authorial unit into three rendered pieces.
- ru:31 ords 6029-6032 (anaphoric «Он — как ...» litany, truth lineated): one interior prose call
  (iid) breaks the anaphora; smoothing repairs it.
- ru:78 ords 589-595 (breath litany «Вдох - ... / Выдох - ...»): det=prose throughout; the student
  reads 590-595 at posterior >=0.9. The AUTHORED page renders all seven paragraphs with uniform
  spacing — the visual gives weak evidence either way, so this is a genuine-ambiguity class for
  the panel/human loop, not a proven importer error. Today it ships as four indented one-sentence
  prose paragraphs; a lineated verdict would set one tight stanza.

## Error taxonomy

1. det verse-missed (weak side, ~0.444 of det=prose disagreements): dominant class; litany /
   parallelism / formula blocks. E3's target.
2. det over-lineated (0.025, en 2-3x ru): small but position-safe to audit; needs its own ordering
   (suspicion_v0 is 0 on det=lineated by design).
3. Student boundary false-folds: a short prose lead-in glued into the adjacent stanza (ru:78 ord
   590 at 0.91). The reason the decision unit is the run, never the row.
4. Student interior splits: rare after smoothing (5) but catastrophic per unit; almost entirely
   low-margin, hence deferrable.
5. Genuine ambiguity (uniformly-spaced single-sentence paragraphs): no rule or model should decide
   these; they go to the panel/human queue.

## Smallest justified next experiment

**E3: panel sweep of the det=prose band** — already sized and ordered by the re-run E2
(131,503 lines, suspicion_v0 order, the 44,335-line disagreement core first; book 78-ru alone
holds 5,658 posterior>=0.9 candidates). ds-flash cost ~$4. BLOCKED on OPENROUTER_API_KEY and
spend approval.

NOT yet justified: new model families (CV/holdout gaps are not the frontier), selective-prediction
machinery (the abstain band is directionally validated at n=13; calibration needs E3 labels),
forced-lineated corrections (the importer cannot apply them; converter-rule RCA comes first).
