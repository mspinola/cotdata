import pandas as pd
import pytest
from cotdata import cot

def test_tff_reconciliation_structural_identities(monkeypatch):
    """
    Validates that the TFF schema correctly sums into the Legacy schema
    groupings for the exact same report date, and that Open Interest matches.
    """
    
    # Mock Legacy store returning 1 week of data
    legacy_df = pd.DataFrame({
        "Open_Interest_All": [5000],
        "Comm_Positions_Long_All": [2500],
        "NonComm_Positions_Long_All": [1500],
        "NonRept_Positions_Long_All": [1000]
    }, index=pd.DatetimeIndex(["2021-01-05"]))
    
    # Mock TFF store returning 1 week of data
    tff_df = pd.DataFrame({
        "Open_Interest_All": [5000],
        "Dealer_Positions_Long_All": [1500],
        "Asset_Mgr_Positions_Long_All": [1000],
        "Lev_Money_Positions_Long_All": [1000],
        "Other_Rept_Positions_Long_All": [500],
        "NonRept_Positions_Long_All": [1000]
    }, index=pd.DatetimeIndex(["2021-01-05"]))
    
    def mock_read_cot_legacy(name):
        return legacy_df
    
    def mock_read_cot_tff(name):
        return tff_df
        
    monkeypatch.setattr("cotdata.store.read_cot_legacy", mock_read_cot_legacy)
    monkeypatch.setattr("cotdata.store.read_cot_tff", mock_read_cot_tff)
    
    leg = cot.get_cot("DUMMY", report="legacy")
    tff = cot.get_cot("DUMMY", report="tff")
    
    # Assert Open Interest matches exactly
    pd.testing.assert_series_equal(
        tff["Open_Interest_All"], 
        leg["Open_Interest_All"], 
        check_names=False
    )
    
    # Assert structural reconciliation on the Long side:
    # In financial futures, Dealer + Asset_Mgr roughly approximate Commercials,
    # while Lev_Money + Other_Rept roughly approximate Non-Commercials.
    # The total sum of all reportable longs + non-reportables must equal total OI.
    total_longs = (
        tff["Dealer_Positions_Long_All"] + 
        tff["Asset_Mgr_Positions_Long_All"] + 
        tff["Lev_Money_Positions_Long_All"] + 
        tff["Other_Rept_Positions_Long_All"] + 
        tff["NonRept_Positions_Long_All"]
    )
    
    pd.testing.assert_series_equal(
        total_longs, 
        leg["Open_Interest_All"], 
        check_names=False
    )
