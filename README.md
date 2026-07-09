# cotdata

Canonical data layer for the COT / futures-strategy stack. It exists so that
**cot-analyzer** and **pardo_quant_framework** never fetch data themselves — they
read a shared, file-based store through a stable API. This decouples the two
apps from each other and from any single data vendor.

```
        PRODUCER  (runs where each source is reachable)
   Windows: Norgate export        anywhere: CFTC COT download
                       │  writes   │
                       ▼           ▼
        ┌───────────────────────────────────────┐
        │  CANONICAL STORE   ($COTDATA_STORE)    │
        │  prices/*.parquet  cot/*.parquet       │   ← synced (rsync / Dropbox / S3)
        │  manifest.json     (the contract)      │
        └───────────────────────────────────────┘
                       ▲           ▲   reads (offline, cross-platform)
        ┌──────────────┴───┐  ┌────┴─────────────┐
        │   cot-analyzer   │  │  pardo_quant_fwk │      both:  import cotdata
        └──────────────────┘  └──────────────────┘
```

## The contract

The **store is the API boundary** — not Python imports. Producers write Parquet +
`manifest.json`; consumers only read. Nobody touches a vendor SDK at app runtime.
Swapping a vendor is a producer-only change.

- `prices/{symbol}_{adjustment}.parquet` — Open/High/Low/Close/Volume/Open Interest,
  tz-naive `Date` index. `adjustment` ∈ {`backadj`, `unadj`}. Close = exchange settlement.
- `cot/{code}.parquet` — weekly CFTC positioning.
- `manifest.json` — per-table `last_date`, `n_rows`, `source`, `updated_at`, `schema_version`.

## Consumer (Mac / anywhere)

```python
import cotdata
df = cotdata.get_prices("ES", adjustment="backadj")   # signals + stops
sz = cotdata.get_prices("ES", adjustment="unadj")      # position sizing / point value
cot = cotdata.get_cot("ES")
```
Set `COTDATA_STORE` to the synced store directory.

## Producer (run on the machine that can reach the source)

```
COTDATA_STORE=/store  cotdata-update --prices --symbols ES NQ    # Norgate (Windows)
COTDATA_STORE=/store  cotdata-update --cot                        # CFTC (cross-platform)
```
Schedule nightly (prices, after the Norgate Data Updater) and weekly (COT, Fri).

## Design rules

- **backadj for signals/stops** (settlement close, gap-free arithmetic rolls,
  shape-preserving); **unadj only** for absolute price / point-value sizing.
- One **symbol registry** (`cotdata.registry`) maps internal ↔ Norgate ↔ CFTC code
  ↔ asset class ↔ positioning lookback. No scattered maps.
- Databento is a **dormant provider** (`providers/databento.py`) — kept for the
  intraday news-failure work and settlement cross-checks, not the live EOD path.
- Don't build a multi-provider plugin framework; the thin `providers/base.py` seam
  is enough for a single active source.
