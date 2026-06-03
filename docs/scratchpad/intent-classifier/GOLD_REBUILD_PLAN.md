# Gold rebuild plan — lineation (prose vs lineated)

Status: DRAFT execution spec for review. Methodology validated 2026-06 (`[[lineated_prose_not_brief_separable]]`).
Not authoritative until the user approves scope + budget. Defaults marked [calibrate] are starting points.

## Data tiers (these are distinct — don't conflate)
- **GOLD** = a human/panel-labeled SAMPLE of body display-lines = trusted ground truth. NOT the whole corpus.
- **SILVER** = the full ~75-book corpus labeled by the distilled STUDENT (trained on gold).
- **PRODUCTION OUTPUT** = the converter's lineation-pass result (student-as-a-pass) rendered on the site.
Flow: build GOLD accurately → distill student → SILVER-label corpus → converter pass. (Verse register is a later layer; `[[book41_real_verse_example]]`.)

## Evidence hierarchy (authority)
- The **DOCX page screenshot (LibreOffice) is the VISUAL AUTHORITY** for how the author's lines sit (stanza gaps, indentation, density, short-line runs).
- The **prose / lineated render panels are candidate render HYPOTHESES, never ground truth** — they can be wrong (several render bugs were fixed). They are comparison aids.
- The **structure listing** (text + WRAPS/nowrap + emphasis + .sub group + hard markers) is the text-reader's evidence.

## Validated components (don't re-litigate)
- **Substrate:** Slice-0 votability mask at the STRUCTURAL seam (`normalize(stop_before_lineation=True)`); `needs_review` honest (~1.5%), join sound, renders faithful. `[[needs_review_is_verse_span_drop]]`
- **Reader brief:** **v5** (`data/phaseb/reader_brief_v5.txt`), frozen production brief — generalizes to unseen books (prose held 100% across 4 prose books, lineated high).
- **Panel readers:** grok (best), gemini-pro, deepseek-v4-flash (text). glm only as extra (NEVER solo for prose — it over-lineates). Drop owl/mimo/minimax.
- **Baseline aggregation POLICY (current best heuristic — NOT final truth machinery):** grok-led decision + the rest of the panel as a disagreement detector. *SOTA-later:* once enough human truth exists, LEARN/calibrate per-reader reliability by stratum (weighted aggregation) to replace the grok-led heuristic.

## Adaptive rep protocol + ACCEPTANCE GATES
1. **1 initial rep** per core reader (grok, gemini-pro, ds-flash) on every region.
2. **ACCEPT** a line (no escalation) iff ALL: grok-label == panel-majority-label; grok conf ≥ 0.7 [calibrate]; ≥2/3 core readers labeled it AND agree; coverage complete (no abstain/parse-fail among core).
3. **ESCALATE to 3 reps** iff ANY: grok ≠ panel-majority; grok conf < 0.7 ("low-margin"); a core reader abstained or parse-failed; `needs_review`; soft/prior-dependent flag.
4. Cap at **3 reps** for most contested regions; **5 reps** ONLY for calibration batches, model audits, or stubborn ambiguous regions.
5. **Parse failure** = abstain for that reader; one retry; if persistent → the line escalates (never silently dropped). The bench HARD-GUARDS empty output.
6. **Route to HUMAN:** persistent grok/panel split after 3 reps; `needs_review` real-content; soft/prior-dependent (book-consistency) cases.

## RANDOM AUDIT of accepted lines (catches systematic error)
Routing only DISAGREEMENTS misses systematic bias (the earlier audit caught panel UNDER-lineation that the panel agreed on). So: randomly sample ACCEPTED high-confidence lines, stratified by stratum + book — especially **grok-led-accepted PROSE and lineated-prose** — for human spot-check. Track accepted-line error rate per stratum/book; if a stratum/book shows systematic error, re-open it. [audit rate: calibrate, e.g. 5-10% of accepted lines.]

## Pipeline (per region)
1. `frame` (free): segment all books; stratify; surface `n_review`.
2. `sample`: stratified (TOC excluded; tile long runs) — emit a **sample manifest** (seed + region list).
3. `package --force` (free, heavy LibreOffice): composite = DOCX page | prose | lineated + structure; record composite hash.
4. **Panel (paid):** core readers, v5 brief, **adaptive reps**, gate-strict per reader.
5. **Decision:** baseline grok-led; accept / escalate / route per the gates above.
6. **BLOCK reconstruction:** the site needs coherent BLOCKS (runs), not only per-line labels. Reconstruct block boundaries from the line labels; metrics: **boundary-F1, exact-block-match, + visual render-diff spot-check** (does the reconstructed block render true against the page?).
7. **Consensus GOLD** = accepted + human-routed.
8. Distill (below).

## Reproducibility / artifact contract (manifest per gold run)
Record, so any gold line is traceable to exact inputs: docx package hash(es); v5 prompt hash; per-reader model id + provider + date; run-id (`tag_suffix`); raw replies (committed); package/composite hash; scorer version; random seed + sample manifest.

## Distillation (student)
- Split GOLD **by BOOK** into train/val/test — no book in two splits (no leakage). NEVER tune on the final test split.
- **Metrics:** by stratum; hard-only vs soft (all-labels) sensitivity; prose-recall; lineated-recall; balanced-acc; **block metrics (boundary-F1, exact-block-match)**; abstain behavior.
- **Feature contract for the converter:** student consumes structure features (text, WRAPS, .sub group, emphasis, indent, neighbor context) + optionally the page; emits per-line label + confidence; low confidence → abstain → `needs_review` (human/converter fallback), never a silent guess.

## Scope / sampling (OPEN — user decision)
- Gold = SAMPLE. Proposal: stratified ~N regions/book biased to ambiguous strata (mid_gap/mid_flat); cover all ~75 books.
- Open: gold target size (lines); per-book floor; human-routing budget; audit budget (your adjudication time).

## Cost tiering
- **Adaptive reps = the main cost lever** (most regions 1 rep/reader; only the contested minority escalate to 3; ~none to 5).
- **Cheap text reader (ds-flash) is a TRIAGE/escalation signal ONLY — it does NOT produce final accepted gold alone** (text-only, over-lineation-prone). Final acceptance requires the grok-led + vision-panel gate.
- Tier: triage → vision → aggregate → route. Budget = hard ceiling; fail loud, never silently.

## Risks
1. grok single-point-of-failure — it's the baseline heuristic; disagreement-routing + random audit + (later) learned weighting are the net.
2. Soft/prior-dependent boundary is intrinsic — human is authority; route.
3. Budget — adaptive reps + tiering contain it.
4. Substrate Slice-1+ (clean SourceFate) deferred; `needs_review` routed/flagged, not silently trusted.
5. Block reconstruction from per-line labels can mis-segment — boundary-F1 + render-diff catch it.

## Immediate next actions (on user go)
1. Promote v5 → production brief.
2. Decide gold scope (size, per-book floor, human-routing + audit budget).
3. Build production-grade aggregation + routing + audit + manifest tooling (current `score_*` are scratchpad-grade).
4. Run tiered (triage → vision → aggregate → route → human → consensus → distill).
