"""Fixture (PAN017-import-work-kinds / bad): the public CLI hardcodes ``--kind``
choices to a literal that re-admits ``project`` as an importable kind."""

from __future__ import annotations

import argparse


def add_work_import_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--kind", choices=("book", "poem", "project"))
