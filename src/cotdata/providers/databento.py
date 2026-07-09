"""Databento provider — DORMANT. NOT in the live EOD path (Norgate replaced it).

Retained deliberately for:
  1. the intraday news-failure work (Norgate has no intraday; Databento is the
     source for the release-window reaction refinement of the CMR trigger), and
  2. cross-checking Norgate's settlement close against Databento statistics.

Ported from cot-analyzer/src/core/market_data.py (GLBX.MDP3 ohlcv-1d + statistics).
The hard-won part preserved here is the STATISTICS extraction — Open Interest
(stat_type 9) and the settlement fix: use StatType.SETTLEMENT_PRICE == 3 (NOT 7 =
LOWEST_OFFER, which overwrote Close with the day's lowest offer), dated by ts_ref
(the session it applies to) not ts_event (final settle is disseminated next morning).

Standalone-adapted for cotdata: lazy `import databento` (behind the [databento]
extra), symbols from the registry, cache under $COTDATA_STORE/_cache/databento.
Requires DATABENTO_API_KEY. The daily path below is superseded by Norgate; the
intraday work will reuse this statistics logic against ohlcv-1h / trades schemas.
"""
import logging
import os
import sys
import time
import warnings
from pathlib import Path

import pandas as pd

from .. import config
from ..registry import all_symbols

logger = logging.getLogger(__name__)

# Symbols Databento GLBX.MDP3 doesn't carry as .n.0 continuous (fall back to yfinance).
_DATABENTO_UNSUPPORTED = {"CC", "OJ", "SB", "KC", "LBR", "CT"}
_API_LAST_CHECKED = {}   # in-memory throttle to avoid hammering the API


def _cache_dir() -> Path:
    d = config.store_root() / "_cache" / "databento"
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_batch_backfill(symbols: list) -> None:
    """Submit a Databento batch job to download massive historical daily data."""
    import databento as db
    db_key = os.environ.get("DATABENTO_API_KEY")
    if not db_key:
        raise ValueError("DATABENTO_API_KEY is missing. Cannot run batch backfill.")
    client = db.Historical(key=db_key)

    db_syms, sym_map = [], {}
    for sym in symbols:
        db_sym = f"{sym}.n.0"
        db_syms.append(db_sym)
        sym_map[db_sym] = sym

    try:
        res = client.symbology.resolve(
            dataset="GLBX.MDP3", symbols=db_syms, stype_in="continuous",
            stype_out="instrument_id", start_date="2010-06-06",
            end_date=pd.Timestamp.now().strftime("%Y-%m-%d"),
        )
        not_found = res.get("not_found", [])
        if not_found:
            db_syms = [s for s in db_syms if s not in not_found]
    except Exception as e:  # noqa: BLE001
        logger.warning("Symbol resolution failed, submitting anyway: %s", e)
    if not db_syms:
        raise ValueError("No valid Databento symbols remain after validation.")

    job = client.batch.submit_job(
        dataset="GLBX.MDP3", symbols=db_syms, stype_in="continuous", schema="ohlcv-1d",
        start="2010-06-06", end=pd.Timestamp.now().strftime("%Y-%m-%d"),
        encoding="csv", split_symbols=False, delivery="download",
    )
    job_id = job.get("id")
    if not job_id:
        raise RuntimeError("Failed to submit batch job: no job ID returned.")

    while True:
        time.sleep(30)
        my_job = next((j for j in client.batch.list_jobs() if j.get("id") == job_id), None)
        if not my_job:
            continue
        state = my_job.get("state", "unknown")
        if state == "done":
            break
        if state == "expired" or "fail" in state.lower():
            raise RuntimeError(f"Batch job failed or expired: {my_job}")

    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        paths = client.batch.download(job_id=job_id, output_dir=tmpdir)
        for path in paths:
            if not str(path).endswith(".csv"):
                continue
            df_raw = pd.read_csv(path)
            if "symbol" not in df_raw.columns:
                continue
            for db_sym, df_sym in df_raw.groupby("symbol"):
                internal_sym = sym_map.get(db_sym)
                if not internal_sym:
                    continue
                df_sym = df_sym.rename(columns={
                    "ts_event": "Date", "open": "Open", "high": "High",
                    "low": "Low", "close": "Close", "volume": "Volume"})
                df_sym["Date"] = pd.to_datetime(df_sym["Date"]).dt.tz_localize(None).dt.normalize()
                df_sym = df_sym.set_index("Date")
                keep = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in df_sym.columns]
                df_clean = df_sym[keep].copy()
                df_clean["Open Interest"] = float("nan")
                df_clean = df_clean.dropna(subset=["Close"]).sort_index()
                df_clean.to_parquet(_cache_dir() / f"{internal_sym}_daily.parquet")


def fetch_daily_ohlc(symbol: str, start_date: str = "2000-01-01",
                     force_refresh: bool = False, price_type: str = "close") -> pd.DataFrame:
    """Daily OHLC + Open Interest via Databento (GLBX.MDP3 ohlcv-1d + statistics),
    append-only cache; yfinance fallback for _DATABENTO_UNSUPPORTED. price_type
    'settlement' pulls stat_type 3 dated by ts_ref."""
    import databento as db
    display_sym = symbol
    _pt_suffix = "" if price_type == "close" else f"_{price_type}"
    cache_path = _cache_dir() / f"{symbol}_daily{_pt_suffix}.parquet"

    local_df = pd.DataFrame()
    if cache_path.exists() and not force_refresh:
        try:
            cached = pd.read_parquet(cache_path)
            if not cached.empty:
                has_oi = "Open Interest" in cached.columns and not cached["Open Interest"].isna().all()
                if has_oi or symbol in _DATABENTO_UNSUPPORTED:
                    local_df = cached
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to read cache for %s: %s", display_sym, e)

    if local_df.empty:
        fetch_start = "2000-01-01"
    else:
        last_date = local_df.index.max()
        today = pd.Timestamp.now().normalize()
        if last_date >= today - pd.Timedelta(days=1):
            return local_df
        if "--fast" in sys.argv:
            return local_df
        now = time.time()
        if not force_refresh and (now - _API_LAST_CHECKED.get(symbol, 0) < 3600):
            return local_df
        _API_LAST_CHECKED[symbol] = now
        fetch_start = (last_date + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    fetch_end = (pd.Timestamp.now() - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    if pd.Timestamp(fetch_start) >= pd.Timestamp(fetch_end):
        return local_df

    new_df = pd.DataFrame()
    db_key = os.environ.get("DATABENTO_API_KEY")
    if pd.Timestamp(fetch_start) < pd.Timestamp("2010-06-06"):
        fetch_start = "2010-06-06"  # GLBX.MDP3 history floor
    db_success = False

    if db_key and symbol not in _DATABENTO_UNSUPPORTED:
        for attempt in range(1, 4):
            try:
                client = db.Historical(key=db_key)
                db_sym = f"{symbol}.n.0"
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", message=".*No data found.*")
                    warnings.filterwarnings("ignore", message=".*did not resolve.*")
                    data = client.timeseries.get_range(
                        dataset="GLBX.MDP3", symbols=[db_sym], stype_in="continuous",
                        schema="ohlcv-1d", start=fetch_start, end=fetch_end)
                raw = data.to_df()
                if not raw.empty:
                    raw = raw.reset_index().rename(columns={
                        "ts_event": "Date", "open": "Open", "high": "High",
                        "low": "Low", "close": "Close", "volume": "Volume"})
                    raw["Date"] = pd.to_datetime(raw["Date"]).dt.tz_localize(None).dt.normalize()
                    raw = raw.set_index("Date")
                    keep = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in raw.columns]
                    new_df = raw[keep].copy()
                    try:
                        with warnings.catch_warnings():
                            warnings.filterwarnings("ignore", message=".*No data found.*")
                            warnings.filterwarnings("ignore", message=".*did not resolve.*")
                            stat_raw = client.timeseries.get_range(
                                dataset="GLBX.MDP3", symbols=[db_sym], stype_in="continuous",
                                schema="statistics", start=fetch_start, end=fetch_end).to_df()
                        if not stat_raw.empty:
                            stat_raw = stat_raw.reset_index()
                            if "stat_type" in stat_raw.columns:
                                new_df = new_df.reset_index()
                                date_col = "ts_event"
                                # Open Interest = stat_type 9
                                oi_df = stat_raw[stat_raw["stat_type"] == 9].copy()
                                if not oi_df.empty:
                                    oi_df["Date"] = pd.to_datetime(oi_df[date_col]).dt.tz_localize(None).dt.normalize()
                                    oi_df["Open Interest"] = oi_df["quantity"] if "quantity" in oi_df.columns else oi_df["price"]
                                    oi_df = oi_df.groupby("Date")["Open Interest"].last().reset_index()
                                    new_df = pd.merge(new_df, oi_df, on="Date", how="left")
                                # Settlement = stat_type 3 (NOT 7=LOWEST_OFFER), dated by ts_ref
                                if price_type == "settlement":
                                    stl_df = stat_raw[stat_raw["stat_type"] == 3].copy()
                                    if not stl_df.empty:
                                        settle_dt = "ts_ref" if "ts_ref" in stl_df.columns else date_col
                                        stl_df["Date"] = pd.to_datetime(stl_df[settle_dt]).dt.tz_localize(None).dt.normalize()
                                        stl_df["Settlement"] = stl_df["price"]
                                        stl_df = stl_df.groupby("Date")["Settlement"].last().reset_index()
                                        new_df = pd.merge(new_df, stl_df, on="Date", how="left")
                                        if "Settlement" in new_df.columns:
                                            new_df["Close"] = new_df["Settlement"].combine_first(new_df["Close"])
                                            new_df = new_df.drop(columns=["Settlement"])
                                new_df = new_df.set_index("Date")
                    except Exception as e:  # noqa: BLE001
                        logger.warning("Could not fetch Open Interest for %s: %s", display_sym, e)
                    if "Open Interest" not in new_df.columns:
                        new_df["Open Interest"] = float("nan")
                    db_success = True
                    break
                db_success = True  # empty (holiday) — don't trigger fallback
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning("Databento download failed for %s (%d/3): %s", display_sym, attempt, exc)
                if attempt < 3:
                    time.sleep(5)

    if not db_success and symbol in _DATABENTO_UNSUPPORTED:
        try:
            import yfinance as yf
            yf_df = yf.download(f"{symbol}=F", start=fetch_start,
                                end=pd.Timestamp.now().strftime("%Y-%m-%d"), progress=False)
            if not yf_df.empty:
                yf_df = yf_df.reset_index()
                if isinstance(yf_df.columns, pd.MultiIndex):
                    yf_df.columns = [c[0] for c in yf_df.columns]
                yf_df["Date"] = pd.to_datetime(yf_df["Date"]).dt.tz_localize(None).dt.normalize()
                yf_df = yf_df.set_index("Date")
                keep = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in yf_df.columns]
                new_df = yf_df[keep].copy()
                new_df["Open Interest"] = float("nan")
        except Exception as yf_exc:  # noqa: BLE001
            logger.error("yfinance fallback failed for %s: %s", display_sym, yf_exc)

    if not local_df.empty and not new_df.empty:
        new_df = new_df[new_df.index > local_df.index.max()]
    if new_df.empty:
        return local_df
    new_df = new_df.dropna(subset=["Close"])
    combined = pd.concat([local_df, new_df]) if not local_df.empty else new_df
    combined = combined[~combined.index.duplicated(keep="last")].sort_index()
    combined.to_parquet(cache_path)
    return combined


def update_all_daily_prices(force_refresh: bool = False) -> None:
    """Refresh the Databento price cache for all registry symbols (dormant path)."""
    to_batch = []
    for s in all_symbols():
        symbol = s.internal
        raw_cache_path = _cache_dir() / f"{symbol}_daily.parquet"
        needs = force_refresh or not raw_cache_path.exists()
        if not needs:
            try:
                if pd.read_parquet(raw_cache_path).empty:
                    needs = True
            except Exception:  # noqa: BLE001
                needs = True
        if needs:
            to_batch.append(symbol)
        else:
            try:
                fetch_daily_ohlc(symbol)
            except Exception as e:  # noqa: BLE001
                logger.error("Failed daily gap fetch for %s: %s", symbol, e)
    if to_batch:
        try:
            run_batch_backfill(to_batch)
        except Exception as e:  # noqa: BLE001
            logger.error("Batch backfill failed: %s", e)
