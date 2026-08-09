# Handoff: ADR-0007 step 2, the `marketdata` futures provider is written

**Status:** **STEP 1 OF §7 SHIPPED AND VERIFIED ON THE WINDOWS BOX (2026-08-09, §7).**
Contract specs (§7.2) came with it. Steps §7.3–§7.5
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

In `marketdata`, all green (119 tests, ruff clean) and verified against a real Norgate
run — see §7:

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
| `bars.get_bars(volume=)` | front / reconstructed volume. Added after the Windows run — the producer wrote the columns and nothing served them (§7) |
| `scripts/verify_against_cotdata.py` | the port-verification harness, with its comparison logic unit tested |
| `provenance` / `pin` | made tier-aware (see §4) |

`get_prices(symbol, adjustment=...)` becomes `get_bars(symbol, adjustment)`. Tier names
are unchanged, so a repoint is an import change and not a semantic one.

## 3. The both-tiers rule is enforced in three places

The work order's §4 warned that shipping a `backadj`-only futures producer would break
`crowdmon` with a `raise` inside `riskunits` on a weekly scheduled job. **That consumer is
gone — see §5a — and the rule still holds, on `npf`'s evidence rather than `crowdmon`'s.**
Three guards, so it cannot be lost by a later edit to any one of them:

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

## 5a. `crowdmon` was deprecated three days after the work order was written

Checked because `cotdata`'s `CLAUDE.md` mentioned it in passing. It is not a passing
matter: **the work order's §4 — the section it says it exists to record — rests entirely
on `crowdmon`, and `crowdmon` is now inert.**

`crowdmon/DEPRECATED.md`, decided **2026-08-07**, one day after the work order's own
`crowdmon` companion document. Four pre-registered tests, no positive result, and the §10
validation came back uninformative with the hand-identified clean episodes **spent**. The
repo is frozen, not deleted, with three stated conditions for revisiting.

Its §3 was resolved **2026-08-08**, the day this provider was written:

- both launchd jobs (`crowdmon-publish`, `crowdmon-live-tests`) unloaded, plists deleted
- `cot-analyzer`'s `/damage` page removed in that repo's PR #22, with its artifact reader
- the one open work order closed unstarted
- **"This package now has no consumers at all; nothing in `npf` or `livebook` ever
  imported it."** `~/code/crowdmon_store` is written by nothing and read by nothing

### The requirement survives. Its justification has to move

The both-tiers rule is **not** weakened by this, and §3 above should not be relaxed. But
it can no longer be argued from `crowdmon`, and it does not need to be — a live consumer
makes the same case harder.

`npf/books/treasury_seasonal.py` (npf pushed 2026-08-06, no deprecation) sets
`RETURN_TIER = "propadj"` and records why, amended 2026-07-26 after its first run came
back void:

> Norgate's `backadj` is ADDITIVE: roll gaps accumulate into the level, so the series is
> not a price and can cross zero. On the verdict window ZB's back-adjusted close runs
> −12.24 to 48.36 and is negative on 454 days, while the contract actually traded 72.66 to
> 112.19. A percent return needs a positive denominator, and **15 of ZB's 100
> verdict-window trades had their SIGN INVERTED.**

And, independently of `propadj`, the same file needs both stored frames anyway:

> Roll DETECTION still reads backadj and unadj, because their difference IS the
> accumulated adjustment and is precisely what steps at a roll.

Fifteen sign-inverted trades in a live book is a sharper argument than a `raise` inside a
weekly job in a package that no longer runs. **Anchor §4 on `npf`.**

### What this does to §7.3

The work order says: *"Repoint `crowdmon`'s ten call sites. It is the smallest consumer
and the one whose tier requirements are strictest, so it fails loudest. **Do it first, not
last.**"*

That instruction is now void. Repointing a frozen package with zero consumers is pure
waste, and `DEPRECATED.md` §2 asks for the opposite — it wants the live pins *neutralised*
because "a frozen repo should not have tests that depend on data collected after it was
frozen". Repointing would add a dependency on data collected after the freeze.

**Do not repoint `crowdmon`.** The ten call sites leave §7.3 entirely, and with them the
step that was supposed to de-risk everything after it. What remains:

| consumer | needs | status |
|---|---|---|
| `crowdmon` | both tiers + `propadj` | **dropped — deprecated, no consumers** |
| `npf` | both tiers + `propadj` (live book) | ADR-0007 **defers** this, with `livebook` |
| `cotmetrics` / `cot-analyzer` | `backadj` only | the only repoint actually left |

So the "hard one first" ordering has evaporated, and what is left of §7.3–§7.4 is the
`backadj`-only repoint the work order calls "therefore easy". That is a genuine
simplification and also a genuine loss: the strictest consumer was the one that would have
proved the provider correct by failing loudly, and nothing else exercises `propadj` until
the deferred `npf` pass runs. **The Windows-box comparison in §7 is now the main evidence
that this port preserved the numbers**, which is an argument for running it sooner.

Whether `npf`'s deferral still makes sense given it is now the *only* `propadj` consumer
is ADR-0007's call, not this handoff's. Flagging it because the deferral was decided when
it was one of two.

## 6. Scope left out, deliberately

- **§7.3 repoint `crowdmon`** — **do not do this**, see §5a. **§7.4 `cotmetrics` /
  `cot-analyzer`** is not started and is now the only repoint left standing.

  The rest of this bullet is retained because it documents a coupling the work order's
  call-site table does not capture, and the same pattern may appear elsewhere. It was found
  while checking the `MME`/`MFS` question, before §5a established that `crowdmon` should not
  be repointed at all. `ContractMaster.load()` does not only *call* `cotdata`; it parses
  the shape of `cotdata`'s manifest:

  ```python
  for name in load_manifest().get("prices", {}):
      sym, _, adj = str(name).rpartition("_")     # "ES_backadj" -> ("ES", "backadj")
  ```

  `marketdata`'s entries live under `"bars"` and read `futures/norgate/ES_backadj`, so
  `rpartition("_")` yields `futures/norgate/ES` and matches no registry symbol. Every
  symbol would go non-joinable. It fails loudly — `test_every_registry_symbol_but_the_
  uncovered_ones_joins` breaks — but swapping the import is not sufficient, and the
  eleventh coupling is a manifest *format* rather than a function call.
- **§7.5 delete from `cotdata`.** Not started, and it must not be until the repointing
  lands. `cotdata`'s price code is still every consumer's only working path.
- **`MME` / `MFS`.** Norgate carries no continuous series for either, so `cotdata` prices
  them off the EEM and EFA ETF proxies through yfinance. Serving them in `marketdata`
  needs a futures-domain path in the yfinance provider, which is separate work from the
  Norgate producer. They are **absent** from the futures registry rather than present and
  unserviceable.

  **Checked against `crowdmon`, and this costs it nothing.** Both already fail
  `contract_master.coverage()`, which requires a spec plus both stored tiers: they carry
  `norgate: null`, so they report `missing: specs,unadj_price,backadj_price` and
  `joinable: False` today. `tests/test_contract_master_live.py` pins exactly that
  (`not_joinable <= {"MFS", "MME"}`), the 2026-08-04 spec inventory measured
  `joinable-but-unseen []` and `seen-but-unjoinable []`, and both are `Role: heldout` in
  the deployed `params.yaml`. `futures/roll.py:110` names them directly as the ETF proxies
  with no Delivery Month, which raises rather than returning a wrong answer. The counts
  agree: `crowdmon` reports 49 of 51 joinable and the ported futures registry holds 49.
  After §7.3 the rows still appear — symbols keep coming from `cotdata`'s registry, since
  COT identity stays here — and still read non-joinable, so the live test's assertion
  survives the repoint unchanged.
- **Step 3 (`livebook`).** Still out, still a live book.

## 7. RUN AGAINST REAL NORGATE, 2026-08-09 — identical

**This section said "not yet run" when it was written. It has now run, on the Windows
producer, and the port is verified.** `marketdata/scripts/verify_against_cotdata.py`
against a `cotdata` store built by the original producer:

| symbol | rows per tier | passthrough | reconstruction |
|---|---:|---|---|
| ES | 7,279 | identical | identical |
| CL | 10,887 | identical | identical |
| GC | 12,156 | identical | identical |
| ZS | 12,271 | identical | identical |
| DC | 7,299 | identical | identical |

49,892 rows per tier, both tiers, plus contract specs for all five, exit 0. **Exact
equality, not a tolerance**: both producers drive the same Norgate install through two
code paths, so a difference would have been a port bug rather than vendor
disagreement. Symbols span an index, an energy, a metal and the two markets whose
`backadj` history goes non-positive.

That is what §7.5 needs before `cotdata`'s price code is deleted, and it was obtainable
only while both halves still exist. **It is now on the record, so the deletion no longer
has to wait on it.**

### What the real box found that the offline suite could not

Two defects, on the first two contacts, and neither was in the data:

1. **`--domain futures` stopped at the import guard.** The provider was ported without
   its dependency: `cotdata` declares `norgate = ["norgatedata"]` and `marketdata` had
   no such extra, so nothing installed it. The guard behaved correctly and its message
   was wrong for the one machine that matters, sending the Windows producer to
   `--domain equities`.
2. **`get_bars` had no `volume=` parameter.** The producer half of volume
   reconstruction was ported and the consumer half was not, so the columns were written
   and nothing served them. `npf`'s `ml/labels.py:50` passes `volume=` through, so a
   repointed call would have raised `TypeError`.

Neither is visible to a suite that cannot install the vendor or call a parameter that
does not exist. **The lesson for §7.4 and §7.5: offline green says nothing about the
producer box**, and each repoint should be exercised there before it is called done.

The second was found only because the harness was changed to report the columns it had
**not** compared. It had been printing reconstruction columns solely when they differed,
so "compared and identical" and "never compared" rendered as the same silence and a PASS
could not be distinguished from a PASS that skipped half the frame.

### One expectation this corrected

The reconstruction columns were expected to drift, because each producer reconstructs
incrementally over its own store's history and `marketdata`'s was fresh where `cotdata`'s
had accumulated over months. They agree exactly. Norgate's historical individual-contract
volumes are immutable and the algorithm is identical, so the incremental path converges
on what a full recompute produces — which makes the harness's `--strict-volume` usable
rather than theoretical.
