# Step 2: contract master and normalisation: PROPOSAL

**Status: proposed, not accepted.** Written before any step-2 code, per the request to
propose the approach first. Module spec: [crowdmon_futures_cot_module.md](crowdmon_futures_cot_module.md)
§5.1, §5.2, §13 step 2. Everything measured below was measured against the real store on
2026-07-30, and several measurements change what step 2 can be.

---

## Headline: step 2 should not be built in `cotdata`

Step 1 (vintage) belonged here, and [ADR-0008](https://github.com/mspinola/crucible-stack/blob/main/docs/adr/ADR-0008-cot-vintage-provenance-in-parquet.md)
argued why: it is CFTC-positioning provenance, it sits beside the fetch it records, and it
never crosses the instrument-domain axis [ADR-0007](https://github.com/mspinola/crucible-stack/blob/main/docs/adr/ADR-0007-cotdata-is-cot-only-bars-live-in-marketdata.md)
draws. Flow decomposition (§6.4, just built) is the same shape: COT in, COT out.

**Normalisation is not that shape. It is by definition a joiner**: net contracts times a
multiplier times a price, then scaled by a volatility estimated from returns. It needs COT
*and* bars *and* contract specs in one function.

ADR-0007's central measurement was that almost nothing joins the two domains, exactly two
files, which is what made the split cheap. A normalisation layer inside `cotdata` would
drag bars back in at precisely the moment ADR-0007 is pushing them out. Inside `marketdata`
it would drag COT in, and `marketdata` deliberately imports nothing from `cotdata`. The
only home that does not violate the seam is **the consumer**, which is the crowdmon package
the module spec already describes in §12 and which does not exist yet.

So step 2 begins by creating `crowdmon-futures` as a workspace sibling, with a boundary
test in the shape of `crucible-stack/tests/test_boundaries.py`: crowdmon may import
`cotdata` and `marketdata`; neither may import crowdmon.

This is a recommendation, not a fait accompli. The alternative is prototyping inside `npf`,
which is faster to start and worse to unpick later, since npf is the strategy repo and this
is infrastructure.

---

## What is actually available (measured 2026-07-30)

| Input | Present? | Detail |
|---|---|---|
| Multiplier | **yes** | `metadata/contract_specs.parquet`, `Point Value`, 47 rows |
| Tick size / value | yes | same table |
| Currency | **yes, and all 47 are USD** | so no FX conversion layer is needed at all |
| Exchange margin | yes | `Margin` column, so §8's margin sensitivity is buildable |
| Unadjusted prices | **yes** | `prices/<SYM>_unadj.parquet`, 47 symbols |
| Back-adjusted prices | yes | `prices/<SYM>_backadj.parquet`, 47 symbols |
| Roll calendar | **no** | not in the specs table, and there is no per-expiry source |
| First notice date | **no** | same |
| Daily price limits | **no** | spec §3 says "manually maintained", and nothing maintains it |

The all-USD result is a genuine simplification worth banking: §5.2 rung 3 says "net notional
USD", and for this universe that is a multiplication with no FX join.

### Coverage ceiling: 42 of 95 markets

| Set | Count |
|---|---|
| Legacy COT market codes in the store | 95 |
| Registry symbols | 49 |
| Registry symbols with contract specs | 47 |
| Registry symbols with unadjusted prices | 47 (`MME`, `MFS` have none) |
| **COT markets joinable to both specs and price** | **42** |

This is not a bug, it is the registry universe: `cotdata.registry` only names instruments
somebody wanted prices for. But it means **any "cross-market" result from step 2 onward
covers 44% of the CFTC market list**, and §7's PCA and trend-alignment panels inherit that.
State it in output rather than letting a reader assume 95.

---

## Three findings that change the plan

### 1. The categories the spec cares about are not in the report that is wired

The registry declares **41 disaggregated and 8 TFF symbols, and zero legacy**. That matches
the module spec: the positioning engine, the fragility weights (§6.3), the cross-market
panel (§7) and the CTA calibration (§9.2) all key on **Managed Money** and **Leveraged
Funds**, which exist only in the Disaggregated and TFF reports. Legacy's `noncommercial` is
the pre-2006 proxy for them, not the same category.

The vintage layer has a canonicaliser for **Legacy only**. Disagg and TFF are captured as
raw bytes and drain as `skipped`.

So: **a point-in-time Managed Money series does not exist today, and step 2 has nothing
correct to normalise.** This is the real step-2 prerequisite, and it is a `cotdata` change
rather than a crowdmon one. The change-only and revision machinery is already report-type
agnostic, the controlled vocabularies are already declared, and the raw bytes are already
retained, so this is writing two canonicalisers and their tests, not new architecture.

**Recommendation: do the disagg and TFF canonicalisers before step 2, in `cotdata`.**

> **Done, 2026-07-30.** Both canonicalisers shipped and were verified against the real
> first production capture: 39,235 Disaggregated and 12,500 TFF canonical rows for 2026,
> ingesting in about 5 seconds. `asof()` now returns Managed Money and Leveraged Funds
> point-in-time, with per-category spreading, per-category trader counts and CR4/CR8, none
> of which Legacy carries. Detail and the measured zero-sum results are in
> [cot_vintage.md](cot_vintage.md) §8. **This section's blocker is cleared**; the rest of
> the proposal stands as written.

### 2. Notional must use unadjusted prices, and getting it wrong is invisible today

§5.1 says the unadjusted series is "retained separately for notional and margin
calculations". That is right, and the size of the error is worth pinning down, because it
has a property that makes it dangerous:

| Market | Date | Back-adjusted close | Unadjusted close | Notional error |
|---|---|---|---|---|
| GC | 2002-05-30 | 1282.00 | 325.50 | **+294%** |
| CL | 2004-12-13 | 146.48 | 41.01 | **+257%** |
| ZC | 2002-04-10 | 587.50 | 199.75 | **+194%** |
| GC | 2026-07-30 | 4100.10 | 4100.10 | +0.0% |

**The error is exactly zero at the present date and grows monotonically backwards**, because
back-adjustment anchors on the most recent contract. So a notional computed from the
back-adjusted series passes every spot check anyone would actually run, and silently
corrupts the entire history a backtest is evaluated over. Crude's back-adjusted series even
reaches **-27.52**, which is not a price.

Meanwhile volatility must come from the **back-adjusted** series, because that is the one
with correct returns; unadjusted returns carry fake roll gaps.

So `net_notional × σ_daily` draws its two factors from two different price series. That is
not an implementation detail to discover in review, it is the central correctness fact of
step 2, and it should be pinned by a test that fails if either leg is swapped.

### 3. The COT side now has point-in-time discipline; the price side does not

`livebook/docs/OPERATIONS.md` records that Norgate back-adjusted series **restate history on
every roll**. `marketdata` captures no vintages and has no `asof`.

So a release-date-indexed, vol-scaled notional series is point-in-time on its COT leg and
current-state on its price leg. That asymmetry is not fixable inside step 2, and it is not
a reason to stop, but it should be stated in output (a `pit_complete: False` flag or
equivalent) rather than left for someone to infer. §5.3's warning about `derived` release
dates has the same character: a provenance flag is worth more than a clean-looking number.

---

## Proposed build

```
crowdmon_futures/
  ingest/cot_adapter.py       CotSource over cotdata.vintage_ingest.asof; release_date
                              indexed, `derived` excludable, zero_sum_check on every load
  normalize/
    contract_master.py        registry x contract_specs, and a COVERAGE REPORT as its
                              first-class output rather than a silent inner join
    notional.py               net_contracts x Point Value x UNADJUSTED close
    riskunits.py              x sigma_daily from BACK-ADJUSTED returns
  tests/
    test_boundaries.py        crowdmon imports cotdata/marketdata; neither imports crowdmon
    test_price_series_split.py   fails if notional uses backadj or sigma uses unadj
```

### The normalisation ladder, rung by rung

| Rung (spec §5.2) | Additional input | Status |
|---|---|---|
| 1. net contracts | COT only | available. Spec says do not report it, and it is right |
| 2. net / open interest | COT only | available, but the denominator is missing spreading (see below) |
| 3. net notional USD | Point Value + unadjusted price | available, 42 markets |
| 4. vol-scaled notional | σ from back-adjusted returns | available, 42 markets. The default for every cross-market comparison |

Rung 2 carries a defect found while building §6.4: `NonComm_Positions_Spread_All` is not
captured by `providers/cftc.py`, so open interest includes spreading contracts the numerator
cannot see (gold: 8% of OI). Rung 2 is therefore biased low by a market-and-time-varying
amount. Rungs 3 and 4 are unaffected, since they never divide by OI. **Either fix the
capture first or do not use rung 2**, and the module spec's preference for rung 4 as the
default makes the second option cheap.

### Explicitly out of step 2, and why

- **Roll calendar, first notice date, roll congestion (§8).** No per-expiry price source
  exists in the stack and none is being built (workspace CLAUDE.md, ADR-0007 step 2 on ice).
  Blocked on data, not code.
- **Daily price limits (§8).** Would be a hand-maintained table. Worth doing eventually;
  not on step 2's critical path.
- **Seasonal adjustment (§5.4).** The spec puts it at step 7. It is tempting to pull
  forward because ag commercial z-scores are visibly seasonal, but it is a modelling choice
  and step 2 is meant to be units.

---

## Recommended sequencing

| # | Work | Where | Blocking? |
|---|---|---|---|
| 0 | ~~Disaggregated + TFF vintage canonicalisers~~ | `cotdata` | **done 2026-07-30** |
| 1 | `crowdmon-futures` skeleton + boundary test | new sibling | yes |
| 2 | `contract_master.py`, coverage report as its first output | crowdmon | |
| 3 | `notional.py`, with the unadj/backadj split pinned by test | crowdmon | |
| 4 | `riskunits.py` (vol-scaled) | crowdmon | |
| 5 | Fix spreading capture, if rung 2 is wanted | `cotdata` | no, rung 4 does not need it |

## Three decisions this needs before code

1. **Create `crowdmon-futures` as a sibling now, or prototype inside `npf`?** Recommend the
   sibling, on the ADR-0007 seam argument above.
2. **Do the disagg/TFF canonicalisers first?** Recommend yes. Without them step 2 normalises
   Legacy `noncommercial`, which is not the category any downstream engine wants.
3. **Accept the 42-of-95 coverage ceiling, or widen the registry first?** Recommend accept
   and report it, since widening it means sourcing prices for 53 more markets and that is a
   `marketdata` project, not a crowdmon one.

## Bottom line

Step 2 is the first build step that crosses the seam between COT and prices, so it belongs
in a new consumer package rather than in `cotdata`. Most of what it needs is already in the
store and in USD, which is better than expected. Two things are not: the Disaggregated and
TFF reports have no vintage canonicaliser, so the Managed Money and Leveraged Funds series
the whole system is built around cannot yet be read point-in-time, and that is a genuine
prerequisite rather than a detail. And the single correctness trap, using back-adjusted
prices for notional, is one whose error is exactly zero today and reaches nearly 300% in
2002, so it will not be caught by looking at the output.
