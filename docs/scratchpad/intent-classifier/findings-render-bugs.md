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

**Two consequences:**
1. **A shared false-accept.** `g05_b37 392.1` (the body under "**3. Птица**") was ACCEPTED by the
   gate as prose; the human (page authority) corrected it to LINEATED. The audit caught it — but its
   siblings `388.1 / 390.1 / 394.1` were not sampled and are almost certainly the same wrongly-
   accepted prose. **Reopen the whole g05 enumerated block before treating it as clean gold.**
2. The mangled prose candidate biased the lead: grok read it and voted prose where the page is
   lineated. (Argues for learned per-reader weights by stratum, not a fixed grok-led heuristic.)

**Likely cause (to verify):** the numbered-list / `1.`-label paragraph shape (architecture §12 G2
"no typed numbered-label+body item") isn't preserved at display-line grain in the candidate prose
render. Confirm against the render path — and reproduce on real site output before escalating.

**Action:** reopen the g05 block (region-level); the human reads it as lineated. Do not call book
37's accepted gold clean until swept. [[lineation_is_hierarchical_prior]]
