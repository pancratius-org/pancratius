# import-pure: no filesystem mutation
"""Publish-gate URL-scheme neutralization."""

from __future__ import annotations

import re
from dataclasses import replace

from pancratius import ir

# ---------------------------------------------------------------------------
# URL-scheme allowlist (defense-in-depth: the renderer emits raw HTML unsanitized)
# ---------------------------------------------------------------------------

# The site's Markdown renderer has NO sanitizer (lineated <div>, <span dir>,
# p.signature are emitted as raw HTML on purpose), so the IMPORT is the gate: a
# DOCX-authored link/image target must never become an active scheme in a
# published page. Only these schemes — plus relative / anchor / scheme-less
# targets — are allowed through; `javascript:`/`vbscript:`/`data:` (non-image) and
# any other scheme are unsafe.
_ALLOWED_URL_SCHEMES: frozenset[str] = frozenset({"http", "https", "mailto"})
# A leading `scheme:` per RFC 3986 (ALPHA then *(ALPHA / DIGIT / "+" / "-" / ".")),
# matched case-insensitively. A target with NO such prefix is relative/anchor and
# is allowed; one WITH a prefix is allowed only when the scheme is in the set.
URL_SCHEME_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.\-]*):")


def _is_safe_url(target: str) -> bool:
    """True if `target` is a safe link/image target: a relative/anchor/scheme-less
    path, or an absolute URL whose scheme is in `_ALLOWED_URL_SCHEMES`.

    A scheme-less target (`./x`, `/works/x`, `#anchor`, `images/a.png`, or bare
    text) carries no active scheme and is allowed. A target with an explicit
    `scheme:` prefix is allowed only for http/https/mailto; `javascript:`,
    `vbscript:`, `data:`, `file:`, etc. are rejected. Leading control/space chars
    (a `\\tjavascript:` evasion) are stripped before the scheme is read, mirroring
    how a browser would parse the attribute."""
    stripped = target.strip().lstrip("\x00\t\n\r ")
    m = URL_SCHEME_RE.match(stripped)
    if m is None:
        return True  # relative / anchor / scheme-less
    return m.group(1).lower() in _ALLOWED_URL_SCHEMES


def sanitize_urls(doc: ir.Document, diagnostics: ir.DiagnosticSink) -> ir.Document:
    """Drop unsafe link/image targets across the document, returning the
    sanitized document (diagnostics are appended to the caller's sink).

    For each reachable inline: an `ir.Link` with an unsafe target is replaced by
    its child inlines (the link text is KEPT, only the active target is dropped);
    an `ir.ImageInline` with an unsafe `src` is dropped entirely. Each removal
    surfaces a `warning` diagnostic so the admin sees what was neutralized. Runs
    BEFORE lowering (and before the asset pass), so an unsafe image never reaches
    asset resolution and an unsafe link never reaches the Markdown/HTML emitters.
    This is the URL half of the import gate; the asset pass + lowerer enforce the
    image-resolution half (an in-root-but-unresolvable ref is handled there)."""

    def visit_inlines(inlines: list[ir.Inline]) -> list[ir.Inline]:
        out: list[ir.Inline] = []
        for n in inlines:
            # isinstance, not match: the container arm tests `ir.ContainerInline`
            # (a runtime tuple), which can't appear in a `case`.
            if isinstance(n, ir.Link) and not _is_safe_url(n.target):
                diagnostics.append(ir.Diagnostic(
                    "warning", "import.unsafe-url",
                    f"link target {n.target!r} uses a disallowed URL scheme; dropped "
                    "the link, kept its text.",
                ))
                out.extend(visit_inlines(n.children))
            elif isinstance(n, ir.ImageInline) and not _is_safe_url(n.src):
                diagnostics.append(ir.Diagnostic(
                    "warning", "import.unsafe-url",
                    f"image source {n.src!r} uses a disallowed URL scheme; dropped "
                    "the image.",
                ))
                # drop it entirely (no replacement inline)
            elif isinstance(n, ir.ContainerInline):
                out.append(ir.rebuild_container(n, visit_inlines(n.children)))
            else:
                out.append(n)
        return out

    return replace(
        doc,
        blocks=[ir.map_block_inlines(b, visit_inlines) for b in doc.blocks],
        footnotes=[
            replace(fn, blocks=[ir.map_block_inlines(b, visit_inlines) for b in fn.blocks])
            for fn in doc.footnotes
        ],
    )
