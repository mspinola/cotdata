# Plan — Promote reconstructed volume to default (versioned, deliberate)

## Why this is its own step

The additive change (`c8dfa02`) added `Volume_Reconstructed`, `Volume_Source`,
`First/SecondVolume`, `First/SecondContract` to the store **without touching the
canonical `Volume`/`Open Interest` columns**. That step is intentionally inert
downstream: the new columns are dropped before any consumer sees them.

There is exactly **one** live gate: `cotdata/prices.py` → `_COLS` whitelist,
returns `df[keep]`. Every live price/volume read in both consumers goes through
`cotdata.get_prices`. (`cot-analyzer/src/core/norgate_reader.py` has its own
`_PIPELINE_COLS` whitelist and a duplicate `roll_dates`, but it has **zero live
callers** — it is dead code and should be deleted, not taught the new columns.)

"Promote to default" is the opposite kind of move: it changes the number that
existing consumers already read. Folding it into the additive commit would be a
**silent semantic change** — the failure mode we called out in the V1 review.
The CotIndexer cache-staleness guards key on **column presence, not value**, so
swapping what `Volume` means under the same name would not invalidate a single
cache. Stale volume-derived features (Build Ratio `|ΔOI|/volume`, Speculation
Ratio `volume/OI`) would persist with no signal that anything moved.

So: promotion is a separate, versioned release with an explicit cache bust and a
downstream note. This document is the runbook for that release.

## Current state (facts to build on)

- `config.SCHEMA_VERSION = 1`. `_touch_manifest` stamps it into `manifest.json`
  on every write. **No consumer branches on it** — `load_manifest()` exposes it
  but nothing validates it. A version bump is inert until something reads it.
- Reconstructed columns exist in both `*_backadj.parquet` and `*_unadj.parquet`
  for every symbol that has individual-contract history; `Volume_Source` marks
  `reconstructed` vs `raw` fallback per row.
- **Actual volume surface is thin** (audited across both consumers):
  - cot-analyzer: `CotIndexer.py:405` reads `Volume` only as an OI-missing
    fallback (5% of volume proxy flow + a flow cap). No Build/Speculation Ratio
    is actually computed. Fetches via `cotdata.get_prices`.
  - pardo: `Volume` is in `xgboost_grinder` `exclude_cols` (deliberately **not**
    an ML feature); used by the zipline adapter (needs a column named `volume`,
    indifferent to raw vs reconstructed) and a news-outlier diagnostic. PIT
    feature builders don't touch volume. Fetches via `cotdata.get_prices`.
- **Neither consumer needs the reconstructed number physically in the `Volume`
  column** → open question #1 is resolved: **1B** (keep raw, expose an intent
  view). See "Consumer updates" below.

## Goal

Make reconstructed volume the value that volume-derived features consume, as an
explicit `schema_version 1 → 2` release: caches rebuild, the change is visible in
the manifest and a changelog note, and rollback is a one-line revert.

## Guardrails

1. **Never overwrite the meaning of `Volume` in place.** Keep raw front-month
   `Volume` as-is (audit trail, sizing, `Volume_Source='raw'` reconciliation).
   Promotion = flip *which column the pipeline reads*, not mutate `Volume`.
2. **The version bump must be load-bearing** before it ships — a consumer has to
   read `schema_version` and change behavior, or bumping it is theater.
3. **Cache key must include `schema_version`** so this promotion and every future
   one auto-invalidate instead of relying on humans to remember.
4. One PR per repo, cotdata first (producer/API), cot-analyzer second (consumer),
   never interleaved.

## Step 1 — Promotion surface — RESOLVED: 1B (intent view)

Audited both consumers (see "Current state"): **neither requires the reconstructed
value physically in the `Volume` column**, so 1A is not forced.

- **1A — swap the `Volume` column's contents** to reconstructed. Maximum
  blast radius, hardest to audit (raw number gone from the default view). Rejected.
- **1B — keep both columns; cotdata owns an intent view.** cotdata exposes
  `Volume_Reconstructed` + `Volume_Source`, plus a `get_prices(..., volume=...)`
  parameter that populates the returned `Volume` column with the chosen semantics
  (`"front"` = raw, default; `"reconstructed"` = reconstructed-with-per-row-raw-
  fallback). Consumers express *intent*; the fallback logic lives once, in the
  data layer, not re-derived at every call site. Raw is never mutated → reversible.
  **Chosen.**

### Data-layer ownership (do these in cotdata, not downstream)

The staged rollout surfaced logic that was about to be pushed into consumers:

1. **Volume source-selection is a cotdata concern.** Without the `volume=` view,
   every consumer would re-implement `row.get('Volume_Reconstructed', Volume)`
   fallback (CotIndexer:405, zipline adapter, news diagnostic, …). Own it once.
2. **Schema/version is a cotdata contract.** Add `cotdata.schema_version()` and
   `require_schema(min_version)` so consumers key caches on a real token instead
   of cot-analyzer's column-presence heuristic. (Optionally a per-symbol
   `data_version` from the manifest's `last_date`/`n_rows`/`updated_at`.)
3. **Delete the dead `norgate_reader.py`** in cot-analyzer (zero live callers) —
   a drifted second copy of read/normalize/whitelist/`roll_dates`. Remove it so
   there is a single schema-aware reader, rather than teaching it the new columns.

## Step 2 — Version bump + make it load-bearing (cotdata PR)

1. `config.SCHEMA_VERSION = 2`.
2. Add a short migration note constant/table describing what v2 means:
   "prices carry reconstructed volume; consumers may read `Volume_Reconstructed`
   / `Volume_Source`."
3. **Backfill correctly.** `_touch_manifest` writes the *current* `SCHEMA_VERSION`
   on any write, so a partial producer run would stamp `2` onto a store whose
   other parquet files are still v1-shaped. Either (a) run a full producer pass
   so every prices entry is rewritten under v2 before the bump lands, or (b) move
   the version stamp so per-entry schema is tracked, not just a global int.
   Prefer (a) for this release — it's one `norgate.update()` over all symbols.
4. Expose the columns + intent view through the API (`prices.py`): add
   `Volume_Reconstructed` and `Volume_Source` to `_COLS`; add the
   `volume="front"|"reconstructed"` param (default `"front"` = today's behavior,
   so the API addition is itself non-breaking). Add `schema_version()` /
   `require_schema(min_version=2)` helpers (export from `cotdata.__init__`).
5. Tests: `get_prices` returns the new columns; `volume="reconstructed"` puts the
   reconstructed value in `Volume` and preserves `Volume_Source`; default still
   returns raw front-month; a v1 manifest raises/warns via `require_schema`;
   reconciliation (`scripts/reconcile_volume.py`) still green.

## Step 3 — Consumer updates

### cot-analyzer PR
The two cache layers (startup `try_load_from_cache`, freshness at
`CotIndexer.py:606`) key on column presence, so they will NOT self-invalidate on
a value change. Wire the version in explicitly:

1. Switch the flow engine to reconstructed volume: `CotIndexer.py:374` fetch
   becomes `cotdata.get_prices(symbol, adjustment='backadj', volume='reconstructed', start=...)`.
   The OI-fallback read at `:405` then transparently uses reconstructed volume.
2. Read `cotdata.schema_version()` (or `require_schema`) at cache-build time and
   **fold it into the cache key / cache-file name** — forces a rebuild on this
   promotion and every future one, automatically.
3. One-time: bust on-disk cache artifacts built under v1 (delete/rename cache dir
   or a `--rebuild` flag) so the first post-deploy run recomputes from
   reconstructed volume.
4. **Delete `src/core/norgate_reader.py`** (dead — zero live callers). Confirm no
   imports break.
5. Regression check: recompute affected outputs before/after on a couple of
   symbols; deltas should localize to roll windows / OI-missing rows, not
   everywhere — a diff everywhere means something else moved.

### pardo PR — capability-only, zero behaviour change
pardo is **indifferent** to the promotion: its read shapes (OHLC + front volume)
are unchanged by the additive+opt-in v2, so it must not be forced onto
reconstructed volume and must not be gated.

1. **No ML change** — `Volume` is already in `xgboost_grinder` `exclude_cols`;
   PIT feature builders don't use volume. (Extended `exclude_cols` with the
   reconstruction column names as a defensive leak-guard.)
2. **Do NOT** hard-`require_schema(2)` at the data-loader — pardo reads unchanged
   shapes; a gate would block the honest-null pipeline against a change it doesn't
   consume.
3. **Do NOT** silently switch the zipline backtest or `news_failure_trigger`
   diagnostic to reconstructed volume — zipline's volume-based slippage/commission
   models would change results, and the news outlier would flag different days.
   Both stay on front volume.
4. Capability only: `fetch_daily_ohlc(..., volume=...)` now passes through to
   `get_prices`, default `'front'`. pardo *can* opt a specific call into
   reconstructed volume later, deliberately, without a silent global change.

## Step 4 — Note it to downstream

- cotdata `README.md` schema table already documents the columns; add a
  **CHANGELOG / schema-version note**: "v2 — reconstructed volume promoted;
  consumers should read `Volume_Reconstructed` and honor `Volume_Source`."
- One message to downstream owners: what changed, which features rebuild, how to
  force a rebuild (`schema_version` now in the cache key), and the rollback.
- Update `docs/keenan_coverage_map.md` / any analysis notes that assumed raw
  front-month volume in Build Ratio, since that input has changed.

## Step 5 — Rollout, verify, rollback

- **Order:** cotdata PR (Step 2) → full producer pass → cot-analyzer PR (Step 3)
  → pardo PR. Never ship a consumer flip before the store is uniformly v2.
- **Verify:** manifest shows `schema_version: 2` for all prices entries;
  `get_prices` returns the columns; indexer rebuilds on first run; feature deltas
  localized to roll windows; `Volume_Source` distribution sane (mostly
  `reconstructed`, `raw` only for crypto/ICE-soft symbols with no individual
  contracts).
- **Rollback:** revert the consumer flip (features read `Volume` again) — raw
  column was never mutated, so this is clean. `SCHEMA_VERSION` can stay at 2
  (columns are additive); only the *consumption* is reverted.

## Open questions

1. ~~Does any consumer need reconstructed volume *in* the `Volume` column?~~
   **Resolved: no.** Both audited; 1B (intent view) chosen.
2. Per-entry schema tracking vs global int in the manifest — worth doing now
   (Step 2.3b) or defer? Global int + full-pass is fine for this release.
3. Should `unadj` volume be promoted too, or only `backadj`? Reconstruction is
   adjustment-independent (volume isn't adjusted), so both carry the columns; the
   live consumers all read `adjustment='backadj'`, so `backadj` is the one that
   matters — `unadj` can stay reconstructed-in-store but no consumer flips to it.
