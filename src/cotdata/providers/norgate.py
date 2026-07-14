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
# If roll-day overnight moves exceed this multiple of the normal-day median, the
# series looks UNADJUSTED (calendar-spread gaps not stitched). Self-calibrating
# per symbol, so it works across products with different spread magnitudes.
ROLL_GAP_RATIO_WARN = 1.5

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


def _check_roll_gaps(internal_symbol: str, df: pd.DataFrame) -> bool:
    """Warn if roll-day overnight moves ≫ normal-day moves — the signature of an
    UNADJUSTED continuous. Returns True if it looks unadjusted. Self-calibrating:
    compares each symbol's roll-day moves to its own non-roll baseline."""
    if "Delivery Month" not in df.columns or len(df) < 60:
        return False
    dm = df["Delivery Month"]
    roll = dm.ne(dm.shift()) & dm.shift().notna()
    if int(roll.sum()) < 8:
        return False
    overnight = (df["Close"] - df["Close"].shift(1)).abs()
    roll_med = overnight[roll].median()
    nonroll_med = overnight[~roll].median()
    if nonroll_med and roll_med > ROLL_GAP_RATIO_WARN * nonroll_med:
        print(f"  ⚠️  {internal_symbol}: roll-day moves {roll_med:.1f} vs normal {nonroll_med:.1f} "
              f"({roll_med / nonroll_med:.1f}x) — series looks UNADJUSTED. Expected the _CCB "
              f"back-adjusted symbol; a close-based stop would false-trigger on roll gaps.")
        return True
    return False


def update(symbols=None) -> None:
    """Fetch + write to the store for the given internal symbols (backadj only)."""
    syms = symbols or [s.internal for s in all_symbols()]
    for sym in syms:
        try:
            out = fetch(sym)
            _check_roll_gaps(sym, out)  # sanity: warn (don't block) if it looks unadjusted
            store.write_prices(sym, "backadj", out, source="norgate")
            print(f"{sym:5s}: {len(out):6d} bars -> store")
        except Exception as e:  # noqa: BLE001
            print(f"{sym:5s}: FAILED — {e}")


def get_symbol_metadata(internal_symbol: str) -> dict | None:
    """Fetch contract specifications for a single continuous futures symbol."""
    import norgatedata  # imported lazily
    ng_sym = REGISTRY[internal_symbol].norgate + CCB_SUFFIX

    data = {'Symbol': internal_symbol, 'Norgate_Symbol': ng_sym}
    try:
        data['Name'] = norgatedata.security_name(ng_sym)
    except Exception:
        data['Name'] = None
    try:
        data['Exchange'] = norgatedata.exchange_name(ng_sym)
    except Exception:
        data['Exchange'] = None
    try:
        data['Group'] = norgatedata.classification_at_level(
            ng_sym,
            schemename='NorgateDataFuturesClassification',
            classificationresulttype='Name',
            level=1,
        )
    except Exception:
        data['Group'] = None
    try:
        data['Contract Size'] = norgatedata.point_value(ng_sym)
    except Exception:
        data['Contract Size'] = None
    try:
        data['Tick Size'] = norgatedata.tick_size(ng_sym)
    except Exception:
        data['Tick Size'] = None

    ts = data['Tick Size']
    cs = data['Contract Size']
    data['Tick Value'] = (ts * cs) if (ts is not None and cs is not None) else None
    data['Point Value'] = cs

    try:
        data['Currency'] = norgatedata.currency(ng_sym)
    except Exception:
        data['Currency'] = None
    try:
        data['Margin'] = norgatedata.margin(ng_sym)
    except Exception:
        data['Margin'] = None

    return data


def update_metadata(symbols=None) -> None:
    """Fetch and write contract specifications (metadata) to the store."""
    import concurrent.futures
    syms = symbols or [s.internal for s in all_symbols()]
    
    print(f"Fetching metadata for {len(syms)} symbols...")
    metadata_rows = []
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        futs = {pool.submit(get_symbol_metadata, s): s for s in syms}
        for f in concurrent.futures.as_completed(futs):
            result = f.result()
            if result:
                metadata_rows.append(result)

    if metadata_rows:
        df = pd.DataFrame(metadata_rows)
        # Ensure consistent column ordering and sorting
        df = df.sort_values("Symbol").reset_index(drop=True)
        store.write_metadata(df, source="norgate")
        print(f"Successfully wrote metadata for {len(df)} symbols -> store")
    else:
        print("No metadata fetched.")
