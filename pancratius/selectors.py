"""Typed resource selectors for the public library CLI.

Selectors are command-surface identities such as ``book:50``. They are not
storage paths and not provider jobs; handlers resolve them into the owning
domain objects.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from pancratius.kinds import CORPUS_WORK_KINDS


class SelectorError(ValueError):
    """A public selector string is not valid for the requested command."""


@dataclass(frozen=True, slots=True)
class BookSelector:
    number: int

    @property
    def kind(self) -> Literal["book"]:
        return "book"

    def __str__(self) -> str:
        return f"book:{self.number}"


@dataclass(frozen=True, slots=True)
class PoemSelector:
    number: int

    @property
    def kind(self) -> Literal["poem"]:
        return "poem"

    def __str__(self) -> str:
        return f"poem:{self.number}"


type WorkSelector = BookSelector | PoemSelector

_WORK_KINDS = frozenset(CORPUS_WORK_KINDS)


def _parse_number(kind: str, raw_number: str) -> int:
    if not raw_number or not raw_number.isascii() or not raw_number.isdecimal():
        raise SelectorError(f"{kind} selector must be shaped as {kind}:NN")
    number = int(raw_number)
    if number <= 0:
        raise SelectorError(f"{kind} selector number must be positive")
    return number


def parse_work_selector(raw: str, *, allowed: Iterable[str] = _WORK_KINDS) -> WorkSelector:
    """Parse a corpus work selector, preserving the typed work kind."""
    allowed_kinds = frozenset(allowed)
    kind, sep, raw_number = raw.partition(":")
    if not sep:
        expected = "|".join(f"{k}:NN" for k in sorted(allowed_kinds))
        raise SelectorError(f"expected selector {expected}, got {raw!r}")
    if kind not in _WORK_KINDS:
        raise SelectorError(f"unsupported selector kind {kind!r}; expected book or poem")
    if kind not in allowed_kinds:
        expected = ", ".join(sorted(allowed_kinds))
        raise SelectorError(f"{kind}:{raw_number} is not valid here; expected {expected}")
    number = _parse_number(kind, raw_number)
    if kind == "book":
        return BookSelector(number)
    if kind == "poem":
        return PoemSelector(number)
    raise SelectorError(f"selector kind {kind!r} needs a typed selector class")


def parse_book_selector(raw: str) -> BookSelector:
    selector = parse_work_selector(raw, allowed=("book",))
    if isinstance(selector, BookSelector):
        return selector
    raise AssertionError("parse_work_selector returned a non-book selector")


def dedupe_work_selectors(selectors: Iterable[WorkSelector]) -> tuple[WorkSelector, ...]:
    """Dedupe selectors while preserving the user's order."""
    seen: set[tuple[str, int]] = set()
    deduped: list[WorkSelector] = []
    for selector in selectors:
        key = (selector.kind, selector.number)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(selector)
    return tuple(deduped)
