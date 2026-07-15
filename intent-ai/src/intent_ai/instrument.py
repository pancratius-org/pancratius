"""Versioned identities for evaluation instruments."""
from __future__ import annotations

from dataclasses import dataclass
from typing import NewType

EvalSetName = NewType("EvalSetName", str)
InstrumentIdentity = NewType("InstrumentIdentity", str)
EvalSetDigest = NewType("EvalSetDigest", str)


@dataclass(frozen=True, slots=True)
class InstrumentVersion:
    identity: InstrumentIdentity
    source_identity: str
    predecessor: InstrumentIdentity | None
    frozen_set: EvalSetName
    frozen_digest: EvalSetDigest
    frozen_size: int
    working_set: EvalSetName
    working_digest: EvalSetDigest
    working_size: int

    def __post_init__(self) -> None:
        if self.frozen_set == self.working_set:
            raise ValueError("instrument halves must be distinct")
        if self.frozen_size <= 0 or self.working_size <= 0:
            raise ValueError("instrument halves must be non-empty")
        for digest in (self.frozen_digest, self.working_digest):
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError(f"invalid eval-set SHA-256 {digest!r}")


E1_V2 = InstrumentVersion(
    identity=InstrumentIdentity("e1-v2"),
    source_identity="source-v3",
    predecessor=InstrumentIdentity("e1-v1"),
    frozen_set=EvalSetName("e1-v2-frozen"),
    frozen_digest=EvalSetDigest(
        "7d7db0c99d848c74daa5895cdd6cfe76be70806dc5b763fd46672a5d5cbff8c9"
    ),
    frozen_size=726,
    working_set=EvalSetName("e1-v2-working"),
    working_digest=EvalSetDigest(
        "65a93b07434f25280e7e46a70a518ecc120f8412b12e5fa0d23eac47e2997175"
    ),
    working_size=728,
)
