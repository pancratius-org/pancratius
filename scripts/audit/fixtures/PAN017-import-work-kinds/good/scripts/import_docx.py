"""Fixture (PAN017-import-work-kinds / good): the import CLI derives its --kind
choices from the WORK_KINDS source of truth — `choices=WORK_KINDS`, imported
from lib.kinds. The audit must stay silent."""

from __future__ import annotations

import argparse

from lib.kinds import WORK_KINDS


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser()
    ap.add_argument("docx")
    ap.add_argument("--kind", choices=WORK_KINDS, help="Required for a new work.")
    return ap
