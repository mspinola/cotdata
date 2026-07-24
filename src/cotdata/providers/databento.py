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
import datetime as dt
import json
import logging
import os
import sys
import time
import warnings
from pathlib import Path
from typing import Optional

import pandas as pd

from .. import config, store
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


def fetch_daily_ohlc(symbol: str, start_date: Optional[str] = None,
                     force_refresh: bool = False, price_type: str = "close") -> pd.DataFrame:
    """Daily OHLC + Open Interest via Databento (GLBX.MDP3 ohlcv-1d + statistics),
    append-only cache; yfinance fallback for _DATABENTO_UNSUPPORTED. price_type
    'settlement' pulls stat_type 3 dated by ts_ref.

    The cache is always a single, from-inception, append-only series per
    symbol+price_type, shared across every caller — that is what makes repeated
    calls cheap. `start_date` therefore means two different things depending on
    whether it is the FIRST call for a symbol or not, and both are deliberate:

    * **Cold cache (symbol never fetched before):** `start_date` clamps the fetch
      floor (still no lower than the GLBX.MDP3 history floor 2010-06-06), so a
      narrow first-time query — e.g. "just the last 3 months" — actually costs a
      narrow API pull, not a from-2000 one.
    * **Warm cache (symbol already has some history):** `start_date` does NOT
      change what gets fetched — the top-up always resumes from the cache's
      `last_date + 1 day`, exactly as before. Letting a later, narrower
      `start_date` shrink the fetch would silently truncate a cache other
      callers already rely on being complete. `start_date` still filters what is
      RETURNED to this call, just not what is fetched or persisted.

    In both cases the returned frame never contains rows before `start_date` — the
    filter is applied uniformly to whatever the cache ends up holding. `None`
    (the default) is unbounded: full cached history, unfiltered, unchanged
    behaviour from before this parameter existed.

    One consequence of the "warm cache never shrinks its floor" rule: if a symbol's
    cache was FIRST populated by a narrow `start_date` (e.g. "since 2024"), a later
    call asking for `start_date="2010-01-01"` will NOT backfill 2010-2023 — the
    cache only ever grows forward. Not a concern for the one real caller today
    (`update_all_daily_prices`, which never passes `start_date` and always wants
    full history), but worth knowing before relying on this for a symbol multiple
    call sites touch with different windows.
    """
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

    def _filtered(df: pd.DataFrame) -> pd.DataFrame:
        if start_date and not df.empty:
            return df[df.index >= pd.Timestamp(start_date)]
        return df

    if local_df.empty:
        # Cold cache: start_date narrows the fetch floor (still API-cost-bounded
        # by the GLBX floor below), so a first-time narrow query is actually cheap.
        fetch_start = start_date if start_date else "2000-01-01"
    else:
        # Warm cache: start_date does NOT narrow this — see the docstring. It only
        # affects the return-value filter below.
        last_date = local_df.index.max()
        today = pd.Timestamp.now().normalize()
        if last_date >= today - pd.Timedelta(days=1):
            return _filtered(local_df)
        if "--fast" in sys.argv:
            return _filtered(local_df)
        now = time.time()
        if not force_refresh and (now - _API_LAST_CHECKED.get(symbol, 0) < 3600):
            return _filtered(local_df)
        _API_LAST_CHECKED[symbol] = now
        fetch_start = (last_date + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    fetch_end = (pd.Timestamp.now() - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    if pd.Timestamp(fetch_start) >= pd.Timestamp(fetch_end):
        return _filtered(local_df)

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
        return _filtered(local_df)
    new_df = new_df.dropna(subset=["Close"])
    combined = pd.concat([local_df, new_df]) if not local_df.empty else new_df
    combined = combined[~combined.index.duplicated(keep="last")].sort_index()
    combined.to_parquet(cache_path)          # cache always persists the FULL series
    return _filtered(combined)               # start_date only shapes what's returned


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


# ── Raw ingest (Stage 1): the paid, append-only landing store ────────────────
# ADR-0006: databento is a two-stage producer. Stage 1 (here) is the ONLY step
# that hits the paid API. It pulls raw .n.0 / .n.1 ohlcv-1d + statistics into an
# immutable, append-only raw store, keyed by fetched date range in a manifest so a
# re-run resumes from last_date+1 and never re-pulls a range already held. The free
# Stage 2 `build` (see ADR item 4) re-derives back-adjusted prices from these local
# files with no API cost. The raw store is PRODUCER-INTERNAL, not the consumer
# contract — keep it out of any store sync to consumers.

GLBX_HISTORY_FLOOR = "2010-06-06"   # earliest GLBX.MDP3 history
_FEEDS = (".n.0", ".n.1")           # front + second continuous (second gives the roll gap)
_SCHEMAS = ("ohlcv-1d", "statistics")


def raw_root() -> Path:
    """Producer-internal databento raw store: $COTDATA_DATABENTO_RAW if set, else a
    ``_raw/databento`` namespace under the cotdata store (leading underscore = not a
    consumer domain; exclude it from any consumer sync)."""
    env = os.environ.get("COTDATA_DATABENTO_RAW", "").strip()
    return Path(env) if env else (config.store_root() / "_raw" / "databento")


def _raw_path(symbol: str, feed: str, schema: str) -> Path:
    sub = "ohlcv" if schema == "ohlcv-1d" else "statistics"
    return raw_root() / sub / f"{symbol}{feed}.parquet"


def _ingest_manifest_path() -> Path:
    return raw_root() / "ingest_manifest.json"


def _load_ingest_manifest() -> dict:
    p = _ingest_manifest_path()
    return json.loads(p.read_text()) if p.exists() else {}


def _write_ingest_manifest(m: dict) -> None:
    p = _ingest_manifest_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(m, indent=2, sort_keys=True))
    os.replace(tmp, p)


def _to_naive(x):
    """tz-naive UTC for either tz-aware or naive datetime input (databento returns UTC).
    Handles both a DatetimeIndex (``.tz_convert``) and a Series (``.dt.tz_convert``)."""
    ts = pd.to_datetime(x, utc=True)
    return ts.dt.tz_convert(None) if isinstance(ts, pd.Series) else ts.tz_convert(None)


def _normalize(raw: pd.DataFrame, schema: str) -> pd.DataFrame:
    """Light bronze normalization: tz-naive timestamps, one row per day for ohlcv.
    Columns are otherwise preserved as databento returns them, so Stage 2 can
    re-extract settlement/OI/etc. without a re-fetch."""
    raw = raw.copy()
    if schema == "ohlcv-1d":
        raw.index = _to_naive(raw.index).normalize()
        raw.index.name = "Date"
        return raw[~raw.index.duplicated(keep="last")].sort_index()
    # statistics: flatten ts_event out of the index, keep every stat row.
    raw = raw.reset_index()
    for c in ("ts_event", "ts_ref"):
        if c in raw.columns:
            raw[c] = _to_naive(raw[c])
    return raw.drop_duplicates().reset_index(drop=True)


def _date_bounds(raw: pd.DataFrame, schema: str):
    if schema == "ohlcv-1d":
        return str(raw.index.min().date()), str(raw.index.max().date())
    if "ts_event" in raw.columns and len(raw):
        d = pd.to_datetime(raw["ts_event"])
        return str(d.min().date()), str(d.max().date())
    return None, None


def _append_raw(symbol: str, feed: str, schema: str, new_df: pd.DataFrame) -> None:
    path = _raw_path(symbol, feed, schema)
    existing = pd.read_parquet(path) if path.exists() else pd.DataFrame()
    combined = pd.concat([existing, new_df]) if not existing.empty else new_df
    if schema == "ohlcv-1d":
        combined = combined[~combined.index.duplicated(keep="last")].sort_index()
    else:
        combined = combined.drop_duplicates().reset_index(drop=True)
    store._atomic_write_parquet(combined, path)


def _client_from_env():
    import databento as db  # lazy — behind the [databento] extra
    key = os.environ.get("DATABENTO_API_KEY")
    if not key:
        raise RuntimeError("DATABENTO_API_KEY is not set; cannot ingest from databento.")
    return db.Historical(key=key)


def _fetch(client, dataset: str, dbsym: str, schema: str, start: str, end: str) -> pd.DataFrame:
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=".*No data found.*")
        warnings.filterwarnings("ignore", message=".*did not resolve.*")
        # Databento flags a handful of historically "degraded" sessions (e.g. 2014-06-11)
        # on every request. It's a known data-condition note, not an error, and it floods
        # the ingest/cron logs — quiet it.
        warnings.filterwarnings("ignore", message=".*reduced quality.*")
        data = client.timeseries.get_range(
            dataset=dataset, symbols=[dbsym], stype_in="continuous",
            schema=schema, start=start, end=end)
    return data.to_df()


def ingest(symbols=None, *, client=None, dataset="GLBX.MDP3", end=None,
           cold_start=GLBX_HISTORY_FLOOR) -> dict:
    """Fetch raw databento daily bars (.n.0 + .n.1) and statistics into the raw
    store, append-only. Resumes each (symbol, feed, schema) from its manifest
    last_date+1. Scoped to registry symbols that databento can serve (a non-null
    ``databento`` mapping); pass ``symbols`` to narrow further.

    `client` is injectable (databento.Historical-shaped) for tests; the default
    builds one from DATABENTO_API_KEY. Returns {kind, ok, symbols, rows}."""
    targets = [s for s in all_symbols()
               if s.databento and s.price_source in (None, "databento")
               and (symbols is None or s.internal in symbols)]
    if not targets:
        print("databento ingest: no databento-capable symbols"
              + (f" among {symbols}" if symbols else ""))
        return {"kind": "ingest_databento", "ok": True, "symbols": 0, "rows": 0}

    if client is None:
        client = _client_from_env()

    end = end or (pd.Timestamp.now().normalize() - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    manifest = _load_ingest_manifest()
    total_rows, failed = 0, 0

    for s in targets:
        for feed in _FEEDS:
            dbsym = f"{s.databento}{feed}"
            for schema in _SCHEMAS:
                key = f"{s.internal}{feed}:{schema}"
                rec = manifest.get(key, {})
                last = rec.get("last_date")
                start = ((pd.Timestamp(last) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
                         if last else cold_start)
                if pd.Timestamp(start) < pd.Timestamp(GLBX_HISTORY_FLOOR):
                    start = GLBX_HISTORY_FLOOR
                if pd.Timestamp(start) > pd.Timestamp(end):
                    continue  # already current
                try:
                    raw = _fetch(client, dataset, dbsym, schema, start, end)
                except Exception as e:  # noqa: BLE001 — databento/network is flaky
                    print(f"{s.internal}{feed} {schema}: databento fetch failed ({start}..{end}) — {e}")
                    failed += 1
                    continue
                if raw is None or raw.empty:
                    continue
                raw = _normalize(raw, schema)
                if raw.empty:
                    continue
                _append_raw(s.internal, feed, schema, raw)
                first, newest = _date_bounds(raw, schema)
                manifest[key] = {
                    "first_date": rec.get("first_date") or first,
                    "last_date": newest,
                    "n_rows": int(rec.get("n_rows", 0)) + len(raw),
                    "fetched_at": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
                }
                total_rows += len(raw)
                print(f"{s.internal}{feed} {schema}: +{len(raw):5d} rows ({start}..{newest}) -> raw store")

    _write_ingest_manifest(manifest)
    return {"kind": "ingest_databento", "ok": failed == 0, "symbols": len(targets), "rows": total_rows}


# ── Build (Stage 2): free, raw store -> back-adjusted store prices ────────────
# ADR-0006. Derives two series per symbol from the local raw store (no API cost):
#   unadj   — raw front continuous (.n.0), settlement close, roll gaps intact.
#   backadj — additive back-adjustment, Norgate's method exactly: at each roll the
#             gap = new_close - old_close on the roll date = n1_settle - n0_settle;
#             every price up to AND INCLUDING the roll date is shifted by the gap;
#             gaps accumulate back-to-front so the newest segment stays at real prices.
# Then store.write_prices(..., source='databento'). propadj is derived on read from
# unadj + backadj by the consumer API, unchanged.

_OHLCV_COLMAP = {"open": "Open", "high": "High", "low": "Low",
                 "close": "Close", "volume": "Volume"}
_OUT_COLS = ["Open", "High", "Low", "Close", "Volume", "Open Interest"]


def _read_ohlcv(symbol: str, feed: str) -> pd.DataFrame:
    p = _raw_path(symbol, feed, "ohlcv-1d")
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_parquet(p).rename(columns=_OHLCV_COLMAP)
    # instrument_id is the roll signal: for a continuous series databento's `symbol`
    # column is the constant alias ("ES.n.0"), while instrument_id changes to the new
    # contract at each roll. Keep both.
    keep = [c for c in ("Open", "High", "Low", "Close", "Volume", "instrument_id", "symbol")
            if c in df.columns]
    df = df[keep].copy()
    df.index = pd.to_datetime(df.index).normalize()
    df.index.name = "Date"
    return df[~df.index.duplicated(keep="last")].sort_index()


def _stat_series(symbol: str, feed: str, stat_type: int, date_col: str, value_col: str) -> pd.Series:
    """Daily series of a databento statistic — settlement (stat_type 3, dated by
    ts_ref, the session it applies to) or Open Interest (stat_type 9, by ts_event)."""
    p = _raw_path(symbol, feed, "statistics")
    if not p.exists():
        return pd.Series(dtype="float64")
    st = pd.read_parquet(p)
    if not {"stat_type", date_col, value_col}.issubset(st.columns):
        return pd.Series(dtype="float64")
    sel = st[st["stat_type"] == stat_type].copy()
    if sel.empty:
        return pd.Series(dtype="float64")
    sel["D"] = pd.to_datetime(sel[date_col]).dt.normalize()
    return sel.groupby("D")[value_col].last()


def _with_settlement(ohlcv: pd.DataFrame, settle: pd.Series) -> pd.DataFrame:
    """Override Close with exchange settlement (stat_type 3) where present, so the
    series is settlement-based like Norgate rather than the ohlcv last-trade close."""
    if ohlcv.empty or settle.empty:
        return ohlcv
    out = ohlcv.copy()
    out["Close"] = settle.reindex(out.index).combine_first(out["Close"])
    return out


def _roll_key(df: pd.DataFrame) -> Optional[str]:
    """The column that identifies the active contract, for roll detection. Prefer
    ``instrument_id`` (changes at each roll); ``symbol`` is only useful when it carries
    resolved contracts rather than databento's constant continuous alias."""
    return "instrument_id" if "instrument_id" in df.columns else (
        "symbol" if "symbol" in df.columns else None)


def _cumulative_offset(n0: pd.DataFrame, n1: pd.DataFrame):
    """Additive back-adjust offset per date: the sum of the roll gap
    (n1_close - n0_close measured ON each roll date) over all rolls at or after that
    date. A roll date is the last session a front contract is active — its active
    contract (instrument_id) differs from the next day's. Returns (offset Series on
    n0.index, n_rolls, n_missing)."""
    offset = pd.Series(0.0, index=n0.index)
    key = _roll_key(n0)
    if key is None or n1.empty or "Close" not in n1.columns:
        return offset, 0, 0
    sym = n0[key]
    is_roll = sym.ne(sym.shift(-1)) & sym.shift(-1).notna()
    roll_dates = list(n0.index[is_roll])
    if not roll_dates:
        return offset, 0, 0
    n0_close, n1_close = n0["Close"], n1["Close"].reindex(n0.index)
    gaps, missing = {}, 0
    for d in roll_dates:
        g = n1_close.get(d, float("nan")) - n0_close.get(d, float("nan"))
        if pd.isna(g):
            missing += 1
            continue
        gaps[d] = float(g)
    # Walk dates newest->oldest; add each roll's gap as we pass it (inclusive of the
    # roll date), so every earlier price carries the cumulative offset.
    running, out = 0.0, {}
    for d in reversed(list(n0.index)):
        if d in gaps:
            running += gaps[d]
        out[d] = running
    return pd.Series(out).reindex(n0.index), len(gaps), missing


def build(symbols=None) -> dict:
    """Stage 2 (free): read the raw store and write unadj + backadj daily bars to the
    cotdata store for every databento-capable symbol. Requires the raw store populated
    by ingest(). Returns {kind, ok, symbols, wrote}."""
    targets = [s for s in all_symbols()
               if s.databento and s.price_source in (None, "databento")
               and (symbols is None or s.internal in symbols)]
    wrote, skipped = 0, 0
    for s in targets:
        n0 = _read_ohlcv(s.internal, ".n.0")
        if n0.empty:
            print(f"{s.internal}: no raw .n.0 ohlcv — run ingest first; skipping")
            skipped += 1
            continue
        n1 = _read_ohlcv(s.internal, ".n.1")
        n0 = _with_settlement(n0, _stat_series(s.internal, ".n.0", 3, "ts_ref", "price"))
        if not n1.empty:
            n1 = _with_settlement(n1, _stat_series(s.internal, ".n.1", 3, "ts_ref", "price"))
        oi = _stat_series(s.internal, ".n.0", 9, "ts_event", "quantity")

        unadj = n0.copy()
        unadj["Open Interest"] = oi.reindex(unadj.index) if not oi.empty else float("nan")
        extra = []
        key = _roll_key(unadj)
        if key:
            # The active-contract id, as a string, so roll_dates() can detect the change.
            # (databento gives no calendar month here, so this is the instrument_id.)
            unadj["Delivery Month"] = unadj[key].astype(str)
            extra = ["Delivery Month"]

        offset, n_rolls, n_missing = _cumulative_offset(n0, n1)
        if n_rolls == 0:
            print(f"{s.internal}: no rolls detected (backadj == unadj) — verify the raw ohlcv "
                  f"carries `instrument_id` (the roll signal; `symbol` is the constant alias)")
        if n_missing:
            print(f"{s.internal}: {n_missing} roll gap(s) unmeasurable (no .n.1 close) — treated as 0")

        backadj = unadj.copy()
        for c in ("Open", "High", "Low", "Close"):
            if c in backadj.columns:
                backadj[c] = backadj[c] + offset

        for c in _OUT_COLS:
            if c not in unadj.columns:
                unadj[c] = float("nan")
                backadj[c] = float("nan")
        cols = _OUT_COLS + extra
        store.write_prices(s.internal, "unadj", unadj[cols], source="databento")
        store.write_prices(s.internal, "backadj", backadj[cols], source="databento")
        wrote += 1
        print(f"{s.internal}: built unadj+backadj ({len(unadj)} bars, {n_rolls} rolls) -> store")

    return {"kind": "build_databento", "ok": skipped == 0, "symbols": len(targets), "wrote": wrote}
