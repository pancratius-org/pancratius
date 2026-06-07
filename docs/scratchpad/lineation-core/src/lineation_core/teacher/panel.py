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

from dataclasses import dataclass
from typing import Protocol

from ..identity import ReaderTag
from .responses import RawReaderResponse, parse_reader_reply
from .tasks import AssetKind, Modality, Task, TaskItem

type ModelId = str          # an OpenRouter model id, e.g. "x-ai/grok-4"
type Message = dict         # an OpenAI-style chat message: {"role", "content"}


@dataclass(frozen=True, slots=True)
class ChatReply:
    content: str
    finish_reason: str | None = None    # "length" ⇒ truncated; the caller may retry larger


class ChatCompleter(Protocol):
    """Messages → a completion. The ONE injectable LLM boundary: the OpenRouter adapter implements
    it for real; tests pass a fake. Nothing in the panel core imports an HTTP client."""

    def complete(self, *, model: ModelId, messages: list[Message], temperature: float,
                 max_tokens: int) -> ChatReply: ...


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
    """One reader's one rep on one item: the parsed response + which rep + the model + whether the
    reply was truncated. The per-rep evidence behind the resolved `votes.jsonl`."""
    item_id: str
    tag: ReaderTag
    rep: int
    model: ModelId
    response: RawReaderResponse
    finish_reason: str | None


_RETURN = ('Return ONLY a JSON array, one object per line key shown:\n'
           '[{"key": "L001", "label": "prose" | "lineated", "conf": 0.0-1.0}]')


def build_prompt(item: TaskItem, reader: ReaderConfig, instructions: str) -> list[Message]:
    """The reader-facing messages for one item. The opaque-keyed listing carries the structure (its
    feature tokens ARE a text reader's view of it); a vision reader also gets the composite image.
    The prompt asks for the item's keys ONLY — no idx/src_ordinal can appear, by construction."""
    text = f"{instructions}\n\nLines to judge (answer EVERY key shown):\n{item.context}\n\n{_RETURN}"
    parts: list[dict] = [{"type": "text", "text": text}]
    if reader.modality is Modality.VISION:
        composite = next((a for a in item.assets if a.kind is AssetKind.COMPOSITE), None)
        if composite is not None:
            parts.append({"type": "image_url", "image_url": {"url": composite.data_uri}})
    return [{"role": "user", "content": parts}]


def run_panel(task: Task, cfg: PanelConfig, completer: ChatCompleter) -> list[PanelRep]:
    """For each reader × rep × item: build_prompt → completer.complete → parse. Returns per-rep
    parsed responses (resolution + rep aggregation are separate). Pure given the completer."""
    reps: list[PanelRep] = []
    for reader in cfg.readers:
        for rep in range(cfg.reps):
            for item in task.items:
                reply = completer.complete(
                    model=reader.model, messages=build_prompt(item, reader, task.instructions),
                    temperature=reader.temperature, max_tokens=reader.max_tokens)
                reps.append(PanelRep(
                    item_id=item.id, tag=reader.tag, rep=rep, model=reader.model,
                    response=parse_reader_reply(item.id, reader.tag, reply.content),
                    finish_reason=reply.finish_reason))
    return reps
