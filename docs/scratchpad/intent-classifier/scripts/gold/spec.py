# research-pure: a declarative run recipe — one TOML file == one reproducible panel run.
"""A `RunSpec` captures everything that defines a gold panel run (regions, panel, gates, reps,
audit) as data, so a new experiment is a new `runs/*.toml`, not a code edit. The schema is here;
the recipes are pure TOML. `run.py` turns a spec into the `panel` (paid reads) and `recipe`
(deterministic aggregate→audit→manifest) steps.

Paths (`brief`, `soft`, `needs_review`) are relative to the intent-classifier dir.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from .types import Gates


@dataclass(frozen=True, slots=True)
class Panel:
    core: tuple[str, ...]                # acceptance-gate readers; first is the lead
    diagnostic: tuple[str, ...] = ()     # run alongside but NOT in the gate (e.g. glm)
    reps_initial: int = 1                # adaptive protocol's first stage
    reps_cap: int = 3                    # escalation ceiling

    @property
    def readers(self) -> tuple[str, ...]:
        return (*self.core, *self.diagnostic)


@dataclass(frozen=True, slots=True)
class AuditSpec:
    rate: float = 0.08
    seed: int = 7
    prose_bias: float = 2.0


@dataclass(frozen=True, slots=True)
class RunSpec:
    name: str                            # the run_id; output lands in gold/<name>/
    description: str
    dataset: str
    prefix: str                          # rep-file prefix the panel writes / recipe consumes
    brief: str                           # reader brief recorded in the manifest
    panel: Panel
    audit: AuditSpec
    rids: tuple[str, ...] = ()           # explicit region selection (the sample manifest)
    conf_floor: float = 0.7
    min_agree: int = 2
    escalate_reps: int = 3
    soft: str | None = None              # json {rid:[[idx,sub]]} of prior-dependent lines
    needs_review: str | None = None      # json {rid:[[idx,sub]]} of substrate-flagged lines
    notes: str = ""

    def gates(self) -> Gates:
        return Gates(core=self.panel.core, lead=self.panel.core[0], conf_floor=self.conf_floor,
                     min_core_agree=self.min_agree, escalate_reps=self.escalate_reps)


def load(path: str | Path) -> RunSpec:
    """Parse a run-recipe TOML into a validated RunSpec (a bad/missing key raises)."""
    d = tomllib.loads(Path(path).read_text())
    p, a, g, r = d.get("panel", {}), d.get("audit", {}), d.get("gates", {}), d.get("regions", {})
    f = d.get("flags", {})
    spec = RunSpec(
        name=d["name"], description=d["description"], dataset=d["dataset"],
        prefix=d["prefix"], brief=d["brief"],
        panel=Panel(core=tuple(p["core"]), diagnostic=tuple(p.get("diagnostic", ())),
                    reps_initial=p.get("reps_initial", 1), reps_cap=p.get("reps_cap", 3)),
        audit=AuditSpec(rate=a.get("rate", 0.08), seed=a.get("seed", 7),
                        prose_bias=a.get("prose_bias", 2.0)),
        rids=tuple(r.get("rids", ())),
        conf_floor=g.get("conf_floor", 0.7), min_agree=g.get("min_agree", 2),
        escalate_reps=g.get("escalate_reps", 3),
        soft=f.get("soft"), needs_review=f.get("needs_review"), notes=d.get("notes", ""),
    )
    spec.gates()   # validate gate coherence (lead∈core, min_agree≤len core, conf range) eagerly
    return spec
