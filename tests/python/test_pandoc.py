from __future__ import annotations

import pytest

from pancratius import pandoc


def test_find_pandoc_prefers_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pandoc.shutil, "which", lambda _tool: "/usr/bin/pandoc")

    assert pandoc.find_pandoc() == pandoc.PandocExecutable("/usr/bin/pandoc")


def test_find_pandoc_returns_none_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pandoc.shutil, "which", lambda _tool: None)

    assert pandoc.find_pandoc() is None
