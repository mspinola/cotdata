import pandas as pd
from unittest import mock

# Mock norgatedata so we can import and test norgate on any OS
import sys
import types
mock_norgatedata = types.ModuleType("norgatedata")
mock_norgatedata.PaddingType = mock.Mock()
mock_norgatedata.PaddingType.NONE = "NONE"
sys.modules["norgatedata"] = mock_norgatedata

from cotdata.providers import norgate

@mock.patch("cotdata.providers.norgate.store.write_prices")
@mock.patch("norgatedata.price_timeseries", create=True)
def test_norgate_update_fetches_both_adjustments(mock_price_ts, mock_write_prices):
    """Verify that update() fetches both the backadj and unadj series for a symbol."""
    
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
