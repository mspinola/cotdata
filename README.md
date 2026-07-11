# cotdata

Canonical data layer for the COT / futures-strategy stack. It exists so that quantitative analysis toolset never fetch data directly. They read a shared, file-based store through a stable API.

```
        PRODUCER  (runs where each source is reachable)
   Windows: Norgate export        anywhere: CFTC COT download
                       │  writes   │
                       ▼           ▼
        ┌───────────────────────────────────────┐
        │  CANONICAL STORE   ($COTDATA_STORE)    │
        │  prices/*.parquet  cot/*.parquet       │   ← synced (rsync / Dropbox / S3)
        │  manifest.json     (the contract)      │
        └───────────────────────────────────────┘
                       ▲           ▲   reads (offline, cross-platform)
        ┌──────────────┴───┐  ┌────┴──────────────┐
        │   COT analyzer   │  │  analysis toolset │      both:  import cotdata
        └──────────────────┘  └───────────────────┘
```

## Workspace setup (uv)

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

- `prices/{symbol}_{adjustment}.parquet` — Open/High/Low/Close/Volume/Open Interest,
  tz-naive `Date` index. `adjustment` ∈ {`backadj`, `unadj`}. Close = exchange settlement.
- `cot/{code}.parquet` — weekly CFTC positioning.
- `manifest.json` — per-table `last_date`, `n_rows`, `source`, `updated_at`, `schema_version`.

## Consumer

```python
import cotdata
df = cotdata.get_prices("ES", adjustment="backadj")   # USE THIS FOR SIGNALS + STOPS
sz = cotdata.get_prices("ES", adjustment="unadj")     # USE FOR POSITION SIZING / POINT VALUE
cot = cotdata.get_cot("ES")
```

Set `COTDATA_STORE` to the synced store directory. 

**Predecessor Stitching & Scaling:** The `get_cot()` function doesn't just read a file; it dynamically stitches historical CFTC codes for contracts that migrated exchanges (like the Russell 2000) or rescales data for contracts that changed tick sizes (like Lumber). Downstream models see one clean, continuous asset.

## Producer (run on the machine that can reach the source)

```
COTDATA_STORE=/store  cotdata-update --prices --symbols ES NQ    # Norgate (Windows)
COTDATA_STORE=/store  cotdata-update --cot                       # CFTC (cross-platform)
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
