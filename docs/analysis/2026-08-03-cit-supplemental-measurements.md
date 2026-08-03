# Supplemental (CIT) report: what the files actually say

**Date:** 2026-08-03. Point-in-time. Per the doc lifecycle, `analysis/` is never amended:
if a later measurement contradicts this, write a new dated file rather than editing this
one. Corrected once before merge, on the same day, while it existed only on the branch that
introduced it (§3's rename count, and §4's exact-vs-breach rates, both caught in
pre-merge review). Nothing was published under the wrong numbers; from here the file is
frozen.

Every number here comes from the real CFTC archives, `dea_cit_txt_2006.zip` through
`dea_cit_txt_2026.zip` (21 files, 13,584 market-weeks, 2006-01-03 to 2026-07-28),
downloaded live on 2026-08-03, plus `dea_fut_xls_*.zip` (Legacy futures-only) and
`dea_com_xls_*.zip` (Legacy futures-and-options combined) as controls. Reproducers are
the probe scripts described in §7.

This exists because `docs/handoffs/2026-08-03-cit-supplemental.md` asked for the report's
properties to be **derived from the data rather than assumed**, and four of them came back
different from the handoff or from the CFTC prose.

---

## 1. It is futures-and-options combined, and the file does not say so

The Supplemental has no `FutOnly_or_Combined` column. Disaggregated and TFF both do, which
is what `vintage_ingest._combined_flag` reads, so the Supplemental would have defaulted to
`combined=False` and put a guessed value into the natural key.

Established instead by matching against the two Legacy series on identical
(market_code, report_date) keys, 2026, 390 market-weeks, all 13 markets:

| compared against | `Open_Interest_All` match | `NonRept_Positions_Long_All` match |
|---|---|---|
| Legacy futures-only (`annual.xls`) | **0 / 390** | 0 / 390 |
| Legacy futures-and-options combined (`annualof.xls`) | **390 / 390** | 390 / 390 |

Wheat-SRW on 2026-07-28: Supplemental OI 566,358, Legacy combined 566,358, Legacy
futures-only 463,502.

`canonicalize_supplemental` therefore asserts `combined=True` and takes no override, and
raises if a `FutOnly_or_Combined` column ever appears saying otherwise.

## 2. The date column is named two different things, and the rename was cosmetic

| years | column name |
|---|---|
| 2006-2012 | `As_of_Date_In_Form_MM/DD/YYYY` |
| 2013-2026 | `As_of_Date_In_Form_YYYY-MM-DD` |

The **values are ISO `YYYY-MM-DD` in every year**, including the ones whose header claims
MM/DD/YYYY: the 2006 file's first row carries `2006-12-26` under the MM/DD/YYYY heading.
So 2013 fixed a mislabelled header, not a format. `providers/cftc_cit._parse_zip` accepts
either and renames to the repo's `Report_Date_as_MM_DD_YYYY`; it raises if it finds
neither, rather than letting `_canonicalize` fall back to the frame's positional index.

Two other columns were renamed at the same boundary, both unused here:
`Pct_OI_Tot_Rept_{Long,Short}_All_NoCIT` and `Traders_Tot_Rept_{Long,Short}_All_NoCIT`
lost the `_NoCIT` suffix in **2012**, a year earlier than the date column. Column count is
54 in every year.

Report dates are Tuesdays (9,114 of 9,192 distinct market-weeks in the 2013+ sample), with
78 Mondays where a holiday shifted the week. That matches the rest of the COT family.

## 3. Coverage is 12 markets, then 13 from 2013

Both counts circulate because both are right for part of the history.

| years | markets |
|---|---|
| 2006-2012 | 12 |
| 2013-2026 | 13 |

**Soybean Meal (026603) entered in 2013**, first report date 2013-01-08. That is the only
entry and there are no exits in 21 years. Nothing else changed: no market code appears or
disappears, and the twelve originals run unbroken 2006-2026.

**Six** codes were **renamed** without changing code, in two waves, which is why
market-name matching would have produced spurious entries and exits and market_code
matching does not:

| code | old name | current name | renamed |
|---|---|---|---|
| 033661 | COTTON NO. 2 - NEW YORK BOARD OF TRADE | COTTON NO. 2 - ICE FUTURES U.S. | 2007 |
| 073732 | COCOA - NEW YORK BOARD OF TRADE | COCOA - ICE FUTURES U.S. | 2007 |
| 080732 | SUGAR NO. 11 - NEW YORK BOARD OF TRADE | SUGAR NO. 11 - ICE FUTURES U.S. | 2007 |
| 083731 | COFFEE C - NEW YORK BOARD OF TRADE | COFFEE C - ICE FUTURES U.S. | 2007 |
| 001602 | WHEAT - CHICAGO BOARD OF TRADE | WHEAT-SRW - CHICAGO BOARD OF TRADE | 2013 |
| 001612 | WHEAT - KANSAS CITY BOARD OF TRADE | WHEAT-HRW - CHICAGO BOARD OF TRADE | 2013 |

001612 is the sharp one: the market changed exchange (KCBT to CBOT, after CME Group
acquired the Kansas City Board of Trade) and kept its CFTC code. Market names in the
2006-2012 files also carry a trailing space.

**The 2007 wave is also why "the latest name" cannot be the lexicographic max**, which is
what a first pass of `derive_coverage` used: `'I' < 'N'`, so a max-of-strings rule reports
the pre-2007 NYBOT name for all four ICE markets in every year after the rename. The wheat
pair comes out right under either rule, which is exactly how it survived a first reading.
Caught in pre-merge review; the shipped rule takes the name at the latest report date.

All 13 codes resolve in `registry.yaml`: ZW, KE, ZC, ZM, CT, HE, ZS, LE, GF, CC, ZL, SB, KC.

## 4. The open-interest identity, and why the exception rate is 45% rather than 0%

Summing the four canonical categories per side and adding non-commercial spreading:

| | exact | per-year exact | residual |
|---|---|---|---|
| long side | 55.2% of 13,584 market-weeks | 51.6% - 59.5%, sd 2.1pp | never more than **2 contracts** |
| short side | 55.4% | — | never more than 2 contracts |

Counting a market-week as a breach if **either** side is off, the **breach** rate is 67.7%
(per-year 62.6% in 2012 to 71.7% in 2023, sd 2.7pp). Two different rates, so keep the
qualifier attached: 55% is how often the identity is *exact on one side*, 68% is how often
*either side* is off by a contract or two.

Both are **stable across the whole history** — no trend, no regime break, nothing at the
2013 coverage change and nothing at 2018.

Using CFTC's own published `Tot_Rept_Positions_*` columns instead of summing categories,
the identity `Tot_Rept + NonRept == OI` is exact on 74.9% of rows and off by **at most 1**
on the rest.

**The cause is combined reporting, not the Supplemental.** Control, on the same 2026 weeks
and the same identity:

| file | exact |
|---|---|
| Legacy **futures-only** | 99.7% (31 breaches in 10,719 rows) |
| Legacy **combined** | 90.1%, residual +/-1 (a handful at +/-2) |
| Supplemental (combined) | 55% |

A futures-and-options report publishes delta-weighted option equivalents rounded to whole
contracts, independently per category. Summing `n` independently rounded values admits at
most `n` contracts of error, which is exactly the bound `rounding_tolerance` already
returns (4 for the Supplemental's four categories). The Supplemental sits lowest of the
three because it has one more rounded addend than Legacy combined.

This is worth stating plainly because it looks alarming and is not: **this is rounding, not
a data-quality problem**, the residual never approaches the tolerance, and `validate()`
raises zero soft warnings across all 21 years.

## 5. Index traders are carved out of three buckets, not two

CFTC's prose says index traders are drawn from the commercial and non-commercial
categories. Differencing the Supplemental against Legacy combined on the same keys shows a
third source: **non-commercial spreading**.

    CIT_Long = (Comm_Long - Comm_Long_NoCIT)
             + (NonComm_Long - NonComm_Long_NoCIT)
             + (NonComm_Spread - NonComm_Spread_NoCIT)

Exact on 59.5% of 2026 rows and within +/-1 on essentially all of the rest, which is the
same rounding as §4 (three rounded differences). Wheat-SRW 2026-07-28:
76,743 + 45,210 + 24,157 = 146,110 = `CIT_Positions_Long_All`, exactly.

No component is ever negative in any sampled year, so this is a partition rather than a
netting.

**The composition has drifted substantially**, which matters for anything treating the
index book as one stable population (median share of `CIT_Long` by source):

| year | from commercial | from non-commercial | from NC spreading |
|---|---|---|---|
| 2006 | 0.886 | 0.113 | 0.000 |
| 2010 | 0.808 | 0.188 | 0.003 |
| 2015 | 0.716 | 0.270 | 0.007 |
| 2020 | 0.622 | 0.274 | 0.094 |
| 2026 | 0.555 | 0.339 | 0.088 |

Non-Reportable is untouched: index traders are reportable by definition, which is why it
has no `_NoCIT` variant and why it matches Legacy combined exactly (§1).

## 6. Header spellings

CFTC's header row carries two long-standing typos and one internal inconsistency. All are
tolerated by `_resolve` rather than normalised at parse, so a future correction is not a
breakage:

- `NComm_Postions_Spread_All_NoCIT` — "Postions", missing the `i`
- `Change_NonComm_Spead_All_NoCIT` — "Spead"
- position columns say `NComm_`, while `Pct_OI_` and `Traders_` columns say `NonComm_`
- `Change_Comm_Short_All_NoCIT ` carries a trailing space

## 7. Distribution details

- Every year 2006-2026 is an individual zip; **there is no `dea_cit_txt_hist_2006_2016.zip`**
  (404). Disaggregated and TFF are bundled that way, the Supplemental is not.
- Unlike disagg/TFF, whose annual zips 404 before 2010, **all 21 Supplemental years return
  200**, so `annual_sources` needs a separate 2006 floor rather than sharing the 2010 one.
- Archive member is `annualci.txt` in every year except 2006, which is `annualci2006.txt`.
  Nothing here depends on the name (the parser takes `namelist()[0]`, as the other three do).
- A weekly static exists at `https://www.cftc.gov/dea/newcot/deacit.txt` (200). It is
  **not** in the capture set: the Legacy weekly static already supplies the true
  publication `Last-Modified` that `vintage_schedule.sync_published` resolves release dates
  from, and all COT reports publish together, so a second one adds a source to the daily
  sweep and no information.

### Reproducers

Scripts used, in the session scratchpad rather than committed (they download ~30 MB and
depend on nothing in this repo but `pandas`): `probe_cit.py` (schema stability, coverage,
identities), `probe2.py` (per-year breakdown), `probe3.py` (futures-only vs combined),
`probe4.py` (carve-out identity, control). The committed equivalent is
`tests/test_cit_supplemental.py`, which pins every property above that can be pinned from
the trimmed fixtures, and `tests/_gen_cit_fixtures.py`, which rebuilds those fixtures from
the real archives.

Full-history figures were reproduced end to end through the shipped code path: 21 raw
snapshots captured, ingested to **54,336 observations across 13,584 market-weeks with zero
revisions and zero validation warnings**, and a full replay of all 21 snapshots wrote zero
further observations.
