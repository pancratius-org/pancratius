# research-pure: a study's provenance manifest — what code, inputs, and sampling produced a scorecard.
"""The reproducibility record an experiment commits beside its scorecard: the git SHA (with `+dirty`
if the tree was uncommitted), the timestamp, the eval set + per-modality prompt + response-contract
fingerprints, the model ids behind the tags, the sampling config, the price-table version, and the
sweep. A scorecard without this is a number with no lineage; with it, the study is re-runnable and
its claims are auditable. PURE data — the git SHA and the timestamp are STAMPED BY THE SHELL and
passed in, never read here, so the manifest builder stays a value object."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Self

from ..identity import JsonObject, ModelId, ReaderTag


@dataclass(frozen=True, slots=True)
class PromptFingerprint:
    """One modality's reader prompt as committed: its filename + content sha256, so a replayed study
    fails loud if the prompt text drifted under the same name."""
    filename: str
    sha256: str

    def to_dict(self) -> JsonObject:
        return {"filename": self.filename, "sha256": self.sha256}

    @classmethod
    def from_dict(cls, d: Mapping[str, object]) -> Self:
        return cls(filename=str(d["filename"]), sha256=str(d["sha256"]))


@dataclass(frozen=True, slots=True)
class Manifest:
    """A study run's full provenance. `git_sha`/`timestamp` are stamped by the shell (the runner passes
    them in). `eval_set_sha256` pins the slice MEMBERSHIP file and `truth_sha256` the joined truth
    as scored (truth lives in `labels.jsonl`, so the membership hash alone cannot prove the labels);
    `prompts` pins the per-modality reader text, `base_response_contract` the DEFAULT output shape
    (the AUTHORITATIVE contract provenance is `sweep_axis`/`sweep_points` when the study sweeps
    `contract`); `models` resolves each reader tag to its model id; `sweep_axis`/`sweep_points`
    record what was varied."""
    git_sha: str
    timestamp: str                  # ISO8601, passed in by the shell — never hardcoded
    eval_set: str
    eval_set_sha256: str            # the membership file bytes
    truth_sha256: str               # the joined {LineId: label} truth as scored
    prompts: Mapping[str, PromptFingerprint]    # modality value → prompt fingerprint
    base_response_contract: str     # the recipe default; a contract sweep's points are authoritative
    models: Mapping[ReaderTag, ModelId]
    temperature: float
    max_tokens: int
    reps: int
    seed: int
    price_table_version: str
    sweep_axis: str
    sweep_points: tuple[str, ...]

    def to_dict(self) -> JsonObject:
        return {
            "git_sha": self.git_sha,
            "timestamp": self.timestamp,
            "eval_set": self.eval_set,
            "eval_set_sha256": self.eval_set_sha256,
            "truth_sha256": self.truth_sha256,
            "prompts": {mod: fp.to_dict() for mod, fp in self.prompts.items()},
            "base_response_contract": self.base_response_contract,
            "models": dict(self.models),
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "reps": self.reps,
            "seed": self.seed,
            "price_table_version": self.price_table_version,
            "sweep_axis": self.sweep_axis,
            "sweep_points": list(self.sweep_points),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, object]) -> Self:
        return cls(
            git_sha=str(d["git_sha"]),
            timestamp=str(d["timestamp"]),
            eval_set=str(d["eval_set"]),
            eval_set_sha256=str(d["eval_set_sha256"]),
            truth_sha256=str(d["truth_sha256"]),
            prompts={str(mod): PromptFingerprint.from_dict(fp)
                     for mod, fp in d["prompts"].items()},
            base_response_contract=str(d["base_response_contract"]),
            models={str(t): str(m) for t, m in d["models"].items()},
            temperature=float(d["temperature"]),
            max_tokens=int(d["max_tokens"]),
            reps=int(d["reps"]),
            seed=int(d["seed"]),
            price_table_version=str(d["price_table_version"]),
            sweep_axis=str(d["sweep_axis"]),
            sweep_points=tuple(str(p) for p in d["sweep_points"]),
        )
