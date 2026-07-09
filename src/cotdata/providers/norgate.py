"""Norgate price producer — RUNS ON WINDOWS (Norgate Data Updater running +
`norgatedata`). The active EOD source: exchange settlement close, deep history,
arithmetic back-adjusted (gap-free, shape-preserving) continuous contracts.

For futures, Norgate's price_timeseries() provides a single Close series which
is back-adjusted and gap-free. There is no separate unadjusted option in the API.
"""
import pandas as pd

from ..registry import REGISTRY, all_symbols
from .. import store

_COLMAP = {
    "Open": "Open", "High": "High", "Low": "Low", "Close": "Close",
    "Volume": "Volume", "Open Interest": "Open Interest",
    "Delivery Month": "Delivery Month",   # kept → exact roll detection downstream
}


def fetch(internal_symbol: str, start: str = "1970-01-01") -> pd.DataFrame:
    """Fetch Norgate continuous bars: settlement close, back-adjusted, gap-free."""
    import norgatedata  # imported lazily; only present on the Windows producer
    ng_sym = REGISTRY[internal_symbol].norgate  # e.g., "&ES"
    df = norgatedata.price_timeseries(
        ng_sym,
        padding_setting=norgatedata.PaddingType.NONE,
        timeseriesformat="pandas-dataframe",
        start_date=start,
    )
    df = df.rename(columns=_COLMAP)
    keep = [c for c in _COLMAP.values() if c in df.columns]
    out = df[keep].copy()
    out.index = pd.to_datetime(out.index).tz_localize(None).normalize()
    out.index.name = "Date"
    return out.sort_index()


def update(symbols=None) -> None:
    """Fetch + write to the store for the given internal symbols (backadj only)."""
    syms = symbols or [s.internal for s in all_symbols()]
    for sym in syms:
        try:
            out = fetch(sym)
            store.write_prices(sym, "backadj", out, source="norgate")
            print(f"{sym:5s}: {len(out):6d} bars -> store")
        except Exception as e:  # noqa: BLE001
            print(f"{sym:5s}: FAILED — {e}")
