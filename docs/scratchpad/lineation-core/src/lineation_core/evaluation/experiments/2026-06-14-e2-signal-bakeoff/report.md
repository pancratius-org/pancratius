# E2 — signal bakeoff + φ-fork (e1-instrument-working, n=747)

Target: `det ≠ truth` on the working half = **64** lines ({'lineated': 642, 'prose': 105}). Truth is mostly `gate` (panel); only **21** are human ground truth. Posterior = book-held-out OOF (alpha=0), never the in-sample fit.

## (b) Signal ranking — detectors of `det ≠ truth`
Oriented so higher = more suspect. AUC(all) over 747 (mostly det-vs-PANEL, can be gate-circular); AUC(human) over the 21 ground-truth lines — the only independent truth, and the one the router must hold up on:

| signal | AUC(all) | AUC(human) |
|---|---|---|
| det_student_disagree | 0.9203 | 0.55 |
| suspicion_v0 | 0.8721 | 0.8688 |
| student_uncertainty | 0.6444 | 0.525 |
| panel_vote_spread | 0.5696 | 0.5 |

Note the all/human split: `det_student_disagree` tops AUC(all)=0.9203 but only 0.55 on the 21 human lines — its AUC(all) edge comes only from ranking det=lineated by 1−posterior, so it is GATE-CIRCULAR and collapses to ~chance where truth is independent. `suspicion_v0` is robust on BOTH (all 0.8721 / human 0.8688), so it — not the AUC(all) leader — orders the sweep.

## (c) The inside/outside-φ fork
Spearman(student uncertainty, panel vote-spread) = **0.2391** (criterion ρ ≥ +0.3); terciles monotone: **False**; off-diagonal mass 0.647.
Tercile cross-tab (rows = uncertainty 0..2, cols = vote-spread 0..2): [[84, 86, 79], [99, 80, 70], [66, 83, 100]]
**Verdict: OUTSIDE-φ** — student uncertainty does NOT track panel disagreement; it stays audit-only.

## Recommended E3 router
**sweep the whole det=prose band; ORDER it by suspicion_v0 (robust on both gate AUC=0.8721 and human AUC=0.8688)**
- E3 does not gate — it sweeps all 70994 det=prose lines (ds-flash, ~$4); the router only ORDERS the sweep. Chosen by robustness on independent truth: suspicion_v0 (gate 0.8721 / human 0.8688), NOT the AUC(all) leader det_student_disagree (AUC(all)=0.9203 but AUC(human)=0.55 — gate-circular, disqualified) whose edge is gate-circular. That human-AUC verdict rests on only ~21 human / 5 det-disagreement-positive lines — fine for ORDERING the sweep, NOT for aggressive pruning/early-stop. student uncertainty is audit-only (outside-φ).
- corpus sweep ≈ **70994** lines (the whole det=prose band (70994) is swept; the suspicion_v0 ordering prioritizes the 33667 disagreement lines first. det=lineated disagreement (11589) stays AUDIT-ONLY. NOTE: det⊕student here is NOT independent proof of the readout's 0.46 rate — the recon student is trained on the SAME gate labels, so det⊕student only SIZES a candidate suspect slice CONSISTENT with the working readout).
- NOTE: this orders the det=prose band only. The EN-first det=lineated over-lineation audit needs its OWN ordering score (`suspicion_v0` scores det=lineated as 0.0 by design and cannot order it) — likely `1 − posterior` + REVIEW/lang priors — to be built in E3.

## Caveats
- working half only; frozen scored once in E4
- book-held-out OOF posterior (alpha=0) — never the in-sample fit_full
- target det≠truth is mostly det-vs-PANEL (gate truth); only 21 human lines are ground truth
- AUC on the human ground-truth subset reported separately and is tiny-N (caveat, not a claim)
