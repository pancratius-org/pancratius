# Lineation ML — handoff & roadmap

Status map for the lineation-core work (recover authorial **lineation** intent — *prose* vs *lineated*,
per body display-line, across ~75 RU+EN DOCX books — by distilling an LLM teacher panel into a cheap
interpretable student via **seeded, pool-based active learning**). Transient status; the durable
contracts are `SPEC.md` (data/algorithm) and `ARCHITECTURE.md` (code layout + the active-learning loop).

## Where we are (one line)
The clean-room package, the teacher half (text+vision), the student, and the **eval harness** are
built and green (180 tests). The decision **policy is settled** (legacy anchor-led wins). What's left
is wiring the live decision loop, extracting the validated prompt + orphan labels from the legacy
tree, and the paid 300-line acquire run (needs your API key).

## What WORKS (validated / adopted)
- **Clean-room package** — one per-line `LineRecord` (id+text+inlines+role+votable+source_fate+φ+meta),
  ONE producer (`producer.read_lines`), `LineId(lang,book_id,src_ordinal,sub)` joined by src_ordinal
  with docx/paragraph/line **hash rails**; functional-core / imperative-shell with `store.py` the only
  IO edge. Replaced the old intent-classifier pipeline (a landmine field).
- **Interpretable student** — logistic, φ-only, book-held-out OOF, ~**0.96 balanced-acc** on the
  contested set; **run-smoothing α=0.75** (a composable layer; matches grok, beats other readers).
- **Reproducible physics** — `fill`/`wraps` features measured with a **vendored, hash-pinned Liberation
  Serif** (`src/lineation_core/vendor/`), with a drift-guard vs the live LibreOffice; packaged into the
  wheel/sdist (clone-build-run works).
- **Teacher half** (text + vision) — `tasks` (the `L001→LineId` opaque-key mint), `responses` (the one
  resolution choke point, fail-loud faults), `panel` (ChatCompleter + safe-promote), `recipes`
  (selectors + tiling + CLI), `openrouter` (the OpenRouter SDK adapter), `promote` (validated merge),
  `render` (authored-page vision composites via `pancratius.docx_render`).
- **The brief** — **v5** (structure-first) is the validated production prompt: generalizes to unseen
  books (prose-recall 100% across 4 fresh prose books). Lives in `pancratius-prerewrite-full/.../data/phaseb/reader_brief_v5.txt`.
- **The panel** — core readers **grok / gemini-pro / ds-flash-text** (glm diagnostic-only); slugs in
  the old `scripts/gold/registry.py` (grok→x-ai/grok-4.3, gemini-pro→google/gemini-3.1-pro-preview,
  ds-flash-text→deepseek/deepseek-v4-flash). The panel is **recipe TOML config**, never hardcoded.
- **The decision policy** (`teacher/decision.py`) — pluggable `AnchorLedPolicy`/`EqualMajorityPolicy` +
  `route_with`; mechanism named for its role (anchor is roster config, not "grok").
- **The eval harness** (`evaluation/{datasets,metrics,policy_replay}` + TOML) — replays policies on the
  515-line aligned set (truth ⋈ votes); multi-dimensional metrics (accept-quality vs human-load, kept
  separate; **total-population capture**, not misleading accept-set recall).
- **The acquire set** — `annotations/selections/acquire.json`: 300 least-confident lines, **≤40/book**
  (19 books, broad), razor-uncertain (margin max 0.011).

## SETTLED result — which decision policy
Replay on the 515 historical aligned lines (candidate-composite vote artifact):

    policy            balAcc  autoProse  autoLin  P->L  humanRt
    legacy(2,0.7)      0.969   1.000      0.870     0    0.064     ← WINNER
    unanimous          0.969   0.746      0.664     0    0.287
    equal_majority     0.948   1.000      0.894     0    0.002

**Legacy anchor-led (min_core_agree=2, conf_floor=0.7)** — same accuracy as the unanimous default but
4.5× less human load, captures all 63 true prose, zero prose-mislabeled. Use it live. **Caveat:** this
is settled on the *historical* protocol; monitor on the future page-only/live protocol.

## What we TRIED and REJECTED (don't re-tread)
- **Briefs v1/v2/v3** — only slid the prose/lineated threshold. **v6/v7/v8** — later experiments, NEVER
  validated over v5; do NOT promote a brief by version number.
- **CRF** — didn't beat α=0.75 (kept as a documented negative-result-with-insight). **Asymmetric
  smoothing** — NULL (reshuffles one line; nested CV flat).
- **`<w:br>`→lineated auto-rule** — dropped; page-verification found prose counter-examples ("verified
  81/81" was hollow). Everything votes; no hard rule.
- **Roleplay / immersion readers** (gpt-4o-as-Светозар) — worst on the structural lineation cut;
  roleplay helps register, hurts lineation.
- **Equal-majority cross-reader** — under-lineates hard lineated (prose-biased readers outvote the
  anchor). **My "unanimous" default** — over-conservative, routed too much to humans. Legacy beats both.
- **Candidate prose/lineated render tiles** in the vision composite — confound the gate + drag in a
  Playwright/CSS harness; vision is **page-only** (the authored render is the LUPI signal).
- The whole **intent-classifier/** tree — clean-room rewritten; eviction pending (see below).

## DEFERRED (intentional, not forgotten)
- **The live escalation driver** — `decision.py` is offline ACCEPT/HUMAN only; live needs
  `ACCEPT/ESCALATE/ROUTE_HUMAN/NEEDS_RERUN` + adaptive reps (run 1, escalate to ~5 on big inter-LLM
  disagreement). Decide `CONF_MISSING`/`LOW_CONFIDENCE` terminal-vs-rerun then (currently TERMINAL,
  inert on the data since conf is fully populated).
- **`student.py` return annotations** — unannotated because `sequence` is lazily imported (keeps
  sklearn off the import path); cleanly annotating fights the `get_type_hints` sweep.
- **`model/` folder** — YAGNI while the student is one file (it's `student/` when it earns one, not `model/`).
- **`809a07c` commit message reword** — blocked: a concurrent site-fonts commit (`6dbd7b2`) landed on
  top, rebase-i is unavailable; safe only once that agent is done.

## ROADMAP — what's left (Codex's order)
1. **Wire the decision live** — `votes.jsonl → route_with(AnchorLedPolicy[legacy], roster) → accepted +
   human queues → adjudicate.html → labels.jsonl`. Build the escalation statuses first (see deferred).
   *Buildable now, no inputs.*
2. **Extract-before-evict** — pull `reader_brief_v5.txt` + the orphan human guardrail labels
   (`responses-fresh-prose-guardrail-*`, `responses-wide-prose-guardrail-*`, `review_gold-stage12.json`
   audit corrections; **exclude book 73** — intentionally pruned political content) from
   `pancratius-prerewrite-full/docs/scratchpad/intent-classifier/`. Then eviction is safe.
3. **Live 300-acquire run** — needs **OPENROUTER_API_KEY** + the ~3 reader model ids + the v5 prompt in
   a recipe (vision, the 19 acquire books). Build with `recipes build`, then `panel` (paid).
4. **Step 6 — the active-learning loop** (the GOAL): panel-label the 300 acquire lines (legacy policy +
   route splits to human), update the student, the confidence-vs-disagreement diagnostic, repeat.
5. **Evict intent-classifier** — after (2); `git rm` tracked + `rm -rf` ~800 untracked / ~1.2 GB.
   Needs coordination (the repo overall is dirty with concurrent work).
6. **Graduate** — promote `physics.py` into `pancratius`; the student becomes a converter lineation-pass.

DO NOT: evict before extracting; run the paid 300-acquire before live routing has the legacy policy +
escalation semantics.

## Process notes
- All large/mechanical work went to **worktree agents** (reorg, /simplify, eval harness) — reviewed,
  not rubber-stamped (caught a `grok_led` kind leak, a `_fonts/` misplacement, a broken package build,
  a metric over-claim). Verify byte-identical via the metric-locks, not just green.
- The user runs **concurrent agents** on the same tree — re-check HEAD before any history rewrite.

## Pointers
- Contracts: `SPEC.md`, `ARCHITECTURE.md`. Memory: `lineation_core_rewrite`, `lineation_panel_canonical`,
  `lineation_eval_and_layout`, `lineated_prose_not_brief_separable`, `abstraction_no_hardcoded_config`,
  `eval_integrity_traps`, `framing_objective_contingent`, `lo_oo_ambiguity_detector`.
- Legacy (for extraction only, never import): `pancratius-prerewrite-full/docs/scratchpad/intent-classifier/`.
