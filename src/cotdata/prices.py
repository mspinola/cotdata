"""Consumer price API. Reads the store and returns the shape downstream code
expects (the old fetch_daily_ohlc contract). No network, cross-platform."""
from typing import Optional

import pandas as pd

from . import store

_COLS = ["Open", "High", "Low", "Close", "Volume", "Open Interest"]


def get_prices(symbol: str, adjustment: str = "backadj",
               start: Optional[str] = None) -> pd.DataFrame:
    """Daily bars for `symbol`.

    adjustment: 'backadj' (signals + stops — settlement close, gap-free rolls,
    shape-preserving) or 'unadj' (absolute price / point-value sizing).
    Returns Open/High/Low/Close/Volume/Open Interest indexed by tz-naive Date
    (plus 'Delivery Month' if the producer carried it), or empty if absent.
    """
    df = store.read_prices(symbol, adjustment)
    if df.empty:
        return df
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df.index.name = "Date"
    df = df.sort_index()
    if start:
        df = df[df.index >= pd.Timestamp(start)]
    for c in _COLS:
        if c not in df.columns:
            df[c] = float("nan")
    keep = _COLS + (["Delivery Month"] if "Delivery Month" in df.columns else [])
    return df[keep].copy()


def roll_dates(symbol: str, adjustment: str = "backadj") -> pd.DatetimeIndex:
    """Exact roll dates = days where the underlying Delivery Month changes.
    Empty if the producer didn't carry 'Delivery Month'."""
    df = get_prices(symbol, adjustment)
    if df.empty or "Delivery Month" not in df.columns:
        return pd.DatetimeIndex([])
    changed = df["Delivery Month"].ne(df["Delivery Month"].shift())
    return df.index[changed.fillna(False)]
