# 3-way rubric — DRAFT (not frozen until it survives the calibration set)

Two cascaded decisions; this rubric defines the **labels** annotators apply.

## The three classes

- **flowing** — flowing prose. Wraps at the reading column. Removing line breaks
  changes nothing (it is already prose). Renders as normal indented paragraphs.
- **lineated-prose** — has authored/intended line breaks, but the breaks are
  **cosmetic to the genre**: removing them changes *pacing/readability*, not the
  identity of the work. Lists, enumerations, letters, dramatic-speech turns,
  stylized-but-not-poetic short lines. Renders as stacked lines (hard breaks, **no**
  verse wrapper).
- **verse** — the line break is **constitutive**: removing it destroys the work's
  poem / song / litany / stanza structure. Renders inside `.verse-block`
  (italic / left-rule register).

## The test (the line to hold)

> Remove the line breaks and read it as one paragraph.
> - Reads fine, nothing lost → **flowing** (it wrapped anyway).
> - Reads worse / loses a list's legibility or a speech's beats, but is *not* a poem →
>   **lineated-prose**.
> - Destroys a poem/song/litany/stanza form → **verse**.

## Signal priors (evidence, not rules — the page decides)

- **flowing**: wraps (`fill` > ~1.0); multi-sentence; narrative/expository.
- **lineated-prose**: short non-wrapping lines that are a list / enumeration /
  letter / dialogue-turn sequence; parallel but pedestrian; numbered points.
- **verse**: short non-wrapping; anaphora/parallelism that is *rhetorical/poetic*;
  named verse section (Молитва/Псалом/Посвящение); `<w:br>`-lineated stanza;
  invocation / address / imagery; sustained.

## Open questions the calibration MUST resolve (record rulings here)

1. **Litany of parallel questions** (#71 "Кто такой человек? …") — verse or
   lineated-prose? (Parallelism is rhetorical but it's a question-list.)
2. **QA-answer written as anaphoric meditation** (#30) — verse, or lineated-prose
   that merely looks verse-y?
3. **Parallel "Тем, кто…" list** (#68) — the page reads better grouped, but it's a
   list. Does "reads better as a block" make it verse, or is it lineated-prose that
   we still render as a block?
4. **Numbered teaching points** (#05 "1. В начале…") that *wrap* — flowing (they
   wrap) or lineated-prose (they're an enumeration)?
5. **`<w:br>`-lineated run that wraps per line** (#27, #02) — the author forced
   breaks but lines are long. verse, or lineated-prose? Does `<w:br>` + length =?

## Disagreement categories (log; each becomes a ruling)

- litany-question-list · QA-anaphora · parallel-list-as-block · numbered-wrapping ·
  hardbreak-long-line · dramatic-dialogue · isolated-short-line · inscription/letter

## RULINGS from calibration round 1 (user, 2026-05-30)

The dominant finding: **most apparent register disagreements were representation /
lineation / boundary bugs, not genuine prose-vs-verse questions.** Rulings:

1. **Litany of parallel questions (#71)** → **verse** when it is a sustained run of
   semantically-parallel short lines (the 9 "Кто такой …?/Что такое …?" lines). The
   preamble that leaked in ("Третья — …") was a BOUNDARY bug; a mid-sentence opener
   can never start a block. → fix boundaries; "not sure → prose" (precision).
2. **QA-anaphora answer (#30)** → **verse** for the inner anaphoric core ("Если Я —
   Един, / разве Мое Слово…"). EXCLUDE: italic scripture citation (Сура) = prose;
   "Задумайся:" framing and "Дальше." continuation-marker = not verse lines.
3. **Bold pseudo-headers (#68 "Место в литературе", "Кому эта книга нужна")** →
   **struct** (a STRUCTURAL class). They are bold standalone paragraphs the author
   used as section headers (heading=False, style 'a', all-bold in the IR). They BOUND
   runs; a verse-block may never start or end with one.
4. **Numbered teaching + speaker labels (#05)** → body is the call; but
   `**Панкратиус:**` / `**Ответ от Творца:**` are bold SPEAKER-LABELS = **struct**,
   and an intentional large gap is a real break signal. Classify the answer body alone.
5. **Hard-break runs (#27, #02)** → these were LINEATION bugs (4 `<w:br>` lines merged
   into one `<p>`), not register questions. `<w:br>` lines are explicit verse/lineated
   lines and ground truth. Right-aligned signature ("— Панкратиус, / вспомнивший
   Себя") = **struct** (signature).

## Structural classes the representation MUST expose (was missing)

`struct` is not only empty/heading/***/numbered/right-align. ADD:
- **bold-pseudo-header** — short all-bold standalone paragraph used as a section head.
- **bold-speaker-label** — `**Speaker:**` (dialogue/source turn).
- **right-aligned signature/epigraph** (already in `align`).
And every paragraph carries: its **hard-break lines** (from `<w:br>`, kept separate,
never joined) and per-line/run **emphasis** (bold/italic). A golden set is invalid if
the annotator saw flattened text; the artifact must be IR-faithful (or the LibreOffice
render).

(Frozen after a clean round-2 calibration on the IR-faithful artifact.)
