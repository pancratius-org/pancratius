# research-pure: locates the corpus DOCX under src/content (read-only).
"""Corpus source discovery: the book/poetry DOCX paths everything else iterates.

The per-paragraph feature record that used to live here (`FeatureRow`, with its
"NOISE / negative controls" framing for spacing/indent/justification/style) has been
retired — the 75-book probe falsified that prior, and the feature contract now lives
in `rows.py` (the two-layer `Row`: meta / features / label). This module is just the
corpus-directory helper its many importers actually use.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
CONTENT = ROOT / "src" / "content"


def book_dirs() -> list[tuple[int, Path]]:
    out: list[tuple[int, Path]] = []
    for d in sorted((CONTENT / "books").glob("[0-9]*-*")):
        m = re.match(r"(\d+)-", d.name)
        if m and (d / "ru.docx").is_file():
            out.append((int(m.group(1)), d / "ru.docx"))
    return out


def poetry_dirs() -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    for d in sorted((CONTENT / "poetry").glob("[0-9]*-*")):
        if (d / "ru.docx").is_file():
            out.append((d.name, d / "ru.docx"))
    return out
