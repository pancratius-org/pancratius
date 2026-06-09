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
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from ..annotations import PanelVote, VoteKey
from ..identity import ListingKey, ModelId, PromptFingerprint, ReaderTag
from .responses import RawReaderResponse, parse_reader_reply
from .tasks import AssetKind, Modality, RegionId, Task, TaskItem

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


def verdict_schema(keys: Sequence[ListingKey]) -> dict[str, object]:
    """An OpenRouter structured-output `response_format` that CONSTRAINS the reply to one verdict per
    SHOWN key: `key` is an enum of exactly these keys — so an invented or sequence-continued key is
    structurally impossible under strict decoding — `label` ∈ {prose, lineated}, `conf` ∈ [0,1].
    Object-wrapped because a top-level array is not allowed. `strict`+`additionalProperties:false` are
    the enforcement. (Coverage — that EVERY key is answered — is not guaranteed by the schema; the
    resolver still checks it.)"""
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


# The call identity INCLUDES a fingerprint of the exact prompt sent (see `prompt_fingerprint`): a
# model/prompt change must re-call, never silently reuse a reply made under a different prompt — the
# resume cache is per task_id, and the prompt (instructions + listing) can change under a fixed id.
type CallKey = tuple[RegionId, ReaderTag, int, ModelId, PromptFingerprint]   # the call identity
type CallCache = dict[CallKey, ChatReply]            # already-completed calls to RESUME from
type OnCall = Callable[[CallKey, ChatReply], None]   # persist a fresh reply the instant it lands


def prompt_fingerprint(messages: list[Message]) -> PromptFingerprint:
    """A short stable hash of the WHOLE prompt the model sees — the text parts (instructions +
    opaque-keyed listing) AND any image part's data-URI. The cache key carries it so a reply is reused
    ONLY for an identical prompt: editing the instructions OR changing the rendered page image both
    shift the fingerprint, so neither silently reuses a stale reply (the render slice can change under
    a fixed task)."""
    h = hashlib.sha256()
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


@dataclass(frozen=True, slots=True)
class _Call:
    """One planned panel call: who/what to ask and the resume key. Built for EVERY reader×rep×item
    up front so the network fetches can fan out while the assembled result stays in this order."""
    reader: ReaderConfig
    rep: int
    item: TaskItem
    messages: list[Message]
    key: CallKey


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
                messages = build_prompt(item, reader, instructions)
                key: CallKey = (item.id, reader.tag, rep, reader.model, prompt_fingerprint(messages))
                calls.append(_Call(reader, rep, item, messages, key))

    replies: dict[CallKey, ChatReply] = {c.key: cache[c.key] for c in calls if c.key in cache}
    misses = [c for c in calls if c.key not in cache]
    lock = threading.Lock()

    def fetch(c: _Call) -> tuple[CallKey, ChatReply]:
        reply = completer.complete(
            model=c.reader.model, messages=c.messages,
            temperature=c.reader.temperature, max_tokens=c.reader.max_tokens,
            response_format=verdict_schema([ln.key for ln in c.item.lines]))
        if on_call is not None:                             # persist before parse — evidence survives
            with lock:
                on_call(c.key, reply)
        return c.key, reply

    if max_workers > 1 and len(misses) > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            for key, reply in ex.map(fetch, misses):
                replies[key] = reply
    else:
        for c in misses:
            key, reply = fetch(c)
            replies[key] = reply

    return [PanelRep(
        item_id=c.item.id, tag=c.reader.tag, rep=c.rep, model=c.reader.model,
        content=replies[c.key].content,
        response=parse_reader_reply(c.item.id, c.reader.tag, replies[c.key].content),
        finish_reason=replies[c.key].finish_reason, usage=replies[c.key].usage) for c in calls]


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
