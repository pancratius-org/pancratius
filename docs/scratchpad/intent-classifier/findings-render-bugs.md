# Render / IR bugs surfaced during gold adjudication

Bugs in the production converter/render found while a human adjudicated the gold task against the
DOCX page. These are about the COMPILER (pancratius), not the gold pipeline.

## R1 — prose render collapses an enumerated list into one paragraph (g05_b37)

**Region:** book 37, `g05_b37`, lines 388/390/392/394 (the items "**1. Вода**", "2. …", "3. …",
"4. …").

**Observed (user):** on the DOCX page each enumerated item sits on its OWN line. The `prose`
candidate render mangles all four into a single flowing paragraph — the line boundaries are lost.

**Why it matters:** a numbered/enumerated sequence with one item per line is lineated, not prose.
The prose render erasing the boundaries (a) is wrong on the page, and (b) actively misled the panel
— grok read the mangled prose render and voted PROSE, while the page-faithful reading (and the rest
of the panel) is LINEATED. So the render bug propagated into a wrong lead vote.

**Likely cause:** the numbered-list / `1.`-label paragraph shape (architecture §12 G2 "no typed
numbered-label+body item") isn't preserved at display-line grain in the prose lowering — the items
join as if one paragraph. Confirm against `lower.py` / the prose render path at display-line grain.

**Action:** the gold for these 4 lines is LINEATED (human, page authority). Re-examine book 37's
gold after the render fix; the prose CANDIDATE is not trustworthy for enumerated shapes.

## Note: grok-led is not infallible
g05_b37 is direct evidence the lead reader can be wrong where a render bug biases it. Argues for
LEARNED per-reader weights by stratum, not a fixed grok-led heuristic. [[lineation_is_hierarchical_prior]]
