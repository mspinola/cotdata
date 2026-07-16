"""Hermetic tests for the yfinance price provider — no network, no store writes.

Mocks yfinance.download (so no Yahoo call) and store.write_prices (so nothing is
written), and checks the provider normalizes Yahoo's (field, ticker) MultiIndex frame
to the store's Open/High/Low/Close/Volume + DatetimeIndex('Date') shape, for both the
backadj and unadj adjustments."""
import pandas as pd
import pytest


def _fake_yahoo_frame():
    # yfinance returns a (field, ticker) column MultiIndex even for one symbol.
    idx = pd.to_datetime(["2020-01-02", "2020-01-03", "2020-01-06"])
    cols = pd.MultiIndex.from_product(
        [["Open", "High", "Low", "Close", "Volume"], ["EEM"]])
    return pd.DataFrame([[10, 11, 9, 10.5, 1000],
                         [10.5, 11.5, 10, 11, 1200],
                         [11, 12, 10.8, 11.8, 900]], index=idx, columns=cols)


def test_yfinance_update_normalizes_and_writes_both_adjustments(monkeypatch):
    import yfinance
    from cotdata import store
    from cotdata.providers import yfinance as yprov

    monkeypatch.setattr(yfinance, "download", lambda *a, **k: _fake_yahoo_frame())
    written = {}
    monkeypatch.setattr(store, "write_prices",
                        lambda sym, adj, df, source: written.__setitem__((sym, adj), (df, source)))

    res = yprov.update(symbols=["MME"])          # MME carries yahoo="EEM" in the registry
    assert res["wrote"] == 1 and res["ok"]
    assert ("MME", "backadj") in written and ("MME", "unadj") in written   # ETF proxy → both

    df, source = written[("MME", "backadj")]
    assert source == "yahoo"
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]  # flattened
    assert df.index.name == "Date" and df.index.tz is None                 # tz-naive Date index
    assert len(df) == 3 and df["Close"].iloc[-1] == 11.8


def test_yfinance_update_skips_symbols_without_ticker(monkeypatch):
    from cotdata.providers import yfinance as yprov
    # GC has no yahoo ticker → nothing to do, no fetch attempted.
    res = yprov.update(symbols=["GC"])
    assert res["wrote"] == 0 and res["ok"]


def test_yfinance_update_reports_empty_as_failure(monkeypatch):
    import yfinance
    from cotdata.providers import yfinance as yprov
    monkeypatch.setattr(yfinance, "download", lambda *a, **k: pd.DataFrame())
    res = yprov.update(symbols=["MME"])
    assert res["wrote"] == 0 and res["ok"] is False
