# import-pure: no filesystem mutation
"""Fixture (PAN018-writer-only-mutation / good): a module that DECLARES the
import-pure marker and contains no filesystem mutation — it only builds and
returns a plain value. The audit must stay silent."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WriteOp:
    rel_path: str
    content: str


def build_ops(body: str) -> list[WriteOp]:
    # Pure: assembles WriteOps. No write_text / mkdir / copy / open-for-write.
    return [WriteOp(rel_path="books/99-probe/ru.md", content=body)]
