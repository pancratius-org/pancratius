from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import yaml

from pancratius.locales import Locale
from pancratius.localization._yaml import as_mapping

type TagLabels = Mapping[str, str]


def load_tag_labels(path: Path, locale: Locale) -> TagLabels:
    try:
        raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError):
        return {}
    data = as_mapping(raw)
    if data is None:
        return {}
    labels = as_mapping(data.get(locale))
    if labels is None:
        return {}
    return {str(k): str(v) for k, v in labels.items() if isinstance(v, str)}
