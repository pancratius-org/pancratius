"""Tag-glossary loading: the RU-key → EN-label map that keeps one concept one tag."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pancratius.translate.profile import load_tag_labels


def test_reads_the_en_block(tmp_path: Path) -> None:
    g = tmp_path / "tag-glossary.json"
    g.write_text(
        json.dumps({"ru": {"Библия": "Библия"}, "en": {"Библия": "The Bible", "молитва": "Prayer"}}),
        encoding="utf-8",
    )
    assert load_tag_labels(g) == {"Библия": "The Bible", "молитва": "Prayer"}


def test_rejects_a_glossary_without_an_en_block(tmp_path: Path) -> None:
    g = tmp_path / "tag-glossary.json"
    g.write_text(json.dumps({"ru": {"Библия": "Библия"}}), encoding="utf-8")
    with pytest.raises(ValueError, match="en"):
        load_tag_labels(g)


def test_drops_non_string_labels(tmp_path: Path) -> None:
    g = tmp_path / "tag-glossary.json"
    g.write_text(json.dumps({"en": {"Библия": "The Bible", "x": 5, "y": None}}), encoding="utf-8")
    assert load_tag_labels(g) == {"Библия": "The Bible"}
