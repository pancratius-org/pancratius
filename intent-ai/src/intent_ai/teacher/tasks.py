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

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Self, cast

from .. import producer
from ..identity import JsonObject, LegacyLineId, LineId, LineTextHash, ListingKey
from ..records import BookRecords
from ..wire import mapping, sequence, string

# `TaskKey`/`RegionId`/`ResponseContract` live in `contracts` (the wire protocol owns the
# wire-visible names); re-exported here so task-model consumers keep one import home.
from .contracts import RegionId, ResponseContract, TaskKey  # noqa: F401


class Modality(StrEnum):
    TEXT = "text"           # evidence = the feature-rich listing only
    VISION = "vision"       # evidence = the listing + rendered page/candidate images


type LineOption = tuple[str, str]     # (value, display label) for the UI's per-line picker
_DEFAULT_OPTIONS: tuple[LineOption, ...] = (("prose", "Prose"), ("lineated", "Lineated"))


@dataclass(frozen=True, slots=True)
class EvidenceAsset:
    """One authored-page render as an embeddable data-URI (so the UI works offline and the panel
    can inline it). VISION-only; built by `teacher.render`."""
    data_uri: str


@dataclass(frozen=True, slots=True)
class TaskLine:
    """One votable body line as the reader/UI sees it: an OPAQUE key + the line text. No `LineId`,
    no `src_ordinal`. The feature-rich rendering of the whole region is `TaskItem.context`."""
    key: TaskKey
    text: str


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
class RetiredTaskLine:
    """A task-local key whose source-v2 line has no active canonical target."""

    id: LegacyLineId
    text_hash: LineTextHash


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
    retired: dict[TaskKey, RetiredTaskLine] | None = None

    def __post_init__(self) -> None:
        live = set(self.by_key)
        if live != set(self.text_hash_by_key) or live != set(self.item_by_key):
            raise ValueError("task manifest live maps must have identical keys")
        retired = self.retired or {}
        object.__setattr__(self, "retired", retired)
        if live & retired.keys():
            raise ValueError("task manifest key cannot be both live and retired")

    @property
    def item_ids(self) -> frozenset[RegionId]:
        return frozenset(self.item_by_key.values())

    def keys_for_item(self, item_id: RegionId) -> frozenset[TaskKey]:
        return frozenset(k for k, it in self.item_by_key.items() if it == item_id)

    def to_dict(self) -> JsonObject:
        retired = self.retired or {}
        return {
            "by_key": {k: lid.as_key() for k, lid in self.by_key.items()},
            "text_hash_by_key": dict(self.text_hash_by_key),
            "item_by_key": dict(self.item_by_key),
            "retired_by_key": {k: line.id.as_key() for k, line in retired.items()},
            "retired_text_hash_by_key": {k: line.text_hash for k, line in retired.items()},
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, object]) -> Self:
        by_key = cast(Mapping[str, object], d["by_key"])
        hashes = cast(Mapping[str, object], d["text_hash_by_key"])
        items = cast(Mapping[str, object], d["item_by_key"])
        retired_ids = cast(Mapping[str, object], d.get("retired_by_key", {}))
        retired_hashes = cast(Mapping[str, object], d.get("retired_text_hash_by_key", {}))
        if retired_ids.keys() != retired_hashes.keys():
            raise ValueError("retired task identities and text hashes must have identical keys")
        return cls(
            by_key={
                key: LineId.from_key(cast(Iterable[object], value))
                for key, value in by_key.items()
            },
            text_hash_by_key={key: LineTextHash(str(value)) for key, value in hashes.items()},
            item_by_key={key: str(value) for key, value in items.items()},
            retired={
                key: RetiredTaskLine(
                    LegacyLineId.from_key(cast(Iterable[object], value)),
                    LineTextHash(str(retired_hashes[key])),
                )
                for key, value in retired_ids.items()
            },
        )


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
        items: list[TaskItem] = []
        for index, raw_item in enumerate(sequence(payload.get("items", []), field="task.items")):
            item = mapping(raw_item, field=f"task.items[{index}]")
            lines: list[TaskLine] = []
            for line_index, raw_line in enumerate(
                sequence(item.get("lines", []), field=f"task.items[{index}].lines")
            ):
                line = mapping(raw_line, field=f"task.items[{index}].lines[{line_index}]")
                lines.append(TaskLine(
                    key=string(line["key"], field="task.line.key"),
                    text=string(line["text"], field="task.line.text"),
                ))
            assets = tuple(
                EvidenceAsset(data_uri=string(url, field="task.image"))
                for url in sequence(item.get("images", []), field=f"task.items[{index}].images")
            )
            items.append(TaskItem(
                id=string(item["id"], field="task.item.id"),
                context=string(item.get("structure", ""), field="task.item.structure"),
                lines=tuple(lines),
                assets=assets,
            ))
        return cls(
            title=string(payload.get("title", ""), field="task.title"),
            instructions=string(payload.get("instructions", ""), field="task.instructions"),
            items=tuple(items),
            manifest=TaskManifest.from_dict(manifest),
        )


def _item_payload(it: TaskItem, options: tuple[LineOption, ...]) -> JsonObject:
    payload: JsonObject = {
        "id": it.id,
        "mode": "per-line",
        "structure": it.context,
        "lineOptions": [{"value": v, "label": label} for v, label in options],
        "lines": [_line_payload(ln) for ln in it.lines],
    }
    images = [a.data_uri for a in it.assets]
    if images:                                      # vision only — one page image per part; text=none
        payload["images"] = images
    return payload


def _line_payload(ln: TaskLine) -> JsonObject:
    return {"key": ln.key, "text": ln.text}


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


# The one-page votable-span cap (source paragraphs): a region whose votable lines span more than
# this cannot be rendered on one page. ONE source of truth for both the splitter (`recipes.page_size_
# regions`) and the renderer (`render.make_compositor`/`_region_assets`), here in the shared lower layer
# both import without a cycle, so the split bound and the render bound provably agree.
PAGE_SPAN_CAP = 120


def _key(n: int, width: int) -> TaskKey:
    return f"L{n:0{width}d}"


def build_task(
    *, title: str, instructions: str, specs: Sequence[ItemSpec], records: BookRecords,
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
