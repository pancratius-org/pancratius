# E2 — signal bakeoff + φ-fork (e1-v2-working, n=728)

Target: `det ≠ truth` on the working half = **56** lines ({'lineated': 638, 'prose': 90}). Truth is mostly `gate` (panel); only **20** are human ground truth. Posterior = book-held-out OOF (alpha=0), never the in-sample fit.

## (b) Signal ranking — detectors of `det ≠ truth`
Oriented so higher = more suspect. AUC(all) over 728 (mostly det-vs-PANEL, can be gate-circular); AUC(human) over the 20 ground-truth lines — the only independent truth, and the one the router must hold up on:

| signal | AUC(all) | AUC(human) |
|---|---|---|
| det_student_disagree | 0.9086 | 0.56 |
| suspicion_v0 | 0.8423 | 0.8667 |
| student_uncertainty | 0.6525 | 0.2933 |
| panel_vote_spread | 0.5863 | 0.5 |

Note the all/human split: `det_student_disagree` tops AUC(all)=0.9086 but only 0.56 on the 20 human lines — its AUC(all) edge comes only from ranking det=lineated by 1−posterior, so it is GATE-CIRCULAR and collapses to ~chance where truth is independent. `suspicion_v0` is robust on BOTH (all 0.8423 / human 0.8667), so it — not the AUC(all) leader — orders the sweep.

## (c) The inside/outside-φ fork
Spearman(student uncertainty, panel vote-spread) = **0.2203** (criterion ρ ≥ +0.3); terciles monotone: **True**; off-diagonal mass 0.639.
Tercile cross-tab (rows = uncertainty 0..2, cols = vote-spread 0..2): [[85, 80, 78], [87, 85, 71], [71, 78, 93]]
**Verdict: OUTSIDE-φ** — student uncertainty does NOT track panel disagreement; it stays audit-only.

## Recommended E3 router
**sweep the whole det=prose band; ORDER it by suspicion_v0 (robust on both gate AUC=0.8423 and human AUC=0.8667)**
- E3 does not gate — it sweeps all 131503 det=prose lines (ds-flash, ~$4); the router only ORDERS the sweep. Chosen by robustness on independent truth: suspicion_v0 (gate 0.8423 / human 0.8667), NOT the AUC(all) leader det_student_disagree (AUC(all)=0.9086 but AUC(human)=0.56 — gate-circular, disqualified) whose edge is gate-circular. That human-AUC verdict rests on only 20 human / 5 det-disagreement-positive lines — fine for ORDERING the sweep, NOT for aggressive pruning/early-stop. student uncertainty is audit-only (outside-φ).
- corpus sweep ≈ **131503** lines (the whole det=prose band (131503) is swept; the suspicion_v0 ordering prioritizes the 44335 disagreement lines first. det=lineated disagreement (26307) stays AUDIT-ONLY. NOTE: det⊕student here is NOT independent proof of the readout's weak-side rate — the recon student is trained on the SAME gate labels, so det⊕student only SIZES a candidate suspect slice CONSISTENT with the working readout).
- NOTE: this orders the det=prose band only. The EN-first det=lineated over-lineation audit needs its OWN ordering score (`suspicion_v0` scores det=lineated as 0.0 by design and cannot order it) — likely `1 − posterior` + REVIEW/lang priors — to be built in E3.

## Caveats
- working half only; frozen scored once in E4
- book-held-out OOF posterior (alpha=0) — never the in-sample fit_full
- target det≠truth is mostly det-vs-PANEL (gate truth); only the human subset is ground truth
- AUC on the human ground-truth subset reported separately and is tiny-N (caveat, not a claim)
