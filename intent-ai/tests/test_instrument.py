from __future__ import annotations

import hashlib

from intent_ai import paths, store
from intent_ai.identity import LineId
from intent_ai.instrument import E1_V2


def test_e1_v2_identity_pins_both_disjoint_halves():
    frozen_path = paths.ANNOTATIONS / "eval_sets" / f"{E1_V2.frozen_set}.json"
    working_path = paths.ANNOTATIONS / "eval_sets" / f"{E1_V2.working_set}.json"
    frozen = {LineId.from_key(key) for key in store.load_eval_set(E1_V2.frozen_set)}
    working = {LineId.from_key(key) for key in store.load_eval_set(E1_V2.working_set)}

    assert E1_V2.source_identity == "source-v3"
    assert len(frozen) == E1_V2.frozen_size
    assert len(working) == E1_V2.working_size
    assert frozen.isdisjoint(working)
    assert hashlib.sha256(frozen_path.read_bytes()).hexdigest() == E1_V2.frozen_digest
    assert hashlib.sha256(working_path.read_bytes()).hexdigest() == E1_V2.working_digest
