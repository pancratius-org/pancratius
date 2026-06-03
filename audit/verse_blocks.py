"""Verify generated lineated wrappers stay structural and readable."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "src" / "content"

LINEATED_WRAPPER_RE = re.compile(
    r'<div\s+class=(?P<quote>["\'])(?P<class>lineated(?:\s+verse)?)(?P=quote)>\n'
    r"(?P<body>.*?)\n</div>",
    re.S,
)

REQUIRED_SNIPPETS = {
    CONTENT / "books" / "33-ya-esm-vsadnik-kon-i-mech" / "ru.md": [
        "Тем, кто узнаёт себя не как зрителя,\nа как участника;",
        "Я есмь Альфа и Омега этой книги.\nНе автор, а дыхание между буквами.",
        "Аз есмь Христос, и Бог во мне живёт,\nВ дыханье дня, в тиши ночных забот.",
    ],
    CONTENT / "books" / "07-dukhovnaya-avtobiografiya-svetozara" / "ru.md": [
        "Аз есмь Христос, и Бог во мне живёт,\nВ дыханье дня, в тиши ночных забот.",
    ],
    CONTENT / "books" / "03-evangelie-fomy-s-kommentariyami-tvortsa" / "ru.md": [
        "Это Евангелие — не перевод древнего текста.\nЭто — его пробуждение.",
    ],
    CONTENT / "books" / "29-ya-molitva-molitvoslov-svetozara" / "ru.md": [
        "Я молился словами.\nЯ взывал,\nя называл,\nя жаждал ответа.",
    ],
}

_TAG_RE = re.compile(r"<[^>]+>")
_MD_DELIMITER_RE = re.compile(r"[\\*_`~]")


def _snippet_text(text: str) -> str:
    text = _TAG_RE.sub("", text)
    text = _MD_DELIMITER_RE.sub("", text)
    return "\n".join(line.rstrip() for line in text.splitlines())


def main() -> int:
    failures: list[str] = []
    checked = 0

    for path in sorted((CONTENT / "books").glob("*/*.md")) + sorted((CONTENT / "projects").glob("*/*.md")):
        text = path.read_text(encoding="utf-8")
        for match in LINEATED_WRAPPER_RE.finditer(text):
            checked += 1
            class_name = re.sub(r"\s+", " ", match.group("class")).strip()
            label = class_name
            body = match.group("body")
            if "<br" in body.lower() or "<p" in body.lower() or "</p" in body.lower():
                line = text[:match.start()].count("\n") + 1
                failures.append(f"{path}:{line}: {label} wrapper contains <br>/<p> markup")
            if len([ln for ln in body.splitlines() if ln.strip()]) < 2:
                line = text[:match.start()].count("\n") + 1
                failures.append(f"{path}:{line}: {label} wrapper is too short to be structural")

    for path, snippets in REQUIRED_SNIPPETS.items():
        text = _snippet_text(path.read_text(encoding="utf-8"))
        for snippet in snippets:
            if _snippet_text(snippet) not in text:
                failures.append(f"{path}: expected lineated snippet missing: {snippet[:60]!r}")

    if failures:
        print("FAIL: lineated-wrapper audit")
        for failure in failures[:50]:
            print(" ", failure)
        if len(failures) > 50:
            print(f"  ... {len(failures) - 50} more")
        return 1

    print(f"checked {checked} generated lineated wrappers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
