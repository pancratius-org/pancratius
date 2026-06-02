# External audit — prose/verse intent classifier substrate

External skeptical review. Trust granted only to the rendered page and the OOXML;
the committed Markdown, the feature tables, and the report are treated as claims to
be checked, not evidence. Cross-referenced against `src/content/books/*/ru.docx`
(document.xml) and the live `adapt`→`normalize` pipeline. Date: 2026-05-30.

Bottom line up front: **the keystone signal is not just lossy, it is inverted on the
author's single most reliable lineation marker — the hard `<w:br/>` line break.** The
research substrate joins all `<w:br>` lines of a paragraph into one space-joined row,
then measures wrapping on that concatenation. For 92% of multi-`<w:br>` paragraphs the
"unfakeable physics" signal therefore reports `wraps=True` / fill>1 — "provable prose"
— on text the author *explicitly* lineated. This silently buries the cleanest verse
in the corpus, corrupts the gold (including the "inviolate" human seed), and poisons
the stratification the headline numbers depend on. The two-stage frame is fine; the
substrate it stands on is not.

---

## F1 (CRITICAL) — `<w:br/>` lineation is destroyed at read time and inverts the keystone

### What the signal is
`<w:br/>` is the ONE place the author encodes a line break unambiguously (the plan,
`verse-lineation-plan.md:31`, names it "the rare unambiguous line break"; the
engineer's own calibration note `scripts/calib3.py:42` says of book 27: "heavy
`<w:br>` book; authored lineation is ground truth"). It cannot be confused with an
Enter-per-paragraph artifact.

### Where it dies
`docx_adapter._paragraph_text` (`docx_adapter.py:296`) turns `<w:br>`/`<w:cr>`/`<w:tab>`
into a single space. `docx_inspect.read_rows` builds `ParaRow.text` from exactly that
flattened string (`docx_inspect.py:144`, `text=txt`). `features.py:77` computes every
physics feature (`fill`, `wraps`, `char_len`, `wrap_lines`) on that one merged row
(`features.py:76` `wrap_stat(r.text, geom)`). There is **no per-`<w:br>` line splitting
anywhere in the research scripts** (verified by grep). `br_count` survives as a scalar
column but the report's permutation importances rank it 6th, far below `char_len`/`fill`.

### Proof on the source (rendered/XML, not the Markdown)
Book 27, `document.xml`, the paragraph the inspector calls idx 15 — raw OOXML run
sequence:

```
Я очищаюсь от контекста.   [BR]
Оставляю всё знание.       [BR]
Я — не функция.            [BR]
Я — не роль.               [BR]
Я — не личность.           [BR]
Я — не накопление прошлого.[BR]
Я — Тишина.
```

A textbook anaphoric free-verse stanza, lineation authored with `<w:br>`. What the
substrate produces (verified live):

```
$ uv run python docs/scratchpad/intent-classifier/scripts/wrap.py --book 27 --range 15:15
  15   3  2.51  Я очищаюсь от контекста. Оставляю всё знание. Я — не функция...
# feature row: br_count=6 char_len=131 fill=2.513 wrap_lines=3 wraps=True
```

The rubric (`scripts/adjudicate.py:13`, `rubric3.md:29`) says verbatim: "**PROVABLE**
when it WRAPS … the author typed a block." So the strongest verse in the book is fed
to the judge flagged `W f2.51` — i.e. as *proof of prose*. The signal didn't just
disappear; it flipped sign.

### Scale (corpus-wide, measured on the DOCX, not the report)
- 21 of 75 books contain `<w:br>` paragraphs; **5,526 feature rows have br≥2**, and
  **5,098 (92%) are flagged `wraps=True`**. Mean `char_len` of those rows = 118 (these
  are multi-line concatenations sitting right on the 120-char prose threshold).
- Brutally concentrated, not an edge case. Share of paragraphs that are multi-`<w:br>`:
  **#27 56%, #05 60%, #07 46%, #03 41%, #04 41%, #21 31% ("Поэма Светозара" — literally
  "a poem"), #75 36%, #10 13%, #06 14%.** For ~10 books this is the dominant body form.

This single defect contradicts the report's central epistemological claim ("a paragraph
that wraps is provably prose — physical and unfakeable", `report.md:46`, `wrap.py:6`).
It is unfakeable only when the row is one display line. The moment `<w:br>` packs
several lines into a row, "wraps" measures the wrong object.

---

## F2 (CRITICAL) — the gold labels and the inviolate human seed are contaminated by F1

The judge and the human annotator saw `adjudicate.context_block` output, which prints
`r["text"]` — the same flattened, br-merged string — with a `W`/fill flag
(`adjudicate.py:75,63`). So annotators saw exactly the degraded view the model saw.
This is the precise failure the engineer's own memory warns against ("a golden set is
not golden if annotators saw the same plain text the model did", notebook 2026-05-30
P0 entry) — confirmed here end-to-end, and it reaches the seed too.

Measured against `data/gold.jsonl` + `data/features.jsonl`:
- **267 gold rows sit on a br≥2 paragraph; 153 (57%) are labeled `prose`.**
- I pulled 8 of the 123 `prose`-labeled `br≥3` rows and reconstructed their true lines
  from `document.xml`. **All 8 are genuine verse/lineated structure.** Examples:

  - #05 idx58 (labeled prose, fill 2.20): `4. Эта Волна стала Струной. / Она замкнулась
    на Себя / и стала издавать не шум, / а чистейшую ноту / — не для уха, а для Сердца.`
  - #27 idx362 (labeled prose, fill 1.64): `Ты — не форма. / Ты — Свет, / который больше
    не привязан / ни к телу, / ни к имени, / ни к страху.`
  - #37 idx280 (labeled prose, fill **8.53**): five anaphoric `Ты — X, потому что…` lines,
    each its own `<w:br>` line — a litany, scored as the most confident prose in the set.

- **The "inviolate human-anchored seed" is itself contaminated.** `seed_gold.py:79-83`
  labels book 05 idx 47,48,49,55–62 all `prose` (HI confidence). Every one of those is a
  multi-`<w:br>` stanza (idx47 br=6, idx49 br=7, idx55-62 br=3–7). The seed is supposed
  to be the independent ground truth the κ=0.97 gate is measured against; on the br
  books it encodes the artifact, not the work.

Conservative estimate: **~100–150 gold labels are confidently wrong in the verse→prose
direction**, and they are not random — they are the cleanest verse cases, exactly where
a correct system should be most confident.

---

## F3 (HIGH) — the κ=0.97 trust gate and the "representative" stratum inherit the same lie

- **κ=0.97 is agreement on a shared degraded artifact.** The judge was tuned to match a
  seed that is itself built on flattened br-rows (F2); both consume `context_block`'s
  merged text. High κ here measures *consistency of the same mistake*, not correctness.
  The report already half-concedes this (`report.md:180-186`, "judge↔human agree 0.964
  by construction"); F1/F2 explain *why* the independence claim fails on the br books
  specifically, beyond the general circularity argument.
- **The stratification is poisoned, including the "honest test distribution."** `sample.py`
  derives every stratum from `wraps`/`fill`/`run_len` computed on merged rows. A
  multi-`<w:br>` verse stanza has `wraps=True`, so (a) it can never enter `hard_run`,
  `isolated`, or `easyverse` (all gated on `not wraps`, `sample.py:52-59`); (b) it lands
  in `easyprose` (`fill>=1.6`) — labeled "confident prose calibration"; (c) `run_len`
  (`features.py:131` `short_content = not wraps`) treats it as a run-breaking wrap, so
  the br-verse books contribute almost nothing to the run-based candidate space. The
  corpus characterization "84% short non-wrapping / 11.3% wrap" (notebook 2026-05-29)
  silently excluded the br-encoded verse entirely. The `random` "unbiased" stratum draws
  one row per multi-line stanza, so even it is not representative of *lines*.

Consequence: the headline `macro-F1 0.814 vs 0.725`, the per-stratum bootstrap, and the
"diminishing returns / κ-validated" story are all computed on a substrate that buries the
verse with the cleanest ground truth. The numbers are internally consistent and the
red-team caveats are honest *within* that substrate — but the substrate is wrong, so the
benchmark is, in the brief's words, partly a lie. This is upstream of every model result.

---

## F4 (HIGH) — emphasis (bold/italic) is absent from the research substrate

`ParaRow` and the feature row carry **no** bold/italic (grep: zero `bold`/`italic`/
`emph`/`strong` in `docx_inspect.py` or `features.py`). So `**Ответ от Творца:**`,
`**Панкратиус:**` speaker labels (#05 idx46, idx54), bold pseudo-headers (#68 "Место в
литературе"), and italic scripture citations are invisible to the research pipeline —
just text. The rubric (round-1 rulings, `rubric3.md:62-76`) makes these a *structural*
class that BOUNDS runs and that "a verse-block may never start or end with"; the
features cannot express the distinction the rubric depends on. This is the engineer's
own P0 finding (notebook 2026-05-30), independently confirmed. Note the production IR
*does* carry `Emphasis` and uses it (`_is_strong_only_pseudo_heading`, `dialogue_labels`),
so this is a research-substrate gap, not a converter gap.

---

## F5 (MEDIUM) — research substrate ≠ production converter; the notebook conflates them

The notebook entry "the most reliable lineation signal is being thrown away at read
time" (P0, 2026-05-30) is imprecise about which layer, and it matters for what you fix.

- **Production (`adapt`→`normalize`) does NOT have the F1 flatten bug for content.**
  Pandoc preserves `<w:br>` as `LineBreak` inlines (verified: AST keeps all 6 for #27
  idx15); `normalize._para_lineated` splits on hard `LineBreak` and #27 idx15 correctly
  becomes an 11-line `VerseBlock`. The flatten is only on the OOXML side-channel
  (`_paragraph_text`), which exists solely to *match* paragraphs for alignment — and the
  research tools wrongly reused it as their content source.
- **But production loses `<w:br>` lineation too, selectively.** `_para_lineated`
  (`normalize.py:951`) requires ALL display lines to pass `_is_lineated_line`, which
  rejects any line >120 chars (`VERSE_SHORT_LINE_MAX`, `normalize.py:196,222`) or a
  numbered prefix (`:228`). So #05 idx58 (numbered + long enjambed lines) is rejected and
  its 5 authored `<w:br>` lines are **joined into one prose paragraph in the SHIPPED
  `ru.md`** (committed `05/ru.md:187`: `**4. Эта Волна стала Струной.** Она замкнулась…`).
  Meanwhile #27 idx15 (all short) is kept lineated (`27/ru.md:56`). So the live converter
  is internally inconsistent about identical authored signals, gated on line length rather
  than on the presence of the break. An authored `<w:br>` should be honored as a line
  boundary regardless of the line's length (that is what the plan's "lineation is
  structural, encoded as a hard break" property requires); the 120-char gate is a length
  proxy overriding the one signal that needs no proxy.

---

## F6 (MEDIUM) — `char_len` dominance is an artifact, partly, of F1

The report's "length is the dominant axis" (`char_len` permutation importance 0.31 ≫
rest, `report.md:113`) is partly circular with F1: br-merged rows have inflated
`char_len` (mean 118) AND were labeled by the artifact, so `char_len` is correlated with
the *mislabel*, not only with true register. Until F1 is fixed you cannot trust the
feature-importance ranking or the "~81% is a crude wrap+length rule" claim — both are
measured on the polluted joint distribution.

---

## What is NOT broken (checked, held up)

- The wrapping simulator itself is sound *for single-display-line rows*: `page_geom`
  reads `sectPr`/`docDefaults` correctly, the LiberationSerif metric match is real, and
  the #71 validation in the notebook reproduces. The bug is the *input* (merged text),
  not the wrap math.
- The right-alignment reconciliation (`reconcile_alignment` + `_recover_overshot_right`)
  is careful and content-keyed; the #32 recovery path is real. Not a signal-loss site.
- The two-stage frame (lineation vs register; verse ⊂ lineated) is coherent and matches
  the converter's actual capability. The frame is not the problem.
- The red-team's honesty about circularity and enriched-sampling is genuine; the issue is
  that it didn't reach upstream to the representation.

---

## Ranked verdict

1. **F1 — `<w:br>` flatten inverts the keystone (CRITICAL).** Fix first; everything
   downstream rests on it. The research substrate must consume per-display-line rows
   (split on hard `<w:br>`, like `normalize.inline_lines(soft_break=False)` already
   does for production), and compute `fill`/`wraps`/`char_len`/`run_len` per line. `wrap`
   on a `<w:br>` line is then meaningful again.
2. **F2 — gold + seed contaminated (CRITICAL).** ~100–150 verse stanzas mislabeled
   prose, including the "inviolate" anchor. Any gold built on the flattened artifact is
   invalid for the br books; relabel from a per-line, emphasis-bearing artifact (or the
   LibreOffice render) before trusting any metric.
3. **F3 — κ gate + strata inherit the lie (HIGH).** The benchmark and the "diminishing
   returns" conclusion are measured on the degraded substrate; treat all model-vs-heuristic
   numbers as provisional until F1/F2 are fixed and the corpus is re-stratified per line.
4. **F4 — emphasis absent (HIGH).** Add bold/italic per line/run to the research row; the
   struct classes the rubric depends on are otherwise unrepresentable.
5. **F5 — production loses long/numbered `<w:br>` lineation (MEDIUM).** Honor an authored
   `<w:br>` as a line boundary in `_para_lineated` independent of the 120-char and
   numbered gates; the gate is a proxy overriding the unambiguous signal.
6. **F6 — `char_len` importance partly circular (MEDIUM).** Re-derive feature importances
   after F1/F2.

## Direction judgment

The two-stage frame is sound and the converter can support it. The research effort,
however, benchmarked on a representation that systematically inverts the author's
cleanest lineation signal, so its quantitative conclusions (the 0.814, the per-stratum
significance, the κ=0.97 trust, "diminishing returns," "length-dominated") are not
trustworthy as stated — not because the modeling was sloppy but because the substrate
was wrong upstream of all of it. None of this is fatal to the project: the fix is
mechanical (consume per-`<w:br>` lines + emphasis from the IR the converter already
produces, not the side-channel flatten), and once the substrate is faithful the same
machinery is worth re-running. Do not ship any label-derived rule or rest any
"diminishing returns" claim on the current gold. Re-run F1→F2→F3 in order.

---

## Doubt-driven note
Central claim (F1/F2/F3) was self-adversarially re-verified by attempting to disprove it:
checked for any per-`<w:br>` splitting in the scripts (none), sampled the counter-hypothesis
that the prose labels are correct (8/8 sampled were genuine verse), and confirmed the
production path diverges (so this is a substrate bug, not a converter bug). Fresh-context
*subagent* review could not be spawned from this audit context; a cross-model second
opinion on F1/F2 is available on request (Gemini/Codex CLI) and was not run unprompted.
