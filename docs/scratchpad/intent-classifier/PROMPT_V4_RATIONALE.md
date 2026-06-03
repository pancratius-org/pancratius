# Reader brief v4 — design rationale (candidate, to A/B vs baseline)

This is a **v4 candidate**, not a final. It is meant to be A/B-tested against the current baseline
(`reader_brief.txt`) and v3 (`reader_brief_fewshot.txt`) under the existing eval harness, not shipped
on assertion. Single-pass labels are ~19% unstable run-to-run, so any single comparison is noisy;
judge it on aggregate over multiple runs and across the strong readers (grok, gemini-pro,
deepseek-flash).

## Reconciliation pass against the human-adjudicated regions

I role-played the reader: applied v4 to each adjudicated region's structure listing (text-only path)
and checked whether the brief, as written, reaches the editor's verdict. Summary:

- v4 reaches the editor's verdict on every adjudicated region checked. Lineated regions land via the
  short-nowrap-run cue (`g06_b40`, `g18_b60_t0/t1`, `g22_b31_t1/t3/t4`, `g14_b07`), colon/dash members
  (`g04_b73`, `g22_b31_t3`), single-sentence split (`g18_b60_t1`, `g18_b60_t0` 5166/5167), and
  standalone bold header-lines (`g05_b37`, `g38_b28`). Prose lands via WRAPS + whole-sentence chains
  (`g23_b17`, `g09_b16_t2`).
- **The hard case — `g27_b67_t0/t1` (narrative, editor: lineated).** This is flowing *story* content
  ("В старом почтовом отделении, / которое давно хотели закрыть…") set as short nowrap lines. By
  CONTENT a reader could call it prose; the editor called it lineated because the lines are short and
  do not wrap — the author broke them on purpose. v4 had the right cue ("short successive nowrap
  lines") but its PROSE list still says "narrative/dialogue reads as continuous sentences," which
  could pull a reader toward prose here. Fix: the lineated cue now states explicitly that short
  non-wrapping lines hold EVEN WHEN the content reads like narrative/dialogue — they are not prose
  just because the words would make sense as a paragraph. This closes the only near-miss I found.
- **`g05_b37` validates split runs:** only the bold list *headers* are lineated; their WRAPS prose
  bodies are prose. v4's per-line, split-run handling reaches this.

Cues added from the user's adjudication intuitions (all consistent with the regions above): (a) the
"four words, then five, none reaching the wrap column → can't be prose" test, now the lead lineated
cue; (b) a single sentence split across two lines as its own intent signal; (c) author indentation
as weak corroborating evidence (never decisive — matches `g23_b17`/`g09_b16_t2`, where indentation
appears on PROSE too).

## What failed before, and what v4 changes

- **v1 (permissive) / v2 (lp2, restrictive)** only slid the prose↔lineated threshold. Recall and the
  prose guardrail moved together (anti-correlated), no discrimination gain. v4 does not push the
  threshold; it changes the *decision variable* from "is this grammatical / could it be joined" to
  "does the break do reader-facing work," which is what actually separates the two classes.
- **v3 (fewshot)** added concrete examples (good) but (a) framed the task as "name the device or it's
  prose," which suppresses real lineated-prose where intent is visible but unnamed, and (b) included a
  malformed paraphrase ("Если мамона…") not quoted from source. v4 keeps concrete anchors but quotes
  them exactly, and explicitly de-couples lineation from naming a device.

## How v4 addresses each critique point

1. **Three concerns separated.** The brief is now sectioned: task definition (prose=join-safe vs
   lineated=join-damages), THE ONE QUESTION (the decision variable), EVIDENCE (the hierarchy),
   STRONG SIGNALS + DEFAULT + CONSISTENCY (the editorial policy), then ANCHORS. Definition, evidence,
   and policy no longer bleed into each other.

2. **Centered on reader-facing function, not device-naming.** "THE ONE QUESTION" says decide by
   function, and explicitly: "You do not have to label the device… Many lineated passages are plain
   declarative sentences whose intent shows only as short-line cadence, density, or page shape — that
   counts." This is grounded in the editor's own notes: `g29_b69_t1` ("Not a verse/litany/etc, just
   lineated prose"), `g31_b13` ("technically prose… but it has to be 'dense' by intent"),
   `g33_b66` ("Technically lineated — written visually dense"). The editor repeatedly lineates on
   density/page-shape with no nameable device, so v4 must permit that.

3. **Strong cues made explicit and directional.** Toward-lineated: short successive nowrap lines
   (named the single strongest cue, per editor's `g04_b73` / `g18_b60` / `g08_b55` reasoning); colon-
   led short members; contrast/opposition split; emphatic fragments / vows / prayers / stanza gaps.
   Toward-prose: long WRAPPING sentences; whole multi-clause sentences with anaphora (the editor's
   `g23_b17` logic, and the explicit "parallel WHOLE sentences = prose" boundary); clean join. The
   WRAPS/nowrap flag is given a concrete meaning (wraps = long by nature; nowrap = ends short by
   choice) so text-only readers can act on it.

4. **Base-rate hints removed; SAFE DEFAULT softened.** No "most text is lineated / keep recall high"
   and no "prose is the SAFE DEFAULT." Replaced with: "Choose prose only when the breaks carry no
   visible reading function. Do NOT default to prose merely because the text is grammatical, or could
   in principle be joined." This directly counters the under-lineation bias the baseline produced
   while still giving a tie-breaker ("reads MORE TRUE broken than joined" → lineated).

5. **No single-cue decisions.** EVIDENCE is an explicit weigh-in-this-order list (page → render
   comparison → structure), prefaced "do not decide from one cue alone." Indentation is demoted to
   "weak evidence only," matching the editor treating indentation as a hint, never a rule
   (`g09_b16_t2`, `g23_b17`, `g10_b19` all involve indentation that does NOT settle the call).

6. **Works for vision AND text-only readers.** Evidence step 1 (page) and step 2 (render comparison)
   are vision-only and flagged "text-only readers skip to step 3." The STRONG SIGNALS and DEFAULT are
   stated in terms of the structure listing (WRAPS/nowrap, short runs, colon/dash, sentence
   completeness), so the policy is fully actionable from text alone.

## The mechanism, stated plainly (v4 opening)

v4 names the actual mechanism up front: the author USUALLY ended each line with Enter (not
Shift+Enter), so MOST lines are their own paragraph and the break type carries no information. Intent
lives in how he STYLES those paragraphs: prose set DENSE (tight, filling, running on), lineated set
APART (spacing, indentation, short stopping lines). Crucially he styles this DIFFERENTLY per
document, so there is no global threshold; each page must be read on its own terms. Stating the
mechanism gives the reader the *why* behind "read the whole page," and the per-document point is
grounded in the editor repeatedly opening the book for in-context calibration (`g04_b73` "is book or
a section prose heavy? is book line-heavy?", `g06_b40` "I had to open the book to see how it's done
in other sections", `g05_b37` "Judge in-context"). v4 therefore tells readers NOT to import a rule
from another book.

**Not "always Enter" — soft breaks exist and are a strong cue.** Earlier drafts said the author used
Enter on *every* line; that is false and discards a reliable signal. He sometimes (rare) used a soft
break (Shift+Enter) to hold several lines INSIDE one paragraph — an explicit, lossless marker that
those lines are a deliberate multi-line unit, i.e. near-certain lineated. In the structure listing
this surfaces as sub-lines under one paragraph index (incrementing `.sub`), confirmed in the data:
`g14_b07` verse (`624.0/.1/.2/.3`) and `g04_b73` article members (`10307.0/.1/.2/.3`) are `.sub`
groups, whereas the prose region `g23_b17` is all `.0` (one paragraph per line). v4 now tells readers
that a `.sub > 0` group is the author deliberately keeping lines together, and that this cue is
available to text-only readers too (it does not need the image).

## DOCX-page-wins tiebreaker (added in v4)

Added an explicit "On CONFLICT" rule to EVIDENCE: when a render disagrees with the DOCX page, trust
the page. This is grounded directly in the editor's notes, where the dominant hard case is a *buggy
render*, and the editor resolves it by falling back to the page:
`g23_b17` ("the whole 'prose' rendering… mangled as a single paragraph. Bug" → relies on docx png),
`g00_b64_t2` ("a bug in parsing"), `g18_b60_t4` ("I rule prose out based on docx png"),
`g27_b67_t2`, `g38_b28`, `g24_b28`, `g31_b13`, `g09_b16_t2` (all "rendering bug," page used as truth).

**The page IS authoritative for the verdict, not just layout.** The DOCX page is not ambiguous about
prose vs lineated — the author encoded intent visually (gaps, indentation, density, short-line runs
together), and to a human the answer is usually obvious on the page. The ambiguity lives in the
*lossy extraction* (renders and the WRAPS/nowrap listing), not in the page. So v4 tells image readers
to read the verdict off the page and treat disagreeing renders/listings as extraction artifacts. This
matches the editor reading the verdict straight off the png: `g23_b17` ("as a human, I clearly see…
2-row lines, each starting with an indent — makes it OBVIOUS it's not lineated"), `g29_b69_t0`
("author used indents to emphasize"), `g00_b64_t0` ("incoherent with png and principle of reading
flow").

The single guarded exception is the degenerate cue "each line sits on its own row → lineated": that
one feature is a pure artifact of the Enter habit and proves nothing. v4 isolates exactly that cue
("the one thing the page does NOT prove by itself is raw row position") while affirming every other
visual signal on the page as real. This is narrower and more correct than an earlier "layout facts,
not the verdict" framing, which wrongly demoted the page.

Text-only guard: those readers have no page; v4 tells them to lean on the listing's *content* cues
(short nowrap runs, colon/dash members, sentence completeness, WRAPS) rather than its block grouping,
which can be mis-extracted.

Risk: strengthening the page's pull is right when renders are buggy (the common case), but a reader
could still over-read raw row position despite the explicit carve-out. Worth watching whether vision
readers drift toward lineated relative to text-only readers after this change.

## Editor-note grounding (specific lines mined)

- Short-member litany after a colon is lineated even though "Это право включает:" alone is borderline
  — `g04_b73` note: members are "clear contrast, opposition," and the colon header rides with them
  for "style coherence." → anchor 1 + the colon/dash cue.
- A single sentence split to stage an opposition is lineated — `g18_b60_t1` note: "between … and …,
  same arguments, but it's even a single sentence." → anchor 2 + "even within one grammatical
  sentence."
- Lineation can rest on plain sentences set short with no device — `g29_b69_t1` note: "Not a
  verse/litany… just lineated prose," tell by "how does it look better." → anchor 3 + the
  function-not-device framing.
- Anaphoric WRAPPING sentences are prose — `g23_b17` note: "clearly all prose… on average 2-row
  lines." → anchor 4 + the toward-prose "whole multi-clause sentences, even with anaphora" rule.
- Indentation as weak hint, not rule — `g09_b16_t2`, `g23_b17`, `g10_b19`. → INDENTATION demoted.

## Example source rids (exact) and disjointness assertion

Anchors quoted verbatim from `data/render_audit/reader_pkg.json` `structure` fields:

| Anchor | Source rid | Lines |
|---|---|---|
| LINEATED — colon + short members | `g04_b73` | 10306.0, 10307.0–10307.1 |
| LINEATED — one sentence staging a pair | `g18_b60_t1` | 5180.0–5183.0 |
| LINEATED — short sentences set a contrast | `g29_b69_t1` | 951.0, 952.0 |
| PROSE — anaphoric wrapping sentences | `g23_b17` | 126.0 |

**Disjointness:** none of `g04_b73`, `g18_b60_t1`, `g29_b69_t1`, `g23_b17` is among the 13 scored eval
regions {g00_b64_t2, g29_b69_t0, g05_b37, g18_b60_t3, g22_b31_t5, g24_b28, g31_b13, g33_b66,
g00_b64_t1, g34_b63, g27_b67_t2, g09_b16_t2, g10_b19}. Verified set-intersection is empty.
Note the near-misses are different *tiles of the same book/region group* but distinct rids with
distinct line ranges: eval has `g29_b69_t0` and `g18_b60_t3`; anchors use `g29_b69_t1` (lines 947–955)
and `g18_b60_t1` (lines 5172–5199) — non-overlapping line sets. All four anchors are from the
non-eval adjudicated set explicitly listed as usable. Quotes were copied from the structure listing,
not paraphrased (the v3 "Если мамона…" defect is not repeated).

## Risks / overfitting concerns

- **Anchor leakage by proximity.** Anchors share book/region groups with some eval tiles (b73, b60,
  b69, b17). The lines differ, but a reader that memorizes "book 73 = lineated" could transfer. The
  anchors teach a *principle* (short members vs wrapping sentences), not a book verdict, which
  mitigates this; still worth checking eval deltas on b60/b69/b73 tiles specifically.
- **Recall-vs-guardrail re-balance.** Softening the prose default is intended to lift lineated recall.
  If it instead just slides the threshold the other way (more false lineated on `g23`/`g09`-type
  prose), v4 has the same failure mode as v1/v2 — watch the prose guardrail (false-lineation rate on
  the consensus-prose regions) alongside lineated recall. The discrimination claim only holds if
  recall rises WITHOUT the guardrail degrading proportionally.
- **"Reads more true broken than joined" is subjective** and may add variance for text-only readers
  who can't see the page. Monitor text-only vs vision reader agreement.
- **Three short anchors may underspecify** the dense-page / no-device lineated-prose case
  (`g31_b13`, `g33_b66` types), which is the hardest. Resisted adding a 5th anchor to keep the brief
  actionable; if dense-prose recall lags, that is the first place to add an anchor (from a non-eval
  region — `g38_b28` or `g22_b31_t4` are candidates).
