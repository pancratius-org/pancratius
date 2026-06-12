# import-pure: no filesystem mutation
"""Asset pass: assign content-hash asset ids to body images; plan their copy.

The asset pass reads the extracted media files to hash them (read-only `open`),
then assigns content-hash asset ids; it mutates nothing on disk — the returned
`PlannedAsset`s are what the writer later copies.

Image hashing, extension normalization, the hash-prefix length, the raster-cap
set live here next to the asset pass that is their sole user; `PlannedAsset` is
the plan-adjacent value type from `writeplan`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import assert_never

from pancratius import ir
from pancratius.passes.sanitize import URL_SCHEME_RE
from pancratius.writeplan import PlannedAsset

# ---------------------------------------------------------------------------
# body-image asset constants + helpers (content-addressed planning)
# ---------------------------------------------------------------------------

# Length of the content-hash prefix used for `images/<hash>.<ext>` asset ids.
HASH_PREFIX_LEN = 12

# Image extensions a body media file may carry; `_normalize_ext` folds `.jpeg`/
# `.jpe` to `.jpg` so equivalent encodings hash to the same asset id.
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".tiff", ".emf", ".wmf")
EXT_FROM_MIME = {".jpeg": ".jpg", ".jpe": ".jpg"}

# Raster body-image extensions the import-time longest-edge cap applies to (after
# `_normalize_ext` folds `.jpeg`->`.jpg`). Vector (svg/emf/wmf) and animated (gif)
# are copied verbatim. The cap itself is a writer transform; this set only labels
# which planned assets are cap-eligible.
RASTER_CAP_EXTS = frozenset({".png", ".jpg", ".webp", ".avif"})


def _normalize_ext(ext: str) -> str:
    ext = ext.lower()
    return EXT_FROM_MIME.get(ext, ext)


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()[:HASH_PREFIX_LEN]


def _is_image_path(p: str) -> bool:
    return any(p.lower().endswith(e) for e in IMAGE_EXTS)


def _escapes_media_root(src: str, media_root: Path) -> bool:
    """True if `src` resolves OUTSIDE the pandoc media-extraction dir.

    The real-path confinement: `(media_root / src).resolve()` (absolute `src`
    overrides `media_root` under `Path.__truediv__`, so an absolute path resolves to
    itself; a relative one joins) must be `media_root` itself or sit under it, with
    BOTH sides `resolve()`d so a symlinked component or a `/tmp -> /private/tmp`
    style root is normalized. `..` in the path parts is treated as escaping too
    (defense-in-depth for the parent-traversal intent, even where it would resolve
    back). This is what stops an `src` like `/etc/passwd` or `../../secret` from
    being read/copied — WITHOUT rejecting the absolute-but-in-root paths Pandoc
    legitimately emits (`<media_root>/media/imageN.jpg`)."""
    if ".." in PurePosixPath(src).parts:
        return True
    root = media_root.resolve()
    cand = (media_root / src).resolve()
    return cand != root and root not in cand.parents


def _confined_media_source(src: str, media_root: Path) -> Path | None:
    """Resolve a body-image `src` to a real file CONFINED under `media_root`.

    Returns the resolved candidate iff it does NOT escape `media_root` and is a
    readable file; otherwise `None` (the caller drops the ref, with a diagnostic for
    the escape case). The previous `Path(src)` arbitrary-path fallback is removed: a
    ref that does not resolve safely UNDER `media_root` is never read."""
    if _escapes_media_root(src, media_root):
        return None
    cand = (media_root / src).resolve()
    if not cand.is_file():
        return None
    return cand


def _is_remote_url(src: str) -> bool:
    """True for a safe remote (http/https) image url, kept as-is. Unsafe schemes are
    dropped upstream by `sanitize_urls`, so surviving scheme-bearing srcs are
    http/https."""
    m = URL_SCHEME_RE.match(src.strip())
    return m is not None and m.group(1).lower() in {"http", "https"}


# Outcome of resolving one body-image src.
@dataclass(frozen=True)
class _ResolvedAsset:
    """Resolved to a content-hash asset; `asset_id` is its `<hash><ext>` filename
    (the ref is rewritten to `./images/<asset_id>`)."""

    asset_id: str


@dataclass(frozen=True)
class _DropImage:
    """An unresolvable local image: FATAL upstream, the ref is dropped."""


@dataclass(frozen=True)
class _KeepRemote:
    """A safe remote (http/https) ref, kept as-is."""


type _ImageResolution = _ResolvedAsset | _DropImage | _KeepRemote


def plan_assets(
    doc: ir.Document,
    media_root: Path,
    diagnostics: ir.DiagnosticSink,
) -> tuple[ir.Document, list[PlannedAsset]]:
    """Resolve every body image, assign its content-hash `<hash>.<ext>` asset id,
    and return the rebuilt document plus the deduped `PlannedAsset`s for the
    writer to copy.

    `media_root` is the directory pandoc extracted media into. An image whose source
    is a safe REMOTE url (http/https) is kept as-is. A LOCAL image whose source does
    NOT resolve to a safe readable file UNDER `media_root` — a missing in-root ref,
    or an absolute / `..`-escaping path — is FATAL (docs/import-pipeline.md: "an
    unresolvable local image is fatal"): a FATAL diagnostic is surfaced AND the ref
    is DROPPED so the lowerer never writes a dangling/escaping path (e.g. a
    `/Users/...` leak) into the published body. PURE: this only READS the media files
    to hash them. The returned list is sorted by bundle-relative path, giving the
    writer a stable asset order.
    """
    seen: dict[str, _ResolvedAsset] = {}
    planned: dict[str, PlannedAsset] = {}

    def resolve(src: str) -> _ImageResolution:
        """Resolve ONE image src to a tagged `_ImageResolution` (see the union above).

        A cached src was previously resolved to an asset; a safe remote ref is kept;
        any other src is a LOCAL image that must resolve to a safe readable image file
        under `media_root` or it is FATAL (surfaced) and dropped."""
        if src in seen:
            return seen[src]
        if _is_remote_url(src):
            return _KeepRemote()  # valid remote image ref — not a local image
        cand = _confined_media_source(src, media_root)
        if cand is None or not _is_image_path(cand.name):
            # A LOCAL image ref that does not resolve to a safe readable image file
            # under the media dir. The documented FATAL: surface it and drop the ref
            # (the writer refuses the whole write; the body never leaks the path).
            escaped = _escapes_media_root(src, media_root)
            diagnostics.append(ir.Diagnostic(
                "fatal", "import.image-unresolved",
                f"local image source {src!r} "
                + ("escapes the media-extraction dir" if escaped else "does not resolve to a readable image under the media dir")
                + "; refusing the write and dropping the ref (no dangling path emitted).",
            ))
            return _DropImage()
        h = _hash_file(cand)
        ext = _normalize_ext(cand.suffix)
        rel_within = f"images/{h}{ext}"
        planned.setdefault(
            rel_within,
            PlannedAsset(rel_within=rel_within, source=cand, is_raster=ext in RASTER_CAP_EXTS),
        )
        resolved = _ResolvedAsset(asset_id=f"{h}{ext}")
        seen[src] = resolved
        return resolved

    def visit_inlines(inlines: list[ir.Inline]) -> list[ir.Inline]:
        out: list[ir.Inline] = []
        for n in inlines:
            # isinstance, not match: the container arm tests `ir.ContainerInline`
            # (a runtime tuple), which can't appear in a `case`.
            if isinstance(n, ir.ImageInline):
                match resolve(n.src):
                    case _DropImage():
                        continue  # unresolvable local image: FATAL upstream, drop the ref
                    case _ResolvedAsset(asset_id=asset_id):
                        out.append(ir.ImageInline(src=n.src, alt=n.alt, asset_id=asset_id))
                    case _KeepRemote():
                        out.append(ir.ImageInline(src=n.src, alt=n.alt, asset_id=None))
                    case unexpected:
                        assert_never(unexpected)
            elif isinstance(n, ir.ContainerInline):
                out.append(ir.rebuild_container(n, visit_inlines(n.children)))
            else:
                out.append(n)
        return out

    def visit_block(b: ir.Block) -> ir.Block:
        # Deliberately PARTIAL (a `case _` delegating to the shared skeleton, NOT
        # `assert_never`): an `ImageBlock` is the one leaf the shared inline-descent
        # cannot express (its image is a block field, not an inline list), so it is
        # rebuilt here; every other block kind has its inline-list leaves handled by
        # `map_block_inlines`.
        match b:
            case ir.ImageBlock():
                match resolve(b.src):
                    case _DropImage():
                        # An unresolvable local block image is FATAL; blank the src so
                        # the lowerer emits no dangling path (the write is refused).
                        return replace(b, src="", asset_id=None)
                    case _ResolvedAsset(asset_id=asset_id):
                        return replace(b, asset_id=asset_id)
                    case _KeepRemote():
                        return b  # a remote block image keeps its src; no asset id
                    case unexpected:
                        assert_never(unexpected)
            case _:
                return ir.map_block_inlines(b, visit_inlines)

    out_doc = replace(doc, blocks=[visit_block(b) for b in doc.blocks])
    return out_doc, [planned[k] for k in sorted(planned)]
