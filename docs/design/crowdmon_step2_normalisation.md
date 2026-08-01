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

### Coverage: not a constraint. Every deployed market is joinable

**Corrected 2026-07-30.** An earlier draft of this section made "42 of 95 markets, 44%" a
headline finding. That was measured against the Legacy store only, and it framed the
denominator as the full CFTC market list, which step 2 does not aim at. Measured against
the deployed universe instead:

| Set | Count |
|---|---|
| `cotmetrics-config/params.yaml` universe | 47 (**42 `Role: deploy`**, 5 `Role: heldout`) |
| Joinable to contract specs **and** an unadjusted price, matching COT in **any** report | **45 of 47** |
| Of the 42 `deploy` markets | **42 of 42** |
| Not joinable | `MME`, `MFS` (MSCI Emerging Markets, MSCI EAFE) |

So the ceiling is not binding on anything anyone actually trades. Both failures are
`Role: heldout`, which params.yaml defines as "never in deploy selection", and both fail
for the same simple reason: Norgate carries neither contract specs nor prices for them.

Two traps in these numbers worth naming, because both are easy to fall into:

- **The two 42s are not the same fact.** "42 markets joinable against the Legacy store"
  and "42 markets with `Role: deploy`" happen to be the same number *and* the same set,
  but for unrelated reasons. Three symbols (`EMD`, `KE`, `NKD`) have full specs and price
  coverage and are absent from the joinable set only because they have no **Legacy** COT
  table at all; they are Disaggregated and TFF markets. Since the disagg and TFF
  canonicalisers landed they are joinable, which is what moves the total from 42 to 45.
- **The registry's `report_type` is a preference, not an existence claim.** It declares 41
  disagg and 8 TFF and zero legacy, yet all 42 `deploy` markets do have a Legacy table.
  Legacy covers everything back to 1986; `report_type` records which report cotmetrics
  prefers to read, not which reports exist for that market.

*(Aside, outside this document's scope: the workspace `CLAUDE.md` says params.yaml holds
"47 across 9 asset classes (7 heldout)". The file has 5 heldout, not 7.)*

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
corrupts the entire history a backtest is evaluated over.

> **Corrected 2026-08-01**, by a test failing against real data. An earlier version of this
> paragraph said crude's back-adjusted series "reaches -27.52, which is not a price",
> attributing it to additive back-adjustment accumulating roll gaps below zero over
> decades. The number is right, the explanation was wrong, and the real one is a better
> argument.
>
> Both series bottom in **April 2020**. What happens is that the single enormous roll gap
> out of the May 2020 contract, which settled at **-37.63**, is propagated backwards
> through every earlier bar. The sharpest row in the store is **2020-04-21**: crude traded
> at **+11.57**, a perfectly ordinary positive price, while the back-adjusted bar for the
> same day reads **-27.52**. Crude was genuinely below zero on **exactly one day**; the
> back-adjusted series is below zero on **64**.
>
> A second claim in the same vein was also wrong: the unadjusted series *can* be negative,
> because 2020-04-20 really happened. So a negative price is not by itself evidence of the
> wrong series, and normalisation code must not clip or reject one. On that day a LONG
> position genuinely had negative notional. What identifies the artifact is that it reports
> a negative price on days the market was positive.

Meanwhile volatility must come from the **back-adjusted** series, because that is the one
with correct returns; unadjusted returns carry fake roll gaps.

> **Nothing in the stack currently ships this error** (verified 2026-07-30). Every one of
> cotmetrics' three price reads is `backadj`, but none of them multiplies a historical
> price by a quantity: two consume returns or bar shape, where back-adjusted is correct,
> and the third reads only the latest close, where the two series are equal by
> construction. Step 2 is the first thing in the stack that needs a *level* in currency,
> so this is a trap to avoid rather than a bug to go and fix. See "What cotmetrics does and
> does not give us" below.

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

## What cotmetrics does and does not give us

Checked directly against `~/code/trading_workspace/cotmetrics` on 2026-07-30, because
"there is probably nothing to reuse" is the kind of assumption that costs a rewrite if it
is wrong in either direction.

### Nothing to reuse for notional, and the reason is structural

`point_value`, `Point Value`, `contract_specs` and `read_metadata` return **zero hits** in
cotmetrics. The only `multiplier` hits are `sigma_multiplier`, a z-score threshold in
`conditions.py`, which is unrelated. So the assumption holds: the COT Index is unitless,
and cotmetrics never converts contracts to currency.

The single exception is not an exception. `options_data.calculate_intrinsic_curve` computes
a "notional intrinsic value" for **equity ETF option chains** using a hardcoded
`* 100` shares-per-contract, for max-pain plots on proxy ETFs. It is an equity options
multiplier, not a futures contract multiplier, and there is nothing in it step 2 can use.

**Consequence: step 2 writes the notional path fresh.** There is no tested prior art to
inherit, and equally no existing consumer that would need fixing.

### It never resolves a market code, so there is no resolver to reuse

`cftc_code`, `CotSymbolCodeMap` and `contract_market_code` also return **zero hits**, and
cotmetrics never imports `cotdata.registry`. ADR-0007's observation #3 is confirmed against
the code, not just remembered.

What it does instead is the interesting part: its entire cotdata surface is three
functions, `get_cot`, `get_prices` and `schema_version`, each taking the **internal symbol**
(`GC`, `ES`) and letting cotdata resolve the market code privately. cotmetrics never holds
a CFTC market code at all. Its only symbol table, `market_data._SYMBOL_TO_NAME`, is a
params.yaml-derived symbol-to-display-name map for chart labels.

That is why there is nothing to reuse, and the direction of travel is the reason:
**cotmetrics starts from a symbol; crowdmon's `contract_master` starts from a market code**,
because the canonical vintage rows are keyed by `market_code`. Going code to symbol needs
`cotdata.registry`, which is exported in `__all__` and which crowdmon would become the
first consumer of. Nothing in cotmetrics can help with a lookup it never performs.

### Every price read really is `backadj`, and the error is not shipping

Three production call sites, all explicit:

| Call site | Adjustment | What it does with the series |
|---|---|---|
| `signals.py:1028` | `"backadj"` | wick-rejection scores, shape-based |
| `CotIndexer.py:655` | `'backadj'` | OHLC for the index and charts |
| `options_data.py:366` | `"backadj"` | `Close.iloc[-1]` only, for a live price |

(Plus two `monkeypatch.setattr` stubs in `tests/test_signals.py`, which set no adjustment.)

ADR-0007's load-bearing claim is therefore true of the code as it stands, not only of the
ADR. And a second, stronger result falls out of reading what each one *does* with the
series: **none of them multiplies a historical price by a quantity.** Two consume returns
or bar shape, where back-adjusted is the correct choice; the third reads only the most
recent close, where back-adjusted and unadjusted are equal by construction. So the +294%
error described above is **not currently shipping anywhere**, and there is nothing to go
back and fix.

The advice not to copy cotmetrics' price access stands, but for a narrower reason than
"it uses the wrong series". It uses the right series for what it does. Step 2 is simply
the first thing in the stack that needs a *level* in currency rather than a return, and
`unadj` is a different call that no existing code makes.

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
| 3. net notional USD | Point Value + unadjusted price | available for all 42 `deploy` markets |
| 4. vol-scaled notional | σ from back-adjusted returns | available for all 42 `deploy` markets. The default for every cross-market comparison |

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
3. ~~**Accept the 42-of-95 coverage ceiling, or widen the registry first?**~~ **Withdrawn.**
   Measured, and there is no decision here: all 42 `deploy` markets are joinable, and the
   only two failures are held-out markets Norgate does not cover. Sourcing prices for the
   53 Legacy codes outside the registry would be a `marketdata` project, and nothing in
   the module spec asks for it.

## Bottom line

**Updated 2026-07-30 after the canonicalisers landed and cotmetrics was checked.**

Step 2 is the first build step that crosses the seam between COT and prices, so it belongs
in a new consumer package rather than in `cotdata`. That is still the recommendation and it
is the only open question of substance.

Everything else got better on measurement. The prerequisite is cleared: Managed Money and
Leveraged Funds are readable point-in-time. Coverage turned out not to be a constraint at
all, since all 42 `deploy` markets join cleanly and the only two failures are held-out
markets nobody trades. The inputs are all present and all in USD, so there is no FX layer.

There is nothing to inherit from cotmetrics, and that is a clean answer rather than a
disappointing one: it has no notional path because the COT Index is unitless, and no market
code resolver because it never holds a market code, passing internal symbols to `get_cot`
and `get_prices` and letting cotdata resolve privately. Step 2 writes both fresh, and
becomes the first consumer of `cotdata.registry`.

The one real trap remains. Notional must come from the unadjusted series and volatility
from the back-adjusted one, and getting that backwards is wrong by nearly 300% in 2002
while being exactly right today. Nothing currently ships that error, so it is a thing to
avoid rather than a thing to repair, and it should be pinned by a test rather than a
comment.
