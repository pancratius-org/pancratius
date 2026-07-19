"""Source-v3 identity migration is total, explicit, and reproducible from its ledger."""
from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path

import pytest
from intent_ai import paths
from intent_ai.identity import LegacyLineId, LineId
from intent_ai.migration import (
    MigrationLedger,
    Moved,
    NeedsAdjudication,
    QuarantinedSurface,
    RemappedSurface,
    Retired,
    RewrittenSurface,
    SourceMigrationReceipt,
    membership_delta,
)

_BASELINE_ANNOTATIONS = "docs/scratchpad/lineation-core/annotations"


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _id(raw: object) -> LineId:
    return LineId.from_key(raw)


def _legacy_id(raw: object) -> LegacyLineId:
    return LegacyLineId.from_key(raw)


def _ledger() -> MigrationLedger:
    return MigrationLedger.from_rows(
        _jsonl(paths.ANNOTATIONS / "migrations" / "source-v3.jsonl")
    )


def _receipt() -> SourceMigrationReceipt:
    return SourceMigrationReceipt.from_dict(
        _json(paths.ANNOTATIONS / "migrations" / "source-v3-surfaces.json")
    )


def _surface_count(path: Path) -> int:
    if path.suffix == ".jsonl":
        return len(_jsonl(path))
    value = _json(path)
    if path.name.endswith(".manifest.json"):
        return len(value["by_key"]) + len(value.get("retired_by_key", {}))
    return len(value)


def _baseline_blob(commit: str, path: str) -> bytes:
    return subprocess.run(
        ["git", "show", f"{commit}:{_BASELINE_ANNOTATIONS}/{path}"],
        check=True,
        capture_output=True,
    ).stdout


def _wire_membership(payload: bytes) -> list[list[object]]:
    value = json.loads(payload)
    assert isinstance(value, list)
    return value


def test_migration_ledger_is_complete_and_collision_free():
    ledger = _ledger()
    assert Counter(type(entry) for entry in ledger.entries) == {
        Moved: 179,
        NeedsAdjudication: 21,
        Retired: 16,
    }
    targets = [entry.after for entry in ledger.entries if isinstance(entry, Moved)]
    assert len(targets) == len(set(targets))


# labels.jsonl / votes.jsonl are GROWING stores: their receipt after-snapshots were true at
# resolution time, but new truth and panel evidence append past them by design. Their permanent
# conservation is proven by the ledger + the static quarantine files + the baseline-blob test —
# a current-bytes pin would re-freeze the store.
_GROWING_STORES = frozenset({"labels.jsonl", "votes.jsonl"})


def test_migration_receipt_pins_every_changed_live_surface():
    receipt = _receipt()
    assert receipt.baseline_commit == "bef6cc6a8886091823fd148e6fd380ce388f48d0"
    assert receipt.before_source_identity == "source-v2"
    assert receipt.after_source_identity == "source-v3"
    paths_seen: set[str] = set()
    for snapshot in receipt.live_snapshots:
        assert snapshot.path not in paths_seen
        paths_seen.add(snapshot.path)
        path = paths.ANNOTATIONS / snapshot.path
        if snapshot.path in _GROWING_STORES:
            assert _surface_count(path) >= snapshot.count
            continue
        assert _surface_count(path) == snapshot.count
        assert hashlib.sha256(path.read_bytes()).hexdigest() == snapshot.sha256


@pytest.mark.repository_history
def test_migration_receipt_proves_baseline_blobs_and_membership_deltas():
    receipt = _receipt()
    for surface in receipt.surfaces:
        before = _baseline_blob(receipt.baseline_commit, surface.before.path)
        assert hashlib.sha256(before).hexdigest() == surface.before.sha256
        assert _surface_count_from_bytes(surface.before.path, before) == surface.before.count

        if isinstance(surface, RemappedSurface):
            after = (paths.ANNOTATIONS / surface.after.path).read_bytes()
            removed, added = membership_delta(
                _wire_membership(before),
                _wire_membership(after),
            )
            assert removed == surface.removed
            assert added == surface.added
        elif isinstance(surface, (QuarantinedSurface, RewrittenSurface)):
            pass


def _surface_count_from_bytes(path: str, payload: bytes) -> int:
    if path.endswith(".jsonl"):
        return len(payload.splitlines())
    value = json.loads(payload)
    if path.endswith(".manifest.json"):
        return len(value["by_key"]) + len(value.get("retired_by_key", {}))
    return len(value)


def test_every_historical_label_has_one_explicit_disposition():
    active = _jsonl(paths.ANNOTATIONS / "labels.jsonl")
    unresolved = _jsonl(
        paths.ANNOTATIONS / "history" / "source-v2" / "unresolved-labels.jsonl"
    )
    historical = {
        _legacy_id(
            row.get("provenance", {}).get("source_v3_migration", {}).get("from", row["id"])
        )
        for row in active
    } | {_legacy_id(row["id"]) for row in unresolved}
    assert len(historical) == 2_194

    by_before = _ledger().by_before
    for row in active:
        migrated_from = row.get("provenance", {}).get("source_v3_migration", {}).get("from")
        if migrated_from is None:
            assert _legacy_id(row["id"]) not in by_before
        else:
            entry = by_before[_legacy_id(migrated_from)]
            assert isinstance(entry, Moved)
            assert entry.after == _id(row["id"])
            assert entry.text_hash == row["line_text_hash"]
    for row in unresolved:
        assert not isinstance(by_before[_legacy_id(row["id"])], Moved)


def test_all_live_annotation_references_target_resolved_lines():
    """A live row must never BE a migrated-away v2 line. Numeric keys COINCIDE across the two
    identity spaces (v2 and v3 ordinals were renumbered), so raw-key disjointness is the wrong
    test at density: a dense selection legitimately contains current lines whose keys equal some
    legacy before-key. The typed contract is hash-shaped — where a live surface carries a text
    hash (labels, task manifests), a key that collides with a ledger before-key must carry a
    DIFFERENT hash than that legacy line did."""
    ledger = _ledger()
    resolved = {entry.after for entry in ledger.entries if isinstance(entry, Moved)}
    unresolved = {entry.before for entry in ledger.entries if not isinstance(entry, Moved)}
    old_hash_by_key = {
        tuple(entry.before.as_key()): (
            entry.text_hash if isinstance(entry, Moved) else entry.old_text_hash
        )
        for entry in ledger.entries
    }

    def assert_not_the_legacy_line(raw_key: object, text_hash: object) -> None:
        old = old_hash_by_key.get(tuple(_id(raw_key).as_key()))
        if old is not None:
            assert text_hash != old, f"live row {raw_key} carries a migrated-away line's hash"

    live: set[LineId] = set()
    for row in _jsonl(paths.ANNOTATIONS / "labels.jsonl"):
        live.add(_id(row["id"]))
        assert_not_the_legacy_line(row["id"], row["line_text_hash"])
    for row in _jsonl(paths.ANNOTATIONS / "votes.jsonl"):
        live.add(_id(row["id"]))
    for directory in ("eval_sets", "selections"):
        for path in (paths.ANNOTATIONS / directory).glob("*.json"):
            live |= {_id(raw) for raw in _json(path)}
    for path in (paths.ANNOTATIONS / "tasks").glob("*.manifest.json"):
        manifest = _json(path)
        for key, raw in manifest["by_key"].items():
            live.add(_id(raw))
            assert_not_the_legacy_line(raw, manifest["text_hash_by_key"][key])
        retired = {_legacy_id(raw) for raw in manifest.get("retired_by_key", {}).values()}
        assert retired <= unresolved

    assert resolved <= live


def test_unresolved_truth_stays_out_of_the_active_store():
    unresolved = _jsonl(
        paths.ANNOTATIONS / "history" / "source-v2" / "unresolved-labels.jsonl"
    )
    human = [row for row in unresolved if row["source"] == "human"]
    assert len(unresolved) == 35
    assert len(human) == 1
    assert human[0]["id"] == ["en", "63", 5964, 0]
