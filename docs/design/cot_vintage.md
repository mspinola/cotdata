# COT Vintage Store & Revision Tracking

Session working notes for the `crowdmon-futures` step-1 build (handoff v0.2).
Authored under `docs/` per workspace governance. This is the durable record of the
discovery phase, the `Last-Modified` spike, and the design as adapted to this repo.

## 1. Why

CFTC serves current-state COT files only: there is no as-published endpoint and no
official vintage archive. Trader **reclassification** moves positions between
categories after the fact, silently rewriting the historical baseline that every
downstream rolling z-score / percentile is computed against (precedent: the July
2008 restatement reached back to July 2007). Vintage data can only be accumulated
going forward, so capture must start now — every uncaptured week is permanently
lost.

## 2. Discovery findings (§2)

The repo is **Parquet-per-symbol + JSON manifest, no database** — a deliberate
producer/consumer contract, and ADR-0007 is actively narrowing `cotdata` to CFTC
positioning only. The vintage layer must sit *alongside* that contract, additively,
leaving current-state output byte-identical.

| # | Finding |
|---|---|
| Storage | One parquet per symbol under `$COTDATA_STORE/{cot_legacy,cot_disagg,cot_tff}/`, atomic temp+`os.replace` ([store.py](../../src/cotdata/store.py)). No DB. |
| Raw retention | Year zips are cached at `$COTDATA_STORE/_cache/cot_legacy/` but **overwritten in place** on regeneration (conditional on `Last-Modified`) — a destructive cache, not an immutable landing zone. |
| Schema | Parse output is a `Report_Date`-indexed (tz-naive `DatetimeIndex`) table: OI, category long/short, trader counts ([cftc.py:27](../../src/cotdata/providers/cftc.py#L27)). **No `release_date` anywhere.** |
| Sources | Legacy (`dea_fut_xls_<year>.zip`), Disaggregated (`fut_disagg_txt_<year>.zip`, from 2006), TFF (`fut_fin_txt_<year>.zip`) — all **futures-only, all annual zips**. No combined, no supplemental, no Socrata. |
| Date handling | `Report_Date` stored **as reported**, `format='mixed'`, never rounded to Tuesday ([cftc.py:98](../../src/cotdata/providers/cftc.py#L98)) — already §6-compliant. Release date not captured. |
| Idempotency | Re-fetch: HEAD/`Last-Modified` skips download if cache fresh; parse always rebuilds the full per-code table and **overwrites** the parquet. Idempotent in outcome, but **destroys prior state** — no diff, no history. This is the gap the vintage layer closes. |
| Manifest shape | Per-half JSON (`manifests/cot.json`, `manifests/prices.json`) + legacy aggregate fallback. Each domain is `{name: {last_date, n_rows, source, updated_at}}`, `schema_version` at top. `half_for()` refuses undeclared domains ([store.py:123](../../src/cotdata/store.py#L123)). |
| Git archaeology | **Zero data files ever committed** (checked parquet/csv/zip/txt/db/duckdb/json across all history). No historical vintages recoverable by any means. `vintage recover --from-git` would be a no-op — not built. |

## 3. NEGATIVE RESULT — `Last-Modified` cannot backfill historical release dates

**Tested 2026-07-30.** Recorded explicitly as a null so it is not re-investigated:
an afternoon spent rediscovering this is the failure this section prevents. The
short version — *annual zips regenerate weekly, so their `Last-Modified` carries no
per-week information, and past weekly statics are not archived.*

Probed CFTC headers directly (network to cftc.gov works from this host, contrary to
the sandbox caveat noted in CLAUDE.md):

| File | `Last-Modified` (UTC) | ET | Verdict |
|---|---|---|---|
| `dea_fut_xls_2025.zip` (annual) | Fri 24 Jul 2026 19:27:59 | 15:27 Fri | **Regenerated weekly.** A 2025 file stamped 2026-07-24 proves the annual zip's timestamp tracks the latest regeneration, not any week's publication. Useless as a per-week release date. |
| `deafut.txt` (weekly static) | Fri 24 Jul 2026 19:27:44 | 15:27 Fri | ~3 min before the nominal Fri 15:30 ET Legacy release. **Reflects true publication time — current week only.** |

**Conclusions**

1. **The taxonomy does not collapse.** Historical release dates cannot be backfilled
   from `Last-Modified`: annual zips are regenerated weekly (worthless per-week), and
   past weekly statics are overwritten, not archived. `scheduled` (published release
   schedule) + `announced` (Special Announcements) remain the only historical
   sources — the §4.6 fallback chain stands as written.
2. **Bonus for forward capture:** the weekly-static `Last-Modified` *is* a real
   publication timestamp. Captured at fetch time it gives a cleaner `observed`
   release date than "first `observed_at` at poll interval." Refines §4.6; does not
   change the plan's shape.
3. Answers the §11 open question negatively: weekly statics are a single
   overwritten file, **not a frozen historical archive** — no further historical
   vintage recovery is possible through them.

## 3b. Measured: sha churn on annual zips (2026-07-30)

Open question was whether zip regeneration rewrites embedded entry timestamps, which
would make every annual file a new sha every week (byte-dedupe never fires, a fresh
multi-MB copy retained weekly). **Measured, and it does not.**

| File | HTTP `Last-Modified` | inner entry mtime | Reading |
|---|---|---|---|
| `dea_fut_xls_2015.zip` | — | 2015-12-31 | frozen at year end |
| `dea_fut_xls_2024.zip` | — | 2025-01-03 | frozen just after year end |
| `dea_fut_xls_2025.zip` | **2026-07-24** (this week) | **2026-01-02** | `Last-Modified` churns weekly; CONTENT frozen since January |
| `dea_fut_xls_2026.zip` | 2026-07-24 | 2026-07-23 | current year: genuinely regenerated, real new data |

**Caveat on the evidence.** Inner entry mtime is strong evidence, not proof: a
regeneration that preserved source mtimes would look identical. The definitive test is
`content_sha256` compared across two weeks, which the capture system now performs
automatically. Week one settles it.

### Which files are actually re-touched (measured 2026-07-30)

An earlier draft of this section claimed "closed years are re-touched weekly." **That was
wrong.** The header sweep shows only the current year and the immediately-prior year move:

| Year | `Last-Modified` |
|---|---|
| 2020 | 2021-10-29 |
| 2021 | 2022-01-14 |
| 2022 / 2023 / 2024 | 2026-01-15 (one bulk re-touch, not weekly) |
| **2025** | **2026-07-24 19:27:59** |
| **2026** | **2026-07-24 19:27:59** |

2025 and 2026 share an identical timestamp: the weekly job regenerates a rolling
current-plus-prior-year window. Everything older is static.

### Conditional GET: works, and `If-None-Match` is dead weight

- **No `ETag` on any CFTC file** (annual zips or weekly static). `If-None-Match` can
  therefore never fire. It is harmless to keep sending (future-proofing if CFTC adds
  one), but `If-Modified-Since` is the only live mechanism today.
- **`If-Modified-Since` genuinely returns 304**, verified directly against both a static
  year (2015) and the current year. Confirmed end-to-end in the pipeline: a second
  `cotdata-vintage fetch` returned 304 on all four default sources, retained nothing new,
  and recorded four check-only snapshot records.

So the conditional GET is NOT decorative: on an `--all` run only the current and prior
year actually transfer, and ~38 of ~40 annual files 304. Sha-dedupe is the backstop for
the two that do transfer (2025 re-downloads weekly, ~8 MB, and dedupes away).

### Decisions this settles

1. **Retention: keep everything, no pruning.** ~1 GB/year of current-year churn is
   immaterial relative to irreplaceability — those weekly copies *are* the vintage
   series, so pruning them destroys the artifact being built. Recorded deliberately
   rather than left to be discovered.
2. **Run `--all` monthly or quarterly, not weekly, and not for backfill.** If closed-year
   files are genuinely frozen, a `content_sha256` change on a closed year is *precisely*
   the 2008-style retroactive-restatement signature. The point of `--all` is not to
   collect bytes but to detect a change that should be impossible — the only automated
   detector for the failure mode this whole subsystem exists to guard against. Weekly is
   pointless (nothing older moves); monthly/quarterly is cheap because almost everything
   304s.

## 3d. DEPLOYMENT HAZARD: `robocopy /MIR` deletes a replica-local vintage tree

**Read before scheduling capture anywhere.**

This deployment syncs the store from one Windows producer to two read-only replicas
(Mac over SMB, Linux VPS over rsync). The Mac push is `robocopy /MIR`, which deletes
anything present at the destination and absent at the source, excluding only
`/XD _cache _raw citpy` and `/XF manifest.json`. **`vintage` is not excluded.**

So a `vintage/` tree written on the Mac replica is **destroyed by the next producer
sync**. This is not hypothetical: `sync-store.cmd` already documents exactly this
outcome for `citpy` ("not written by any producer, so /MIR removes it and no producer
run brings it back"). The difference is that vintage data is **irreplaceable** — CFTC
serves current state only, so a deleted vintage cannot be re-fetched, ever.

Two safe placements:

1. **Run capture on the producer (preferred).** Vintage capture *is* a producer action
   (it fetches from CFTC), so it belongs on the same machine as the COT half, and the
   tree then syncs outward to replicas like any other store content. This also matches
   the ADR-0007 seam: the CFTC producer owns the `cot` half.
2. **Run on a replica with `COTDATA_VINTAGE_ROOT` pointing outside the mirrored store.**
   The override exists for this. The tree then survives `/MIR`, but does not propagate to
   other machines — acceptable for a single-box capture, but it makes that box the sole
   custodian of irreplaceable data, so it needs its own backup.

Adding `vintage` to the `/XD` list is a third option and is NOT recommended: it protects
the tree only as long as every future sync invocation remembers the flag, and one
forgotten flag is unrecoverable.

### Real-network smoke (2026-07-30, pre-merge)

Default path (`cotdata-vintage fetch` = current year × 3 reports + weekly static) run
against live cftc.gov into a throwaway store: all four URLs resolved, the UA was
accepted, four 200s retained (legacy 4,943,331 B; disagg 1,399,661 B; TFF 403,332 B;
weekly static 417,064 B), provenance recorded with `parse_status=pending`. The legacy sha
matched an independent manual download. A second run returned 304 on all four and
retained nothing. URL builders and headers are therefore verified live, not just in
fixtures.

## 3c. Decision: futures-only, `combined` is constant-False for now

`annual_sources` fetches `dea_fut_xls` / `fut_disagg_txt` / `fut_fin_txt` — all
**futures-only**. The futures-and-options-combined files are NOT fetched, so the
`combined` dimension of the natural key is `False` for every row today.

This is a **deliberate carry-forward, not an omission**: the existing producer has only
ever fetched futures-only (discovery §2.3), and this task's stated non-goal is to avoid
changing existing fetch scope. `combined` stays in the natural key so the two series can
never silently merge into one time series (§6) if/when combined files are added — adding
them later is a fetch-list change, with no schema migration.

Consequence to be explicit about: **half the reportable universe is absent** until that
happens. Anything needing futures-and-options-combined positioning is blocked on this.

## 4. Design as adapted to this repo

Layout under `$COTDATA_STORE/vintage/` (a new subtree, disjoint from `current/` which
is the untouched existing `cot_legacy/` etc.):

```
vintage/raw/{source_kind}/{year}/{retrieved_at}_{sha8}.{ext}   immutable
vintage/observations/report_year=YYYY/*.parquet                change-only rows
vintage/revisions/detected_year=YYYY/*.parquet                 append-only, field-level
vintage/release_schedule.parquet
vintage/announcements.parquet
```

Manifest: a new `vintage` block on the **cot-half** file (`manifests/cot.json`), so it
stays on the CFTC producer's side of the ADR-0007 seam. `store._DOMAIN_HALF` gains
`vintage -> cot`.

### §4.6 amendment (confirmed, not contingent)
The spike upgrades the `observed` mechanism: capturing the weekly-static
`Last-Modified` **at fetch time** yields a *true publication timestamp*, not merely a
value accurate to the polling interval. So `observed` is now the confirmed primary
path for weeks captured going forward, and §4.6's precedence chain (`observed` >
`announced` > `scheduled` > `derived`) otherwise stands exactly as written — the
fallback path is the confirmed path, not a contingency.

### Deviations from v0.2 (and why)
- **pandas/pyarrow, not polars.** polars is not a dependency; pandas + `pyarrow>=10`
  are. Implementing change-only-insert / PIT in pandas avoids adding a datastore-like
  dep, consistent with the no-DB decision.
- **Fixtures are synthetic**, matching the repo's existing test idiom (`tests/test_cot.py`
  builds DataFrames directly), rather than trimmed real `.xls` — deterministic, offline,
  and avoids an `xlrd` fixture dependency. Capture is tested with small byte payloads.
- **ADR home is crucible-stack** (beside ADR-0007), numbered ADR-0008. cotdata keeps a
  one-line stub pointing to it; the full ADR is moved in the crucible-stack worktree.
- **ADR is cotdata-local** (`docs/adr/ADR-0001`), cross-referencing crucible-stack
  ADR-0007, rather than editing the sibling repo from this worktree.
- **`vintage recover --from-git` not built** — git archaeology found nothing to
  recover.
- **`current/` unchanged**: the vintage layer reads the same parsed frames the
  existing providers already build; it does not alter their write path.

### Canonical schema
Natural key `(report_date, market_code, report_type, combined, category)`. `combined`
is `False` for every row this session (only futures-only is fetched) but is kept in
the key so a future combined series never collides. Controlled vocab per report type
is derived from the provider column constants (Legacy: `commercial/noncommercial/
nonreportable`; Disagg: `producer_merchant/swap/managed_money/other_reportable`; TFF:
`dealer/asset_manager/leveraged/other_reportable`).

## 5. Scope this session (§10)

In: raw snapshot capture; change-only observation writes; field-level revisions;
release schedule + announcements ingest + `release_date`/`release_date_source`
backfill; §8 tests; `current/` byte-identical.

**`published` shipped too, contrary to the original deferral.** §10 deferred
weekly-static work on the assumption it required parsing that file. Measurement showed
otherwise: the weekly static is a headerless positional CSV covering exactly ONE report
date (365 rows, 129 columns, a single distinct value in field 2), so mapping a snapshot
to its report date reads one field, and its publication timestamp was already being
captured into `snapshots.json`. Deriving `published` cost ~30 lines rather than a
canonicaliser, so it landed. Verified live: report date 2026-07-21 resolves to a
2026-07-24 ET publication date. (Full canonicalisation of the weekly static *into
observations* remains genuinely larger and is still out of scope.)

`published` is forward-only by nature — the weekly static holds one week and is
overwritten — so it covers weeks from the first capture onward; historical weeks stay on
`announced` / `scheduled` / `derived`.

Still deferred: `vintage stats` (needs a quarter of data to measure),
category-migration detection (needs revisions to exist), tombstone *logic* (column
present, needs a real disappearing key to design against).

## Bottom line

Discovery and the spike are done. The genuine null on historical recovery holds: no
past vintages exist to recover, from git or from CFTC, so the vintage series starts
from this session's first capture. The one materially useful thing against *existing*
history is the release-date backfill (schedule + announcements), which the spike
confirmed cannot be shortcut via `Last-Modified` and must come from the two published
sources — most valuably for the Oct–Dec 2025 backlog weeks.
