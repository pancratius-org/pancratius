# research-pure: per-reader scoring for a study — protocol health, decision quality, cost, kept apart.
"""How well one panel reader (model × modality) recovered prose-vs-lineated on a study's frozen eval
slice, at what cost — pure value objects in THREE dimensions, never collapsed to one scalar:

  - `ProtocolHealth` — did the reader actually answer (coverage, truncation, resolution faults);
  - `DecisionQuality` — given the answers, how good (balanced accuracy + per-class recall, delegating
    to `metrics.balanced` so there is ONE balanced-accuracy definition, plus rep instability);
  - `Cost` — real OpenRouter spend = tokens × an INJECTED price (no module-global price table).

`reader_cost` is pure — it sums each rep's `usage` token counts × the price passed in; a study that
cannot price a reader fails at the price table, never reports `$0` as free here.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from ..annotations import PanelVote
from ..identity import Label, LineId, ReaderTag
from ..teacher.panel import PanelRep
from ..teacher.tasks import Modality
from .metrics import balanced
from .prices import Price, PriceTable

__all__ = ["Cost", "DecisionQuality", "Price", "PriceTable", "ProtocolHealth", "ReaderResult",
           "class_recall", "coverage", "instability", "reader_cost"]


@dataclass(frozen=True, slots=True)
class ProtocolHealth:
    """Did the reader answer the protocol at all: `coverage` = answered eval lines / total eval lines
    (an unvoted eval line is a MISS), `truncated` = reps the model cut off at max_tokens, `faults` =
    resolution faults by kind (unmapped/dup/bad-label/text-drift/…)."""
    coverage: float
    truncated: int
    faults: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class DecisionQuality:
    """Given the reader's answers, how good — per-class recall over the FULL eval denominator (an
    unvoted line counts as a miss for its class), `balanced_acc` the mean of the two. `instability`
    is the share of covered lines whose reps disagreed (papered over by the majority-of-reps vote;
    0 = stable or single-rep). `n_prose`/`n_lineated` are the class denominators."""
    balanced_acc: float
    prose_recall: float
    lineated_recall: float
    instability: float
    n_prose: int
    n_lineated: int


@dataclass(frozen=True, slots=True)
class Cost:
    """Real spend over EVERY call this reader made (cache-resumed or fresh alike): `usd` = tokens ×
    the injected price, the prompt/completion token totals behind it, and `usd_per_1k_lines` the
    cost normalized to the eval size so two readers compare on the same denominator."""
    usd: float
    prompt_tokens: int
    completion_tokens: int
    usd_per_1k_lines: float


@dataclass(frozen=True, slots=True)
class ReaderResult:
    """One reader's full study scorecard for one sweep point — the three dimensions side by side,
    never a fused score."""
    tag: ReaderTag
    modality: Modality
    health: ProtocolHealth
    quality: DecisionQuality
    cost: Cost


def _majority(labels: Sequence[str]) -> str | None:
    return Counter(labels).most_common(1)[0][0] if labels else None


def class_recall(votes: Iterable[PanelVote], truth: Mapping[LineId, Label],
                 eval_lines: Iterable[LineId]) -> tuple[float, float, float, int, int]:
    """Per-class recall over the FULL eval denominator — an eval line no reader voted on counts as a
    MISS for its truth class, never dropped. Delegates the recall math to `metrics.balanced` (the one
    balanced-accuracy definition) by feeding it the per-line `(truth, predicted-or-sentinel)` pairs;
    an unvoted line predicts a sentinel that matches neither class, so it is a guaranteed miss.
    Returns `(balanced_acc, prose_recall, lineated_recall, n_prose, n_lineated)`."""
    by_line: dict[LineId, list[str]] = defaultdict(list)
    for v in votes:
        by_line[v.id].append(v.label)
    y_true: list[Label] = []
    y_pred: list[Label] = []
    for lid in eval_lines:
        if lid not in truth:
            continue
        y_true.append(truth[lid])
        pred = _majority(by_line.get(lid, []))
        y_pred.append(pred if pred in ("prose", "lineated") else "__miss__")  # type: ignore[arg-type]
    m = balanced(y_true, y_pred)
    n_prose = sum(t == "prose" for t in y_true)
    n_lineated = sum(t == "lineated" for t in y_true)
    return m.balanced_acc, m.prose_recall, m.lineated_recall, n_prose, n_lineated


def instability(votes: Iterable[PanelVote]) -> float:
    """Share of COVERED lines whose reps did not all agree — the single-shot instability the
    majority-of-reps vote hides. 0 = perfectly stable (or single-rep). Pure."""
    by_line: dict[LineId, list[str]] = defaultdict(list)
    for v in votes:
        by_line[v.id].append(v.label)
    if not by_line:
        return 0.0
    return sum(1 for labs in by_line.values() if len(set(labs)) > 1) / len(by_line)


def coverage(votes: Iterable[PanelVote], eval_lines: Sequence[LineId]) -> float:
    """Answered eval lines / total eval lines — an unvoted eval line is a miss, not dropped."""
    if not eval_lines:
        return 0.0
    voted = {v.id for v in votes} & set(eval_lines)
    return len(voted) / len(eval_lines)


def reader_cost(reps: Iterable[PanelRep], price: Price, *, n_lines: int) -> Cost:
    """Real USD over every rep's `usage` token counts × the INJECTED `price` — pure, no disk read, no
    module-global price. Empty reps → $0 (a reader that made no call cost nothing — a true zero, not a
    silent unknown-model fallback). `usd_per_1k_lines` normalizes by the eval size."""
    prompt_price, completion_price = price
    ptok = ctok = 0
    for rep in reps:
        usage = rep.usage or {}
        ptok += int(usage.get("prompt_tokens") or 0)
        ctok += int(usage.get("completion_tokens") or 0)
    usd = ptok * prompt_price + ctok * completion_price
    per_1k = usd / n_lines * 1000 if n_lines else 0.0
    return Cost(usd=usd, prompt_tokens=ptok, completion_tokens=ctok, usd_per_1k_lines=per_1k)
