# research-pure: a declarative run recipe — one TOML file == one reproducible panel run.
"""A `RunSpec` captures everything that defines a gold panel run (regions, panel, gates, reps,
audit) as data, so a new experiment is a new `runs/*.toml`, not a code edit. The schema is here;
the recipes are pure TOML. `run.py` turns a spec into the `panel` (paid reads) and `recipe`
(deterministic aggregate→audit→manifest) steps.

`loads` validates structure eagerly (unknown keys, unsafe name/prefix, rep/audit ranges, empty
core, duplicate rids) so a malformed recipe fails before any spend. File existence (brief/flags)
is checked by `run.py`, which owns the path root. Paths are relative to the intent-classifier dir.
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .types import Gates

_NAME = re.compile(r"[A-Za-z0-9_-]+")
# prefix becomes part of a `_`-delimited rep filename (reader_<lead>_<prefix><N>.jsonl), so it must
# NOT contain `_` — else the rep-number parse breaks and escalation can overwrite rep 1.
_PREFIX = re.compile(r"[A-Za-z0-9-]+")
_TOP = {"name", "description", "dataset", "prefix", "brief", "notes",
        "panel", "audit", "gates", "regions", "flags"}
_SECTIONS = {"panel": {"core", "diagnostic", "reps_initial", "reps_cap", "workers"},
             "audit": {"rate", "seed", "prose_bias"},
             "gates": {"conf_floor", "min_agree", "escalate_reps"},
             "regions": {"rids"},
             "flags": {"soft", "needs_review"}}


@dataclass(frozen=True, slots=True)
class Panel:
    core: tuple[str, ...]                # acceptance-gate readers; first is the lead
    diagnostic: tuple[str, ...] = ()     # run alongside but NOT in the gate (e.g. glm)
    reps_initial: int = 1                # adaptive protocol's first stage
    reps_cap: int = 3                    # escalation ceiling
    workers: int = 12                    # concurrent API calls per panel run (recipe-tunable)

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


def _check_keys(section: dict, allowed: set[str], where: str) -> None:
    if extra := set(section) - allowed:
        raise ValueError(f"unknown key(s) in {where}: {sorted(extra)}")


def loads(text: str) -> RunSpec:
    """Parse + structurally validate a run-recipe TOML. Raises ValueError on any malformation."""
    d = tomllib.loads(text)
    _check_keys(d, _TOP, "recipe")
    for k in ("name", "description", "dataset", "prefix", "brief"):
        if k not in d:
            raise ValueError(f"missing required key: {k}")
    for sec, allowed in _SECTIONS.items():
        _check_keys(d.get(sec, {}), allowed, f"[{sec}]")
    if not _NAME.fullmatch(d["name"]):
        raise ValueError(f"name must be [A-Za-z0-9_-]+ (no paths/spaces): {d['name']!r}")
    if not _PREFIX.fullmatch(d["prefix"]):
        raise ValueError(f"prefix must be [A-Za-z0-9-]+ (no '_'/paths/spaces): {d['prefix']!r}")

    p, a, g, r, f = (d.get(s, {}) for s in ("panel", "audit", "gates", "regions", "flags"))
    core = tuple(p.get("core", ()))
    if not core:
        raise ValueError("[panel].core must be non-empty")
    ri, rc = p.get("reps_initial", 1), p.get("reps_cap", 3)
    if not 1 <= ri <= rc:
        raise ValueError(f"reps_initial ({ri}) must be in 1..reps_cap ({rc})")
    workers = p.get("workers", 12)
    if workers < 1:
        raise ValueError(f"[panel].workers must be ≥ 1: {workers}")
    rate = a.get("rate", 0.08)
    if not 0 < rate <= 1:
        raise ValueError(f"[audit].rate must be in (0,1]: {rate}")
    if a.get("prose_bias", 2.0) <= 0:
        raise ValueError("[audit].prose_bias must be > 0")
    rids = tuple(r.get("rids", ()))
    if dup := sorted({x for x in rids if rids.count(x) > 1}):
        raise ValueError(f"duplicate rids: {dup}")

    spec = RunSpec(
        name=d["name"], description=d["description"], dataset=d["dataset"],
        prefix=d["prefix"], brief=d["brief"],
        panel=Panel(core=core, diagnostic=tuple(p.get("diagnostic", ())),
                    reps_initial=ri, reps_cap=rc, workers=p.get("workers", 12)),
        audit=AuditSpec(rate=rate, seed=a.get("seed", 7), prose_bias=a.get("prose_bias", 2.0)),
        rids=rids, conf_floor=g.get("conf_floor", 0.7), min_agree=g.get("min_agree", 2),
        escalate_reps=g.get("escalate_reps", 3),
        soft=f.get("soft"), needs_review=f.get("needs_review"), notes=d.get("notes", ""),
    )
    spec.gates()   # validate gate coherence (lead∈core, min_agree≤len core, conf range) eagerly
    return spec


def load(path: str | Path) -> RunSpec:
    return loads(Path(path).read_text())
