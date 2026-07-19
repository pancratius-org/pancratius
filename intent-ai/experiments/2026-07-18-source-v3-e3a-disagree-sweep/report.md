# E3a — ds-flash sweep of the det=prose disagreement band

Swept ALL 44,335 lines where the importer says prose but the book-held-out student scores
P(lineated) >= 0.5 (selection minted from the committed 151-edition recon; ru 26,890 / en
17,445). One cheap text reader (ds-flash, 3 reps at temp 0.5, majority-aggregated; 87.3% of
keys rep-unanimous), UNROUTED by design — a single reader cannot clear the anchor+support gate,
so this run minted VOTES (evidence), zero labels. Coverage is total: 44,335/44,335 lines voted.
Cost: $16.38 actual (en $7.63 / ru $8.75, 25,954 calls incl. retries) — 2x the pre-run estimate;
ds-flash completion/reasoning tokens grew ~2.5x beyond the early-call sample.

## What the sweep says — and what it cannot say

ds-flash confirms **96.9%** of the band as lineated (ru 0.964 / en 0.978), rising with the
student's own posterior (0.945 in the 0.5-0.75 band -> 0.980 above 0.9). Read this with the
correlated-witness caveat in full view: on the 34 human-labeled lines inside the band the reader
agrees only 0.706 — it catches 23/25 truly-lineated but ALSO calls 8 of 9 truly-prose lines
lineated. Both witnesses read the same structural surface, so their agreement OVERSTATES
certainty on the prose side. The human-anchored composition estimate is therefore ~25/34 = 73%
genuinely lineated (small n) — i.e. the det weak side at corpus scale is on the order of
**~30k verse-missed lines shipping as prose**, consistent with the working readout's 0.444 on
the unenriched working half.

The sweep's INDEPENDENT contributions:
  - the **dissent set** — 1,363 lines the reader calls prose AGAINST the student's suspicion:
    cheap de-noising of the queue (likely student boundary false-folds; ru:78 ord 590's class);
  - **run-level structure** — the 8,037 touched runs split into 857 WHOLE runs unanimously
    lineated (entire authorial units folded flat: litany/anaphora/formula blocks — the cleanest
    rule-RCA and adjudication candidates), 6,533 partial-run confirmations (the un-suspected
    remainder of each run needs a run-level verdict before anything ships), 290 unanimous-prose,
    357 mixed;
  - **book ordering** — ru:78 ("Anatomiya very") dominates with 8,286 confirmed of its 8,648
    band lines; en:64/ru:64/ru:63/ru:19/ru:52 follow.

## What crosses, what does not

Nothing from this run touches truth or production: votes are evidence. The importer still cannot
apply lineated-direction corrections, so the road to shipping remains: run-level human
adjudication of sampled patterns -> converter-rule RCA for the classes that generalize
(whole-run litany/anaphora first) -> labels via the gated panel where rules do not reach.
Escalation order: whole-run unanimous (857 runs) by book, dissent set audited alongside as the
student's error mirror. The uniform-spacing ambiguity class (ru:78 ords 589-595) goes to HUMAN
eyes first — the authored page carries no visual verdict, and neither student nor a text reader
can settle authorial intent there.

## Caveats

- ONE cheap text reader; correlated with the student; NOT gate-grade; zero labels minted.
- human overlap n=34 (and its prose side n=9) — a direction, not a rate.
- gate-overlap agreement (0.917) is panel-derived context, never accuracy.
- cost model: dry-run estimates ran 2x optimistic AGAIN (E1 precedent) — future sweeps budget
  from measured $/call of the SAME region shape, not early-call samples.
