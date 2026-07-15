"""Typed lineage for source-identity migrations."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import assert_never, cast

from .identity import JsonObject, LegacyLineId, LineId, LineTextHash


class MigrationStatus(StrEnum):
    MOVED = "moved"
    NEEDS_ADJUDICATION = "needs_adjudication"
    RETIRED = "retired"


@dataclass(frozen=True, slots=True)
class Moved:
    before: LegacyLineId
    after: LineId
    text_hash: LineTextHash
    reason: str

    def __post_init__(self) -> None:
        if self.before.as_key() == self.after.as_key():
            raise ValueError(f"migration does not move {self.before}")
        if self.before.book_key != self.after.book_key:
            raise ValueError(f"migration crosses editions: {self.before} -> {self.after}")


@dataclass(frozen=True, slots=True)
class NeedsAdjudication:
    before: LegacyLineId
    old_text_hash: LineTextHash | None
    reason: str


@dataclass(frozen=True, slots=True)
class Retired:
    before: LegacyLineId
    old_text_hash: LineTextHash | None
    reason: str


type LineMigration = Moved | NeedsAdjudication | Retired


@dataclass(frozen=True, slots=True)
class SurfaceSnapshot:
    path: str
    count: int
    sha256: str

    def __post_init__(self) -> None:
        if not self.path or self.count < 0:
            raise ValueError("invalid migration surface snapshot")
        if len(self.sha256) != 64 or any(c not in "0123456789abcdef" for c in self.sha256):
            raise ValueError(f"invalid migration-surface SHA-256 {self.sha256!r}")


@dataclass(frozen=True, slots=True)
class SurfaceDelta:
    count: int
    sha256: str

    def __post_init__(self) -> None:
        if self.count < 0:
            raise ValueError("negative migration surface delta")
        if len(self.sha256) != 64 or any(c not in "0123456789abcdef" for c in self.sha256):
            raise ValueError(f"invalid migration-delta SHA-256 {self.sha256!r}")


def membership_delta(
    before: Iterable[Iterable[object]],
    after: Iterable[Iterable[object]],
) -> tuple[SurfaceDelta, SurfaceDelta]:
    """Removed and added LineId memberships, hashed as sorted compact JSON arrays."""

    def keys(rows: Iterable[Iterable[object]]) -> set[tuple[object, ...]]:
        return {tuple(LegacyLineId.from_key(row).as_key()) for row in rows}

    def delta(rows: set[tuple[object, ...]]) -> SurfaceDelta:
        wire = json.dumps(
            [list(row) for row in sorted(rows)],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
        return SurfaceDelta(len(rows), hashlib.sha256(wire).hexdigest())

    before_keys = keys(before)
    after_keys = keys(after)
    return delta(before_keys - after_keys), delta(after_keys - before_keys)


@dataclass(frozen=True, slots=True)
class QuarantinedSurface:
    name: str
    before: SurfaceSnapshot
    after: SurfaceSnapshot
    quarantined: SurfaceSnapshot

    def __post_init__(self) -> None:
        if self.before.count != self.after.count + self.quarantined.count:
            raise ValueError(f"{self.name} migration loses rows")


@dataclass(frozen=True, slots=True)
class RemappedSurface:
    name: str
    before: SurfaceSnapshot
    after: SurfaceSnapshot
    removed: SurfaceDelta
    added: SurfaceDelta

    def __post_init__(self) -> None:
        if self.before.count - self.removed.count + self.added.count != self.after.count:
            raise ValueError(f"{self.name} migration has an inconsistent membership delta")


@dataclass(frozen=True, slots=True)
class RewrittenSurface:
    name: str
    before: SurfaceSnapshot
    after: SurfaceSnapshot

    def __post_init__(self) -> None:
        if self.before.count != self.after.count:
            raise ValueError(f"{self.name} migration loses manifest identities")


type MigratedSurface = QuarantinedSurface | RemappedSurface | RewrittenSurface


def _snapshot(value: object) -> SurfaceSnapshot:
    raw = cast(Mapping[str, object], value)
    return SurfaceSnapshot(str(raw["path"]), int(cast(int, raw["count"])), str(raw["sha256"]))


def _delta(value: object) -> SurfaceDelta:
    raw = cast(Mapping[str, object], value)
    return SurfaceDelta(int(cast(int, raw["count"])), str(raw["sha256"]))


def _surface(value: object) -> MigratedSurface:
    raw = cast(Mapping[str, object], value)
    common = {
        "name": str(raw["name"]),
        "before": _snapshot(raw["before"]),
        "after": _snapshot(raw["after"]),
    }
    if "quarantined" in raw and "removed" not in raw and "added" not in raw:
        return QuarantinedSurface(**common, quarantined=_snapshot(raw["quarantined"]))
    if "removed" in raw and "added" in raw and "quarantined" not in raw:
        return RemappedSurface(
            **common, removed=_delta(raw["removed"]), added=_delta(raw["added"])
        )
    if not ({"quarantined", "removed", "added"} & raw.keys()):
        return RewrittenSurface(**common)
    raise ValueError(f"migration surface {common['name']!r} has an ambiguous conservation rule")


@dataclass(frozen=True, slots=True)
class SourceMigrationReceipt:
    before_source_identity: str
    after_source_identity: str
    baseline_commit: str
    surfaces: tuple[MigratedSurface, ...]
    ledger: SurfaceSnapshot

    def __post_init__(self) -> None:
        if self.before_source_identity == self.after_source_identity:
            raise ValueError("source migration must change identity")
        if len(self.baseline_commit) != 40:
            raise ValueError("source migration baseline must be a full commit SHA")
        names = [surface.name for surface in self.surfaces]
        if len(names) != len(set(names)):
            raise ValueError("migration receipt contains duplicate surface names")

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> SourceMigrationReceipt:
        if value.get("schema") != "intent_ai.source_migration_receipt.v1":
            raise ValueError("unsupported source migration receipt")
        return cls(
            before_source_identity=str(value["before_source_identity"]),
            after_source_identity=str(value["after_source_identity"]),
            baseline_commit=str(value["baseline_commit"]),
            surfaces=tuple(_surface(row) for row in cast(Iterable[object], value["changed_surfaces"])),
            ledger=_snapshot(value["ledger"]),
        )

    @property
    def live_snapshots(self) -> tuple[SurfaceSnapshot, ...]:
        snapshots: list[SurfaceSnapshot] = [self.ledger]
        for surface in self.surfaces:
            snapshots.append(surface.after)
            if isinstance(surface, QuarantinedSurface):
                snapshots.append(surface.quarantined)
        return tuple(snapshots)


def _to_dict(entry: LineMigration) -> JsonObject:
    match entry:
        case Moved(before=before, after=after, text_hash=text_hash, reason=reason):
            return {
                "before": before.as_key(),
                "after": after.as_key(),
                "status": MigrationStatus.MOVED.value,
                "old_text_hash": text_hash,
                "new_text_hash": text_hash,
                "reason": reason,
            }
        case NeedsAdjudication(before=before, old_text_hash=old_hash, reason=reason):
            status = MigrationStatus.NEEDS_ADJUDICATION
        case Retired(before=before, old_text_hash=old_hash, reason=reason):
            status = MigrationStatus.RETIRED
        case unsupported:
            assert_never(unsupported)
    return {
        "before": before.as_key(),
        "after": None,
        "status": status.value,
        "old_text_hash": old_hash,
        "new_text_hash": None,
        "reason": reason,
    }


def _from_dict(value: Mapping[str, object]) -> LineMigration:
    before = LegacyLineId.from_key(cast(Iterable[object], value["before"]))
    reason = str(value["reason"])
    old_hash = value.get("old_text_hash")
    old_text_hash = LineTextHash(str(old_hash)) if old_hash is not None else None
    status = MigrationStatus(str(value["status"]))
    match status:
        case MigrationStatus.MOVED:
            after = value.get("after")
            new_hash = value.get("new_text_hash")
            if after is None or old_text_hash is None or new_hash != old_text_hash:
                raise ValueError(f"moved migration has inconsistent target/hash for {before}")
            return Moved(
                before,
                LineId.from_key(cast(Iterable[object], after)),
                old_text_hash,
                reason,
            )
        case MigrationStatus.NEEDS_ADJUDICATION:
            if value.get("after") is not None or value.get("new_text_hash") is not None:
                raise ValueError(f"unresolved migration has a target for {before}")
            return NeedsAdjudication(before, old_text_hash, reason)
        case MigrationStatus.RETIRED:
            if value.get("after") is not None or value.get("new_text_hash") is not None:
                raise ValueError(f"retired migration has a target for {before}")
            return Retired(before, old_text_hash, reason)
    assert_never(status)


@dataclass(frozen=True, slots=True)
class MigrationLedger:
    entries: tuple[LineMigration, ...]

    def __post_init__(self) -> None:
        before = [entry.before for entry in self.entries]
        if len(before) != len(set(before)):
            raise ValueError("migration ledger contains duplicate source identities")
        targets = [entry.after for entry in self.entries if isinstance(entry, Moved)]
        if len(targets) != len(set(targets)):
            raise ValueError("migration ledger contains duplicate canonical targets")

    @classmethod
    def from_rows(cls, rows: Iterable[Mapping[str, object]]) -> MigrationLedger:
        return cls(tuple(_from_dict(row) for row in rows))

    @property
    def by_before(self) -> dict[LegacyLineId, LineMigration]:
        return {entry.before: entry for entry in self.entries}

    def rows(self) -> tuple[JsonObject, ...]:
        return tuple(_to_dict(entry) for entry in self.entries)
