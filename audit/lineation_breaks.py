"""Guard the two-trailing-space hard-break lineation encoding in every lineated
work / page Markdown body.

The corpus encodes LINEATION (where a line breaks) in EVERY lineated body as
CommonMark two-trailing-space hard breaks (content-model.md / decisions.md) — one
uniform encoding, no authored-vs-generated split. The whole scheme dies SILENTLY if
a formatter, editor, or git filter trims trailing whitespace from a `.md` — the
break vanishes, the renderer reflows the line into the next, and nothing else fails.
This audit is the tripwire: it re-derives, structurally, which lines MUST carry the
break and fails if any has lost it.

Lineated surfaces:

  1. BOOK / project `<div class="lineated">` and
     `<div class="lineated verse">` wrappers — every NON-FINAL line of a stanza
     (a stanza is a maximal run of non-blank lines, with `***` its own one-line
     stanza) must end with exactly two trailing spaces.
  2. POEM bodies — whole-body verse (no wrapper). Same rule on the body's stanzas,
     skipping the trailing `[^N]:` footnote appendix (single-line defs, not verse).
  3. VERSE-REGISTER AUTHORED bodies — the mission/manifesto pages (rendered via
     `<Verse>`) and the project subpages whose frontmatter sets `weight: verse`
     (the only weight ProjectSubpagePage maps to `<Verse>`). Same whole-body rule:
     these are lineated verse, so their CSS no longer uses `white-space: pre-line`
     and the `<br>` carries the break — the trailing spaces are load-bearing.

A non-final stanza line that does NOT end in two spaces is a trimmed break (the
failure mode). The final line of a stanza, blank lines, and `***` separators carry
no break and are not flagged. Honours `PANCRATIUS_AUDIT_ROOT`; wrapped as a fatal
core rule in audit/rules/poetry.ts.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path


def _audit_root() -> Path:
    env = os.environ.get("PANCRATIUS_AUDIT_ROOT")
    return Path(env).resolve() if env else Path(__file__).resolve().parents[1]


ROOT = _audit_root()
CONTENT = ROOT / "src" / "content"

LINEATED_WRAPPER_RE = re.compile(
    r'<div\s+class=(?P<quote>["\'])(?P<class>lineated(?:\s+verse)?)(?P=quote)>\n'
    r"(?P<body>.*?)\n</div>",
    re.S,
)
FOOTNOTE_DEF_RE = re.compile(r"^\[\^[0-9A-Za-z._-]+\]:\s")
# A subpage is verse-register iff its frontmatter sets `weight: verse` (the only
# weight ProjectSubpagePage maps to <Verse>); the mission page is the one <Verse>
# page route. Both render their WHOLE body as lineated verse.
WEIGHT_VERSE_RE = re.compile(r"^weight:\s*verse\s*$", re.M)
VERSE_PAGE_SLUGS = ("mission",)
# A line is "lineated verse" (must carry a break unless it closes its stanza) when
# it is non-blank and not a bare `***` separator. Exactly two trailing spaces is the
# break; we flag a non-final stanza line that has lost it (0 trailing spaces) — and
# also a single trailing space, which is a half-trimmed break, not a valid one.
TWO_SPACE_BREAK_RE = re.compile(r"(?<! ) {2}$")

def _check_stanza_lines(lines: list[str]) -> list[int]:
    """Return the indices (into `lines`) of NON-FINAL stanza lines that have lost
    their two-trailing-space break. A stanza is a maximal run of non-blank lines;
    `***` is its own one-line stanza (never a break)."""
    bad: list[int] = []
    n = len(lines)
    for i, line in enumerate(lines):
        if line.strip() == "" or line.strip() == "***":
            continue
        nxt = lines[i + 1] if i + 1 < n else ""
        # This line closes its stanza (next is blank / `***` / end) → no break.
        if nxt.strip() == "" or nxt.strip() == "***":
            continue
        if not TWO_SPACE_BREAK_RE.search(line):
            bad.append(i)
    return bad


def _strip_footnote_appendix(lines: list[str]) -> list[str]:
    for i, line in enumerate(lines):
        if FOOTNOTE_DEF_RE.match(line):
            return lines[:i]
    return lines


def _check_lineated_wrappers(path: Path, text: str, failures: list[str]) -> int:
    checked = 0
    for match in LINEATED_WRAPPER_RE.finditer(text):
        class_name = re.sub(r"\s+", " ", match.group("class")).strip()
        checked += 1
        label = f"{class_name} wrapper"
        inner = match.group("body")
        lines = inner.split("\n")
        for idx in _check_stanza_lines(lines):
            line_no = text[: match.start()].count("\n") + 1 + idx + 1
            failures.append(
                f"{path}:{line_no}: {label} line lost its two-space hard break: "
                f"{lines[idx]!r}"
            )
    return checked


def _split_body(text: str) -> str:
    if not text.startswith("---\n"):
        return text
    end = text.index("\n---\n", 3) + len("\n---\n")
    return text[end:]


def _check_whole_body(path: Path, text: str, label: str, failures: list[str]) -> None:
    """Check a whole-body lineated verse register (poems, mission page, verse
    subpages): every non-final stanza line in the body must carry the break."""
    body = _split_body(text)
    lines = _strip_footnote_appendix(body.split("\n"))
    for idx in _check_stanza_lines(lines):
        # Re-anchor the line number to the file (body starts after the frontmatter).
        offset = text.count("\n", 0, text.index(body)) if body in text else 0
        line_no = offset + idx + 1
        failures.append(
            f"{path}:{line_no}: {label} line lost its two-space hard break: {lines[idx]!r}"
        )


def _verse_register_pages() -> list[Path]:
    """Authored verse-register bodies: the <Verse> mission page(s) and every project
    subpage whose frontmatter sets `weight: verse`."""
    pages: list[Path] = []
    for slug in VERSE_PAGE_SLUGS:
        pages.extend(sorted((CONTENT / "pages" / slug).glob("*.md")))
    for md in sorted((CONTENT / "projects").glob("*/subpages/*/*.md")):
        if WEIGHT_VERSE_RE.search(md.read_text(encoding="utf-8")):
            pages.append(md)
    return pages


def main() -> int:
    failures: list[str] = []
    lineated_wrappers = 0

    for path in sorted((CONTENT / "books").glob("*/*.md")) + sorted((CONTENT / "projects").glob("*/*.md")):
        text = path.read_text(encoding="utf-8")
        lineated_wrappers += _check_lineated_wrappers(path, text, failures)

    poems = sorted((CONTENT / "poetry").glob("*/*.md"))
    for path in poems:
        if path.stem not in {"ru", "en"}:
            continue
        _check_whole_body(path, path.read_text(encoding="utf-8"), "poem", failures)

    verse_pages = _verse_register_pages()
    for path in verse_pages:
        _check_whole_body(path, path.read_text(encoding="utf-8"), "verse-register", failures)

    if failures:
        print("FAIL: two-space hard-break lineation trimmed")
        for failure in failures[:50]:
            print(" ", failure)
        if len(failures) > 50:
            print(f"  ... {len(failures) - 50} more")
        print(
            "\nA trimmed trailing-space break is silent lineation loss. Check .editorconfig "
            "([*.md] trim_trailing_whitespace = false) and any formatter/git filter, then "
            "regenerate the affected body."
        )
        return 1

    print(
        f"checked {lineated_wrappers} lineated wrappers + {len(poems)} poem bodies + {len(verse_pages)} "
        "verse-register pages; all hard breaks intact"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
