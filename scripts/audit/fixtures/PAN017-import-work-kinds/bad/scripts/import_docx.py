"""Fixture (PAN017-import-work-kinds / bad): the import CLI hardcodes its --kind
choices to a literal that RE-ADMITS `project` as an importable kind — drifting
from the WORK_KINDS source of truth (book/poem). This is exactly the retired-
capability regression PAN015/PAN017 forbid; the audit must fire."""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser()
    ap.add_argument("docx")
    ap.add_argument("--kind", choices=("book", "poem", "project"), help="Pick a kind.")
    return ap
