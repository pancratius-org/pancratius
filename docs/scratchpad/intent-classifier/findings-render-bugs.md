# Candidate-render / IR-view fidelity issues surfaced during gold adjudication

Issues found while a human adjudicated the gold task against the DOCX page. Status: these are
proven in the GOLD-PIPELINE candidate render / `ir_view` only. NOT yet proven on actual site output
— reproduce against the real Astro/Markdown render before calling any of them a production compiler
bug.

## R1 — prose CANDIDATE render collapses an enumerated list into one paragraph (g05_b37)

**Region:** book 37, `g05_b37`, the items "**1. Вода**", "2. …", "3. …", "4. …" (lines 388/390/
392/394) and their body lines (`.1` sub-lines).

**Observed (user):** on the DOCX page each enumerated item sits on its OWN line. The `prose`
CANDIDATE render mangles them into a single flowing paragraph — the line boundaries are lost.

**RESOLVED (user, authority, from the book): the bodies are LINEATED.** The whole book 37 is a
lineated work, so the four parable bodies `388.1/390.1/392.1/394.1` are **LINEATED** despite reading
as flowing prose paragraphs in local isolation. So:
- The gate accepted them as **PROSE → WRONG. These are FALSE-ACCEPTS** (Codex's original framing was
  right; an earlier "RESOLVED to prose" edit here was my misread of the user — corrected).
- `392.1` was the only one in the audit; the human (correctly) said lineated → the audit caught a
  REAL gate error. Its siblings `388.1/390.1/394.1` were not sampled and are the same false-accepts.
  **Sweep all four bodies to LINEATED.**
- THE LESSON (strongest case yet for book context): a body that reads as prose LOCALLY is lineated
  because the BOOK is lineated — book-level register is decisive, and the per-line gate (no book
  context) systematically mislabels prose-looking lineated bodies. Note the distinction the priors
  memo must keep: the user's "the whole book is lineated" is AUTHORIAL ground truth about the book,
  NOT a statistical prior inferred from gate labels. Authorial per-book register is exactly the
  metadata worth capturing; statistical book-priors still must not auto-label. [[lineation_is_hierarchical_prior]]

**The render issue is separate and still real.** The prose preview glues a paragraph's `<w:br>`
segments with spaces, so `bold-title` `<br>` `body` renders as `**1. Вода** Мир…`. Vision readers
(grok, gemini-pro) vote on that composite, so it confounds panel evidence on title+body shapes —
worth making faithful. But it is NOT the cause of the body mislabels: those are a book-context gap in
the gate, not a render bug. Reproduce on real site output before calling anything a production bug.
