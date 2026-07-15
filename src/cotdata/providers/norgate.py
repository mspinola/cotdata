"""Norgate price producer — RUNS ON WINDOWS (Norgate Data Updater running +
`norgatedata`). The active EOD source: exchange settlement close, deep history,
back-adjusted (gap-free, shape-preserving) continuous contracts.

ADJUSTMENT (verified 2026-07 via test_adjustment.py): Norgate selects continuous
adjustment by SYMBOL SUFFIX, not the stock_price_adjustment_setting kwarg. The
BASE symbol '&ES' is UNADJUSTED (shows real calendar-spread gaps at each roll,
e.g. +146 pts at the 2026-06 Jun→Sep roll). '&ES_CCB' is BACK-ADJUSTED (gaps
stitched out). A close-based stop needs the gap-free series → we fetch _CCB.
"""
from __future__ import annotations  # PEP 604 unions (dict | None) on Python 3.9

import pandas as pd
import numpy as np
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

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

MONTH_CODES = {'F': 1, 'G': 2, 'H': 3, 'J': 4, 'K': 5, 'M': 6, 'N': 7, 'Q': 8, 'U': 9, 'V': 10, 'X': 11, 'Z': 12}


def _reconstruct_volume(internal_symbol: str, continuous_df: pd.DataFrame, adjustment: str,
                        full: bool = False) -> pd.DataFrame:
    """Calculate FirstVolume, SecondVolume, and Volume_Reconstructed.
    Uses incremental fetching based on the last successful Volume_Reconstructed date.
    Returns continuous_df with the additive columns attached.

    full=True recomputes the ENTIRE history from scratch, ignoring the incremental
    window. Needed when the reconstruction *logic* changes (not just new data): the
    trailing-60-day window would otherwise leave old rows on the previous algorithm.
    """
    import norgatedata

    # 1. Gap-aware incremental bounds
    existing_df = store.read_prices(internal_symbol, adjustment)
    last_date = pd.Timestamp("1970-01-01")
    if not full and "Volume_Reconstructed" in existing_df.columns:
        valid_dates = existing_df.dropna(subset=["Volume_Reconstructed"]).index
        if len(valid_dates) > 0:
            # Recompute trailing 60 days to catch late data & bridge partial failures
            last_date = valid_dates.max() - pd.Timedelta(days=60)
            
    # Base Norgate symbol (e.g. "&ES" -> "ES")
    base_sym = REGISTRY[internal_symbol].norgate.lstrip("&").split("_")[0]
    
    # 2. Find needed individual contracts
    all_futures = norgatedata.database_symbols('Futures')
    pattern = re.compile(rf"^{re.escape(base_sym)}-(\d{{4}})([FGHJKMNQUVXZ])$")
    
    needed_contracts = []
    for sym in all_futures:
        m = pattern.match(sym)
        if m:
            year, month = int(m.group(1)), MONTH_CODES[m.group(2)]
            expiry_date = pd.Timestamp(year=year, month=month, day=1) + pd.DateOffset(months=1)
            if expiry_date >= last_date:
                needed_contracts.append(sym)
                
    # 3. Fallback: if no individual contracts (e.g., crypto, ICE softs)
    if not needed_contracts:
        res = continuous_df.copy()
        res["FirstVolume"] = np.nan
        res["SecondVolume"] = np.nan
        res["FirstContract"] = ""
        res["SecondContract"] = ""
        res["Volume_Reconstructed"] = res["Volume"]
        res["Volume_Source"] = "raw"
        return res

    # 4. Fetch OHLCV for needed contracts
    frames = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futs = {
            pool.submit(
                norgatedata.price_timeseries, 
                c, 
                padding_setting=norgatedata.PaddingType.NONE, 
                timeseriesformat="pandas-dataframe", 
                start_date=last_date.strftime("%Y-%m-%d")
            ): c 
            for c in needed_contracts
        }
        for f in as_completed(futs):
            c = futs[f]
            try:
                df_c = f.result()
                if "Date" not in df_c.columns:
                    df_c = df_c.reset_index()
                if not df_c.empty:
                    df_c["Symbol"] = c
                    frames.append(df_c[["Date", "Volume", "Symbol"]])
            except Exception as e:
                print(f"  ⚠️  Failed to fetch individual contract {c}: {e}")

    res = continuous_df.copy()

    if not frames:
        res["FirstVolume"] = np.nan
        res["SecondVolume"] = np.nan
        res["FirstContract"] = ""
        res["SecondContract"] = ""
        res["Volume_Reconstructed"] = res["Volume"]
        res["Volume_Source"] = "raw"
        return res
        
    all_indiv = pd.concat(frames, ignore_index=True)
    all_indiv['Date'] = pd.to_datetime(all_indiv['Date']).dt.tz_localize(None).dt.normalize()
    
    # 5. Contract identification: First / Second = the two HIGHEST-VOLUME contracts
    # trading that day, NOT the two nearest by expiry. Products with serial months
    # around a bi-monthly liquid cycle (e.g. GC, SI) carry almost no volume in the
    # nearest serial month, so an expiry-order pick would sum near-empty contracts
    # and badly understate true volume. Rank by descending volume; ties break by
    # nearest expiry (columns are pre-sorted by expiry and the sort is stable);
    # NaN (contract not trading that day) sorts last.
    pivot = all_indiv.pivot(index="Date", columns="Symbol", values="Volume")

    def get_expiry(sym):
        m = pattern.match(sym)
        return pd.Timestamp(year=int(m.group(1)), month=MONTH_CODES[m.group(2)], day=1)

    sorted_cols = sorted(pivot.columns, key=get_expiry)
    pivot = pivot[sorted_cols]

    vol_array = pivot.values
    rank_key = np.where(np.isnan(vol_array), -np.inf, vol_array)
    order = np.argsort(-rank_key, axis=1, kind='stable')
    compressed = np.take_along_axis(vol_array, order, axis=1)
    names_arr = np.array(pivot.columns)
    
    rec_df = pd.DataFrame(index=pivot.index)
    
    num_cols = compressed.shape[1]
    if num_cols > 0:
        rec_df['FirstVolume'] = compressed[:, 0]
        rec_df['FirstContract'] = np.where(np.isnan(compressed[:, 0]), '', names_arr[order[:, 0]])
    else:
        rec_df['FirstVolume'] = np.nan
        rec_df['FirstContract'] = ''
        
    if num_cols > 1:
        rec_df['SecondVolume'] = compressed[:, 1]
        rec_df['SecondContract'] = np.where(np.isnan(compressed[:, 1]), '', names_arr[order[:, 1]])
    else:
        rec_df['SecondVolume'] = np.nan
        rec_df['SecondContract'] = ''
        
    rec_df['Volume_Reconstructed'] = rec_df['FirstVolume'].fillna(0) + rec_df['SecondVolume'].fillna(0)
    rec_df.loc[rec_df['FirstVolume'].isna() & rec_df['SecondVolume'].isna(), 'Volume_Reconstructed'] = np.nan
    rec_df['Volume_Source'] = "reconstructed"
    
    # 6. Merge the newly reconstructed subset into the existing history
    for col in ["FirstVolume", "SecondVolume", "FirstContract", "SecondContract", "Volume_Reconstructed", "Volume_Source"]:
        if col not in res.columns:
            if col in existing_df.columns:
                res[col] = existing_df[col]
            else:
                res[col] = "" if col in ("FirstContract", "SecondContract", "Volume_Source") else np.nan
                
    res.update(rec_df)
    
    common_idx = res.index.intersection(rec_df.index)
    for col in ["FirstContract", "SecondContract"]:
        res.loc[common_idx, col] = rec_df.loc[common_idx, col]

    mask = res['Volume_Reconstructed'].isna()
    res.loc[mask, 'Volume_Reconstructed'] = res.loc[mask, 'Volume']
    res.loc[mask, 'Volume_Source'] = "raw"
    
    return res


def fetch(internal_symbol: str, adjustment: str = "backadj", start: str = "1970-01-01") -> pd.DataFrame:
    """Fetch Norgate continuous bars: settlement close."""
    import norgatedata  # imported lazily; only present on the Windows producer
    ng_sym = REGISTRY[internal_symbol].norgate
    if adjustment == "backadj":
        ng_sym += CCB_SUFFIX
        
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


def update(symbols=None, full: bool = False) -> None:
    """Fetch + write to the store for the given internal symbols (backadj and unadj).

    full=True forces a complete rebuild of the reconstructed-volume columns rather
    than the trailing-60-day incremental update — use it after a reconstruction
    logic change so old rows are recomputed under the new algorithm.
    """
    import time
    from .. import status

    syms = symbols or [s.internal for s in all_symbols()]
    prior = store.load_manifest().get("prices", {})  # to report per-symbol date deltas
    t0 = time.time()
    ok, failed, total_rows, newest = [], [], 0, None
    for sym in syms:
        try:
            # 1. Back-Adjusted
            out_backadj = fetch(sym, adjustment="backadj")
            _check_roll_gaps(sym, out_backadj)  # sanity: warn if backadj looks unadjusted

            # 2. Unadjusted (Raw calendar spreads)
            out_unadj = fetch(sym, adjustment="unadj")

            # 3. Volume Reconstruction (Additive)
            out_backadj = _reconstruct_volume(sym, out_backadj, "backadj", full=full)
            out_unadj = _reconstruct_volume(sym, out_unadj, "unadj", full=full)

            store.write_prices(sym, "backadj", out_backadj, source="norgate")
            store.write_prices(sym, "unadj", out_unadj, source="norgate")

            ok.append(sym)
            total_rows += len(out_backadj) + len(out_unadj)
            new = str(out_backadj.index.max().date()) if len(out_backadj) else "—"
            newest = max(newest, new) if newest else new
            was = (prior.get(f"{sym}_backadj") or {}).get("last_date")
            delta = new if (was is None or was == new) else f"{was} -> {new}"
            print(f"{sym:5s}: {len(out_backadj):6d} backadj, {len(out_unadj):6d} unadj  [{delta}]")
        except Exception as e:  # noqa: BLE001
            failed.append((sym, e))
            print(f"{sym:5s}: FAILED — {e}")

    print(status.run_summary("prices update", ok, failed, total_rows,
                             time.time() - t0, newest=newest))


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
