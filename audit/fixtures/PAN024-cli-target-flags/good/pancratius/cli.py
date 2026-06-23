from __future__ import annotations

import argparse


def add(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("selectors", nargs="*")
    ap.add_argument("--books-root")
