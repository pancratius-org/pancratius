from __future__ import annotations

from types import ModuleType

import pytest

from pancratius import pandoc


class _FakePypandoc(ModuleType):
    def get_pandoc_path(self) -> str:
        return "/vendor/pandoc"


def test_find_pandoc_prefers_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pandoc.shutil, "which", lambda _tool: "/usr/bin/pandoc")

    assert pandoc.find_pandoc() == pandoc.PandocExecutable("/usr/bin/pandoc", "path")


def test_find_pandoc_falls_back_to_packaged_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pandoc.shutil, "which", lambda _tool: None)
    monkeypatch.setattr(pandoc, "import_module", lambda _name: _FakePypandoc("pypandoc"))

    assert pandoc.find_pandoc() == pandoc.PandocExecutable("/vendor/pandoc", "pypandoc-binary")


def test_find_pandoc_returns_none_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(_name: str) -> ModuleType:
        raise ModuleNotFoundError(_name)

    monkeypatch.setattr(pandoc.shutil, "which", lambda _tool: None)
    monkeypatch.setattr(pandoc, "import_module", missing)

    assert pandoc.find_pandoc() is None
