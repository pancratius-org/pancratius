from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import yaml

from pancratius.locales import Locale
from pancratius.localization._yaml import as_mapping

type TagLabels = Mapping[str, str]
# YouTube playlist id → canonical RU tag key. The id is the stable fact; the
# playlist's display title is channel copy that gets renamed.
type PlaylistTagKeys = Mapping[str, str]


def load_tag_labels(path: Path, locale: Locale) -> TagLabels:
    return _string_section(path, locale)


def load_playlist_tag_keys(path: Path) -> PlaylistTagKeys:
    return _string_section(path, "playlists")


def _string_section(path: Path, key: str) -> Mapping[str, str]:
    try:
        raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError):
        return {}
    data = as_mapping(raw)
    if data is None:
        return {}
    section = as_mapping(data.get(key))
    if section is None:
        return {}
    return {str(k): str(v) for k, v in section.items() if isinstance(v, str)}
