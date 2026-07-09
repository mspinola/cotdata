"""cotdata — canonical data layer (see README). Public consumer API."""
from .prices import get_prices, roll_dates
from .cot import get_cot
from .registry import symbol, all_symbols, REGISTRY, Symbol
from .store import load_manifest

__version__ = "0.1.0"
__all__ = [
    "get_prices", "roll_dates", "get_cot",
    "symbol", "all_symbols", "REGISTRY", "Symbol", "load_manifest",
]
