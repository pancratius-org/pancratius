from __future__ import annotations

import pytest

from pancratius.selectors import (
    BookSelector,
    PoemSelector,
    SelectorError,
    dedupe_work_selectors,
    parse_book_selector,
    parse_work_selector,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("book:50", BookSelector(50)),
        ("book:050", BookSelector(50)),
        ("poem:1", PoemSelector(1)),
    ],
)
def test_parse_work_selector(raw: str, expected: BookSelector | PoemSelector) -> None:
    assert parse_work_selector(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["50", "book-50", "book:", "book:0", "book:-1", "book:x", "book:１２", "book:١٢"],
)
def test_parse_work_selector_rejects_noncanonical_identity(raw: str) -> None:
    with pytest.raises(SelectorError):
        parse_work_selector(raw)


def test_parse_book_selector_rejects_poem() -> None:
    with pytest.raises(SelectorError, match="not valid here"):
        parse_book_selector("poem:1")


def test_dedupe_work_selectors_preserves_user_order() -> None:
    assert dedupe_work_selectors([
        BookSelector(2),
        BookSelector(2),
        PoemSelector(1),
        BookSelector(1),
    ]) == (BookSelector(2), PoemSelector(1), BookSelector(1))
