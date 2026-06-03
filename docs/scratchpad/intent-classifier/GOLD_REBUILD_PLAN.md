# Gold rebuild plan — lineation (prose vs lineated)

Status: DRAFT for review. Methodology validated 2026-06 (see commits + `[[lineated_prose_not_brief_separable]]`).
Not authoritative until the user approves scope + budget.

## Goal
A page-grounded GOLD dataset labeling each **body display-line** of the ~75 Russian books
**prose vs lineated**, accurate enough to (a) distill a cheap student model that labels the full
corpus, and (b) feed the production converter's lineation pass. Verse register is a LATER layer
(decide prose-vs-lineated first); see `[[book41_real_verse_example]]`.

## Validated components (don't re-litigate)
- **Substrate:** Slice-0 votability mask observed at the STRUCTURAL seam (`normalize(stop_before_lineation=True)`) — `needs_review` is honest (~1.5%), join sound, renders faithful. `[[needs_review_is_verse_span_drop]]`
- **Reader brief:** **v5** (`data/phaseb/reader_brief_v5.txt`) — structure-first, un-capped; generalizes to unseen books (prose held 100% across 4 prose books, lineated up). Frozen as production brief.
- **Panel readers:** **grok** (best: FRESH lineated 83%, prose 100%), gemini-pro, deepseek-v4-flash(text). glm only as extra (over-lineates prose SOLO — never solo for prose). Drop owl/mimo/minimax.
- **Aggregation (recommended, not theorem):** **grok-led decision**; the rest of the panel as a
  **disagreement detector**. 5-rep gate-strict per reader (>=3/5 agree else abstain; abstain/missing → needs-review).
- **Single-pass noise ~19%** → 5-rep aggregation is mandatory.

## Pipeline (per region)
1. `frame` (free): segment all books; stratify (wrap_prose / hardbreak / mid_gap / mid_flat / tiny / toc). `n_review` surfaced.
2. `sample`: stratified sample of runs (TOC excluded; tile long runs). Decide N per stratum.
3. `package --force` (free, heavy LibreOffice): composite per region = DOCX page | prose render | lineated render + structure listing. (renders verified faithful.)
4. **Panel (paid):** each reader (grok, gemini-pro, deepseek-flash) × **5 reps** with **v5 brief**, gate-strict aggregate per reader.
5. **Decision:** grok-led label per line. **Route to HUMAN** when: grok and the panel-majority DISAGREE; OR the line is `needs_review` (unmapped/mixed); OR it's a soft/prior-dependent case (book-consistency-driven, e.g. dense prose in a lineated book — flag, don't auto-decide).
6. **Consensus gold** = grok-led-where-confident + human-adjudicated-where-routed.
7. **Distill** a student on the gold; student labels the full corpus; feed the converter.

## Scope / sampling (OPEN — needs user decision)
- Gold is a SAMPLE, not the whole corpus (~353k body lines is too much to panel). The student generalizes.
- Proposal: stratified sample ~N regions/book biased to the ambiguous strata (mid_gap/mid_flat) where the decision matters; cover all ~75 books for breadth.
- Open: target gold size (lines)? per-book floor? how much human-routing budget (human time)?

## Cost tiering (paid steps = panel)
- Panel cost ≈ readers × reps × regions × per-call. grok/gemini are vision (pricier); deepseek-flash text (cheap).
- Tier the run: cheap text readers first as a screen, then the vision readers; stop early on failures (the bench HARD-GUARDS empty output now).
- Budget is a hard ceiling — fail loud, never silently.

## Reporting discipline (carried from this validation)
- Gate-strict aggregation (abstain/missing = wrong/needs-review); report COVERAGE per reader (not all readers hit 100% — e.g. glm 75/79 on the wide set).
- Soft/prior-dependent human labels → report as a hard-only sensitivity stratum, not silently folded in.
- N regions/books stated precisely (regions != books).

## Risks
1. **grok single-point-of-failure** — grok-led depends on one vendor model; the human-routing-on-disagreement net is what guards it. If grok regresses on a model update, the panel quorum + human catch it.
2. **Soft boundary is intrinsic** — prior-dependent/dense-prose lines are genuinely fuzzy; the human is the authority there (route them).
3. **Budget** — full panel over a large sample is the main spend; tier + cap.
4. **Substrate Slice-1+** — clean SourceFate/per-line provenance is deferred; `needs_review` lines are routed/flagged, not silently trusted.

## Immediate next actions (on user go)
1. Promote v5 → production brief (user confirms).
2. Decide gold scope (size, per-book floor, human-routing budget).
3. Build the aggregation+routing scorer to production quality (current `score_wide`/`score_v5` are scratchpad-grade).
4. Tier-1 cheap-reader screen on the sample → Tier-2 vision → aggregate → route → human → consensus → distill.
