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
| Futures prices, back-adjusted | [Databento](https://databento.com/) GLBX.MDP3 | Paid per query | any OS |
| Futures prices, research-grade fallback | Yahoo Finance | **Free** | any OS |
| *Reading the store* (any of the above) | — | Free | any OS |

## Contents

- [Quickstart](#quickstart) · [How it works](#how-it-works) · [Reading data](#reading-data-consumer) · [Producing data](#producing-data-producer) · [Windows setup](docs/WINDOWS_SETUP.md) · [Scheduling on Windows](docs/WINDOWS_SCHEDULING.md) · [Scheduling on Linux](docs/LINUX_SCHEDULING.md) · [Operations](#operations) · [Concepts & design](#concepts--design) · [Reference: schemas](#reference-data-schemas) · [Reference: COT formats](#reference-cot-formats-explained) · [Diagnostics](#diagnostics) · [Development](#development) · [Contributing](#contributing) · [License](#license)

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

- `prices/{symbol}_{adjustment}.parquet` — Open/High/Low/Close/Volume/Open Interest, tz-naive `Date` index. `adjustment` ∈ {`backadj`, `unadj`} on disk; `propadj` is a third view **derived on read** (not stored). Close = exchange settlement.
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
milk    = cotdata.get_prices("DC", adjustment="propadj")  # ratio-adjusted: strictly positive, %-return preserving

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

Run on the machine that can reach the source. Norgate prices require Windows, CFTC COT runs anywhere, and a server without Norgate can build prices from Databento instead (see [Cross-platform prices without Norgate](#cross-platform-prices-without-norgate-databento)).

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

### Cross-platform prices without Norgate (databento)

A server that cannot run Norgate (for example the public dashboard host) can build the price store from Databento instead. One provider owns each symbol end to end, so this is a full replacement, not a blend. Install the extras and set the environment:

```bash
pip install "cotdata[databento,yahoo]"      # databento producer + the Yahoo fallback

export COTDATA_STORE=/path/to/store         # the store the dashboard reads
export COTDATA_PRICE_SOURCE=databento       # deployment default, so softs/MSCI fall to Yahoo
export DATABENTO_API_KEY=db-...             # for the paid ingest step only
# optional: export COTDATA_DATABENTO_RAW=/path/to/raw   # defaults to $COTDATA_STORE/_raw/databento
```

Then build the store in order (ingest before build):

```bash
cotdata-update --ingest-databento     # Stage 1 (PAID): raw .n.0/.n.1 ohlcv-1d + statistics -> raw store
cotdata-update --build-databento      # Stage 2 (FREE): additive back-adjustment -> $COTDATA_STORE/prices
cotdata-update --prices-yahoo         # softs, lumber, MSCI proxies (resolve to Yahoo on this deployment)
cotdata-update --cot-all              # CFTC COT, the dashboard needs it too
cotdata-update --check                # coverage, newest dates, staleness
```

- **Two stages, one paid.** Stage 1 is the only step that hits the API. It writes an append-only raw store and resumes from the last fetched date, so re-runs pull only new days. Stage 2 reads that raw store with no API cost, so the back-adjustment can be iterated offline. The raw store is producer-internal, so keep it out of any sync to consumers.
- **History starts 2010-06-06** (the GLBX floor), shallower than Norgate. Markets not on CME Globex (ICE softs, lumber, MSCI intl) fall back to Yahoo.
- **First-run check.** A healthy symbol prints `built unadj+backadj (N bars, K rolls)`. If it prints `no rolls detected`, back-adjustment is a no-op for that symbol, so investigate before trusting it.
- **Validate against Norgate** (optional gate) with `scripts/validate_databento_vs_norgate.py` if you have both stores.
- **Schedule** the two price commands nightly and `--cot-all` weekly — see [Scheduling on Linux](docs/LINUX_SCHEDULING.md).

### Producer halves: one host, one job

`cotdata` has two producers by design: the CFTC downloader (free, any OS) and the price
producer (Norgate needs Windows). Two entry points scope a host to one of them:

```bash
cotdata-cot     --cot-all                      # CFTC half, any OS
cotdata-prices  --prices --metadata --require-final   # price half, Windows for Norgate
cotdata-update  ...                            # both, for a single-machine deployment
```

Each scoped entry point refuses the other half's flags, so a price box cannot quietly
become a second COT producer racing the first. `--check` and `--reconcile` are read-only
and work from either.

Each half also owns its own manifest (`manifests/cot.json`, `manifests/prices.json`).
The manifest update is a read-modify-write, so two producers sharing one file eventually
lose an entry. The legacy top-level `manifest.json` is still written for consumers pinned
to an older cotdata, but current readers prefer the per-half files. See ADR-0007.

### Scheduling on Windows (Task Scheduler)

Full setup, including wrapper scripts, the three-task layout (daily prices, daily COT catch-up, Friday release-window poller), `--require-final` event-driven pricing, restart-on-failure retry settings, and Norgate/Task-Scheduler troubleshooting (notably: NDU needs an interactive session), is in **[docs/WINDOWS_SCHEDULING.md](docs/WINDOWS_SCHEDULING.md)**. Start with the [Windows Setup Guide](docs/WINDOWS_SETUP.md) first if Python/the venv/`COTDATA_STORE` aren't configured yet.

The short version: prices fire once daily near the Norgate Continuous Futures Final (~8:55pm ET), COT gets a daily morning catch-up plus a tight Friday-afternoon poll around its ~3:30pm ET release, and every task uses restart-on-failure so idempotent, cheap re-runs absorb both transient errors and "not published yet."

### Scheduling on Linux (cron)

Full setup, including wrapper scripts, the crontab entries (nightly prices, daily COT catch-up, Friday release-window poller), `flock` overlap protection, and troubleshooting (cron's bare environment, timezone conversion, `DATABENTO_API_KEY` not being picked up), is in **[docs/LINUX_SCHEDULING.md](docs/LINUX_SCHEDULING.md)**.

The short version: a databento server schedules the same way as the Windows/Norgate producer — prices nightly, COT gets a daily morning catch-up plus a tight Friday-afternoon poll around its ~3:30pm ET release, all idempotent and safe to over-run.

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

Futures contracts expire, forcing traders to "roll" into the next contract, which usually trades at a slightly different price. Simply stitching contracts together creates artificial price gaps, so cotdata stores two series and derives a third:

- **`backadj` (signals & stops).** Gap-free *arithmetic* (additive) rolls shift historical prices to align with the new contract, preserving *absolute* daily point moves. Use this for indicators, signals, and stop-losses to avoid false triggers on rollover gaps.
- **`unadj` (position sizing).** Back-adjustment shifts historical prices (sometimes negative), so you can't use it for dollar values. Use `unadj` (raw, real-life prices) for that day to compute true dollar risk and contract counts.
- **`propadj` (proportional / ratio adjustment — strictly positive).** Derived on read from `unadj` + `backadj`; preserves daily *percentage* returns and never goes non-positive. Use it for **low-priced, long-history contracts where additive back-adjustment accumulates roll gaps below zero** and breaks price-based stops and R-multiples. See *Class III Milk (DC)* below.

#### Why `propadj` exists — Class III Milk (DC)

Norgate publishes continuous futures in only two forms: unadjusted and **additive** back-adjusted (`_CCB`) — there is no native ratio-adjusted series. Additive adjustment subtracts each roll's calendar spread from all prior history, and for a low-priced, seasonal, ~29-year contract like **DC (Class III Milk, ~$15–20/cwt)** those gaps accumulate past zero: **46.7% of `DC_backadj` closes are ≤ 0** (range −9.83 to 23.09). A price-based stop, an R-multiple, or a percentage return is meaningless on a non-positive series, so CMR cannot use DC's `backadj` at all — even though DC is the flagship *new-asset-class* (Dairy) held-out generalization market.

`propadj` salvages it. Because the additive series `B` and unadjusted series `U` differ by an offset `O = B − U` that steps only at rolls, each roll's calendar spread is recoverable (`s = O[r−1] − O[r]`) and convertible to a multiplicative roll ratio `k = (U[r−1] + s)/U[r−1]`. Scaling each historical segment by the cumulative product of `k` (most-recent segment anchored to actual prices) yields a series that is **strictly positive over the full 1997–2026 history** (DC range 4.68–25.01), preserves within-segment percentage returns exactly, and is sign-identical to `backadj` on every day including rolls. It is a pure function of two already-stored series, so it needs no producer re-run — `get_prices("DC", adjustment="propadj")` works today. Recommendation: **CMR reads DC (and any similarly low-priced contract) with `adjustment="propadj"`.** Restricting DC to its positive-price era (2011→present, ~15y) or dropping it were the fallbacks; neither is needed.

### Providers & authentication

Which vendor prices a symbol is a deployment choice, not a fixed fact (the same ES is Norgate for local research and databento on a public-dash server). It is resolved when a producer runs, from three inputs: the deployment default `COTDATA_PRICE_SOURCE` (`norgate` if unset), per-symbol capability (the `norgate` / `databento` / `yahoo` mappings in the registry, `null` where a vendor has no series), and an optional per-symbol `price_source` override. A symbol uses its override if set, otherwise the default when that vendor can serve it, otherwise a Yahoo fallback where a ticker exists. Each producer writes only the symbols that resolve to it, so a symbol is never blended across vendors. One provider owns each symbol end to end.

- **Norgate Data (paid, Windows).** No Python API key. The `norgatedata` package talks locally to the Norgate Data Updater app, which must be installed, authenticated, and running on Windows. This is the default for local research (`cotdata-update --prices`).
- **Databento (paid, cross-platform).** A two-stage producer for a server that cannot run Norgate. Stage 1 (`cotdata-update --ingest-databento`) pulls raw `.n.0` / `.n.1` `ohlcv-1d` and `statistics` into an append-only raw store (`$COTDATA_DATABENTO_RAW`, else `_raw/databento` under the store). This is the only paid step, and it is resumable, so re-runs fetch only new dates. Stage 2 (`cotdata-update --build-databento`) derives the back-adjusted prices from the raw store with no API cost, so the build logic can be iterated offline. Set `DATABENTO_API_KEY` and `COTDATA_PRICE_SOURCE=databento`. History starts 2010-06-06, and markets not on CME Globex (ICE softs, lumber, MSCI intl) fall back to Yahoo. The raw store is producer-internal, so exclude it from any consumer sync.
- **Yahoo Finance (free, research-grade).** `cotdata-update --prices-yahoo` prices the markets that resolve to yfinance on this deployment: the MSCI ETF proxies always, plus the softs and lumber on a databento server. Expect gaps and silent revisions. This is not a production replacement for the paid feeds. Requires the `[yahoo]` extra.

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

**Reading reconstructed volume:** the reconstruction columns below are internal storage. Consumers should not read `Volume_Reconstructed` directly — call `get_prices(symbol, volume="reconstructed")` and the `Volume` column is served as reconstructed-with-per-row-raw-fallback, plus a `Volume_Source` column for audit. The default `volume="front"` returns the front-month series unchanged (byte-identical to the pre-v2 API).

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

## Ecosystem

cotdata is the *data* layer of a small, unbundled toolchain — it stops at "clean
data behind a stable API" on purpose. What you do with that data is a separate,
swappable step:

- **cotdata** *(this package)* — the *data* layer. One synced store of futures
  prices and CFTC COT positioning; many readers, no vendor SDK at read time.
- **[crucible](https://github.com/mspinola/crucible)** — the *edge* layer. Feed a
  signal built on cotdata frames into crucible and it tells you — with a
  confidence interval and a p-value — whether the trade-level edge is real, before
  you open a funded account.

The flow runs one direction: **`cotdata` (data) → your signal → `crucible`
(edge)**. Neither imports the other, so cotdata stays useful on its own for any
COT/futures research — crucible is just the most common thing to point at it next.

## Development

Want to contribute or work on cotdata locally? See [CONTRIBUTING.md](CONTRIBUTING.md) for:
- Virtual environment setup with `uv` or standard `pip`
- Running the test suite
- Platform-specific notes (Norgate is Windows-only; CFTC parsing runs anywhere)
- Code style guidelines

## Contributing

Issues and pull requests are welcome. Please see [CONTRIBUTING.md](CONTRIBUTING.md) for setup, tests, and conventions. When filing a bug, include your OS — Norgate features require Windows, while store reads and CFTC COT run anywhere.

## License

Released under the MIT License — see [LICENSE](LICENSE).
