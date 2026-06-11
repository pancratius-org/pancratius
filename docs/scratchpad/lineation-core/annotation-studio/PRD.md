# Annotation Studio — PRD & Technical Architecture (v-next)

Status: **Proposal / Draft — revised after ML + adversarial review** · Scope: the human adjudication
tool (`adjudicate.html`) and its place in the lineation active-learning loop · Author: PO/CTO,
Adjudication Studio · Date: 2026-06-08

> A product/architecture proposal, not a locked contract. It proposes the *next* studio version and
> makes one consequential build decision (single-file-by-hand vs. a Vite toolchain that still outputs
> one offline file). It does **not** change the pipeline data contract: the studio loads what
> `teacher.tasks.to_payload()` emits and exports what `teacher.responses.parse_ui_responses` consumes,
> unchanged. A review log (incorporated findings) is at the end.

---

## 1. Context — where the studio sits

`annotation-studio/adjudicate.html` is the **human-in-the-loop stage** of a seeded, pool-based active
learning loop (`../ARCHITECTURE.md`):

```
produce → estimate uncertainty → select → privileged teach → update student → judge → repeat
                                              ▲
                              ┌───────────────┴───────────────┐
                  panel disagreement                 student uncertainty
                  (teacher/decision.py)              (selection.py)
                              └──────────────► HUMAN ORACLE ◄──────────┘
                                          (annotation-studio)
```

Two acquisition signals route work to the human: panel **disagreement** (round 0 / the live
`recipes route` decision step, which auto-accepts gate labels and tiles the rest into a single-use
`<task_id>-adjudication` sub-task this tool consumes, then `ingest --task-id`) and student
**uncertainty** (rounds 1..N). The studio is how the human applies a verdict (`prose` | `lineated`)
per contested body line.

The studio is **deliberately** single-file, dependency-free, offline (`file://`), images inlined as
data-URIs, key-scheme-agnostic, and "used verbatim by the pipeline." Those are load-bearing (§6).

### The contract (must not break)

**In** — `Task.to_payload()` (opaque keys; manifest withheld). Verified against `tasks.py:110-154`:
```jsonc
{
  "title": "…", "instructions": "…",
  "items": [{
    "id": "<RegionId>",            // the UI item id; echoed back verbatim
    "mode": "per-line",            // lineation is ALWAYS per-line (the only mode the pipeline mints)
    "structure": "<feature-rich listing>",   // = TaskItem.context (votable lines keyed, neighbours un-keyed)
    "lineOptions": [{"value":"prose","label":"Prose"},{"value":"lineated","label":"Lineated"}],
    "lines": [{"key":"L001","text":"…","hint?":"…"}],   // hint is CONDITIONAL (omitted when empty) → type hint?: string
    "image": "data:image/png;base64,…"        // VISION only: ONE composite (NOT an assets[] array — see §2 G7)
  }]
}
```

**Out** — consumed by `parse_ui_responses` (`responses.py:184-192`):
```jsonc
{ "responses": { "<item_id>": { "lines": { "L001": "prose", "L002": "lineated" }, "note": "…" } } }
```
The current tool also emits top-level `title` + `completedAt` (`adjudicate.html:922-936`) — both already
ignored by the parser — and a per-item `answer` field for non-`per-line` `mode`. **`answer` is dead for
this pipeline** (lineation is always `per-line`) and must be dropped in the rewrite, not ported.

**Resolution is the choke point** — the private `_resolve()` (`responses.py:96-146`), called by both
`resolve_panel` and `resolve_adjudication`. It maps each `(item_id, key, label)` through the PRIVATE
manifest to a `LineId` and surfaces faults: `unknown_item`, `unmapped_key`, `key_item_mismatch`,
`dup_key` (**first occurrence is kept, the second faults** — not a whole-item reject), `bad_label`,
`text_drift`; `missing_key` is a coverage warning on a partial batch.

**Three facts that shape this PRD:**
1. `parse_ui_responses` reads **only** `responses[id].lines` (`{key:label}`) and `responses[id].note`
   (`responses.py:188-191`). → **Additive metadata is contract-safe** in `responses[id].meta` or a
   top-level `studio` block; the parser ignores it. (A golden test against the real parser makes this a
   proof, not a claim — moved to M0, see §8.)
2. The studio must treat `key` and `text` as **opaque and immutable**. Mutating `text` trips the
   `text_drift` rail at ingest — by design; not a bug to "fix."
3. **The studio mints CANDIDATE human truth, never authoritative truth.** Precedence against committed
   `human`/`override` labels is arbitrated downstream by `ingest`/`promote` (`recipes.py:329,370,403`),
   which "never clobbers/re-queues a human/override label" — also the eval-leakage guard. The studio
   **never reads `annotations/labels.jsonl`** and never reasons about committed precedence.

---

## 2. Problem statement

Post-hardening (id/line-key validation, overlay-reset on load, pan/zoom reader, `/` search, status
ribbon) the tool is a clean labeler. It is **not yet an instrument you'd trust for a paid, multi-hour,
multi-annotator run**, and it under-serves the loop that consumes it:

| # | Gap | Consequence |
|---|-----|-------------|
| G1 | **All state in memory.** | A crash / `⌘R` / closed tab loses a whole queue. No autosave/resume. Dominant risk. |
| G2 | **No round-trip.** | Can't resume across sessions, second-pass, or merge a partial batch. The export *is* the state, but it's write-only. |
| G3 | **First-pass-only navigation.** | After a sweep you must page through everything to find the gaps the ribbon shows. |
| G4 | **No oracle-quality signals.** | The loop treats the human as a noiseless oracle and captures nothing about *how* the truth was made (difficulty/confidence, hesitation, consistency). That metadata is valuable to selection and label-noise weighting and is currently discarded. |
| G5 | **No mis-key recovery.** | Fast keying slips; no undo. |
| G6 | **Single ~1.2k-line untyped IIFE.** | The v-next features add contract-critical state (durable store, round-trip merge, fail-loud parity, additive export). That logic deserves types + unit tests and currently has neither — a silent bug there means **corrupted training truth**. |
| G7 | **`assets[]` is a design dead-end, not a gap.** | The payload emits a single `"image"` (page composite); candidate render-tiles were tried and **rejected** ("vision is page-only", `ml-handoff.md`). Multi-asset is speculative forward-compat, not a real miss. |

---

## 3. Users & jobs-to-be-done

- **The adjudicator** (today the project owner; tomorrow contracted annotators). Job: *apply correct
  verdicts fast, never lose work, recover mis-keys, know what's left, not be biased by the panel.*
- **The ML/AE engineer** (consumes `responses.json` → `labels.jsonl`). Job: *get clean, attributable,
  candidate truth plus quality signals (difficulty/confidence) the loop can use* — with leakage hygiene
  (eval slices) intact.
- **The pipeline** (`teacher.responses`). Job: *resolve opaque keys → `LineId`, fail loud on drift.*
  Cares only that the export stays exactly `responses[id].{lines,note}`.

---

## 4. Goals / Non-goals

**Goals (v-next)**
- Zero data loss across crash/reload; resumable, round-trippable sessions.
- Faster gap-closing pass (next-unresolved, undo, flags/filters).
- Capture *difficulty/confidence* signals the loop wants — additive, namespaced, never breaking resolution.
- Typed, unit-testable contract-critical logic; behavior-equivalent single-file artifact preserved.
- Preserve every load-bearing invariant: single offline artifact, opaque keys, verbatim text, no network.

**Non-goals (v-next)**
- No backend/accounts/hosted service. The `file://`, zero-server model is a feature (data never leaves
  the machine) and a pipeline assumption.
- No multi-annotator concurrency. (Forward-compat the *metadata*; build no server.)
- No autofill of verdicts from the panel majority (biases the truth we mint).
- **No in-studio calibration / panel-agreement display.** Showing the human how the panel voted — even
  post-commit — biases the truth used to *rank the panel* and can contaminate frozen `eval_sets/`.
  Human↔panel **coverage** is measured offline in `evaluation/`, never in the studio. (Was R9; removed.)
- No change to required response fields; only additive, namespaced metadata.
- No re-theme / dark mode.

---

## 5. Requirements (prioritized)

Priorities: **P0** ship-blocking, **P1** strongly wanted, **P2** opportunistic. Each has acceptance
criteria (AC).

### P0 — durability, round-trip, leakage safety (the trust floor)
- **R1 Autosave.** Verdicts + notes + flags persist to `localStorage`, keyed by a stable fingerprint of
  `{task_id (if present), title, item ids, line keys}` (sync hash — see §7.4; not async SubtleCrypto, to
  avoid an autosave race), debounced on change. *AC:* edit → kill tab → reopen same task → state restored;
  a visible "saved · just now" reflects the last write.
- **R2 File-snapshot durability (primary).** One-click "save session snapshot to file" and "resume from
  snapshot." Because `localStorage` on `file://` origins is browser-inconsistent, the **file snapshot is
  the durable trust floor; `localStorage` is the convenience layer.** *AC:* a snapshot file restores a
  session losslessly even when `localStorage` is unavailable/cleared.
- **R3 Round-trip import with explicit precedence.** Load a prior `responses.json`/snapshot to continue or
  merge. Rules (not optional): (a) the import is **candidate** state, never authoritative; (b) merge is
  **refused across a different task fingerprint / `task_id`**; (c) on a per-`(item,key)` conflict the line
  is **flagged for re-decision, never auto-resolved**; (d) **re-importing an already-exported file is
  idempotent** (no double-count). *AC:* round-trip reproduces state exactly; a partial fills only answered
  lines; conflicting labels surface as flagged re-decisions; importing the same file twice changes nothing.
- **R4 Unsaved-work guard.** `beforeunload` warns on un-exported changes. *AC:* prompts on desktop.
  *Known limit:* `beforeunload` is suppressed on iOS Safari / under bfcache — so R2 (snapshot) and R1
  (autosave) carry durability; the guard is a courtesy, not the safety net.
- **R5 Eval-set quarantine.** When a task is eval-bound (its lines belong to `eval_sets/`, signalled by an
  additive `eval_bound:true` in the payload `studio` block, or a per-item flag the pipeline sets), the
  studio **disables hints and any panel-derived display**, and marks the export `eval_bound`. *AC:* on an
  eval task, no hint/panel signal is reachable; the export is tagged so ingest can route it away from
  training. (This is the single most important leakage boundary in the SPEC — `eval_sets/` "never training".)

### P1 — throughput & correctness
- **R6 Next-unresolved navigation.** A key (`n`) jumps to the next item/line with no verdict; wraps; reports
  "all resolved." *AC:* with scattered gaps across 200 items, `n` visits each gap once; honors the active filter (R8).
- **R7 Undo/redo.** `⌘Z`/`⌘⇧Z` over the verdict history (sets/clears, bulk ops, merge resolutions).
  *AC:* a mis-key is reversible without re-finding the line; an import pushes one undoable step.
- **R8 Flag-for-review + filters.** A flag key (`f`) marks "come back," independent of the verdict; a filter
  cycles all / unresolved / partial / flagged, scoping navigation + the ribbon. *AC:* flags persist (R1) and
  export as additive metadata (R11); the filter restricts `n` (R6).
- **R9 Bulk-op hotkeys with provenance.** `⇧P`/`⇧L`/`⇧C` = All-Prose / All-Lineated / Clear for the current
  item, scroll-preserving, **undoable (R7)**, and **flagged in `meta` as bulk-set** so the loop can audit /
  down-weight un-considered region stamps. *AC:* keyboard-only completion of a uniform page; bulk-set lines
  are marked in the export.
- **R10 Client-side fail-loud parity (what the studio CAN prevent).** Before export, prevent/loudly-warn the
  faults the studio can see with the payload alone: `bad_label` (the studio controls the value set),
  `unmapped_key` / `key_item_mismatch` (cross-check each verdict's `key` against that item's own `lines[]`),
  `dup_key` (already validated on load). It **cannot** prevent `text_drift` (no manifest/hash) or
  `unknown_item` (always a payload item); `missing_key` surfaces as "N lines unresolved." *AC:* an export
  that would raise a *client-preventable* fault is impossible; non-preventable ones are documented as ingest's job.

### P1 — oracle-quality signals (serve the loop, additively)
- **R11 Additive response metadata, with a consumer contract.** Under a namespaced `responses[id].meta` and a
  top-level `studio` block the parser ignores, export: annotator `tag`/id, studio version, task fingerprint +
  `task_id`/sub-task id, per-line **revision count** and **revised-after-seeing-run** flag (cheap coherence
  signal), **bulk-set** flag (R9), flag-for-review state (R8), and session timing. **Every field names its
  intended consumer or is marked diagnostic-only** — unconsumed metadata that looks like training signal is a
  foot-gun (the SPEC's zero-support-feature lesson). *AC:* `parse_ui_responses` consumes the file unchanged
  (golden test, M0); metadata round-trips (R3); `task_id` binds an `ingest --task-id` round-trip.
- **R12 Explicit annotator difficulty/confidence (the AL signal that matters).** Optional, **post-commit**,
  per-line "low-confidence/hard" capture — distinct from R8's workflow flag — exported in `meta`. This is a
  first-class active-learning signal (re-query priority, label-noise weighting, "needs a second annotator").
  **Capture ≠ feature:** it is never a student/serve input unless deliberately promoted. *AC:* confidence is
  capturable without slowing the default path and never shown to the panel/student pipeline as a feature.

### P1 — accessibility
- **R13 Keyboard-complete + screen-reader basics.** Every action reachable by keyboard; an ARIA live region
  announces item changes and save state. (The ribbon stays a mouse affordance; `/`-search + `n` are the
  keyboard equivalents — 200 tab-stops is an anti-feature.)

### P2 — polish & speculative
- **R14 Pace & ETA.** Lines/min and "~N min left," in the export summary (operational, not a training signal).
- **R15 Hover loupe.** In-pane magnifier under the cursor without opening the full reader.
- **R16 Multi-asset vision (speculative).** Render a list of evidence images **iff** the payload ever emits one.
  Today it emits a single `"image"` and page-only vision is settled; this is forward-compat only and requires a
  pipeline `to_payload` change to be real. Do not build ahead of that.

### Moved out of the studio (pipeline concerns)
- **Self-consistency measurement.** An in-session "re-present 5% blind" probe yields too few pairs to be
  statistically meaningful and entangles autosave/undo/round-trip. Intra-annotator consistency is better run as
  a **cross-session gold/probe slice re-routed through the existing `route`→adjudication path** and scored in
  `evaluation/`. The studio's only obligation is to faithfully label whatever lines it's given, including a
  probe line that recurs in a later task. (Was R11 in the draft; removed from studio scope.)
- **Human↔panel agreement.** Computed offline in `evaluation/` over committed labels (it's a **panel-coverage**
  metric — the panel is the unit being ranked — not an annotator-quality signal). Never in the studio. (Was R9.)

---

## 6. Invariants the studio must never break

Inherited from the loop and SPEC; violating any is a release blocker.

1. **One offline artifact.** A single file, `file://`, no network/fetch, evidence inlined as data-URIs.
2. **Opaque keys only.** No `LineId`/`src_ordinal` in UI or export; key-scheme-agnostic.
3. **Verbatim text & keys.** `text`/`key` pass through unmodified (mutation trips `text_drift` by design).
4. **Export shape is sacred.** Only `responses[id].lines` (`{key:label}`) and `responses[id].note` are read by
   the pipeline; everything else is additive and namespaced.
5. **Candidate, not authoritative.** The studio mints candidate human labels; it **never reads
   `annotations/labels.jsonl`** and never arbitrates precedence — `ingest`/`promote` do.
6. **Leakage quarantine.** On an eval-bound task, hints/panel signals are disabled and the export is tagged
   (R5). The studio never folds an eval re-adjudication into a training-bound batch.
7. **No bias leak.** Panel hints stay gated behind the existing global toggle and are **off by default on eval
   tasks**; the studio shows **no** panel-vote/agreement information at any time.
8. **No truth invention.** Records human verdicts; never derives, smooths, or auto-fills a label.

> Hint note: today hints are an **item-level** field (`it.hint`) behind a global on/off toggle
> (`adjudicate.html`), not a per-line, per-commit reveal. The rewrite keeps that model (global toggle,
> default-off on eval tasks); it does not gate `it.hint` on line completion.

---

## 7. Technical architecture & the build decision

### 7.1 The question
The v-next set (durable store, undo history, round-trip merge with precedence, fail-loud parity, additive
export) is materially more stateful than "toggle buttons and serialize." Do we keep authoring one
hand-written `adjudicate.html`, or graduate to a toolchain — without losing the single-offline-file invariant?

### 7.2 Options
- **A. Stay vanilla, one hand-authored file.** *Pro:* zero toolchain; the file *is* the artifact; no node in a
  uv/Python dir; the current tool is a clean, well-sectioned IIFE and vanilla tools run to several thousand
  lines fine. *Con:* the contract-critical logic (export builder, merge precedence, fail-loud parity) — exactly
  the code whose silent bug corrupts training truth — gets no types and no unit tests.
- **B. Full SPA (Vite + framework), bundled normally.** *Con:* breaks §6.1 unless we re-add single-file packaging
  anyway; a framework runtime is overkill for one screen.
- **C. Vite + `vite-plugin-singlefile`, TypeScript, no framework, output = ONE inlined HTML.** ← **recommended.**
  TypeScript on the contract surface; `vitest` unit tests on the pure logic; Playwright for DOM/keyboard/zoom
  flows (our existing QA habit) — and the build emits a single self-contained `adjudicate.html` that still runs
  from `file://`. The pipeline contract is unchanged (still load/save JSON).

### 7.3 Decision (with the honest counter-argument)

> **Adopt Option C for v-next.** The deciding factor is *not* file size (the ~1.2k-line tool is fine; any v-next
> line-count target is a guess) and *not* a self-defined "trigger." It is a single risk argument: the merge /
> export / fail-loud logic is where a silent defect becomes **corrupted training truth**, and types + unit tests
> buy down that specific risk more than a build step costs.
>
> **Steelman for staying vanilla (Option A):** none of R1–R16 *require* TypeScript to implement; the one thing
> that genuinely benefits from a real test harness — the golden contract test against the Python parser — is a
> separate harness concern that could run against the vanilla file too. If, in M0, the contract logic can be
> extracted into a small pure-JS module with a Node test harness *without* Vite, Option A remains defensible and
> cheaper. **So Option C is the recommendation, but M0 is also the go/no-go:** if the typed extraction doesn't
> pay for itself by the end of M0, fall back to A. We do not pre-commit the whole toolchain sight-unseen.

We reject B unconditionally (single-file invariant is non-negotiable; a framework buys nothing for one screen).

### 7.4 Architecture under Option C

Functional-core / imperative-shell, mirroring the Python package's discipline:

```
annotation-studio/
  adjudicate.html        # COMMITTED BUILD ARTIFACT — the pipeline consumes this (contract unchanged)
  README.md  PRD.md
  studio/                # SOURCE + node toolchain, isolated from the uv package (see §7.6)
    package.json  vite.config.ts  tsconfig.json
    src/
      core/              # PURE, framework-free, unit-tested — the contract surface
        contract.ts      #   payload + response types (hint?: string; completedAt present); export builder/parser
        fingerprint.ts   #   stable SYNC hash over {task_id?, title, item ids, line keys}
        merge.ts         #   round-trip import + partial merge + conflict flagging + idempotency (R3)
        verdicts.ts      #   verdict state machine + undo/redo (R7)
        validate.ts      #   client-preventable fail-loud parity (bad_label/unmapped_key/key_item_mismatch/dup_key)
        metadata.ts      #   additive, namespaced export metadata (never touches lines/note)
      ui/                # imperative shell: rendering, keyboard, panes, reader, ribbon, finder
      persist/           # localStorage (R1) + file snapshot (R2) + beforeunload (R4)
    test/                # vitest (core) + Playwright (flows) + the GOLDEN contract test (M0)
```

Boundaries:
- **`core/` imports nothing from `ui/`** — merge/validate/export are pure functions over plain data.
- **The committed `adjudicate.html` is a vendored artifact** (like the hash-pinned font in the Python package):
  the pipeline never knows node exists; a CI/pre-handoff check asserts the committed HTML equals the build of
  `studio/src` (a content check, not a byte-diff of source).
- **No npm dependency reaches the uv package**; `src/lineation_core/` never imports or builds `studio/`.

### 7.5 Contract conformance is a test (M0)
A `vitest` golden feeds a real `to_payload()` sample through `core`'s export builder and asserts the result
parses under a recorded `parse_ui_responses` expectation; a perturbation test asserts mutating `text` is
impossible through the UI path; an idempotency test asserts re-importing an export is a no-op. This makes "the
studio honors the contract" a regression-locked invariant, matching the SPEC's proof-obligation style.

### 7.6 Where the source lives (resolving an architecture invariant)
`ARCHITECTURE.md` currently states `annotation-studio/` holds "nothing but `adjudicate.html`" (intent: prompts
and recipes belong elsewhere). Option C adds a `studio/` source subtree here. **This PRD therefore proposes a
one-line amendment to that invariant:** `annotation-studio/` holds *the human tool — its built artifact and its
own source toolchain*; prompts (`campaigns/prompts/`) and recipes (`campaigns/recipes/`) still do not live here.
Adopting Option C is contingent on that amendment landing in `ARCHITECTURE.md`.

---

## 8. Rollout

- **M0 — lift, type, and PROVE the contract (also the Option-C go/no-go).** Stand up `studio/`; extract the
  contract-critical logic into typed, unit-tested `core/`; **write the golden contract test now**; build emits a
  **behavior-equivalent** (not byte-faithful — Vite reformats/minifies) `adjudicate.html` that passes the
  existing Playwright flows. Drop the dead `answer` branch. *Exit:* committed artifact is build output; golden
  + perturbation + idempotency tests green. *Go/no-go:* if typed extraction didn't pay off, fall back to Option A.
- **M1 — durability & leakage safety (P0).** R1–R5. *Exit:* killed session resumes from snapshot losslessly;
  round-trip merges with precedence; eval tasks are quarantined and tagged.
- **M2 — throughput & parity (P1).** R6–R10, R13.
- **M3 — oracle signals (P1).** R11–R12, with the `ingest --task-id` round-trip AC.
- **M4 — polish (P2).** R14–R15. (R16 only if the payload gains multi-asset.)

Each milestone ends with the existing habit: adversarial diff review + Playwright verification in the real
engine (Firefox), before the artifact is rebuilt and committed.

---

## 9. Productization path (Adjudication Studio & Human Data Inc — future, non-goal now)
The seam, not the build: the export's additive `tag`/id + difficulty signals (R11/R12) are the multi-annotator
hook. A *future* hosted mode would add annotator identity/auth, a task-queue server, inter-annotator agreement &
adjudication-of-adjudicators, gold-question seeding for annotator QA (the natural home for the cross-session
consistency probe moved out of §5), and throughput/payment dashboards. **None is v-next.** We grow toward the
company by making the *data and metadata* multi-annotator-ready, keeping the single-file offline tool as the
reference annotator surface and the privacy story.

---

## 10. Risks & mitigations
- **`localStorage` brittle on `file://`** → R2 file-snapshot is the *primary* durability mechanism; localStorage
  is convenience. `beforeunload` (R4) is suppressed on iOS/bfcache — not relied on.
- **Build drifts from committed artifact** → content check that `adjudicate.html` == build of `studio/src`; red on mismatch.
- **Additive metadata breaks the parser** → golden test against the real `parse_ui_responses` (M0); metadata strictly namespaced.
- **Merge corrupts truth** (clobber/double-count/cross-task) → R3 precedence: candidate-not-truth, fingerprint/`task_id` match required, conflicts flagged, re-import idempotent; studio never reads committed `labels.jsonl`.
- **Eval leakage** → R5 quarantine + §6.6 invariant; export tagged `eval_bound`.
- **Difficulty/timing mistaken for training features** → R11 consumer-contract per field; R12 confidence is capture-only, never a feature unless promoted; timing is operational (R14).
- **Unconsumed metadata theater** → every `meta` field names a consumer or is marked diagnostic.
- **Toolchain in a Python dir** → isolated `studio/` leaf, no cross-import, artifact committed, `ARCHITECTURE.md` amended (§7.6).
- **Scope creep to a framework** → decision record (§7.3) bounds us to no-framework + single file; M0 is the off-ramp.

---

## 11. Open questions
1. **Eval-bound signalling.** How does the payload mark an eval task for R5 — a per-item flag, a `studio.eval_bound`
   block, or by `task_id` convention? (Needs a small `to_payload`/recipe decision with the ML lead.)
2. **`task_id` propagation.** Confirm `recipes route`/`ingest --task-id` can stamp the payload's `studio` block so
   R3/R11 can bind to it (it owns the sub-task identity).
3. **Annotator `tag`.** Keep resolved `tag="human"` (contract untouched) and carry annotator identity in `meta`
   — confirmed direction; flagged here only to lock it before M3.

---

## Appendix A — current state (M0 acceptance baseline to preserve)
Single-file tool: fixed-viewport cockpit (rail + two internally-scrolling panes + persistent action bar); status
ribbon (segment-per-item, click-to-jump, position needle); pan/zoom reader (fit-width default, cursor-anchored
zoom, drag-pan, keyboard, `z`-toggle); `/` command-palette search with jump+flash; per-line keyboard grinding with
focus auto-advance; item-level hints gated behind a global toggle; JSON export. Hardened in review: unique-id +
unique-line-key load validation (fail-loud), overlay/drag reset on load, `pointercancel`/`blur` drag-release.

## Appendix B — review log (incorporated)
Reviewed by an ML/annotation-engineering lead and an adversarial doc reviewer. Incorporated:
- **Removed in-studio calibration** (biases truth used to rank the panel; eval-contamination risk) → offline
  panel-*coverage* metric + **eval-set quarantine** invariant (R5, §6.6). *[ML B1]*
- **Specified merge precedence** — candidate-not-truth, fingerprint/`task_id` match, conflicts flagged,
  re-import idempotent, studio never reads `labels.jsonl` (R3, §1.3, §6.5). *[ML B2 / QA H3]*
- **Threaded `task_id`/sub-task identity** for the live `route`→adjudication→`ingest --task-id` path (R1/R3/R11). *[ML M4]*
- **Added explicit annotator difficulty/confidence capture** as the priority AL signal; demoted raw timing to
  operational; required a consumer-contract per `meta` field (R11/R12). *[ML M1/M2, m1]*
- **Reframed "calibration" → panel-coverage** (the panel is the ranked unit, not the human). *[ML m2]*
- **Contract precision:** `hint?` optional; choke point is `_resolve`; `dup_key` keeps the first occurrence;
  payload emits a single `"image"` not `assets[]`; `completedAt` already present; drop the dead `answer` branch. *[QA H1/H2/L2/M1/L4]*
- **Golden contract test → M0** (was M3); M0 exit is **behavior-equivalent**, not "byte-faithful." *[QA H5/M4]*
- **File-snapshot durability promoted to P0** (R2); `beforeunload`/`localStorage`-on-`file://` caveats noted. *[ML m5 / QA M5]*
- **R10 fail-loud parity** enumerated as client-preventable vs not. *[QA M6]*
- **Multi-asset vision** demoted to speculative P2 (page-only is settled). *[ML m6 / QA M1]*
- **In-session self-consistency probe removed** from studio scope (statistically inert; entangles state) →
  cross-session gold-probe via the pipeline. *[ML M3 / QA H4]*
- **Bulk-ops** get a provenance marker (manual-autofill hazard). *[ML m4]*
- **Build justification** rewritten: dropped the invented line count and the circular "trigger," added the honest
  Option-A steelman and an M0 off-ramp. *[QA M2/L5]*
- **`studio/` location** resolved via a proposed one-line `ARCHITECTURE.md` amendment (§7.6). *[QA L6]*
- **Hint-gating wording** corrected to the actual global-toggle, item-level model (§6 note). *[QA L3]*
