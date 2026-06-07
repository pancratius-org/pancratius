# research-pure: the annotation TASK model + the L001 mint — opaque keys out, LineId in the manifest.
"""A `Task` is a unit of work shown to LLM readers and to the human adjudicator. Outward it carries
ONLY task-local opaque keys (`L001`); the `LineId` each maps to lives in a PRIVATE `TaskManifest`
that `to_payload()` never emits. So a source ordinal cannot reach a prompt or the UI — the reader/UI
echoes `L001`, and `responses.resolve_*` maps it back to a `LineId` at one choke point before
anything persists.

`build_task` is the ONE place `L001…L00N` is minted: a region's votable lines get keys in document
order, context lines are shown un-keyed for orientation. The feature-rich listing comes from the
ONE `producer.render_listing` (passed the `{LineId → TaskKey}` map), so the teacher's evidence and
the student's vector stay one feature set."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Self

from .. import producer
from ..identity import LineId, ListingKey
from ..records import LineRecord, RecordsByBook

type TaskKey = ListingKey   # a task-local opaque key ("L001") — a ListingKey minted per task
type RegionId = str         # stable human-readable region tag (book + range); the UI's item id


class Modality(StrEnum):
    TEXT = "text"           # evidence = the feature-rich listing only
    VISION = "vision"       # evidence = the listing + rendered page/candidate images


class AssetKind(StrEnum):
    DOCX_PAGE = "docx_page"            # the authored page render — the authority
    CANDIDATE_PROSE = "cand_prose"
    CANDIDATE_LINEATED = "cand_lineated"
    COMPOSITE = "composite"           # the docx | prose | lineated tile shown to a vision reader


type LineOption = tuple[str, str]     # (value, display label) for the UI's per-line picker
_DEFAULT_OPTIONS: tuple[LineOption, ...] = (("prose", "Prose"), ("lineated", "Lineated"))


@dataclass(frozen=True, slots=True)
class EvidenceAsset:
    """One visual evidence image as an embeddable data-URI (so the UI works offline and the panel
    can inline it). VISION-only; built by `teacher.render`."""
    kind: AssetKind
    data_uri: str
    caption: str = ""


@dataclass(frozen=True, slots=True)
class TaskLine:
    """One votable body line as the reader/UI sees it: an OPAQUE key + the line text. No `LineId`,
    no `src_ordinal`. The feature-rich rendering of the whole region is `TaskItem.context`."""
    key: TaskKey
    text: str
    hint: str = ""          # optional per-line hint (the UI hides it by default)


@dataclass(frozen=True, slots=True)
class TaskItem:
    """A region judged as a unit. `context` is the feature-rich listing of the whole region
    (votable lines keyed, neighbours un-keyed for orientation); `lines` are the votable lines to
    poll; `assets` is empty for TEXT modality."""
    id: RegionId
    modality: Modality
    context: str
    lines: tuple[TaskLine, ...]
    assets: tuple[EvidenceAsset, ...] = ()


@dataclass(frozen=True, slots=True)
class TaskManifest:
    """The PRIVATE resolution table: task-local key → `LineId`, the line-text hash captured at mint
    time (so resolution fails loud if the corpus drifted under a replayed task), and the item each
    key belongs to (so a verdict returned under the wrong item is caught). Resolution needs ONLY
    this — not the whole `Task` — so ingest just loads the manifest. Never in the reader/UI payload;
    persisted SEPARATELY and read only by `responses`."""
    by_key: dict[TaskKey, LineId]
    text_hash_by_key: dict[TaskKey, str]
    item_by_key: dict[TaskKey, RegionId]

    def resolve(self, key: TaskKey) -> LineId | None:
        return self.by_key.get(key)

    @property
    def item_ids(self) -> frozenset[RegionId]:
        return frozenset(self.item_by_key.values())

    def keys_for_item(self, item_id: RegionId) -> frozenset[TaskKey]:
        return frozenset(k for k, it in self.item_by_key.items() if it == item_id)

    def to_dict(self) -> dict[str, Any]:
        return {"by_key": {k: lid.as_key() for k, lid in self.by_key.items()},
                "text_hash_by_key": dict(self.text_hash_by_key),
                "item_by_key": dict(self.item_by_key)}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Self:
        return cls(by_key={k: LineId.from_key(v) for k, v in d["by_key"].items()},
                   text_hash_by_key=dict(d["text_hash_by_key"]),
                   item_by_key=dict(d["item_by_key"]))


@dataclass(frozen=True, slots=True)
class Task:
    """A whole annotation task: items + the private manifest. `to_payload()` strips the manifest and
    emits exactly what `adjudicate.html` and the panel readers consume — opaque keys only."""
    title: str
    instructions: str
    items: tuple[TaskItem, ...]
    manifest: TaskManifest
    line_options: tuple[LineOption, ...] = _DEFAULT_OPTIONS

    def to_payload(self) -> dict[str, Any]:
        """The reader/UI-facing JSON — opaque keys ONLY, the manifest OMITTED."""
        return {
            "title": self.title,
            "instructions": self.instructions,
            "items": [_item_payload(it, self.line_options) for it in self.items],
        }


def _item_payload(it: TaskItem, options: tuple[LineOption, ...]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": it.id,
        "mode": "per-line",
        "structure": it.context,
        "lineOptions": [{"value": v, "label": label} for v, label in options],
        "lines": [_line_payload(ln) for ln in it.lines],
    }
    composite = next((a for a in it.assets if a.kind is AssetKind.COMPOSITE), None)
    if composite is not None:                       # vision only — the listing carries text mode
        payload["image"] = composite.data_uri
    return payload


def _line_payload(ln: TaskLine) -> dict[str, Any]:
    row: dict[str, Any] = {"key": ln.key, "text": ln.text}
    if ln.hint:
        row["hint"] = ln.hint
    return row


@dataclass(frozen=True, slots=True)
class ItemSpec:
    """Declarative spec for one task item: the votable lines to poll (document order), the
    neighbouring lines shown un-keyed for orientation, and the reader modality."""
    region_id: RegionId
    vote_ids: tuple[LineId, ...]
    context_ids: tuple[LineId, ...] = ()
    modality: Modality = Modality.TEXT


def _key(n: int, width: int) -> TaskKey:
    return f"L{n:0{width}d}"


def build_task(
    *, title: str, instructions: str, specs: Sequence[ItemSpec], records: RecordsByBook,
    with_features: bool = True,
    assets: Mapping[RegionId, tuple[EvidenceAsset, ...]] | None = None,
) -> Task:
    """Mint a Task. `L001…L00N` are assigned PER TASK in document order across all items — the ONE
    place a key is born. Each votable line's record supplies its text + line-text hash (the
    manifest) and the region listing (via `producer.render_listing` with the minted key map).
    `records` is the `{book: records}` data the shell loads; this stays pure."""
    by_id = {r.id: r for book in records.values() for r in book}
    width = max(3, len(str(sum(len(s.vote_ids) for s in specs))))
    assets = assets or {}

    by_key: dict[TaskKey, LineId] = {}
    text_hash_by_key: dict[TaskKey, str] = {}
    item_by_key: dict[TaskKey, RegionId] = {}
    items: list[TaskItem] = []
    n = 0
    for spec in specs:
        key_by_id: dict[LineId, ListingKey] = {}
        lines: list[TaskLine] = []
        for lid in sorted(spec.vote_ids):              # mint in document (reading) order
            rec = by_id[lid]
            n += 1
            key = _key(n, width)
            key_by_id[lid] = key
            by_key[key] = lid
            text_hash_by_key[key] = rec.line_text_hash
            item_by_key[key] = spec.region_id
            lines.append(TaskLine(key=key, text=rec.text))
        region = [by_id[lid] for lid in sorted({*spec.vote_ids, *spec.context_ids})]  # reading order
        context = producer.render_listing(region, keys=key_by_id, with_features=with_features)
        items.append(TaskItem(
            id=spec.region_id, modality=spec.modality, context=context,
            lines=tuple(lines), assets=tuple(assets.get(spec.region_id, ()))))
    return Task(title=title, instructions=instructions, items=tuple(items),
                manifest=TaskManifest(by_key=by_key, text_hash_by_key=text_hash_by_key,
                                      item_by_key=item_by_key))
