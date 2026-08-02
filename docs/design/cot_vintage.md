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

Manifest: ~~a new `vintage` block on the **cot-half** file (`manifests/cot.json`), so it
stays on the CFTC producer's side of the ADR-0007 seam. `store._DOMAIN_HALF` gains
`vintage -> cot`.~~

**Corrected 2026-07-30** (caught in adversarial review; this paragraph described the plan,
not what shipped). Provenance lives in a **self-owned `vintage/snapshots.json`**, and
`store._DOMAIN_HALF` has no `vintage` key. Two reasons, both discovered during the build:
`store.reconcile_manifest` prunes any manifest entry lacking a matching `{name}.parquet`,
and raw snapshot ids are not parquet files, so they would have been ghost-pruned; and both
deployed sync scripts exclude the name `manifest.json` unanchored, so a
`vintage/manifest.json` would have been stripped in transit. The handoff's §12.4 recorded
this deviation correctly, so this file was the stale one.

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
| `frozen_in_window` | **prior year** | `unchanged bytes (deduped)`, once a week | a new sha (restatement), or silence past `BLIND_AFTER_DAYS` (the check went blind) |
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
- **The detector went blind (a frozen-in-window year that stops being re-served): alerts
  ONCE PER QUIET PERIOD**, when the silence passes `BLIND_AFTER_DAYS` (9). This is a
  standing condition. If CFTC stopped re-touching the prior year, a level-triggered alert
  would fire every day forever, and an alert that never clears is one that gets ignored.
  Same reasoning that scopes the ingest revision alert to the current run's snapshots. A
  fetch failure is deliberately **not** a blind condition: connectivity is not provenance.
  Note that the expected outcomes in the table above are per WEEK while the task runs
  DAILY, which is the trap §6b fell into.

`fetch` deliberately still exits zero. The Windows wrapper aborts the whole run on a
non-zero fetch, so alerting there would skip the `ingest` that turns a restatement into
readable revision rows, which is exactly what you want when it fires. The alert is
persisted on the snapshot (`expectation`, `outcome`, `tripwire_alert`) and re-raised by
`ingest`, which is the step that can exit non-zero safely because everything downstream of
it has already run. That path already writes the `REVISIONS_<date>.txt` marker file.

## 6b. The blind detector cried wolf every Saturday (production, 2026-08-02)

First finding from real scheduled operation, and it is a straight contradiction between
§6's own two halves. The table says the prior year's expected outcome is
`unchanged bytes (deduped)`; the paragraph under it says the file transfers **once a week**
and "the other six days return 304". Both are true. The trigger read only the first: it
alerted whenever the previous run had seen bytes and this one did not, which on a daily
schedule is not a blindness test but a day-of-week test.

Measured. The Windows task runs daily at 17:00 ET. CFTC regenerated on Friday 2026-07-31 at
19:27 GMT, touching the whole rolling two-year window in one pass (2025 and 2026 share the
timestamp to the second). The 2025 baseline landed at 13:54Z on Saturday; the scheduled
21:00Z run the same day, seven hours later, got its 304 and fired on all three prior-year
sources:

```
*** FROZEN-YEAR TRIPWIRE ***
    legacy 2025 [frozen_in_window -> not_modified] at 2026-08-01T21:00:09Z
    disaggregated 2025 [...] / tff 2025 [...]
```

Nothing was wrong. A live `HEAD` on 2026-08-02 returned the unchanged
`Last-Modified: Fri, 31 Jul 2026 19:27:43 GMT` and a `content-length` matching the retained
zip byte for byte on all three files. The detector was working; only the alarm was broken.
Left alone it would have fired three times every Saturday forever, which is the same
"alert that never clears" failure the edge-trigger was introduced to prevent, wearing a
weekly costume.

**Fix: blindness is elapsed time since bytes last arrived, not the previous record's
outcome.** `BLIND_AFTER_DAYS = 9` is one weekly cycle plus two days of slack, so one
skipped regeneration or a day of run outages is silent and two consecutive misses are not.
The edge is now the quiet period itself (one alert per period, however many runs fall
inside it), which also makes it robust to a fetch failure landing mid-period. Replaying all
18 production snapshots through the new trigger gives 3 alerts before, 0 after.

Two smaller things fell out:

- **`_latest_with_content` does not mean "when did bytes last arrive".** A 304 record
  copies the sha, etag and `Last-Modified` forward from the snapshot it matched, precisely
  so the next conditional GET can be issued from it, so a 304 passes a
  `content_sha256`-based filter. Measuring silence from it reports "one day" forever and
  silently disables the alarm. `byte_size` is the only field that means bytes arrived, and
  `_latest_delivery` is the lookup that uses it.
- **The §6 bullet listed a fetch failure as a blind condition**, which the same review pass
  that wrote the bullet had already removed from the code. Corrected above.

## 7. Flow decomposition, and what it found in the canonical schema

> **The decomposition has been REMOVED from this package (2026-08-02). What it found has
> not.** `crowdmon.futures.flow.decompose` was built independently on 2026-08-01, and the
> two were carried side by side as defensible alternatives until somebody measured them:
> this one was that one **at `tolerance=1.0` with the gap rule off**, agreeing on
> **100.000000% of 135,835 transitions with zero mismatches** and identical `d_long`,
> `d_short` and `d_net` on every row. One function, with the copy here hard-wired to the
> corner where nothing is ever `mixed` and no interval is ever refused. The measurement is
> in `crowdmon/docs/design/amendments-2026-08-02.md` §B29 and is asserted by
> `crowdmon/tests/test_flow_equivalence.py`.
>
> **This section is kept rather than deleted** because what it records is a result about
> *the canonical schema*, obtained by running a decomposition over it. The zero-sum sweep,
> the spreading finding and the `validate()` finding below all stand and none of them
> depend on where the classifier lives. `zero_sum_check` stays here; it is a statement
> about cotdata's own parse.
>
> The design points immediately below are preserved as the record of how the classifier
> was arrived at. **They describe `crowdmon.futures.flow` now**, with one exception noted
> inline.

Module spec §6.4 was built as `vintage_flow.py`. Weekly ΔLong versus ΔShort per
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

  > **This is the one point that did not carry over, and the argument turned out to be
  > half right.** Parameter-free was a real virtue and it cost the ability to say a week
  > was genuinely two-sided: dominant-leg always commits, so a category that added 30,000
  > longs and 28,000 shorts is called `new_longs`. The surviving implementation takes a
  > dominance `tolerance` instead and reports a `mixed` state, which is the modal outcome
  > at **60% of weeks** on the liquid panel, so the four-state table describes a minority
  > of the data. The objection that a parameter is something to overfit was answered by
  > sweeping it rather than by avoiding it: the tolerance moves 28.74% of labels across
  > 0.15 to 0.40, and **not one of those changes moves a week from one pure state to a
  > different pure state**. It gates whether the classifier commits, never which direction
  > it commits to. `min_frac_oi` itself was never set by anything and is simply gone.
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

## 9. Adversarial review, 2026-07-30

The subsystem was reviewed by an agent given the spec and the diff and **no prior context**,
specifically because every earlier review had been done by its author and would have shared
its blind spots. That was worth doing: it found six confirmed defects, four of them in code
written the same day, and one of them fires on an event as ordinary as a dropped network
connection. It also confirmed the hash-purity property empirically rather than by reading,
which is the one property the whole design rests on.

### Fixed in this pass

| # | Defect | Why it mattered |
|---|---|---|
| 1 | A **fetch failure poisoned the dedupe comparison**. Failure records carry no sha, etag or Last-Modified, so the next fetch sent no `If-Modified-Since`, could not match the dedupe test, and was classified as changed content. | One network blip on a frozen year produced a **false restatement alert**, which is the single alarm this subsystem exists to raise, and re-retained megabytes already on disk. Split into `_latest_with_content` (content questions) versus `_latest_for_url` (outcome questions). |
| 2 | An **absolute `local_path` was re-rooted under the store**, turning `/Volumes/ext/...` into `<store>/Volumes/ext/...`. | `COTDATA_VINTAGE_ROOT` is *mandatory* on a mirrored replica, so on exactly those hosts every ingest failed, was swallowed, and marked the snapshot `failed`, which `--pending` never re-selects. One run drained the entire backlog with no CLI able to reset it. |
| 3 | A bare `ingest` with no flags **replayed every snapshot ever recorded** with `observed_at = now`. | Replaying an older snapshot after a revision wrote the superseded value back with a newer timestamp, emitting a reversed revision (35 to 30) then a re-revision (30 to 35) and inflating `age_days` on both. `revisions/` is the primary artifact and `age_days` is what tells a consumer whether a restatement reached its calibration window. Now defaults to pending, `--all-snapshots` opts into a replay, and `observed_at` comes from the snapshot's own `retrieved_at`. |
| 4 | **`schedule backfill` bypassed the write lock** while doing a read-modify-write of every observations partition. | Run beside an ingest it silently dropped that ingest's appended rows, leaving a revision row asserting a change to a value no longer present in `observations/`. Per-file atomicity was never the missing piece. |
| 5 | **Every flat week was labelled `long_liquidation`**, because `d_long <= 0` swallows zero and the dead zone is off by default. | Measured on the real 2026 Legacy file: 3,308 of 29,787 transitions (11.1%), which made `long_liquidation` the modal state with a third of its bucket being weeks where nothing happened. That `value_counts()` is the CLI's headline output. Zero is now `quiet` unconditionally, with no threshold involved. |
| 6 | **`validate()` accepted duplicate natural keys**, and **had no null-rate band** despite spec §5 requiring one. | Duplicates get identical `(observed_at, snapshot_id)`, exhausting the tie-break so append order decides the stored value. The null band closes the gap `errors="coerce"` opened: a changed value *format* in a column whose *name* never moved would coerce to nulls, which get written as observations and then read as revisions when the values return. Checked per category, since one broken column is only 1/n of a melted frame. |

### Confirmed clean, by measurement rather than by reading

Hash purity, the property everything else depends on, was exercised directly: mutating
`observed_at`, `snapshot_id`, `release_date`, `release_date_source`, `market_name`,
`is_tombstone`, every natural-key column and an unknown extra column all leave
`row_sha256` unchanged, while each of the ten `VALUE_FIELDS` changes it. Scalar stability
holds across `int`, `np.int64/32`, `float`, `np.float64/32`, `str`, `Decimal` and pandas
nullable `Int64`. A release-date backfill was confirmed empirically not to move any hash.

Atomicity was clean everywhere it was checked: `_write_manifest`, `_append_parquet`,
`vintage_schedule._write`, backfill's per-file write and the raw `.part` write all use
temp plus `os.replace`, and `_WriteLock` uses `O_CREAT|O_EXCL` and fails loudly.

### NOT fixed, recorded as a real gap: the `announced` tier is unreachable

> **CLOSED 2026-08-02, see §10.** Criterion 5 is met. The argument below is left exactly
> as it was written, because its reasoning was right and only its premise was wrong: the
> backlog release dates are not free-text prose, they are a published table.

**Acceptance criterion 5 is unmet.** `sync()` scrapes the Special Announcements page into
`announcements.parquet`, but nothing ever writes a `source="announced"` row into
`release_schedule.parquet`, and `backfill` reads only the schedule table plus
`published_from_snapshots()`. So the Oct-Dec 2025 backlog weeks, the named target of that
criterion and §6's "single largest PIT hole", can only ever resolve to `derived` in
production. The precedence logic itself is correct and tested; the tests pass because they
inject a schedule frame directly.

The plumbing is not the missing piece: `write_release_schedule` already accepts a `source`
column, so an `announced` row would flow through correctly. What is missing is a producer,
and building one means extracting a `(report_date, release_date)` pair from free-text
announcement prose. `_parse_announcements` deliberately never raises and captures only
`announcement_date` plus `raw_text`. **Writing that extractor against no corpus of real
announcements would be guessing**, and a release date resolved by a guess is worse than a
`derived` one, because it carries a provenance flag claiming it was announced. Left
unbuilt, and recorded here rather than quietly assumed to work.

### Second review pass, on the six fixes themselves

The fixes were then reviewed by a second cold agent, because they had been written by the
same person who wrote the code being fixed and nobody had looked at them. It downloaded
**every real CFTC annual file, 41 Legacy years plus 17 Disaggregated and 17 TFF years,
roughly 1.6 million canonical rows**, and put all of it through the two new raising checks.

Its verdict on fix 6 is the most valuable result in either review: **no legitimate
duplicate natural key exists in 40 years of any report type, and the null rate on
`long_contracts` / `short_contracts` / `open_interest` is exactly 0.0 in every
`(report_type, category)` group of every year.** So neither new raise can hard-fail a
historical backfill. Fixes 4 and 5 came back sound, with fix 5's 11.1% figure reproducing
to the row. Fix 2 is sound for every path this deployment can reach.

Two fixes closed their reproducer but not their class, and both are now finished:

| Was | Now |
|---|---|
| Fix 1 re-pointed `restatement_suspect` at the content-bearing snapshot but left the FIRST/CHANGED classification keyed to `prev`, so a fetch failure as the **first** record for a URL still fired the false restatement alert, with the two signals disagreeing | `_annotate` takes `content` and classifies FIRST from it |
| Fix 3 corrected the `observed_at` stamp but not the comparison: `ingest_canonical` still diffed against whatever was newest, so `--all-snapshots` fabricated a reversed revision plus a re-revision and grew `revisions/` without bound on every pass | The diff is filtered to `observed_at <= observed_ts`, making the comparison genuinely bitemporal. A replay is now a true no-op, verified three passes deep on real data |

Four further findings, all fixed:

- **The blind-detector alert could not fire at the year rollover.** It required
  `prev_outcome == deduped`, but a year that churned all through 2025 has
  `prev_outcome == changed` on the January morning it becomes frozen-in-window. The
  rollover is the most likely moment for CFTC's window to shift, which made it the worst
  possible blind spot. Now keys on whether the previous fetch saw bytes at all.
- **A fetch failure was a tripwire condition.** Connectivity is not provenance: a dropped
  connection says nothing about whether CFTC restated anything, and routing it here turned
  one blip into a frozen-year restatement alarm. Failures are now counted and printed by
  `fetch`, where an operational problem belongs.
- **A run where every snapshot failed to parse exited 0**, reporting success to Task
  Scheduler while the store gained nothing, with `failed` being terminal. Now non-zero, in
  a single combined message: an earlier draft raised on the failure first and silently
  swallowed a restatement suspect in the same run, which is the more serious of the two.
  **`ingest --retry`** is the way back, and it is the only one: nothing else ever
  wrote `parse_status` back to `pending`, and two separate defects had stranded whole
  backlogs there.
- **Disaggregated and TFF were fetched from 2006**, but cftc.gov serves 404 for
  `fut_disagg_txt_2006..2009` and `fut_fin_txt_2006..2009` (verified live in both the
  review and here). Every `fetch --all` recorded eight permanent failure snapshots that
  could never succeed. First year corrected to 2010.
- **The null band was vacuous for Legacy**, because `canonicalize_legacy` never coerced. A
  value arriving as `"200,000"` stayed an object column, passed every check, and hashed
  differently from the numeric form: precisely the fabricated-revision failure the band
  exists to prevent, on the one report type where it could not see it. Legacy now coerces
  like the other two. **Proved not to move any stored hash**: across 95 markets and 448,236
  canonical rows the real values are already `int64`, so the coercion is an identity.

One caveat carried forward from the review rather than fixed: fix 5's headline 11.1% is
measured over all 418 markets in the 2026 file. Over the 51 markets in the current-state
store the flat-week rate is 0.07%. Both numbers are honest; they describe different
populations, and the tracked universe is much less affected than the full CFTC file.

### Known and accepted

`ingest_canonical` reloads every observation and builds a per-key dict with `iterrows()`
on each call, about 2.3 seconds against a 31k-row store. Fine for the daily path (three
snapshots), but a cold-start `fetch --all` plus `ingest` is roughly 120 snapshots against a
store growing into the millions of rows, so a full historical rebuild is hours rather than
minutes. Not on the daily path, so not fixed here.

## 10. The `announced` tier, built (2026-08-02)

Criterion 5 is met, and the §9 gap note is closed. The reasoning there was sound: a release
date extracted by inference from prose is worse than an honest `derived` one, because it
carries a provenance flag claiming it was announced. What went unchecked was whether the
target was prose at all.

**It is not.** When a disruption moves publication, CFTC publishes a table:

| COT Report Date | Original Publish Date | New Publish Date |
|---|---|---|
| 09/30/2025 | 10/03/2025 | 11/19/2025+ |
| 10/07/2025 | 10/10/2025 | 11/21/2025 |

So `parse_announced_release_dates` reads **tables** and refuses **prose**, which keeps the
original objection intact rather than overriding it. Of roughly 100 announcements on the
page back to 2008, the large majority are prose (holiday shifts, reporting-firm
corrections, a National Day of Mourning closure). None of those yield an exact pair without
inference, so none are read, and their weeks stay on `scheduled` or `derived`.

Measured against the live store on 2026-08-02: **36,296 observation rows move from
`derived` to `announced`**, and the `scheduled` count is unchanged, so nothing correct is
displaced. That is the whole Oct-Dec 2025 backlog, §6's "single largest PIT hole". The
worst individual week is report date 2025-09-30, which `derived` places at 2025-10-03 and
which actually published on 2025-11-19: wrong by 47 days, and wrong in the direction that
claims data existed before the appropriations lapse that stopped it existing.

### Two traps, both found by measuring rather than by reading

**A table is a PLAN, not a set of per-week corrections.** The page carried two overlapping
tables: 2025-11-18, a slow catch-up running to 2026-01-23, and 2025-12-09 ("CFTC to
Accelerate Publication of Backlogged COT Data"), a faster one finishing 2025-12-29. Each
ends with a row marked "COT publication returns to normal schedule", which is a claim about
every week after it too. Merging row-wise, the obvious implementation, keeps the four weeks
past the newer plan's end alive from the superseded one, and three of those four disagree
with CFTC's own published 2026 calendar by a week: 2025-12-30 would be recorded as released
2026-01-13 where the calendar says 2026-01-05. Since `announced` outranks `scheduled`,
those rows would have overwritten correct dates. Caught by diffing the parse against
`release_schedule.parquet` before wiring it up. Only the newest table is taken; weeks it
does not cover fall back to `scheduled`, which loses precision rather than asserting a
false fact.

**Header matching is load-bearing, not defensive.** The page carries five tables and only
two are release dates. The others are a contract-rename table (Contract / Exchange / Old
Name / New Name) and two market lists. "Parse the tables" would file a contract rename as a
publication date.

Parsing is also row-wise rather than a flat cell list grouped into threes, because the
2025-11-18 table puts its `+` footnote in a cell of its own, which desynchronises the
grouping and silently transposes every later row's dates.

### The corpus was never a corpus

Found while looking for the prose the extractor was supposed to read. `_parse_announcements`
scraped every `<li>` in the whole document, so all 95 stored rows were site navigation:
menu entries, footer links and market-name list items ("Contact Us", "Privacy Policy",
"CBT Corn (CFTC ID 002602)"), with `announcement_date` null on every one. The store
reported 95 announcements and held none.

It is now scoped to the content region and keyed on the `Month D, YYYY:` headings, which is
both what the announcements actually are and what carries the date. The 95 legacy rows stay
in the store, since the append-and-dedupe write never drops a row and losing data is the
worse failure here. They remain distinguishable by their null `announcement_date`.

## Bottom line

Discovery and the spike are done. The genuine null on historical recovery holds: no
past vintages exist to recover, from git or from CFTC, so the vintage series starts
from this session's first capture. The one materially useful thing against *existing*
history is the release-date backfill (schedule + announcements), which the spike
confirmed cannot be shortcut via `Last-Modified` and must come from the two published
sources — most valuably for the Oct–Dec 2025 backlog weeks.
