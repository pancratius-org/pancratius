# Lineation ML — handoff & roadmap

Status map for the lineation-core work (recover authorial **lineation** intent — *prose* vs *lineated*,
per body display-line, across ~75 RU+EN DOCX books — by distilling an LLM teacher panel into a cheap
interpretable student via **seeded, pool-based active learning**). Transient status; the durable
contracts are `SPEC.md` (data/algorithm) and `ARCHITECTURE.md` (code layout + the active-learning loop).

## Where we are (one line)
The clean-room package, the teacher half (text+vision), the student, the **eval harness**, and now
the **live decision step** (`recipes route`) are built and green (189 tests). The decision **policy
is settled** (legacy anchor-led wins) and **wired live**. What's left is extracting the validated
prompt + orphan labels from the legacy tree, and the paid 300-line acquire run (needs your API key).

## What WORKS (validated / adopted)
- **Clean-room package** — one per-line `LineRecord` (id+text+inlines+role+votable+source_fate+φ+meta),
  ONE producer (`producer.read_lines`), `LineId(lang,book_id,src_ordinal,sub)` joined by src_ordinal
  with docx/paragraph/line **hash rails**; functional-core / imperative-shell with `store.py` the only
  IO edge. Replaced the old intent-classifier pipeline (a landmine field).
- **Interpretable student** — logistic, φ-only, book-held-out OOF; **run-smoothing α=0.75** (a
  composable layer). Under the FIXED-RENDER re-adjudicated truth (see "RE-ADJUDICATED truth"):
  **0.929 CV balanced-acc / 0.954 contested** (prose_f1 0.854, macro_f1 0.914; clears the 0.852
  majority plain-acc again). The recency-era dip (0.844/0.867, prose_f1 0.71) was the render-bug
  contamination — the "φ-prose-shaped but human-lineated" lines WERE prose; re-judged on the fixed
  render, truth and student moved together.
- **Reproducible physics** — `fill`/`wraps` features measured with a **vendored, hash-pinned Liberation
  Serif** (`src/lineation_core/vendor/`), with a drift-guard vs the live LibreOffice; packaged into the
  wheel/sdist (clone-build-run works).
- **Teacher half** (text + vision) — `tasks` (the `L001→LineId` opaque-key mint), `responses` (the one
  resolution choke point, fail-loud faults), `panel` (ChatCompleter + safe-promote), `recipes`
  (selectors + tiling + CLI), `openrouter` (the OpenRouter SDK adapter), `promote` (validated merge),
  `render` (authored-page vision composites via `pancratius.docx_render`).
- **The prompts** — the live reader prompts are committed in `campaigns/prompts/` (`lineation-page.md`
  for vision, `lineation-structure-text.md` for text). The legacy `reader_brief_v5.txt` (structure-first)
  is NOT production — it carries the known-bad g05 caveat (see §2 below); its structure-first framing
  informed the current prompts, but no single legacy brief is the chosen prompt.
- **The panel** — core readers **grok / gemini-pro / ds-flash-text** (glm diagnostic-only); slugs in
  the old `scripts/gold/registry.py` (grok→x-ai/grok-4.3, gemini-pro→google/gemini-3.1-pro-preview,
  ds-flash-text→deepseek/deepseek-v4-flash). The panel is **recipe TOML config**, never hardcoded.
- **The decision policy** (`teacher/decision.py`) — pluggable `AnchorLedPolicy`/`EqualMajorityPolicy` +
  `route_with`; mechanism named for its role (anchor is roster config, not "grok"). The roster +
  policy TOML grammar (`parse_roster`/`policy_from_toml`/`POLICY_KINDS`, typed `*Table` TypedDicts)
  lives HERE, so both the eval harness and the live recipe build policies from it — no `recipes →
  evaluation` import (the forbidden direction).
- **The LIVE decision step** (`teacher/recipes.py::route` + `route`/`ingest --task-id` CLI) — reads a
  routed recipe's `[roster]`/`[decision]`, restricts `votes.jsonl` to the task's lines **AND to votes
  this task produced** (each `PanelVote` is stamped with its `task` at promote), applies the settled
  policy, **auto-accepts → `gate` labels** (anchor conf + roster/diagnostic vote provenance) and
  **routes the rest → a `<task_id>-adjudication` sub-task** the existing `adjudicate.html`/`ingest`
  path consumes. Precedence-safe (never clobbers/re-queues a human/override label — also the
  eval-leakage guard); refuses a re-route that would re-mint an in-flight human queue's keys; and
  since `panel` promotes all-or-nothing, **REFUSES on partial coverage** (a task line with no
  this-task vote ⇒ the panel didn't run; re-panel, or `allow_partial`) rather than route on stale/
  partial evidence. Surfaces `uncovered` + the operational-vs-terminal split instead of the adaptive-
  reps loop (still deferred — it's the paid-run-coupled wrapper, see DEFERRED).
- **The eval harness** (`evaluation/{datasets,metrics,policy_replay}` + TOML) — replays policies on the
  529-line aligned set (truth ⋈ votes); multi-dimensional metrics (accept-quality vs human-load, kept
  separate; **total-population capture**, not misleading accept-set recall).
- **ONE truth store** — `labels.jsonl` holds EVERY label (704 rows = 620 trainable + 82 `holdout`
  eval-only + 2 unmapped-rejected); `eval_sets/*.json` are MEMBERSHIP-only key lists, truth always
  read through `evaluation.datasets.eval_slice` (fail-loud on a member with no label). The 83
  contested-only labels were provenance-traced to the legacy human adjudication exports
  (pilot/gold-block2/queue-audit/wide-prose-guardrail, by normalized text) and homed as
  `holdout` human labels — 82 homed, 1 dropped (`ru:33:894.0` — a legacy idx written as an
  ordinal; the line is unmapped `ru:33:9000894.0` in the corpus). A study manifest pins
  `eval_set_sha256` (membership) AND `truth_sha256` (the joined truth as scored).
- **RE-ADJUDICATED truth (2026-06-11, supersedes the recency resolution)** — recency
  (latest-mtime human verdict wins; mtimes verified Jun 1 12:25 → Jun 3 21:54, corroborated by
  embedded `completedAt`) resolved the 21 double-judged lines, but it was CONTAMINATED: most of
  the winning lineated verdicts were cast while the OLD prose render mangled multi-line/
  enumerated content into one paragraph (fixed in IR `f80ff63`) — the human's own notes say so
  ("prose renderer mangled it into a single p, so hard to say", "I lean lineated because that's
  literally it on the docx screenshot", "note that prose rendering or IR pipeline has a bug").
  All 21 rows were re-judged on the FIXED render, in order: fixed-render verdict
  (`docx_inspect.lineation_decisions`, reference not ground truth), docx source structure,
  the book/SECTION convention (the human's stated tiebreak), and the bug-independent part of
  the human's note. Outcome: **17 → prose** (b28:1697-99, b30:17/18, b32:30513, b37:414.1,
  b66:55-59 trainable; b33:891/893/895/897/898 holdout) and **4 confirmed** (b19:8155 +
  b23:933/935 stay prose; **b41:2247 stays lineated** — its note's in-sentence-break punchline +
  section-convention reasoning is genuine and bug-independent, the only verdict that survives
  the render fix). Each row: `audit_status: readjudicated_fixed_render`, displaced label in
  `provenance.overturned`, basis in `provenance.readjudication` (+ `fixed_render`,
  `section_convention`), full pass history retained. `ru:37:414.1` (the flagged judgment call)
  is hereby settled: the queue-audit lineated flip was the bug compensation; the parable body
  is prose like its gold siblings 410.1/412.1/416.1. Contested baseline under this truth:
  **0.954** retrained (the recency-era 0.867 lock was the contamination's cost). No committed
  experiment manifest pins `truth_sha256` (all predate the pin), so none needed recomputing; the
  `experiments/` scorecards are historical pre-fix numbers.
  ⚠️ FLAG for the `pancratius/ir` owner (read-only here): on the fixed render `ru:41:2247`
  lowers as prose while the identically-shaped offer lines 2237/2259 in the same lineated
  region lower as lineated — an importer inconsistency worth a look, NOT patched from here.
- **The acquire set** — `annotations/selections/acquire.json`: 300 least-confident lines, **≤40/book**
  (19 books, broad), razor-uncertain (margin max 0.011). ⚠️ Selected under the recency-era
  (contaminated) student; re-derive from the re-adjudicated-truth student before the paid run.

## SETTLED result — which decision policy
Replay on the 529 aligned lines under the RE-ADJUDICATED truth (candidate-composite vote artifact;
68 prose / 461 lineated):

    policy            balAcc  autoProseCap  P->L  humanRt
    legacy(2,0.7)      0.958   67/68          1    39       ← still the pick, still not free
    unanimous          0.969   0.750          0    157
    equal_majority     0.938   67/68          1    1

**Legacy anchor-led (min_core_agree=2, conf_floor=0.7)** stays the live policy; the re-adjudication
lifts every policy's accuracy (the panel had been scored against contaminated prose→lineated flips
it rightly called prose) but preserves the trade-off shape: legacy gives up **~0.010 balanced-acc**
to unanimous and makes **one** prose→lineated false accept for the 4× human-load cut. The old
"same accuracy, zero prose-mislabeled, all prose captured" claim was an artifact of the stale
truth. Re-examine if prose false-accepts are costlier than load. **Caveat:** settled on the
*historical* protocol; monitor on the future page-only/live protocol.

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
- **Single-use adjudication auto-retire** — an `<task_id>-adjudication` sub-task is consume-once
  (route builds it → human fills it → ingest promotes). `route` refuses to re-mint it over a CHANGED
  line set (safe-fail), but it does NOT auto-archive the responses + manifest after `ingest`, so a
  re-route of the SAME task post-ingest must be cleared by hand. Not on the loop's happy path (each
  round uses a fresh `task_id`). Harden by having `ingest` archive the consumed responses + retire the
  bundle (Codex finding 2; left explicit, not built).
- **The live escalation driver** — `decision.py` is offline ACCEPT/HUMAN only; live needs
  `ACCEPT/ESCALATE/ROUTE_HUMAN/NEEDS_RERUN` + adaptive reps (run 1, escalate to ~5 on big inter-LLM
  disagreement). Decide `CONF_MISSING`/`LOW_CONFIDENCE` terminal-vs-rerun then (currently TERMINAL,
  inert on the data since conf is fully populated).
- **`student.py` return annotations** — unannotated because `sequence` is lazily imported (keeps
  sklearn off the import path); cleanly annotating fights the `get_type_hints` sweep.
- **`model/` folder** — YAGNI while the student is one file (it's `student/` when it earns one, not `model/`).
- **`809a07c` commit message reword** — blocked: a concurrent site-fonts commit (`6dbd7b2`) landed on
  top, rebase-i is unavailable; safe only once that agent is done.

## ROADMAP — what's left
1. **Wire the decision live** — ✅ DONE (`recipes route`, see "What WORKS"). The OFFLINE accept/route
   split + the `votes.jsonl → gate labels + adjudication sub-task → ingest → labels.jsonl` loop is
   built and tested (`tests/test_route.py`). The adaptive-reps **escalation** wrapper stays deferred
   (it belongs to the paid live run — `route` surfaces the operational/`uncovered` seam it would act
   on; see DEFERRED). NB: the live config grammar moved into `teacher/decision.py` (not eval).
2. **Extract-before-evict** — INVESTIGATED; little to extract. The legacy lives in the **SIBLING backup
   checkout** `/Users/lr/projects/misc/pancratius-prerewrite-full/docs/scratchpad/intent-classifier/`
   (the in-repo untracked `docs/scratchpad/intent-classifier/` is PARTIAL — no v5). The backup is NOT
   being evicted, so nothing here is at risk; "extract" = decide what (if anything) to bring forward.
   - **The page-only prompt** — NO production prompt is chosen yet; two page-only DRAFTS exist, both
     UNVALIDATED. The legacy `reader_brief_v5.txt` is the COMPOSITE brief (page + *candidate render
     tiles* + idx.sub listing): candidate tiles are REJECTED (vision is page-only) AND v5 carries the
     KNOWN-BAD g05 caveat ("1. Вода body can still be PROSE / judge each line by its own shape").
     `reader_brief_v6.txt` is the validated CORRECTION — **UNIT BEFORE LINE** (a numbered/titled item's
     title/body break is intentional lineation; WRAPS is prose evidence only within the unit). Drafts:
     `campaigns/prompts/lineation-v6-page.md` (v6 reasoning + the 2-source page hierarchy: page=visual-
     intent authority, listing=identity/keys, page-decides-verdict/listing-decides-identity; opaque
     keys) and `lineation-v5-page.md` (the known-bad CONTROL).
     - ⚠️ **RETRACTION:** an earlier A/B/C "all 3 prompts IDENTICAL (0.667) / adopt v5-page / borderline"
       was the INSTRUMENT, not the prompt (a 2-reader insertion-order tie-break MASKED the per-reader
       effect; a hallucinated-key fault bug; a tiny non-random sample). After fixing those (structured-
       output `key` enum → 0 faults; bounded render; per-reader scoring; balanced 32-line/9-book
       stratified sample) the CLEAN A/B/C (grok+gemini, 1 rep, balAcc) is:
         `v5-page grok .781/gem .750 · v6-page grok .750/gem .906 · v3-terse grok .938/gem .812`.
     - **ROBUST conclusion:** v5's stale caveat is BAD; **UNIT BEFORE LINE helps** — mechanistically on
       the structural cases (dialogue/speaker-tags/numbered items) v5 marks prose. It does NOT prove v6
       or v3 is production: the effect is READER-DEPENDENT (v6's explicit rule lifts gemini; v3's terse
       framing lifts grok), and v3's lineated gain costs prose-recall (grok-v3 over-lineates a few prose
       lines). Discrimination rides on ~6 lines / 1 rep — suggestive, not final.
     - **NEXT** (do NOT re-run the same 66-call test): author ONE hybrid (v3-concise framing + v6's
       explicit UNIT-BEFORE-LINE + a compact prose guardrail), and/or validate under the PRODUCTION
       gate (real 3-reader roster + anchor-led decision + a frozen stratified eval slice). NB the gate
       anchors on grok (strongest under v3-terse), so prompt choice interacts with the gate.
   - **Orphan human labels** — DO NOT fold the guardrail batches (`responses-fresh-prose-guardrail-*`,
     `responses-wide-prose-guardrail-*`) into `labels.jsonl` *as trainable truth*. Reading the humans'
     own notes, they are a mixed-criterion GENERALIZATION-TEST artifact — several are explicitly
     uncertain / "lazy" / voted by book-prior against the per-line judgment ("for consistency i vote
     lineation (prior distribution)"). Per the eval invariant, held-out guardrail slices are eval,
     never training. (RESOLVED for the contested slice: its 83 store-orphaned labels are homed in
     `labels.jsonl` as `holdout` eval-only rows — see "ONE truth store" above; the wider guardrail
     batches beyond the contested membership remain unfolded.) Where a guardrail/audit pass
     re-judged an ALREADY-LABELED line, recency precedence applies — qualified by the fixed-render
     re-adjudication (see "RE-ADJUDICATED truth"); the stage12 audit's one disagreement
     (`g05_b37|392.1`) is settled there (prose). (Mapping is by
     NORMALIZED exact text — strip `*` md emphasis, collapse whitespace — to the clean-room line,
     then carry its `line_text_hash`; NOT a hash join, and the legacy `idx` is NOT trusted. The
     book-64 text collisions disambiguate by `block_index==idx` / `src_ordinal==idx` agreement.)
3. **Live 300-acquire run** — needs **OPENROUTER_API_KEY** (reachable via `source .env`) + the ~3 reader
   model ids + the chosen **UNIT-BEFORE-LINE page-only prompt** (step 2 — a hybrid/finalist, NOT yet
   locked) in a routed recipe (vision, the 19 acquire books). Build with `recipes build`, then `panel`
   (paid), then `route`.
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
