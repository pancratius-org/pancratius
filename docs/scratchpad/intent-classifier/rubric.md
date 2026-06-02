# Adjudication rubric — prose vs verse (authorial lineation intent)

You label each **content** paragraph of a rendered book region as **prose** or
**verse**, deciding the question: *how did the author intend this line to render —
as part of a flowing prose paragraph, or as a discrete line in a tight lineated
block (poem / litany / list)?*

This author **presses Enter for every line**, so a paragraph break carries NO
information. Judge from the text, its neighbours, and the trusted physical signals.

## Labels

- **struct** — not a prose/verse body decision; it BOUNDS runs:
  empty paragraph (`E`), heading (`H`), thematic break `***` (`*`), right-aligned
  signature/epigraph (`R`), numbered list item (`N`), a bold pseudo-heading /
  section label (e.g. **Место в литературе**, "Ответ от Творца:", an "Иллюстрация"
  image caption).
- **prose** — intended as flowing prose.
- **verse** — a discrete line meant to stand on its own inside a tight lineated block.

## The two anchors

- **Wrapping = strong PROSE evidence (physical, unfakeable).** `W` / `fill > ~1.0`
  means the paragraph fills >=1 full reading line: the author typed a block. An
  ISOLATED wrapping paragraph, or one amid other wrapping paragraphs, is **prose**.
- **The inversion.** Among SHORT NON-WRAPPING lines (`fill` well under 1) that form
  a multi-line run bounded by deliberate breaks, the DEFAULT is **verse**. The
  author writes most lineated text as one short paragraph per line.

## Decisive cues (register + structure beat raw length)

**VERSE** — pick verse when the run shows any of:
- 2nd-person spiritual/scriptural **address**: "Ты …", "Ты — …", imperatives.
- **Anaphora / parallelism**: consecutive lines share an opening ("Тем, кто …",
  "На …", "— Я …", "Что такое …?", "Кто …?"). Strong anaphora makes it verse EVEN
  IF the lines mildly wrap (e.g. parallel "— Я войду … / — Я прикоснусь …").
- **Litany of short parallel questions/statements** (the #71 "Кто такой человек? /
  Кто такой Бог? …" case — group it, even with no blank lines between).
- **Enumerated parallel list** rendered as separate lines ("Людям, которые …" /
  "Тем, кто …"), short dash bullets ("— гарантии …; — ясной позиции …").
- Short **imagistic / free-verse** lines, enjambment (one sentence split across
  lines), stanza breaks via empties or `***`.
- A short multi-line **quoted vow / inscription** amid prose ("«Если придёт
  момент, / когда … / Я вспомню его»").

**PROSE** — pick prose when:
- It **wraps** and reads as a sentence/paragraph (default for `W`).
- **Narrative fiction**: proper-name subjects (Олег, Сергей, Александра), past-tense
  narration, **dialogue turns** ("— Это не он говорит…", "— спросила Александра"),
  speech verbs (сказал/спросил/ответил/потребовал). Short dialogue/narration lines
  in a scene are PROSE even in a run.
- **Expository / teaching** paragraphs, numbered teaching points ("1. В начале …"),
  a lead-in sentence ending ":" followed by prose.
- A lone short sentence amid wrapping prose with no lineated run around it.

## Hard boundary (the genuine ambiguous middle — flag low confidence)

- Short run (2–4 lines) amid prose: verse if enjambed/quoted/parallel; prose if
  independent terse narrative beats ("Не сбой. / Размышление.").
- A line with `fill` ~0.85–1.1: near the wrap edge — let register + neighbours decide.
- An enumeration of short full sentences ("Первая — Эдем … / Вторая — …"): genuinely
  ambiguous; lean verse under the inversion but mark low confidence.

Output per content paragraph: `{idx, label ∈ prose|verse|struct, conf ∈ hi|med|lo}`.
Do NOT trust paragraph gaps/spacing (incidental, inconsistent across books). Do NOT
trust the committed Markdown. When the page contradicts your text read, the page wins.
