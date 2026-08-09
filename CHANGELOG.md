# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Removed — BREAKING

- **Price bars, and the Norgate and Yahoo producers, are gone from this package**
  (crucible-stack ADR-0007 step 2 §7.5). cotdata is now CFTC positioning only. Every bar,
  tier and contract spec lives in
  [`crucible-marketdata`](https://pypi.org/project/crucible-marketdata/), which keeps its
  own store (`MARKETDATA_STORE`), its own producer and its own schedule.

  | gone from cotdata | use instead |
  |---|---|
  | `cotdata.get_prices(sym, adjustment, start=, volume=)` | `marketdata.get_bars(sym, tier, start=, volume=)` (`start`/`volume` are keyword-only there) |
  | `cotdata.roll_dates(sym)` | no replacement — see below |
  | `cotdata.store.read_metadata()` | `marketdata.read_metadata()` |
  | `cotdata-update --prices` / `--metadata` / `--require-final` | `marketdata-update --bars --domain futures --require-final`, `--metadata` |
  | `cotdata-update --prices-yahoo` | `marketdata-update --bars --domain equities` |
  | `cotdata[norgate]`, `cotdata[yahoo]` extras | `crucible-marketdata[norgate]`, `crucible-marketdata[yahoo]` |
  | `COTDATA_PRICE_SOURCE` | marketdata's registry resolves the vendor per symbol |

  Also removed: `cotdata/prices.py` (including the derived `propadj` tier),
  `providers/norgate.py`, `providers/yfinance.py`, `store.{write,upsert,read}_metadata`,
  and the `packaging` runtime dependency that existed only for `norgatedata`.

  **Deleted rather than deprecated.** A shim left importable would read a store the nightly
  job no longer fills, and stale data is much harder to notice than an `AttributeError` —
  the failure is a number that looks right and is months old.

  `roll_dates` is dropped outright rather than ported: a sweep of the four consumer repos
  found no caller. npf has a `roll_dates` of its own in `books/treasury_seasonal.py`, which
  is a different function (a threshold on the back-adjustment offset, not a
  `Delivery Month` change) and is unaffected. The `Delivery Month` column it read is still
  in marketdata's frames, so the two-line derivation is available to anyone who wants it.

  **What stayed, deliberately:** the **databento** provider and the store-level
  `write_prices` / `read_prices` it writes through. It has no marketdata equivalent yet, and
  deleting it would destroy a validated provider-different alternative (ADR-0006) plus the
  only intraday-capable source in the fleet. `cotdata-prices` therefore still exists,
  scoped to `--ingest-databento` / `--build-databento`. There is no consumer *bar API* here
  any more — read that output with `cotdata.store.read_prices(symbol, adjustment)`.

  The `metadata` manifest domain is still declared but has no writer, so pre-0.4.0 stores
  can still migrate and reconcile their existing entries rather than stranding them in the
  legacy aggregate.

  **Known breakage, recorded rather than fixed:** `crowdmon` (frozen, archived) and
  `npf/docs/crowdmon/reproduce_forced_flow_mechanism.py` still call `cotdata.get_prices`.
  Both are point-in-time records under their repos' doc lifecycle and were left untouched
  on purpose.

### Added
- **The CFTC Supplemental (Commodity Index Trader) report, as a fourth `report_type`.**
  13 select agricultural markets with the index-trader book split out of the commercial and
  non-commercial buckets. New producer action `cotdata-cot --cot-supplemental` (also in
  `--cot-all`) writing `cot_supplemental/{symbol}_{code}.parquet`, new
  `cotdata.get_cot(sym, report="supplemental")`, and `supplemental` added to the vintage
  capture set from 2006 onward with `canonicalize_supplemental` behind it. Scope recorded
  in [ADR-0002](docs/adr/ADR-0002-supplemental-report-is-in-scope.md); it is CFTC
  positioning, so it sits inside ADR-0007's narrowed boundary on the same argument that
  admitted vintage provenance.

  **Purely additive.** Nothing was added to the natural key, `VALUE_FIELDS` or
  `ALL_COLUMNS`, so no stored `row_sha256` moves and no existing observation is touched.
  A store that never runs the new action is unchanged.

  Four things came back different from what the brief and the CFTC prose said, all
  measured against the real 2006-2026 archives
  ([docs/analysis/2026-08-03-cit-supplemental-measurements.md](docs/analysis/2026-08-03-cit-supplemental-measurements.md)):

  - **Coverage is 12 markets, then 13 from 2013**, when Soybean Meal entered. Both counts
    circulate because both are right for part of the history. That is the only entry and
    there are no exits, and **six** markets were renamed without changing code (four NYBOT
    to ICE in 2007, the two wheats relabelled in 2013, including 001612 changing exchange
    from KCBT to CBOT and keeping its code), which is why coverage keys on `market_code`
    and reports the name at the latest report date rather than the lexicographic max.
  - **The report is futures-and-options combined and the file cannot say so** — it carries
    no `FutOnly_or_Combined` column, so the existing `_combined_flag` would have defaulted
    it to `False` and put a guessed value in the natural key. Established by matching open
    interest against both Legacy series: **390/390** against combined, **0/390** against
    futures-only. `canonicalize_supplemental` asserts it and takes no override.
  - **Index traders are carved out of three buckets, not the two CFTC describes**: the
    third is non-commercial *spreading*, ~0% of the index book in 2006 and ~9% now.
  - **The fetch pattern differs from the other three reports.** No 2006-2016 history bundle
    (404), and every year from 2006 returns 200 where disagg/TFF 404 before 2010, so
    `annual_sources` needed its own floor.

  **The open-interest identity is exact on only ~55% of market-weeks, and that is
  rounding.** The residual never exceeds 2 contracts against a tolerance of 4, and the rate
  is flat across 21 years (51.6% to 59.5% by year, sd 2.1pp; the *breach* rate, either side
  off, is 67.7% and ranges 62.6% to 71.7%). Control: on the same weeks,
  Legacy *futures-only* is exact on 99.7% of rows while Legacy *combined* shows the
  identical +/-1 pattern on 10%. Combined reports publish delta-weighted option equivalents
  rounded to whole contracts, independently per category, which is the same n-addends
  mechanism `rounding_tolerance` was already derived from. `validate()` raises zero
  warnings across the full history.

  **The category vocabulary check must stay per-`report_type`.** Supplemental reuses
  `commercial` and `noncommercial`, and they do not mean what they mean under Legacy: they
  are net of index traders, and this report is combined where Legacy here is futures-only.
  Reusing the labels rather than minting `non_commercial`-style spellings is deliberate — a
  new spelling would make `category == "nonreportable"` silently miss every Supplemental
  row while leaving `commercial`, the genuinely confusable label, identical anyway.
  `report_type` and `combined` are both in the natural key, so the series cannot merge.

  **Index Traders does NOT nest inside Disaggregated's Swap Dealer.** The taxonomy is
  Legacy, not Disaggregated, so the two cannot be differenced to isolate levered swap flow.
  Anything relating them is an inference across differently-partitioned reports.
- **`cotdata-vintage coverage`** — emits which markets a report actually covered per year,
  derived from the stored observations rather than from a list in source, and prints every
  entry and exit. A covered set treated as constant is how a consumer reports on 12 markets
  believing it has 13. It compares **consecutive years only** and emits a `gap` record
  otherwise: coverage is derived from what was INGESTED, so a store holding 2006 and 2026
  and nothing between would otherwise report Soybean Meal as entering in 2026, presenting
  an ingest artifact as a fact about the market.

### Changed
- `vintage_ingest._resolve` now tolerates CFTC's `Postions`/`Positions`, `Spead`/`Spread`
  and `NComm`/`NonComm` header variants alongside the single/double-underscore one it
  already handled, and **composes up to three of them**. All are real spellings in shipped
  CFTC files, three of them typos. Composition is what makes the tolerance useful on the
  column that needs it most: `NComm_Postions_Spread_All_NoCIT` carries two defects at once,
  so the realistic upstream cleanup — fix the typo and normalise the prefix in one pass —
  is exactly the case a single-substitution search cannot reach.

  This widens a lookup that previously RAISED on anything it did not recognise, so the
  regression to worry about is a variant silently landing on a different field's column.
  It cannot: across every column name the four canonicalisers ask for, no candidate
  spelling of one field is a candidate of another, and no target matches two real columns
  in the shipped 2026 Legacy, Disaggregated, TFF or Supplemental headers. Both properties
  are asserted, not argued, by
  `tests/test_cit_supplemental.py::test_header_variants_cannot_resolve_to_another_field`.
  A genuine rename still raises.

## [0.3.0] - 2026-08-02

**The first release actually published to PyPI since 0.1.0.** 0.2.0 was tagged earlier
today and never uploaded: the repo had no release workflow at the time, and the only
alternative was a hand-rolled `twine` upload with a stored token. That tag stands as a git
tag and nothing more. Everything in it ships here, described in the 0.2.0 section below.

A PyPI consumer moving from 0.1.0 therefore gets the whole vintage subsystem plus this
release's removal in one step, and `docs/WINDOWS_SETUP.md`'s "PyPI is well behind this
repo, install editable from a clone" note stops being true once this lands.

### Removed
- **`vintage_flow.decompose` and the `cotdata-vintage flow` subcommand.** Flow
  decomposition was duplicated in `crowdmon.futures.flow`, and measurement showed the two
  were not alternatives but **one function**: this copy was that one at `tolerance=1.0`
  with the gap rule off, agreeing on **100.000000% of 135,835 transitions (2006-2026, 27
  markets) with zero mismatches**, and `d_long`/`d_short`/`d_net` identical on every row.
  The copy here was strictly the less capable one: it could not decline to label a
  genuinely two-sided week (no `mixed` state) and it differenced across a 294-day absence
  as though it were a week. The general implementation stays in `crowdmon`, which is where
  the module spec puts the positioning engine. The dedup could not go the other way,
  because `crowdmon/tests/test_boundaries.py` forbids cotdata from importing its own
  consumer. Measurement in `crowdmon/docs/design/amendments-2026-08-02.md` §B29, asserted
  by `crowdmon/tests/test_flow_equivalence.py`.

  **No deprecation path, and the reason is checkable rather than asserted.**
  `decompose` was never in `cotdata.__all__`, has never appeared in this changelog under
  any version, and is in no **published** release: PyPI carries 0.1.0, which predates the
  entire vintage subsystem, and the 0.2.0 note below states in terms that tagging is not
  publishing. Its only caller in the workspace was the CLI subcommand removed with it.

  **Not removed:** `zero_sum_check`, `from_vintage` and `from_current_store` stay. The
  zero-sum identity is a statement about cotdata's own parse and is consumed by
  `crowdmon.futures.cot_adapter` on every load. `min_frac_oi`, the optional dead zone, has
  no equivalent in the surviving implementation and is gone; it defaulted to off and
  nothing set it.
- **`cotdata-vintage flow` is replaced by `cotdata-vintage zero-sum`**, which keeps the
  half of that command that was cotdata's own: source selection plus the identity check,
  now also reporting the rounding-tolerance column and any non-weekly intervals. A command
  named `flow` that no longer decomposes flow would be a worse outcome than a renamed one.

## [0.2.0] - 2026-08-02

The vintage release. `cotdata` gains an as-published provenance layer beside the
current-state store: what CFTC served, when, and what it previously said. Decision
recorded in crucible-stack ADR-0008; design in `docs/design/cot_vintage.md`.

Current-state output is byte-identical, guarded by `tests/test_current_baseline.py`, and
the subsystem is opt-in behind its own entry points, so a store that never runs capture is
indistinguishable from 0.1.0. Consumers pin `cotdata>=0.1.0` and need no change.

**0.1.0 on PyPI predates the producer CLI** (`--metadata`, `--require-final`, the
databento flags) and the whole vintage subsystem, which is why `docs/WINDOWS_SETUP.md`
tells operators to install editable from a clone instead. That guidance stands until this
version is actually published; tagging it is not publishing it.

### Added
- **COT vintage capture** (`cotdata-vintage fetch`) — an immutable, hashed landing
  zone for as-published CFTC files under `$COTDATA_STORE/vintage/raw/`, with
  provenance (etag, last-modified, sha256, size, retrieved-at) recorded in a
  self-owned `vintage/manifest.json`. Purely additive: the current-state store is
  byte-identical (guarded by `tests/test_current_baseline.py`). This is step 1 of
  the vintage/revision-tracking subsystem — capture must start now because an
  uncaptured weekly release is irrecoverable; ingest/diff/PIT land next and run
  retroactively over retained raw bytes. Decision recorded in crucible-stack
  ADR-0008; design in `docs/design/cot_vintage.md`.
- **COT vintage ingest + revision tracking** (`cotdata-vintage ingest|diff|asof`,
  `cotdata-schedule sync|backfill`) — parses retained raw snapshots into a change-only
  bitemporal `observations/` table (a row is written only when its value hash differs
  from the latest for its natural key, so storage grows with revisions not with time),
  emits field-level `revisions/` with `age_days` revision depth, and answers
  point-in-time `asof(t)` reads (greatest `observed_at <= t` per key). Release dates are
  resolved with explicit provenance (`observed > announced > scheduled > derived`), with
  the `announced` tier itself landing separately — see the next entry; this change built
  the precedence machinery and left that tier without a producer. All pandas/pyarrow, no
  database.
- **`announced` release dates** (`cotdata-schedule sync`) — parses the republished
  `COT Report Date / Original Publish Date / New Publish Date` tables off the CFTC Special
  Announcements page and merges them into `release_schedule.parquet` as
  `source="announced"`, which outranks `scheduled`. Closes acceptance criterion 5, recorded
  until now as an unreachable tier on the grounds that extracting a release date from
  free-text prose would be guessing, and that a guessed date is worse than an honest
  `derived` one because it carries a flag claiming it was announced. The reasoning held;
  the premise did not. CFTC publishes these as an exact table, so the extractor reads
  **tables** and still refuses **prose**: of ~100 announcements back to 2008, the prose
  ones (holiday shifts, reporting-firm corrections) yield no exact pair, and their weeks
  stay on `scheduled` or `derived`. Measured on the live store: **36,296 observation rows
  move from `derived` to `announced`**, with the `scheduled` count unchanged so nothing
  correct is displaced, covering the whole Oct–Dec 2025 appropriations-lapse backlog. The
  worst week, report date 2025-09-30, sat at a `derived` 2025-10-03 and actually published
  2025-11-19: wrong by 47 days, in the direction that claims data existed before the lapse
  that stopped it existing. Only the newest table is taken, because a table is a whole
  replacement plan rather than a set of per-week corrections; merging row-wise would have
  let a superseded plan overwrite three dates that CFTC's own published calendar had right.
  Design note in `docs/design/cot_vintage.md` §10.
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

### Changed
- **`--require-final` price gate is now data-driven, not a wall-clock cutoff.**
  `finals_ready` previously deferred until Norgate's `Futures` and `Continuous
  Futures` databases were both refreshed at/after a fixed local time
  (`--final-cutoff`, default 20:55). That is fragile by construction: the cutoff
  must sit below the earliest evening final yet above any daytime interim, and
  Norgate's publish time drifts. On 2026-07-27 Norgate finalized the Futures DB at
  8:49pm, so the `>= 20:55` check never turned true and prices went stale for the
  day. The gate now asks the robust question — does Norgate hold a **newer settled
  continuous bar** than the store already has? — across a liquid reference quorum
  (ES, CL, ZC). It is immune to publish-time drift (early publish → ready early,
  late publish → a retry catches it) and needs no trading calendar (weekends and
  holidays simply produce no new bar). `--final-cutoff` is accepted but ignored
  (deprecated) so existing schedulers do not break. See
  `docs/design/finals_ready_data_driven.md`.

### Fixed
- **The announcements corpus was site navigation, not announcements.** `sync()` scraped
  every `<li>` in the whole document, so all 95 rows it had stored by 2026-08-02 were menu
  entries, footer links and market-name list items ("Contact Us", "Privacy Policy", "CBT
  Corn (CFTC ID 002602)"), with `announcement_date` null on every one — the store reported
  95 announcements and held none. Now scoped to the page's content region and keyed on the
  `Month D, YYYY:` headings, which is both what the announcements are and what carries the
  date. The 95 legacy rows remain in existing stores, since the append-and-dedupe write
  never drops a row, and stay distinguishable by their null `announcement_date`.
- **The frozen-year "detector went blind" alert no longer fires on the ordinary
  weekly gap.** CFTC regenerates the prior year **weekly**; the capture task runs
  **daily**, so six of every seven runs legitimately return 304. The trigger asked
  "did the last run see bytes?", which on that schedule is a day-of-week test, not
  a blindness test: it fired on all three prior-year sources every Saturday, the
  morning after Friday's regeneration, reporting that the restatement detector had
  gone blind while it was working correctly. Found in the first week of production
  capture (2026-08-01); a live `HEAD` the next day confirmed the files unchanged
  and byte-identical to the retained copies. Blindness is now measured as elapsed
  time since bytes last arrived, alerting once per quiet period past
  `BLIND_AFTER_DAYS` (9, one weekly cycle plus slack). Replaying all 18 production
  snapshots gives 3 alerts before and 0 after. Also fixes the measurement it rests
  on: a 304 record carries the previous sha forward, so `_latest_with_content`
  matched it and reported "one day quiet" forever; `_latest_delivery` keys on
  `byte_size`, the only field that means bytes actually arrived. Design note in
  `docs/design/cot_vintage.md` §6b.
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
