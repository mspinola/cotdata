"""cotdata — canonical data layer (see README). Public consumer API."""
from .cot import get_cot
from .prices import get_prices, roll_dates
from .registry import REGISTRY, Symbol, all_symbols, symbol
from .store import load_manifest, require_schema, schema_version

__version__ = "0.1.0"
__all__ = [
    "get_prices", "roll_dates", "get_cot",
    "symbol", "all_symbols", "REGISTRY", "Symbol",
    "load_manifest", "schema_version", "require_schema",
]
