"""Fixture (PAN017-import-work-kinds / good): the `pancratius` CLI door DEFERS
``--kind`` to the importer entry — it declares no ``--kind`` of its own (it would
reuse ``import_docx.add_import_arguments``), so the book|poem boundary is owned in
one place. The audit must stay silent on the door."""

from __future__ import annotations

import argparse


def add_project_args(ap: argparse.ArgumentParser) -> None:
    # A non-kind flag; the door declares NO `--kind` of its own.
    ap.add_argument("--lang")
