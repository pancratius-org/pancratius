"""Namespace canonicalization for pandoc's image reader.

Some source DOCX bind the correct OOXML drawing URIs to GENERIC prefixes
(`ns3:`/`ns5:`/`ns7:` …); pandoc 3.x then resolves no images and drops every one. The adapter
re-prefixes such a doc before pandoc reads it — changing prefixes only, never URIs, so the
recovered images are real and no text is lost. A conventionally-prefixed DOCX is passed through
untouched (the 98-of-103 common case stays byte-identical, protecting the goldens).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import cast

import pytest

from pancratius import docx_adapter as da

pandoc_required = pytest.mark.skipif(
    shutil.which("pandoc") is None,
    reason="pandoc is required for importer-backed DOCX paths",
)

_REPO = Path(__file__).resolve().parents[2]
# A conventionally-prefixed book (images already work) and a generic-prefix book (images dropped
# by pandoc until canonicalized) — both small, both committed.
_CONVENTIONAL = _REPO / "src/content/books/62-kniga-tishiny/ru.docx"
_GENERIC = _REPO / "src/content/books/27-mikki-17/ru.docx"
# Generic-prefix books spanning the recovery surface: book27 (AlternateContent + 1 image),
# book33 (39 images, no AlternateContent) — additivity must hold across both shapes.
_GENERIC_RICH = (_GENERIC, _REPO / "src/content/books/33-ya-esm-vsadnik-kon-i-mech/ru.docx")


def test_conventional_docx_is_passed_through_unchanged(tmp_path: Path) -> None:
    assert da._canonical_pandoc_input(_CONVENTIONAL, tmp_path) == _CONVENTIONAL


def test_generic_prefix_docx_is_rewritten_to_a_temp_copy(tmp_path: Path) -> None:
    out = da._canonical_pandoc_input(_GENERIC, tmp_path)
    assert out != _GENERIC
    assert out.suffix == ".docx" and tmp_path in out.parents


def _image_count(ast: dict) -> int:
    seen = [0]

    def walk(x: object) -> None:
        if isinstance(x, dict):
            node = cast("dict[str, object]", x)
            if node.get("t") == "Image":
                seen[0] += 1
            for v in node.values():
                walk(v)
        elif isinstance(x, list):
            for v in cast("list[object]", x):
                walk(v)

    walk(ast.get("blocks", []))
    return seen[0]


@pandoc_required
def test_pandoc_recovers_images_from_a_generic_prefix_docx(tmp_path: Path) -> None:
    ast, _ = da.run_pandoc_json(_GENERIC, tmp_path)
    assert _image_count(ast) >= 1, "the generic-prefix book yields no images without the fix"


def _body_words(docx: Path, media: Path) -> list[str]:
    from pancratius import ir
    from pancratius.ir.normalize import inline_plain

    doc = da.adapt(docx, media)
    text = " ".join(inline_plain(b.inlines) for b in doc.blocks
                    if isinstance(b, ir.Paragraph) and b.inlines)
    return text.split()


@pandoc_required
@pytest.mark.parametrize("docx", _GENERIC_RICH, ids=lambda p: p.parent.name[:6])
def test_canonicalization_loses_no_body_text(docx: Path, tmp_path: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    # Real comparison: import WITHOUT the rewrite (force passthrough) vs WITH it. The rewrite
    # is strictly ADDITIVE — it recovers images and the body words pandoc dropped alongside them
    # (e.g. textbox text), and removes nothing. So every passthrough word survives in the
    # rewritten import; a regression that corrupted existing text would drop one here.
    from collections import Counter

    rewritten = _body_words(docx, tmp_path / "with")

    monkeypatch.setattr(da, "_canonical_pandoc_input", lambda d, _work_dir: d)
    passthrough = _body_words(docx, tmp_path / "without")

    assert len(passthrough) > 1000                       # both sides have real content
    assert Counter(passthrough) - Counter(rewritten) == Counter()   # nothing lost
    assert len(rewritten) > len(passthrough)             # the recovered words are present
