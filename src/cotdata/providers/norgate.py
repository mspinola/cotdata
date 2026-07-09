"""Norgate price producer — RUNS ON WINDOWS (Norgate Data Updater running +
`norgatedata`). The active EOD source: exchange settlement close, deep history,
back-adjusted (gap-free, shape-preserving) continuous contracts.

ADJUSTMENT (verified 2026-07 via test_adjustment.py): Norgate selects continuous
adjustment by SYMBOL SUFFIX, not the stock_price_adjustment_setting kwarg. The
BASE symbol '&ES' is UNADJUSTED (shows real calendar-spread gaps at each roll,
e.g. +146 pts at the 2026-06 Jun→Sep roll). '&ES_CCB' is BACK-ADJUSTED (gaps
stitched out). A close-based stop needs the gap-free series → we fetch _CCB.
"""
import pandas as pd

from ..registry import REGISTRY, all_symbols
from .. import store

CCB_SUFFIX = "_CCB"  # Norgate "Continuous Contract Back-adjusted"

_COLMAP = {
    "Open": "Open", "High": "High", "Low": "Low", "Close": "Close",
    "Volume": "Volume", "Open Interest": "Open Interest",
    "Delivery Month": "Delivery Month",   # kept → exact roll detection downstream
}


def fetch(internal_symbol: str, start: str = "1970-01-01") -> pd.DataFrame:
    """Fetch Norgate back-adjusted continuous bars: settlement close, gap-free."""
    import norgatedata  # imported lazily; only present on the Windows producer
    ng_sym = REGISTRY[internal_symbol].norgate + CCB_SUFFIX  # "&ES" → "&ES_CCB"
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
