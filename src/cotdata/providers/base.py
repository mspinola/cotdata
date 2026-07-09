"""A thin seam for price producers — enough to swap sources, not a plugin
framework (single active source = Norgate)."""
from typing import Protocol

import pandas as pd


class PriceProvider(Protocol):
    name: str

    def fetch(self, internal_symbol: str, adjustment: str) -> pd.DataFrame:
        """Return daily bars (Open/High/Low/Close/Volume/Open Interest, tz-naive
        Date index; optionally 'Delivery Month') for one symbol/adjustment."""
        ...
