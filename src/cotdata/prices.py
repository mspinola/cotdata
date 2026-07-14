"""Consumer price API. Reads the store and returns the shape downstream code
expects (the old fetch_daily_ohlc contract). No network, cross-platform."""
from typing import Optional

import pandas as pd

from . import store

_COLS = ["Open", "High", "Low", "Close", "Volume", "Open Interest"]


def get_prices(symbol: str, adjustment: str = "backadj",
               start: Optional[str] = None,
               volume: str = "front") -> pd.DataFrame:
    """Daily bars for `symbol`.

    adjustment: 'backadj' (signals + stops — settlement close, gap-free rolls,
    shape-preserving) or 'unadj' (absolute price / point-value sizing).

    volume: which series the `Volume` column carries —
      'front'         : continuous front-month volume as Norgate reports it
                        (default — output is byte-identical to the pre-v2 API).
      'reconstructed' : true market volume (first + second expiring contract,
                        with per-row fall-back to front-month where individual
                        contracts are unavailable). The intent view: the fall-back
                        policy lives here, in the data layer, so consumers ask for
                        what they want instead of re-deriving it. Adds a
                        'Volume_Source' column ('reconstructed' / 'raw') for audit.

    Returns Open/High/Low/Close/Volume/Open Interest indexed by tz-naive Date
    (plus 'Delivery Month' if the producer carried it, plus 'Volume_Source' when
    volume='reconstructed'), or empty if absent.
    """
    if volume not in ("front", "reconstructed"):
        raise ValueError(f"volume must be 'front' or 'reconstructed', got {volume!r}")

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

    keep = list(_COLS)
    if volume == "reconstructed":
        # Swap in reconstructed volume with a per-row raw fall-back. The producer
        # already writes Volume_Reconstructed==Volume on 'raw' rows, so reading it
        # is fall-back-safe where the column exists; guard for older/partial data
        # (pre-reconstruction parquet, or a stray NaN) by filling from front-month.
        if "Volume_Reconstructed" in df.columns:
            df["Volume"] = df["Volume_Reconstructed"].where(
                df["Volume_Reconstructed"].notna(), df["Volume"])
            df["Volume_Source"] = (
                df["Volume_Source"] if "Volume_Source" in df.columns else "reconstructed")
        else:
            # Store predates reconstruction → everything is front-month.
            df["Volume_Source"] = "raw"
        keep = keep + ["Volume_Source"]

    if "Delivery Month" in df.columns:
        keep = keep + ["Delivery Month"]
    return df[keep].copy()


def roll_dates(symbol: str, adjustment: str = "backadj") -> pd.DatetimeIndex:
    """Exact roll dates = days where the underlying Delivery Month changes.
    Empty if the producer didn't carry 'Delivery Month'."""
    df = get_prices(symbol, adjustment)
    if df.empty or "Delivery Month" not in df.columns:
        return pd.DatetimeIndex([])
    changed = df["Delivery Month"].ne(df["Delivery Month"].shift())
    return df.index[changed.fillna(False)]
