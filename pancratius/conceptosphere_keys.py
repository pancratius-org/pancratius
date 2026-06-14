"""Stable identity keys for the conceptosphere graph (dependency-free).

These keys are the bilingual-overlay join contract: the translation file
`data/conceptosphere-i18n/<locale>.json` binds to them, and the build-time
join + the completeness audit are keyed on them. They live here, apart from
the heavy graph generator (`conceptosphere.py` pulls igraph/leidenalg/pymorphy3),
so the contract is importable and testable without the `graph` extra installed.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable


def community_key(members: Iterable[object]) -> str:
    """Content fingerprint of a community = hash of its sorted member ids.

    A community's identity is its membership set, not its size-rank ordinal.
    The ordinal reshuffles on every rebuild that changes sizes; this is
    invariant under reshuffling and changes only when the member set changes —
    which is exactly when the overlay must re-translate it. Deterministic:
    same member set → same key; changed member set → different key.
    """
    joined = "\n".join(sorted(str(m) for m in members))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:8]
