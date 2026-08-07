# Cross-report comparability: what carries between the four reports, and what does not

**Living document.** It exists because the same class of error has now been made, measured and
recorded three separate times, in three different repos, against three different pairs of
reports. The pattern is always the same: two reports describe the same market and the same
week, a category in one looks like a category in the other, and the difference or ratio
between them is computed. **The result is not interpretable, and nothing in the data says so.**

`cotdata` serves all four reports, so this is its fact to hold. Consumers that computed one of
these quantities did not do anything careless; they read two columns with plausible names.

---

## 1. The rule, before the cases

> **Two categories from two different reports may only be compared when both reports partition
> open interest the same way AND count spreading the same way. In practice that is almost
> never, and the exceptions are enumerated below rather than inferred.**

Three things can differ independently, and each alone is enough to break a comparison:

1. **The basis.** Futures-only against futures-and-options-combined.
2. **The partition.** Which traders land in which bucket.
3. **The spreading convention.** Which categories have spreading broken out, and which have it
   netted into long and short.

---

## 2. Legacy against TFF: exactly two quantities carry

Measured over **6,279 overlapping market-weeks**.

| quantity | agreement |
|---|---|
| `open_interest` | **100.0000%**, maximum difference **0** |
| `nonreportable` long | **100.0000%** |
| `nonreportable` short | **100.0000%** |
| TFF category sum == Legacy category sum | 34.6% |
| `dealer` == `commercial` | 15.3% long, 14.5% short |
| `asset_manager + leveraged + other_reportable` == `noncommercial` | 15.6% long, 15.8% short |

So the two reports describe the same pool of contracts and agree exactly on where the
reportable line falls. **Above that line the obvious mapping fails about 85% of the time**, for
two compounding reasons.

**Spreading is counted differently.** Legacy breaks spreading out only for non-commercial
traders and nets commercial spreading into long and short; TFF breaks it out for every
category. Canadian dollar, 2026-07-28, open interest 372,447:

```
TFF     long 348,849 + spread 23,598 = 372,447   residual 0
Legacy  long 362,728 + spread      0 = 362,728   residual 9,719
gap between the two long totals     = 13,879 = 23,598 - 9,719
```

Exact to the contract.

**The traders in each bucket are different people**, which is why no spreading correction
recovers the mapping. If Legacy's `noncommercial` held the same traders as TFF's buy side,
their spreading would match:

```
Legacy non-commercial spreading (derived)          =  9,719
TFF asset_manager + leveraged + other_reportable   = 15,278
```

### The consequence

**Any quantity built by subtracting one report's category from another's mixes a
classification difference with a spreading-convention difference, and neither is recoverable
from published data. The only quantities that carry across are open interest and
`nonreportable`.**

---

## 3. The `spread_contracts` trap, which is ours

**`canonicalize_legacy` sets `spread_contracts` to `NA` on every row.** Summing an all-null
column returns **0**, which prints as a measurement of zero spreading and is not one. The
9,719 in the worked example above is **derived as the identity residual, not read**.

Store-wide, the identity `long + spread == open_interest` closes on:

| report | closes |
|---|---|
| TFF | **99.984%** of market-weeks |
| Legacy | **19.857%** of market-weeks, median residual 912 |

That 19.857% is not a data quality problem. It is the spreading convention showing through a
column we populate with nulls. A caller that aggregates `spread_contracts` for Legacy and gets
0 has measured our null-handling, not the market.

---

## 4. Supplemental against everything else: a different basis

The Supplemental (Commodity Index Trader) report is **futures-and-options COMBINED**, where
Legacy, Disaggregated and TFF are all futures-only. Its `Open_Interest_All` is therefore a
different quantity for the same market and week: WHEAT-SRW on 2026-07-28 is **566,358
combined against 463,502 futures-only**.

**Nothing in the file says so.** It is the one report with no `FutOnly_or_Combined` column, so
`cotdata` asserts the flag rather than reading it, established by matching open interest
against both Legacy series 390/390 and 0/390.

**Its Index Traders does NOT nest inside Disaggregated's Swap Dealer.** The taxonomy is
Legacy, not Disaggregated, so the two cannot be differenced to isolate levered swap flow. The
index book is carved out of commercial, non-commercial **and non-commercial spreading**, three
buckets where CFTC's own prose names two.

**Coverage is 12 markets, then 13 from 2013**, when Soybean Meal entered. Both counts circulate
because both are right for part of the history, and six markets were renamed without changing
code. `cotdata-vintage coverage` derives the covered set from the data and prints every entry
and exit.

Detail: [`../analysis/2026-08-03-cit-supplemental-measurements.md`](../analysis/2026-08-03-cit-supplemental-measurements.md).

---

## 5. What this permits

The enumerated safe comparisons, so that a consumer does not have to infer them from the
prohibitions:

- **Open interest, Legacy against TFF**: identical, exactly, always. Safe.
- **`nonreportable`, Legacy against TFF**: identical, exactly, always. Safe.
- **Open interest, any futures-only report against the Supplemental**: **not** safe, different
  basis.
- **Anything above the reportable line, across any two reports**: not safe.
- **Within one report, across weeks**: safe, subject to the gap rule (§6).

---

## 6. A related trap that is not about reports but bites the same callers

**Gaps in a market's series come from thin markets falling out of the report**, not from data
loss. Oats (`004603`) has a **294-day interval ending 2025-09-09** and five more over 50 days:
a thin market drops below the reporting threshold and reappears when it recovers. Any weekly
differencing needs an explicit gap rule, or one delta spans the whole absence and enters every
ranking as the largest flow in the sample.

**The Oct-Nov 2025 shutdown is the opposite case and is often confused with it.** It left COT
**report** dates intact and broke only **release** dates, which are all `derived` in that
window. Anything indexed by report date is unaffected; anything indexed by release date in
that window is resting on a guess.

---

## Provenance

Sections 2, 3 and 6 were measured in `crowdmon` and are restated here because `cotdata` owns
the facts and crowdmon is deprecated. The original sections, with their reproducers and the
dates they were established, are:

- §2, §3: `crowdmon/docs/design/amendments-2026-08-04.md` §D7, reproducer
  `crowdmon/docs/analysis/reproduce_single_number.py::d7_legacy_and_tff_share_two_things`
- §6, gaps: `crowdmon/docs/design/amendments-2026-08-01.md` §A2
- §6, shutdown: `crowdmon/docs/design/amendments-2026-08-01.md` §A1

They are **restated rather than moved**: those files are point-in-time records under that
repo's doc lifecycle and are not edited by this harvest. See
`crowdmon/docs/HARVEST.md` for the full map of what was ported, what was already resolved, and
what was parked with the hypothesis.

Section 4 was authored here from the start and is summarised rather than moved.
