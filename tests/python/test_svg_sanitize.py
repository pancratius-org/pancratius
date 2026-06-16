"""Unit tests for the SVG sanitizer (pancratius/svg_sanitize.py).

Pure byte→byte transform tests (no filesystem), plus a cross-check that the real
committed BODY SVGs round-trip byte-for-byte (they are clean — the sanitizer is a
no-op on them) so the import boundary never corrupts the author's images.
"""

from __future__ import annotations

from pathlib import Path

from pancratius import svg_sanitize

ROOT = Path(__file__).resolve().parents[2]


def test_strips_script_element() -> None:
    out = svg_sanitize.sanitize_svg(b'<svg><script>alert(1)</script><rect/></svg>')
    assert b"<script" not in out
    assert b"<rect/>" in out


def test_strips_unclosed_script_to_eof() -> None:
    out = svg_sanitize.sanitize_svg(b'<svg><rect/><script>alert(1)')
    assert b"<script" not in out
    assert b"<rect/>" in out


def test_strips_script_with_junk_end_tag_keeps_siblings() -> None:
    # `</script bar>` is a valid close — strip only the script, keep the siblings.
    out = svg_sanitize.sanitize_svg(b'<svg><script>alert(1)</script bar><rect/></svg>')
    assert b"<script" not in out
    assert b"alert(1)" not in out
    assert b"<rect/>" in out
    assert b"</svg>" in out


def test_strips_foreign_object_with_junk_end_tag_keeps_siblings() -> None:
    out = svg_sanitize.sanitize_svg(
        b'<svg><foreignObject><body>h</body></foreignObject id=x><rect/></svg>'
    )
    assert b"foreignObject" not in out
    assert b"<body>" not in out
    assert b"<rect/>" in out


def test_strips_on_handler_attribute() -> None:
    out = svg_sanitize.sanitize_svg(b'<svg onload="x()"><rect onclick=\'y()\'/></svg>')
    assert b"onload" not in out
    assert b"onclick" not in out
    assert b"<rect" in out


def test_strips_javascript_href() -> None:
    out = svg_sanitize.sanitize_svg(b'<svg><a href="javascript:alert(1)"><text>x</text></a></svg>')
    assert b"javascript:" not in out
    assert b"<text>x</text>" in out


def test_strips_foreign_object() -> None:
    out = svg_sanitize.sanitize_svg(b'<svg><foreignObject><body>h</body></foreignObject><rect/></svg>')
    assert b"foreignObject" not in out
    assert b"<rect/>" in out


def test_strips_external_xlink_href_keeps_internal() -> None:
    out = svg_sanitize.sanitize_svg(
        b'<svg xmlns:xlink="x">'
        b'<image xlink:href="https://evil/x.png"/>'
        b'<use xlink:href="#g1"/>'
        b"</svg>"
    )
    assert b"https://evil" not in out
    assert b'xlink:href="#g1"' in out


def test_strips_data_uri_href() -> None:
    out = svg_sanitize.sanitize_svg(b'<svg><image href="data:text/html,<script>x</script>"/></svg>')
    assert b"data:" not in out


def test_clean_svg_unchanged_byte_for_byte() -> None:
    clean = (
        b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'
        b'<defs><linearGradient id="g"/></defs>'
        b'<rect fill="url(#g)" width="5" height="5"/>'
        b'<use xlink:href="#g"/></svg>'
    )
    assert svg_sanitize.sanitize_svg(clean) == clean


def test_sanitize_is_idempotent() -> None:
    dirty = b'<svg onload="x()"><script>a</script><rect/></svg>'
    once = svg_sanitize.sanitize_svg(dirty)
    twice = svg_sanitize.sanitize_svg(once)
    assert once == twice


def test_committed_body_svgs_are_clean_and_unchanged() -> None:
    # The 3 committed body SVGs (DOCX-extracted, content-hash named) must be clean —
    # the sanitizer is a byte-for-byte no-op. This guards against the import boundary
    # ever corrupting the real author images, and documents their safety status.
    body_dir = ROOT / "src/content/books/71-trinadtsatyi-etazh-vozvrashchenie-v-edem/images"
    svgs = sorted(body_dir.glob("*.svg"))
    assert svgs, "expected committed body SVGs to cross-check"
    for svg in svgs:
        raw = svg.read_bytes()
        assert svg_sanitize.sanitize_svg(raw) == raw, f"{svg.name} must be clean (no-op sanitize)"
