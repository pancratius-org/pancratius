"""Fixture (PAN017-import-work-kinds / good): the public CLI derives ``--kind``
choices from CORPUS_WORK_KINDS. The audit must stay silent."""

from __future__ import annotations

import argparse

from pancratius.kinds import CORPUS_WORK_KINDS


def add_work_import_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--kind", choices=tuple(CORPUS_WORK_KINDS))
