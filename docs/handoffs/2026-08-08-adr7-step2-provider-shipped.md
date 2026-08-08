# Handoff: ADR-0007 step 2, the `marketdata` futures provider is written

**Status:** **STEP 1 OF §7 SHIPPED.** Contract specs (§7.2) came with it. Steps §7.3–§7.5
— repoint `crowdmon`, repoint `cotmetrics`/`cot-analyzer`, delete from `cotdata` — are
**not started**
**Date:** 2026-08-08
**Lives at:** `cotdata/docs/handoffs/2026-08-08-adr7-step2-provider-shipped.md`
**Executes:** [crucible-stack ADR-0007](https://github.com/mspinola/crucible-stack/blob/main/docs/adr/ADR-0007-cotdata-is-cot-only-bars-live-in-marketdata.md),
accepted 2026-07-27, via the work order in
`docs/handoffs/2026-08-04-adr7-step2-price-producer-split.md`
**Code landed in:** `mspinola/marketdata`. **Nothing in `cotdata` changed** — its price
code still works and every consumer still reads it. That is deliberate: deletion is §7.5,
and it comes after the repointing, not before

---

## 0. What this adds to the 2026-08-04 work order

One finding, and it is structural rather than a detail.

The work order said step 2 is *write the Norgate futures provider into `marketdata`*
rather than *move a file across a seam*, and priced that difference honestly. It did not
anticipate that **`marketdata`'s store could not hold the result**, and that the fix is a
schema change rather than a provider.

## 1. The finding: one stored frame per symbol was an equities assumption

`marketdata`'s store path was `bars/<domain>/<source>/<symbol>.parquet`. One file per
symbol per vendor, and every adjustment tier derived on read. Its `docs/design.md` states
that posture plainly and credits it to cotdata's `propadj`.

That design rests on a property nobody had written down because equities never violate
it: **corporate actions are dated events the vendor hands over with the bars.** One
stored frame plus `Dividends` and `Stock Splits` reconstructs any tier.

Norgate's back-adjustment is not that. It is roll splicing the vendor performed, and the
stitched calendar spread at each roll appears in no other series it publishes. `backadj`
cannot be derived from `unadj`, or the reverse. The work order's §4 says the same thing
from the consumer side — `crowdmon` needs both stored tiers because `propadj` is derived
from the pair — but read as a statement about *what the producer must fetch*. It is also
a statement about **what the store must be able to hold**, and on 2026-08-04 it could not:
both tiers resolved to the same path and the second write would have silently replaced
the first.

So the store grew a stored-tier component, `<symbol>_<tier>.parquet`, used by futures and
absent for equities. `MARKETDATA_STORE` schema v1 → **v2**. An existing equity store is
extended, not migrated: the equity path is byte-identical and reads through the same code.

**Measured before writing anything.** On v1, every futures read raised:

```
>>> get_bars("ES", "backadj", domain="futures", source="norgate")
ValueError: tier must be one of ('split', 'raw', 'total'), got 'backadj'
```

`check_tier` accepted the tier for the futures domain and `adjust()` then rejected it.
The declared `DOMAIN_TIERS["futures"]` entry, which ADR-0007 notes was added so error
messages would be right from day one, was a promise about error messages and not a
working path. Worth stating because "the futures domain is already declared" reads like
partial progress, and the honest measurement is that the consumer path was 0% built.

## 2. What shipped

In `marketdata`, all green (89 tests, ruff clean):

| Piece | Note |
|---|---|
| `providers/norgate.py` | the provider. Both tiers, volume reconstruction, the data-driven finals gate, NDU-down guard, contract specs |
| store tier axis | `config.bars_path`, `store.{write,read,has}_bars(tier=)`, `sources_for(tier=)`. Schema v2 |
| `adjust.ratio_adjust` | `propadj`, ported from `cotdata.prices._ratio_adjust` as a pure two-frame function |
| `adjust.STORED_TIERS` / `DERIVED_TIERS` | which tiers a producer owes against which are computed. A test asserts they partition each domain |
| `bars.get_bars` futures path | stored tiers read through; `propadj` derived; half-stored raises |
| `store.{write,read,upsert}_metadata` | contract specs. The scoped-run upsert is ported too — specs share one table |
| `registry.yaml` | 49 futures symbols |
| `update.py` | `--domain`, `--metadata`, `--full` |
| `provenance` / `pin` | made tier-aware (see §4) |

`get_prices(symbol, adjustment=...)` becomes `get_bars(symbol, adjustment)`. Tier names
are unchanged, so a repoint is an import change and not a semantic one.

## 3. The both-tiers rule is enforced in three places

The work order's §4 warned that shipping a `backadj`-only futures producer would break
`crowdmon` with a `raise` inside `riskunits` on a weekly scheduled job. Three guards, so
the rule cannot be lost by a later edit to any one of them:

1. **Producer.** Both tiers are fetched and reconstructed before either is written, so a
   failure on the second leaves nothing on disk rather than a half-written symbol.
2. **Consumer.** `propadj` against one stored tier raises and names the missing one.
   Returning empty would read as "no data for this symbol" when the truth is "the
   producer half-finished".
3. **Test.** Parametrised over both halves, asserting the message names the absent tier.

Loudness is the whole point, and the reason is §4's: additive back-adjusted percent
volatility is ~200x too high for soybeans and **0.47x for gold**, and 0.47x never goes
negative and passes every implausibility screen a spot check would apply.

## 4. Two things the port broke on the way through, both fixed

Recorded because both were silent, and neither is in the work order.

**`provenance()` and `--pin` assumed one series per symbol.** `provenance("ES")` returned
`None` for a futures symbol, and an unscoped `marketdata-update --pin` derived its symbol
list from manifest keys, so futures arrived as the "symbol" `ES_backadj` and then failed
lookup. Both are now tier-aware. Snapshot format v1 → **v2**: an entry is one stored
series, so `--symbols ES` pins `ES_backadj` *and* `ES_unadj`. Pinning one would leave
`propadj` half covered, and a study quoting a volatility figure would verify green against
a store that had moved under it. v1 snapshots still verify — their keys are plain symbols
and an absent `tier` field reads as the domain's default.

**`marketdata-update --check` ragged every row.** Its symbol column was a fixed 10
characters, and a futures entry is `futures/norgate/ES_backadj`. Width now comes from the
data.

## 5. Answers to the work order's §5, as implemented

**5.1 Does `propadj` stay derived-on-read?** **Yes, implemented that way.** The work order
recommended it on the voided month-end Treasury verdict, and §1 above is now an
independent second reason: it is the only futures tier that *can* be derived, so deriving
it is also the only thing that keeps the store's stated posture true anywhere in this
domain.

**5.2 Do the two store roots converge?** **Not decided here, and this ships without
needing it.** `MARKETDATA_STORE` stays a separate root with a separate manifest. The
work order is right that three roots is the moment to decide — but that decision binds
launchers and a launchd agent in `crowdmon`, so it belongs with §7.3, not ahead of it.
Nothing here forecloses it.

**5.3 Is a synced store permanent?** **Yes, and the code now says so rather than
implying it.** `norgatedata` drives a local Norgate Data Updater install and NDU is
Windows-only, so no other machine can produce this half at any Python version.
`--bars` therefore skips futures with a message on a non-Windows box instead of failing
the whole run, and `--domain futures` explains why rather than raising
`ModuleNotFoundError`. Worth writing into ADR-0007's open questions as resolved.

## 6. Scope left out, deliberately

- **§7.3 repoint `crowdmon`** (6 modules, 10 call sites) and **§7.4 `cotmetrics` /
  `cot-analyzer`**. Not started. The work order says do `crowdmon` first because its tier
  requirements are strictest and it fails loudest, and that ordering still holds.
- **§7.5 delete from `cotdata`.** Not started, and it must not be until the repointing
  lands. `cotdata`'s price code is still every consumer's only working path.
- **`MME` / `MFS`.** Norgate carries no continuous series for either, so `cotdata` prices
  them off the EEM and EFA ETF proxies through yfinance. Serving them in `marketdata`
  needs a futures-domain path in the yfinance provider, which is separate work from the
  Norgate producer. They are **absent** from the futures registry rather than present and
  unserviceable. A consumer repointed at `marketdata` loses them until that is built —
  worth confirming against `crowdmon`'s universe before §7.3.
- **Step 3 (`livebook`).** Still out, still a live book.

## 7. Not yet run against real Norgate

Every test is offline. The provider has not executed against a live NDU, because this
work happened on Linux and §5.3 is exactly the reason it could not. The pure logic —
`propadj` derivation, the finals-gate cores, the roll-gap check, the tier/store
plumbing — is covered by tests; the `norgatedata` call sites are ported from code that
has run in production in `cotdata` for months, but they are unexercised **here**.

**First action for whoever picks this up on the Windows box:**

```
marketdata-update --bars --domain futures --symbols ES
marketdata-update --check
```

then compare `ES_backadj` against `cotdata`'s existing `ES_backadj` for the same dates.
Both producers can run side by side — separate roots, separate manifests, nothing
deleted — so that comparison is available until §7.5, and it is the cheapest possible
check that the port preserved the numbers.
