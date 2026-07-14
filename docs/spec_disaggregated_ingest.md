# Spec: Disaggregated Futures-Only COT Ingest

This document dictates the canonical data layer extraction for the Disaggregated Futures-Only CFTC report.

## 1. Why Disaggregated?
The existing Legacy report (`cot/`) provides Commercial and NonCommercial positional breakdowns. However, it does not include trader counts, nor does it split out Swap Dealers (SD) or Money Managers (MM). 
To compute Keenan's Positioning Trio (Concentration, Clustering, and Position Size), we must ingest the Disaggregated report, which includes `Traders_*` columns natively.

## 2. Ingest Architecture
This pipeline operates strictly independently from the Legacy pipeline.
*   **Producer Module**: `src/cotdata/providers/cftc_disagg.py`
*   **Source Files**: `fut_disagg_txt_{year}.zip` (TXT/CSV is significantly faster to parse and natively supported by `pandas.read_csv` without the heavy `xlrd` dependency required by Legacy `.xls`).
*   **First Available Year**: 2006
*   **Fidelity**: Unlike Legacy, the Disaggregated pipeline is **near-lossless**. It preserves every `Traders_*` column directly from the zip without sub-setting, only normalizing `Report_Date_as_MM_DD_YYYY` to datetimes and padding `CFTC_Contract_Market_Code` to 6-digits.
*   **Store Namespace**: To prevent collision, it writes to a new `$COTDATA_STORE/cot_disagg/` subdirectory and is tracked under a `"cot_disagg"` key in `manifest.json`.

## 3. Public API
The consumer API remains `cotdata.get_cot(name)`, but now accepts a `report` argument:
```python
df_legacy = cotdata.get_cot("ES", report="legacy")  # existing behavior
df_disagg = cotdata.get_cot("ES", report="disagg")  # new lossless disaggregated DataFrame
```
Predecessor contract stitching (`hist_codes`) applies identically to both reports.

## 4. Reconciliation
Because Disaggregated is parallel to Legacy, we validate it by checking that the summed entities match the Legacy groups:
*   `Money_Manager` + `Other_Reportable` ≈ `NonCommercial` (Large Speculators)
*   `Producer_Merchant` + `Swap_Dealer` ≈ `Commercial` (Hedgers)
