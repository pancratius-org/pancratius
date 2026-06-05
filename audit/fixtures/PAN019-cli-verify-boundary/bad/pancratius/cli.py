"""Fixture (PAN019-cli-verify-boundary / bad): the door grows a `site` proxy group
with an `audit` verb that shells to `npm run audit:repo`, plus a top-level `check` verb —
inverting the mutate/verify cut at the grammar level (the rejected `site`-proxy
alternative, and a build/verify verb under the mutate door). The audit must fire."""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pancratius")
    sub = parser.add_subparsers(dest="group")
    sub.add_parser("work", help="Import corpus works.")
    sub.add_parser("check", help="Type-check the site (FORBIDDEN — belongs to npm).")
    site = sub.add_parser("site", help="Site proxy (FORBIDDEN — verify lives in npm).")
    site_sub = site.add_subparsers(dest="verb")
    site_sub.add_parser("audit", help="Proxy to npm run audit:repo (FORBIDDEN).")
    return parser
