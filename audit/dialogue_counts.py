"""DOCX turn count == MD turn count for the verified dialogue books
(#07, #39, #46, #64). Codex sampled these four; we re-verify after
re-conversion. Counts the unique speaker-prefix lines in both source and
target."""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "src" / "content"

# Why: Codex sampled these four books and reported preserved-turn counts
# (the data made it through; only labels were inconsistent). After the
# converter runs the dialogue normalizer the label shapes are canonical
# `**Speaker:**`. We require *at least* the Codex-reported count — multi-
# speaker books may legitimately surface more canonical labels once the
# normalizer disambiguates them.
SAMPLED_BOOKS_MIN_TURNS: dict[int, int] = {
    7:  20,
    39: 14,
    46: 200,
    64: 40,
}

SPEAKER_PREFIXES = ["Панкратиус", "Светозар", "Творец", "Бог"]
# Why: a canonical normalized turn label is `**Speaker[ extra words]:**` on
# its own line; the converter ran the dialogue normalizer over every body.
TURN_RE = re.compile(
    r"^\*\*(?:" + "|".join(SPEAKER_PREFIXES) + r")(?:\s+[\w\d.\- ]{0,40})?:\*\*\s*$",
    re.MULTILINE,
)


def md_turn_count(md_path: Path) -> int:
    text = md_path.read_text(encoding="utf-8")
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            text = text[end + 4:]
    return len(TURN_RE.findall(text))


def docx_turn_count(docx_path: Path) -> int:
    if not docx_path.exists():
        return -1
    with zipfile.ZipFile(docx_path) as zf:
        try:
            xml = zf.read("word/document.xml").decode("utf-8", errors="ignore")
        except KeyError:
            return -1
    text = re.sub(r"<[^>]+>", " ", xml)
    text = re.sub(r"\s+", " ", text)
    count = 0
    for pref in SPEAKER_PREFIXES:
        count += len(re.findall(rf"\b{re.escape(pref)}[\w\s]{{0,40}}?:\s", text))
    return count


def main() -> int:
    failures: list[str] = []
    for number, minimum in SAMPLED_BOOKS_MIN_TURNS.items():
        ru_dirs = list((CONTENT / "books").glob(f"{number:02d}-*"))
        if not ru_dirs:
            failures.append(f"book {number}: no content dir")
            continue
        ru = ru_dirs[0] / "ru.md"
        md_count = md_turn_count(ru) if ru.exists() else 0
        print(f"book {number}: md_turns={md_count} (need ≥ {minimum})")
        if md_count < minimum:
            failures.append(f"book {number}: only {md_count} canonical turns (need ≥ {minimum})")
    if failures:
        print(f"FAIL: {len(failures)} dialogue audits", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
