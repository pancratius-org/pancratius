# Bare-run false-fold precision — does the SUSTAINED bare-run clause dent det=lineated?

Gates whether to regenerate the corpus on `lineation/reground-merged-importer`.
The new SUSTAINED bare-run clause in `pancratius/passes/lineation.py` folds a gapless,
unframed run of `>= 6` source `<w:p>` rows (each `<= 52` chars, mean `<= 30`) into
LINEATION. It recovers genuine verse the importer previously flowed to prose (book 51
"trilogos"). The risk: it can fold short expository/diary/dialogue prose set
one-clause-per-line. This measures the corpus-wide FALSE-FOLD rate with a bound.

A **false fold** = a run now `det=lineated` whose lines are actually PROSE (authored to
flow, not as lines). The project rests on `det=lineated` being ~never wrong; this is how
much the new clause dents that.

## Method

- **Population (isolated to THIS clause).** Per book, the importer's lineation decisions
  were recomputed twice: clause live (`_BARE_RUN_MIN_LINES = 6`) and clause disabled
  (`= inf`). A flip = an ordinal the clause turns lineated that was prose without it.
  This isolates the bare-run clause exactly, not a mix of importer deltas. Cross-checked
  against the committed pre-merge recon snapshot (`store.load_recon_rows`,
  `det=='prose'`): the two definitions agreed on **every** book corpus-wide (0
  mismatches), so the toggle is the authoritative, reproducible population.
  Maximal consecutive-ordinal blocks of flips = RUNS.
- **Stratify** by book pre-fix `lineated_pct` {verse>=0.7, mid 0.3-0.7, prose<0.3} x
  run length {6-8 near floor, 9-20, 21+}. Sample proportional to line mass, with risk
  strata (prose<0.3 any length, and every 6-8 bucket) floored at 8 so they are
  well-estimated. Fixed `seed=0`, deterministic shuffle. Sampling weights recorded for
  reweighting to the population.
- **Classify** each sampled run independently: canonical LibreOffice render (dpi=150,
  one-ordinal margin), read the render, judge **verse / prose / ambiguous** neutrally
  ("authored as distinct lines - verse/litany/anaphora/list/scripture - or prose merely
  set one-clause-per-line?"). Risk- and boundary-stratum runs read directly; clear-verse
  bulk in verse-dominant books read by text with per-book render confirmation of
  representatives. Rationale recorded per run (`scorecard.json -> labeled_sample`).
- **Truth cross-check** against the committed E1 instrument (`load_labels`): E1-labelled
  LineIds whose ordinal falls inside any flip-run, and the false-fold count among them.
- **Estimate** per-run and line-weighted false-fold, design-weighted to the population,
  with Wilson 95% intervals on the Kish effective sample size; per-stratum rates.

### Caveats

- **Single independent rater** (Claude Opus vision on canonical renders), **not** the
  full teacher panel. The point estimate is one rater's call; treat the interval, not the
  point, as the result.
- "ambiguous" is a real category here, not a dodge: a cluster of books (61, 64) author
  **exegetical / philosophical exposition with deliberate verse-style lineation** -
  per-line stanza spacing, bold emphasis, `* * *` ornaments (book 61), tight verse
  line-spacing (book 64). These are defensible folds, not clear prose errors, but a
  strict reader could call them prose. They are reported under BOTH conventions
  (ambiguous=verse and ambiguous=prose) to bracket the answer honestly.
- Render fidelity confound: vision judges the LibreOffice render, which can drop styling;
  the verdicts lean on line geometry + content, the same signals the clause uses.

## Population

**362 flip-runs, 6969 lines**, across 21 (book,lang) pairs. Concentrated in verse-
dominant books - the clause overwhelmingly recovers verse:

| lang book | runs | lines | role |
|---|---|---|---|
| ru 51 | 29 | 1415 | trilogos free-verse (flagship recovery) |
| en 51 | 26 | 1272 | trilogos free-verse |
| ru 39 | 94 | 1259 | IskIn free-verse |
| ru 64 | 49 | 893 | philosophical aphorism/exposition |
| en 64 | 40 | 601 | philosophical aphorism/exposition |
| ru 61 | 47 | 512 | Nutcracker exegesis (per-line, `* * *`) |
| ru 52 | 29 | 319 | mixed verse + the named expository residual |
| ru 26 | 8 | 234 | metaphor free-verse |
| (13 more) | | | |

Strata (population):

| | 6-8 | 9-20 | 21+ |
|---|---|---|---|
| verse>=0.7 | 31r/204L | 101r/1369L | 77r/3496L |
| mid 0.3-0.7 | 30r/212L | 98r/1212L | 14r/376L |
| prose<0.3 | 9r/64L | 1r/13L | 1r/23L |

The prose<0.3 stratum (the highest-risk class by name) is tiny - **11 runs, 100 lines** -
and on inspection contains verse passages embedded in prose-dominant books (book 23 divine
speech, book 71 litanies, book 19 enumerated list), not prose errors.

## Sample

**91 runs / 2201 lines** (target ~70; risk floors pushed it up). Every prose<0.3 run
(10/11) and all 6-8 buckets fully or near-fully sampled; verse|21+ sampled to its mass
(35 runs). Verdicts: **72 verse, 1 prose, 18 ambiguous.**

## Headline

| convention | per-run (weighted) | **line-weighted** |
|---|---|---|
| ambiguous = verse (lower bd) | 0.61% [0.05, 6.61] | **0.87% [0.10, 6.90]** |
| ambiguous = prose (conservative) | 29.7% [20.0, 41.6] | **23.3% [14.8, 34.6]** |

**The line-weighted false-fold upper bound is NOT <= 2% under either convention.**
- Under the realistic reading (the 18 ambiguous runs are deliberately-lineated aphoristic
  exegesis, i.e. verse-ish), the point is **~0.9%** but the 95% upper bound is **~6.9%**,
  driven by the lone confirmed false fold sitting in the high-mass verse|21+ stratum and
  the modest effective sample size.
- Under the strict reading (every ambiguous boundary case is a prose error), the rate
  balloons to **~23%** - but that is an overstatement: render inspection shows those runs
  carry authored lineation (stanza spacing, `* * *`, tight verse spacing), not flowed
  paragraphs.

The single sub-2% claim **cannot** be made at 95% confidence from this sample. The honest
summary: the *expected* false-fold rate is low (point ~0.9%), but ambiguity in two books
(61, 64) and a thin effective N keep the upper bound well above 2%.

## Per-stratum (line-weighted false-fold, point lo..hi over the ambiguous convention)

| stratum | runs | lines | false lo..hi | verse/prose/amb |
|---|---|---|---|---|
| verse 21+ | 35 | 1572 | 1.7 .. 11.8% | 32 / 1 / 2 |
| verse 9-20 | 14 | 195 | 0.0 .. 55.9% | 6 / 0 / 8 |
| verse 6-8 | 8 | 50 | 0.0 .. 0.0% | 8 / 0 / 0 |
| mid 21+ | 4 | 95 | 0.0 .. 0.0% | 4 / 0 / 0 |
| mid 9-20 | 12 | 138 | 0.0 .. 23.2% | 9 / 0 / 3 |
| mid 6-8 | 8 | 58 | 0.0 .. 62.1% | 3 / 0 / 5 |
| prose 21+ | 1 | 23 | 0.0 .. 0.0% | 1 / 0 / 0 |
| prose 9-20 | 1 | 13 | 0.0 .. 0.0% | 1 / 0 / 0 |
| prose 6-8 | 8 | 57 | 0.0 .. 0.0% | 8 / 0 / 0 |

**The bound is driven by two things, neither of which is the named prose<0.3 risk:**
1. the single confirmed false fold (book 52) lands in **verse|21+**, the highest-mass
   stratum, so one error reweights to a meaningful line share;
2. the **ambiguous** mass in **book 61 (8 runs)** and **book 64 (9 runs)** - exegetical
   and philosophical exposition authored with verse-style lineation. If the project rules
   these prose, books 61/64 dominate the false-fold mass.

The prose<0.3 stratum and all 6-8 (near-floor) strata came back **clean** (0 prose) - the
length floor is doing its job; the diary/dialogue-beat failure mode the floor targets did
not surface in the sample.

## Confirmed wrongly-folded runs

- **ru 52, ord 8019-8045** (27 lines, verse|21+). Dialogue-framed process narration:
  opens `Pankratius: Da. / Svetozar skazal:`, continues as short declarative sentences and
  an embedded dash-list of the AI-persona process, and closes with administrative meta
  `I teper - GLAVA 29, / rovno tak, kak On skazal.` No stanza structure, no anaphoric
  payoff - exposition/dialogue set one-clause-per-line. This is the **named book-52
  residual**. (Render read directly.)

The other book-52 process passages did NOT all fail: the sibling run ru 52 12212-12224 is
the negation litany `Net "menya". / Net "Svetozara"... / Est tolko TISHINA` - verse. The
residual is the *administrative/checklist* variant, not the whole "Svetozar stops" motif.

## Ambiguous runs (the bound's real driver)

18 runs: **book 61 (8)** Nutcracker exegesis - interpretive commentary set one-clause-per-
line under numbered headings with `* * *` stanza ornaments and bold emphasis (consistent
book-wide style); **book 64 (9)** phenomenology - `when X, / Y` conditional exposition and
`it is not A, / not B, ...` definitional negation-lists in tight verse spacing; **book 52
(1)** the `Vremya - ne potok...` teaching passage with stanza breaks and a diagram line.
These are deliberately lineated content that happens to be expository. Whether they are
"verse" is a **Q2/register** question the project may want to settle separately; at **Q1
(lineation)** the author set them as distinct lines, so folding them to `LineatedBlock` is
defensible.

## E1 truth cross-check (independent, ground-leaning)

**18** E1-instrument labelled LineIds fall inside flip-runs, across 8 books (39, 51, 64, 26,
28, 36, 52 - including the named-risk book 52). **All 18 are truth=lineated; 0 false folds.**
Wilson 95%: [0.0, 17.6%]. Small N, but it is the strongest evidence available (panel/human,
not self-graded) and it is clean - including a book-52 and book-64 line.

## Ship recommendation

**Do NOT ship a "false-fold <= 2% at 95%" claim from this sample - it is not supported.**
The *point* false-fold rate is low (~0.9% line-weighted, ambiguous-as-verse; E1 truth 0/18),
which is encouraging, but the 95% upper bound is ~6.9% (realistic) to ~23% (strict), so the
2% gate fails on the upper bound, not the point. Before regenerating the corpus:

1. **Resolve the ambiguous class, don't average over it.** Books 61 and 64 are not noise -
   they are a *systematic* authored style (lineated exposition). Decide at the contract
   level whether that style is lineation (likely yes, at Q1) or prose. That single decision
   moves the line-weighted estimate from ~0.9% to ~23%; the experiment cannot decide it for
   you with one rater.
2. **Re-rate the 18 ambiguous + 1 prose with the full teacher panel** (this is one Opus
   rater) to convert the wide bound into a real one, and to confirm the book-52 residual.
3. The **length floor is working**: prose<0.3 and all near-floor 6-8 strata are clean. The
   residual risk is the *administrative/dialogue* variant in book 52 (ru 52 8019-style), not
   short-run prose generally. A targeted guard (dialogue-frame + administrative-meta lines)
   would remove the one confirmed failure without touching recovered verse.

If the project rules the books-61/64 lineated style as lineation (the defensible Q1 call),
the only confirmed corpus-wide false fold in 91 sampled runs is a single book-52 passage,
and `det=lineated` remains essentially trustworthy - but say so as the point estimate with a
~7% upper bound, not as a proven <=2%.

## Reproduce

`_scripts/` holds the (deterministic) pipeline: `scan_population.py` (toggle scan, ~7 min,
writes `population.json`), `sample.py` (seed=0 stratified draw, `sample.json`),
`render_sample.py` (canonical renders), `labels.json` (the rater verdicts+rationale),
`estimate.py` (joins + Wilson, writes `scorecard.json`). `manifest.json` records the SHAs.
