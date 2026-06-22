"""De-crop model output to the source image dimensions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops

_WHITE_TRIM_THRESHOLD = 12


@dataclass(frozen=True, slots=True)
class DecropReport:
    source_size: tuple[int, int]
    raw_size: tuple[int, int]
    final_size: tuple[int, int]
    resized: bool

    @property
    def ok(self) -> bool:
        return self.source_size == self.final_size


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
    *,
    raw_bytes: bytes,
    source: Path,
    raw_out: Path,
    final_out: Path,
) -> DecropReport:
    """Persist raw output, trim model-added white borders, and match source size."""
    raw_out.parent.mkdir(parents=True, exist_ok=True)
    final_out.parent.mkdir(parents=True, exist_ok=True)
    raw_out.write_bytes(raw_bytes)

    with Image.open(source) as src_img:
        source_size = src_img.size
    with Image.open(raw_out) as raw_img:
        raw_size = raw_img.size
        out = raw_img.copy()

    resized = False
    if out.size != source_size:
        out = _trim_white_border(out)
        out = out.resize(source_size, Image.Resampling.LANCZOS)
        resized = True

    out.convert("RGB").save(final_out, "PNG")
    return DecropReport(
        source_size=source_size,
        raw_size=raw_size,
        final_size=out.size,
        resized=resized,
    )
