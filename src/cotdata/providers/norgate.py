"""Norgate price producer — RUNS ON WINDOWS (Norgate Data Updater running +
`norgatedata`). The active EOD source: exchange settlement close, deep history,
arithmetic back-adjusted (gap-free, shape-preserving) continuous contracts.

Migrated from cot-analyzer/scripts/norgate_export.py. Two Norgate-API specifics
still need confirming against your norgatedata version (marked VERIFY).
"""
import pandas as pd

from ..registry import REGISTRY, all_symbols
from .. import store

# VERIFY against norgatedata docs: how futures back-adjustment is selected
# (distinct symbols vs a price_timeseries kwarg). Fill both so each series is right.
_ADJ_KWARGS = {
    "backadj": dict(),   # e.g. '&ES' already back-adjusted by default
    "unadj":   dict(),   # e.g. dict(stock_price_adjustment_setting=norgatedata.StockPriceAdjustmentType.NONE)
}

_COLMAP = {
    "Open": "Open", "High": "High", "Low": "Low", "Close": "Close",
    "Volume": "Volume", "Open Interest": "Open Interest",
    "Delivery Month": "Delivery Month",   # kept → exact roll detection downstream
}


def fetch(internal_symbol: str, adjustment: str, start: str = "1970-01-01") -> pd.DataFrame:
    import norgatedata  # imported lazily; only present on the Windows producer
    ng_sym = REGISTRY[internal_symbol].norgate
    df = norgatedata.price_timeseries(
        ng_sym,
        padding_setting=norgatedata.PaddingType.NONE,
        timeseriesformat="pandas-dataframe",
        start_date=start,
        **_ADJ_KWARGS[adjustment],
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
