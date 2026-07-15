# research-pure: OFFLINE replay of decision policies over the aligned (truth ⋈ votes) set.
"""Replays a `DecisionPolicy` over committed evidence and scores it against committed truth — the
core of the policy eval. For each aligned line it groups that line's per-reader votes by tag and
applies `policy.decide` (the SAME pure function the live promote path runs), collecting one
`LineDecision` per line; then it scores the run into a `PolicyMetrics` (accept-quality on the lines
it ACCEPTED, human load on the lines it routed, both sliced by book and by stratum).

This is OFFLINE: there is no escalation loop. A live run could send an OPERATIONAL-reason route back
for more reps; here we just apply the policy to the votes ON HAND and COUNT those routes as
would-escalate load (`LoadMetrics.escalatable_routed`). So the harness measures a policy's behavior
on the evidence as committed, which is exactly what makes two policies comparable on identical data.

`evaluation/` MAY import `teacher/decision.py`'s policies (the downstream judge reads the thing it
judges); the reverse — the teacher importing the eval/student — is the forbidden direction.
"""
from __future__ import annotations

import tomllib
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass

from ..identity import BookKey, Label, ReaderTag
from ..teacher.decision import (
    OPERATIONAL_REASONS,
    DecisionPolicy,
    LineDecision,
    Outcome,
    PanelRoster,
    parse_roster,
    policy_from_toml,
)
from .datasets import AlignedSet, Stratum
from .metrics import (
    Breakdown,
    PolicyMetrics,
    accept_metrics,
    capture_metrics,
    load_metrics,
)


@dataclass(frozen=True, slots=True)
class PolicySpec:
    """A named policy bound to the roster it decides over — the unit the replay and the config
    loader pass around. The roster is config (which reader is the anchor), the policy is the rule."""

    name: str
    roster: PanelRoster
    policy: DecisionPolicy


@dataclass(frozen=True, slots=True)
class PolicyOutcome:
    """One policy's raw replay result before scoring: the spec it ran and every `(LineDecision,
    truth, book, stratum)` it produced, in aligned-set (document) order. Kept separate from
    `PolicyMetrics` so the decisions stay inspectable (which lines it routed, and why)."""

    spec: PolicySpec
    decisions: tuple[tuple[LineDecision, Label, BookKey, Stratum], ...]


def replay(aligned: AlignedSet, specs: tuple[PolicySpec, ...]) -> tuple[PolicyOutcome, ...]:
    """Apply each spec's policy to every aligned line (grouping that line's votes by reader tag) and
    collect the routed decisions alongside the truth/book/stratum needed to score them. Pure."""
    outcomes: list[PolicyOutcome] = []
    for spec in specs:
        rows: list[tuple[LineDecision, Label, BookKey, Stratum]] = []
        for line in aligned.lines:
            by_tag = {v.tag: v for v in line.votes}
            d = spec.policy.decide(line.id, by_tag, spec.roster)
            rows.append((d, line.truth, line.id.book_key, line.stratum))
        outcomes.append(PolicyOutcome(spec=spec, decisions=tuple(rows)))
    return tuple(outcomes)


def _accept_by(rows: list[tuple[LineDecision, Label, BookKey, Stratum]],
               key: Callable[[BookKey, Stratum], str]) -> Breakdown:
    """Accept-metrics sliced by a per-row key (book or stratum), computed only over ACCEPTED lines —
    a slice with no accepts is simply absent (an all-zero row would imply a measured 0.0 accuracy)."""
    buckets: dict[str, list[tuple[Label, Label]]] = defaultdict(list)
    for d, truth, book, stratum in rows:
        if d.outcome is Outcome.ACCEPT and d.label is not None:
            buckets[key(book, stratum)].append((truth, d.label))
    return {k: accept_metrics(pairs) for k, pairs in sorted(buckets.items())}


def score(outcome: PolicyOutcome, aligned: AlignedSet) -> PolicyMetrics:
    """Score a replayed policy into its full scorecard: accept-quality on the lines it accepted, load
    on the lines it routed (OPERATIONAL-reason routes counted as escalatable), both broken down by
    book and by stratum, plus `coverage` = accepted / total."""
    rows = list(outcome.decisions)
    n_total = len(rows)

    accepted_pairs = [(truth, d.label) for d, truth, _, _ in rows
                      if d.outcome is Outcome.ACCEPT and d.label is not None]
    accept = accept_metrics(accepted_pairs)

    # total-population capture: every line as (truth, accepted_label-or-None) — None = routed to human.
    capture = capture_metrics([(truth, d.label if d.outcome is Outcome.ACCEPT else None)
                               for d, truth, _, _ in rows])

    human = [d for d, _, _, _ in rows if d.outcome is Outcome.HUMAN]
    escalatable = sum(d.reason in OPERATIONAL_REASONS for d in human)
    load = load_metrics(n_total=n_total, human_routed=len(human), escalatable_routed=escalatable)

    return PolicyMetrics(
        name=outcome.spec.name, accept=accept, capture=capture, load=load,
        by_book=_accept_by(rows, lambda book, stratum: str(book)),
        by_stratum=_accept_by(rows, lambda book, stratum: str(stratum)),
        coverage=accept.n_accepted / n_total if n_total else 0.0)


def replay_and_score(aligned: AlignedSet,
                     specs: tuple[PolicySpec, ...]) -> tuple[PolicyMetrics, ...]:
    """The whole replay in one call: replay each spec, score it. The convenience the CLI + lock test
    use; `replay`/`score` stay separate so the raw decisions remain inspectable."""
    return tuple(score(o, aligned) for o in replay(aligned, specs))


# --- the TOML config: class-in-Python, instance-in-TOML ---------------------------------------
# The roster + per-policy grammar lives in `teacher.decision` (the policy module owns its config), so
# the live teacher path can build a policy without importing the eval. The eval's only addition is
# the MANY-policy framing: a shared `[roster]` + one or more `[[policy]]` tables to compare.

def load_policy_specs(toml_text: str, *,
                      present_readers: frozenset[ReaderTag] | None = None) -> tuple[PolicySpec, ...]:
    """Parse a policy-eval recipe (a `[roster]` table + one or more `[[policy]]` tables) into
    `PolicySpec`s — each `[[policy]]` bound to the shared roster. FAILS LOUD (via the shared parsers)
    on an unknown kind, an anchor that is also in support, an empty roster, or — when `present_readers`
    is given — a roster reader that never voted; and here on a duplicate policy name or no policies."""
    d = tomllib.loads(toml_text)
    roster = parse_roster(d["roster"], known=present_readers, known_desc="the data")
    specs: list[PolicySpec] = []
    seen: set[str] = set()
    for p in d.get("policy", ()):
        name = str(p["name"])
        if name in seen:
            raise ValueError(f"duplicate policy name {name!r}")
        seen.add(name)
        specs.append(PolicySpec(name=name, roster=roster, policy=policy_from_toml(p)))
    if not specs:
        raise ValueError("no [[policy]] tables in the recipe")
    return tuple(specs)


if __name__ == "__main__":
    import sys
    from pathlib import Path

    from .datasets import from_store
    from .metrics import compare

    recipe = Path(sys.argv[1]) if len(sys.argv) > 1 else (
        Path(__file__).resolve().parents[3] / "campaigns" / "recipes" / "policy-eval.toml")
    aligned = from_store()
    present = frozenset(v.tag for line in aligned.lines for v in line.votes)
    specs = load_policy_specs(recipe.read_text(), present_readers=present)
    results = replay_and_score(aligned, specs)

    print(f"aligned lines (truth ⋈ >=1 vote): {aligned.n_total}  "
          f"(prose {aligned.n_prose} / lineated {aligned.n_lineated})\n")
    print(compare(results))
