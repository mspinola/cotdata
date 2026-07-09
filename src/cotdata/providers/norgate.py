"""Norgate price producer — RUNS ON WINDOWS (Norgate Data Updater running +
`norgatedata`). The active EOD source: exchange settlement close, deep history,
arithmetic back-adjusted (gap-free, shape-preserving) continuous contracts.
"""
import pandas as pd

from ..registry import REGISTRY, all_symbols
from .. import store


_COLMAP = {
    "Open": "Open", "High": "High", "Low": "Low", "Close": "Close",
    "Volume": "Volume", "Open Interest": "Open Interest",
    "Delivery Month": "Delivery Month",   # kept → exact roll detection downstream
}


def fetch(internal_symbol: str, adjustment: str, start: str = "1970-01-01") -> pd.DataFrame:
    """Fetch Norgate continuous bars. price_timeseries returns both Close (backadj)
    and Unadjusted Close; extract the one requested.
    """
    import norgatedata  # imported lazily; only present on the Windows producer
    ng_sym = REGISTRY[internal_symbol].norgate  # e.g., "&ES"
    df = norgatedata.price_timeseries(
        ng_sym,
        padding_setting=norgatedata.PaddingType.NONE,
        timeseriesformat="pandas-dataframe",
        start_date=start,
    )
    # Rename Close based on adjustment: backadj stays Close; unadj uses Unadjusted Close
    if adjustment == "unadj" and "Unadjusted Close" in df.columns:
        df = df.rename(columns={"Unadjusted Close": "Close"})
    elif adjustment == "unadj":
        raise ValueError(f"Unadjusted Close not in Norgate output for {internal_symbol}")

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
