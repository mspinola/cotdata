# What the options book changes in a Legacy positioning read

**Date:** 2026-11-27. Point-in-time. Per the doc lifecycle, `analysis/` is never amended: a
later measurement that contradicts this goes in a new dated file.

**Reproducer:** `scripts/legacy_options_impact.py`, run against the live store
(`~/code/cotdata_store`, schema 2, newest report week 2026-11-17). It reads `cot_legacy/`
and `cot_legacy_combined/` through `cot.get_cot(name, "legacy")` and
`cot.get_cot(name, "legacy", combined=True)` for every registry symbol. 49 markets qualify
(both bases present, at least five years judged, a warm three-year window before 2016). BTC
and ETH are skipped as too short. Nothing here reads a price. Every table is descriptive and
no verdict is rendered on anything.

**Why this exists.** Since [ADR-0003](../adr/ADR-0003-combined-basis-is-in-scope.md) the
store holds Legacy on both bases, so the options book is available by subtraction within one
report: combined bucket minus futures-only bucket is that category's delta-weighted options
position. Every consumer still reads futures-only. The question a consumer needs answered
before switching, or before deciding not to, is how often the two bases would be *read
differently*, in which markets, and why.

**The index.** A positioning read here means the rolling min-max index,
`100 * (x - min) / (max - min)` over `lookback + 1` weekly rows, which is the formula of
`cotmetrics.indicators.rolling_cot_index`. It is restated in the script because cotdata sits
upstream of cotmetrics. Two inputs are measured, matching the two index families cotmetrics
builds: **raw net** (long minus short, contracts) and **normalized net** (net divided by
that basis's own open interest). Two lookbacks: 52 and 156 weeks. Judged from 2016-01-01,
568 weeks per market for all but two (ZO has 417, NKD 425).

**Strict disagreement.** A week counts when one basis sits at an extreme (index at or above
90, or at or below 10) while the other sits clearly inside the range (between 20 and 80). A
plain "extreme on one and not the other" count is dominated by boundary flicker, 91 against
89, and overstates the problem severalfold.

---

## 1. In most markets the options book changes nothing

Across 27,538 market-weeks the commercial read on the three-year normalized index disagrees
strictly on **147**, about half a percent. On the 52-week index it is 320, a little over one
percent.

- **30 of 49 markets have zero** strict weeks on the three-year normalized index.
- 19 have at least one, 9 have more than four, 6 have more than ten.
- The zero set is every currency and the dollar index, the refined products and natural
  gas, every equity index except ES, the Treasury curve except ZN and ZF, platinum, lumber,
  coffee, and the grains and oilseeds other than Chicago wheat and soybeans.

Options are simply small or absent in most of these. Median options share of combined open
interest is 13% or less for every currency, under 3% for RB, HO, NG, RTY and ZT, under half
a percent for YM, DX and 6M, and zero for EMD, NKD and the two ICE MSCI contracts.

One of those needs a caveat. Natural gas reads 1.4% (range 0.5% to 6.2%). NYMEX gas options
are a large market, so the likelier explanation is that the option book is reported under
CFTC codes the registry does not sum into NG. That is a guess, not a measurement. Do not read
NG's combined series as the whole options picture until it is checked.

## 2. The nine markets where it matters

Commercial net, three-year index on normalized net, 568 weeks each. Options share is the
options part of combined open interest. "Options net" is the commercial options position as
a percentage of the absolute commercial futures net, median, with its sign. "Same sign" is
the share of weeks the options net has the sign of the futures net.

| market | options share, median (range) | options net | same sign | median gap | 95th pct gap | strict weeks |
|---|---|---|---|---|---|---|
| GC, gold | 31% (19 to 57) | -3% | 70% | 4.9 | 19.1 | 25 |
| CC, cocoa | 17% (7 to 48) | +17% | 35% | 6.3 | 17.6 | 21 |
| HE, lean hogs | 24% (14 to 39) | -65% | 70% | 4.0 | 21.1 | 17 |
| ZW, Chicago wheat | 19% (9 to 34) | +17% | 77% | 4.1 | 18.0 | 13 |
| DC, Class III milk | 50% (37 to 61) | +8% | 63% | 6.0 | 21.5 | 12 |
| SB, sugar | 16% (9 to 27) | +2% | 45% | 2.0 | 12.3 | 12 |
| WBS, WTI crude (ICE) | 24% (13 to 35) | -23% | 69% | 4.2 | 18.0 | 8 |
| CL, WTI crude (NYMEX) | 24% (14 to 36) | -13% | 99% | 4.2 | 14.7 | 6 |
| CT, cotton | 18% (9 to 35) | -4% | 62% | 3.0 | 11.9 | 6 |

Four more have exactly 4 (ZS, GF, LE, ZN). SI has 3. OJ, ES and HG have 2. ZF and PA have 1.

Two things to read off this table.

**The typical week is close even here.** The median gap is 2 to 6 index points in every
market. The problem is confined to the tail: the 95th percentile gap is 12 to 22 points.
Options matter on a minority of weeks, and those weeks cluster (section 4).

**The size of the options position does not predict the damage.** Corn is not in the table.
Its commercial options net is +21% of the futures net, larger than wheat's, and it has zero
strict weeks. Live cattle carries -27% and has 4. A min-max index is unchanged by any
constant scale or offset, so an options position that is large and steady moves nothing. What
moves the read is options exposure, or options open interest, that *varies* relative to the
futures book. Gold is the limiting case: almost no options position and the most
disagreement.

## 3. Two mechanisms, and which market has which

Options can move the normalized index two ways. They change the **position** in the
numerator. They also change the **open interest** in the denominator, because combined open
interest includes the whole options book whoever holds it. Raw net has no denominator, so a
market that disagrees on normalized net and not on raw net is a denominator market.

Strict weeks of 568, commercial net.

| market | normalized, 156w | raw, 156w | normalized, 52w | raw, 52w | mechanism |
|---|---|---|---|---|---|
| GC | 25 | 0 | 39 | 9 | denominator |
| CC | 21 | 1 | 22 | 8 | denominator |
| SB | 12 | 2 | 3 | 0 | denominator |
| CL | 6 | 1 | 26 | 2 | denominator |
| CT | 6 | 0 | 11 | 4 | denominator |
| HE | 17 | 20 | 18 | 22 | position |
| ZW | 13 | 11 | 12 | 13 | position |
| DC | 12 | 26 | 25 | 20 | position |
| WBS | 8 | 14 | 11 | 19 | position |

**Denominator markets: gold, cocoa, sugar, NYMEX crude, cotton.** The commercial position is
essentially the same on both bases. On raw net the three-year index never disagrees strictly
in gold or cotton and does so once or twice in the others. The normalized read moves because
options open interest swells and shrinks, which rescales net-over-open-interest on the
combined basis and leaves the futures-only ratio alone. This is a property of dividing by
open interest. It matters for the normalized family, which is what the NPF research uses, and
hardly at all for a raw-net index.

**Position markets: lean hogs, Class III milk, Chicago wheat, ICE crude.** The raw index
disagrees about as often as the normalized one, sometimes more. Commercials in these markets
hold real exposure in options that the futures-only report does not carry. Lean hogs is the
clearest: the commercial options net is short, about 65% the size of the futures net, and
shares its sign 70% of weeks. A futures-only commercial read in hogs is missing a large part
of the hedge.

At 52 weeks the split blurs for a few markets on the margin. ZN shows 22 normalized and 23
raw, so on the short lookback the ten-year note is a position market. GF shows 15 and 9, LE
21 and 10, OJ 20 and 13. ZS shows 19 and 1, a denominator market at that horizon. The
three-year classification above is the stable one.

## 4. Gold in detail

Gold is the market with the most strict disagreement and the least options position, so it
shows the denominator mechanism cleanly.

**The commercial options position is small.** By year, the median commercial options net
runs from about +5,400 contracts (2026) to about -25,000 (2019 and 2020), against a futures
net between -114,000 and -290,000. On raw net the largest three-year index gap in the whole
period is 15.4 points and no week exceeds 20.

**Options open interest is not small, and it moves.**

| year | options share of combined OI, median | commercial options net, median | commercial futures net, median | weeks with a gap over 20 | mean gap on those weeks |
|---|---|---|---|---|---|
| 2016 | 30.6% | -22,986 | -228,500 | 0 | |
| 2017 | 31.1% | -4,177 | -172,654 | 0 | |
| 2018 | 31.4% | +1,771 | -114,124 | 0 | |
| 2019 | 35.0% | -24,913 | -277,912 | 1 | +20.5 |
| 2020 | 43.4% | -24,973 | -289,756 | 5 | +23.0 |
| 2021 | 25.5% | -8,396 | -231,348 | 0 | |
| 2022 | 23.5% | -9,929 | -172,077 | 9 | -24.7 |
| 2023 | 29.6% | -6,678 | -183,138 | 0 | |
| 2024 | 37.4% | -3,482 | -265,931 | 3 | +22.6 |
| 2025 | 38.1% | +4,593 | -260,829 | 7 | +26.7 |
| 2026 | 29.2% | +5,433 | -211,284 | 1 | -20.0 |

The gap is combined minus futures-only on the three-year normalized index. It exceeds 20
points on 26 of 568 weeks, and most of those weeks sit in the years the options share was
far from its usual level: high in 2020, 2024 and 2025 (combined reads higher, because a swollen
denominator shrinks a short ratio toward zero), low in 2022 (combined reads lower). The
13-week change in options share correlates **0.74** with the 13-week change in the gap.

So in gold the two bases agree on what commercials hold and disagree on how big that is
relative to the market, and the disagreement tracks how much option open interest is
outstanding. Neither is wrong. They are different normalizations, and the combined one
imports the option market's own cycle into a commercial positioning read.

**The last twelve report weeks** show the mechanism at small scale. Commercial futures net
went from -264,718 to -193,750, the options net stayed between +3,600 and +8,100, and the
options share drifted from 28% down to 24% and back.

| week | options share | futures net | options net | 156w index, futures-only | 156w index, combined | 52w index, futures-only | 52w index, combined |
|---|---|---|---|---|---|---|---|
| 2026-09-01 | 28.3% | -264,718 | +3,620 | 3.5 | 4.4 | 7.5 | 5.9 |
| 2026-09-08 | 29.4% | -270,274 | +5,904 | 0.0 | 3.6 | 0.0 | 4.8 |
| 2026-09-15 | 29.0% | -261,721 | +7,456 | 4.2 | 7.6 | 8.8 | 10.2 |
| 2026-09-22 | 27.7% | -262,903 | +6,676 | 4.5 | 5.1 | 9.6 | 6.9 |
| 2026-09-29 | 25.0% | -250,967 | +6,757 | 8.8 | 4.5 | 18.7 | 6.1 |
| 2026-10-06 | 24.9% | -244,527 | +6,926 | 8.9 | 4.6 | 18.8 | 6.2 |
| 2026-10-13 | 24.7% | -242,531 | +6,367 | 12.4 | 6.1 | 21.3 | 6.8 |
| 2026-10-20 | 24.4% | -235,582 | +7,019 | 23.9 | 14.6 | 33.7 | 14.6 |
| 2026-10-27 | 23.5% | -215,624 | +6,584 | 38.8 | 25.2 | 63.5 | 29.9 |
| 2026-11-03 | 25.8% | -204,902 | +8,078 | 49.0 | 40.1 | 80.2 | 47.7 |
| 2026-11-10 | 25.9% | -217,522 | +7,171 | 40.8 | 32.4 | 66.8 | 38.5 |
| 2026-11-17 | 28.3% | -193,750 | +7,382 | 62.0 | 55.4 | 100.0 | 65.9 |

On 2026-11-17 the 52-week normalized index reads 100 futures-only and 66 combined. That is a
strict disagreement this week, in a market where the two bases report nearly the same
commercial position.

## 5. The index formula matters more than the basis

The same gold comparison on a percentile-rank index (share of the prior 156 weeks strictly
below the current value) instead of min-max:

| index | median gap | 95th pct gap | largest gap | weeks with a gap over 20 |
|---|---|---|---|---|
| min-max, as cotmetrics computes it | 4.9 | 19.1 | 33.6 | 4.6% |
| percentile rank | 5.8 | 44.2 | 65.4 | 24.1% |

The medians are close. The tails are not: a rank index puts the two bases more than 20 points
apart on a quarter of weeks, five times as often as min-max. A rank moves fast when the
series crosses a dense part of its own history, so a small difference between two series
becomes a large difference in rank. On 2026-10-27 the rank read 51 futures-only against 14
combined while min-max read 39 against 25.

Any statement of the form "options shift the gold read by N points" is therefore a statement
about one index formula. A single week on a rank index is the least representative number
available.

## 6. The non-commercial side

Same counts for non-commercial net. The affected set is the same markets, with more weeks.

| market | normalized, 156w | raw, 156w | normalized, 52w | raw, 52w | options net, % of futures net |
|---|---|---|---|---|---|
| DC | 60 | 66 | 59 | 69 | +41% |
| GC | 37 | 1 | 41 | 12 | +3% |
| HE | 28 | 21 | 22 | 22 | +66% |
| ZW | 18 | 16 | 8 | 10 | -18% |
| CC | 16 | 2 | 21 | 12 | -19% |
| SB | 9 | 2 | 6 | 0 | -4% |
| ZS | 8 | 0 | 24 | 7 | -8% |
| WBS | 7 | 15 | 12 | 16 | +24% |

Totals over all 49 markets: 219 strict weeks on the three-year normalized index, 148 on raw.
The mechanism split holds: gold, cocoa, sugar and soybeans are denominator markets for
speculators too, and milk, hogs, wheat and ICE crude are position markets. Class III milk
stands apart. Half its combined open interest is options, and the non-commercial read
disagrees strictly on more than one week in ten on either input.

## 7. Where the two bases stand on 2026-11-17

Commercial net, normalized, futures-only against combined. Listed where they differ by more
than 10 points on either lookback.

| market | 156w | 52w |
|---|---|---|
| GC | 62.0 vs 55.4 | 100.0 vs 65.9 |
| CL | 89.6 vs 70.8 | 87.7 vs 62.5 |
| GF | 74.6 vs 58.0 | 96.3 vs 69.8 |
| DC | 89.6 vs 73.2 | 71.5 vs 58.9 |
| WBS | 79.5 vs 64.9 | 79.5 vs 73.0 |
| CC | 95.3 vs 100.0 | 84.1 vs 100.0 |
| ZC | 49.3 vs 54.4 | 70.9 vs 82.3 |
| ZN | 68.4 vs 62.4 | 70.5 vs 81.7 |

Two of these are strict disagreements this week on the 52-week index: gold and feeder cattle.

## 8. What this does and does not establish

Established, from the store, with the reproducer above:

- For 30 of 49 markets the options book never changes a three-year commercial read, and for
  40 of 49 it does so on four weeks or fewer in eleven years.
- Nine markets are affected more than that. Five through the open interest denominator
  (GC, CC, SB, CL, CT) and four through real commercial options exposure (HE, ZW, DC, WBS).
- In gold the commercial position is the same on both bases to within a three-year index gap
  of 15.4 points at worst, and the normalized disagreement follows the options share of open
  interest (0.74 on 13-week changes).
- The size of a market's options position does not predict how often its read changes.

Not established, and not attempted:

- **Which basis is the better read.** Nothing here looks at a price. Whether the combined
  series, the futures-only series, or the options-only difference predicts anything is a
  research question for npf under a pre-registration, not a data-layer measurement.
- **That the options-only difference is a clean flow series.** It is delta-weighted, so it
  changes when price moves and nobody trades. Combined reports also round option equivalents
  per category, so a bucket difference can be off by a contract.
- **Anything about who is inside "commercial".** Both bases use Legacy's taxonomy. In the 13
  Supplemental markets, eight of which are in section 2's table or the line under it, a fifth to two
  thirds of commercial longs are index money
  ([2026-10-31 decomposition](2026-10-31-cit-legacy-combined-decomposition.md)). Options do
  not fix composition.
- **Natural gas.** See section 1.

The thresholds (90 and 10 for an extreme, 20 to 80 for inside, 20 points for a large gap) are
choices, and the strict-week counts move with them. The script exposes them as constants.
The median and 95th-percentile gap columns use none of them.

## 9. Bottom line

The options book is irrelevant to the Legacy positioning read in most markets and matters in
nine. In four of the nine (hogs, milk, Chicago wheat, ICE crude) it matters because
commercials hold exposure in options that futures-only data leaves out. In the other five,
gold first among them, the position is the same and only the open-interest-normalized index
moves, because options open interest inflates and deflates the denominator. Even in the
affected markets the typical week differs by a few index points and the disagreement is
concentrated in episodes. These are measured facts about the data. Whether either basis is
the better signal is untested.
