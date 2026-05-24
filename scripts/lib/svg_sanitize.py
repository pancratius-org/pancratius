# import-pure: no filesystem mutation
"""Sanitize an SVG asset at the import asset-copy boundary (defense-in-depth).

SVG body images are served RAW, same-origin, by the static site. An SVG that
carries a script element, an `on*` event-handler attribute, a `javascript:` href,
a `<foreignObject>` (which can host arbitrary HTML), or an EXTERNAL
`href`/`xlink:href` (a fetch/exfil gadget) is a stored-XSS vector the moment it is
served. The site's Markdown renderer has no sanitizer (we emit raw HTML on
purpose), so the IMPORT is the gate: the writer routes every copied SVG through
`sanitize_svg` so an authored gadget never reaches a published asset.

This is NOT a general HTML sanitizer. It removes a closed, enumerated set of
dangerous constructs with conservative, byte-preserving regex surgery — a CLEAN
SVG (the common case: gradients/symbols referenced by internal `#`-fragment
`xlink:href`) is returned BYTE-FOR-BYTE so the real author SVGs are never
corrupted. Only when a dangerous construct is actually present are bytes rewritten.

The trust model justifies regex over a full XML round-trip: sources are
trusted-authored DOCX whose images are Inkscape/illustrator SVGs, imported locally
by an admin — the goal is to strip an inadvertent (or smuggled) active gadget, not
to defend a parser-differential against a determined attacker mutating the file at
serve time (serving is out of scope). A full XML reserialization would reorder
attributes and break the byte-identity guarantee the clean-SVG case relies on.

This module is PURE: it transforms bytes to bytes and touches no filesystem.
"""

from __future__ import annotations

import re

# A `<script ...>...</script>` element (including self-closing and unclosed-to-EOF),
# case-insensitive, DOTALL so the body spans newlines.
_SCRIPT_RE = re.compile(rb"<script\b[^>]*>.*?</script\s*>", re.IGNORECASE | re.DOTALL)
_SCRIPT_SELF_CLOSE_RE = re.compile(rb"<script\b[^>]*/\s*>", re.IGNORECASE)
_SCRIPT_UNCLOSED_RE = re.compile(rb"<script\b[^>]*>.*\Z", re.IGNORECASE | re.DOTALL)

# A `<foreignObject ...>...</foreignObject>` element (it can host arbitrary HTML).
_FOREIGN_OBJECT_RE = re.compile(
    rb"<foreignObject\b[^>]*>.*?</foreignObject\s*>", re.IGNORECASE | re.DOTALL
)
_FOREIGN_OBJECT_SELF_CLOSE_RE = re.compile(rb"<foreignObject\b[^>]*/\s*>", re.IGNORECASE)

# An `on...="..."` / `on...='...'` event-handler attribute (with its leading
# whitespace, so removal leaves no double space): `onload`, `onclick`, etc.
_ON_HANDLER_RE = re.compile(rb"""\s+on[a-zA-Z]+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""", re.IGNORECASE)

# An `href`/`xlink:href` whose value is UNSAFE: a `javascript:`/`vbscript:`/`data:`
# scheme, or an EXTERNAL ref (http/https/file/ftp/protocol-relative `//`). An
# INTERNAL `#fragment` ref and a relative path are kept (gradients/symbols rely on
# `xlink:href="#id"`). The whole attribute (with leading whitespace) is removed.
_UNSAFE_HREF_RE = re.compile(
    rb"""\s+(?:xlink:)?href\s*=\s*"""
    rb"""("(?:\s*(?:javascript|vbscript|data|https?|file|ftp):|\s*//)[^"]*"""
    rb"""|'(?:\s*(?:javascript|vbscript|data|https?|file|ftp):|\s*//)[^']*')""",
    re.IGNORECASE,
)


def _strip_all(payload: bytes) -> bytes:
    out = _SCRIPT_RE.sub(b"", payload)
    out = _SCRIPT_SELF_CLOSE_RE.sub(b"", out)
    out = _SCRIPT_UNCLOSED_RE.sub(b"", out)
    out = _FOREIGN_OBJECT_RE.sub(b"", out)
    out = _FOREIGN_OBJECT_SELF_CLOSE_RE.sub(b"", out)
    out = _ON_HANDLER_RE.sub(b"", out)
    out = _UNSAFE_HREF_RE.sub(b"", out)
    return out


def is_svg_name(name: str) -> bool:
    """True if `name` is an SVG by extension (the writer's sanitize trigger)."""
    return name.lower().endswith(".svg")


def sanitize_svg(payload: bytes) -> bytes:
    """Return `payload` with SVG XSS gadgets removed.

    Strips `<script>`/`<foreignObject>` elements, `on*` event-handler attributes,
    `javascript:`/`vbscript:`/`data:` and external `href`/`xlink:href` targets;
    keeps everything else, including internal `#`-fragment refs. CLEAN input is
    returned UNCHANGED (the same object), so a clean SVG copies byte-for-byte.

    Runs the strip pass to a fixed point (max a few iterations) so a construct that
    only becomes matchable after a sibling is removed — e.g. a handler revealed when
    a wrapping `<script>` goes — is still caught. Idempotent: re-running on already-
    sanitized bytes is a no-op.
    """
    out = payload
    for _ in range(8):  # fixed point; SVGs are shallow, this converges in 1-2 passes
        stripped = _strip_all(out)
        if stripped == out:
            break
        out = stripped
    return out
