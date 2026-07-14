# cotdata

[![PyPI version](https://img.shields.io/pypi/v/cotdata.svg)](https://pypi.org/project/cotdata/)
[![Python versions](https://img.shields.io/pypi/pyversions/cotdata.svg)](https://pypi.org/project/cotdata/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Canonical data layer for the COT / futures-strategy stack. It exists so that quantitative analysis toolset never fetch data directly. They read a shared, file-based store through a stable API.

```
        PRODUCER  (runs where each source is reachable)
   Windows: Norgate export        anywhere: CFTC COT download
                       │  writes   │
                       ▼           ▼
        ┌───────────────────────────────────────┐
        │  CANONICAL STORE   ($COTDATA_STORE)            │
        │  prices/*.parquet  cot_legacy/*.parquet        │   ← synced (rsync / Dropbox / S3)
        │  cot_disagg/*.parquet  manifest.json           │
        └────────────────────────────────────────────────┘
                       ▲           ▲   reads (offline, cross-platform)
        ┌──────────────┴───┐  ┌────┴──────────────┐
        │   COT analyzer   │  │  analysis toolset │      both:  import cotdata
        └──────────────────┘  └───────────────────┘
```

## Installation

You can install `cotdata` directly from PyPI:
```bash
pip install cotdata
```

If you are running the **producer machine** on Windows to fetch Norgate data, install with the `norgate` extra:
```bash
pip install "cotdata[norgate]"
```

## Development

When using Norgate Data one must run this on a Windows machine as Norgate Updater requires Windows.

The downstream analysis toolset can run on any machine (Windows, Mac, Linux) as they only need to read data from the canonical store. 

One can create a unified workspace by cloning all repositories into the same parent directory, and installing them into a single virtual environment.

```bash
uv venv                                     # create .venv
uv pip install -e .                         # install cotdata & all deps
export COTDATA_STORE=/path/to/synced/store  # the shared store
```

**Producer machine (Windows, Norgate)** — only cotdata + the `norgate` extra:

```powershell
uv venv  --python 3.10                      # Tested through norgate's supported python versions.
uv pip install -e ".[norgate]"              # from the cotdata repo; pulls norgatedata
$env:COTDATA_STORE = "C:\path\to\store"     # Path to output location
cotdata-update --prices --symbols ES NQ
```

Use `uv run <cmd>` to run without activating, or activate with
`source .venv/bin/activate` (Mac) / `.venv\Scripts\activate` (Windows).

## The contract

The **store is the API boundary** — not Python imports. Producers write Parquet +
`manifest.json`; consumers only read. Nobody touches a vendor SDK at app runtime.
Swapping a vendor is a producer-only change.

- `metadata/contract_specs.parquet` — Norgate contract specifications (Tick Size, Point Value, Margin).
- `prices/{symbol}_{adjustment}.parquet` — Open/High/Low/Close/Volume/Open Interest,
  tz-naive `Date` index. `adjustment` ∈ {`backadj`, `unadj`}. Close = exchange settlement.
- `cot_legacy/{code}.parquet` — weekly CFTC Legacy positioning.
- `cot_disagg/{code}.parquet` — weekly CFTC Disaggregated positioning.
- `cot_tff/{code}.parquet` — weekly CFTC Traders in Financial Futures positioning.
- `manifest.json` — per-table `last_date`, `n_rows`, `source`, `updated_at`, `schema_version`.

## Consumer

```python
import cotdata
df = cotdata.get_prices("ES", adjustment="backadj")   # USE THIS FOR SIGNALS + STOPS
sz = cotdata.get_prices("ES", adjustment="unadj")     # USE FOR POSITION SIZING / POINT VALUE
cot_legacy = cotdata.get_cot("ES", report="legacy")   # USE FOR COMM/NON-COMM
cot_disagg = cotdata.get_cot("ES", report="disagg")   # USE FOR TRADER COUNTS (MM/SD/OR)
cot_tff = cotdata.get_cot("ES", report="tff")         # USE FOR TRADER COUNTS (FINANCIALS)
```

Set `COTDATA_STORE` to the synced store directory. 

**Predecessor Stitching & Scaling:** The `get_cot()` function doesn't just read a file; it dynamically stitches historical CFTC codes for contracts that migrated exchanges (like the Russell 2000) or rescales data for contracts that changed tick sizes (like Lumber). Downstream models see one clean, continuous asset.

## Producer (run on the machine that can reach the source)

```
COTDATA_STORE=/store  cotdata-update --prices --symbols ES NQ    # Norgate (Windows)
COTDATA_STORE=/store  cotdata-update --metadata                  # Norgate Metadata (Windows)
COTDATA_STORE=/store  cotdata-update --cot-legacy                # CFTC Legacy (cross-platform)
COTDATA_STORE=/store  cotdata-update --cot-disagg                # CFTC Disaggregated (cross-platform)
COTDATA_STORE=/store  cotdata-update --cot-tff                   # CFTC Traders in Financial Futures (cross-platform)
COTDATA_STORE=/store  cotdata-update --cot-all                   # Update all CFTC COT pipelines
```

Schedule nightly (prices, after the Norgate Data Updater) and weekly (COT Friday releases).

## Design rules

### Why Back-Adjusted vs Unadjusted?

Futures contracts expire, forcing traders to "roll" into the next contract, which usually trades at a slightly different price. Simply stitching these contracts together creates artificial price gaps.

- **`backadj` (for signals & stops)**: Uses gap-free arithmetic rolls. This mathematically shifts historical prices backward to align with the new contract, preserving the *true shape* and percentage moves of the market. You must use this for technical indicators, trade signals, and stop-losses to avoid false triggers on rollover gaps.
- **`unadj` (for position sizing)**: Because back-adjustment shifts historical prices (sometimes into the negative), you cannot use it to calculate true dollar values. You must use `unadj` (raw, real-life) data for that exact day to calculate your true dollar risk and decide exactly how many contracts to buy.

### Providers & Authentication

**Norgate Data (Primary)**: 
There are no API keys to configure in Python. The `norgatedata` Python package communicates locally with the Norgate Data Updater application. You simply need to have the Norgate Data Updater installed, authenticated, and running in the background on your Windows machine.

**Databento (Dormant/Intraday)**: 
Databento is kept as a dormant provider because it works well and can be leveraged for intraday data. If you wish to use it, you must provide your API key via the `DATABENTO_API_KEY` environment variable:
```bash
export DATABENTO_API_KEY="your_api_key_here"
```

### Parameterizing the Asset List

The list of supported futures contracts (the symbol registry) is dynamically loaded from a YAML configuration file, making it easy to add new markets without touching any Python code.

- **Adding a Market**: Simply edit `cotdata/src/cotdata/registry.yaml` and add the new market under its respective Asset Class (e.g., Copper or Cocoa). The system natively handles metadata such as `is_equity` and complex `hist_codes` structures.
- **Environment Override**: The `COTDATA_REGISTRY` environment variable allows you to point to a centralized `registry.yaml` file. For instance, you could place your `registry.yaml` inside your synced `$COTDATA_STORE`. This ensures both your Windows producer and Mac consumer are always looking at the exact same asset definitions, without needing to `git pull` the Python repository.

## Atomic Store

The store uses **atomic writes**. Consumers can safely query the store via `get_prices` or `get_cot` even while `cotdata-update` is actively downloading and writing new data.

## Diagnostics

You can verify your Norgate subscription's data quality and system configuration using the included smoke test script. Run it on your Windows producer machine:
```bash
python tests/test_adjustment.py
```
This diagnostic script tests:
1. **Local Communication**: Verifies that Python can successfully communicate with the Norgate Data Updater running in the background.
2. **Subscription Access**: Validates that your Norgate subscription is active and has the required CME futures data package enabled.
3. **Roll Gap Validation**: Mathematically proves whether your Norgate Data Updater is configured globally to return back-adjusted or unadjusted continuous contracts. It hunts for artificial calendar-spread gaps at contract roll dates to ensure you are receiving gap-free, continuous data, which is absolutely vital for accurate stop-loss modeling.

## COT Formats Explained

The CFTC publishes positioning data in three distinct formats. `cotdata` manages all three to ensure complete market coverage and the deepest possible historical backtesting.

1. **Legacy Format (1986–Present)**
   - **Scope:** All markets.
   - **Categories:** Divides traders broadly into **Commercial** (hedgers) and **Non-Commercial** (large speculators). 
   - **Use Case:** This is the original format. While its broad categories make it less precise for modern analysis, it is the only format that provides data prior to 2006, making it essential for long-term historical backtesting.

2. **Disaggregated Format (DIS) (2006–Present)**
   - **Scope:** Physical commodities only (Agriculture, Energy, Metals).
   - **Categories:** Splits traders into four granular groups: **Producer/Merchant** (classic hedgers), **Swap Dealers** (financial intermediaries), **Managed Money** (hedge funds / CTAs), and **Other Reportables**.
   - **Use Case:** Provides a much clearer view of the "Smart Money" (Managed Money) in commodity markets.

3. **Traders in Financial Futures (TFF) (2006–Present)**
   - **Scope:** Financial markets only (Equities, Rates, Currencies).
   - **Categories:** The financial counterpart to Disaggregated. Splits traders into: **Dealer/Intermediary** (sell-side), **Asset Manager** (pension/mutual funds), **Leveraged Funds** (hedge funds / CTAs), and **Other Reportables**.
   - **Use Case:** The definitive source for tracking speculative flow (Leveraged Funds) in financial markets.

## Data Schemas

The canonical store uses standard Parquet files. When loaded into a pandas DataFrame (e.g., via `pd.read_parquet()`), they conform to the following schemas.

### Price Data (`prices/{symbol}_{adjustment}.parquet`)
The primary source for price history (Norgate Data). Indexed by tz-naive `Date`. The pipeline automatically downloads both the back-adjusted (`backadj`) series for signals/stops and the unadjusted (`unadj`) series for true transaction cost modeling.

| Column | Type | Description |
|--------|------|-------------|
| `Date` | DatetimeIndex | Trading day (tz-naive, normalized to midnight). |
| `Open` | float | Opening price. |
| `High` | float | High price. |
| `Low` | float | Low price. |
| `Close` | float | Exchange settlement close. |
| `Volume` | float | Trading volume. |
| `Open Interest` | float | Total open interest. |
| `Delivery Month` | float | Expiration month of the active contract (e.g. `202609`). Used to detect contract rolls. |

### Contract Specifications (`metadata/contract_specs.parquet`)
The primary source for contract metadata (Norgate Data). Used for exact point-value risk sizing and transaction cost models.

| Column | Type | Description |
|--------|------|-------------|
| `Symbol` | string | Internal ticker symbol (e.g., `ES`). |
| `Norgate_Symbol` | string | Raw Norgate symbol used to query the API (e.g., `&ES_CCB`). |
| `Name` | string | Full name of the contract. |
| `Exchange` | string | Name of the listing exchange. |
| `Group` | string | Norgate asset classification group. |
| `Contract Size` | float | Size multiplier (e.g., $50 for ES). Also called Point Value. |
| `Tick Size` | float | Minimum price fluctuation (e.g., 0.25 for ES). |
| `Tick Value` | float | Dollar value of one tick (`Tick Size` * `Contract Size`). |
| `Point Value` | float | Same as `Contract Size`. |
| `Currency` | string | Base currency of the contract. |
| `Margin` | float | Initial margin requirement (if provided by Norgate). |

### COT Legacy Data (`cot_legacy/{code}.parquet`)
The primary source for Legacy positioning data (CFTC Legacy Futures Report). **History starts in 1986.** Indexed by tz-naive `Report_Date_as_MM_DD_YYYY`.

> [!NOTE]
> **Legacy Reports**: The Legacy reports are broken down by exchange. These reports have a futures only report and a combined futures and options report. Legacy reports break down the reportable open interest positions into two classifications: non-commercial and commercial traders. The `cotdata` pipeline strictly downloads the **Futures-only** reports (located at `https://www.cftc.gov/files/dea/history/dea_fut_xls_{YEAR}.zip`).

> [!NOTE]
> **Column Subset**: While the raw CFTC `.xls` files contain [well over 100 columns](https://www.cftc.gov/MarketReports/CommitmentsofTraders/HistoricalViewable/cotvariableslegacy.html) (including spreading, concentration ratios, etc.), the producer pipeline explicitly discards them. The parquet files only maintain the exact 15-column subset listed below to keep the file sizes extremely small and strictly focused on what the downstream models require. To include additional data points from the raw reports, simply add the exact CFTC column name to the `TARGET_COLS` list inside `src/cotdata/providers/cftc.py`.

| Column | Type | Description |
|--------|------|-------------|
| `Report_Date_as_MM_DD_YYYY` | DatetimeIndex | Reporting date (typically Tuesday). |
| `Market_and_Exchange_Names` | string | Name of the contract and exchange. |
| `CFTC_Contract_Market_Code` | string | 6-digit CFTC contract code. |
| `Open_Interest_All` | float | Total open interest for the contract. |
| `Comm_Positions_Long_All` | float | Commercial Long positions. |
| `Comm_Positions_Short_All` | float | Commercial Short positions. |
| `NonComm_Positions_Long_All` | float | Non-Commercial (Large Speculator) Long positions. |
| `NonComm_Positions_Short_All` | float | Non-Commercial (Large Speculator) Short positions. |
| `NonRept_Positions_Long_All` | float | Non-Reportable (Small Speculator) Long positions. |
| `NonRept_Positions_Short_All` | float | Non-Reportable (Small Speculator) Short positions. |
| `Traders_Tot_All` | float | Total number of reportable traders. |
| `Traders_Comm_Long_All` | float | Number of Commercial Long traders. |
| `Traders_Comm_Short_All` | float | Number of Commercial Short traders. |
| `Traders_NonComm_Long_All` | float | Number of Non-Commercial Long traders. |
| `Traders_NonComm_Short_All` | float | Number of Non-Commercial Short traders. |

### COT Disaggregated Data (`cot_disagg/{code}.parquet`)
The primary source for entity-specific positioning and trader counts (CFTC Disaggregated Futures-Only Report). **History starts in 2006.** Indexed by tz-naive `Report_Date_as_MM_DD_YYYY`.

> [!NOTE]
> **Lossless Image**: Unlike the Legacy schema which filters down to 10 specific columns, the Disaggregated parquets are a **lossless image** of the source CFTC `txt` files. They contain all granular entity groups (Money Manager, Swap Dealer, Producer/Merchant, Other Reportable) and their respective `Traders_*` counts (e.g., `Traders_Tot_All`, `Traders_M_Money_Long_All`). This is the required store for computing Position Size and Clustering metrics.

### COT Traders in Financial Futures (TFF) Data (`cot_tff/{code}.parquet`)
The primary source for entity-specific positioning and trader counts for Financial markets (CFTC Traders in Financial Futures Futures-Only Report). **History starts in 2006.** Indexed by tz-naive `Report_Date_as_MM_DD_YYYY`.

> [!NOTE]
> **Financials Counterpart**: TFF is the exact counterpart to Disaggregated reports, used exclusively for financial markets (Equities, FX, Rates) which do not have Disaggregated reports.

> [!NOTE]
> **Lossless Image**: Like Disaggregated, TFF parquets are a **lossless image** of the source CFTC `txt` files. They contain the financial entity groups (`Dealer`, `Asset_Mgr`, `Lev_Money`, `Other_Rept`) and their respective `Traders_*` counts. This is the required store for computing Position Size and Clustering metrics for financial assets.
