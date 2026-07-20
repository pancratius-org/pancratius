"""Plain-text shingle coverage from the canonical source model to generated Markdown.

This catches loss or duplication after ``docx_source.read`` and drift in committed
output. It is not an independent OOXML reader and cannot prove that the canonical
reader itself retained every source atom. It intentionally strips generated chrome
(TOC, bibliography, copyright/contact tail) because those regions are not part of
the canonical Markdown body.
"""
from __future__ import annotations

import os
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

from pancratius import docx_source

ROOT = Path(os.environ.get("PANCRATIUS_AUDIT_ROOT", Path(__file__).resolve().parents[1]))
CONTENT = ROOT / "src" / "content"
SHINGLE_K = 8
COVERAGE_MIN = 0.85
MARKDOWN_DUPLICATE_MIN_WORDS = 20
DUPLICATE_PASSAGE_K = 20
DUPLICATE_PASSAGE_EXTRA_SHINGLES_MIN = 5
BODY_TAIL_MARKERS = {
    "библиография",
    "bibliography",
    "копирайт",
    "copyright",
    "контакты",
    "contacts",
}
TOC_TITLES = {"оглавление", "contents", "table of contents"}


def _strip_yaml(md: str) -> str:
    if md.startswith("---"):
        end = md.find("\n---", 3)
        if end > 0:
            return md[end + 4:]
    return md


def _simple(text: str) -> str:
    text = text.strip().lower()
    text = text.replace("ё", "е")
    text = re.sub(r"[^\w\sа-яА-Я]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _looks_like_toc_entry(text: str) -> bool:
    stripped = text.strip()
    return bool(stripped and re.search(r"\d+$", stripped))


def _strip_docx_body_chrome(paragraphs: list[str]) -> list[str]:
    rows = list(paragraphs)
    while rows and not rows[0].strip():
        rows.pop(0)
    if rows and _simple(rows[0]) in TOC_TITLES:
        i = 1
        while i < len(rows):
            text = rows[i].strip()
            if not text or _looks_like_toc_entry(text):
                i += 1
                continue
            break
        rows = rows[i:]

    for i, text in enumerate(rows):
        if _simple(text) in BODY_TAIL_MARKERS:
            return rows[:i]
    return rows


def _docx_body_text(docx: Path) -> str:
    source = docx_source.read(docx)
    rows = _strip_docx_body_chrome(
        list(source.body_readings)
    )
    rows.extend(source.note_readings)
    return " ".join(rows)


def _strip_md_body_chrome(md: str) -> str:
    rows = _strip_yaml(md).splitlines()
    for i, line in enumerate(rows):
        if _simple(line) in BODY_TAIL_MARKERS:
            return "\n".join(rows[:i])
    return "\n".join(rows)


def _normalize(text: str) -> str:
    # Compare rendered math, not its storage spelling. Word stores Greek/math
    # glyphs while imported Markdown spells the same notation as TeX commands.
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(
        r"\\(?:mathfrak|mathbf|mathrm|mathit|operatorname|text)\s*\{([^{}]*)\}",
        r" \1 ",
        text,
    )
    text = re.sub(
        r"\\(?:frac|sqrt|cdot|times|approx|to|rightarrow|left|right|begin|end|nabla)\b",
        " ",
        text,
    )
    text = "".join(_greek_letter_name(character) or character for character in text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"!\[[^\]]*]\([^)]+\)", " ", text)
    # Word list labels live in numbering metadata; Markdown materializes them.
    text = re.sub(r"(?m)^\s*\d+[.)]\s+", " ", text)
    text = re.sub(r"[^\w\sа-яёА-ЯЁ]", " ", text, flags=re.UNICODE)
    text = text.lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _greek_letter_name(character: str) -> str:
    name = unicodedata.name(character, "")
    if not name.startswith("GREEK ") or " LETTER " not in name:
        return ""
    return name.split(" LETTER ", 1)[1].split(" WITH ", 1)[0].lower()


def _shingles(text: str, k: int) -> set[str]:
    words = text.split()
    if len(words) < k:
        return set()
    return {" ".join(words[i:i + k]) for i in range(len(words) - k + 1)}


def _shingle_counts(text: str, k: int) -> Counter[str]:
    words = text.split()
    if len(words) < k:
        return Counter()
    return Counter(" ".join(words[i:i + k]) for i in range(len(words) - k + 1))


def _duplicate_word_half(text: str) -> bool:
    words = text.split()
    if len(words) < MARKDOWN_DUPLICATE_MIN_WORDS or len(words) % 2 != 0:
        return False
    mid = len(words) // 2
    return words[:mid] == words[mid:]


def _duplicated_passage(md_text: str, docx_text: str) -> str | None:
    md_counts = _shingle_counts(md_text, DUPLICATE_PASSAGE_K)
    if not md_counts:
        return None
    docx_counts = _shingle_counts(docx_text, DUPLICATE_PASSAGE_K)
    surplus = [
        shingle
        for shingle, count in md_counts.items()
        if (source_count := docx_counts.get(shingle, 0)) > 0 and count > source_count
    ]
    if len(surplus) < DUPLICATE_PASSAGE_EXTRA_SHINGLES_MIN:
        return None
    return surplus[0]


def _docx_paths() -> list[Path]:
    return sorted((CONTENT / "books").glob("*/*.docx"))


def main() -> int:
    failures: list[str] = []
    checked = 0
    lowest: tuple[float, Path] | None = None
    for docx_path in _docx_paths():
        md_path = docx_path.with_suffix(".md")
        if not md_path.exists():
            failures.append(f"{docx_path}: missing sibling Markdown {md_path.name}")
            continue
        md_norm = _normalize(_strip_md_body_chrome(md_path.read_text(encoding="utf-8")))
        docx_norm = _normalize(_docx_body_text(docx_path))
        md_shingles = _shingles(md_norm, SHINGLE_K)
        docx_shingles = _shingles(docx_norm, SHINGLE_K)
        if not docx_shingles:
            failures.append(f"{docx_path}: source DOCX body is empty")
            continue
        checked += 1
        coverage = len(md_shingles & docx_shingles) / len(docx_shingles)
        if lowest is None or coverage < lowest[0]:
            lowest = (coverage, docx_path)
        if coverage < COVERAGE_MIN:
            failures.append(
                f"{docx_path}: shingle coverage {coverage:.3f} < {COVERAGE_MIN}"
            )
        if _duplicate_word_half(md_norm):
            failures.append(f"{md_path}: Markdown body text appears duplicated exactly")
        elif duplicate := _duplicated_passage(md_norm, docx_norm):
            failures.append(
                f"{md_path}: Markdown repeats a source passage more often than DOCX: "
                f"{duplicate!r}"
            )
    if failures:
        print(f"FAIL: {len(failures)} source-text fidelity issue(s)", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    if lowest is None:
        print("checked 0 book DOCX/Markdown pairs")
    else:
        print(
            f"checked {checked} book DOCX/Markdown pair(s); "
            f"lowest coverage {lowest[0]:.3f} ({lowest[1]})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
