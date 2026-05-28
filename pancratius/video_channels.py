"""Loader for ``src/content/videos/channels.yaml``.

Python adapter mirroring the zod schema in ``src/content.config.ts``. The
site reads channels through Astro; the scanner reads them through here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from pancratius.paths import CONTENT_ROOT

CHANNELS_PATH = CONTENT_ROOT / "videos" / "channels.yaml"


@dataclass(frozen=True, slots=True)
class VideoChannel:
    key: str
    platform: str
    handle: str | None
    channel_id: str | None
    url: str
    title: dict[str, str]
    copy: dict[str, str]
    badge: dict[str, str] | None
    scan: bool
    # The locale the scanner writes into `<lang>.md` for entries from this channel.
    default_lang: str


class ChannelsError(RuntimeError):
    """Raised when the channels file is missing or malformed."""


def load_channels(path: Path = CHANNELS_PATH) -> list[VideoChannel]:
    """Return the configured channels, in file order.

    The YAML is a list of objects keyed by `id` (the channel key). Empty
    optional fields collapse to None. Missing required fields raise
    ``ChannelsError``.
    """
    if not path.exists():
        raise ChannelsError(f"channels file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ChannelsError(f"{path}: expected a list at the top level, got {type(raw).__name__}")
    channels: list[VideoChannel] = []
    for index, entry in enumerate(raw):
        try:
            channels.append(_parse_one(entry))
        except KeyError as exc:
            raise ChannelsError(f"{path}[{index}]: missing required field {exc}") from None
        except ChannelsError as exc:
            raise ChannelsError(f"{path}[{index}]: {exc}") from None
    return channels


def _parse_one(entry: object) -> VideoChannel:
    if not isinstance(entry, Mapping):
        raise ChannelsError("expected a mapping")
    # `Mapping` is invariant in K; `Any` here is the load-bearing escape from
    # what would otherwise need a runtime cast on every key access.
    data: Mapping[Any, object] = entry
    key = str(data["id"])
    title = data["title"]
    copy = data["copy"]
    if not isinstance(title, Mapping):
        raise ChannelsError(f"channel {key}: `title` must be a per-locale mapping")
    if not isinstance(copy, Mapping):
        raise ChannelsError(f"channel {key}: `copy` must be a per-locale mapping")
    badge = data.get("badge")
    handle = data.get("handle")
    channel_id = data.get("channel_id")
    return VideoChannel(
        key=key,
        platform=str(data["platform"]),
        handle=str(handle) if handle else None,
        channel_id=str(channel_id) if channel_id else None,
        url=str(data["url"]),
        title=_locale_map(title),
        copy=_locale_map(copy),
        badge=_locale_map(badge) if isinstance(badge, Mapping) else None,
        scan=bool(data.get("scan", True)),
        default_lang=str(data.get("default_lang", "ru")),
    )


def _locale_map(raw: Mapping[Any, object]) -> dict[str, str]:
    return {str(k): str(v) for k, v in raw.items()}


def write_channels(channels: list[VideoChannel], path: Path = CHANNELS_PATH) -> None:
    """Re-serialize the channels list. Used by tests; production never writes
    ``channels.yaml`` (yaml.safe_dump would drop the authored comment header)."""
    payload = [_channel_to_dict(c) for c in channels]
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, width=120),
        encoding="utf-8",
    )


def _channel_to_dict(c: VideoChannel) -> dict[str, Any]:
    entry: dict[str, Any] = {"id": c.key, "platform": c.platform}
    if c.handle:
        entry["handle"] = c.handle
    entry["channel_id"] = c.channel_id or ""
    entry["url"] = c.url
    if c.badge:
        entry["badge"] = dict(c.badge)
    entry["title"] = dict(c.title)
    entry["copy"] = dict(c.copy)
    entry["scan"] = c.scan
    if c.default_lang != "ru":
        entry["default_lang"] = c.default_lang
    return entry
