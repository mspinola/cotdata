import pandas as pd
import numpy as np
from unittest import mock

# Mock norgatedata so we can import and test norgate on any OS
import sys
import types
mock_norgatedata = types.ModuleType("norgatedata")
mock_norgatedata.PaddingType = mock.Mock()
mock_norgatedata.PaddingType.NONE = "NONE"
sys.modules["norgatedata"] = mock_norgatedata

from cotdata.providers import norgate

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
        if sym.endswith("CCB") or not "-" in sym:
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
        if sym.endswith("CCB") or not "-" in sym:
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
