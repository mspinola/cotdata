# cotdata

[![CI](https://github.com/mspinola/cotdata/actions/workflows/python-test.yml/badge.svg)](https://github.com/mspinola/cotdata/actions/workflows/python-test.yml)
[![PyPI version](https://img.shields.io/pypi/v/cotdata.svg)](https://pypi.org/project/cotdata/)
[![Python versions](https://img.shields.io/pypi/pyversions/cotdata.svg)](https://pypi.org/project/cotdata/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**A local, file-based data layer for CFTC Commitments of Traders (COT) positioning.**

cotdata separates *fetching* data (a "producer" that talks to vendors) from *using* it (any number of "consumers" that just read Parquet through a small, stable API). Point every tool at one synced store, and none of them ever call a vendor SDK at runtime — so the same data feeds your research, backtests, and dashboards identically, on any OS.

- **One store, many readers.** Consumers `import cotdata` and read; they never touch a vendor SDK. Swapping a data vendor is a producer-only change.
- **Free, on any OS.** CFTC Commitments of Traders data (1986–present) downloads free from cftc.gov. No account, no subscription.
- **Predecessor stitching.** `get_cot()` transparently stitches migrated CFTC codes (e.g. the Russell 2000) and rescales tick-size changes (e.g. Lumber) into one continuous series.
- **Atomic writes.** Read the store safely even while the producer is downloading and writing.
- **New-data signal.** Every run writes a structured `status.json` so downstream tools can poll one file to detect fresh data.

> [!IMPORTANT]
> **Price bars moved out of this package.** Under [ADR-0007](#ecosystem), cotdata is CFTC
> positioning only, and every daily bar — Norgate futures, Yahoo equities/ETFs, the
> `backadj`/`unadj`/`propadj` tiers, contract specifications — now lives in
> [**crucible-marketdata**](https://pypi.org/project/crucible-marketdata/). Replace
> `cotdata.get_prices(sym, adjustment)` with `marketdata.get_bars(sym, tier)` and point
> `MARKETDATA_STORE` at that store. The two packages keep separate stores and separate
> producers; nothing about `get_cot()` changed.
>
> The one price producer still here is **databento**, which has no marketdata equivalent
> yet (see [Cross-platform prices without Norgate](#cross-platform-prices-without-norgate-databento)).

## Data sources at a glance

| Data | Source | Cost | Runs on |
|------|--------|------|---------|
| CFTC COT — legacy / disaggregated / TFF / supplemental | [cftc.gov](https://www.cftc.gov/) | **Free** | any OS |
| Futures prices, back-adjusted | [Databento](https://databento.com/) GLBX.MDP3 | Paid per query | any OS |
| *Reading the store* | — | Free | any OS |
| Daily bars + contract specs | → [crucible-marketdata](https://pypi.org/project/crucible-marketdata/) | — | — |

## Contents

- [Quickstart](#quickstart) · [How it works](#how-it-works) · [Reading data](#reading-data-consumer) · [Producing data](#producing-data-producer) · [Windows setup](docs/WINDOWS_SETUP.md) · [Scheduling on Windows](docs/WINDOWS_SCHEDULING.md) · [Scheduling on Linux](docs/LINUX_SCHEDULING.md) · [Syncing the store](docs/SYNCING.md) · [Operations](#operations) · [Concepts & design](#concepts--design) · [COT vintage tracking](#cot-vintage-tracking-as-published-history) · [Reference: schemas](#reference-data-schemas) · [Reference: COT formats](#reference-cot-formats-explained) · [Diagnostics](#diagnostics) · [Development](#development) · [Contributing](#contributing) · [License](#license)

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

For daily **bars**, install [crucible-marketdata](https://pypi.org/project/crucible-marketdata/) alongside this and read `marketdata.get_bars("ES", "backadj")`.

## How it works

The **store is the API boundary** — not Python imports. Producers write Parquet + `manifest.json`; consumers only read. Nobody touches a vendor SDK at app runtime, so swapping a vendor is a producer-only change.

```
        PRODUCER  —  runs where each source is reachable
           CFTC COT download (any OS)    databento ingest/build (any OS, paid)
                       │                              │
                       └──────────────┬───────────────┘
                                      ▼   write parquet + manifest
        ┌────────────────────────────────────────────────────────────┐
        │ CANONICAL STORE   ($COTDATA_STORE)                         │
        │   cot_legacy/   cot_disagg/   cot_tff/                     │
        │   cot_supplemental/   prices/  (databento only)            │
        │   manifests/   status.json                                 │
        └────────────────────────────────────────────────────────────┘
                                      │   read  (offline, any OS)
                       ┌──────────────┴───────────────┐
                       ▼                              ▼
             your signal research        your backtest / dashboards

        both just:  import cotdata      ·      store synced via rsync / Dropbox / S3

        Daily bars live in a SEPARATE store:  $MARKETDATA_STORE, read with
        `import marketdata`.  Two stores, two producers, one seam (ADR-0007).
```

The store layout:

- `cot_legacy/{symbol}_{code}.parquet` — weekly CFTC Legacy positioning.
- `cot_disagg/{symbol}_{code}.parquet` — weekly CFTC Disaggregated positioning.
- `cot_tff/{symbol}_{code}.parquet` — weekly CFTC Traders in Financial Futures positioning.
- `cot_supplemental/{symbol}_{code}.parquet` — weekly CFTC Supplemental (Commodity Index Trader) positioning, 13 agricultural markets. **Futures-and-options combined**, unlike the three above.
- `prices/{symbol}_{adjustment}.parquet` — **databento-built bars only**, `adjustment` ∈ {`backadj`, `unadj`}. This is the ADR-0006 alternative producer's output, not a general bar store: read Norgate/Yahoo bars from `marketdata` instead.
- `manifests/{cot,prices}.json` — per-table `last_date`, `n_rows`, `source`, `updated_at`, `schema_version`, one file per producer half.
- `status.json` — machine-readable new-data signal for downstream tools (see [Operations](#operations)).
- `vintage/` — optional as-published (vintage) capture: retained raw CFTC downloads plus
  change-only observations and field-level revisions. Purely additive; the tables above are
  unchanged whether or not it is enabled. See [COT vintage tracking](#cot-vintage-tracking-as-published-history).

## Reading data (consumer)

Set `COTDATA_STORE` to the synced store directory, then:

```python
import cotdata

# COT — four CFTC report families:
legacy  = cotdata.get_cot("ES", report="legacy")   # Commercial / Non-Commercial
disagg  = cotdata.get_cot("ES", report="disagg")   # Managed Money, Swap Dealers, ... (commodities)
tff     = cotdata.get_cot("ES", report="tff")      # Leveraged Funds, Asset Managers, ... (financials)
cit     = cotdata.get_cot("CC", report="supplemental")  # Index Traders (13 ags, combined basis)
```

> [!WARNING]
> `supplemental` is **futures-and-options combined** and the other three are futures-only, so
> its `Open_Interest_All` is a different quantity for the same market and week. Do not
> difference or ratio across reports without accounting for that.

Daily **bars** come from the sibling package, against its own store:

```python
import marketdata                                       # pip install crucible-marketdata

signals = marketdata.get_bars("ES", "backadj")   # signals + stops (gap-free rolls)
sizing  = marketdata.get_bars("ES", "unadj")     # position sizing (true dollar prices)
milk    = marketdata.get_bars("DC", "propadj")   # ratio-adjusted, %-return preserving
```

```
               Open     High      Low    Close     Volume  Open Interest
Date
2026-07-10  7587.25  7628.75  7552.75  7620.25  1078031.0      1966297.0
2026-07-13  7607.00  7615.25  7547.25  7563.00  1274520.0      1945908.0
2026-07-14  7557.00  7613.75  7531.50  7591.25  1139735.0            0.0
```

Same frames, same symbols, same tier names — the import and the environment variable
(`MARKETDATA_STORE`) are what changed. `cotdata.get_prices` and `cotdata.roll_dates` are
gone rather than deprecated: left importable they would read a store the nightly job no
longer fills, and stale data is harder to notice than an `AttributeError`.

**Predecessor stitching & scaling:** `get_cot()` doesn't just read a file — it stitches historical CFTC codes for contracts that migrated exchanges (e.g. the Russell 2000) and rescales data for contracts that changed tick sizes (e.g. Lumber), so downstream models see one clean, continuous asset.

## Producing data (producer)

Run on the machine that can reach the source. CFTC COT runs anywhere; the optional Databento price build also runs anywhere (see [Cross-platform prices without Norgate](#cross-platform-prices-without-norgate-databento)). Norgate and Yahoo bars are produced by `marketdata-update --bars`, in the [sibling package](https://pypi.org/project/crucible-marketdata/).

```bash
COTDATA_STORE=/store  cotdata-update --cot-legacy                # CFTC Legacy (any OS)
COTDATA_STORE=/store  cotdata-update --cot-disagg                # CFTC Disaggregated (any OS)
COTDATA_STORE=/store  cotdata-update --cot-tff                   # CFTC Traders in Financial Futures (any OS)
COTDATA_STORE=/store  cotdata-update --cot-supplemental          # CFTC Supplemental / index traders (any OS)
COTDATA_STORE=/store  cotdata-update --cot-all                   # all four CFTC COT reports
COTDATA_STORE=/store  cotdata-vintage fetch                      # optional: capture as-published COT (any OS)
```

Each run prints a per-domain line and a summary footer. A run **exits non-zero** if a fetch hard-fails (CFTC or Databento unreachable), so a scheduler can retry — see [Scheduling on Linux](docs/LINUX_SCHEDULING.md).

### Cross-platform prices without Norgate (databento)

A server that cannot run Norgate (for example the public dashboard host) can build a price store from Databento instead. One provider owns each symbol end to end, so this is a full replacement, not a blend. Install the extra and set the environment:

```bash
pip install "cotdata[databento]"

export COTDATA_STORE=/path/to/store         # the store the dashboard reads
export DATABENTO_API_KEY=db-...             # for the paid ingest step only
# optional: export COTDATA_DATABENTO_RAW=/path/to/raw   # defaults to $COTDATA_STORE/_raw/databento
```

Then build the store in order (ingest before build):

```bash
cotdata-update --ingest-databento     # Stage 1 (PAID): raw .n.0/.n.1 ohlcv-1d + statistics -> raw store
cotdata-update --build-databento      # Stage 2 (FREE): additive back-adjustment -> $COTDATA_STORE/prices
cotdata-update --cot-all              # CFTC COT, the dashboard needs it too
cotdata-update --check                # coverage, newest dates, staleness
```

- **Two stages, one paid.** Stage 1 is the only step that hits the API. It writes an append-only raw store and resumes from the last fetched date, so re-runs pull only new days. Stage 2 reads that raw store with no API cost, so the back-adjustment can be iterated offline. The raw store is producer-internal, so keep it out of any sync to consumers.
- **History starts 2010-06-06** (the GLBX floor), shallower than Norgate. Markets not on CME Globex (ICE softs, lumber, MSCI intl) are not covered — take those from `marketdata`, which prices them off Yahoo.
- **First-run check.** A healthy symbol prints `built unadj+backadj (N bars, K rolls)`. If it prints `no rolls detected`, back-adjustment is a no-op for that symbol, so investigate before trusting it.
- **Read it back** with `cotdata.store.read_prices(symbol, adjustment)`. There is no consumer bar API here — this store is the alternative producer's output, and the general one is `marketdata.get_bars`.
- **Validate against Norgate** (optional gate) with `scripts/validate_databento_vs_norgate.py`, pointing `--norgate-store` at a `$MARKETDATA_STORE`.
- **Schedule** the two price commands nightly and `--cot-all` weekly — see [Scheduling on Linux](docs/LINUX_SCHEDULING.md).

> [!NOTE]
> Databento staying here is a deliberate exception to ADR-0007, not an oversight. It has
> no marketdata equivalent yet, and deleting it would destroy a validated alternative
> producer (ADR-0006) plus the only intraday-capable source in the fleet. When it is
> ported, `prices/`, `store.write_prices`/`read_prices` and the `prices` manifest half
> go with it and this package becomes COT-only in full.

### Producer halves: one host, one job

`cotdata` has two producers by design: the CFTC downloader and the databento price
builder. Two entry points scope a host to one of them:

```bash
cotdata-cot     --cot-all                              # CFTC half
cotdata-prices  --ingest-databento --build-databento   # price half
cotdata-update  ...                                    # both, for a single-machine deployment
```

Each scoped entry point refuses the other half's flags, so a price box cannot quietly
become a second COT producer racing the first. `--check` and `--reconcile` are read-only
and work from either.

Each half also owns its own manifest (`manifests/cot.json`, `manifests/prices.json`).
The legacy top-level `manifest.json` held both halves in ONE file, which was unsafe two
ways: the update is a read-modify-write, so two producers lose each other's entries, and
a file-level sync between two stores resolves it last-writer-wins and silently discards
one side. The per-half files are disjoint, so both problems go away.

**Nothing writes `manifest.json` any more.** Migrate a store once:

```bash
cotdata-update --migrate-manifests
```

Idempotent, and it never touches data. Until you run it, a domain missing from the
per-half files is still read from the aggregate with a warning. Delete `manifest.json`
once every consumer of that store is on this version. See ADR-0007.

### Scheduling on Windows (Task Scheduler)

The COT tasks (daily catch-up, Friday release-window poller), restart-on-failure retry settings, and Task-Scheduler troubleshooting are in **[docs/WINDOWS_SCHEDULING.md](docs/WINDOWS_SCHEDULING.md)**. Start with the [Windows Setup Guide](docs/WINDOWS_SETUP.md) first if Python/the venv/`COTDATA_STORE` aren't configured yet.

The short version: COT gets a daily morning catch-up plus a tight Friday-afternoon poll around its ~3:30pm ET release, and every task uses restart-on-failure so idempotent, cheap re-runs absorb both transient errors and "not published yet."

The Windows box is also where the **Norgate bar** job runs, but that is now `marketdata-update --bars --domain futures --require-final` from the sibling package, on its own schedule near the Norgate Continuous Futures Final (~8:55pm ET). Both packages' docs cover their own half.

### Scheduling on Linux (cron)

Full setup, including wrapper scripts, the crontab entries (nightly databento prices, daily COT catch-up, Friday release-window poller), `flock` overlap protection, and troubleshooting (cron's bare environment, timezone conversion, `DATABENTO_API_KEY` not being picked up), is in **[docs/LINUX_SCHEDULING.md](docs/LINUX_SCHEDULING.md)**.

The short version: a databento server schedules prices nightly, and COT gets a daily morning catch-up plus a tight Friday-afternoon poll around its ~3:30pm ET release, all idempotent and safe to over-run.

### Syncing the store between machines

A research Mac or a Linux dashboard is usually a **read-only replica** of a store
produced elsewhere. Prefer one producer writing everything and a strictly
one-directional sync. (The bar store syncs the same way, separately — it is a different
directory with a different producer.)

Two directories must be **excluded** for size: `_cache/` (cotdata's cache of downloaded
CFTC source zips) and `_raw/` (the **paid** databento raw store) are producer-internal
and together are ~70% of the bytes. The legacy `manifest.json` should be excluded too.

Anything a consumer put in the store by hand is a **correctness** issue rather than a
saving. No producer creates it, so a mirroring sync deletes it. Exclude it, but the real
fix is to keep it out of the store: the store belongs to its producer.

The same rule bites hardest on `vintage/` if you enable it, because that data cannot be
re-fetched: capture it on the **producer** so it syncs outward, or keep it outside the
mirrored store with `COTDATA_VINTAGE_ROOT`. Its provenance index is deliberately named
`snapshots.json`, since the usual `manifest.json` exclusion matches by name at any depth
and would otherwise strip it in transit, delivering raw archives with no index.

Consumer cloud sync (Dropbox, Google Drive) is a poor fit here: conflict copies land
inside the store, and on-demand placeholder files break `read_parquet` on the machine
doing research.

Full guidance, exclusion table and example scripts: **[docs/SYNCING.md](docs/SYNCING.md)**.

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
  "newest_data": { "prices": "2026-07-14", "cot_legacy": "2026-07-07", "cot_disagg": "2026-07-07", "cot_tff": "2026-07-07", "cot_supplemental": "2026-07-07" },
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

Futures contracts expire, forcing traders to "roll" into the next contract, which usually trades at a slightly different price. Simply stitching contracts together creates artificial price gaps, so two series are stored and a third is derived:

- **`backadj` (signals & stops).** Gap-free *arithmetic* (additive) rolls shift historical prices to align with the new contract, preserving *absolute* daily point moves. Use this for indicators, signals, and stop-losses to avoid false triggers on rollover gaps.
- **`unadj` (position sizing).** Back-adjustment shifts historical prices (sometimes negative), so you can't use it for dollar values. Use `unadj` (raw, real-life prices) for that day to compute true dollar risk and contract counts.
- **`propadj` (proportional / ratio adjustment).** Derived on read from `unadj` + `backadj`; preserves daily *percentage* returns. Use it for **low-priced, long-history contracts where additive back-adjustment accumulates roll gaps below zero** and breaks price-based stops and R-multiples — see *Class III Milk (DC)* below.

The databento builder here writes `backadj` and `unadj`. The tier *reader*, including the
derived `propadj`, is `marketdata.get_bars(symbol, tier)` — see that package's README for
the full treatment. Kept here because it is what the databento build produces and how to
judge whether it produced it correctly.

#### Why `propadj` exists — Class III Milk (DC)

Norgate publishes continuous futures in only two forms: unadjusted and **additive** back-adjusted (`_CCB`) — there is no native ratio-adjusted series. Additive adjustment subtracts each roll's calendar spread from all prior history, and for a low-priced, seasonal, ~29-year contract like **DC (Class III Milk, ~$15–20/cwt)** those gaps accumulate past zero: **46.7% of `DC_backadj` closes are ≤ 0** (range −9.83 to 23.09). A price-based stop, an R-multiple, or a percentage return is meaningless on a non-positive series, so CMR cannot use DC's `backadj` at all — even though DC is the flagship *new-asset-class* (Dairy) held-out generalization market.

`propadj` salvages it. Because the additive series `B` and unadjusted series `U` differ by an offset `O = B − U` that steps only at rolls, each roll's calendar spread is recoverable (`s = O[r−1] − O[r]`) and convertible to a multiplicative roll ratio `k = (U[r−1] + s)/U[r−1]`. Scaling each historical segment by the cumulative product of `k` (most-recent segment anchored to actual prices) preserves within-segment percentage returns exactly and is sign-identical to `backadj` on every day including rolls. Note it is **not** strictly positive, as this section once claimed: ratio adjustment scales by a positive factor, so it preserves the underlying's sign — CL's `propadj` close on 2020-04-20 is −24.11 because WTI really settled at −37.63. That is one bar in the whole store, against `backadj`'s 46.7% for DC. Recommendation: **read DC (and any similarly low-priced contract) with `marketdata.get_bars(sym, "propadj")`.**

### Providers & authentication

- **Databento (paid, cross-platform).** A two-stage producer for a server that cannot run Norgate. Stage 1 (`cotdata-update --ingest-databento`) pulls raw `.n.0` / `.n.1` `ohlcv-1d` and `statistics` into an append-only raw store (`$COTDATA_DATABENTO_RAW`, else `_raw/databento` under the store). This is the only paid step, and it is resumable, so re-runs fetch only new dates. Stage 2 (`cotdata-update --build-databento`) derives the back-adjusted prices from the raw store with no API cost, so the build logic can be iterated offline. Set `DATABENTO_API_KEY`. History starts 2010-06-06, and markets not on CME Globex (ICE softs, lumber, MSCI intl) are not covered. The raw store is producer-internal, so exclude it from any consumer sync.
- **Norgate Data / Yahoo Finance** — moved to [crucible-marketdata](https://pypi.org/project/crucible-marketdata/) with the rest of the bar production (ADR-0007), along with the `[norgate]` and `[yahoo]` extras and the `COTDATA_PRICE_SOURCE` resolution that chose between vendors. That per-symbol resolution now lives in marketdata's registry.

### The symbol registry

The supported futures contracts are defined in a YAML registry, so adding a market needs no code:

- **Add a market:** edit `src/cotdata/registry.yaml` under its asset class. The registry handles metadata like `is_equity` and predecessor `hist_codes`.
- **Centralize it:** set `COTDATA_REGISTRY` to a shared `registry.yaml` (e.g. inside `$COTDATA_STORE`) so producer and consumers use identical asset definitions without a `git pull`.

### Atomic store

The store uses **atomic writes** (write-temp-then-rename). Consumers can safely query via `get_cot` even while `cotdata-update` is actively downloading and writing.

### COT vintage tracking (as-published history)

CFTC revises COT data after publication — most consequentially through **trader
reclassification**, which moves positions between categories retroactively. Because
downstream signals are rolling z-scores and percentiles against years of history, a
restatement silently rewrites the baseline every historical reading was computed against.
There is precedent: in July 2008 the Commission revised reports back to July 3, 2007.

**CFTC serves current state only.** There is no vintage archive and no as-published
endpoint, so vintage data can only be accumulated going forward — every uncaptured week is
a permanent blind spot in the part of the series most likely to have been revised.

This is **opt-in and purely additive**: if you never run it, the store behaves exactly as
before. Enabling it adds a `vintage/` subtree.

```bash
cotdata-vintage fetch                      # capture current + prior year + weekly static (daily)
cotdata-vintage fetch --all                # every year 1986-present (see below)
cotdata-vintage ingest --pending           # parse retained raw -> observations + revisions
cotdata-vintage diff --since 2026-01-01    # field-level revisions, with revision depth
cotdata-vintage asof --as-of 2026-07-24T18:00:00 --report-date 2026-07-21
cotdata-vintage zero-sum --market 088691   # long/short/open-interest identity per week
cotdata-vintage coverage                   # which markets a report covers, per year
cotdata-schedule sync                      # CFTC Special Announcements
cotdata-schedule published                 # true publication dates from retained weekly statics
cotdata-schedule backfill                  # resolve release_date + its provenance
```

How it works:

- **Immutable landing zone.** Every fetch is recorded (including 304s) and raw bytes are
  retained permanently under `vintage/raw/`, written atomically and never rewritten. A
  byte-identical regeneration is deduped — a changed download is not itself a revision.
- **All four reports.** Legacy, Disaggregated, TFF and Supplemental canonicalise into one
  long schema. Disagg and TFF also populate per-category spreading, per-category trader
  counts and CR4/CR8 concentration, none of which the Legacy file carries, and they are
  where **Managed Money** and **Leveraged Funds** live. A suppressed trader count (CFTC
  writes `.`) canonicalises to null rather than to a string. Supplemental is where **Index
  Traders** live, and it is the one report where `combined` is `True`: the category
  vocabulary is checked per `report_type`, so its `commercial` (which is net of index
  traders) can never be mistaken for Legacy's.
- **Coverage is not constant, and `cotdata-vintage coverage` says so.** A report's market
  set is derived from the stored observations and every entry or exit is printed. The live
  case is Supplemental, which covered 12 markets until Soybean Meal entered in 2013.
- **Change-only observations.** A row is written only when its value hash differs from the
  latest for its natural key `(report_date, market_code, report_type, combined, category)`,
  so storage grows with actual revisions rather than with time.
- **Field-level revisions** carry `age_days` (revision depth): whether revisions stay in
  recent weeks or reach back into the calibration window determines how much the rest of
  a system has to care.
- **Point-in-time reads.** `asof(t)` returns each key's latest value observed at or before
  `t`, reconstructing what was actually knowable then.
- **Release dates with provenance.** `report_date` is stored exactly as reported (never
  normalized to Tuesday), and `release_date` is resolved through
  `published > observed > announced > scheduled > derived`, with the source recorded — a
  release date without provenance is worse than none, since indexing on `report_date`
  embeds a lookahead (three days normally, weeks during a backlog). `published` is the
  weekly static's HTTP `Last-Modified`, a true publication timestamp; it is forward-only
  (that file holds one week and is overwritten), so weeks predating capture fall back
  down the chain. `announced` comes from the republication tables CFTC posts on the
  Special Announcements page after a disruption, and is what puts the Oct–Dec 2025
  appropriations-lapse backlog on its real dates: 36,296 stored rows that `derived`
  otherwise places up to 47 days early, before the lapse that stopped them being
  published at all.

- **Flow decomposition.** `cotdata-vintage flow` labels each week's ΔLong versus ΔShort as
  `new_longs` / `short_covering` / `new_shorts` / `long_liquidation`, by dominant leg, with
  no parameters to tune. A rally driven by short covering has a finite fuel supply and one
  driven by fresh longs does not, and they are indistinguishable on a price chart. It also
  emits `days_elapsed`, because COT was **fortnightly until 1992-10-13** and holidays shift
  the rest, so a "weekly" change is not always weekly.

**Run capture on the producer, not a replica**, and schedule it **daily**: nearly every
request returns 304, so a daily run is close to free while catching holiday-shifted and
backlog releases with no schedule logic.

**The frozen-year tripwire.** CFTC regenerates a rolling two-year window (current plus the
immediately-prior year) and nothing older. The prior year is therefore re-served every week
but byte-identical, which is the one place a **content** check on closed data comes free.
It is in the default fetch set for that reason: it costs one roughly 7 MB transfer per week
and zero bytes on disk, and it is the only automated retroactive-restatement detector here.
Anything other than `unchanged bytes (deduped)` on it raises an alert, which `ingest`
re-raises as a non-zero exit and a `REVISIONS_<date>.txt` marker file. `--no-prior-year`
turns it off. `--all` extends the same check to every year, but since nothing older is ever
re-served it mostly confirms 304s; monthly or quarterly is the right cadence for that. Full
design notes, including the measured CFTC caching behaviour, are in
[docs/design/cot_vintage.md](docs/design/cot_vintage.md).

> **Replica warning.** The vintage tree must not be written on a machine whose store is
> mirrored (`robocopy /MIR`, `rsync --delete`) from a producer: the mirror deletes
> destination-only files and the data is irreplaceable. Capture on the producer, or set
> `COTDATA_VINTAGE_ROOT` to a path outside the mirrored store. See [docs/SYNCING.md](docs/SYNCING.md).

## Local development

```bash
uv venv                                     # create .venv
uv pip install -e .                         # install cotdata + deps
export COTDATA_STORE=/path/to/synced/store  # the shared store
uv run pytest                               # run the tests
```

For the databento producer, add the extra with `uv pip install -e ".[databento]"`. Use `uv run <cmd>`, or activate with `source .venv/bin/activate` (Mac/Linux) / `.venv\Scripts\activate` (Windows).

## Reference: Data schemas

The canonical store uses standard Parquet files. Loaded with `pd.read_parquet()`, they conform to the following schemas.

### Price Data (`prices/{symbol}_{adjustment}.parquet`)

> [!NOTE]
> **Databento output only.** Norgate/Yahoo bars and the general bar schema (including the
> reconstructed-volume columns and how to read them) moved to
> [crucible-marketdata](https://pypi.org/project/crucible-marketdata/) under ADR-0007.
> What is documented here is what `--build-databento` writes into this store, read back
> with `cotdata.store.read_prices(symbol, adjustment)`.

Indexed by tz-naive `Date`. The build writes both the back-adjusted (`backadj`) series for signals/stops and the unadjusted (`unadj`) series for true transaction-cost modeling.

**Schema versioning:** `schema_version` in the manifest records the on-disk data version. Consumers key cache invalidation on `cotdata.schema_version()` and can guard with `cotdata.require_schema(min_version)`.

| Column | Type | Description |
|--------|------|-------------|
| `Date` | DatetimeIndex | Trading day (tz-naive, normalized to midnight). |
| `Open` | float | Opening price. |
| `High` | float | High price. |
| `Low` | float | Low price. |
| `Close` | float | Settlement Close price. |
| `Volume` | float | Continuous contract trading volume (front-month only). |
| `Open Interest` | float | Continuous contract open interest, from the `statistics` schema (stat_type 9). |
| `Delivery Month` | float | Expiration month of the active contract (e.g. `202609`). Used to detect contract rolls. |

### Contract Specifications

Moved to `marketdata`'s store (`metadata/contract_specs.parquet` under
`$MARKETDATA_STORE`) with the Norgate producer that writes them — read them with
`marketdata.read_metadata()`. Nothing in this package writes contract specs any more.

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

### COT Supplemental Data (`cot_supplemental/{symbol}_{code}.parquet`)
Index-trader positioning for 13 select agricultural markets (CFTC Supplemental / Commodity Index Trader Report). **History starts in January 2006**, and the covered set was **12 markets until Soybean Meal entered in 2013**. Indexed by tz-naive `Report_Date_as_MM_DD_YYYY` (renamed at parse from CFTC's `As_of_Date_In_Form_*`, whose own name changed in 2013).

> [!WARNING]
> **Combined basis, and the file does not say so.** This is the only report with no futures-only variant, and unlike Disaggregated and TFF it carries no `FutOnly_or_Combined` column. Verified by matching open interest against both Legacy series: 390/390 against futures-and-options combined, 0/390 against futures-only. Its open interest is therefore **not** comparable with the other three for the same market and week.

> [!NOTE]
> **Positions are net of index traders.** `Comm_Positions_Long_All_NoCIT` is commercial *minus* the index book, not Legacy's commercial. The index book is carved out of commercial, non-commercial **and non-commercial spreading** — three buckets, where CFTC's own prose names two. Non-Reportable is untouched, because index traders are reportable by definition.

> [!NOTE]
> **Lossless Image**: Like Disaggregated and TFF, these parquets are a lossless image of the source CFTC `txt` files, including CFTC's own header typos (`NComm_Postions_Spread_All_NoCIT`).

## Reference: COT formats explained

The CFTC publishes positioning data in four formats; `cotdata` manages all four for complete coverage and the deepest history.

1. **Legacy (1986–Present)** — *all markets.* Divides traders into **Commercial** (hedgers) and **Non-Commercial** (large speculators). The only format with pre-2006 data, so it's essential for long-term backtesting.
2. **Disaggregated / DIS (2006–Present)** — *physical commodities only* (Agriculture, Energy, Metals). Splits traders into **Producer/Merchant**, **Swap Dealers**, **Managed Money**, and **Other Reportables** — a clearer view of "smart money" (Managed Money) in commodities.
3. **Traders in Financial Futures / TFF (2006–Present)** — *financial markets only* (Equities, Rates, Currencies). Splits traders into **Dealer/Intermediary**, **Asset Manager**, **Leveraged Funds**, and **Other Reportables** — the definitive source for speculative flow (Leveraged Funds) in financials.
4. **Supplemental / CIT (2006–Present)** — *13 select agricultural markets only.* Takes the Legacy split and carves **Index Traders** out of it, the only public source that separates index flow. Futures-and-options **combined** only. The taxonomy is Legacy, not Disaggregated, so Index Traders does **not** nest inside Disaggregated's Swap Dealer and the two cannot be differenced to isolate levered swap flow.

## Diagnostics

```bash
cotdata-update --check       # coverage, newest dates, staleness — no network
python scripts/vintage_alert_selftest.py     # the vintage capture's alerting path
```

Norgate subscription and roll-gap diagnostics moved to `marketdata` with the provider.

## Ecosystem

cotdata is the *data* layer of a small, unbundled toolchain — it stops at "clean
data behind a stable API" on purpose. What you do with that data is a separate,
swappable step:

- **cotdata** *(this package)* — CFTC COT positioning. One synced store, many
  readers, no vendor SDK at read time.
- **[crucible-marketdata](https://pypi.org/project/crucible-marketdata/)** — the
  daily bars that used to live here: Norgate futures, Yahoo equities/ETFs, the
  adjustment tiers, contract specs. Split out by ADR-0007 so a positioning question
  and a price question have separate stores, producers and schedules.
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
- Platform-specific notes (CFTC parsing and the databento build run anywhere)
- Code style guidelines

## Contributing

Issues and pull requests are welcome. Please see [CONTRIBUTING.md](CONTRIBUTING.md) for setup, tests, and conventions. When filing a bug, include your OS. If it is about price bars, it probably belongs on [crucible-marketdata](https://github.com/mspinola/marketdata) instead.

## License

Released under the MIT License — see [LICENSE](LICENSE).
