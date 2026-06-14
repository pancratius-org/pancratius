"""Stable-identity contract for the bilingual conceptosphere overlay.

Tests the dependency-free key logic (`conceptosphere_keys.community_key`) and
the invariants the generator relies on, asserted against the CURRENTLY COMMITTED
graph JSON — no regen, no heavy `graph` extra. The generator (conceptosphere.py)
promotes the lemma to `concept_id` and fingerprints each community with this
key; these tests pin the properties that make that join sound.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from pancratius.conceptosphere_keys import community_key

REPO_ROOT = Path(__file__).resolve().parents[2]
CONCEPTS = REPO_ROOT / "data" / "pancratius-concepts-graph.json"
BOOKS = REPO_ROOT / "data" / "pancratius-books-graph.json"


# --- community_key: pure determinism / stability -------------------------


def test_community_key_is_order_invariant() -> None:
    assert community_key(["свет", "творец", "бог"]) == community_key(["бог", "свет", "творец"])


def test_community_key_same_members_same_key() -> None:
    members = ["свет", "творец", "истина"]
    assert community_key(members) == community_key(list(members))


def test_community_key_changed_members_changed_key() -> None:
    base = ["свет", "творец", "истина"]
    added = [*base, "страх"]
    removed = ["свет", "творец"]
    assert community_key(base) != community_key(added)
    assert community_key(base) != community_key(removed)


def test_community_key_accepts_int_members() -> None:
    # Book communities fingerprint over book numbers, not strings.
    assert community_key([3, 1, 2]) == community_key([1, 2, 3])
    assert community_key([1, 2, 3]) != community_key([1, 2, 4])


def test_community_key_is_eight_hex_chars() -> None:
    key = community_key(["свет", "творец"])
    assert len(key) == 8
    assert all(c in "0123456789abcdef" for c in key)


# --- committed graph preconditions for the promotion ---------------------


def test_concept_lemmas_are_unique_so_concept_id_is_unique() -> None:
    nodes = json.loads(CONCEPTS.read_text(encoding="utf-8"))["nodes"]
    lemmas = [n["lemma"] for n in nodes]
    assert len(lemmas) == len(set(lemmas)), "lemma promotion to concept_id requires 1:1 uniqueness"
    # `id` is the lemma today, so promotion is a faithful rename.
    assert all(n["id"] == n["lemma"] for n in nodes)


def test_community_key_computes_over_committed_concept_communities() -> None:
    graph = json.loads(CONCEPTS.read_text(encoding="utf-8"))
    members: dict[int, list[str]] = defaultdict(list)
    for n in graph["nodes"]:
        members[n["community"]].append(n["lemma"])
    assert len(members) == len(graph["communities"])
    keys = {community_key(m) for m in members.values()}
    # Distinct membership sets → distinct fingerprints (no collisions on real data).
    assert len(keys) == len(members)


def test_community_key_computes_over_committed_book_communities() -> None:
    graph = json.loads(BOOKS.read_text(encoding="utf-8"))
    members: dict[int, list[int]] = defaultdict(list)
    for n in graph["nodes"]:
        members[n["community"]].append(n["number"])
    assert len(members) == len(graph["communities"])
    keys = {community_key(m) for m in members.values()}
    assert len(keys) == len(members)
