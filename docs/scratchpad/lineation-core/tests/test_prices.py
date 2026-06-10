# research-pure: the committed price table round-trips, surfaces its version, and FAILS LOUD on an
# unknown model — a study must never report an unpriced reader as $0.
"""Locks `store.load_prices` + `PriceTable`: the disk boundary returns the RAW dict (no upward import
into evaluation), `PriceTable.from_dict` parses it, every seeded model prices, an unlisted model raises
(not a silent (0,0)), and the version is surfaced for the manifest stamp."""
from __future__ import annotations

import pytest

from lineation_core import store
from lineation_core.evaluation.reader_metrics import PriceTable


def test_load_prices_returns_the_raw_dict():
    raw = store.load_prices()                       # the disk boundary parses no domain type
    assert isinstance(raw, dict) and raw["version"] == "2026-06-09"
    assert "models" in raw


def test_load_prices_round_trips_seeded_models():
    pt = PriceTable.from_dict(store.load_prices())
    assert pt.version == "2026-06-09"
    assert pt.price("x-ai/grok-4.3") == (1.25e-6, 2.5e-6)
    assert pt.price("deepseek/deepseek-v4-flash") == (0.0983e-6, 0.1966e-6)


def test_price_fails_loud_on_unknown_model():
    pt = PriceTable.from_dict(store.load_prices())
    with pytest.raises(KeyError, match="no price"):
        pt.price("acme/not-a-real-model")


def test_price_table_version_surfaced():
    pt = PriceTable(version="2099-01-01", models={"m": (1e-6, 2e-6)})
    assert pt.version == "2099-01-01" and pt.price("m") == (1e-6, 2e-6)
