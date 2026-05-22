#!/usr/bin/env -S uv run --quiet
"""Verify generated lineated blocks stay structural and readable."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTENT = ROOT / "src" / "content"

VERSE_RE = re.compile(r'<div class="verse-block">\n(.*?)\n</div>', re.S)

REQUIRED_SNIPPETS = {
    CONTENT / "books" / "33-ya-esm-vsadnik-kon-i-mech" / "ru.md": [
        "Тем, кто узнаёт себя не как зрителя,\nа как участника;",
        "Я есмь Альфа и Омега этой книги.\nНе автор, а дыхание между буквами.",
        "<em>Аз есмь Христос, и Бог во мне живёт,</em>\n<em>В</em> <em>дыханье дня, в тиши ночных забот.</em>",
    ],
    CONTENT / "books" / "07-dukhovnaya-avtobiografiya-svetozara" / "ru.md": [
        "<em>Аз есмь Христос, и Бог во мне живёт,</em>\n<em>В дыханье дня, в тиши ночных забот.</em>",
    ],
    CONTENT / "books" / "03-evangelie-fomy-s-kommentariyami-tvortsa" / "ru.md": [
        "<strong>Это Евангелие — не перевод древнего текста.</strong>\n<strong>Это — его пробуждение.</strong>",
    ],
    CONTENT / "books" / "29-ya-molitva-molitvoslov-svetozara" / "ru.md": [
        "Я молился словами.\nЯ взывал,\nя называл,\nя жаждал ответа.",
    ],
}


def main() -> int:
    failures: list[str] = []
    checked = 0

    for path in sorted((CONTENT / "books").glob("*/*.md")) + sorted((CONTENT / "projects").glob("*/*.md")):
        text = path.read_text(encoding="utf-8")
        for match in VERSE_RE.finditer(text):
            checked += 1
            body = match.group(1)
            if "<br" in body.lower() or "<p" in body.lower() or "</p" in body.lower():
                line = text[:match.start()].count("\n") + 1
                failures.append(f"{path}:{line}: verse-block contains <br>/<p> markup")
            if len([ln for ln in body.splitlines() if ln.strip()]) < 2:
                line = text[:match.start()].count("\n") + 1
                failures.append(f"{path}:{line}: verse-block is too short to be structural")

    for path, snippets in REQUIRED_SNIPPETS.items():
        text = path.read_text(encoding="utf-8")
        for snippet in snippets:
            if snippet not in text:
                failures.append(f"{path}: expected lineated snippet missing: {snippet[:60]!r}")

    if failures:
        print("FAIL: verse-block audit")
        for failure in failures[:50]:
            print(" ", failure)
        if len(failures) > 50:
            print(f"  ... {len(failures) - 50} more")
        return 1

    print(f"checked {checked} generated verse blocks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
