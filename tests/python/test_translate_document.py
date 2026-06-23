"""The load-bearing guarantee of the translation segmenter: parsing a generated
Markdown body and re-rendering it under the identity mapping reproduces the body
byte-for-byte. Proven on hand-built fixtures *and* on every committed book/poem
body, so a real-world wrapper shape cannot silently break reconstruction.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pancratius.content_catalog import split_frontmatter
from pancratius.translation.text.document import Slot, UnitKind, parse_document

ROOT = Path(__file__).resolve().parents[2]
CONTENT = ROOT / "src" / "content"


def _bodies() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for kind in ("books", "poetry"):
        for md in sorted((CONTENT / kind).glob("*/*.md")):
            _fm, body = split_frontmatter(md.read_text(encoding="utf-8"))
            out.append((f"{kind}/{md.parent.name}/{md.name}", body))
    return out


def test_roundtrip_identity_on_fixtures() -> None:
    samples = [
        "",
        "Плоская проза.\n",
        "Без завершающего перевода строки.",
        "# Заголовок\n\nАбзац.\n",
        '<div class="lineated">\n\nПервая строка  \nВторая строка\n\n</div>\n',
        '<div class="lineated verse">\n\nСтих один  \nСтих два\n\n</div>\n',
        '<blockquote class="scripture">\n\nИбо так возлюбил.\n\n</blockquote>\n',
        "> Цитата в выноске.\n>\n> Второй абзац выноски.\n",
        "- Первый пункт\n- Второй пункт\n",
        "***\n",
        "![подпись](./images/x.jpg)\n",
        '<p class="signature">Подпись</p>\n',
        "**Жирный заголовок строки**\n\n*Курсивная реплика.*\n",
    ]
    for body in samples:
        assert parse_document(body).render({}) == body


@pytest.mark.parametrize("name,body", _bodies(), ids=lambda v: v if isinstance(v, str) else "")
def test_roundtrip_identity_on_corpus(name: str, body: str) -> None:
    assert parse_document(body).render({}) == body, name


def test_slots_translate_without_touching_structure() -> None:
    body = '<div class="lineated verse">\n\nСвет  \nТьма\n\n</div>\n'
    doc = parse_document(body)
    assert [u.kind for u in doc.units] == [UnitKind.VERSE, UnitKind.VERSE]
    rendered = doc.render({u.id: "X" for u in doc.units})
    # Both verse lines keep their two-space hard break; only the words change.
    assert rendered == '<div class="lineated verse">\n\nX  \nX\n\n</div>\n'


def test_heading_and_list_kinds() -> None:
    doc = parse_document("## Глава\n\n- пункт\n1. первый\n")
    kinds = {u.source: u.kind for u in doc.units}
    assert kinds["Глава"] is UnitKind.HEADING
    assert kinds["пункт"] is UnitKind.LIST_ITEM
    assert kinds["первый"] is UnitKind.LIST_ITEM


def test_structural_lines_are_not_units() -> None:
    doc = parse_document("***\n\n![](./a.jpg)\n")
    assert doc.units == ()
    assert not any(isinstance(p, Slot) for p in doc.pieces)


def test_image_alt_is_translatable_but_path_is_not() -> None:
    body = "![Колодец Иакова](./images/9e826b65d882.jpg)\n"
    doc = parse_document(body)
    assert [u.source for u in doc.units] == ["Колодец Иакова"]
    assert doc.units[0].kind is UnitKind.IMAGE_ALT
    rendered = doc.render({doc.units[0].id: "Jacob's Well"})
    assert rendered == "![Jacob's Well](./images/9e826b65d882.jpg)\n"
