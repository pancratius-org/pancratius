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

**RESOLVED (region-level review, user from the book):** each item is a **title line + a flowing
prose body**. The four bodies `388.1/390.1/392.1/394.1` are **PROSE**; only the titles
`388.0/390.0/392.0/394.0` are lineated labels. So:
- The gate's accepted PROSE for the bodies was **CORRECT** — `392.1` was **not** a false-accept. The
  audit "disagreement" was a wrong HUMAN blind-per-line guess (confounded by the mangled prose
  preview + the per-line framing), overturned by region-level review. g05 gold stands; reopen cleared.
- Lesson: the audit flags disagreements to INVESTIGATE, not to assume the gate is wrong. And the
  book prior ("whole book is lineated") would have WRONGLY pushed these bodies to lineated — live
  proof that book-dominant-register must never auto-label. [[lineation_is_hierarchical_prior]]

**The render issue is still real (just not a gate error here).** The prose preview glues a paragraph's
`<w:br>` segments with spaces, so a `bold-title` `<br>` `body` paragraph renders as `**1. Вода** Мир…`.
Vision readers (grok, gemini-pro) vote on that composite, so a mangled prose candidate confounds the
PANEL'S evidence on heading+body shapes — worth making faithful (keep a leading bold heading line its
own line in prose mode). Cosmetic for data integrity (substrate is correct), real for panel evidence.
Reproduce on actual site output before calling anything a production compiler bug.
