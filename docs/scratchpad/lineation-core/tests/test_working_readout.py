# research-pure: the working-half readout's load-bearing guard + the Cell rate, without DOCX.
"""The readout sizes paid work, so its frozen-leak guard must FAIL LOUD. The guard runs before any
DOCX read, so a synthetic eval-set pair exercises it with no corpus."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from lineation_core.evaluation import working_readout as wr


def _write_set(ann: Path, name: str, keys: list[list]) -> None:
    (ann / "eval_sets").mkdir(parents=True, exist_ok=True)
    (ann / "eval_sets" / f"{name}.json").write_text(json.dumps(keys))


def _write_labels(ann: Path, rows: list[dict]) -> None:
    (ann / "labels.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


def _label_row(key: list, label: str) -> dict:
    return {"id": key, "label": label, "source": "human", "confidence": None,
            "audit_status": "", "notes": "", "provenance": {}, "holdout": False}


def test_frozen_leak_fails_loud(tmp_path: Path) -> None:
    shared = ["ru", "01", 5, 0]
    _write_set(tmp_path, wr.WORKING_SET, [shared, ["ru", "01", 6, 0]])
    _write_set(tmp_path, wr.FROZEN_SET, [shared, ["ru", "01", 7, 0]])      # overlaps on `shared`
    with pytest.raises(AssertionError, match="leaks"):
        wr.compute(annotations=tmp_path)


def test_membership_without_label_fails_loud(tmp_path: Path) -> None:
    """eval_sets are membership; a member id with no committed label must FAIL LOUD before any DOCX
    read, never a silently smaller denominator. The guard runs after the leak check, so the sets are
    disjoint here and only the missing-label condition trips."""
    have, missing = ["ru", "01", 5, 0], ["ru", "01", 6, 0]
    _write_set(tmp_path, wr.WORKING_SET, [have, missing])
    _write_set(tmp_path, wr.FROZEN_SET, [["ru", "01", 7, 0]])
    _write_labels(tmp_path, [_label_row(have, "lineated")])   # `missing` has no label row
    with pytest.raises(ValueError, match="no label in labels"):
        wr.compute(annotations=tmp_path)


def test_cell_rate_and_serialization() -> None:
    assert wr.Cell(0, 0).rate is None                 # empty slice → no rate, no ZeroDivision
    assert wr.Cell(3, 4).rate == 0.75
    assert wr.Cell(0, 0).to_dict() == {"k": 0, "n": 0, "rate": None}
    assert wr.Cell(3, 4).to_dict() == {"k": 3, "n": 4, "rate": 0.75}
