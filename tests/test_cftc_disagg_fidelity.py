import zipfile
from cotdata.providers.cftc_disagg import _parse_zip, CONTRACT_CODE, REPORT_DATE

def test_disagg_fidelity_preserves_columns(tmp_path):
    """
    Fidelity test: Unlike the legacy provider which filters to a strict 10-column
    subset, the disaggregated provider must be a lossless image of the CSV,
    preserving all granular entity classes and Traders_* counts.
    """
    # A dummy CSV mimicking the CFTC Disaggregated txt format
    csv_content = b"""Market_and_Exchange_Names,Report_Date_as_MM_DD_YYYY,CFTC_Contract_Market_Code,Open_Interest_All,Prod_Merc_Positions_Long_All,Traders_Tot_All,Swap__Positions_Long_All,M_Money_Positions_Long_All,Other_Rept_Positions_Long_All
"CORN - CHICAGO BOARD OF TRADE",2021-01-05,002602,1000,200,50,150,300,100
"""
    zip_path = tmp_path / "fut_disagg_txt_2021.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("f_year.txt", csv_content)
        
    df = _parse_zip(zip_path)
    
    # 9 columns in the CSV -> 9 columns in the parsed DataFrame
    assert len(df.columns) == 9, "Lossless parsing must preserve all columns from the zip"
    
    # Check that crucial non-legacy columns survived
    assert "Traders_Tot_All" in df.columns
    assert "Swap__Positions_Long_All" in df.columns
    
    # Check that coercion worked
    assert df[CONTRACT_CODE].iloc[0] == "002602", "Contract code must be 6-digit zero padded"
    assert df[REPORT_DATE].iloc[0].year == 2021, "Report date must be parsed to datetime"
