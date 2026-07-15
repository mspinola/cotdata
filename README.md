# cotdata

[![CI](https://github.com/mspinola/cotdata/actions/workflows/python-test.yml/badge.svg)](https://github.com/mspinola/cotdata/actions/workflows/python-test.yml)
[![PyPI version](https://img.shields.io/pypi/v/cotdata.svg)](https://pypi.org/project/cotdata/)
[![Python versions](https://img.shields.io/pypi/pyversions/cotdata.svg)](https://pypi.org/project/cotdata/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**A local, file-based data layer for futures prices and CFTC Commitments of Traders (COT) positioning.**

cotdata separates *fetching* data (a "producer" that talks to vendors) from *using* it (any number of "consumers" that just read Parquet through a small, stable API). Point every tool at one synced store, and none of them ever call a vendor SDK at runtime — so the same data feeds your research, backtests, and dashboards identically, on any OS.

- **One store, many readers.** Consumers `import cotdata` and read; they never touch a vendor SDK. Swapping a data vendor is a producer-only change.
- **Free COT, optional paid prices.** CFTC Commitments of Traders data (1986–present) downloads free from cftc.gov on any OS. Futures prices/specs come from [Norgate](https://norgatedata.com/) (paid, Windows) and are optional.
- **Cross-platform reads.** Produce on Windows (for Norgate); read anywhere (Mac/Linux/Windows), offline.
- **Predecessor stitching.** `get_cot()` transparently stitches migrated CFTC codes (e.g. the Russell 2000) and rescales tick-size changes (e.g. Lumber) into one continuous series.
- **Atomic writes.** Read the store safely even while the producer is downloading and writing.
- **New-data signal.** Every run writes a structured `status.json` so downstream tools can poll one file to detect fresh data.

## Data sources at a glance

| Data | Source | Cost | Runs on |
|------|--------|------|---------|
| CFTC COT — legacy / disaggregated / TFF | [cftc.gov](https://www.cftc.gov/) | **Free** | any OS |
| Futures prices + contract specs | [Norgate Data](https://norgatedata.com/) | Paid subscription | **Windows** (producer only) |
| *Reading the store* (any of the above) | — | Free | any OS |

## Contents

- [Quickstart](#quickstart) · [How it works](#how-it-works) · [Reading data](#reading-data-consumer) · [Producing data](#producing-data-producer) · [Scheduling on Windows](#scheduling-on-windows-task-scheduler) · [Operations](#operations) · [Concepts & design](#concepts--design) · [Reference: schemas](#reference-data-schemas) · [Reference: COT formats](#reference-cot-formats-explained) · [Diagnostics](#diagnostics) · [Contributing](#contributing) · [License](#license)

## Quickstart

The fastest zero-cost path uses free CFTC COT data — no account, any OS:

```bash
pip install cotdata
export COTDATA_STORE=~/cotdata_store          # where the shared store lives
cotdata-update --cot-legacy                   # free CFTC download (first run pulls history; cached after)
python -c "import cotdata; print(cotdata.get_cot('ES').tail())"
```

That downloads the CFTC Legacy COT history and reads the S&P 500 (ES) positioning back out:

```
                           Open_Interest_All  Comm_Positions_Long_All  Comm_Positions_Short_All  NonComm_Positions_Long_All  NonComm_Positions_Short_All
Report_Date_as_MM_DD_YYYY
2026-06-23                           1980254                  1444102                   1531232                      251385                       286833
2026-06-30                           1967167                  1422155                   1509889                      249934                       287526
2026-07-07                           1969636                  1435736                   1502199                      244103                       286994
```

Futures **prices** additionally require a Norgate subscription on Windows — see [Producing data](#producing-data-producer).

## How it works

The **store is the API boundary** — not Python imports. Producers write Parquet + `manifest.json`; consumers only read. Nobody touches a vendor SDK at app runtime, so swapping a vendor is a producer-only change.

```
        PRODUCER  —  runs where each source is reachable
           Norgate export (Windows)      CFTC COT download (any OS)
                       │                              │
                       └──────────────┬───────────────┘
                                      ▼   write parquet + manifest
        ┌────────────────────────────────────────────────────────────┐
        │ CANONICAL STORE   ($COTDATA_STORE)                         │
        │   prices/   cot_legacy/   cot_disagg/   cot_tff/           │
        │   metadata/   manifest.json   status.json                  │
        └────────────────────────────────────────────────────────────┘
                                      │   read  (offline, any OS)
                       ┌──────────────┴───────────────┐
                       ▼                              ▼
             your signal research        your backtest / dashboards

        both just:  import cotdata      ·      store synced via rsync / Dropbox / S3
```

The store layout:

- `prices/{symbol}_{adjustment}.parquet` — Open/High/Low/Close/Volume/Open Interest, tz-naive `Date` index. `adjustment` ∈ {`backadj`, `unadj`}. Close = exchange settlement.
- `cot_legacy/{symbol}_{code}.parquet` — weekly CFTC Legacy positioning.
- `cot_disagg/{symbol}_{code}.parquet` — weekly CFTC Disaggregated positioning.
- `cot_tff/{symbol}_{code}.parquet` — weekly CFTC Traders in Financial Futures positioning.
- `metadata/contract_specs.parquet` — Norgate contract specifications (tick size, point value, margin).
- `manifest.json` — per-table `last_date`, `n_rows`, `source`, `updated_at`, `schema_version`.
- `status.json` — machine-readable new-data signal for downstream tools (see [Operations](#operations)).

## Reading data (consumer)

Set `COTDATA_STORE` to the synced store directory, then:

```python
import cotdata

# Prices — pick the adjustment that matches your use:
signals = cotdata.get_prices("ES", adjustment="backadj")  # signals + stops (gap-free rolls)
sizing  = cotdata.get_prices("ES", adjustment="unadj")    # position sizing (true dollar prices)

# COT — three CFTC report families:
legacy  = cotdata.get_cot("ES", report="legacy")   # Commercial / Non-Commercial
disagg  = cotdata.get_cot("ES", report="disagg")   # Managed Money, Swap Dealers, ... (commodities)
tff     = cotdata.get_cot("ES", report="tff")      # Leveraged Funds, Asset Managers, ... (financials)
```

A price frame (`get_prices("ES", adjustment="backadj").tail(3)`):

```
               Open     High      Low    Close     Volume  Open Interest
Date
2026-07-10  7587.25  7628.75  7552.75  7620.25  1078031.0      1966297.0
2026-07-13  7607.00  7615.25  7547.25  7563.00  1274520.0      1945908.0
2026-07-14  7557.00  7613.75  7531.50  7591.25  1139735.0            0.0
```

**Predecessor stitching & scaling:** `get_cot()` doesn't just read a file — it stitches historical CFTC codes for contracts that migrated exchanges (e.g. the Russell 2000) and rescales data for contracts that changed tick sizes (e.g. Lumber), so downstream models see one clean, continuous asset.

## Producing data (producer)

Run on the machine that can reach the source. Norgate prices require Windows; CFTC COT runs anywhere.

```bash
COTDATA_STORE=/store  cotdata-update --prices                    # Norgate prices, ALL registry symbols (Windows)
COTDATA_STORE=/store  cotdata-update --prices --symbols ES NQ    # ...or a subset
COTDATA_STORE=/store  cotdata-update --metadata                  # Norgate contract specs (Windows)
COTDATA_STORE=/store  cotdata-update --cot-legacy                # CFTC Legacy (any OS)
COTDATA_STORE=/store  cotdata-update --cot-disagg                # CFTC Disaggregated (any OS)
COTDATA_STORE=/store  cotdata-update --cot-tff                   # CFTC Traders in Financial Futures (any OS)
COTDATA_STORE=/store  cotdata-update --cot-all                   # all three CFTC COT reports
```

`--prices` with no `--symbols` updates every symbol in the registry; add `--symbols` to scope it. Each run prints a per-symbol line with the date advance (e.g. `ES: … [2026-07-13 -> 2026-07-14]`) and a summary footer (OK/failed counts, rows written, elapsed, newest date). A run **exits non-zero** if a fetch hard-fails (Norgate/CFTC unreachable), so a scheduler can retry — see [Scheduling on Windows](#scheduling-on-windows-task-scheduler).

### Installation for the producer

```bash
pip install "cotdata[norgate]"     # adds the norgatedata dependency (Windows)
```

The `norgatedata` package talks locally to the Norgate Data Updater application — there are no API keys. You just need the Updater installed, authenticated, and running.

### Scheduling on Windows (Task Scheduler)

The goal: **prices daily**, and **COT caught within minutes of its Friday ~3:30pm ET release** while surviving holiday delays. Two properties make this simple:

- **Idempotent.** `cotdata-update --cot-*` HEAD-checks each CFTC year zip and skips it if unchanged, so re-running is cheap. Running before the release lands is a harmless no-op; the first run *after* it lands picks it up.
- **Fails loudly.** A run exits non-zero only on a hard fetch error (source unreachable) — *not* when there's simply no new data yet. So Task Scheduler's "restart on failure" retries real errors without firing on ordinary "nothing new" runs.

Point each task at a small wrapper that sets `COTDATA_STORE` and calls the venv's `cotdata-update`. Prices use `--require-final` so they run only once Norgate's **Final** futures prices are in (not interim bars) — `run-prices.cmd`:

```bat
@echo off
set COTDATA_STORE=C:\path\to\store
C:\path\to\.venv\Scripts\cotdata-update.exe --prices --metadata --require-final
```

(`run-cot.cmd` is the same with `--cot-all`.) Then create three tasks — times are the **machine's local** time; convert from ET if it isn't on Eastern:

```bat
:: 1) Prices — fire at the Continuous Futures Final (~8:55pm ET); --require-final + restart
::    below keep retrying (cheap no-ops) until Norgate has actually pulled the Finals.
schtasks /Create /TN "cotdata prices" /TR "C:\path\run-prices.cmd" /SC DAILY /ST 20:55

:: 2) COT — daily morning catch-up for holiday-delayed releases and as a safety net
schtasks /Create /TN "cotdata COT (catch-up)" /TR "C:\path\run-cot.cmd" /SC DAILY /ST 08:10
```

The **Friday release window** needs a *repeating* trigger, which `schtasks` can't express on a weekly schedule (`/ET` and `/DU` are MINUTE/HOURLY only). Create it in PowerShell instead — weekly on Friday at 3:25pm ET, repeating every 5 min for 45 minutes so it catches the ~3:30 release within minutes:

```powershell
$act = New-ScheduledTaskAction -Execute "C:\path\run-cot.cmd"
$trg = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Friday -At 3:25PM
# borrow a repetition pattern (schtasks/New-ScheduledTaskTrigger can't set it directly on a weekly trigger):
$rep = (New-ScheduledTaskTrigger -Once -At 3:25PM `
        -RepetitionInterval (New-TimeSpan -Minutes 5) `
        -RepetitionDuration (New-TimeSpan -Minutes 45)).Repetition
$trg.Repetition = $rep
Register-ScheduledTask -TaskName "cotdata COT (Fri release)" -Action $act -Trigger $trg
```

(Or in the Task Scheduler GUI: New Task → Trigger *Weekly, Friday, 3:25pm* → check *"Repeat task every: 5 minutes for a duration of: 45 minutes."*)

**Event-driven prices with `--require-final`.** cotdata reads two Norgate databases: **Continuous Futures** (the `&ES` / `_CCB` series) and **Futures** (the individual `ES-2026H` contracts used to reconstruct volume). Their **Final** prices land ~8:40pm ET (Futures) and ~8:55pm ET (Continuous Futures), but your Norgate Data Updater still has to *pull* them on its next poll. Rather than guess a fixed time, `--require-final` checks `norgatedata.last_database_update_time()` for both databases and only fetches once each has been refreshed at/after `--final-cutoff` (default `20:55` local — set it to your machine's local equivalent of 8:55pm ET). Until then it **defers with a non-zero exit**, so the restart setting below turns "fire at 8:55pm" into "run the moment NDU has the Finals."

**Retry / wait via Task Scheduler:** open each task → **Settings** tab → check *"If the task fails, restart every: 10 minutes, up to 6 times."* This does double duty: it retries transient fetch errors, and — for the price task — it waits out the gap between 8:55pm and NDU actually pulling the Finals (each retry is a cheap `last_database_update_time` check that exits immediately until ready). On a genuine no-session day the retries simply exhaust, harmlessly.

**Monitoring:** after any run, `status.json` reflects `newest_data.<domain>` and `last_run.symbols_failed` — poll it to confirm the Friday COT actually advanced, or to alert on failures (see [Operations](#operations)).

> The Friday window intentionally over-polls (8 runs, 3:25–4:00pm); idempotency makes every run after the release lands a no-op. If you'd rather actively wait out *late* releases, a wrapper can loop until `status.json`'s `newest_data.cot_legacy` reaches the expected Tuesday — but daily catch-up already covers holiday slips with far less machinery.

## Operations

Read-only and maintenance commands, all cross-platform (they work off the store, no network):

```bash
cotdata-update --check       # store status: row counts, newest data, staleness
cotdata-update --reconcile   # prune stale manifest entries (see below)
```

`--check` reports per-domain row counts, newest data date, last write, and any entries lagging behind their peers (a partial-run signal):

```
domain       entries         rows   newest data      last write (UTC)  behind
prices            84      829,096    2026-07-14  2026-07-15T10:15:24Z      1d
cot_legacy        44       70,201    2026-07-07  2026-07-14T04:26:55Z      8d
...
✓ all entries current (none lag behind their domain's newest).
```

### `status.json` — new-data signal for downstream tools

Every producer run writes `$COTDATA_STORE/status.json` (atomically, beside the data), so tools that trigger on fresh data poll one small structured file instead of scanning the store:

```json
{
  "generated_at": "2026-07-15T10:15:24Z",
  "schema_version": 2,
  "newest_data": { "prices": "2026-07-14", "cot_legacy": "2026-07-07", "cot_disagg": "2026-07-07", "cot_tff": "2026-07-07" },
  "domains":     { "prices": { "newest_data": "2026-07-14", "last_write": "2026-07-15T10:15:24Z", "entries": 84, "rows": 829096, "lagging": 0 }, "...": {} },
  "last_run":    { "kinds": ["prices"], "ok": ["ES", "..."], "symbols_failed": [], "rows": 1658000, "seconds": 88, "at": "2026-07-15T10:15:24Z" }
}
```

**Polling contract:**
- To detect **new data**, compare `newest_data.<domain>` (e.g. `newest_data.prices`, `newest_data.cot_legacy`) against your last-seen value. It advances **only when genuinely new daily data arrives** — a no-op run leaves it unchanged.
- To detect that **a run happened at all** (new data or not), use `generated_at`.
- `last_run` carries the most recent run's outcome (which domains, per-symbol failures) for alerting.

Prices and each COT report are separate domains, so a price-triggered tool and a COT-triggered tool each watch their own key.

### `--reconcile` — manifest hygiene

COT tables are stored per code as **`{symbol}_{code}`** (e.g. `RTY_23977A`), so a symbol's current and predecessor (`hist_codes`) contracts are both attributable to it. `--reconcile` drops manifest entries whose parquet file is missing — bare-code ghosts and retired domains left by older naming schemes — so `--check` and `status.json` show only real, consistently-named entries. It never touches data (only removes bookkeeping for files that don't exist).

## Concepts & design

### Back-adjusted vs unadjusted prices

Futures contracts expire, forcing traders to "roll" into the next contract, which usually trades at a slightly different price. Simply stitching contracts together creates artificial price gaps, so cotdata stores two series:

- **`backadj` (signals & stops).** Gap-free arithmetic rolls shift historical prices to align with the new contract, preserving the *true shape* and percentage moves. Use this for indicators, signals, and stop-losses to avoid false triggers on rollover gaps.
- **`unadj` (position sizing).** Back-adjustment shifts historical prices (sometimes negative), so you can't use it for dollar values. Use `unadj` (raw, real-life prices) for that day to compute true dollar risk and contract counts.

### Providers & authentication

- **Norgate Data (primary prices).** No Python API keys — the `norgatedata` package talks locally to the Norgate Data Updater app, which must be installed, authenticated, and running on Windows.
- **Databento (dormant / intraday).** Kept as a dormant provider for potential intraday use. If enabled, provide `DATABENTO_API_KEY` via the environment.

### The symbol registry

The supported futures contracts are defined in a YAML registry, so adding a market needs no code:

- **Add a market:** edit `src/cotdata/registry.yaml` under its asset class. The registry handles metadata like `is_equity` and predecessor `hist_codes`.
- **Centralize it:** set `COTDATA_REGISTRY` to a shared `registry.yaml` (e.g. inside `$COTDATA_STORE`) so producer and consumers use identical asset definitions without a `git pull`.

### Atomic store

The store uses **atomic writes** (write-temp-then-rename). Consumers can safely query via `get_prices` / `get_cot` even while `cotdata-update` is actively downloading and writing.

## Local development

```bash
uv venv                                     # create .venv
uv pip install -e .                         # install cotdata + deps
export COTDATA_STORE=/path/to/synced/store  # the shared store
uv run pytest                               # run the tests
```

On the Windows producer, install the Norgate extra with `uv pip install -e ".[norgate]"` (tested on Python 3.10, within Norgate's supported versions). Use `uv run <cmd>`, or activate with `source .venv/bin/activate` (Mac/Linux) / `.venv\Scripts\activate` (Windows).

## Reference: Data schemas

The canonical store uses standard Parquet files. Loaded with `pd.read_parquet()`, they conform to the following schemas.

### Price Data (`prices/{symbol}_{adjustment}.parquet`)
Primary price history (Norgate Data), indexed by tz-naive `Date`. The pipeline downloads both the back-adjusted (`backadj`) series for signals/stops and the unadjusted (`unadj`) series for true transaction-cost modeling.

**Reading reconstructed volume:** the reconstruction columns below are internal storage. Consumers should not read `Volume_Reconstructed` directly — call `get_prices(symbol, volume="reconstructed")` and the `Volume` column is served as reconstructed-with-per-row-raw-fallback, plus a `Volume_Source` column for audit. The default `volume="front"` returns the front-month series unchanged (byte-identical to the pre-v2 API). See `docs/plan_promote_reconstructed_volume.md`.

**Schema versioning:** `schema_version` in `manifest.json` records the on-disk data version (v2 = reconstructed volume promoted). Consumers key cache invalidation on `cotdata.schema_version()` and can guard with `cotdata.require_schema(min_version)`.

| Column | Type | Description |
|--------|------|-------------|
| `Date` | DatetimeIndex | Trading day (tz-naive, normalized to midnight). |
| `Open` | float | Opening price. |
| `High` | float | High price. |
| `Low` | float | Low price. |
| `Close` | float | Settlement Close price. |
| `Volume` | float | Continuous contract trading volume (front-month only). |
| `Open Interest` | float | Continuous contract open interest. |
| `Volume_Reconstructed` | float | True market volume (sum of First and Second contract). Differs from raw `Volume` by symbol — typically higher for products whose rolls spread volume across contracts, but roughly equal or lower for symbols with a near-empty back month (e.g. crypto). Not a drop-in replacement. |
| `Volume_Source` | string | `reconstructed` if First+Second available, `raw` fallback if not. |
| `FirstVolume` / `SecondVolume` | float | Trading volume of the specific first and second expiring contracts. |
| `FirstContract` / `SecondContract` | string | Contract names for the first and second expirations (e.g., `ES-2024H`). |
| `Delivery Month` | float | Expiration month of the active contract (e.g. `202609`). Used to detect contract rolls. |

### Contract Specifications (`metadata/contract_specs.parquet`)
Contract metadata (Norgate Data), used for exact point-value risk sizing and transaction cost models.

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

### COT Legacy Data (`cot_legacy/{symbol}_{code}.parquet`)
Legacy positioning data (CFTC Legacy Futures Report). **History starts in 1986.** Indexed by tz-naive `Report_Date_as_MM_DD_YYYY`.

> [!NOTE]
> **Legacy Reports**: broken down by exchange, with futures-only and combined futures-and-options variants. Legacy classifies reportable open interest into non-commercial and commercial traders. The `cotdata` pipeline strictly downloads the **Futures-only** reports (`https://www.cftc.gov/files/dea/history/dea_fut_xls_{YEAR}.zip`).

> [!NOTE]
> **Column Subset**: The raw CFTC `.xls` files contain [well over 100 columns](https://www.cftc.gov/MarketReports/CommitmentsofTraders/HistoricalViewable/cotvariableslegacy.html); the pipeline keeps the focused 15-column subset below to keep files small. To include more, add the exact CFTC column name to `TARGET_COLS` in `src/cotdata/providers/cftc.py`.

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

### COT Disaggregated Data (`cot_disagg/{symbol}_{code}.parquet`)
Entity-specific positioning and trader counts (CFTC Disaggregated Futures-Only Report). **History starts in 2006.** Indexed by tz-naive `Report_Date_as_MM_DD_YYYY`.

> [!NOTE]
> **Lossless Image**: Unlike the filtered Legacy schema, the Disaggregated parquets are a **lossless image** of the source CFTC `txt` files — all granular entity groups (Money Manager, Swap Dealer, Producer/Merchant, Other Reportable) and their `Traders_*` counts. Required for computing Position Size and Clustering metrics.

### COT Traders in Financial Futures (TFF) Data (`cot_tff/{symbol}_{code}.parquet`)
Entity-specific positioning and trader counts for financial markets (CFTC TFF Futures-Only Report). **History starts in 2006.** Indexed by tz-naive `Report_Date_as_MM_DD_YYYY`.

> [!NOTE]
> **Financials Counterpart**: TFF is the exact counterpart to Disaggregated, used for financial markets (Equities, FX, Rates), which have no Disaggregated report.

> [!NOTE]
> **Lossless Image**: Like Disaggregated, TFF parquets are a **lossless image** of the source CFTC `txt` files — the financial entity groups (`Dealer`, `Asset_Mgr`, `Lev_Money`, `Other_Rept`) and their `Traders_*` counts.

## Reference: COT formats explained

The CFTC publishes positioning data in three formats; `cotdata` manages all three for complete coverage and the deepest history.

1. **Legacy (1986–Present)** — *all markets.* Divides traders into **Commercial** (hedgers) and **Non-Commercial** (large speculators). The only format with pre-2006 data, so it's essential for long-term backtesting.
2. **Disaggregated / DIS (2006–Present)** — *physical commodities only* (Agriculture, Energy, Metals). Splits traders into **Producer/Merchant**, **Swap Dealers**, **Managed Money**, and **Other Reportables** — a clearer view of "smart money" (Managed Money) in commodities.
3. **Traders in Financial Futures / TFF (2006–Present)** — *financial markets only* (Equities, Rates, Currencies). Splits traders into **Dealer/Intermediary**, **Asset Manager**, **Leveraged Funds**, and **Other Reportables** — the definitive source for speculative flow (Leveraged Funds) in financials.

## Diagnostics

Verify your Norgate subscription and configuration with the included smoke test, on the Windows producer:

```bash
python tests/test_adjustment.py
```

It checks: (1) **Local communication** — Python can reach the Norgate Data Updater; (2) **Subscription access** — your subscription includes the required CME futures package; (3) **Roll-gap validation** — proves whether the Updater is returning back-adjusted (gap-free) vs unadjusted continuous contracts, by hunting for calendar-spread gaps at roll dates. Gap-free data is vital for accurate stop-loss modeling.

## Contributing

Issues and pull requests are welcome. Please see [CONTRIBUTING.md](CONTRIBUTING.md) for setup, tests, and conventions. When filing a bug, include your OS — Norgate features require Windows, while store reads and CFTC COT run anywhere.

## License

Released under the MIT License — see [LICENSE](LICENSE).
