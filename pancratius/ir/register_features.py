# import-pure: no filesystem mutation
"""The verse-register feature producer and student scorer.

ONE producer: the teacher extraction (research, `docs/scratchpad/display-register/
teacher/`), training, and the production register decision all read THESE
features — a feature either lives here or does not exist, so teacher and
student can never drift apart.

The student artifact is a standardized logistic regression exported as plain
JSON (feature names, means, stds, coefficients, intercept, threshold); scoring
is a dot product — no ML dependency enters the import pipeline.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pancratius import ir

_TERM_RE = re.compile(r"[.!?…]\s*$")
_DASH_RE = re.compile(r"^[—–-]\s")
_Q2P_RE = re.compile(r"\b(ты|тебя|тебе|тобой|твой|твоя|твоё|твои)\b", re.IGNORECASE)
_DIVINE_RE = re.compile(r"\b(Я|Меня|Мне|Мной|Мой|Моя|Моё|Мои)\b")
_QUOTE_OPEN_RE = re.compile(r"^[«\"„]")
_NUM_LEAD_RE = re.compile(r"^\d{1,4}[.:)\s]")

# The exported feature order; the artifact pins the same list and the scorer
# refuses a mismatch (fail loud on drift).
FEATURE_NAMES = (
    "n_lines", "mean_len", "max_len", "cv_len", "term_rate", "dash_rate",
    "q2p_rate", "divine_rate", "quote_open_rate", "num_lead_rate",
    "question_rate", "comma_end_rate", "lower_start_rate", "n_stanzas",
    "multi_line_stanzas", "ev_hard_break", "ev_inferred", "ev_stanza_break",
    "ev_compact_callout", "ctx_heading", "ctx_named", "ctx_separator",
    "len_vs_book", "book_lineated_frac",
)


@dataclass(frozen=True)
class BookStats:
    """Within-book baselines for contrastive features."""

    mean_para_len: float
    lineated_frac: float


def verse_register_features(
    lines: list[str],
    stanzas: ir.LineatedStanzas,
    evidence: ir.LineationEvidence,
    *,
    ctx_heading: bool,
    ctx_named: bool,
    ctx_separator: bool,
    book: BookStats,
) -> dict[str, float]:
    """The block's register feature vector (keys = ``FEATURE_NAMES``)."""
    from pancratius.ir.normalize import inline_plain

    n = len(lines)
    lens = [len(x) for x in lines] or [0]
    mean_len = sum(lens) / len(lens)
    stanza_sizes = [
        size for st in stanzas if (size := sum(1 for line in st if inline_plain(line)))
    ]
    rate = (lambda pred: sum(1 for x in lines if pred(x)) / n) if n else (lambda _pred: 0.0)
    return {
        "n_lines": float(n),
        "mean_len": mean_len,
        "max_len": float(max(lens)),
        "cv_len": (
            (sum((x - mean_len) ** 2 for x in lens) / len(lens)) ** 0.5 / mean_len
            if mean_len else 0.0
        ),
        "term_rate": rate(lambda x: bool(_TERM_RE.search(x))),
        "dash_rate": rate(lambda x: bool(_DASH_RE.match(x))),
        "q2p_rate": rate(lambda x: bool(_Q2P_RE.search(x))),
        "divine_rate": rate(lambda x: bool(_DIVINE_RE.search(x))),
        "quote_open_rate": rate(lambda x: bool(_QUOTE_OPEN_RE.match(x))),
        "num_lead_rate": rate(lambda x: bool(_NUM_LEAD_RE.match(x))),
        "question_rate": rate(lambda x: "?" in x),
        "comma_end_rate": rate(lambda x: x.rstrip().endswith((",", "—", "–"))),
        "lower_start_rate": rate(lambda x: x[:1].islower()),
        "n_stanzas": float(len(stanza_sizes)),
        "multi_line_stanzas": float(sum(1 for s in stanza_sizes if s > 1)),
        "ev_hard_break": float(evidence.hard_break),
        "ev_inferred": float(evidence.inferred_source_rows),
        "ev_stanza_break": float(evidence.stanza_break),
        "ev_compact_callout": float(evidence.compact_callout),
        "ctx_heading": float(ctx_heading),
        "ctx_named": float(ctx_named),
        "ctx_separator": float(ctx_separator),
        "len_vs_book": mean_len / book.mean_para_len if book.mean_para_len else 0.0,
        "book_lineated_frac": book.lineated_frac,
    }


@dataclass(frozen=True)
class StudentModel:
    """A standardized-logistic student: ``p(verse | features)``."""

    features: tuple[str, ...]
    mean: tuple[float, ...]
    std: tuple[float, ...]
    coef: tuple[float, ...]
    intercept: float
    threshold: float

    def probability(self, feats: dict[str, float]) -> float:
        z = self.intercept
        for name, mu, sd, w in zip(self.features, self.mean, self.std, self.coef, strict=True):
            z += w * ((feats[name] - mu) / sd)
        return 1.0 / (1.0 + math.exp(-z))


_MODEL_PATH = Path(__file__).parent / "verse_register_student.json"


@lru_cache(maxsize=1)
def load_student() -> StudentModel | None:
    """The committed student artifact, or ``None`` when absent (the
    deterministic ladder then owns the decision alone)."""
    if not _MODEL_PATH.exists():
        return None
    raw = json.loads(_MODEL_PATH.read_text(encoding="utf-8"))
    if tuple(raw["features"]) != FEATURE_NAMES:
        raise ValueError(
            "verse-register student artifact feature schema drifted from the producer"
        )
    return StudentModel(
        features=tuple(raw["features"]),
        mean=tuple(raw["mean"]),
        std=tuple(raw["std"]),
        coef=tuple(raw["coef"]),
        intercept=float(raw["intercept"]),
        threshold=float(raw["threshold"]),
    )
