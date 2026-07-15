# research-pure: the evaluation half — scores annotations and the student, never produces them.
"""Judges quality: scores each LLM reader and the interpretable student against the committed
truth, on shared labeled lines (`compare`) and on the hard human-readjudicated eval slice
(`contested`). It only READS annotations + records through the store edge; it never writes truth.

Ownership rule for this package: a module that SCORES labels/votes/models lives here; one that
PRODUCES them lives in `teacher/`; one that trains/serves the predictor is the `student`."""
from __future__ import annotations
