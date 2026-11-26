# Cross-report comparability: what carries between the four reports, and what does not

**Living document.** It exists because the same class of error has now been made, measured and
recorded three separate times, in three different repos, against three different pairs of
reports. The pattern is always the same: two reports describe the same market and the same
week, a category in one looks like a category in the other, and the difference or ratio
between them is computed. **The result is not interpretable, and nothing in the data says so.**

The opposite error has been made too: generalising the Legacy-against-TFF finding to every pair
of reports. **Disaggregated against Legacy is the exception**: on the same basis the
Disaggregated groups are an exact split of the Legacy buckets (§3). A fourth recorded error,
in npf on 2026-11-25, was this one.

`cotdata` serves all four reports, so this is its fact to hold. Consumers that computed one of
these quantities did not do anything careless; they read two columns with plausible names.

---

## 1. The rule, before the cases

> **Two categories from two different reports may only be compared when both reports partition
> open interest the same way AND count spreading the same way. Across most pairs that is
> never, and the exceptions are enumerated below rather than inferred. The one pair where the
> partition nests is Disaggregated inside Legacy (§3).**

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

**Any quantity built by subtracting a TFF category from a Legacy one (or the reverse) mixes a
classification difference with a spreading-convention difference, and neither is recoverable
from published data. The only quantities that carry between those two reports are open
interest and `nonreportable`.** This is a fact about Legacy and TFF, not about every pair:
§3 is the counter-case.

---

## 3. Disaggregated against Legacy: an exact split, to the contract

The opposite of §2. **On the same basis, the Disaggregated report re-partitions each Legacy
bucket and never moves a contract across the Legacy commercial line:**

```
Prod_Merc long  + Swap long  + Swap spread           = Legacy Comm long
Prod_Merc short + Swap short + Swap spread           = Legacy Comm short
M_Money long    + Other_Rept long                    = Legacy NonComm long
M_Money short   + Other_Rept short                   = Legacy NonComm short
M_Money spread  + Other_Rept spread                  = Legacy NonComm spread
Open_Interest_All, NonRept long, NonRept short       identical
```

Swap spreading appears on BOTH commercial sides because Legacy nets commercial spreading into
long and short (the convention of §2), so a swap dealer's spread position is a long and a short
there. Managed money and other reportable keep their spreading broken out, as Legacy
non-commercial does.

Measured 2026-11-25 on the futures-only series (`get_cot(sym, "disagg")` against
`get_cot(sym, "legacy")`, the defaults), every registry symbol with a Disaggregated series:
**28 markets, 2006-06-13 to 2026-11-17, 29,562 shared market-weeks.** (The npf universe's 23
commodity markets are among them; the other five are DC, WBS, KE, ZO, ZR.)

| result | markets |
|---|---|
| every identity above holds on **100.00%** of weeks, maximum residual **0** | 27 |
| RB: **98.97%**, 11 failing weeks, 2006-06-13 to 2006-08-22 | 1 |

**The RB weeks are a stitching effect, not a report fact.** Open interest disagrees on the same
11 weeks, by up to 43k contracts. RBOB (`111659`) was listed in 2006 beside the unleaded
gasoline contract it replaced (`111652`, an RB `hist_codes` entry). Legacy's history holds both
codes, so on overlap weeks `get_cot` keeps the larger open interest, which until 2006-08-29 is
the old contract. The Disaggregated store has no `111652` file, so it reads RBOB alone. Against
the primary code's own Legacy file (`store.read_cot_legacy("RB_111659")`, no stitch) every
identity holds on all 11 weeks. The fact to carry: **a stitched Legacy series and an
unstitched Disaggregated series can be different contracts on hand-off weeks**, so check
open interest before trusting a cross-report identity on a market with `hist_codes`.

Three things to know before using it:

- **Non-commercial spreading is derived, not read.** The store's Legacy table has no
  non-commercial spreading column (§4), so the Legacy side of the spreading identity is the
  closure residual `Open_Interest_All - Comm long - NonComm long - NonRept long`. That it
  closes exactly against the Disaggregated spreading columns is itself the evidence that the
  residual is the spreading.
- **Trader counts do not add.** A trader is counted once in a Legacy bucket but can appear in
  several Disaggregated categories (swap long and swap spread, say). `Traders_Prod_Merc_Long + Traders_Swap_Long == Traders_Comm_Long` holds on 0%
  of weeks in CL, NG, GC, HG, SI and WBS and at most 61% (OJ) anywhere. Use positions.
- **Combined basis: exact only to option-delta rounding.** On `cot_disagg_combined/` against
  `cot_legacy_combined/` open interest and non-reportable still agree on every week, but the
  bucket identities close exactly on only 13% to 64% of weeks, because each report rounds its
  delta-weighted option equivalents separately. The residual is never more than **2
  contracts** (LBR 8, which is 2 at the 4.0 `hist_codes` scale of its predecessor; RB aside
  for the stitch above). Run the reproducer with `--combined`.

### The consequence

Disaggregated groups can be read as a decomposition of Legacy: producer/merchant plus swap
dealer IS the commercial bucket, and managed money plus other reportable IS the non-commercial
bucket, the same traders on the same week. **Legacy bucket minus its Disaggregated components
is zero, not a residual to interpret**, and a share such as "managed money as a fraction of
Legacy non-commercial" is a genuine within-bucket share. What does not follow: that a
Disaggregated group behaves like the Legacy bucket containing it. Producers alone and swap
dealers alone are different series: in every grain and livestock market, swap dealers are net
long on at least 86% of weeks and producers net short on at least 69% (2006-2026, same run),
so the split is exact while the components pull opposite ways.

Reproducer: [`../../scripts/disagg_legacy_split.py`](../../scripts/disagg_legacy_split.py).
Applies to commodity markets only: financial futures have TFF, not Disaggregated.

---

## 4. The `spread_contracts` trap, which is ours

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

## 5. Supplemental against everything else: a different basis

The Supplemental (Commodity Index Trader) report is **futures-and-options COMBINED**, where
the default series of Legacy, Disaggregated and TFF are all futures-only. Its
`Open_Interest_All` is therefore a different quantity for the same market and week: WHEAT-SRW
on 2026-07-28 is **566,358 combined against 463,502 futures-only**.

> **Since 2026-10-31 the store holds both bases of the other three** (`cot_*_combined/`,
> [ADR-0003](../adr/ADR-0003-combined-basis-is-in-scope.md)), so the basis is no longer a
> property of the report but of the series you read. The Supplemental's open interest IS
> comparable with `cot_legacy_combined/` for the same market-week (that is the 390/390 match
> below, now reproducible from inside the store), and is NOT comparable with `cot_legacy/`.
> Across the three reports, combined-against-combined behaves like futures-only against
> futures-only in §6. Measured 2026-10-27: ES 2,843,639 combined against 1,955,711, GC
> 518,784 against 396,810, CL 2,606,478 against 1,886,280.

**Nothing in the file says so.** It is the one report with no `FutOnly_or_Combined` column, so
`cotdata` asserts the flag rather than reading it, established by matching open interest
against both Legacy series 390/390 and 0/390.

**Its Index Traders does NOT nest inside Disaggregated's Swap Dealer.** The taxonomy is
Legacy, not Disaggregated, so the two cannot be differenced to isolate levered swap flow. The
index book is carved out of commercial, non-commercial **and non-commercial spreading**, three
buckets where CFTC's own prose names two.

**Coverage is 12 markets, then 13 from 2013-04-02**, when Soybean Meal entered (the
2026-08-03 analysis gives 2013-01-08, the 2013 file's first date; corrected in the
2026-10-31 analysis). Both counts circulate
because both are right for part of the history, and six markets were renamed without changing
code. `cotdata-vintage coverage` derives the covered set from the data and prints every entry
and exit.

Detail: [`../analysis/2026-08-03-cit-supplemental-measurements.md`](../analysis/2026-08-03-cit-supplemental-measurements.md).

---

## 6. What this permits

The enumerated safe comparisons, so that a consumer does not have to infer them from the
prohibitions:

- **Disaggregated against Legacy, same basis, bucket by bucket** (§3): safe. Producer plus
  swap (swap spreading on both sides) is Legacy commercial, managed money plus other
  reportable is Legacy non-commercial, exactly on futures-only, to 2 contracts on combined.
  Check open interest on a market with `hist_codes`, where a stitched Legacy week can be the
  predecessor contract. Positions only, not trader counts.
- **Open interest, Legacy against TFF, same basis**: identical, exactly, always. Safe.
- **`nonreportable`, Legacy against TFF, same basis**: identical, exactly, always. Safe.
- **Open interest, any futures-only series against the Supplemental**: **not** safe, different
  basis. Against `cot_legacy_combined/`: safe, the two agree 390/390 (13,753/13,753 over the
  full history, 2026-10-31).
- **The Supplemental against `cot_legacy_combined/`, bucket by bucket**: safe, and the one
  subtraction across two reports that IS interpretable, because the Supplemental's `_NoCIT`
  buckets are Legacy combined's buckets with the index traders removed: same traders, same
  week, same classification. Legacy bucket minus `_NoCIT` bucket is the index position carved
  from it, to one contract of rounding per bucket. `cotdata.cit.decompose` makes it and
  refuses when open interest or non-reportable disagree. Measurements:
  [`../analysis/2026-10-31-cit-legacy-combined-decomposition.md`](../analysis/2026-10-31-cit-legacy-combined-decomposition.md).
- **Anything across the two bases of one report** (`cot_legacy/` against
  `cot_legacy_combined/`): not safe as a difference or ratio without knowing it measures the
  option book, which is what the comparison is for if you make it deliberately.
- **Anything above the reportable line, across any other two reports** (Legacy against TFF,
  the Supplemental against Disaggregated, Disaggregated against TFF): not safe.
- **Within one report, across weeks**: safe, subject to the gap rule (§7).

---

## 7. A related trap that is not about reports but bites the same callers

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

Sections 2, 4 and 7 were measured in `crowdmon` and are restated here because `cotdata` owns
the facts and crowdmon is deprecated. The original sections, with their reproducers and the
dates they were established, are:

- §2, §4: `crowdmon/docs/design/amendments-2026-08-04.md` §D7, reproducer
  `crowdmon/docs/analysis/reproduce_single_number.py::d7_legacy_and_tff_share_two_things`
- §7, gaps: `crowdmon/docs/design/amendments-2026-08-01.md` §A2
- §7, shutdown: `crowdmon/docs/design/amendments-2026-08-01.md` §A1

They are **restated rather than moved**: those files are point-in-time records under that
repo's doc lifecycle and are not edited by this harvest. See
`crowdmon/docs/HARVEST.md` for the full map of what was ported, what was already resolved, and
what was parked with the hypothesis.

Section 5 was authored here from the start and is summarised rather than moved. Section 3
was measured here on 2026-11-25, reproducer `scripts/disagg_legacy_split.py`.
