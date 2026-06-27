# research-pure: the reader RESPONSE CONTRACT — the verdict wire protocol, one closed concept.
"""A response contract is the wire protocol for a reader's verdicts — one concept with three
inseparable, mutually-defined behaviors:

  ASK       — the response-encoding instruction appended to the prompt;
  CONSTRAIN — the structured-output `response_format` schema scoped to the shown keys, or `None`
              (schemaless: the instruction is the sole constraint);
  READ      — the parser, raw reply → `RawReaderRow`s.

The instruction and the schema are TWO ENCODINGS of one format (prose + optional machine
constraint); the parse is its inverse. `spec_for` is the ONE dispatch — adding a contract is one
enum member + one `ContractSpec` + one match arm + tests, all in this module. The lowest teacher
layer: imports only `identity`, so the panel, the tasks model, and the resolution choke point all
name the contract without a cycle."""
from __future__ import annotations

import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import assert_never

from ..identity import ListingKey, ReaderTag

type TaskKey = ListingKey   # a task-local opaque key ("L001") — a ListingKey minted per task
type RegionId = str         # stable human-readable region tag (book + range); the UI's item id


class ResponseContract(StrEnum):
    """The wire SHAPE a reader is asked to return — folded into the call identity (the panel) so a
    contract change re-calls, never silently reuses a reply shaped under a different format.
    JSON_ARRAY: `{"verdicts":[{key, lineation_label, confidence}]}` — `key` is a free enum string
    (coverage unenforced).
    JSON_KEYED: `{"L001":{"lineation_label":"prose","confidence":0.8}, …}` — one REQUIRED property per
    shown key, so the keys ARE the schema (none invented/missing/duplicated under strict decoding); the
    value carries `confidence` too, so it differs from JSON_ARRAY only in SHAPE and both feed the gate.
    TSV: tab-separated `key⇥lineation_label⇥confidence` rows, SCHEMALESS — the instruction is all."""
    JSON_ARRAY = "json_array"
    JSON_KEYED = "json_keyed"
    TSV = "tsv"


@dataclass(frozen=True, slots=True)
class RawReaderRow:
    """What a reader/adjudicator returns for one line: an OPAQUE key + verdict. `conf` is optional
    (a human emits none; a model's is stored verbatim, never a fabricated default)."""
    key: TaskKey
    label: str          # the RAW reader output — validated to a `Label` only at resolution
    conf: float | None = None


@dataclass(frozen=True, slots=True)
class RawReaderResponse:
    """One reader's verdicts on one task item. `tag` is the panel reader, or "human" for an
    adjudication; `note` is the human's per-item free text."""
    item_id: RegionId
    tag: ReaderTag
    rows: tuple[RawReaderRow, ...]
    note: str = ""


# CONSTRAIN: an OpenRouter structured-output `response_format` scoped to the shown keys, or None
# (schemaless — TSV). Standard JSON Schema spelling ("schema"); any SDK-specific field aliasing is
# the network adapter's concern, never the pure core's.
type ResponseFormat = dict[str, object]
type SchemaFor = Callable[[Sequence[ListingKey]], ResponseFormat | None]
type ReplyParser = Callable[[str], tuple[RawReaderRow, ...]]


@dataclass(frozen=True, slots=True)
class ContractSpec:
    """One contract's three behaviors, bound together: `instruction` (ASK), `schema` (CONSTRAIN —
    `None` means the instruction is the sole constraint), `parse` (READ, the format's inverse)."""
    contract: ResponseContract
    instruction: str
    schema: SchemaFor
    parse: ReplyParser


def spec_for(contract: ResponseContract) -> ContractSpec:
    """THE dispatch: a contract → its spec. One match arm per member, `assert_never` closes it."""
    match contract:
        case ResponseContract.JSON_ARRAY:
            return _JSON_ARRAY
        case ResponseContract.JSON_KEYED:
            return _JSON_KEYED
        case ResponseContract.TSV:
            return _TSV
        case _:
            assert_never(contract)


# --- ASK: the per-contract instructions -------------------------------------------------------
# Every format example uses a PLACEHOLDER key, never a real one like "L001": a literal real key both
# collides with item 1's key and primes the model to echo / continue the L-sequence (observed
# key_item_mismatch faults from readers inventing keys past the ones shown). The explicit "do NOT
# invent" is the instruction-side guard for that hallucination; the JSON schemas enforce it
# structurally for adapters that honor structured outputs. The JSON_ARRAY object wrapper
# (`{"verdicts": […]}`) matches its schema (a top-level array is not allowed there).

_JSON_ARRAY_INSTRUCTION = (
    'Return ONLY a JSON object {"verdicts": [ … ]}, one entry per line key shown — use the EXACT '
    'keys shown above, do NOT invent keys or continue the numbering:\n'
    '{"verdicts": [{"key": "<one of the keys shown>", "lineation_label": "prose" | "lineated", '
    '"confidence": 0.0 to 1.0}]}')

_JSON_KEYED_INSTRUCTION = (
    'Return ONLY a JSON object with one entry per line key shown — use the EXACT keys shown above, '
    'do NOT invent keys or continue the numbering:\n'
    '{"<one of the keys shown>": {"lineation_label": "prose" | "lineated", "confidence": 0.0 to 1.0}}')

_TSV_INSTRUCTION = (
    'Return ONLY plain text, one TAB-separated row per line key shown — use the EXACT keys shown '
    'above, do NOT invent keys or continue the numbering:\n'
    '<one of the keys shown>\tprose | lineated\t<confidence 0.0 to 1.0>')


# --- CONSTRAIN: the per-contract response_format schemas --------------------------------------

def _json_array_schema(keys: Sequence[ListingKey]) -> ResponseFormat | None:
    """A `{key, lineation_label, confidence}` array, object-wrapped. `key` is an enum of exactly the
    shown keys, so an invented/continued key is structurally impossible; coverage is NOT enforced — the
    resolver checks it. `strict`+`additionalProperties:false` are the enforcement."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "lineation_verdicts",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "verdicts": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "key": {"type": "string", "enum": list(keys),
                                        "description": "The exact key of the line being judged."},
                                "lineation_label": {
                                    "type": "string", "enum": ["prose", "lineated"],
                                    "description": "Whether the line is ordinary prose or "
                                                   "intentionally lineated."},
                                "confidence": {
                                    "type": "number", "minimum": 0.0, "maximum": 1.0,
                                    "description": "Confidence in the verdict, from 0.0 to 1.0."},
                            },
                            "required": ["key", "lineation_label", "confidence"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["verdicts"],
                "additionalProperties": False,
            },
        },
    }


def _json_keyed_schema(keys: Sequence[ListingKey]) -> ResponseFormat | None:
    """The shown keys ARE the schema: one REQUIRED property per key — so a compliant reply has exactly
    one verdict per shown key, no invent/miss/dup. Each value is a `{label, conf}` object: the keys
    stay the fault-proofing, the value carries the same confidence the array contract does (so the two
    differ ONLY in shape, and both feed the conf-using gate)."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "lineation_by_key",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    k: {"type": "object",
                        "properties": {
                            "lineation_label": {
                                "type": "string", "enum": ["prose", "lineated"],
                                "description": "Whether the line is ordinary prose or "
                                               "intentionally lineated."},
                            "confidence": {
                                "type": "number", "minimum": 0.0, "maximum": 1.0,
                                "description": "Confidence in the verdict, from 0.0 to 1.0."},
                        },
                        "required": ["lineation_label", "confidence"], "additionalProperties": False,
                        "description": "The verdict for the line under this key."}
                    for k in keys},
                "required": list(keys),
                "additionalProperties": False,
            },
        },
    }


# --- READ: the per-contract parsers ------------------------------------------------------------

def _parse_json_array(text: str) -> tuple[RawReaderRow, ...]:
    """JSON_ARRAY read: the last balanced key-bearing JSON array. Under structured outputs the reply
    arrives OBJECT-WRAPPED (`{"verdicts": […]}`) and unfenced, but the scan extracts the inner array
    either way (tolerating the wrapper / code fences / leading prose). `conf` is kept verbatim,
    `None` when absent or out of range."""
    return tuple(
        RawReaderRow(key=str(o["key"]), label=_raw_label(o.get("lineation_label")),
                     conf=_conf(o.get("confidence")))
        for o in _json_array(text) if isinstance(o, dict) and "key" in o)


def _parse_json_keyed(text: str) -> tuple[RawReaderRow, ...]:
    """JSON_KEYED read: the last balanced `{key: {label, conf}}` object, top-level duplicate keys
    PRESERVED (one row per pair) so a repeated key still surfaces as `DUP_KEY` exactly as in the array
    path. Each value is a `{label, conf}` object (which `object_pairs_hook` yields as inner pairs); a
    reader that returns a bare `{key: label}` instead degrades to label-only, `conf=None`."""
    rows: list[RawReaderRow] = []
    for k, v in _json_object_pairs(text):
        vd = dict(v) if isinstance(v, list) else {}      # the inner {lineation_label,confidence} → dict
        if vd:
            rows.append(RawReaderRow(key=str(k), label=_raw_label(vd.get("lineation_label")),
                                     conf=_conf(vd.get("confidence"))))
        else:                                            # bare {key: "label"} — no confidence given
            rows.append(RawReaderRow(key=str(k), label=_raw_label(v), conf=None))
    return tuple(rows)


def _parse_tsv(text: str) -> tuple[RawReaderRow, ...]:
    """TSV read: the LAST contiguous block of tab-separated `key⇥label[⇥conf]` rows — the same
    last-answer-block discipline as the JSON scans, so reasoning / fences / an earlier draft before
    the final block are skipped. Duplicate keys are preserved (`DUP_KEY` fires at resolution); a
    non-numeric or out-of-range conf is `None`, never a fabricated default."""
    best: list[RawReaderRow] = []
    block: list[RawReaderRow] = []
    for line in text.splitlines():
        fields = [f.strip() for f in line.split("\t")]
        if len(fields) >= 2 and fields[0]:
            block.append(RawReaderRow(key=fields[0], label=_raw_label(fields[1]),
                                      conf=_conf(_number(fields[2])) if len(fields) > 2 else None))
        elif block:
            best, block = block, []
    return tuple(block or best)


def _raw_label(v: object) -> str:
    """A label is text; a non-string model value (number, object, null) is NO label — coerced to ''
    so it surfaces as `BAD_LABEL` at resolution and never violates `RawReaderRow.label: str`. The
    ONE raw-label coercion every contract's parse goes through, so the paths behave identically."""
    return v if isinstance(v, str) else ""


def _conf(v: object) -> float | None:
    """A reader's self-reported confidence: a finite probability, or `None`. Never a fabricated
    default; an out-of-range or NaN value is dropped to `None` rather than persisted."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    c = float(v)
    return c if math.isfinite(c) and 0.0 <= c <= 1.0 else None


def _number(s: str) -> float | None:
    """A TSV field as a number, or `None` — `_conf` range-checks the rest."""
    try:
        return float(s)
    except ValueError:
        return None


def _json_object_pairs(text: str) -> list[tuple[str, object]]:
    """The (key, value) PAIRS of the last balanced JSON object in a reply, DUPLICATES PRESERVED.
    Scans brace depth (tolerating ``` fences / leading prose / a trailing stray `}`) and keeps the
    last object that parses — an unmatched OPENING `{` in reasoning still defeats it, same as
    `_json_array`. Uses `object_pairs_hook` so a key repeated in the reply yields two pairs — raw
    `json.loads` would silently keep only the last, hiding the conflict the resolver must fault as
    `DUP_KEY`."""
    best: list[tuple[str, object]] = []
    depth = start = 0
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0:
                try:
                    pairs = json.loads(text[start:i + 1], object_pairs_hook=list)
                except json.JSONDecodeError:
                    continue
                if isinstance(pairs, list):     # the TOP-LEVEL object is the pair list
                    best = [(str(k), v) for k, v in pairs]
    return best


def _json_array(text: str) -> list:
    """The LAST balanced JSON array of `key`-bearing objects in a model reply. Scans bracket depth
    (so a stray `[` in reasoning can't truncate it) and ignores ``` fences and leading arrays."""
    best: list = []
    depth = start = 0
    for i, ch in enumerate(text):
        if ch == "[":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "]" and depth > 0:
            depth -= 1
            if depth == 0:
                try:
                    data = json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    continue
                if isinstance(data, list) and any(isinstance(o, dict) and "key" in o for o in data):
                    best = data
    return best


def _no_schema(keys: Sequence[ListingKey]) -> ResponseFormat | None:  # noqa: ARG001  (keys: protocol)
    """A SCHEMALESS contract's CONSTRAIN behavior: no `response_format` — the instruction is the sole
    constraint and the parser reads the prose back (e.g. TSV). The named twin of the JSON schemas; it
    takes `keys` only to match the `ContractSpec.schema` signature."""
    return None


_JSON_ARRAY = ContractSpec(ResponseContract.JSON_ARRAY, _JSON_ARRAY_INSTRUCTION,
                           _json_array_schema, _parse_json_array)
_JSON_KEYED = ContractSpec(ResponseContract.JSON_KEYED, _JSON_KEYED_INSTRUCTION,
                           _json_keyed_schema, _parse_json_keyed)
_TSV = ContractSpec(ResponseContract.TSV, _TSV_INSTRUCTION, _no_schema, _parse_tsv)
