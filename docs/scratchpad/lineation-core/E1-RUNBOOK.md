# E1 runbook — the paid half (~$6.3) + what follows

Everything below assumes `cd docs/scratchpad/lineation-core`. The $0 half is done: instrument
minted (seed 0, self-weighting), bundles built, recipes carry `holdout_eval_set` so the frozen
750 cannot leak into training.

## 1. Panel (paid; needs the key in env)

```sh
export OPENROUTER_API_KEY=...   # or run via `!` in the Claude session
# smoke first: ~$0.05 — the en task is the smaller one; watch one rep complete clean
uv run --extra live python -m lineation_core.teacher.recipes panel campaigns/recipes/e1-instrument-en.toml
uv run --extra live python -m lineation_core.teacher.recipes panel campaigns/recipes/e1-instrument-ru.toml
```

Panel promotes votes all-or-nothing per task; an interrupted run resumes from the call cache
(request-fingerprint keyed) and re-pays nothing. Expected ≈ $6.3 total at 3 reps
(grok-vision 3.51 + gemini-lite 0.48 + ds-flash 0.165 per 1k lines).

## 2. Route (free)

```sh
uv run python -m lineation_core.teacher.recipes route campaigns/recipes/e1-instrument-ru.toml
uv run python -m lineation_core.teacher.recipes route campaigns/recipes/e1-instrument-en.toml
```

Gate-accepts become `gate` labels (frozen members stamped `holdout=True` automatically); the
rest tile into `<task>-adjudication` sub-tasks for `annotation-studio/adjudicate.html`.
Expected human queue ≈ 5–10% (75–150 lines), region-tiled.

## 3. Human pass (~3–4 h, can be spread)

- Adjudicate the routed queue in the studio; `ingest` brings verdicts back (holdout stamped).
- Spot-check ~300 gate-accepted lines, prioritizing every det-vs-gate disagreement
  (`recon` rows × new labels join — E2's first cell computes this list).

```sh
uv run python -m lineation_core.teacher.recipes ingest campaigns/recipes/e1-instrument-ru.toml --task-id e1-instrument-ru-adjudication
```

## 4. Immediately after labels exist ($0)

- **Holdout audit**: every `labels.jsonl` row whose id ∈ `eval_sets/e1-instrument-frozen.json`
  must have `holdout=true` — one jq/python line; run it before anything trains.
- **E1 numbers**: corpus base rate; **P(truth=lineated | det=prose)** (the number that sizes
  residual risk); gate accept-accuracy on representative data; EN-vs-RU det error parity
  (→ the §5 decision rule); det-voter confidence calibration.
- **E2** on the working half only: signal bakeoff (det-vs-student, suspicion-v0, margins),
  the inside/outside-φ fork (pre-registered ρ ≥ +0.3), downsample-replay of rep protocols and
  rosters incl. the `ir-det` synthetic voter.

The frozen 750 stays untouched until E4 (scored once, Wilson bounds, EN stratum separate).
