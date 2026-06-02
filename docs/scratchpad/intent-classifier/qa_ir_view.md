# Adversarial QA — ir_view.py substrate (executed by lead; QA agent only planned)

The delegated QA agent produced a strong test PLAN (tasks/plan.md, tasks/todo.md) but
did not execute it. Lead ran the high-value checks directly. Results:

## Defects found and FIXED
1. **[HIGH] BlockQuote → ROLE_BODY fallthrough.** 65 BlockQuotes in a 20-book sample
   hit `case _`; BlockQuote holds `.blocks` not `.inlines`, so `getattr(b,"inlines",[])`
   yielded an EMPTY line → quoted material silently became an empty body (prose/verse)
   candidate. FIX: explicit `ir.BlockQuote` case → `ROLE_BLOCKQUOTE` (hard boundary,
   flattens child paragraphs for preview). `case _` now → `ROLE_OTHER` (hard boundary),
   never silently body. (Was the exact class of bug the external audit warned about.)
2. **[MED] Ghost body paragraphs.** A `<w:p>` whose only inline is an empty `Emphasis`
   (`** **` husk; non-empty per adapter, flattens to "") became a ROLE_BODY with zero
   real lines (2 in #27). FIX: a body para yielding zero non-empty lines → ROLE_EMPTY
   (separator), never body. Verified 0 ghosts across #27/#68/#30/#02/#13.

## Invariants verified (5 structurally-diverse books)
- 0 ghost body paragraphs; dense/strictly-increasing index integrity.
- segments() never contains a hard OR soft boundary role (runs never cross a header/
  ***/blockquote/pseudo-header). boundary-in-run = 0 everywhere.
- keystone (#27 para172): 4 separate bold non-wrapping lines (not 1 merged wraps=True).
- corpus: 4,814/5,231 (92%) multi-<w:br> paras have all-lines-non-wrapping (audit mirror).
- emphasis: mixed-bold paragraphs (747 in #27) correctly get bold_all=False with
  per-line bold flags; line<=br+1 invariant holds across 2,076 multi-line paras.
- pseudo-header / speaker-label inference firing (#68 "Место в литературе" bounded;
  #27 297 speaker-labels, 151 pseudo-headers).

## Still OPEN (lower priority, for the next pass)
- pseudo-header / speaker-label PRECISION not yet measured against rendered pages
  (over/under-firing). It's a SOFT boundary, so errors are recoverable, but worth a
  calibration check on a stratified sample before the 3-way gold.
- per-line `fill` reading-column is per-book sectPr (validated earlier); re-confirm it's
  meaningful now that it's load-bearing per LINE not per paragraph.
