# research-pure: the teacher half — PRODUCES annotations (panel votes + human adjudications).
"""Produces the committed truth the `student` learns from and `evaluation` judges: LLM-panel
votes (`votes.jsonl`) and human page-adjudications (`labels.jsonl`, `eval_sets/*.json`).

The load-bearing invariant: a reader/adjudicator only ever sees and returns TASK-LOCAL opaque
keys (`L001`); the parser resolves them to `LineId` at one choke point before anything persists,
so a source ordinal can never reach a prompt or a reader response.

Ownership rule for this package: a module that PRODUCES labels/votes/responses lives here; one
that SCORES them lives in `evaluation/`; one that trains/serves the predictor is the `student`."""
from __future__ import annotations
