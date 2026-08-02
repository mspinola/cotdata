# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
