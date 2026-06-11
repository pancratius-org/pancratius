# Student margin vs panel disagreement vs panel confidence

**Question.** Per line we have three signals. Can the cheap one stand in for the expensive one?
- **A — student margin** `1 − 2·|P(lineated) − 0.5|`: free, local, runs on the whole corpus.
- **B — panel disagreement**: spread of the per-reader labels. Expensive (the whole panel). The target.
- **C — panel confidence**: mean reader `.conf`. Cheap-ish but panel-internal.

If A predicts B, the student triages what to send the panel — for free. That is the prize.

## Method
Leak-free: book-held-out OOF (each book scored by a student fit on the *other* books), at the
deployed smoothing `alpha=0.75`. B is the *raw* vote spread, independent of the gold the student
trained on. n = 526 voted lines with an OOF posterior (4 dropped: book 13 has no gold fold).
B bins: 189 unanimous / 138 one-dissenter / 199 ≥2-dissenters.

## Result — A does not proxy B; C does
| pair | Spearman ρ |
|---|---|
| **A (student) ~ B** | **−0.29** — wrong sign |
| **C (panel conf) ~ B** | **−0.50** — right sign, strong |
| C ~ A | +0.05 — unrelated |

A correlation coefficient hides the real question, which is the *off-diagonal*. Terciles × B bins,
row-normalized:

```
A×B  (student)        B: unanim  1-diss  ≥2-diss       C×B  (panel conf)   unanim  1-diss  ≥2-diss
 A low  (confident)        .20    .18    .61            C low  (unsure)       .10    .25    .64
 A mid                     .39    .43    .18            C mid                 .36    .27    .37
 A high (unsure)           .48    .18    .34            C high (confident)    .64    .26    .10
```

The student gradient is **inverted**: where the student is most confident, the panel most splits
(.61); where the student is unsure, the panel agrees (.48). The panel-confidence gradient is clean
and monotone. Same lines, same sample — so this is not range restriction from the ambiguity-enriched
pool; C survives it, A doesn't.

**Divergence is large:** 108 lines confident-but-split (the student would silently skip them —
e.g. `ru:60:5182–5186`), 84 unsure-but-unanimous (wasted oracle budget — e.g. `ru:19:8150–8153`).
~37% of lines sit in one of the two extremes.

C is clumped high (mean .91, sd .035, 65% ≥ .90) but not degenerate — and still discriminates B.

## Verdict
**The student margin cannot proxy panel disagreement, so it cannot drive panel-send routing** — it
is confident precisely on the ambiguous lines. This rules out the bootstrap "send the student's
uncertain lines to the panel"; it does *not* say student uncertainty is worthless for active
learning (unsure-but-unanimous lines may still be good training labels). The split lives in signal
this feature-vector margin doesn't carry.

**C tracks B** (ρ −0.50) — a reader's stated confidence is internally aligned with committee
agreement — but it's panel-internal: useful to prioritize *human adjudication among paneled lines*,
not to decide what to panel.

**Next.** For a *cheap* B-proxy the candidate is the LO/OO structural-disagreement detector (free,
corpus-wide), benchmarked against this student margin as the baseline — the Phase-B bakeoff.

*Also ran raw `alpha=0.0` as a sanity check: same verdict, weaker effect (ρ −0.13, saturation 51%
vs 16% smoothed). We deploy 0.75; the smoothed margin is what's reported above.*

*Reproduce: `student.oof_smoothed(build_dataset(load_records_many(books), load_labels()), records,
alpha=0.75)` → `LineDecision.posterior`; B from `load_votes()` label spread.*
