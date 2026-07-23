# CLAUDE.md

Project memory for Claude sessions working in this repo.

## Memory: NPF normalization — ADF vs second-moment (2026-07)

Context: a Discord post analyzed **NPF** (net position fraction = net
non-commercial positioning / open interest) built from CFTC Legacy COT data
(the same data `src/cotdata/providers/cftc.py` downloads).

Feedback received, and accepted as correct:

- Dividing net positioning by OI is a *level-stationary* transformation, but
  raw net is already ADF-stationary in most markets — positioning oscillates
  around a mean by construction — so the gain shown by an ADF test is modest
  (it mainly rescues markets with strong secular OI growth).
- The real benefit is **second-moment** and ADF cannot detect it: net measured
  in contracts scales with market size (±20k contracts was an extreme in 1995,
  noise in 2015), so full-history raw net is a mixture of a narrow early era
  and a wide late era. That inflates kurtosis and lets the recent
  high-variance era dominate percentile-based thresholds. NPF collapses the
  eras onto one scale: era variance stabilizes, kurtosis drops, and thresholds
  (and the "gray zone" between long/short triggers) calibrated on full history
  stay meaningful across decades.
- Remaining caveat: NPF fixes *scale*, not *composition*. The 2004–2006
  commodity-index influx changed who holds positions in ags/energy; no
  denominator fixes that structural break — consider post-break calibration
  windows.

Verification plan (script `npf_check.py`, handed off to a trusted-network
session via the trigger "NPF variance-stability check" because some sandbox
network policies block cftc.gov): for gold, silver, WTI, nat gas, corn,
EUR FX, 10Y note, and E-mini S&P over 1990–present, compare raw net vs NPF on
(1) ADF p-values, (2) era variance ratio = std(last third of history) /
std(first third), (3) excess kurtosis. Expected pattern if the feedback holds:
similar ADF p-values with most markets stationary in both forms; raw variance
ratios well above 1 vs NPF ratios near 1; lower kurtosis under NPF.
