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

from cotdata.providers import databento as dbprov  # noqa: E402,I001 (import after sys.modules mock injection above)


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


# ── start_date ──────────────────────────────────────────────────────────────
# start_date means two different things depending on cache state (see the
# fetch_daily_ohlc docstring): it narrows the FETCH on a cold cache (cost benefit —
# the whole reason this was broken), but only narrows the RETURN on a warm cache
# (correctness — never lets a later, narrower caller truncate what other callers
# already rely on being cached). Each get is exercised separately below.

def test_cold_cache_start_date_narrows_the_fetch_floor(tmp_path, monkeypatch, ohlcv, stats):
    monkeypatch.setenv("COTDATA_STORE", str(tmp_path))
    monkeypatch.setenv("DATABENTO_API_KEY", "test-key")
    client = _client(ohlcv, stats)
    mock_databento.Historical = mock.Mock(return_value=client)

    dbprov.fetch_daily_ohlc("ES", start_date="2019-01-01", price_type="close")

    ohlcv_call = next(c for c in client.timeseries.get_range.call_args_list
                      if c.kwargs.get("schema") == "ohlcv-1d")
    assert ohlcv_call.kwargs["start"] == "2019-01-01"        # NOT "2000-01-01"


def test_cold_cache_start_date_is_still_clamped_to_the_glbx_floor(tmp_path, monkeypatch,
                                                                   ohlcv, stats):
    monkeypatch.setenv("COTDATA_STORE", str(tmp_path))
    monkeypatch.setenv("DATABENTO_API_KEY", "test-key")
    client = _client(ohlcv, stats)
    mock_databento.Historical = mock.Mock(return_value=client)

    dbprov.fetch_daily_ohlc("ES", start_date="1990-01-01", price_type="close")

    ohlcv_call = next(c for c in client.timeseries.get_range.call_args_list
                      if c.kwargs.get("schema") == "ohlcv-1d")
    assert ohlcv_call.kwargs["start"] == "2010-06-06"         # clamped, not 1990


def test_returned_frame_excludes_rows_before_start_date(tmp_path, monkeypatch, ohlcv, stats):
    monkeypatch.setenv("COTDATA_STORE", str(tmp_path))
    monkeypatch.setenv("DATABENTO_API_KEY", "test-key")
    mock_databento.Historical = mock.Mock(return_value=_client(ohlcv, stats))

    df = dbprov.fetch_daily_ohlc("ES", start_date="2020-01-03", price_type="close")

    assert "2020-01-02" not in df.index.strftime("%Y-%m-%d")  # before start_date


def test_no_start_date_is_unbounded_default(tmp_path, monkeypatch, ohlcv, stats):
    """The default (None) is unchanged from before this parameter existed."""
    monkeypatch.setenv("COTDATA_STORE", str(tmp_path))
    monkeypatch.setenv("DATABENTO_API_KEY", "test-key")
    client = _client(ohlcv, stats)
    mock_databento.Historical = mock.Mock(return_value=client)

    df = dbprov.fetch_daily_ohlc("ES", price_type="close")

    ohlcv_call = next(c for c in client.timeseries.get_range.call_args_list
                      if c.kwargs.get("schema") == "ohlcv-1d")
    assert ohlcv_call.kwargs["start"] == "2010-06-06"    # 2000-01-01 clamped to GLBX floor
    assert "2020-01-02" in df.index.strftime("%Y-%m-%d")


def test_warm_cache_start_date_does_not_narrow_the_fetch_but_still_filters_the_return(
        tmp_path, monkeypatch):
    """The case the docstring warns about: a cache already holding 06-01..06-03 must
    keep being topped up from its own last_date, unaffected by a later, narrower
    start_date — but the RETURN must still respect it, excluding 06-01."""
    monkeypatch.setenv("COTDATA_STORE", str(tmp_path))
    monkeypatch.setenv("DATABENTO_API_KEY", "test-key")
    monkeypatch.setattr(dbprov, "_API_LAST_CHECKED", {})   # fresh throttle state

    cache_dir = tmp_path / "_cache" / "databento"
    cache_dir.mkdir(parents=True)
    existing = pd.DataFrame(
        {"Open": [10.0, 11.0, 12.0], "High": [10.5, 11.5, 12.5],
         "Low": [9.5, 10.5, 11.5], "Close": [10.2, 11.2, 12.2],
         "Volume": [100, 100, 100], "Open Interest": [500.0, 500.0, 500.0]},
        index=pd.DatetimeIndex(["2019-06-01", "2019-06-02", "2019-06-03"], name="Date"))
    existing.to_parquet(cache_dir / "ES_daily.parquet")

    new_ohlcv = pd.DataFrame(
        {"open": [13.0], "high": [13.5], "low": [12.5], "close": [13.2], "volume": [100]},
        index=pd.DatetimeIndex(["2019-06-04"], name="ts_event"))
    new_stats = pd.DataFrame(
        {"stat_type": [9], "price": [0.0], "quantity": [600.0],
         "ts_ref": pd.to_datetime(["2019-06-04"])},
        index=pd.DatetimeIndex(["2019-06-04"], name="ts_event"))
    client = _client(new_ohlcv, new_stats)
    mock_databento.Historical = mock.Mock(return_value=client)

    df = dbprov.fetch_daily_ohlc("ES", start_date="2019-06-02", force_refresh=False,
                                 price_type="close")

    ohlcv_call = next(c for c in client.timeseries.get_range.call_args_list
                      if c.kwargs.get("schema") == "ohlcv-1d")
    assert ohlcv_call.kwargs["start"] == "2019-06-04"        # resumed from last_date+1,
                                                              # NOT narrowed to start_date
    dates = set(df.index.strftime("%Y-%m-%d"))
    assert "2019-06-01" not in dates                         # excluded: before start_date
    assert {"2019-06-02", "2019-06-03", "2019-06-04"} <= dates

    # the ON-DISK cache still holds the full series — start_date shaped the
    # return, not what was persisted.
    on_disk = pd.read_parquet(cache_dir / "ES_daily.parquet")
    assert "2019-06-01" in on_disk.index.strftime("%Y-%m-%d")
