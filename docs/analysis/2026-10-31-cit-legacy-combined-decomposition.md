# The Supplemental joined to Legacy combined: what the index carve-out looks like from the store

**Date:** 2026-10-31. Point-in-time. Per the doc lifecycle, `analysis/` is never amended: a
later measurement that contradicts this goes in a new dated file.

**Reproducer:** `scripts/cit_legacy_decomposition.py`, run against a store holding
`cot_supplemental/` (copied from the live store, newest report week 2026-10-27) and
`cot_legacy_combined/` for the 13 Supplemental codes, 2006 to 2026, fetched the same day with
`cotdata.providers.cftc.update(codes=..., first_year=2006, combined=True)`. The library side
is `cotdata.cit.decompose` and `cotdata.cit.check`, added in the same change. Nothing here
reads a price. Every table is descriptive; no verdict is rendered on anything.

**Why this exists.** The 2026-08-03 measurements established, on one year of raw files, that
the Supplemental is Legacy combined with the index traders carved out of commercial,
non-commercial and non-commercial spreading. With the combined basis now in the store
([ADR-0003](../adr/ADR-0003-combined-basis-is-in-scope.md)), the join can be made from the
store for the whole history, and two questions become answerable that neither report answers
alone: how much of Legacy's own buckets is index money in these markets, and how far the
ex-index series sit from the Legacy series a positioning index is normally computed on.

---

## 1. The two exact identities hold on every shared week, 2006 to 2026

Open interest and non-reportable are published on both reports and should agree to the
contract when both are the combined basis. They do, on **13,753 of 13,753** shared
market-weeks, for all 13 markets. This extends the 390/390 result of the 2026-08-03 doc from
one year to the full history, now reproducible from inside the store, and it is why
`decompose` refuses a market where either disagrees: a breach is a basis, stitching or
vintage error, never a market fact.

| market | shared weeks | legacy-only weeks | OI exact | non-rept exact | negative carve-outs | min carve-out | first shared |
|---|---|---|---|---|---|---|---|
| ZC | 1087 | 0 | 100.0% | 100.0% | 10 | -1 | 2006-01-03 |
| ZW | 1087 | 0 | 100.0% | 100.0% | 5 | -1 | 2006-01-03 |
| KE | 1087 | 0 | 100.0% | 100.0% | 2 | -1 | 2006-01-03 |
| ZS | 1087 | 0 | 100.0% | 100.0% | 5 | -1 | 2006-01-03 |
| ZM | 709 | 378 | 100.0% | 100.0% | 0 | 0 | 2013-04-02 |
| ZL | 1087 | 0 | 100.0% | 100.0% | 27 | -1 | 2006-01-03 |
| CT | 1087 | 0 | 100.0% | 100.0% | 29 | -1 | 2006-01-03 |
| CC | 1087 | 0 | 100.0% | 100.0% | 47 | -1 | 2006-01-03 |
| SB | 1087 | 0 | 100.0% | 100.0% | 16 | -1 | 2006-01-03 |
| KC | 1087 | 0 | 100.0% | 100.0% | 36 | -1 | 2006-01-03 |
| HE | 1087 | 0 | 100.0% | 100.0% | 2 | -1 | 2006-01-03 |
| LE | 1087 | 0 | 100.0% | 100.0% | 8 | -1 | 2006-01-03 |
| GF | 1087 | 0 | 100.0% | 100.0% | 0 | 0 | 2006-01-03 |

No Supplemental week is missing from Legacy combined (zero supplemental-only weeks
everywhere). The 378 legacy-only weeks are Soybean Meal before it entered the Supplemental.

**Negative carve-outs are rounding.** 187 of the 82,518 carve-out cells (six per shared
week) come out at exactly -1 and none below it. A combined report rounds each category's
delta-weighted option equivalents independently, so Legacy's bucket and the Supplemental's
ex-index bucket can each be off by one from the unrounded value, and their difference by one
when the true carve-out is zero. The 2026-08-03 doc reported no negative component in the
years it sampled; over the whole history there are a few, all one contract deep, which is
the bound that doc's own rounding argument predicts.

**Correction to the 2026-08-03 doc, section 3.** It gives Soybean Meal's first Supplemental
report as 2013-01-08. That is the first report date in the 2013 FILE, not Soybean Meal's.
Parsing `dea_cit_txt_2013.zip` directly: 52 distinct report dates from 2013-01-08, and
market 026603 is present on 40 of them, from **2013-04-02**. The store is right and the doc
was wrong; the npf pre-registration draft of the same date (`2026-10-31-cit-index-flow-prereg.md`,
guard G2) noted the quarter's disagreement and asked the executor to record which is right.
This is the record. Coverage is therefore 12 markets through 2013-03-26 and 13 from
2013-04-02.

## 2. Where the index book comes from, both sides, whole history

Median over market-weeks, per year, of each source's share of the index position. The long
side reproduces the 2026-08-03 table (0.886 / 0.113 / 0.000 in 2006, 0.558 / 0.348 / 0.085
in 2026) from the store rather than from raw files, which pins the two derivations against
each other. The short side was not in that doc.

| year | long: from comm | from noncomm | from spread | short: from comm | from noncomm | from spread |
|---|---|---|---|---|---|---|
| 2006 | 0.886 | 0.113 | 0.000 | 1.000 | 0.000 | 0.000 |
| 2008 | 0.849 | 0.149 | 0.000 | 0.969 | 0.000 | 0.005 |
| 2010 | 0.808 | 0.188 | 0.003 | 0.951 | 0.000 | 0.041 |
| 2012 | 0.764 | 0.228 | 0.005 | 0.943 | 0.000 | 0.043 |
| 2014 | 0.717 | 0.276 | 0.006 | 0.906 | 0.000 | 0.077 |
| 2016 | 0.715 | 0.269 | 0.008 | 0.909 | 0.000 | 0.090 |
| 2017 | 0.699 | 0.273 | 0.018 | 0.812 | 0.010 | 0.150 |
| 2018 | 0.665 | 0.273 | 0.057 | 0.606 | 0.070 | 0.282 |
| 2019 | 0.604 | 0.291 | 0.096 | 0.471 | 0.169 | 0.360 |
| 2020 | 0.622 | 0.274 | 0.094 | 0.408 | 0.157 | 0.433 |
| 2022 | 0.576 | 0.328 | 0.086 | 0.342 | 0.114 | 0.466 |
| 2024 | 0.562 | 0.363 | 0.081 | 0.356 | 0.321 | 0.284 |
| 2026 | 0.558 | 0.348 | 0.085 | 0.357 | 0.193 | 0.392 |

(Every year is in the reproducer's output; alternate years are shown where nothing happens
between them.)

Two things the short side adds. First, until 2017 the index book's short leg was almost
entirely classified commercial, with non-commercial contributing nothing at all for eleven
years. Second, **2017 to 2019 is a regime change on the short side**: the commercial share
fell from 0.81 to 0.47 in two years while spreading rose from 0.15 to 0.36 and
non-commercial appeared from nowhere. The long side drifted smoothly over the same years.
Whatever moved (reclassification of index-replicating swap dealers, the growth of long-short
and roll-yield index products, or both) is not identifiable from these files, but any study
that pools index SHORT positioning across 2017 is pooling two populations.

The spreading share is the residual `cit - from_comm - from_noncomm`, because the store does
not keep Legacy's spreading column (`providers/cftc.TARGET_COLS` is a 15-column slice). It is
exact by construction here; the 2026-08-03 doc established the three-way split independently
from the raw files, where it holds to rounding.

## 3. How much of Legacy's own buckets is index money

Median of the weekly ratio index-from-bucket over the Legacy bucket, per market, full history
and the last three years, long side. `cit_net_pct_oi` is the median index net position as a
percentage of combined OI; `short/long` is the median ratio of index shorts to index longs.

| market | comm long: index frac | last 3y | noncomm long: index frac | last 3y | CIT net % OI | short/long |
|---|---|---|---|---|---|---|
| ZC | 0.371 | 0.278 | 0.333 | 0.468 | 18.7 | 0.18 |
| ZW | 0.595 | 0.407 | 0.379 | 0.443 | 26.1 | 0.19 |
| KE | 0.441 | 0.463 | 0.289 | 0.486 | 22.8 | 0.13 |
| ZS | 0.319 | 0.240 | 0.293 | 0.417 | 16.6 | 0.20 |
| ZM | 0.332 | 0.298 | 0.314 | 0.298 | 17.8 | 0.19 |
| ZL | 0.392 | 0.294 | 0.278 | 0.423 | 21.5 | 0.14 |
| CT | 0.507 | 0.345 | 0.232 | 0.435 | 25.3 | 0.10 |
| CC | 0.200 | 0.205 | 0.258 | 0.337 | 11.2 | 0.27 |
| SB | 0.387 | 0.292 | 0.332 | 0.461 | 21.0 | 0.19 |
| KC | 0.364 | 0.345 | 0.286 | 0.322 | 19.0 | 0.16 |
| HE | 0.653 | 0.495 | 0.321 | 0.416 | 27.4 | 0.09 |
| LE | 0.607 | 0.375 | 0.285 | 0.323 | 29.5 | 0.05 |
| GF | 0.377 | 0.210 | 0.298 | 0.267 | 18.2 | 0.11 |

In the median week, between a fifth (cocoa) and two thirds (lean hogs) of what Legacy calls
commercial longs is index money, and between a quarter and two fifths of non-commercial
longs. The commercial fraction has fallen in eleven of thirteen markets over the last three
years and the non-commercial fraction has risen in eleven, which is section 2's drift seen
per market: the same index book, progressively reclassified out of commercial and into
non-commercial. The index position is almost one-sided (shorts are 5% to 27% of longs) and
large, a median net long of 11% to 30% of combined open interest.

## 4. The ex-index series against the Legacy series they are carved from

Net positions as a fraction of combined OI, weekly, 2006 to 2026. `corr_level` is the
Pearson correlation of the two series, `corr_d4` of their 4-week changes, `sign_agree` the
share of weeks where both nets have the same sign, `gap` the median absolute difference in
percentage points of OI, and the last two columns the mean net of each series.

| market | bucket | corr_level | corr_d4 | sign_agree | gap (pp OI) | Legacy mean % OI | ex-index mean % OI |
|---|---|---|---|---|---|---|---|
| ZC | comm | 0.892 | 0.951 | 0.678 | 13.2 | -3.2 | -17.0 |
| ZC | noncomm | 0.979 | 0.984 | 0.839 | 5.4 | 6.9 | 1.6 |
| ZW | comm | 0.704 | 0.849 | 0.384 | 19.1 | 6.1 | -14.6 |
| ZW | noncomm | 0.950 | 0.954 | 0.714 | 6.4 | -3.9 | -10.3 |
| KE | comm | 0.948 | 0.954 | 0.679 | 16.1 | -5.8 | -21.8 |
| KE | noncomm | 0.977 | 0.974 | 0.800 | 6.8 | 8.1 | 1.3 |
| ZS | comm | 0.893 | 0.961 | 0.762 | 10.9 | -4.3 | -17.3 |
| ZS | noncomm | 0.984 | 0.987 | 0.868 | 5.0 | 8.4 | 3.6 |
| ZM | comm | 0.981 | 0.979 | 0.862 | 12.4 | -13.8 | -26.1 |
| ZM | noncomm | 0.990 | 0.986 | 0.877 | 5.9 | 9.6 | 3.7 |
| ZL | comm | 0.933 | 0.973 | 0.724 | 15.9 | -8.2 | -24.6 |
| ZL | noncomm | 0.978 | 0.991 | 0.844 | 5.3 | 6.6 | 1.5 |
| CT | comm | 0.925 | 0.921 | 0.831 | 19.2 | -14.4 | -34.2 |
| CT | noncomm | 0.986 | 0.988 | 0.890 | 5.5 | 12.6 | 6.9 |
| CC | comm | 0.960 | 0.970 | 0.860 | 6.3 | -12.6 | -19.1 |
| CC | noncomm | 0.973 | 0.976 | 0.888 | 4.6 | 10.6 | 5.3 |
| SB | comm | 0.921 | 0.952 | 0.800 | 14.8 | -10.1 | -25.7 |
| SB | noncomm | 0.980 | 0.976 | 0.809 | 6.2 | 8.4 | 2.2 |
| KC | comm | 0.915 | 0.948 | 0.761 | 13.1 | -7.6 | -22.3 |
| KC | noncomm | 0.988 | 0.985 | 0.918 | 4.7 | 6.2 | 1.3 |
| HE | comm | 0.659 | 0.926 | 0.773 | 19.5 | -8.4 | -30.2 |
| HE | noncomm | 0.967 | 0.982 | 0.811 | 6.6 | 12.7 | 5.2 |
| LE | comm | 0.683 | 0.925 | 0.878 | 20.6 | -12.7 | -33.3 |
| LE | noncomm | 0.960 | 0.981 | 0.881 | 7.6 | 19.2 | 11.1 |
| GF | comm | 0.835 | 0.919 | 0.614 | 8.4 | 5.3 | -3.2 |
| GF | noncomm | 0.943 | 0.971 | 0.749 | 8.6 | 12.5 | 3.3 |

What the table says, in words:

- **The commercial series is the one that changes character.** Removing the index book
  shifts the mean commercial net by 6 to 22 points of OI, always toward the short side,
  which is what a hedger population should look like. In Chicago wheat and feeder cattle
  the Legacy commercial net is positive on average and the ex-index commercial net is
  negative: the sign of the "hedgers" flips. Sign agreement between the two series is 38%
  in wheat, 61% in feeders, 68% in corn and Kansas wheat. Level correlations run from 0.66
  (hogs) to 0.98 (meal). The 4-week changes agree much better (0.85 to 0.98): the week-to-week
  movement of Legacy commercials is mostly hedger movement, and what the index book
  contaminates is the LEVEL, which is exactly what a percentile-of-level positioning index
  reads.
- **The non-commercial series is shifted, not reshaped.** Level correlations of 0.94 to
  0.99, 4-week change correlations of 0.95 to 0.99, a mean shift of 5 to 9 points of OI
  toward the short side, sign agreement 71% to 92%. A speculative positioning index built
  on Legacy non-commercials in these markets is reading something close to the ex-index
  series plus a slowly varying offset. Not nothing (the offset is the index book's own
  drift, section 2), but a different order of problem from the commercial side.
- **Caveat on the level correlations.** Positioning levels are near unit-root
  (`cotmetrics/docs/positioning-series-properties.md`, median lag-1 0.956), so a Pearson
  correlation between two levels is inflated by shared persistence and should be read as
  description, not as a test. The 4-week change correlation is the better-grounded column
  and the sign-agreement and mean-shift columns do not depend on it.

## 5. What this does and does not establish

Established, from the store:

- The join is well defined and exact where it must be, over the entire history, and
  `cotdata.cit.decompose` makes it in one call.
- In the 13 agricultural markets, the Legacy commercial series a positioning index is
  normally computed on is a mixture of hedgers and index money, in proportions that vary
  by market (20% to 65% of longs) and have drifted over twenty years. The `_NoCIT`
  series is the hedger population on its own.
- The Legacy non-commercial series in these markets is close to the ex-index speculative
  series, offset by the index book's share.
- Soybean Meal enters the Supplemental on 2013-04-02, not 2013-01-08.
- The short side of the index book changed population between 2017 and 2019.

Not established, and not attempted:

- Whether an index computed on the ex-index commercial series, or on index flow itself,
  predicts anything. That is the pre-registered question in npf
  (`docs/handoffs/2026-10-31-cit-index-flow-prereg.md`), which reads the Supplemental alone
  and is designed, reviewed and executed by separate sessions. Nothing in this file bears on
  its gate or its decision rule.
- Why the 2017 to 2019 short-side composition changed.
- Anything before 2006. Legacy combined runs from 1995, so the eleven years before the
  Supplemental begins are available on the same basis as a pre-index baseline, but no
  index carve-out exists for them and none was computed.

## 6. Bottom line

The Supplemental turns Legacy from three reportable buckets into four for the agricultural
markets, with the index book isolated and the commercial and speculative series cleaned of
it, and the arithmetic holds exactly wherever it can be checked across twenty years. The
commercial series is the one materially changed by the cleaning: in several markets the
Legacy "hedgers" are net long on average only because index money is counted among them.
That is a measured fact about the data. Whether the cleaned series is a better input to any
book is a hypothesis, untested, and belongs to the pre-registered test.
