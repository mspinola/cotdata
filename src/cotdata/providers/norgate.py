"""Norgate price producer — RUNS ON WINDOWS (Norgate Data Updater running +
`norgatedata`). The active EOD source: exchange settlement close, deep history,
arithmetic back-adjusted (gap-free, shape-preserving) continuous contracts.
"""
import pandas as pd

from ..registry import REGISTRY, all_symbols
from .. import store


def _get_adj_kwargs(adjustment: str) -> dict:
    """Back-adjustment selection for norgatedata.price_timeseries.

    TOTALRETURN (default) = arithmetic back-adjusted (gap-free, shape-preserving).
    UNADJUSTED = unadjusted (for position sizing / absolute price).
    """
    import norgatedata
    if adjustment == "backadj":
        return dict()  # TOTALRETURN is the default
    elif adjustment == "unadj":
        return dict(stock_price_adjustment_setting=norgatedata.StockPriceAdjustmentType.UNADJUSTED)
    else:
        raise ValueError(f"Unknown adjustment: {adjustment}")

_COLMAP = {
    "Open": "Open", "High": "High", "Low": "Low", "Close": "Close",
    "Volume": "Volume", "Open Interest": "Open Interest",
    "Delivery Month": "Delivery Month",   # kept → exact roll detection downstream
}


def fetch(internal_symbol: str, adjustment: str, start: str = "1970-01-01") -> pd.DataFrame:
    import norgatedata  # imported lazily; only present on the Windows producer
    ng_sym = REGISTRY[internal_symbol].norgate.lstrip("&")  # Norgate symbols have no & prefix
    df = norgatedata.price_timeseries(
        ng_sym,
        padding_setting=norgatedata.PaddingType.NONE,
        timeseriesformat="pandas-dataframe",
        start_date=start,
        **_get_adj_kwargs(adjustment),
    )
    df = df.rename(columns=_COLMAP)
    keep = [c for c in _COLMAP.values() if c in df.columns]
    out = df[keep].copy()
    out.index = pd.to_datetime(out.index).tz_localize(None).normalize()
    out.index.name = "Date"
    return out.sort_index()


def update(symbols=None, adjustments=("backadj", "unadj")) -> None:
    """Fetch + write to the store for the given internal symbols."""
    syms = symbols or [s.internal for s in all_symbols()]
    for sym in syms:
        for adj in adjustments:
            try:
                out = fetch(sym, adj)
                store.write_prices(sym, adj, out, source="norgate")
                print(f"{sym:5s} {adj:8s}: {len(out):6d} bars -> store")
            except Exception as e:  # noqa: BLE001
                print(f"{sym:5s} {adj:8s}: FAILED — {e}")
