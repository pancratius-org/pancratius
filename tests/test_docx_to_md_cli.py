from __future__ import annotations

import argparse
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import docx_to_md  # noqa: E402


def parse_cli(*argv: str) -> argparse.Namespace:
    parser = docx_to_md.build_parser()
    args = parser.parse_args(list(argv))
    docx_to_md.validate_args(parser, args)
    return args


def test_batch_selection_uses_repeated_kinds() -> None:
    args = parse_cli("--kind", "book", "--kind", "poem", "--kind", "book")

    assert args.kind == ["book", "poem"]
    assert args.number is None
    assert args.test is False


def test_single_book_selection_uses_kind_and_number() -> None:
    args = parse_cli("--kind", "book", "--number", "33")

    assert args.kind == ["book"]
    assert args.number == 33


@pytest.mark.parametrize(
    "argv",
    [
        (),
        ("book", "33"),
        ("--kind", "book", "--kind", "poem", "--number", "33"),
        ("--kind", "project", "--number", "1"),
        ("--kind", "book", "--slug", "enlightened-ai"),
        ("--test", "--kind", "book"),
    ],
)
def test_rejects_ambiguous_or_removed_shapes(argv: tuple[str, ...]) -> None:
    with pytest.raises(SystemExit):
        parse_cli(*argv)
