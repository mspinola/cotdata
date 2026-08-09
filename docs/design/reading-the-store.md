# Reading the store: universe, coverage, continuity and price tiers

**Living document.** Four things a consumer of this store needs before it computes anything,
each of which has already cost a session that reasoned from a plausible-looking column or a
plausible-looking count. None of them is about report comparability, which has its own
document: [`cross-report-comparability.md`](cross-report-comparability.md).

**Scope note (ADR-0007 step 2).** §1–§3 are about this store. §4 and §5 are about the BAR
store, which moved to [`crucible-marketdata`](https://pypi.org/project/crucible-marketdata/)
along with the providers that fill it. They stay here, updated to the new API, because they
are the reason this document exists: both are naming-and-composition traps that cost real
time, and a reader arriving from a COT question is exactly who walks into them.

---

## 1. The Disaggregated universe is mostly power and gas basis

Of the 279 markets in the Disaggregated report, **213 (76%) are ICE Futures Energy Division or
Nodal Exchange**: power hubs, gas basis, carbon allowances, renewable energy certificates. Not
classic outrights.

The consequence is specific rather than decorative. **Any cross-market statistic computed over
"all Disaggregated markets" is mostly a statement about ERCOT and PJM.** A principal component
over positioning changes across the full universe produces a PC1 describing power basis, not
the macro book, and anything downstream of it inherits that population.

State which universe a result is over, before computing it.

---

## 2. A coverage ratio whose denominator nobody chose is not a measurement

This has now been reported as a shortfall and then withdrawn **twice**, in two different
packages, for the same reason:

- "42 of 95 joinable, 44%", withdrawn once it turned out all 42 deployed markets joined.
- "25 of 279 markets", framed as a 9% sample and a limitation to be noted beside every result.
  It is not a sample. The other 254 are markets **nobody trades**: no liquidity, no access, or
  they are the power and gas basis contracts of §1. A join reaching every market in the
  tradeable universe is complete, not 9% complete.

The rule that generalises: **the denominator has to be a set someone chose.** "Every market
the CFTC publishes" is not a target any consumer has, so a ratio against it measures nothing.
Where a coverage figure is genuinely useful, derive it from the data at read time rather than
quoting a number from prose. Counts here move: covered markets have been 25, then 45, then 47
as specs were added, and every one of those figures was correct on the day it was written and
stale within a fortnight.

---

## 3. A market code is not an instrument, and a hole is two different things

A gap in a code's weekly series has two unrelated causes, and only one of them is a migration.
Telling them apart requires joining a code's **internal continuity** to its **siblings**,
which nothing does automatically.

Over 51 codes in the two current-state panels, **46 have a longest inter-week gap of 8 days**,
which is a holiday shift. The five that do not split into two causes.

**Cause one: a migration, where a sibling code fills the hole.** Russell 2000:

| code | venue | weeks | span | longest internal gap |
|---|---|---|---|---|
| `239742` | CME | 587 | 2006-06-13 to 2026-07-28 | **3,255 days, 8.9 years**, ending 2017-08-15 |
| `23977A` | ICE | 516 | 2008-07-22 to 2018-06-05 | none |

The two are **complementary, not redundant**: the ICE code covers the CME code's hole almost
exactly, and together they are a continuous twenty-year market. Read apart, the CME code looks
like it began in 2017. A downstream percentile with a three-year warm-up then does not reach
it until 2023, and anything scoring an event in that window scores it on the **retiring**
venue against a much shorter reference series than its peers.

Lumber is the clean contrast: `058643` hands off to `058644` in 2023 with a two-month overlap
and no holes on either side.

**Cause two: a thin market falling out of the report.** Oats (`004603`) has a **294-day
interval ending 2025-09-09** and five more over 50 days. A market below the reporting
threshold disappears and returns when it recovers. No sibling fills these, because nothing is
missing: the market genuinely was not reported.

**Consequences.** Any weekly differencing needs an explicit gap rule, or one delta spans the
whole absence and enters every ranking as the largest flow in the sample. Any per-code history
length is a statement about the code, not the market. And a merge across sibling codes must
precede any differencing, because the other order fails silently.

---

## 4. Volume: the fuller-sounding parameter is the narrower series

`marketdata.get_bars` takes `volume="front"` or `volume="reconstructed"`, documented as
"continuous front-month volume" and "true market volume (first + second expiring contract)".

**The second reads like the fuller series and is the narrower one.**
`Volume_Reconstructed = FirstVolume + SecondVolume`, exactly two expiries, while the plain
`Volume` field behind `front` spans the whole curve.

Two independent measurements establish that `front` is whole-market:

1. **Open interest matches the CFTC to the contract.** The price files carry an
   `Open Interest` column Norgate collects from the exchange; the CFTC collects its own from
   clearing members. Against COT total-market open interest for the same Tuesday: **exact
   agreement on 25 of 26 markets**, palladium at 0.998, median ratio **1.000**. Two vendors
   and two collection paths cannot agree that precisely unless both measure the whole market.
   Front-month data would be a fraction.
2. **Curve concentration orders exactly as contract structure predicts.**

This is recorded because the naming cost real time: several places across two repos stated
that whole-market volume was absent from the store, and all of them were describing a naming
problem as a data gap.

---

## 5. Price tiers, and who can produce a store that carries them

Three facts that were each written down separately and never composed:

1. **`propadj` is derived on read** from `unadj` + `backadj`. It is not a stored tier.
2. **Norgate is the only vendor supplying all tiers, and it is Windows-only by mechanism
   rather than by licence**: `norgatedata` talks to a locally installed Norgate Data Updater
   application rather than to an API (`marketdata/providers/norgate.py`, moved there by
   ADR-0007 step 2 §7.1).
3. **databento owes exactly one series per symbol**, `backadj`, because crucible-stack
   ADR-0007 scopes it to the Linux dashboard's needs and says its coverage "should not be
   broadened toward parity with Norgate".

**Composed: a databento-backed futures store cannot produce `propadj` at all**, so any
consumer needing correct percentage returns cannot be served by one. It is a **tier fact, not
an operating system fact**, a distinction that has been got wrong more than once:

**Fact 3 is now out of date, and the constraint with it.** ADR-0007 scoped databento to one
`backadj` series per symbol for the Linux dashboard. When it was ported into `marketdata`
(2026-08-09) it came across as a full futures provider writing BOTH stored tiers, because
that package enforces both-tiers-or-neither on every producer — `propadj` derives from the
pair, and `get_bars` raises rather than returning empty when only one is present. So a
databento-backed store now produces `propadj` like any other. The §5 table below is kept as
written because the OS/tier distinction it draws is still the point.

| box | Python >= 3.10 | Norgate | can produce a store carrying all tiers |
|---|---|---|---|
| Windows | yes | **yes** | **yes** |
| macOS | yes | no | no |
| Linux server | no, runs 3.9 | no | no |

The reason a wrong tier is a `raise` and not a warning is that the error survives a spot check:
`backadj` percentage volatility is 201x too high for soybeans and **0.47x for gold**, which
never goes negative and passes every implausibility screen.

---

## Provenance

Every section here was measured in `crowdmon` and is restated because `cotdata` owns the facts
and `crowdmon` is deprecated. Restated, never moved: those files are point-in-time records
under that repo's doc lifecycle and are not edited by the harvest.

| section | source |
|---|---|
| §1 | `crowdmon/docs/design/amendments-2026-08-01.md` §A5 |
| §2 | `crowdmon/docs/design/amendments-2026-08-01.md` §A14, and §C12 of the 08-03 file for the moving count |
| §3 | `crowdmon/docs/design/amendments-2026-08-02.md` §B26, §B27, §B30; oats from §A2 |
| §4 | `crowdmon/docs/design/amendments-2026-08-01.md` §A13 |
| §5 | `crowdmon/docs/design/amendments-2026-08-04.md` §D14 |

`crowdmon/docs/HARVEST.md` is the full map of what was ported, what was already resolved
upstream, and what was parked with the hypothesis.

**One deliberate omission.** §5's source composes its argument partly through
`crowdmon.futures.contract_master.ContractMaster.coverage()`, and that API goes with the
package. Nothing here points at it; the three facts above stand on `cotdata`'s own providers
and on ADR-0007.
