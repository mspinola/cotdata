# Handoff: COT Vintage Store & Revision Tracking

**Target:** Claude Code session working in `trading_workspace/cotdata`
**Status:** step 1 of the `crowdmon-futures` build (see `crowdmon_futures_cot_module.md` §5.3)
**Version:** v0.2 — amended after repo review
**Estimated scope:** ~400–500 LOC including tests

---

## Changelog v0.1 → v0.2

Amended in response to the implementing agent's review. Three substantive changes:

1. **No database.** v0.1 specified DuckDB/SQLite. That was convenience, not requirement, and it cut against the repo's deliberate Parquet + JSON manifest contract. Persistence is now Parquet throughout (§3).
2. **`release_date` is no longer assumed available.** It now carries an explicit provenance flag, with a resolution order and a cheap spike that may collapse the whole taxonomy (§4.6).
3. **Scope repriced** after git-history recovery returned no historical vintages. Analytics deferred; release-date backfill promoted (§10).

---

## 0. Read this first

You have the repo; the author of this spec does not. Everything below describes intended behaviour, not existing structure. **Do not assume the described modules exist or that the described layout matches what's there.** Adapt the design to what's actually in the repo rather than restructuring the repo to match this document.

**Non-goals.** Do not refactor existing fetch/parse logic beyond what's needed to add snapshotting. Do not change existing public APIs. Do not add analytics, metrics, or plotting. This task adds a provenance layer underneath what already works.

**Compatibility requirement.** The existing current-state Parquet output must remain byte-identical. The vintage layer is purely additive alongside it. Existing consumers see no change.

---

## 1. Problem and motivation

CFTC revises published COT data after the fact. Four mechanisms:

1. Contract-level error corrections (narrow — one market, one date)
2. Late or amended filings from reporting firms (usually recent-dated)
3. Contract universe additions (composition, not values)
4. **Trader reclassification** — the one that matters

Reclassification moves positions *between categories*. Since Managed Money and Leveraged Funds are the sole input to everything downstream, and since all downstream outputs are rolling z-scores and percentiles against three years of history, a retroactive restatement silently rewrites the baseline against which every historical reading was computed.

Precedent for large retroactive restatements: in July 2008 the Commission revised reports for affected markets going back to July 3, 2007 — over a year of history rewritten in one event.

CFTC serves current-state files only. No official vintage archive, no as-published endpoint. **Vintage data can only be accumulated going forward,** which is why this is step 1. Git-history recovery has been attempted and returned nothing, so the vintage series starts from the first capture this session produces.

CFTC does publish a revision log, ingested here as a first-class source:
`https://www.cftc.gov/MarketReports/CommitmentsofTraders/HistoricalSpecialAnnouncements/index.htm`

---

## 2. Discovery — resolved and outstanding

**Resolved:** git-history archaeology found no recoverable historical vintages. Vintage accumulation is forward-only from this session.

**Still to report before writing code:**

1. **Existing storage.** What is persisted today, and are raw downloads retained or discarded after parse?
2. **Existing schema.** Column names, dtypes, index conventions of the parse output. This is the basis for §3.2.
3. **Which sources are fetched.** Report types (legacy / disaggregated / TFF / supplemental), futures-only vs combined, annual zips vs weekly statics vs the Socrata public reporting environment.
4. **Date handling.** Does the code assume the as-of date is always a Tuesday? (It isn't — §6.) Is any release date stored today?
5. **Idempotency.** What happens on re-fetch of an already-ingested week — overwrite, skip, or duplicate?
6. **Manifest shape.** Current `manifest.json` structure, so the vintage block extends it rather than colliding with it.

---

## 3. Persistence — Parquet, no database

Nothing in the bitemporal design requires SQL. The data is small enough that Parquet is adequate indefinitely: change-only writes over roughly 50k rows per year means the entire vintage history stays in single-digit megabytes. DuckDB can query these files directly when ad-hoc SQL is wanted, without adopting a datastore as the storage format.

**Record this as an ADR**, since it sits directly on ADR-0007's boundary. The decision being made: *vintage provenance is in scope for the narrowed `cotdata`, because it is CFTC-positioning provenance, and it persists within the existing Parquet + manifest contract rather than introducing a datastore.*

### 3.1 Layout

```
raw/{source_kind}/{year}/{retrieved_at}_{sha8}.{ext}   immutable; never rewritten
observations/report_year=YYYY/*.parquet                change-only rows
revisions/detected_year=YYYY/*.parquet                 append-only, field-level
release_schedule.parquet
announcements.parquet
manifest.json                                          gains a `vintage` block
current/                                               UNCHANGED — existing consumer contract
```

Raw bytes are retained permanently; the files are small and reprocessing history without re-hitting CFTC is worth the disk.

### 3.2 `observations/` — bitemporal facts

Natural key: `(report_date, market_code, report_type, combined, category)`

| Column | Type | Notes |
|---|---|---|
| `report_date` | date | as-of date **as reported** — do not normalise to Tuesday |
| `release_date` | date | nullable; see §4.6 |
| `release_date_source` | text | `observed` \| `announced` \| `scheduled` \| `derived` \| `unknown` |
| `market_code` | text | CFTC contract market code |
| `market_name` | text | |
| `report_type` | text | `legacy` \| `disaggregated` \| `tff` \| `supplemental` |
| `combined` | bool | futures-only vs futures-and-options |
| `category` | text | controlled vocabulary per report type |
| `long_contracts` | int | |
| `short_contracts` | int | |
| `spread_contracts` | int | nullable |
| `trader_count_long` | int | nullable (suppressed) |
| `trader_count_short` | int | nullable |
| `open_interest` | int | market total |
| `cr4_net_long` … `cr8_net_short` | float | concentration ratios |
| `observed_at` | timestamp | when we saw this value (UTC) |
| `snapshot_id` | text | provenance → raw file |
| `row_sha256` | text | hash of value tuple only, excluding provenance columns |
| `is_tombstone` | bool | column present this session; handling deferred (§10) |

**Change-only writes.** On ingest, compute `row_sha256` and compare against the most recent row for that natural key. Write only if it differs. Storage grows with actual revisions, not with time.

**Point-in-time read.** For each natural key, the row with greatest `observed_at <= t`. No `valid_to` column. In polars this is a filter plus group-by-max — milliseconds at this size.

### 3.3 `revisions/` — derived, field-level, append-only

| Column | Type |
|---|---|
| `revision_id` | text (uuid or content hash) |
| natural key columns | as above |
| `field` | text |
| `old_value` | text |
| `new_value` | text |
| `delta` | float (numeric fields only) |
| `pct_delta` | float |
| `old_snapshot_id` / `new_snapshot_id` | text |
| `detected_at` | timestamp |
| `age_days` | int — `detected_at − report_date`, i.e. **revision depth** |

`age_days` is the field that determines how much the rest of the system needs to care. Confined to recent weeks means PIT discipline matters only for the current observation; reaching into the calibration window means every historical percentile is live.

### 3.4 `raw/` provenance — recorded in `manifest.json`

Per snapshot: `snapshot_id`, `source_url`, `source_kind` (`annual_zip` | `weekly_static` | `socrata`), `report_year`, `retrieved_at`, `http_etag`, `http_last_modified`, `content_sha256`, `byte_size`, `local_path`, `parse_status`, `parse_error`.

**A changed `content_sha256` does not imply changed data** — zips get regenerated. Byte-level change triggers parse-and-diff; it is not itself a revision.

### 3.5 `release_schedule.parquet`

| Column | Type | Notes |
|---|---|---|
| `report_date` | date | |
| `release_date` | date | |
| `source` | text | `announced` \| `scheduled` |
| `note` | text | e.g. holiday shift, backlog catch-up |
| `ingested_at` | timestamp | |

Seeded from the CFTC [release schedule](https://www.cftc.gov/MarketReports/CommitmentsofTraders/ReleaseSchedule/index.htm) and the Special Announcements page.

### 3.6 `announcements.parquet`

`announcement_date`, `raw_text`, `affected_report_types` (nullable), `affected_markets` (nullable), `affected_date_from` / `affected_date_to` (nullable), `url`, `scraped_at`.

Text parsing is best-effort — always store `raw_text`, treat structured extraction as convenience. Purpose is attribution: matching observed diffs to announced causes, and flagging historical windows as known-restated even where pre-revision values are unrecoverable.

---

## 4. Behaviour

### 4.1 Fetch
Conditional GET with `If-None-Match` / `If-Modified-Since` where supported. Always record a snapshot entry, including on 304 (record the check, skip the download). Rate-limit; set a descriptive User-Agent.

### 4.2 Ingest
Parse → canonical schema → validate (§5) → change-only write → emit revisions. Must be idempotent: **re-ingesting an identical file produces zero new observation rows and zero revision rows.** Primary correctness test.

### 4.3 Diff
Computed on parsed values, never on file bytes. Field-level: one changed `short_contracts` produces one revision row, not a whole-row replacement.

### 4.4 Disappearing rows
A natural key present in an earlier snapshot and absent from a later one covering the same period is not an unchanged value. Column is present this session; write handling deferred (§10). Do not silently carry values forward in the meantime — leave the gap visible.

### 4.5 Category migration
Deferred to a later session (§10), but note the intent: when multiple categories for the same `(report_date, market_code, report_type, combined)` change in one ingest, a conserved total with shifted composition is the reclassification signature.

### 4.6 Release-date resolution

The annual zips carry only `report_date`. Do not manufacture a release date — resolve it and record how, in this precedence order:

| `release_date_source` | Mechanism | Coverage |
|---|---|---|
| `observed` | First `observed_at` for that report_date | Forward-only, accurate to polling interval |
| `announced` | Special Announcements — holiday shifts, backlog catch-up schedules | Irregular weeks, historical |
| `scheduled` | CFTC published release schedule | Normal weeks, historical |
| `derived` | `report_date + 3d`, holiday-adjusted | Fallback |
| `unknown` | — | Nothing resolved |

`derived` fails on exactly the weeks that matter, so downstream code must be able to exclude those rows from strict PIT evaluation. That is the entire reason the flag exists — a release date without provenance is worse than none.

**Spike first, before building any of the above.** Fetch a weekly static file for a known past week and check whether the HTTP `Last-Modified` header reflects true publication time. If CFTC serves accurate modification times, true release dates can be backfilled across all history in one pass and most of this taxonomy collapses to a single mechanism. Ten minutes to test. Do not build weekly-static fetching for any other purpose this session.

---

## 5. Validation on every ingest

Fail loudly; never silently drop rows.

- Schema conformance and dtype check
- `long + short + spread <= open_interest` per category (warn, don't fail — definitional edge cases exist)
- Category values within the controlled vocabulary for that `report_type`
- Non-null natural key columns
- Row count within a sane band of the previous snapshot for the same source
- Null-rate per column within a sane band
- **`release_date − report_date` within the normal 3-day gap; flag and log if not** (catches holiday shifts and backlog weeks rather than letting them pass silently)

---

## 6. Known gotchas

- **The as-of date is not always Tuesday.** Federal holidays shift the release by a day, and at least one December report covered the prior Monday's open interest instead of Tuesday's. Store the reported date as-is; any code deriving report date by rounding to Tuesday will silently misalign those weeks.
- **The Oct–Dec 2025 backlog.** COT processing and publication were interrupted from 1 October to 12 November 2025 by the lapse in federal appropriations; CFTC then republished in chronological order, clearing the backlog by 29 December 2025. For those report dates, `release_date` trails `report_date` by weeks — in some cases over a month. This is the single largest PIT hole in the existing history, and backfilling true release dates for these weeks is the highest-value historical fix available. The 2023 ION cyber incident produced a smaller version of the same thing.
- **Index on release date, not report date,** for anything used in historical evaluation. Report date embeds a lookahead — normally three days, but weeks during backlog periods.
- **Futures-only and futures-and-options-combined are different series.** Never mix within one time series; keep `combined` in the natural key.
- **File regeneration ≠ data revision.** See §3.4.
- **Classifications are not auditable.** CEA Section 8 confidentiality means CFTC does not publish how individual traders are classified; reclassification can only be inferred from aggregate footprints.
- **Program under review.** CFTC opened a request for public comment on the COT Reports program in May 2026 covering potential procedural modifications. Keep ingestion loosely coupled from schema so a format change stays contained.

---

## 7. CLI

```
cotdata vintage fetch      [--year YYYY | --all] [--source annual|socrata]
cotdata vintage ingest     [--snapshot ID | --pending]
cotdata vintage diff       [--since DATE] [--market CODE] [--report-type T]
cotdata vintage asof       --as-of TIMESTAMP --report-date DATE [--market CODE]
cotdata schedule sync                       # release schedule + announcements
cotdata schedule backfill                   # resolve release_date across existing history
```

`vintage stats` is deferred (§10).

---

## 8. Tests (required)

| Test | Assertion |
|---|---|
| Idempotent ingest | Same file twice → 0 new observations, 0 revisions |
| Byte-change, data-same | Regenerated archive, identical values → 0 revisions |
| Single-field revision | Fixture with one changed field → exactly 1 revision row, correct old/new |
| PIT query | `asof(t)` before a revision returns the pre-revision value |
| Revision depth | `age_days` correct across a synthetic year-old restatement |
| Holiday week | Monday as-of fixture parses without Tuesday normalisation |
| Backlog week | Report date in the Oct–Nov 2025 window resolves to its true announced release date, flagged `announced` |
| Release-date precedence | `observed` beats `announced` beats `scheduled` beats `derived` |
| Validation failure | Malformed fixture raises rather than partially ingesting |
| Contract preservation | `current/` output byte-identical to pre-change baseline |

Build fixtures from real CFTC files trimmed to a handful of markets, plus hand-edited copies for the revision cases. Commit fixtures so tests run offline.

---

## 9. Acceptance criteria

1. Every fetch recorded in the manifest with raw bytes retained.
2. Re-ingesting unchanged data is a no-op at the observation level.
3. Any value change produces a field-level revision row with correct provenance on both sides.
4. `vintage asof` reconstructs the dataset as known at an arbitrary past timestamp.
5. Release schedule and announcements ingested; `release_date` + `release_date_source` backfilled across existing history, including the Oct–Dec 2025 backlog weeks.
6. No database introduced; all persistence in Parquet + manifest.
7. `current/` output byte-identical; existing repo tests still pass.
8. All §8 tests pass offline against committed fixtures.
9. ADR recorded for the scope and persistence decision (§3).

---

## 10. Scope for this session

Git recovery returned nothing, so the diff machinery has no input until the next release. Analytics are therefore deferred and the release-date backfill — the only piece with immediate value against existing history — is promoted.

**In scope:**
1. Raw snapshot capture: hashed, immutable, manifest-recorded. Must start now; every uncaptured week is permanently lost.
2. Change-only observation writes with `observed_at` / `snapshot_id` provenance.
3. Field-level revisions Parquet.
4. Release schedule + announcements ingestion; `release_date` / `release_date_source` backfill.
5. Tests per §8.
6. `current/` byte-identical.

**Deferred:**
- `vintage stats` and revision analytics — nothing to measure yet; build once a quarter of data exists.
- Category-migration detection — requires revisions to exist first.
- Tombstone handling — add the column, defer the logic.
- Weekly-static fetching beyond the `Last-Modified` spike (§4.6).

---

## 11. Report back

- Outstanding discovery findings (§2), especially existing schema and manifest shape
- **Result of the `Last-Modified` spike** — this determines whether the release-date taxonomy collapses to one mechanism
- Any deviation from this spec and why
- Date coverage achieved by the release-date backfill, broken down by `release_date_source`
- Whether weekly static reports appear frozen at publication or restated alongside annual files — open empirical question; the answer determines whether any further historical vintage recovery is possible

---

## 12. Outcome (2026-07-30)

Everything above is the spec **as written before implementation** and is left unedited.
This section records what the build established, including where the spec was wrong.
Implementation lives on branch `claude/cot-revision-snapshots-9b196f`
([cotdata PR #78](https://github.com/mspinola/cotdata/pull/78)), unmerged at time of
writing. Design detail: [cot_vintage.md](cot_vintage.md). Decision: crucible-stack
ADR-0008 ([crucible-stack PR #13](https://github.com/mspinola/crucible-stack/pull/13)).

> `cot_vintage.md` ships in this same PR, so that link resolves here and on `main` after
> merge. The cross-repo `ADR-0008` reference is the exception: it lands separately with
> crucible-stack #13, so it resolves only once that merges. Everything stated in this
> section is independent of both and stands on its own.

### 12.1 The `Last-Modified` spike: a negative result

§4.6 hoped the spike would collapse the release-date taxonomy to one mechanism. **It did
not.** Historical release dates cannot be recovered from HTTP headers:

- Annual zips are regenerated, so their `Last-Modified` carries no per-week information.
- Past weekly statics are not archived — one file, overwritten each week.

`announced` and `scheduled` therefore remain the only sources for historical weeks, and
§4.6's fallback chain stands as written rather than as a contingency. Recorded explicitly
as a null so it is not re-investigated.

The spike did produce one gain: the weekly static's `Last-Modified` **is** a true
publication timestamp for the current week, which is strictly better than "first
`observed_at` at polling interval."

### 12.2 Header sweep (measured 2026-07-30)

| Year | `Last-Modified` |
|---|---|
| 2020 | 2021-10-29 |
| 2021 | 2022-01-14 |
| 2022 / 2023 / 2024 | 2026-01-15 (one bulk re-touch, not weekly) |
| **2025** | **2026-07-24 19:27:59** |
| **2026** | **2026-07-24 19:27:59** |

**The rolling two-year window.** 2025 and 2026 share an identical timestamp: CFTC's weekly
job regenerates the current year *and the immediately-prior year*. Everything older is
static. An earlier draft claimed all closed years were re-touched weekly; that was wrong.

This explains a long-standing observation that weekly downloads grab both the current and
prior year: the pipeline conditions on `Last-Modified`, CFTC re-touches it, and identical
bytes are re-fetched. No revision is involved.

**Conditional GET.** cftc.gov serves **no `ETag` on any file**, so `If-None-Match` can
never fire. `If-Modified-Since` does work (verified 304 against both a static year and the
current year), so on an `--all` sweep only ~2 of ~40 annual files actually transfer.

**Content churn.** Inner zip entry mtimes show closed years are byte-frozen (2025's content
has not changed since January despite weekly header re-touching); only the current year
genuinely churns. Caveat: inner mtime is strong evidence, not proof — a regeneration
preserving source mtimes would look identical. `content_sha256` across two weeks is
definitive, and capture now does that automatically.

### 12.3 Decisions taken

- **Retention: keep everything, no pruning.** ~1 GB/year of current-year churn is
  immaterial against irreplaceability, and those weekly copies *are* the vintage series —
  pruning them destroys the artifact being built.
- **`--all` is a restatement tripwire, not a backfill.** Since closed years are frozen, a
  `content_sha256` change on one is precisely the 2008-style retroactive-restatement
  signature, and it is the only automated detector for the failure mode this subsystem
  exists to guard against. Monthly or quarterly; weekly is pointless because nothing older
  moves. Implemented as a `restatement_suspect` flag on any closed-year content change.
- **Capture runs on the producer, daily.** Daily because nearly everything 304s, so it is
  nearly free while catching holiday shifts and backlog publications with no schedule
  logic, and it tightens `observed_at` from a seven-day to a one-day bound. On the
  producer because the replicas are mirrored (`robocopy /MIR`, `rsync --delete`) and a
  vintage tree written on a replica is **deleted** by the next sync — irrecoverable, since
  CFTC serves current state only. `COTDATA_VINTAGE_ROOT` relocates the tree for a replica
  that must capture anyway.
- **Futures-only carry-forward.** Only `dea_fut_xls` / `fut_disagg_txt` / `fut_fin_txt` are
  fetched, all futures-only, so `combined` is constant-`False` in the natural key today.
  This is a deliberate carry-forward of existing producer scope under the §0 non-goal, not
  an oversight. `combined` stays in the key so the two series can never silently merge
  (§6); adding the combined files later is a fetch-list change with no schema migration.
  **Consequence: half the reportable universe is absent** until that happens.

### 12.4 Deviations from this spec

| Spec said | Built | Why |
|---|---|---|
| polars (§3.2) | pandas + pyarrow | polars is not a dependency; pandas/pyarrow already are, so the no-database decision is not undone by adding a near-equivalent |
| `manifest.json` (§3.1) | `snapshots.json` | Both deployed sync scripts exclude `manifest.json` **unanchored** (robocopy `/XF`, rsync `--exclude` match at any depth), so it would have been stripped in transit, delivering raw archives to a replica with no index |
| fixtures from trimmed real CFTC files (§8) | synthetic frames | Matches the repo's existing test idiom, runs offline, and avoids an `xlrd` fixture dependency |
| `vintage recover --from-git` (§7) | not built | Git archaeology found zero committed data files; there is nothing to recover |
| weekly-static work deferred (§10) | **`published` shipped** | The deferral assumed the file needed parsing. It is a headerless positional CSV covering exactly ONE report date (365 rows, 129 columns, one distinct value in field 2), so the mapping reads one field. ~30 lines, so it landed. Verified live: report date 2026-07-21 → 2026-07-24 ET publication |

### 12.5 Answers to §11

- **Backfill coverage by `release_date_source`: none yet.** No production vintages exist —
  capture is forward-only and git recovery returned nothing, so there is nothing to report
  coverage over. The machinery is tested (backlog week → `announced`; otherwise
  `derived`), but real numbers require captures to accumulate.
- **Are weekly statics frozen at publication or restated?** Neither, and the question
  dissolves: the weekly static is a **single file, overwritten** each week, not a per-week
  archive. So it offers no route to further historical vintage recovery.

### 12.6 Still deferred

`vintage stats` (needs roughly a quarter of data to measure), category-migration detection
(needs revisions to exist), tombstone *logic* (column present; needs a real disappearing
key to design against), and full canonicalisation of the weekly static **into
observations** (129 positional columns, genuinely larger than the `published` mapping).
