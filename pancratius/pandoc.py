from __future__ import annotations

import shutil
from dataclasses import dataclass
from importlib import import_module
from typing import Literal, Protocol, cast

PandocProvider = Literal["path", "pypandoc-binary"]


class PandocNotFoundError(RuntimeError):
    pass


class _PypandocModule(Protocol):
    def get_pandoc_path(self) -> str: ...


@dataclass(frozen=True)
class PandocExecutable:
    argv0: str
    provider: PandocProvider


def find_pandoc() -> PandocExecutable | None:
    if path := shutil.which("pandoc"):
        return PandocExecutable(path, "path")

    try:
        pypandoc = cast(_PypandocModule, import_module("pypandoc"))
    except ModuleNotFoundError:
        return None

    try:
        path = pypandoc.get_pandoc_path()
    except (OSError, RuntimeError):
        return None
    if not path:
        return None
    return PandocExecutable(path, "pypandoc-binary")


def pandoc_argv0() -> str:
    executable = find_pandoc()
    if executable is None:
        raise PandocNotFoundError(
            "pandoc not found; run `uv sync` or install it with `brew install pandoc`."
        )
    return executable.argv0
