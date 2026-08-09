"""cotdata — canonical CFTC positioning layer (see README). Public consumer API.

Bars are NOT here. ADR-0007 makes this package COT-only and moves every price
series to `marketdata`, so `get_prices`/`roll_dates` are gone — import
``marketdata.get_bars`` instead. The two packages keep separate stores
($COTDATA_STORE, $MARKETDATA_STORE) and separate producers.
"""
from .cot import get_cot
from .registry import REGISTRY, Symbol, all_symbols, symbol
from .store import load_manifest, require_schema, schema_version

__version__ = "0.4.0"
__all__ = [
    "get_cot",
    "symbol", "all_symbols", "REGISTRY", "Symbol",
    "load_manifest", "schema_version", "require_schema",
]
