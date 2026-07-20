"""Smoke tests for the DORMANT Databento provider. It isn't in the live EOD path,
but it carries the hard-won statistics logic (Open Interest = stat_type 9;
settlement = stat_type 3, NOT 7=LOWEST_OFFER, dated by ts_ref) that the intraday
work will reuse — so lock that parsing here. `databento` is an optional extra, so
mock the SDK (as test_norgate_provider mocks norgatedata) to run on any machine."""
import sys
import types
from unittest import mock

import pandas as pd
import pytest

# databento is optional (the [databento] extra); force a mock module so the lazy
# `import databento as db` inside the provider resolves without the SDK installed.
mock_databento = types.ModuleType("databento")
sys.modules["databento"] = mock_databento

from cotdata.providers import databento as dbprov


def _client(ohlcv_df, stats_df):
    """A fake databento Historical whose timeseries.get_range returns the OHLCV
    frame for schema 'ohlcv-1d' and the statistics frame otherwise."""
    client = mock.Mock()

    def get_range(**kwargs):
        res = mock.Mock()
        res.to_df.return_value = ohlcv_df if kwargs.get("schema") == "ohlcv-1d" else stats_df
        return res

    client.timeseries.get_range.side_effect = get_range
    return client


@pytest.fixture
def ohlcv():
    idx = pd.DatetimeIndex(["2020-01-02"], name="ts_event")
    return pd.DataFrame(
        {"open": [99.0], "high": [101.0], "low": [98.0], "close": [100.0], "volume": [1234]},
        index=idx,
    )


@pytest.fixture
def stats():
    # OI (stat_type 9) is disseminated for the session date; the settlement
    # (stat_type 3) for the 01-02 session is disseminated the NEXT morning
    # (ts_event 01-03) but carries ts_ref 01-02 — the session it applies to.
    idx = pd.DatetimeIndex(["2020-01-02", "2020-01-03"], name="ts_event")
    return pd.DataFrame(
        {
            "stat_type": [9, 3],
            "price": [0.0, 101.5],
            "quantity": [5000.0, 0.0],
            "ts_ref": pd.to_datetime(["2020-01-02", "2020-01-02"]),
        },
        index=idx,
    )


def test_provider_imports_without_sdk():
    """The provider module imports and exposes its entry points even though
    `databento` isn't a hard dependency (import is lazy, behind the extra)."""
    assert callable(dbprov.fetch_daily_ohlc)
    assert callable(dbprov.update_all_daily_prices)


def test_fetch_extracts_open_interest_from_stat_type_9(tmp_path, monkeypatch, ohlcv, stats):
    monkeypatch.setenv("COTDATA_STORE", str(tmp_path))
    monkeypatch.setenv("DATABENTO_API_KEY", "test-key")
    mock_databento.Historical = mock.Mock(return_value=_client(ohlcv, stats))

    df = dbprov.fetch_daily_ohlc("ES", price_type="close")

    assert df.loc["2020-01-02", "Close"] == 100.0            # ohlcv close, untouched
    assert df.loc["2020-01-02", "Open Interest"] == 5000.0   # from stat_type 9


def test_settlement_overrides_close_dated_by_ts_ref(tmp_path, monkeypatch, ohlcv, stats):
    """price_type='settlement' replaces Close with stat_type 3's price, joined by
    ts_ref (the session), so the next-morning-disseminated settle lands on 01-02."""
    monkeypatch.setenv("COTDATA_STORE", str(tmp_path))
    monkeypatch.setenv("DATABENTO_API_KEY", "test-key")
    mock_databento.Historical = mock.Mock(return_value=_client(ohlcv, stats))

    df = dbprov.fetch_daily_ohlc("ES", price_type="settlement")

    assert df.loc["2020-01-02", "Close"] == 101.5            # settle, not ohlcv 100.0
    assert df.loc["2020-01-02", "Open Interest"] == 5000.0
