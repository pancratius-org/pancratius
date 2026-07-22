from __future__ import annotations

import shutil
from dataclasses import dataclass


class PandocNotFoundError(RuntimeError):
    pass


@dataclass(frozen=True)
class PandocExecutable:
    argv0: str


def find_pandoc() -> PandocExecutable | None:
    if path := shutil.which("pandoc"):
        return PandocExecutable(path)
    return None


def pandoc_argv0() -> str:
    executable = find_pandoc()
    if executable is None:
        raise PandocNotFoundError("pandoc not found on PATH; run `mise install pandoc`.")
    return executable.argv0
