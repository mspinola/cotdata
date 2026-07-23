# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **`propadj` price adjustment** — a proportional (ratio) back-adjusted view
  derived on read from the stored `unadj` + `backadj` series via
  `get_prices(symbol, adjustment="propadj")`. It preserves daily percentage
  returns and stays strictly positive, unlike Norgate's additive `backadj`.
  Motivated by **DC (Class III Milk)**: additive back-adjustment drove 46.7% of
  `DC_backadj` closes ≤ 0 (range −9.83 to 23.09), making price-based stops and
  R-multiples unusable; `propadj` yields a strictly-positive DC series
  (4.68–25.01) over the full 1997–2026 history. Recommended for low-priced,
  long-history contracts. Derived from already-stored series — no producer
  re-run or schema change required.
  ([#23](https://github.com/mspinola/cotdata/pull/23), [#26](https://github.com/mspinola/cotdata/pull/26))
- **Yahoo Finance price provider** (`cotdata-update --prices-yahoo`) — a
  cross-platform, research-grade price source for registry symbols carrying a
  `yahoo` ticker, so markets Norgate/databento don't cover can still be priced
  off ETF proxies. Adds the MSCI EM (MME→EEM) and EAFE (MFS→EFA) held-out
  generalization markets. ([#24](https://github.com/mspinola/cotdata/pull/24))

### Fixed
- **`databento.fetch_daily_ohlc`'s `start_date` parameter now actually does
  something** (dormant provider). It was previously silently ignored on every
  code path — a cold cache always backfilled from 2000-01-01 and a warm cache
  always resumed from `last_date + 1 day` regardless of what was passed — so a
  caller trying to bound a fetch (e.g. "just the last 3 months") got a
  full-history pull instead, at full Databento API cost, with no error or
  warning. Now: on a **cold** cache (a symbol's first-ever fetch) `start_date`
  narrows the fetch floor, so a narrow first-time query is actually cheap. On
  a **warm** cache it does *not* narrow the incremental fetch — the top-up
  always resumes from the cache's own `last_date + 1`, so a later, narrower
  `start_date` can never silently truncate a cache other callers already rely
  on being complete — but the *returned* frame is still filtered to
  `>= start_date` either way. The on-disk cache always persists the full
  series regardless of `start_date`; only the fetch cost (cold cache) and the
  return value (always) are affected.
- **Fail fast when the Norgate service (NDU) is unreachable** — the producer now
  probes `norgatedata.status()` before fetching and aborts with a clear error and
  a non-zero exit. Previously norgatedata retried each call 10x then called bare
  `sys.exit()`, which exits **0** (a scheduled run looked "successful" while
  writing nothing and never retried) and raised `SystemExit` past the per-symbol
  handler, killing the run on the first symbol.
- **Never persist all-null contract-spec rows** — if Norgate returns nothing for
  every spec field of a covered symbol (a transient failure), `--metadata` now
  skips it with a warning instead of writing a null row or, on a scoped upsert,
  overwriting good existing specs with nulls.
- **Skip Yahoo-only markets in the Norgate producer** — MME/MFS have no Norgate
  continuous series, so `--prices`/`--metadata` were erroring on `&MME_CCB` /
  `&MFS_CCB` and silently writing all-null contract-spec rows. They are now
  marked `norgate: null` in the registry and skipped by the Norgate producer.
  ([#27](https://github.com/mspinola/cotdata/pull/27))
- **Scoped metadata refresh no longer drops other markets** — a
  `--metadata --symbols …` run now UPSERTs by `Symbol` into `contract_specs`
  instead of replacing the whole table, so specs for markets outside the request
  survive. ([#25](https://github.com/mspinola/cotdata/pull/25))
