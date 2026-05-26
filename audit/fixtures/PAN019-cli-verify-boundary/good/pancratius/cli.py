"""Fixture (PAN019-cli-verify-boundary / good): the door registers only MUTATE
groups (work, data) — no `audit`/`site` sub-parser. The audit must stay silent."""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pancratius")
    sub = parser.add_subparsers(dest="group")
    sub.add_parser("work", help="Import corpus works.")
    sub.add_parser("data", help="Generate corpus data products.")
    return parser
