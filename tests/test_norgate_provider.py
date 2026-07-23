# Mock norgatedata so we can import and test norgate on any OS
import datetime as _dt
import sys
import types
from unittest import mock

import numpy as np
import pandas as pd
import pytest

mock_norgatedata = types.ModuleType("norgatedata")
mock_norgatedata.PaddingType = mock.Mock()
mock_norgatedata.PaddingType.NONE = "NONE"
mock_norgatedata.status = mock.Mock(return_value=True)  # NDU reachable (preflight)
sys.modules["norgatedata"] = mock_norgatedata

from cotdata.providers import norgate  # noqa: E402 (import after sys.modules mock injection above)


@mock.patch("cotdata.providers.norgate.store.read_prices")
@mock.patch("norgatedata.database_symbols", create=True)
@mock.patch("cotdata.providers.norgate.store.write_prices")
@mock.patch("norgatedata.price_timeseries", create=True)
def test_norgate_update_fetches_both_adjustments(mock_price_ts, mock_write_prices, mock_db_symbols, mock_read_prices):
    """Verify that update() fetches both the backadj and unadj series for a symbol."""
    mock_db_symbols.return_value = []
    mock_read_prices.return_value = pd.DataFrame()

    # Setup mock returns
    mock_df = pd.DataFrame({
        "Open": [100.0], "High": [101.0], "Low": [99.0], "Close": [100.5],
        "Volume": [1000], "Open Interest": [5000], "Delivery Month": [202609]
    }, index=pd.DatetimeIndex(["2026-07-01"]))

    mock_price_ts.return_value = mock_df

    # Mock all_symbols to just return a dummy registry entry for "ES"
    mock_symbol = mock.Mock()
    mock_symbol.internal = "ES"

    # We must patch REGISTRY and all_symbols so it uses our mock
    with mock.patch("cotdata.providers.norgate.all_symbols", return_value=[mock_symbol]), \
         mock.patch.dict("cotdata.providers.norgate.REGISTRY", {"ES": mock.Mock(norgate="&ES")}):

        # Run update
        norgate.update(symbols=["ES"])

        # Verify norgatedata API was called twice with correct raw symbols
        assert mock_price_ts.call_count == 2
        calls = mock_price_ts.call_args_list
        # Call 1: backadj ("&ES_CCB")
        assert calls[0][0][0] == "&ES_CCB"
        # Call 2: unadj ("&ES")
        assert calls[1][0][0] == "&ES"

        # Verify store.write_prices was called twice with correct adjustment flags
        assert mock_write_prices.call_count == 2
        write_calls = mock_write_prices.call_args_list
        # Call 1: store.write_prices("ES", "backadj", out_backadj, source="norgate")
        assert write_calls[0][0][0] == "ES"
        assert write_calls[0][0][1] == "backadj"
        # Call 2: store.write_prices("ES", "unadj", out_unadj, source="norgate")
        assert write_calls[1][0][0] == "ES"
        assert write_calls[1][0][1] == "unadj"

@mock.patch("cotdata.providers.norgate.store.write_prices")
@mock.patch("cotdata.providers.norgate.store.read_prices")
@mock.patch("norgatedata.database_symbols", create=True)
@mock.patch("norgatedata.price_timeseries", create=True)
def test_volume_reconstruction(mock_price_ts, mock_db_symbols, mock_read_prices, mock_write_prices):
    """Verify that _reconstruct_volume correctly appends additive columns without modifying default Volume."""

    # Mock continuous dataframe
    mock_continuous = pd.DataFrame({
        "Open": [100.0], "High": [101.0], "Low": [99.0], "Close": [100.5],
        "Volume": [1000], "Open Interest": [5000], "Delivery Month": [202609]
    }, index=pd.DatetimeIndex(["2026-07-01"]))

    # Mock individual contracts
    mock_indiv_H = pd.DataFrame({
        "Date": [pd.Timestamp("2026-07-01")],
        "Volume": [600],
        "Open Interest": [3000]
    })
    mock_indiv_M = pd.DataFrame({
        "Date": [pd.Timestamp("2026-07-01")],
        "Volume": [400],
        "Open Interest": [2000]
    })

    def mock_ts_side_effect(sym, **kwargs):
        if sym.endswith("CCB") or "-" not in sym:
            return mock_continuous.copy()
        if sym == "ES-2026H":
            return mock_indiv_H.copy()
        if sym == "ES-2026M":
            return mock_indiv_M.copy()
        return pd.DataFrame()

    mock_price_ts.side_effect = mock_ts_side_effect
    mock_db_symbols.return_value = ["ES-2026H", "ES-2026M", "ES-2025Z"]

    # Mock existing prices to trigger full backfill
    mock_read_prices.return_value = pd.DataFrame()

    mock_symbol = mock.Mock()
    mock_symbol.internal = "ES"

    with mock.patch("cotdata.providers.norgate.all_symbols", return_value=[mock_symbol]), \
         mock.patch.dict("cotdata.providers.norgate.REGISTRY", {"ES": mock.Mock(norgate="&ES")}):

        norgate.update(symbols=["ES"])

        # Verify the written dataframe has the additive columns and default Volume is untouched
        write_call = mock_write_prices.call_args_list[0]
        written_df = write_call[0][2]

        assert "FirstVolume" in written_df.columns
        assert "SecondVolume" in written_df.columns
        assert "Volume_Reconstructed" in written_df.columns
        assert "Volume_Source" in written_df.columns

        # Default Volume should be UNTOUCHED (1000)
        assert written_df["Volume"].iloc[0] == 1000

        # FirstVolume (ES-2026H) + SecondVolume (ES-2026M) = 1000
        assert written_df["FirstVolume"].iloc[0] == 600
        assert written_df["SecondVolume"].iloc[0] == 400
        assert written_df["Volume_Reconstructed"].iloc[0] == 1000
        assert written_df["Volume_Source"].iloc[0] == "reconstructed"
        assert written_df["FirstContract"].iloc[0] == "ES-2026H"
        assert written_df["SecondContract"].iloc[0] == "ES-2026M"


@mock.patch("cotdata.providers.norgate.store.write_prices")
@mock.patch("cotdata.providers.norgate.store.read_prices")
@mock.patch("norgatedata.database_symbols", create=True)
@mock.patch("norgatedata.price_timeseries", create=True)
def test_reconstruction_picks_by_volume_not_expiry(mock_price_ts, mock_db_symbols, mock_read_prices, mock_write_prices):
    """First/Second must be the two HIGHEST-VOLUME contracts, not the two nearest by
    expiry. Models the GC/SI case: the nearest serial month is near-empty while a
    later contract is dominant. An expiry-order pick would name the empty serial as
    'First' and understate volume; volume-rank must name the dominant contract."""
    mock_continuous = pd.DataFrame({
        "Open": [100.0], "High": [101.0], "Low": [99.0], "Close": [100.5],
        "Volume": [1000], "Open Interest": [5000], "Delivery Month": [202606],
    }, index=pd.DatetimeIndex(["2026-07-01"]))

    # ES-2026H (March) = nearest by expiry but near-empty; ES-2026M (June) = dominant.
    near_empty = pd.DataFrame({"Date": [pd.Timestamp("2026-07-01")], "Volume": [50]})
    dominant = pd.DataFrame({"Date": [pd.Timestamp("2026-07-01")], "Volume": [900]})

    def mock_ts_side_effect(sym, **kwargs):
        if sym.endswith("CCB") or "-" not in sym:
            return mock_continuous.copy()
        if sym == "ES-2026H":
            return near_empty.copy()
        if sym == "ES-2026M":
            return dominant.copy()
        return pd.DataFrame()

    mock_price_ts.side_effect = mock_ts_side_effect
    mock_db_symbols.return_value = ["ES-2026H", "ES-2026M"]
    mock_read_prices.return_value = pd.DataFrame()

    mock_symbol = mock.Mock()
    mock_symbol.internal = "ES"
    with mock.patch("cotdata.providers.norgate.all_symbols", return_value=[mock_symbol]), \
         mock.patch.dict("cotdata.providers.norgate.REGISTRY", {"ES": mock.Mock(norgate="&ES")}):
        norgate.update(symbols=["ES"])
        written_df = mock_write_prices.call_args_list[0][0][2]

        # Dominant (June, 900) is First even though March expires sooner.
        assert written_df["FirstContract"].iloc[0] == "ES-2026M"
        assert written_df["FirstVolume"].iloc[0] == 900
        assert written_df["SecondContract"].iloc[0] == "ES-2026H"
        assert written_df["SecondVolume"].iloc[0] == 50
        assert written_df["Volume_Reconstructed"].iloc[0] == 950


@mock.patch("cotdata.providers.norgate.store.write_prices")
@mock.patch("cotdata.providers.norgate.store.read_prices")
@mock.patch("norgatedata.database_symbols", create=True)
@mock.patch("norgatedata.price_timeseries", create=True)
def test_volume_reconstruction_incremental(mock_price_ts, mock_db_symbols, mock_read_prices, mock_write_prices):
    """Verify that _reconstruct_volume preserves old Volume_Source during an incremental run."""

    # Existing df has an old date with a "raw" fallback, and a slightly newer one with "reconstructed"
    mock_existing = pd.DataFrame({
        "Volume": [500, 800],
        "Volume_Reconstructed": [500, 800],
        "FirstVolume": [np.nan, 500],
        "SecondVolume": [np.nan, 300],
        "FirstContract": ["", "ES-2026H"],
        "SecondContract": ["", "ES-2026M"],
        "Volume_Source": ["raw", "reconstructed"]
    }, index=pd.DatetimeIndex(["2020-01-01", "2026-06-01"]))

    mock_read_prices.return_value = mock_existing.copy()

    # New continuous dataframe has the old dates + a new date
    mock_continuous = pd.DataFrame({
        "Open": [10, 10, 10], "High": [10, 10, 10], "Low": [10, 10, 10], "Close": [10, 10, 10],
        "Volume": [500, 800, 1000], "Open Interest": [0, 0, 0], "Delivery Month": [0, 0, 0]
    }, index=pd.DatetimeIndex(["2020-01-01", "2026-06-01", "2026-07-01"]))

    # The new date gets fetched. The trailing 60 days from 2026-06-01 is 2026-04-02.
    # We will just return some mock individual contracts.
    mock_indiv_U = pd.DataFrame({
        "Date": [pd.Timestamp("2026-07-01")],
        "Volume": [600], "Open Interest": [0]
    })
    mock_indiv_Z = pd.DataFrame({
        "Date": [pd.Timestamp("2026-07-01")],
        "Volume": [400], "Open Interest": [0]
    })

    def mock_ts_side_effect(sym, **kwargs):
        if sym.endswith("CCB") or "-" not in sym:
            return mock_continuous.copy()
        if sym == "ES-2026U":
            return mock_indiv_U.copy()
        if sym == "ES-2026Z":
            return mock_indiv_Z.copy()
        return pd.DataFrame()

    mock_price_ts.side_effect = mock_ts_side_effect
    mock_db_symbols.return_value = ["ES-2026U", "ES-2026Z"]

    mock_symbol = mock.Mock()
    mock_symbol.internal = "ES"

    with mock.patch("cotdata.providers.norgate.all_symbols", return_value=[mock_symbol]), \
         mock.patch.dict("cotdata.providers.norgate.REGISTRY", {"ES": mock.Mock(norgate="&ES")}):

        norgate.update(symbols=["ES"])

        write_call = mock_write_prices.call_args_list[0]
        written_df = write_call[0][2]

        # Verify the 2020-01-01 row is still "raw"
        assert written_df.loc["2020-01-01", "Volume_Source"] == "raw"
        assert written_df.loc["2020-01-01", "Volume_Reconstructed"] == 500

        # Verify the 2026-06-01 row is still "reconstructed"
        assert written_df.loc["2026-06-01", "Volume_Source"] == "reconstructed"

        # Verify the newly fetched 2026-07-01 row is computed correctly
        assert written_df.loc["2026-07-01", "Volume_Source"] == "reconstructed"
        assert written_df.loc["2026-07-01", "Volume_Reconstructed"] == 1000
        assert written_df.loc["2026-07-01", "FirstContract"] == "ES-2026U"


@mock.patch("cotdata.providers.norgate.store.write_prices")
@mock.patch("cotdata.providers.norgate.store.read_prices")
@mock.patch("norgatedata.database_symbols", create=True)
@mock.patch("norgatedata.price_timeseries", create=True)
def test_full_rebuild_bypasses_incremental_window(mock_price_ts, mock_db_symbols, mock_read_prices, mock_write_prices):
    """update(full=True) must recompute from epoch, ignoring the trailing-60-day
    window — even when the store already carries recent Volume_Reconstructed. The
    individual-contract fetch should be issued with start_date=1970-01-01."""
    mock_existing = pd.DataFrame({
        "Volume": [800],
        "Volume_Reconstructed": [800],
        "FirstVolume": [500], "SecondVolume": [300],
        "FirstContract": ["ES-2026H"], "SecondContract": ["ES-2026M"],
        "Volume_Source": ["reconstructed"],
    }, index=pd.DatetimeIndex(["2026-06-01"]))
    mock_read_prices.return_value = mock_existing.copy()

    mock_continuous = pd.DataFrame({
        "Open": [10, 10], "High": [10, 10], "Low": [10, 10], "Close": [10, 10],
        "Volume": [800, 1000], "Open Interest": [0, 0], "Delivery Month": [0, 0],
    }, index=pd.DatetimeIndex(["2026-06-01", "2026-07-01"]))
    mock_indiv = pd.DataFrame({"Date": [pd.Timestamp("2026-07-01")], "Volume": [1000]})

    def mock_ts_side_effect(sym, **kwargs):
        if sym.endswith("CCB") or "-" not in sym:
            return mock_continuous.copy()
        if sym == "ES-2026U":
            return mock_indiv.copy()
        return pd.DataFrame()

    mock_price_ts.side_effect = mock_ts_side_effect
    mock_db_symbols.return_value = ["ES-2026U"]

    mock_symbol = mock.Mock()
    mock_symbol.internal = "ES"
    with mock.patch("cotdata.providers.norgate.all_symbols", return_value=[mock_symbol]), \
         mock.patch.dict("cotdata.providers.norgate.REGISTRY", {"ES": mock.Mock(norgate="&ES")}):
        norgate.update(symbols=["ES"], full=True)

    # Every individual-contract fetch (sym containing '-') must start from epoch.
    indiv_starts = [c.kwargs.get("start_date") for c in mock_price_ts.call_args_list
                    if "-" in c.args[0]]
    assert indiv_starts, "expected at least one individual-contract fetch"
    assert all(s == "1970-01-01" for s in indiv_starts), indiv_starts


def test_scoped_update_metadata_upserts_preserving_others(tmp_path, monkeypatch):
    """`update_metadata(symbols=[...])` must UPSERT by Symbol into the existing
    contract_specs — the data-loss regression where a 5-symbol run replaced the
    whole 42-market table. Untouched markets survive; requested ones are refreshed.
    Exercises the real store round-trip through a tmp COTDATA_STORE."""
    monkeypatch.setenv("COTDATA_STORE", str(tmp_path))
    from cotdata import store

    # Pre-existing full table (stand-in for the 42 markets already on disk)
    store.write_metadata(
        pd.DataFrame({"Symbol": ["ES", "NQ", "DC"], "Tick Size": [0.25, 0.25, 0.01]}),
        source="seed",
    )

    def fake_meta(sym):
        return {"Symbol": sym, "Tick Size": 99.0}  # sentinel refreshed value

    with mock.patch("cotdata.providers.norgate.get_symbol_metadata", side_effect=fake_meta):
        norgate.update_metadata(symbols=["DC"])

    df = store.read_metadata().set_index("Symbol")
    assert set(df.index) == {"ES", "NQ", "DC"}   # ES/NQ preserved — not dropped
    assert df.loc["DC", "Tick Size"] == 99.0      # DC refreshed
    assert df.loc["ES", "Tick Size"] == 0.25      # untouched market unchanged


def test_full_update_metadata_replaces_table(tmp_path, monkeypatch):
    """`update_metadata()` with no symbols regenerates the whole registry and
    REPLACES the table (drops symbols no longer produced)."""
    monkeypatch.setenv("COTDATA_STORE", str(tmp_path))
    from cotdata import store

    store.write_metadata(
        pd.DataFrame({"Symbol": ["OLD"], "Tick Size": [1.0]}), source="seed",
    )

    sym_a = mock.Mock(internal="ES")
    sym_b = mock.Mock(internal="NQ")
    with mock.patch("cotdata.providers.norgate.all_symbols", return_value=[sym_a, sym_b]), \
         mock.patch("cotdata.providers.norgate.get_symbol_metadata",
                    side_effect=lambda s: {"Symbol": s, "Tick Size": 1.0}):
        norgate.update_metadata()  # no symbols → full replace

    assert set(store.read_metadata()["Symbol"]) == {"ES", "NQ"}  # OLD gone


def test_update_aborts_fast_when_ndu_unreachable(monkeypatch):
    """When NDU is down, update() must raise BEFORE any fetch — not fall into
    norgatedata's 10x-retry + bare sys.exit() (which exits 0 and defeats scheduler
    retry). norgatedata.status() returning False is the trip wire."""
    monkeypatch.setattr(mock_norgatedata, "status", mock.Mock(return_value=False))
    with pytest.raises(RuntimeError, match="Norgate Data service is not reachable"):
        norgate.update(symbols=["ES"])


def test_update_metadata_aborts_fast_when_ndu_unreachable(monkeypatch):
    monkeypatch.setattr(mock_norgatedata, "status", mock.Mock(return_value=False))
    with pytest.raises(RuntimeError, match="Norgate Data service is not reachable"):
        norgate.update_metadata(symbols=["ES"])


def test_metadata_skips_all_null_spec_rows(tmp_path, monkeypatch):
    """A COVERED symbol whose specs all come back None (a transient Norgate failure,
    not the MME/MFS no-coverage case) must be skipped — never written as a null row,
    and on a scoped upsert never used to overwrite good existing specs."""
    monkeypatch.setenv("COTDATA_STORE", str(tmp_path))
    from cotdata import store

    sym_es = mock.Mock(internal="ES", norgate="&ES")
    sym_nq = mock.Mock(internal="NQ", norgate="&NQ")

    def fake_meta(sym):
        base = {"Symbol": sym, "Norgate_Symbol": f"&{sym}_CCB"}
        if sym == "NQ":                                   # all specs empty → junk
            return {**base, **{k: None for k in norgate._SPEC_FIELDS}}
        return {**base, **{k: None for k in norgate._SPEC_FIELDS}, "Tick Size": 0.25}

    with mock.patch("cotdata.providers.norgate.all_symbols", return_value=[sym_es, sym_nq]), \
         mock.patch.dict("cotdata.providers.norgate.REGISTRY", {"ES": sym_es, "NQ": sym_nq}), \
         mock.patch("cotdata.providers.norgate.get_symbol_metadata", side_effect=fake_meta):
        norgate.update_metadata()  # full run

    assert set(store.read_metadata()["Symbol"]) == {"ES"}   # NQ null row skipped


def test_metadata_skips_symbols_without_norgate_coverage(tmp_path, monkeypatch):
    """Yahoo-only markets (registry norgate=None, e.g. MME/MFS) must be skipped by
    the Norgate metadata producer — never fetched, never written as null rows. The
    regression: `&MME_CCB not found` spam + all-null spec rows in contract_specs."""
    monkeypatch.setenv("COTDATA_STORE", str(tmp_path))
    from cotdata import store

    sym_es = mock.Mock(internal="ES", norgate="&ES")
    sym_mme = mock.Mock(internal="MME", norgate=None)   # no Norgate coverage
    called = []

    def fake_meta(sym):
        called.append(sym)
        return {"Symbol": sym, "Tick Size": 1.0}

    with mock.patch("cotdata.providers.norgate.all_symbols",
                    return_value=[sym_es, sym_mme]), \
         mock.patch.dict("cotdata.providers.norgate.REGISTRY",
                         {"ES": sym_es, "MME": sym_mme}), \
         mock.patch("cotdata.providers.norgate.get_symbol_metadata",
                    side_effect=fake_meta):
        norgate.update_metadata()  # full run

    assert called == ["ES"]                                   # MME never fetched
    assert set(store.read_metadata()["Symbol"]) == {"ES"}     # no null MME row


def test_covered_filter_drops_none_norgate():
    """Unit: _norgate_covered keeps only symbols whose registry norgate is truthy."""
    with mock.patch.dict("cotdata.providers.norgate.REGISTRY",
                         {"ES": mock.Mock(norgate="&ES"),
                          "MME": mock.Mock(norgate=None)}):
        assert norgate._norgate_covered(["ES", "MME"]) == ["ES"]




def test_finals_ready_pure_logic():
    from cotdata.providers.norgate import _finals_ready
    now = _dt.datetime(2026, 7, 15, 21, 30)   # 9:30pm local
    after  = _dt.datetime(2026, 7, 15, 20, 56)  # updated after 20:55 cutoff
    before = _dt.datetime(2026, 7, 15, 20, 40)  # updated before cutoff
    # both DBs refreshed after cutoff -> ready
    ok, _ = _finals_ready({"Futures": after, "Continuous Futures": after}, "20:55", now)
    assert ok is True
    # one DB still on pre-cutoff (interim) data -> not ready
    ng, _ = _finals_ready({"Futures": after, "Continuous Futures": before}, "20:55", now)
    assert ng is False
    # missing update time -> not ready
    nn, _ = _finals_ready({"Futures": None, "Continuous Futures": after}, "20:55", now)
    assert nn is False


def test_finals_ready_handles_tz_aware_times():
    """norgatedata returns tz-aware datetimes (e.g. -04:00); comparing them against
    a naive cutoff must not raise, and must evaluate by local wall-clock."""
    import datetime as d

    from cotdata.providers.norgate import _finals_ready
    et = d.timezone(d.timedelta(hours=-4))
    now = d.datetime(2026, 7, 15, 21, 30)                       # 9:30pm naive local
    after  = d.datetime(2026, 7, 15, 20, 56, tzinfo=et)          # aware, after 20:55
    before = d.datetime(2026, 7, 15, 6, 12, tzinfo=et)           # aware, morning update
    ok, _ = _finals_ready({"Futures": after, "Continuous Futures": after}, "20:55", now)
    assert ok is True
    ng, _ = _finals_ready({"Futures": after, "Continuous Futures": before}, "20:55", now)
    assert ng is False
