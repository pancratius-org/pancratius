"""The verse-register student runtime and enrichment pass.

The student artifact is a standardized logistic regression exported as plain
JSON (trained in `docs/scratchpad/display-register/teacher/`); scoring is a
dot product, dependency-free. The pass flips the verse register on
lineated-family blocks where the student is confident; inside the abstention
band the ladder's verdict stands. Named verse sections are never demoted and
scaffold shapes never promoted.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from pancratius import ir
from pancratius.intent.features import (
    FEATURE_NAMES,
    book_stats,
    iter_with_register_context,
    lineated_lines,
    verse_register_features,
)
from pancratius.ir.normalize import (
    is_dash_scaffold,
    is_equation_scaffold,
    is_lineated_line,
)

# The abstention band: a probability inside it is not a decision — the
# deterministic ladder keeps owning those blocks. Tuned on the held-out human
# truth (`teacher/evaluate_student.py`).
ABSTAIN_LO = 0.35
ABSTAIN_HI = 0.65

_MODEL_PATH = Path(__file__).parent / "models" / "verse_register_v1.json"


@dataclass(frozen=True)
class StudentModel:
    """A standardized-logistic student: ``p(verse | features)``."""

    version: int
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


def load_student(path: Path = _MODEL_PATH) -> StudentModel | None:
    """The exported student artifact, or ``None`` when not shipped."""
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if tuple(raw["features"]) != FEATURE_NAMES:
        raise ValueError(
            "verse-register student artifact feature schema drifted from the producer"
        )
    return StudentModel(
        version=int(raw.get("version", 0)),
        features=tuple(raw["features"]),
        mean=tuple(raw["mean"]),
        std=tuple(raw["std"]),
        coef=tuple(raw["coef"]),
        intercept=float(raw["intercept"]),
        threshold=float(raw["threshold"]),
    )


def apply_verse_register(
    doc: ir.Document,
    *,
    lang: str,
    model: StudentModel | None = None,
) -> None:
    """The learned Q2 enrichment pass (in place, RU sources only).

    RU-only because the student's voice features read Russian; EN editions keep
    the deterministic ladder's verdicts until cross-language register
    inheritance exists. A missing artifact makes the pass a no-op, so the
    pipeline is fully deterministic until a model ships.
    """
    if lang != "ru":
        return
    student = model if model is not None else load_student()
    if student is None:
        return

    stats = book_stats(doc.blocks)
    promoted = demoted = candidates = 0
    out: list[ir.Block] = []
    for block, ctx in iter_with_register_context(doc.blocks):
        if not isinstance(block, (ir.LineatedBlock, ir.VerseBlock)):
            out.append(block)
            continue
        lines = lineated_lines(block)
        if (
            len(lines) < 2
            or not all(is_lineated_line(line) for line in lines)
            or is_dash_scaffold(lines)
            or is_equation_scaffold(lines)
            or ctx.named
        ):
            out.append(block)  # deterministic rules own this block
            continue
        candidates += 1
        p = student.probability(verse_register_features(
            lines, block.stanzas, block.evidence, ctx=ctx, book=stats,
        ))
        if isinstance(block, ir.VerseBlock) and p <= ABSTAIN_LO:
            demoted += 1
            out.append(ir.LineatedBlock(
                stanzas=block.stanzas, evidence=block.evidence,
                source_span=block.source_span,
            ))
        elif isinstance(block, ir.LineatedBlock) and p >= ABSTAIN_HI:
            promoted += 1
            out.append(ir.VerseBlock(
                stanzas=block.stanzas, evidence=block.evidence,
                source_span=block.source_span,
            ))
        else:
            out.append(block)
    doc.blocks = out
    if promoted or demoted:
        doc.diagnostics.append(ir.Diagnostic(
            "info", "intent.verse-register",
            f"student v{student.version}: promoted {promoted}, demoted {demoted} "
            f"of {candidates} candidate blocks",
        ))
