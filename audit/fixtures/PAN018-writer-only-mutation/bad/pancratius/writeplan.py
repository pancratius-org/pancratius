# import-pure: no filesystem mutation
"""Fixture (PAN018-writer-only-mutation / bad): a module that DECLARES the
import-pure marker but then writes to the filesystem with `.write_text` — the
exact boundary leak the rule forbids (a pure import module must not mutate
src/content; only the writer may). The audit must fire."""

from __future__ import annotations

from pathlib import Path


def build_ops(body: str, target: Path) -> None:
    # BOUNDARY LEAK: a marked-pure module must not touch the filesystem.
    target.write_text(body, encoding="utf-8")
