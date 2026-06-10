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
from typing import Self

from .. import producer
from ..identity import JsonObject, LineId, LineTextHash, ListingKey
from ..records import RecordsByBook
# `TaskKey`/`RegionId`/`ResponseContract` live in `contracts` (the wire protocol owns the
# wire-visible names); re-exported here so task-model consumers keep one import home.
from .contracts import RegionId, ResponseContract, TaskKey  # noqa: F401


class Modality(StrEnum):
    TEXT = "text"           # evidence = the feature-rich listing only
    VISION = "vision"       # evidence = the listing + rendered page/candidate images


class AssetKind(StrEnum):
    COMPOSITE = "composite"           # the authored page render shown to a vision reader


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
    poll; `assets` carries the page renders (empty for a text-only task) — what a reader receives
    is the READER's modality choice, not the item's."""
    id: RegionId
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
    text_hash_by_key: dict[TaskKey, LineTextHash]
    item_by_key: dict[TaskKey, RegionId]

    @property
    def item_ids(self) -> frozenset[RegionId]:
        return frozenset(self.item_by_key.values())

    def keys_for_item(self, item_id: RegionId) -> frozenset[TaskKey]:
        return frozenset(k for k, it in self.item_by_key.items() if it == item_id)

    def to_dict(self) -> JsonObject:
        return {"by_key": {k: lid.as_key() for k, lid in self.by_key.items()},
                "text_hash_by_key": dict(self.text_hash_by_key),
                "item_by_key": dict(self.item_by_key)}

    @classmethod
    def from_dict(cls, d: Mapping[str, object]) -> Self:
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

    def to_payload(self) -> JsonObject:
        """The reader/UI-facing JSON — opaque keys ONLY, the manifest OMITTED."""
        return {
            "title": self.title,
            "instructions": self.instructions,
            "items": [_item_payload(it, self.line_options) for it in self.items],
        }

    @classmethod
    def from_bundle(cls, payload: Mapping[str, object], manifest: Mapping[str, object]) -> Self:
        """Rebuild a Task from a persisted payload + manifest — enough to RE-RUN the panel and
        RESOLVE (items + instructions + the manifest). The composite assets are reconstructed from the
        payload's `images` (one per page), so a vision re-run still attaches every page."""
        items = tuple(
            TaskItem(
                id=it["id"], context=it.get("structure", ""),
                lines=tuple(TaskLine(key=ln["key"], text=ln["text"], hint=ln.get("hint", ""))
                            for ln in it.get("lines", [])),
                assets=tuple(EvidenceAsset(kind=AssetKind.COMPOSITE, data_uri=u)
                             for u in it.get("images", [])))
            for it in payload.get("items", []))
        return cls(title=payload.get("title", ""), instructions=payload.get("instructions", ""),
                   items=items, manifest=TaskManifest.from_dict(manifest))


def _item_payload(it: TaskItem, options: tuple[LineOption, ...]) -> JsonObject:
    payload: JsonObject = {
        "id": it.id,
        "mode": "per-line",
        "structure": it.context,
        "lineOptions": [{"value": v, "label": label} for v, label in options],
        "lines": [_line_payload(ln) for ln in it.lines],
    }
    images = [a.data_uri for a in it.assets if a.kind is AssetKind.COMPOSITE]
    if images:                                      # vision only — one page image per part; text=none
        payload["images"] = images
    return payload


def _line_payload(ln: TaskLine) -> JsonObject:
    row: JsonObject = {"key": ln.key, "text": ln.text}
    if ln.hint:
        row["hint"] = ln.hint
    return row


@dataclass(frozen=True, slots=True)
class ItemSpec:
    """One task item: the region's lines IN THE ORDER they are shown (the caller/selector owns this
    order — it is rendered verbatim, never re-sorted), and which of them are votable (get an `L00N`
    key). A region line that is not votable is context, shown un-keyed for orientation."""
    region_id: RegionId
    region: tuple[LineId, ...]
    votable: frozenset[LineId]

    @classmethod
    def all_votable(cls, region_id: RegionId, ids: Sequence[LineId]) -> Self:
        """A region with no separate context — every shown line is polled, in the given order."""
        return cls(region_id=region_id, region=tuple(ids), votable=frozenset(ids))


# The one-page votable-span cap (source paragraphs): a region whose mapped votable lines span more than
# this cannot be rendered on one page. ONE source of truth for both the splitter (`recipes.page_size_
# regions`) and the renderer (`render.make_compositor`/`_region_assets`), here in the shared lower layer
# both import without a cycle, so the split bound and the render bound provably agree.
PAGE_SPAN_CAP = 120


def _key(n: int, width: int) -> TaskKey:
    return f"L{n:0{width}d}"


def build_task(
    *, title: str, instructions: str, specs: Sequence[ItemSpec], records: RecordsByBook,
    with_features: bool = True,
    assets: Mapping[RegionId, tuple[EvidenceAsset, ...]] | None = None,
) -> Task:
    """Mint a Task. `L001…L00N` are assigned PER TASK in the caller's region order across all items
    — the ONE place a key is born. Each votable line's record supplies its text + line-text hash
    (the manifest) and the region listing (via `producer.render_listing` with the minted key map).
    `records` is the `{book: records}` data the shell loads; this stays pure. The region is rendered
    VERBATIM in the order the selector passed — never re-sorted."""
    by_id = {r.id: r for book in records.values() for r in book}
    width = max(3, len(str(sum(len(s.votable) for s in specs))))
    assets = assets or {}

    by_key: dict[TaskKey, LineId] = {}
    text_hash_by_key: dict[TaskKey, LineTextHash] = {}
    item_by_key: dict[TaskKey, RegionId] = {}
    items: list[TaskItem] = []
    n = 0
    for spec in specs:
        key_by_id: dict[LineId, ListingKey] = {}
        lines: list[TaskLine] = []
        for lid in spec.region:                        # the caller's order, verbatim
            if lid not in spec.votable:
                continue                               # a context line — shown un-keyed
            rec = by_id[lid]
            n += 1
            key = _key(n, width)
            key_by_id[lid] = key
            by_key[key] = lid
            text_hash_by_key[key] = rec.line_text_hash
            item_by_key[key] = spec.region_id
            lines.append(TaskLine(key=key, text=rec.text))
        region = [by_id[lid] for lid in spec.region]   # rendered in the caller's order — never sorted
        context = producer.render_listing(region, keys=key_by_id, with_features=with_features)
        items.append(TaskItem(
            id=spec.region_id, context=context,
            lines=tuple(lines), assets=tuple(assets.get(spec.region_id, ()))))
    return Task(title=title, instructions=instructions, items=tuple(items),
                manifest=TaskManifest(by_key=by_key, text_hash_by_key=text_hash_by_key,
                                      item_by_key=item_by_key))
