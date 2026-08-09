"""cotdata — canonical CFTC positioning layer (see README). Public consumer API.

Bars are NOT here. ADR-0007 makes this package COT-only and moves every price
series to `marketdata` — Norgate, databento and Yahoo alike — so `get_prices` and
`roll_dates` are gone; import ``marketdata.get_bars`` instead. The two packages
keep separate stores ($COTDATA_STORE, $MARKETDATA_STORE), separate registries and
separate producers.
"""
from .cot import get_cot
from .registry import REGISTRY, Symbol, all_symbols, symbol
from .store import load_manifest, require_schema, schema_version

__version__ = "0.5.0"
__all__ = [
    "get_cot",
    "symbol", "all_symbols", "REGISTRY", "Symbol",
    "load_manifest", "schema_version", "require_schema",
]
