# import-pure: no filesystem mutation
"""Sanitize an SVG asset at the import asset-copy boundary (defense-in-depth).

SVG body images are served raw, same-origin; a script element, an `on*` handler, a
`javascript:` href, a `<foreignObject>` (it can host arbitrary HTML), or an
external `href`/`xlink:href` (a fetch/exfil gadget) is then stored XSS. The
renderer has no sanitizer (it emits raw HTML by design), so import is the gate.

Not a general HTML sanitizer: it removes a closed, enumerated set of constructs
with byte-preserving regex surgery, so a clean SVG (the common case:
gradients/symbols via internal `#`-fragment `xlink:href`) returns byte-for-byte and
the author SVGs are never corrupted. Regex over an XML round-trip is intentional —
sources are admin-imported, trusted-authored DOCX SVGs (serve-time parser
differentials are out of scope), and reserialization would reorder attributes and
break the clean-SVG byte-identity. PURE: bytes to bytes, no filesystem.
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

    Strips `<script>`/`<foreignObject>` elements, `on*` handler attributes, and
    `javascript:`/`vbscript:`/`data:`/external `href`/`xlink:href` targets; keeps
    everything else, including internal `#`-fragment refs. Clean input returns
    unchanged, so a clean SVG copies byte-for-byte.

    Strips to a fixed point so a construct revealed only after a sibling is removed
    (e.g. a handler unwrapped when its `<script>` goes) is still caught. Idempotent.
    """
    out = payload
    for _ in range(8):  # SVGs are shallow; converges in 1-2 passes
        stripped = _strip_all(out)
        if stripped == out:
            break
        out = stripped
    return out
