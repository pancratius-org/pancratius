# Corpus reconnaissance (free signals, corpus-wide)

103/103 (book, lang) scanned; student fitted on 620 trainable labels.

- votable lines: **538257** (ru 364184, en 174073)
- tier-0: lineated 465815, prose 69028, uncovered 3414; mask-review 7282; unmapped records 55; det-unjoined 0
- det-vs-student disagreement: prose-side 33631 (the suspect slice core), lineated-side 9312 (audit-only)
- EN envelope (5–95% ru band): 3 of 29 en books outside — 06, 07, 71

Per-book census in `scorecard.json`; per-line rows in `_artifacts/recon/`.

## Findings (what E0 resolved, 2026-06-11)

1. **Denominator: 538,257 votable** (assumed ~450–500k). EN share 32% → E1's proportional en
   stratum ≈ 485 lines, consistent with the planned ~400.
2. **The corpus is 87% det-lineated.** The weak direction (det=prose, where the verified error
   mass lives) is only 69,028 lines — small enough that E3 can ds-flash-sweep it WHOLE at 1 rep
   (≈$4) instead of routing a tail. The E2 router then prioritizes escalation order and covers
   the uncovered/REVIEW band; recall-at-budget is no longer the binding question.
3. **The current student is not a precision router**: it calls lineated on 33,631 of the 69k
   det=prose lines (49%) — far above any plausible true error rate; it over-predicts the
   majority class. Anticipated; E2 re-derives the signal on E1's unbiased half.
4. **EN is not structurally alien**: 3 of 29 en books outside the ru 5–95% band (06, 07, 71 —
   71-en det-lineated 23% vs ~90% typical). Default = fold EN into E3, pending E1's en stratum.
5. **Fate ledger is 2%, not <0.1%**: uncovered 3,414 + mask-review 7,282. Needs class-level RCA
   in E3 (expected: lists/tables/empties, non-votable by role) before the DoD bar is meaningful.
6. **det-unjoined = 0** once span-interior blanks are excluded — the importer's ordinal space
   and the producer's records are consistent corpus-wide.
7. **E0b floors committed** (`tests/test_det_regression.py`): trainable 0.9636 / bench 0.9582 /
   contested 0.9169 / structural 0.8125 balanced-acc; prose-recall 0.978–1.000. The asymmetry
   beam re-verified: 3 unique prose→lineated lines across ALL gold (ru:17:140, ru:17:141,
   ru:24:1522) vs 95 lineated→prose. Truth moved under the 06-11 strategy's numbers (the 17
   render-bug re-adjudications landed after its measurement), so these floors, not the strategy
   table, are the baseline.
