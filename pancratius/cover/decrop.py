"""De-crop: force model output dimensions to match the source cover exactly.

The generation model sometimes re-frames the canvas (adding white margins or
cropping). Source dimensions are ground truth; we fix this deterministically
with Pillow rather than asking the model to "remove the border".

We keep the raw model output (.raw.png) alongside the de-cropped final (.en.png)
so both stages stay inspectable.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops

_WHITE_TRIM_THRESHOLD = 12  # brightness delta from pure-white counted as "border"


@dataclass(frozen=True, slots=True)
class DecropReport:
    source_size: tuple[int, int]
    raw_size: tuple[int, int]
    final_size: tuple[int, int]
    resized: bool

    @property
    def ok(self) -> bool:
        return self.final_size == self.source_size


def _trim_white_border(img: Image.Image) -> Image.Image:
    """Crop off a near-white margin added by the model; no-op otherwise."""
    rgb = img.convert("RGB")
    white = Image.new("RGB", rgb.size, (255, 255, 255))
    diff = ImageChops.difference(rgb, white)
    bbox = ImageChops.add(diff, diff, 2.0, -_WHITE_TRIM_THRESHOLD).getbbox()
    if bbox and bbox != (0, 0, rgb.width, rgb.height):
        return img.crop(bbox)
    return img


def decrop_to_source(
    raw_bytes: bytes,
    source: Path,
    raw_out: Path,
    final_out: Path,
) -> DecropReport:
    """Write ``raw_out`` from ``raw_bytes``, then de-crop to ``source`` dimensions
    and write ``final_out``. Returns a report."""
    raw_out.write_bytes(raw_bytes)

    src = Image.open(source)
    out = Image.open(raw_out)
    source_size = (src.width, src.height)
    raw_size = (out.width, out.height)

    if out.size != src.size:
        out = _trim_white_border(out)
        out = out.resize(src.size, Image.LANCZOS)
        resized = True
    else:
        resized = False

    out.convert("RGB").save(final_out, "PNG")
    final_size = (out.width, out.height)  # after resize = src.size when resized
    return DecropReport(
        source_size=source_size,
        raw_size=raw_size,
        final_size=final_size,
        resized=resized,
    )
