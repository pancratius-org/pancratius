# research-pure: the per-token price table — a leaf value object the store boundary can build.
"""`PriceTable` is the versioned per-model OpenRouter price a study costs its readers against. It lives
in its OWN leaf module (importing only `identity`); `store.load_prices` returns the RAW dict and
`PriceTable.from_dict` parses it HERE, so the disk boundary never imports up into `evaluation/`. The
cost math that USES it lives in `reader_metrics`."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ..identity import JsonObject, ModelId

type Price = tuple[float, float]   # ($/token prompt, $/token completion) for one model


@dataclass(frozen=True, slots=True)
class PriceTable:
    """Per-token OpenRouter prices a study costs its readers against, versioned (pricing drifts). Built
    from the RAW `prices.toml` dict (`store.load_prices` reads, `from_dict` parses — the disk boundary
    does not reach up into evaluation); the `version` is stamped into the manifest so a scorecard's
    cost is reproducible. `price` FAILS LOUD on an unlisted model — a study that cannot price a reader
    must not report `$0` as free."""
    version: str
    models: Mapping[ModelId, Price]

    @classmethod
    def from_dict(cls, raw: JsonObject) -> PriceTable:
        """Parse the raw `prices.toml` dict (`{version, models: {model: {prompt, completion}}}`) into a
        typed table. The caller-side construction `store.load_prices` feeds, so the disk boundary stays
        a pure reader with no upward import into `evaluation/`."""
        models = {model: (float(row["prompt"]), float(row["completion"]))
                  for model, row in raw.get("models", {}).items()}
        return cls(version=str(raw["version"]), models=models)

    def price(self, model: ModelId) -> Price:
        if model not in self.models:
            raise KeyError(
                f"no price for model {model!r} in price table {self.version!r} — add it to "
                f"prices.toml; a study must not report an unpriced reader as $0")
        return self.models[model]
