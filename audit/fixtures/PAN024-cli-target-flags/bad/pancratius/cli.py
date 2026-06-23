from __future__ import annotations

import argparse


def add(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--book", type=int)
    ap.add_argument("--poem", type=int)
    ap.add_argument("--number", type=int)
    ap.add_argument("--into")
