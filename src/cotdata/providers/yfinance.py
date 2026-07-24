"""yfinance price provider — free Yahoo Finance OHLCV for registry symbols that carry
a ``yahoo`` ticker.

For markets Norgate/databento don't cover (e.g. MSCI EM / EAFE, priced via the EEM /
EFA ETF proxies). Research-grade: Yahoo is a free, unofficial feed — expect occasional
gaps, silent revisions, and API breakage; not a production replacement for Norgate.

Writes the same Open/High/Low/Close/Volume frame with a tz-naive DatetimeIndex named
``Date`` that the store's other price providers use, so ``cotdata.get_prices`` stays
source-agnostic. ETF/spot proxies have no futures roll, so backadj == unadj (both are
written, since consumers ask for ``backadj``).
"""
from __future__ import annotations

import pandas as pd

from .. import store
from ..registry import all_symbols, default_price_source, resolve_source


def _fetch(ticker: str) -> pd.DataFrame:
    import yfinance as yf
    raw = yf.download(ticker, period="max", auto_adjust=True,
                      progress=False, threads=False)
    if raw is None or raw.empty:
        return pd.DataFrame()
    # yfinance returns a (field, ticker) column MultiIndex even for a single symbol.
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    keep = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in raw.columns]
    df = raw[keep].copy()
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df.index.name = "Date"
    return df.dropna(subset=["Close"]).sort_index()


def update(symbols=None) -> dict:
    """Fetch Yahoo OHLCV for registry symbols that RESOLVE to yfinance on this
    deployment (see registry.resolve_source): markets the default vendor can't serve
    (e.g. MSCI ETF proxies always; ICE softs when the default is databento) plus any
    explicit ``price_source: yfinance`` override. Keyed on $COTDATA_PRICE_SOURCE, so
    the same softs stay on Norgate locally and fall to Yahoo on a databento server.
    Pass ``symbols`` to scope. Returns {kind, ok, wrote}."""
    default = default_price_source()
    targets = [s for s in all_symbols()
               if s.yahoo and resolve_source(s, default) == "yfinance"
               and (symbols is None or s.internal in symbols)]
    if not targets:
        print("yfinance: no registry symbols with a 'yahoo' ticker"
              + (f" among {symbols}" if symbols else ""))
        return {"kind": "prices_yahoo", "ok": True, "wrote": 0}

    wrote, failed = 0, 0
    for s in targets:
        try:
            df = _fetch(s.yahoo)
        except Exception as e:  # noqa: BLE001 — yfinance/network is flaky by nature
            print(f"{s.internal}: yfinance fetch failed ({s.yahoo}) — {e}")
            failed += 1
            continue
        if df.empty:
            print(f"{s.internal}: yfinance returned no data ({s.yahoo})")
            failed += 1
            continue
        for adj in ("backadj", "unadj"):
            store.write_prices(s.internal, adj, df, source="yahoo")
        wrote += 1
        print(f"{s.internal}: {len(df):5d} bars ({s.yahoo}) "
              f"{df.index.min().date()}..{df.index.max().date()} -> store")
    return {"kind": "prices_yahoo", "ok": failed == 0, "wrote": wrote}
