import pandas as pd
import pytest
from cotdata import cot

def test_reconciliation_structural_identities(monkeypatch):
    """
    Validates that the Disaggregated schema correctly sums into the Legacy schema
    groupings for the exact same report date.
    
    Legacy Commercial = Disagg Producer/Merchant + Swap Dealer
    Legacy NonCommercial = Disagg Money Manager + Other Reportable
    """
    
    # Mock Legacy store returning 1 week of data
    legacy_df = pd.DataFrame({
        "Comm_Positions_Long_All": [1000],
        "NonComm_Positions_Long_All": [500],
    }, index=pd.DatetimeIndex(["2021-01-05"]))
    
    # Mock Disaggregated store returning 1 week of data
    disagg_df = pd.DataFrame({
        "Prod_Merc_Positions_Long_All": [600],
        "Swap__Positions_Long_All": [400],    # 600 + 400 = 1000 (Legacy Comm)
        "M_Money_Positions_Long_All": [400],
        "Other_Rept_Positions_Long_All": [100], # 400 + 100 = 500 (Legacy NonComm)
    }, index=pd.DatetimeIndex(["2021-01-05"]))
    
    def mock_read_cot_legacy(name):
        return legacy_df
    
    def mock_read_cot_disagg(name):
        return disagg_df
        
    monkeypatch.setattr("cotdata.store.read_cot_legacy", mock_read_cot_legacy)
    monkeypatch.setattr("cotdata.store.read_cot_disagg", mock_read_cot_disagg)
    
    leg = cot.get_cot("DUMMY", report="legacy")
    dis = cot.get_cot("DUMMY", report="disagg")
    
    # Assert structural reconciliation on the Long side
    c_derived = dis["Prod_Merc_Positions_Long_All"] + dis["Swap__Positions_Long_All"]
    nc_derived = dis["M_Money_Positions_Long_All"] + dis["Other_Rept_Positions_Long_All"]
    
    pd.testing.assert_series_equal(
        c_derived, 
        leg["Comm_Positions_Long_All"], 
        check_names=False
    )
    
    pd.testing.assert_series_equal(
        nc_derived, 
        leg["NonComm_Positions_Long_All"], 
        check_names=False
    )
