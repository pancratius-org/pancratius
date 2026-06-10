# research-pure: the LLM panel runner — an injectable ChatCompleter, opaque keys on the wire.
"""Runs the reader panel over a task: for each reader × rep × item, build a prompt that shows the
opaque-keyed listing (and, for a vision reader, the composite image) and asks for a JSON array of
`{key, label, conf}` over the item's keys ONLY — never an idx or src_ordinal. The network is a
single injected `ChatCompleter` boundary, so the panel is unit-testable with a fake completer and
imports no HTTP library; an OpenAI-compatible OpenRouter adapter is the one impl that does I/O.

The criteria shown to readers are the task's `instructions` (the same text the human adjudicator
sees) — there is no separate "brief". `build_prompt` assembles those instructions + the listing
into the PROMPT (the model input)."""
from __future__ import annotations

import hashlib
import threading
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, assert_never

from ..annotations import PanelVote, VoteKey
from ..identity import JsonRow, ListingKey, ModelId, PromptFingerprint, ReaderTag
from .responses import RawReaderResponse, parse_reader_reply
from .tasks import AssetKind, Modality, RegionId, ResponseContract, Task, TaskItem

# `ResponseContract` is DEFINED in `tasks` (the shared lower layer, beside `Modality`) so the panel and
# the `responses` choke point name it without an import cycle; re-exported as the panel's public surface.
__all__ = ["ResponseContract"]

type Message = dict[str, object]   # an OpenAI-style chat message: {"role", "content"}


class FinishReason(StrEnum):
    """The OpenAI/OpenRouter completion stop reasons the panel branches on. `LENGTH` means the
    model hit `max_tokens` mid-answer — a truncated, under-covered reply the run REFUSES to promote.
    Stored verbatim as the raw `str` on `ChatReply` (an unknown provider value passes through
    untouched); this enum is the named value to compare against, never re-parsed from the wire."""
    STOP = "stop"
    LENGTH = "length"
    CONTENT_FILTER = "content_filter"
    TOOL_CALLS = "tool_calls"


@dataclass(frozen=True, slots=True)
class ChatReply:
    content: str
    finish_reason: str | None = None    # LENGTH ⇒ truncated; the caller may retry larger
    usage: dict[str, object] | None = None   # provider token/cost accounting, verbatim; None if absent


class ChatCompleter(Protocol):
    """Messages → a completion. The ONE injectable LLM boundary: the OpenRouter adapter implements
    it for real; tests pass a fake. Nothing in the panel core imports an HTTP client. `response_format`
    is an optional OpenRouter structured-output schema (`verdict_schema`); an adapter that cannot honor
    it ignores it and the free-text parse still applies."""

    def complete(self, *, model: ModelId, messages: list[Message], temperature: float,
                 max_tokens: int, response_format: dict[str, object] | None = None) -> ChatReply: ...


@dataclass(frozen=True, slots=True)
class ReaderConfig:
    """One panel reader: its tag, the model behind it, and the modality it reads in (a text reader
    gets no image even when the task carries one)."""
    tag: ReaderTag
    model: ModelId
    modality: Modality
    temperature: float = 0.0
    max_tokens: int = 8192


@dataclass(frozen=True, slots=True)
class PanelConfig:
    readers: tuple[ReaderConfig, ...]
    reps: int = 1                       # repetitions per reader per item (instability evidence)
    contract: ResponseContract = ResponseContract.ARRAY   # the structured-output shape every reader returns


@dataclass(frozen=True, slots=True)
class PanelRep:
    """One reader's one rep on one item: the RAW completion text, its parse, which rep, the model,
    and whether the reply was truncated. `content` is the unparsed reply kept verbatim — so a
    malformed JSON / empty / all-reasoning answer survives as evidence even when `response.rows` is
    empty. The per-rep record behind the resolved `votes.jsonl`."""
    item_id: RegionId
    tag: ReaderTag
    rep: int
    model: ModelId
    content: str
    response: RawReaderResponse
    finish_reason: str | None
    usage: dict[str, object] | None = None   # provider token/cost accounting, carried from the reply


# The format example uses a PLACEHOLDER key, never a real one like "L001": a literal real key both
# collides with item 1's key and primes the model to echo / continue the L-sequence (observed
# key_item_mismatch faults from readers inventing keys past the ones shown). The explicit "only the
# keys shown, do not invent" is the instruction-side guard for that hallucination — and `verdict_schema`
# enforces it structurally for adapters that honor structured outputs (the `key` enum). The object
# wrapper (`{"verdicts": […]}`) matches that schema (a top-level array is not allowed there).
_RETURN = ('Return ONLY a JSON object {"verdicts": [ … ]}, one entry per line key shown — use the EXACT '
           'keys shown above, do NOT invent keys or continue the numbering:\n'
           '{"verdicts": [{"key": "<one of the keys shown>", "label": "prose" | "lineated", '
           '"conf": 0.0-1.0}]}')


def verdict_schema(contract: ResponseContract, keys: Sequence[ListingKey]) -> dict[str, object]:
    """An OpenRouter structured-output `response_format` scoped to the SHOWN keys, dispatched on
    `contract`. ARRAY constrains a `{key,label,conf}` array (`key` an enum of exactly these keys, so an
    invented/continued key is structurally impossible; coverage is NOT enforced — the resolver checks
    it). KEYED_OBJECT makes the keys themselves the schema: one REQUIRED string property per key, so a
    compliant reply has exactly one label per shown key — no invent, no miss, no dup.
    `strict`+`additionalProperties:false` are the enforcement either way."""
    match contract:
        case ResponseContract.KEYED_OBJECT:
            return {
                "type": "json_schema",
                "json_schema": {
                    "name": "lineation_by_key",
                    "strict": True,
                    "schema_": {            # serialized to "schema" by the SDK (pydantic alias)
                        "type": "object",
                        "properties": {
                            k: {"type": "string", "enum": ["prose", "lineated"],
                                "description": "Author's lineation intent for this line."}
                            for k in keys},
                        "required": list(keys),
                        "additionalProperties": False,
                    },
                },
            }
        case ResponseContract.ARRAY:
            return {
                "type": "json_schema",
                "json_schema": {
                    "name": "lineation_verdicts",
                    "strict": True,
                    "schema_": {            # serialized to "schema" by the SDK (pydantic alias)
                        "type": "object",
                        "properties": {
                            "verdicts": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "key": {"type": "string", "enum": list(keys)},
                                        "label": {"type": "string", "enum": ["prose", "lineated"]},
                                        "conf": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                                    },
                                    "required": ["key", "label", "conf"],
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "required": ["verdicts"],
                        "additionalProperties": False,
                    },
                },
            }
        case _:
            assert_never(contract)


def build_prompt(item: TaskItem, reader: ReaderConfig, instructions: str) -> list[Message]:
    """The reader-facing messages for one item. The opaque-keyed listing carries the structure (its
    feature tokens ARE a text reader's view of it); a vision reader also gets one page image per
    rendered page. The prompt asks for the item's keys ONLY — no idx/src_ordinal can appear, by
    construction."""
    text = f"{instructions}\n\nLines to judge (answer EVERY key shown):\n{item.context}\n\n{_RETURN}"
    parts: list[dict] = [{"type": "text", "text": text}]
    if reader.modality is Modality.VISION:
        parts += [{"type": "image_url", "image_url": {"url": a.data_uri}}
                  for a in item.assets if a.kind is AssetKind.COMPOSITE]   # one part per page
    return [{"role": "user", "content": parts}]


def _fingerprint(messages: Sequence[Message], *, temperature: float, max_tokens: int,
                 contract: ResponseContract) -> PromptFingerprint:
    """A short stable hash of the WHOLE request the model sees — the prompt text parts (instructions +
    opaque-keyed listing), any image part's data-URI, the sampling config (temperature, max_tokens),
    AND the response contract. The cache key carries it so a reply is reused ONLY for an identical
    request: editing the instructions, changing the rendered page image, the sampling params, OR the
    output schema each shift the fingerprint, so none silently reuses a reply made under a different
    config (the render slice, the sampling params, and the contract can all change under a fixed task)."""
    h = hashlib.sha256()
    h.update(f"\x00G{temperature!r}|{max_tokens}|{contract.value}".encode())  # request config
    for m in messages:
        content = m.get("content")
        for p in (content if isinstance(content, list) else []):
            if not isinstance(p, dict):
                continue
            if p.get("type") == "text":
                h.update(b"\x00T" + str(p.get("text", "")).encode())
            elif p.get("type") == "image_url":
                url = p.get("image_url", {})
                ref = url.get("url", "") if isinstance(url, dict) else ""
                h.update(b"\x00I" + str(ref).encode())
    return h.hexdigest()[:16]


# The call identity (see `CompletionRequest`): a model/prompt/sampling/contract change must re-call,
# never silently reuse a reply made under a different request. The resume cache stays keyed by the
# stable 5-tuple `cache_key` shape (per task_id; the prompt, sampling, and contract all fold into the
# `fingerprint` term of that key).
type CallKey = tuple[RegionId, ReaderTag, int, ModelId, PromptFingerprint]   # the call identity
type CallCache = dict[CallKey, ChatReply]            # already-completed calls to RESUME from


@dataclass(frozen=True, slots=True)
class CompletionRequest:
    """One planned panel call: the FULL request to send + the identity/(de)serialization it owns.
    Built for EVERY reader×rep×item up front so the network fetches can fan out while the assembled
    result stays in this order. `fingerprint` hashes the whole request (prompt + image + sampling +
    contract); `cache_key` is the stable resume-cache key; `to_row`/`reply_from_row` are the ONE
    (de)serializer for the resume log — no consumer reassembles this identity by hand."""
    item_id: RegionId
    tag: ReaderTag
    rep: int
    model: ModelId
    messages: tuple[Message, ...]
    temperature: float
    max_tokens: int
    contract: ResponseContract
    fingerprint: PromptFingerprint = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "fingerprint", _fingerprint(
            self.messages, temperature=self.temperature, max_tokens=self.max_tokens,
            contract=self.contract))

    @property
    def cache_key(self) -> CallKey:
        return (self.item_id, self.tag, self.rep, self.model, self.fingerprint)

    def to_row(self, reply: ChatReply) -> JsonRow:
        """The resume-log row for a fresh reply — every identity field the resume reuse keys on,
        plus the reply payload. The ONE serializer; `reply_from_row` is its inverse."""
        return {"item_id": self.item_id, "tag": self.tag, "rep": self.rep, "model": self.model,
                "prompt_hash": self.fingerprint, "contract": self.contract.value,
                "content": reply.content, "finish_reason": reply.finish_reason, "usage": reply.usage}

    @staticmethod
    def reply_from_row(row: JsonRow) -> ChatReply:
        """A resume-log row → the `ChatReply` it persisted — the ONE reply deserializer."""
        return ChatReply(content=str(row.get("content", "")),
                         finish_reason=row.get("finish_reason"),  # type: ignore[arg-type]
                         usage=row.get("usage"))  # type: ignore[arg-type]


type OnCall = Callable[[CompletionRequest, ChatReply], None]   # persist a fresh reply the instant it lands


@dataclass(frozen=True, slots=True)
class _Call:
    """A planned call paired with its item — the item carries the keys the schema is scoped to and
    the order the result assembles in; the request owns everything sent + the resume identity."""
    item: TaskItem
    req: CompletionRequest


def run_panel(task: Task, cfg: PanelConfig, completer: ChatCompleter, *,
              cached: CallCache | None = None, on_call: OnCall | None = None,
              instructions_by_modality: Mapping[Modality, str] | None = None,
              max_workers: int = 1) -> list[PanelRep]:
    """For each reader × rep × item: REUSE a saved reply from `cached`, else build_prompt →
    completer.complete → (persist via `on_call`) → parse. `cached`+`on_call` make a run RESUMABLE:
    a saved `(item, reader, rep, model, prompt_fp)` reply is never re-fetched, and a fresh one is
    persisted BEFORE it is parsed, so a crash mid-run loses no paid call. A reply is reused ONLY for
    the SAME prompt (the key carries the prompt fingerprint), so editing the prompt re-calls.

    `instructions_by_modality` lets each reader get a MODALITY-appropriate prompt — a vision reader the
    page-authority prompt, a text reader a listing/structure-authority one (a text reader cannot use a
    page it never receives). A reader's modality not in the map falls back to `task.instructions`.

    `max_workers` runs the cache-MISS fetches on a thread pool (the calls are I/O-bound on the
    completer) — `1` is the plain sequential path. Results are assembled in reader×rep×item order
    REGARDLESS of completion order, so the output is deterministic; `on_call` is invoked under a lock,
    so a single-file resume log stays line-atomic. Requires a thread-safe completer for `>1`.
    Returns per-rep records (resolution + rep aggregation are separate). Pure given the completer."""
    cache = cached or {}
    by_modality = instructions_by_modality or {}
    calls: list[_Call] = []
    for reader in cfg.readers:
        instructions = by_modality.get(reader.modality, task.instructions)
        for rep in range(cfg.reps):
            for item in task.items:
                req = CompletionRequest(
                    item_id=item.id, tag=reader.tag, rep=rep, model=reader.model,
                    messages=tuple(build_prompt(item, reader, instructions)),
                    temperature=reader.temperature, max_tokens=reader.max_tokens,
                    contract=cfg.contract)
                calls.append(_Call(item, req))

    replies: dict[CallKey, ChatReply] = {c.req.cache_key: cache[c.req.cache_key]
                                         for c in calls if c.req.cache_key in cache}
    misses = [c for c in calls if c.req.cache_key not in cache]
    lock = threading.Lock()

    def fetch(c: _Call) -> tuple[CallKey, ChatReply]:
        reply = completer.complete(
            model=c.req.model, messages=list(c.req.messages),
            temperature=c.req.temperature, max_tokens=c.req.max_tokens,
            response_format=verdict_schema(c.req.contract, [ln.key for ln in c.item.lines]))
        if on_call is not None:                             # persist before parse — evidence survives
            with lock:
                on_call(c.req, reply)
        return c.req.cache_key, reply

    if max_workers > 1 and len(misses) > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            for key, reply in ex.map(fetch, misses):
                replies[key] = reply
    else:
        for c in misses:
            key, reply = fetch(c)
            replies[key] = reply

    return [PanelRep(
        item_id=c.req.item_id, tag=c.req.tag, rep=c.req.rep, model=c.req.model,
        content=replies[c.req.cache_key].content,
        response=parse_reader_reply(cfg.contract, c.req.item_id, c.req.tag,
                                    replies[c.req.cache_key].content),
        finish_reason=replies[c.req.cache_key].finish_reason,
        usage=replies[c.req.cache_key].usage) for c in calls]


def aggregate_reps(per_rep: Sequence[Sequence[PanelVote]]) -> tuple[PanelVote, ...]:
    """Collapse RESOLVED per-rep votes to ONE canonical vote per (reader, LineId): the strict
    majority label among the reps that voted (a tie or no-majority ABSTAINS — the line is rerun /
    escalated, never guessed). `conf` = mean conf of the agreeing reps (None if none reported).
    Run-to-run instability stays in `panel_runs`; this is the promotable per-reader view, with no
    duplicate (reader, line), so it is safe to promote directly."""
    by_key: dict[VoteKey, list[PanelVote]] = defaultdict(list)
    for rep in per_rep:
        for v in rep:
            by_key[(v.tag, v.id)].append(v)
    out: list[PanelVote] = []
    for (tag, lid), votes in by_key.items():
        top, n = Counter(v.label for v in votes).most_common(1)[0]
        if n * 2 <= len(votes):                      # not a strict majority → abstain
            continue
        confs = [v.conf for v in votes if v.label == top and v.conf is not None]
        out.append(PanelVote(id=lid, tag=tag, label=top,
                             conf=sum(confs) / len(confs) if confs else None))
    return tuple(sorted(out, key=lambda v: (v.id, v.tag)))
