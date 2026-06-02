# Editorial findings — mining the human adjudication notes (gold_block2)

Read-only analysis of the 23 adjudication notes in
`adjudicate/responses-lineation-adjudication-gold-block2-contested-lines.json`,
cross-checked against the cited DOCX-source PNGs in
`data/gold_block2/png/`. The goal is to refine the ONTOLOGY and the LABELING
CRITERION, and to fix the reader brief so the panel stops systematically
under-lineating. Not a code change.

Quantitative frame (given): 235 human labels = 197 lineated / 38 prose (84%
lineated). Blind audit of 4 consensus regions: human disagreed on 12/31 lines,
and EVERY disagreement was consensus=prose → human=lineated. The panel under-
lineates in one direction only. The most aggressive lineator (grok) is closest
to the human; conservative readers are farthest.

---

## 1. "Lineated prose" — yes, `lineated` is two things, but the LABEL stays binary

The human explicitly coins **lineated prose** (g29_b69_t0, g29_b69_t1):

> "it's lineated prose (some lines in each 'Ответ от Творца:' are long) that he
> wanted to highlight" (g29_b69_t0)
> "Not a verse/litany/etc, just lineated prose. … Can it be just prose? 100% it
> can." (g29_b69_t1)

So `lineated` actually covers **two ontologically different things**:

- **verse-register lineated** — the break carries poetic/liturgical force: verse,
  litany, invocation, vow, broken aphoristic couplet (e.g. g14_b07 "clearly a
  verse, with editorial short line breaks"; g22_b31_t4 "part of a verse stanza";
  g08_b55 "line-separated contrast/opposition").
- **prose-register lineated** — the *content* is ordinary prose (full sentences,
  some of them long enough to wrap) but the author **deliberately gave each
  sentence its own line** as an emphasis/pacing device. g29 is the type case:
  "Бегите в тишину. / Бегите в то, что не может быть разрушено. / … / Но долина —
  это место погружения. / Гора — это место зрения." Each is a whole sentence on
  its own line; this is intended structure, not an Enter habit.

**Does the binary need refining?** For the *label that drives rendering*, no —
keep `prose` vs `lineated`. The decisive question for the label is "did the
author MEAN the break?", and for both verse-lineated and prose-lineated the
answer is yes, so both are `lineated`. Collapsing them is correct for the
current pipeline. The human confirms this is a downstream concern, not a label
concern:

> "We have other expressive means available… maybe we'll do a separate register
> for QA and style it creatively… it's important for us here not to over-optimize
> with the means used by author, seeing the intent." (g29_b69_t0)

**Recommendation:** keep the binary, but the brief must *name* prose-lineated as
a first-class member of `lineated`, because the panel's failure mode is exactly
here — it sees "long sentences that wrap" and reflexively says prose. The
prose/verse split belongs in a LATER register/styling layer (a `register` field
on a lineated run, e.g. `verse` | `prose-lineated`), not in this label.

**How a labeler decides the g29-type case:** content being prose does NOT settle
it. Ask: *would joining these sentences into one wrapped paragraph lose
something the author put there on purpose?* In g29 the one-sentence-per-line
cadence is the rhetoric (command → consequence → contrast: долина vs гора). Join
them and the drumbeat dies. → `lineated`. The human's own tie-breaker is "look
how it reads/looks": if joined prose produces ugly half-empty lines and kills a
deliberate cadence, it was lineated.

---

## 2. Indents as intent — reconciling "indent = prose" with "indent = lineated"

The apparent contradiction (g23_b17: indented = clearly PROSE; g29/g31: indents
argue lineated) **dissolves once you look at WHICH indent**. There are two
different typographic objects, both visible in the DOCX PNGs:

- **First-line indent (красная строка / red-line).** Only the *first* line of a
  paragraph is indented; wrapped continuation lines run flush to the left margin;
  the block is multi-line. This is the standard Russian prose paragraph marker.
  - g23_b17 docx: every block is a ~2-row paragraph, first line indented, second
    flush left. Human: "they are even indented in author's docx… on average,
    2-row 'lines', each starting with an indent. It makes it obvious for me it's
    not lineated blocks." → **PROSE.**
  - g09_b16_t2 docx: same — first-line-indented multi-line paragraphs. "It's
    prose, super clear — it's even indented in author's docx." → **PROSE.**
  - g10_b19 ("Запомни: Имена…") docx: first-line indent, wraps to 3 flush-left
    rows. Human labeled **prose**.

- **Whole-line / hanging block indent.** *Every* line of the unit is pushed in as
  a standalone short line; only a forced wrap of an over-long line drops to the
  margin. The indent marks each line as its own unit.
  - g29_b69_t1 docx: "Бегите в тишину." / "Бегите в то…" / "Но долина — это
    место погружения." / "Гора — это место зрения." each sit indented and alone;
    only the long "Все, кто не побегут…" sentence wraps its tail flush-left. →
    **LINEATED** (prose-register).
  - g29_b69_t0 docx ("Ответ от Творца:"): the answer lines are block-indented as
    a highlighted group. Author "used indents to 'emphasize' lines relating to
    answer." → **LINEATED.**
  - audit_g31_b13: indented but *dense* (see §3) → **LINEATED.**

**The rule (articulated):**

> Indentation by itself proves nothing. Read the SHAPE of the indent.
> - If the indent is a **first-line-only** marker on a multi-row paragraph whose
>   continuation runs to the left margin → that is the Russian prose paragraph
>   convention → evidence for **PROSE**.
> - If **every line of the unit is indented as a standalone short line** (a
>   hanging/block indent, lines not filling the column, gaps between them) →
>   the author is marking lines as units → evidence for **LINEATED**.
> - The discriminator is "does the indent recur per line, or only once per
>   paragraph?" and "do continuation lines wrap to the margin (prose) or does
>   each line stand alone (lineated)?"

This is the single most under-used signal in the panel, and it is *visible in
the authority image*, which the readers were told to consult but the brief never
told them how to read.

---

## 3. Density-as-intent — sound, with a stated boundary

audit_g31_b13: "technically, given indents and wrapping lines, it's prose. But
it has to be 'dense' by intent, 'all lineated' is more defensible here from the
visual viewpoint."
audit_g33_b66: "Technically, it's lineated — written visually dense, without
indents, so it's the most defensible option. But honestly — prose … would also
work here."

What "dense" means, from the two PNGs:

- **g33_b66 docx**: a block of consecutive **short, flush-left, full-sentence
  lines with NO paragraph gaps and NO first-line indents** — "Он не играет в игру
  'лучше — хуже', 'успех — провал'. / Он смотрит глубже…". A wall of one-sentence
  lines stacked tight. No prose paragraph machinery (no red-line, no inter-para
  gap) is present, so the *only* coherent reading of the layout is "each line is a
  unit" → lineated.
- **g31_b13 docx**: similarly tight, the lines pack the column with minimal
  leading and no paragraph indents.

So "dense" = **the lines are packed tight (minimal/zero leading), each is a
complete short clause/sentence, and the prose paragraph markers — first-line
indent + inter-paragraph gap — are ABSENT.** When prose machinery is absent and
the unit is still visually a stack of discrete clauses, lineated is the more
defensible reading.

**Is it sound?** Mostly yes, as a tie-breaker, but it is the human's *weakest*
argument and he flags it himself ("honestly — prose would also work here just
fine, assuming it matches other styling/conventions of a book"). Density is
genuinely ambiguous between "tight prose" and "lineated"; the human resolves it
by (a) absence of prose markers and (b) consistency with the rest of the
book/section. Treat density as a **soft tie-breaker that leans lineated**, not a
hard rule — and always pair it with the consistency check (§5, g04/g05/g06).
Note both these calls were confounded by a rendering bug (verse styling shown
where there is no verse; prose render mangled into one giant paragraph), so the
human was reasoning largely from the DOCX page, not the comparison renders.

---

## 4. The prose default is WRONG for this corpus — invert it

The brief's central instruction is:

> "prose … This is the SAFE DEFAULT — when unsure, choose prose."
> "Be conservative: over-lineating ordinary prose is the costly error."

The evidence contradicts this on every axis:

- **Base rate:** 84% of contested lines are lineated. A "default to prose" rule
  is betting against a 5:1 prior on exactly the hard cases.
- **Error direction:** 12/12 audit disagreements were prose→lineated. The panel
  never over-lineates; it only ever under-lineates. The brief is correcting a
  mistake that isn't happening and amplifying the one that is.
- **Reader ranking:** the most aggressive lineator (grok) is closest to truth;
  conservative readers are farthest. The brief is selecting for the wrong
  behavior.
- **Asymmetric-cost claim is backwards.** The brief asserts over-lineation is the
  costly error. In this corpus the costly and *frequent* error is under-lineation
  (flattening deliberate structure into a wall of prose, which the human flags as
  visually "ugly gaps" / "wall of text" — g05_b37, g29_b69_t1).

**Corrected default/criterion:**

- Drop "when unsure, choose prose." Replace with a **neutral, evidence-first**
  rule: decide from the author's intent signals (indent shape, density, register,
  cadence, parallelism, section consistency); only fall back to a tilt **toward
  lineated** when genuinely 50/50, because the prior and the cost both point that
  way for this corpus.
- Reframe the question as the human does: not "is this prose?" but **"did the
  author MEAN this break? would joining the lines damage how it reads?"** The
  human's recurring positive tests for lineated are: contrast/opposition across
  lines (g04, g08, g18_b60_t1), parallel/enumerated structure (g04 "Articles",
  g18_b60_t0), verse stanza membership (g14, g22_b31_t4), and one-sentence-per-
  line cadence (g29).

**Implication for the eventual model:** the decision threshold should be
calibrated to the 84% prior, not to a symmetric or prose-leaning prior; and the
loss should penalize false-prose (missed lineation) at least as heavily as
false-lineated. The current consensus aggregation (which under-lineates) should
be reweighted toward the aggressive readers, or the threshold lowered.

A large fraction of the "contested" lines were not real ambiguity at all but
**rendering/parsing bugs** the human kept hitting (g00_b64_t0/t1/t2 same-sentence
colon-introductions split into separate paragraphs; g05/g23/g24/g27/g38 prose
render "mangled into a single giant P"; g22_b31_t5 stanzas broken; *** and bold
lines mis-parsed). These bugs make prose renders look plausibly wrong and verse
renders appear where no verse exists — which would *also* push a render-trusting
reader toward the wrong label. The brief should warn readers to trust the DOCX
page over the comparison renders when they conflict (they currently are told the
DOCX is "the AUTHORITY" but also to judge "which rendering reads TRUE" — the
latter is unreliable while these bugs exist).

---

## 5. Per-region: human reasoning → generalizable rule

| Region | Human's reasoning (1 line) | Generalizable rule |
|---|---|---|
| g00_b64_t0 | Colon-introduced clauses with no internal period, same sentence, split into separate paragraphs — incoherent with png & reading flow | Lines that complete a sentence opened on the prior line (after ":") belong to it; a run never splits a single sentence. (Also a parsing bug.) |
| g00_b64_t1 | Same; structure shows "Я говорю:" then content — "становится фоном" likely mis-parsed as hard structure | Don't let mis-detected hard boundaries split a continuing sentence. |
| g00_b64_t2 | Bold lines between two halves of one sentence broken into 3 paragraphs — a parse bug, not a disagreement | Bold/emphasis mid-sentence is not a structural boundary. |
| g04_b73 | Declarational, legal-like; lines used for contrast/opposition ("не в страхе… а в Свете"); "Это право включает:" lineated for style-consistency with surrounding Articles | Contrast/opposition across lines ⇒ lineated; and prefer the style the author uses for parallel "collective" content (Articles/questions) consistently across the section. |
| g05_b37 | List headers; both options work because RU never glues header+content on one line; chose lineated as png shows lineated and lineated render keeps a contrasting gap | List/enumeration headers ⇒ lineated when the layout gives them their own contrasting line; collapsing would make a wall of text. |
| g06_b40 | "Ответ:" — in-context; consistent across book ⇒ either defensible; chose lineated to match png and Q/A visual flow | Q/A answer markers: decide by book-wide consistency; match the dialogue's visual flow. |
| g08_b55 | Line-separated contrast/opposition | Antithesis split across lines ⇒ lineated. |
| g09_b16_t2 | Prose, super clear — first-line-indented multi-row paragraphs in docx | First-line-indented multi-row paragraphs ⇒ prose. (Prose render bug noted.) |
| g10_b19 | "Запомни:…" first-line indented, wraps 3 rows ⇒ prose | Same: красная-строка indent + wrapping continuation ⇒ prose. |
| g18_b60_t0 | Verse parts; the enumerated "показывали одно и то же" split in two to emphasize same→different ⇒ lineated | Author splitting an enumeration to mark a contrast resolves ambiguity toward lineated. |
| g18_b60_t1 | Verse part + in-sentence contrast ("кто воспринимает / что воспринимается") ⇒ lineated even as one sentence | Membership in a verse passage + cross-line antithesis ⇒ lineated. |
| g18_b60_t4 | Pseudo-header "Библиография" caused confusion; ruled by docx png ⇒ lineated | A line that looks like a header but the docx shows in-flow ⇒ judge by the page, not by header-shape heuristics. |
| g22_b31_t4 | Part of a verse stanza ⇒ not prose | Stanza membership ⇒ lineated. |
| g22_b31_t5 | Stanzas ("Но не извне — / а изнутри.") — render broke 2 stanzas into big blocks (bug) | Short antithetical stanza lines ⇒ lineated; broken stanza rendering is a bug. |
| g23_b17 | All prose — first-line-indented ~2-row paragraphs in docx | Per-paragraph first-line indent + multi-row wrap ⇒ prose (the clearest prose signature). |
| g27_b67_t2 | Confusion likely from `***`; mangled into one paragraph in prose render | `***` is a hard boundary that must be encoded strongly so LLMs treat it as a wall; not a label conflict. |
| g29_b69_t0 | Lineated prose — long answer lines the author block-indented to highlight intent | Block-indented highlighted lines ⇒ lineated even if content is prose; don't over-optimize the author's chosen device. |
| g29_b69_t1 | Lineated prose — one full sentence per line, block-indented; joining gives ugly gaps | One-sentence-per-line with deliberate cadence ⇒ lineated; tie-break by "which rendering looks/reads better." |
| g38_b28 | `***` folded into header; answer text starts on header line (bug) | `***` and header parsing bugs corrupt the run; not a real prose/lineated conflict. |
| audit_g14_b07 | Clearly a verse with editorial short breaks ⇒ lineated | Short editorial verse lines ⇒ lineated (panel had this as prose-consensus — a miss). |
| audit_g24_b28 | "Почему это сложно?" bold question matches context/png ⇒ lineated (prose defensible if consistent) | A standalone bold rhetorical question set off by layout ⇒ lineated, by context. |
| audit_g31_b13 | Technically prose (indents+wrap) but dense by intent ⇒ all-lineated more defensible | Dense packed clauses, prose markers weak ⇒ lean lineated (soft). |
| audit_g33_b66 | Visually dense, no indents ⇒ lineated most defensible (prose also OK) | Tight flush stack of one-clause lines, no prose markers ⇒ lean lineated (soft). |

Cross-cutting positive tests for lineated the human applies repeatedly:
**contrast/opposition across lines; parallel/enumerated structure; verse-stanza
membership; one-sentence-per-line cadence; whole-line/block indentation;
absence of prose paragraph markers (no first-line indent, no inter-para gap);
section-wide stylistic consistency.**

---

## 6. Concrete edits to `data/gold_block2/reader_brief.txt`

The current brief causes the systematic under-lineation through three lines.
Proposed replacements (drop-in):

**(a) Remove the prose default; make the question intent-first.**
Current:
> "prose … This is the SAFE DEFAULT — when unsure, choose prose."
> "Be conservative: over-lineating ordinary prose is the costly error."
Replace with:
> "There is NO safe default. Decide from the author's intent signals below.
> In THIS corpus most contested lines are deliberately lineated, and the common
> error is the opposite of what you'd expect: flattening intended structure into
> prose. If after weighing the signals you are genuinely 50/50, lean LINEATED.
> Under-lineation (collapsing a deliberate line into a paragraph) is the costly
> error here, not over-lineation."

**(b) Add a "lineated includes prose-lineated" clause to the lineated definition.**
> "lineated also includes PROSE-LINEATED: ordinary prose sentences (sometimes
> long enough to wrap) that the author deliberately set ONE PER LINE for cadence
> or emphasis. The content being prose does NOT make it prose-class — ask whether
> joining the sentences into a wrapped paragraph would lose a deliberate rhythm
> or emphasis. If yes, it is lineated."

**(c) Add an explicit "how to read indentation" section (the missing rule).**
> "INDENTATION IN THE DOCX PAGE — read the shape, not the presence:
>  • FIRST-LINE indent only, with wrapped lines running to the LEFT MARGIN and
>    the block spanning several rows = the Russian prose-paragraph marker
>    (красная строка) ⇒ evidence for PROSE.
>  • EVERY line of the unit indented as a standalone short line (hanging/block
>    indent; lines don't fill the column; gaps between them) ⇒ the author is
>    marking each line as a unit ⇒ evidence for LINEATED.
>  Discriminator: does the indent recur once per paragraph (prose) or once per
>  line (lineated)? Do continuation lines wrap to the margin (prose) or does each
>  line stand alone (lineated)?"

**(d) Add the density tie-breaker.**
> "DENSITY: a tight stack of short, complete clauses/sentences with NO first-line
> indent and NO inter-paragraph gaps (no prose-paragraph machinery) leans
> LINEATED — the only coherent reading of that layout is line-as-unit. This is a
> soft tie-breaker; confirm against the rest of the section's style."

**(e) Add the positive lineation tests + consistency rule.**
> "Strong signals FOR lineated: contrast/opposition across consecutive lines
> ('не X — а Y'); parallel or enumerated sequences (Articles, repeated question
> forms, answer markers); membership in a verse/litany/prayer/vow passage; one
> complete sentence per line forming a cadence. When a section repeats a
> 'collective' structure (Articles, Q&A, invocations), label it CONSISTENTLY —
> the author styles parallel content uniformly, so prefer the section-wide
> reading over per-line waffling."

**(f) Fix the render-trust instruction (bug-aware).**
> "The DOCX page is the sole AUTHORITY. The PROSE and LINEATED comparison renders
> currently contain parsing bugs (whole passages collapsed into one paragraph,
> verse styling shown where there is no verse, stanzas and *** mis-split). When a
> render conflicts with the DOCX page, TRUST THE DOCX PAGE. Do not infer the
> label from how the comparison renders look."

These six edits target the exact failure modes in the notes: (a)+(b) kill the
prose tilt and name prose-lineated; (c) supplies the indent rule the panel never
had; (d) the density tie-breaker; (e) the positive tests + consistency the human
leans on; (f) stops readers being misled by the known render bugs. Together they
would have flipped the 12/12 under-lineation audit misses, every one of which
was a prose→lineated correction explainable by (a), (c), or (e).
