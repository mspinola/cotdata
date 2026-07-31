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

## 6. The frozen-year tripwire is now automated (2026-07-30)

§3b established that CFTC regenerates a **rolling two-year window**, and §12.3 of the
handoff recorded the consequence as a decision ("`--all` is a restatement tripwire, not a
backfill, monthly or quarterly"). That decision had a hole: a tripwire that only fires when
somebody remembers to run `--all` by hand is not a detector, it is an intention. This pass
closes it.

Three regeneration regimes, each with a different expected outcome:

| Regime | Files | Expected weekly outcome | What violates it |
|---|---|---|---|
| `churn` | current year | new bytes | nothing, that is the data arriving |
| `frozen_in_window` | **prior year** | `unchanged bytes (deduped)` | a new sha (restatement), or a 304 / failure (the check went blind) |
| `frozen_out_of_window` | 2 or more years back | `304 not-modified` | a 200 carrying new bytes |
| `weekly` | the weekly static | new content each Friday | n/a |

The prior-year slot is the load-bearing one, and it is the only place CFTC hands over a
free weekly **content** check on closed data. Everything older is never re-served, so its
content is not verified at all: a 304 there proves only that a header did not move.

**Changed: the prior year is now in the DEFAULT fetch set.** It costs one roughly 7 MB
transfer per week (the other six days return 304, because CFTC re-touches `Last-Modified`
weekly) and zero bytes on disk, because the content is byte-identical and dedupes. That is
the entire price of the only automated retroactive-restatement detector in the stack.
`--no-prior-year` turns it off. A targeted `--year N` is still taken at face value with no
prior-year companion, because that is a targeted capture rather than the scheduled sweep.

Two alert shapes, deliberately triggered differently:

- **Content changed on a frozen year: always alerts.** It is a discrete event with new
  bytes sitting beside the old ones, so it is diffable, and two restatements in
  consecutive weeks are two things worth knowing rather than one. In January the message
  says so explicitly, since year-end finalisation is the one benign way this fires.
- **The detector went blind (frozen-in-window 304 or fetch failure): alerts on the
  TRANSITION only.** This is a standing condition. If CFTC stopped re-touching the prior
  year, a level-triggered alert would fire every day forever, and an alert that never
  clears is one that gets ignored. Same reasoning that scopes the ingest revision alert to
  the current run's snapshots.

`fetch` deliberately still exits zero. The Windows wrapper aborts the whole run on a
non-zero fetch, so alerting there would skip the `ingest` that turns a restatement into
readable revision rows, which is exactly what you want when it fires. The alert is
persisted on the snapshot (`expectation`, `outcome`, `tripwire_alert`) and re-raised by
`ingest`, which is the step that can exit non-zero safely because everything downstream of
it has already run. That path already writes the `REVISIONS_<date>.txt` marker file.

## 7. Flow decomposition, and what it found in the canonical schema

Module spec §6.4 built as `vintage_flow.py`. Weekly ΔLong versus ΔShort per
market/category, labelled `new_longs` / `short_covering` / `new_shorts` /
`long_liquidation`. No prices, no contract master, no multiplier: every input is a column
the canonical schema already stores, which is what makes it a clean smoke test of that
schema rather than of anything else.

Two design points that are not in the spec because they only appear against real data:

- **Classification is by dominant leg.** The spec's table has a "~0" leg, which never
  happens: both legs always move a little. Whichever of |ΔLong|, |ΔShort| is larger names
  the state and its sign picks the direction, with exact ties going to the long leg so the
  result is deterministic. This is parameter-free, so there is nothing to tune and nothing
  to overfit. An optional `min_frac_oi` dead zone adds a `quiet` state; it defaults to 0.0
  because any other value is a judgement in the same class as the fragility weights and
  belongs in a caller's config where it can be swept.
- **`oi_corroborates`.** Futures are closed and zero-sum, so contracts exist only because
  somebody opened them: fresh positioning should coincide with rising open interest and
  exits with falling. Where it does not, the label is describing a transfer of an existing
  position between categories rather than new or closed risk. Kept as a separate column
  rather than folded into the state, because open interest in the canonical schema is the
  MARKET total (that is what CFTC reports), so it corroborates a per-category label
  against a market-level quantity. A real check, not a proof.

### Measured over the whole store (2026-07-30)

95 Legacy markets, 1986 to 2026-07-21, canonicalised and decomposed:

| Measure | Result |
|---|---|
| Zero-sum identity (Σ long across categories == Σ short) | **149,412 / 149,412 weeks balanced** |
| Flow rows produced | 447,951 |
| Exceptions | 0 |
| `validate()` warnings | 0 (after the fix below) |

The zero-sum result is the strongest available validation of `canonicalize_legacy`: if the
category mapping had dropped, duplicated or misrouted a column, the identity would break on
the first week. It does not break on any week of any market in forty years.

### Finding 1: non-commercial SPREADING is not captured, and never was

The side totals reconcile with each other but fall short of open interest by a constant,
equal amount on both sides. That gap is `NonComm_Positions_Spread_All`, which is absent
from `providers/cftc.py`'s `TARGET_COLS` and therefore from the stored parquet, from the
canonical rows, and from every vintage observation. Gold on 2026-07-21: OI 383,368, both
sides 351,385, gap 31,983. 64 of 95 markets have at least one week where the gap is zero
(no spreading that week), which is the confirming case.

Spreading is a matched long and short held by one trader, so it cancels out of the
long-versus-short identity while still counting toward open interest, which is exactly why
the gap is equal on both sides. **Consequences to know before relying on this:**

- Net positioning is unaffected, because spreading is long and short in equal measure.
- Anything denominated as a **share of open interest** (module spec §5.2 step 2, which is
  the next build step) is measuring a numerator against a denominator that includes
  contracts the numerator cannot see. In gold that is 8% of OI.
- Trader counts are likewise absent from the stored table, though the vintage ingest path
  reads them from the zip directly rather than from the parquet.

Not fixed here. Adding the column changes `providers/cftc.py` output, which breaks the
byte-identical guarantee and the `current/` golden baseline that exists to protect it, so
it is a deliberate separate change and not a drive-by.

### Finding 2: `validate()`'s open-interest warning was mis-specified

It compared one category's `long + short + spread` against total open interest. That is
not a real bound: the two sides of a market are counted separately, so a category holding
80k long and 294k short in a 383k-OI market sums to 374k with nothing wrong. Measured, it
fired on **811 of 5,778 gold rows (14%)**, which is noise, and a soft warning at that rate
is one nobody reads.

Corrected to the invariant that does hold: per side, summed across every category, since
every long contract in the market belongs to exactly one category. Store-wide warnings
went from thousands to **zero**. The module spec's §4 adapter contract said
`long + short + spread <= OI`, so the spec was wrong too and is amended in the same pass.

### Finding 3: COT was FORTNIGHTLY until 1992-10-13

`days_elapsed` is emitted as a column rather than assumed to be seven, and that
immediately showed the reason. Across the store, 415,908 of 447,951 intervals are 7 days;
the 15-day (9,057) and 14-day (5,775) intervals are almost entirely pre-October-1992,
when the report was published twice a month. Gold's first 7-day interval is 1992-10-13.

So a "weekly change" computed over pre-1992 history is a fortnightly change and is not
comparable to the rest of the series. The remaining off-7 intervals are holiday shifts
(gold has 11-day gaps at 2002-01-08 and 2003-02-25). The column is the fix: callers filter
on it rather than discovering this in a result.

## 8. Disaggregated and TFF canonicalisers (2026-07-30)

The step-2 proposal identified this as the real prerequisite, not step 2 itself: the
registry declares 41 disaggregated and 8 TFF symbols and zero legacy, and every engine in
the module spec keys on **Managed Money** and **Leveraged Funds**, which exist only in
those two reports. Ingest wired Legacy only, so no point-in-time series existed for the
categories the whole system is built around. Now it does.

Both were built and verified against the **real captured snapshots** from the first
production capture (2026-07-31 01:15Z), not fixtures.

| Report | Canonical rows for 2026 | Weeks | Categories |
|---|---|---|---|
| Legacy | 31,041 | 10,347 | commercial, noncommercial, nonreportable |
| Disaggregated | 39,235 | 7,847 | producer_merchant, swap, managed_money, other_reportable, nonreportable |
| TFF | 12,500 | 2,500 | dealer, asset_manager, leveraged, other_reportable, nonreportable |

Ingest of all three takes about 5 seconds. 418 distinct market codes, which is far more
than the 95 in the current-state Legacy store, because the vintage layer canonicalises
everything CFTC published rather than the registry universe. That is deliberate: capture
everything, filter on read.

### These two reports populate three fields Legacy never does

- **Per-category spreading.** So the identity closes completely, which the Legacy defect
  (§7 finding 1) prevents there.
- **Per-category trader counts.** §6.2's breadth-depth quadrant needs these and cannot be
  built from Legacy.
- **CR4 / CR8 net concentration**, per market, repeated on each category row exactly as
  open interest is. Only the net ratios have canonical columns; the gross ones are
  published too and have nowhere to land.

### The zero-sum identity by report, measured

| Report | Exact | Within tolerance | `oi_gap` |
|---|---|---|---|
| Legacy | 10,321 / 10,347 | **10,347 / 10,347** | never zero (the uncaptured spreading) |
| Disaggregated | **7,847 / 7,847** | 7,847 / 7,847 | **zero everywhere** |
| TFF | 2,463 / 2,500 | **2,500 / 2,500** | zero on 98.6% |

Disaggregated closing exactly, with a zero gap, is the confirming counterpart to the Legacy
finding: the gap there really is the missing spreading column and nothing else.

**The residual is CFTC's own rounding, and it is fully localised.** Every off-by-one-or-two
row in *both* Legacy and TFF falls in exactly three markets:

| Market code | Name | Legacy rows | TFF rows | Worst |
|---|---|---|---|---|
| `13874+` | S&P 500 Consolidated | 12 | 17 | 2 |
| `20974+` | NASDAQ-100 Consolidated | 11 | 15 | 1 |
| `12460+` | DJIA Consolidated | 3 | 5 | 1 |

The `+` suffix is CFTC's own marker for a Consolidated contract, which aggregates several
contract sizes onto a common unit and therefore involves a division. So the tolerance is
derived from the mechanism rather than fitted: summing `n` independently rounded category
figures admits at most `n` contracts of error, which is what `rounding_tolerance()`
returns. Without it, 48 off-by-one warnings fire on the 2026 files alone, which is the
same cry-wolf rate §7 finding 2 was corrected for.

### Three implementation points worth knowing before debugging them

1. **CFTC's header row has a typo, and it is load-bearing.**
   `Swap__Positions_Short_All` and `Swap__Positions_Spread_All` have a double underscore;
   `Swap_Positions_Long_All` has one. Both spellings resolve, so the day CFTC fixes it is
   not the day Swap Dealer positions start ingesting as nulls.
2. **A column that cannot be resolved raises.** Silently returning nulls is the worst
   available outcome: they get written as real observations, and the next genuine value is
   then recorded as a revision that never happened, permanently polluting the revision
   history this subsystem exists to produce.
3. **Trader counts arrive as strings**, because CFTC writes `.` for a suppressed count.
   Every value field is coerced with `errors="coerce"` so a suppression marker or a stray
   pad can never reach `row_sha256` as a literal string.

### `combined` is now read from the file

Both reports carry a `FutOnly_or_Combined` column. It is read rather than hardcoded, and a
file mixing the two is refused. Today it is constant-`FutOnly`, so nothing changes, but
adding the combined files becomes purely a fetch-list change with the canonicaliser already
correct. §3c's carry-forward stands; the code no longer assumes it.

### `canonicalize_legacy` was deliberately left alone

The new code shares one vectorised helper; Legacy still uses its original per-row loop.
That duplication is intentional. Legacy's output feeds `row_sha256`, which is a permanent
artifact over rows already stored in production, and rewriting the code path that produced
those hashes to save a little duplication risks registering every stored row as revised at
once. The saving was not worth the risk.

### Fixed in passing: raw paths were unreadable off the producer

`snapshots.json` records `local_path` as written by the capturing machine, and the producer
is Windows, so the real store carries `vintage\raw\annual_zip\2026\....zip`. On macOS or
Linux that is a single filename containing backslashes, so ingest on either replica failed
with "no such file" on a file plainly sitting there. Normalised on read rather than on
write, so every snapshot already recorded on the producer stays readable.

## Bottom line

Discovery and the spike are done. The genuine null on historical recovery holds: no
past vintages exist to recover, from git or from CFTC, so the vintage series starts
from this session's first capture. The one materially useful thing against *existing*
history is the release-date backfill (schedule + announcements), which the spike
confirmed cannot be shortcut via `Last-Modified` and must come from the two published
sources — most valuably for the Oct–Dec 2025 backlog weeks.
