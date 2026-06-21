"""Guard canonical Markdown structure after frontmatter.

The corpus uses ATX headings and canonical ``***`` thematic breaks. Body
``---`` / ``===`` markers are forbidden because they can parse as setext heading
underlines, which pollutes ToCs, anchors, document outlines, and screen-reader
navigation. Lineated wrappers are parsed as Markdown inside raw HTML, so heading
syntax inside them is checked explicitly too.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from pancratius.thematic import is_setext_underline_marker, is_thematic_marker


def _audit_root() -> Path:
    env = os.environ.get("PANCRATIUS_AUDIT_ROOT")
    return Path(env).resolve() if env else Path(__file__).resolve().parents[1]


ROOT = _audit_root()
CONTENT = ROOT / "src" / "content"

LINEATED_DIV_RE = re.compile(
    r'<div\b[^>]*\bclass=(?P<quote>["\'])(?P<class>[^"\']*\blineated\b[^"\']*)(?P=quote)[^>]*>\n'
    r"(?P<body>.*?)\n</div>",
    re.S,
)
FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
ATX_HEADING_RE = re.compile(r"^ {0,3}#{1,6}(?:\s|$)")
RAW_HEADING_RE = re.compile(r"<\s*/?\s*h[1-6]\b", re.I)


@dataclass(frozen=True)
class MarkdownBody:
    text: str
    line_offset: int


class FenceGlyph(Enum):
    BACKTICK = "`"
    TILDE = "~"


@dataclass(frozen=True)
class Fence:
    glyph: FenceGlyph
    minimum_length: int


@dataclass(frozen=True)
class NoFenceOpener:
    pass


@dataclass(frozen=True)
class OutsideFence:
    pass


type FenceOpener = Fence | NoFenceOpener
type FenceScanState = Fence | OutsideFence


class ThematicDivider(Enum):
    NONE = "none"
    CANONICAL = "canonical"
    NONCANONICAL = "noncanonical"


class SetextRisk(Enum):
    NONE = "none"
    SINGLE_HYPHEN = "single-hyphen"
    HEADING_UNDERLINE = "heading-underline"


class LineatedStructuralMarker(Enum):
    THEMATIC_BREAK = "thematic break"
    NONCANONICAL_DIVIDER = "noncanonical divider"
    SINGLE_HYPHEN_SETEXT_MARKER = "single-hyphen setext marker"
    SETEXT_UNDERLINE = "setext underline"


def _split_body(text: str) -> MarkdownBody:
    if not text.startswith("---\n"):
        return MarkdownBody(text=text, line_offset=0)
    end = text.find("\n---\n", 3)
    if end < 0:
        return MarkdownBody(text=text, line_offset=0)
    body_start = end + len("\n---\n")
    return MarkdownBody(text=text[body_start:], line_offset=text[:body_start].count("\n"))


def _fence_opener(line: str) -> FenceOpener:
    match = FENCE_OPEN_RE.match(line)
    if not match:
        return NoFenceOpener()
    marker = match.group(1)
    if marker[0] == "`" and "`" in match.group(2):
        return NoFenceOpener()
    glyph = FenceGlyph.BACKTICK if marker[0] == "`" else FenceGlyph.TILDE
    return Fence(glyph=glyph, minimum_length=len(marker))


def _is_fence_closer(line: str, fence: Fence) -> bool:
    return bool(
        re.match(rf"^ {{0,3}}{re.escape(fence.glyph.value)}{{{fence.minimum_length},}}\s*$", line)
    )


def _thematic_divider(line: str) -> ThematicDivider:
    stripped = line.strip()
    if stripped == r"\*\*\*":
        return ThematicDivider.NONE
    if not is_thematic_marker(stripped):
        return ThematicDivider.NONE
    return ThematicDivider.CANONICAL if stripped == "***" else ThematicDivider.NONCANONICAL


def _setext_risk(line: str) -> SetextRisk:
    stripped = line.strip()
    if not is_setext_underline_marker(stripped):
        return SetextRisk.NONE
    if stripped == "-":
        return SetextRisk.SINGLE_HYPHEN
    return SetextRisk.HEADING_UNDERLINE


def _scan_body_markers(path: Path, body: MarkdownBody, failures: list[str]) -> None:
    fence_state: FenceScanState = OutsideFence()
    previous_body_line_has_text = False
    for idx, line in enumerate(body.text.splitlines()):
        if isinstance(fence_state, Fence):
            if _is_fence_closer(line, fence_state):
                fence_state = OutsideFence()
            continue
        opener = _fence_opener(line)
        if isinstance(opener, Fence):
            fence_state = opener
            previous_body_line_has_text = False
            continue

        thematic_divider = _thematic_divider(line)
        if thematic_divider is ThematicDivider.NONCANONICAL:
            failures.append(
                f"{path}:{body.line_offset + idx + 1}: non-canonical body divider {line!r}; "
                "use canonical ***"
            )
        if (
            thematic_divider is ThematicDivider.NONE
            and previous_body_line_has_text
            and _setext_risk(line) is SetextRisk.HEADING_UNDERLINE
        ):
            failures.append(
                f"{path}:{body.line_offset + idx + 1}: setext-prone body underline {line!r}; "
                "use ATX headings or canonical *** with blank lines"
            )

        previous_body_line_has_text = bool(line.strip())


def _scan_lineated_wrappers(path: Path, text: str, failures: list[str]) -> None:
    for match in LINEATED_DIV_RE.finditer(text):
        class_name = re.sub(r"\s+", " ", match.group("class")).strip()
        line_offset = text[: match.start("body")].count("\n")
        for idx, line in enumerate(match.group("body").splitlines()):
            line_no = line_offset + idx + 1
            if ATX_HEADING_RE.match(line):
                failures.append(
                    f"{path}:{line_no}: {class_name} wrapper contains ATX heading syntax: {line!r}"
                )
            if RAW_HEADING_RE.search(line):
                failures.append(
                    f"{path}:{line_no}: {class_name} wrapper contains raw HTML heading: {line!r}"
                )
            thematic_divider = _thematic_divider(line)
            setext_risk = _setext_risk(line)
            if thematic_divider is not ThematicDivider.NONE or setext_risk is not SetextRisk.NONE:
                descriptor = _lineated_structural_marker(thematic_divider, setext_risk)
                failures.append(
                    f"{path}:{line_no}: {class_name} wrapper contains {descriptor.value} "
                    f"{line!r}; close the wrapper and place canonical *** between wrappers"
                )


def _lineated_structural_marker(
    thematic_divider: ThematicDivider, setext_risk: SetextRisk
) -> LineatedStructuralMarker:
    if thematic_divider is ThematicDivider.CANONICAL:
        return LineatedStructuralMarker.THEMATIC_BREAK
    if thematic_divider is ThematicDivider.NONCANONICAL:
        return LineatedStructuralMarker.NONCANONICAL_DIVIDER
    if setext_risk is SetextRisk.SINGLE_HYPHEN:
        return LineatedStructuralMarker.SINGLE_HYPHEN_SETEXT_MARKER
    return LineatedStructuralMarker.SETEXT_UNDERLINE


def main() -> int:
    failures: list[str] = []
    if not CONTENT.exists():
        print(f"content root missing: {CONTENT}")
        return 0

    for path in sorted(CONTENT.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        body = _split_body(text)
        _scan_body_markers(path, body, failures)
        _scan_lineated_wrappers(path, text, failures)

    if failures:
        print("FAIL: non-canonical Markdown structure")
        for failure in failures[:80]:
            print(" ", failure)
        if len(failures) > 80:
            print(f"  ... {len(failures) - 80} more")
        print(
            "\nBody thematic breaks must be canonical `***`, never `---` / `===`; "
            "lineated wrappers must not contain heading or divider markers."
        )
        return 1

    print("checked content Markdown structure; no setext-prone dividers or lineated headings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
