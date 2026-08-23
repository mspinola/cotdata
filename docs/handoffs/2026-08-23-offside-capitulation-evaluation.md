# Offside capitulation: a pre-registered first-look evaluation

**Date:** 2026-08-23. **Status: OPEN — design frozen, execution handed off.**
The executable form of §4 is [`scripts/offside_capitulation_check.py`](../../scripts/offside_capitulation_check.py);
the script and this document were written together, before any look at the
statistic they define. §§1–5 are frozen: an executing session runs the script,
reports what comes back, and does not edit them. Results belong in a new dated
`docs/analysis/` file, per the doc lifecycle.

**Ownership note, stated rather than hidden.** Per ADR-0007, cotdata is CFTC
positioning and a consumer question like this one belongs with the consumer
(`npf` is the natural final home). It is filed here because the task arrived on
a cotdata branch and the deliverable is self-contained: the script downloads
its own data exactly as `npf_check.py` did and touches nothing in `src/`.

---

## 1. The question

"Offside capitulation": a speculative crowd at a positioning extreme, with
price moving against it (**offside**), rapidly unwinds its net (**capitulates**).
What do returns do next? The folk readings disagree on the *sign*, and the
workspace's own vocabulary carries both:

- **Claim A, "washout"**: capitulation ends the adverse move — the selling (or
  covering) that was still to come has happened, so continuation stops or
  reverts. This is the reading behind cotmetrics' `FLAG_BULL_CAPITULATION`
  ("the institutional floor") and behind treating spec washouts as bottoms.
- **Claim B, "stampede"**: capitulation removes the last resistance and the
  adverse move continues. This is the reading behind cotmetrics'
  `CAPITULATION` ("liquidity vacuum... no bottom in sight") and
  `COMMS_CAPITULATION` ("the Briese stampede").

Both cannot be right at the same horizon on the same event. That built-in
contradiction is what makes this worth measuring, and it is why the test is
two-sided.

## 2. Vocabulary provenance

- **Offside** is crowdmon's term: crowding = *lopsided, offside, trapped*
  (crowdmon `amendments-2026-08-04 §D9`). There, "offside" is operationalized
  as distance-to-forced-flip; here it is operationalized directly as an
  adverse move against an extreme, because Legacy COT has no trigger machinery
  and needs none for this question.
- **Capitulation** is the npf/cotmetrics term (signal family
  `CAPITULATION` / `COMMS_CAPITULATION` / `FLAG_*_CAPITULATION`), where it is
  defined on daily candles, OI kinetics and crowding z-scores. Here it is
  reduced to the one ingredient Legacy COT carries: an unusually large
  one-week unwind of the crowded net.

## 3. What is already measured, and what is not

Nothing below is re-tested here; it is the register this evaluation sits in.

1. **The offside pool does capitulate when its level is hit — marginally.**
   The forced-flow mechanism verdict
   (`npf/docs/crowdmon/2026-08-06-forced-flow-mechanism-verdict.md`) returned
   `supported` on its pre-registered criteria, then showed most of the effect
   was positioning mean reversion (the placebo carried 52–74% of the
   headline); the crossing-specific residual is ~1–2% of the pool's size, with
   the Disaggregated side marginal. Its respecification
   (`crowdmon/docs/handoffs/2026-08-06-forced-flow-respecification.md` §3)
   adds an advisory crosstab: after a crossing the *agreeing* pool's week is
   `long_liquidation`-dominated 3–5x more often than the contradicted pool's.
   So "the offside crowd capitulates" has measured support as a *flow*
   statement. **What returns do afterward was not the outcome variable of any
   of it.**
2. **The capitulation-family booleans were never individually scored.** They
   entered the ML feature stack, whose honest point-in-time re-evaluation
   returned no edge (`npf/docs/npf/ml_lookahead.md`); no per-signal
   pre-registered verdict on a capitulation event exists in the register this
   session searched (`npf`, `crowdmon`, `cotdata`, `cotmetrics`).
3. **Mean reversion of positioning is the trap.** The FFM verdict's central
   lesson: any contrast whose groups are defined by the sign or size of
   `pool_net` gets a "result" from books drifting toward flat. §4's controls
   exist because of that lesson.

## 4. Frozen protocol

Universe: the 8 Legacy futures-only markets of the NPF variance check (gold,
silver, WTI, nat gas, corn, EUR FX, 10Y note, E-mini S&P), 1990–present, each
market entering when its data does. Weekly non-commercial net; NPF = net/OI.
Report weeks not 6–8 days apart are never differenced (the FFM gap rule).

Definitions (all trailing, per market; parameters frozen in the script header):

- **Extreme**: NPF's percentile rank within its trailing 156-week window
  (min 104) ≥ 0.80 (crowded-long) or ≤ 0.20 (crowded-short).
- **Offside**: extreme, and the trailing 4-week log return is ≥ 1.0 trailing
  sigma (of 4-week returns, 156w window) *against* the crowd's side.
- **Capitulation week**: |ΔNPF| ≥ its own trailing 90th percentile (156w),
  signed as an unwind of the previous week's crowd side.
- **Event**: capitulation at `t` with offside at `t−1` or `t−2`; 8-week
  per-market-and-side cooldown.
- **Outcome**: forward sigma-scaled weekly log returns (trailing 52w sigma),
  summed over h ∈ {1, 4, 13} weeks, **signed so that positive = the adverse
  move continues** (claim B direction; negative = claim A).
- **Controls**, both inheriting the crowd side and its sign convention:
  (a) offside without capitulation; (b) capitulation without offside.
- **Statistic**: pooled mean (median reported beside it), two-sided `p_null`
  from a 13-week calendar-block bootstrap recentred on zero (the FFM p-value
  correction, applied from the start rather than discovered again), 5000
  draws, seed 20260823.

**Declared readings**: 2 sides x 3 horizons on the event group = 6, primary =
the two 4-week event readings; the same 6 on each control as context. Total
declared: 18. Anything else quoted from a run is an addition and must say so.

**Pre-committed reading of the outcome, at the 4-week primaries:**

- **Claim A supported**: both sides' event means negative, `p_null` < 0.05,
  and each more negative than its offside-without-capitulation control.
- **Claim B supported**: the mirror image, positive.
- **Insufficient data**: pooled event n < 30 on a side — report counts, no
  verdict on that side. Events are conjunctions of three rare conditions;
  this outcome is likely and is a finding, not a failure.
- **Anything else**: "no separation" — the event carries no forward-return
  information beyond its conditioning, which, given §3.1, would itself be
  the coherent completion of the FFM story.

This is a first look, not a strategy test: no trades, no `TradeLog`, no
claim that a reader of the Friday release could have acted (the FFM §3.2
scope refusal is inherited; report-date alignment ignores the 3-day
publication lag deliberately).

## 5. Execution constraints, measured

> **Amended 2026-08-23, pre-execution, at the author's direction, before any
> live number existed.** The first draft sourced data by download only. The
> author pointed out the obvious: the data already lives in the stores this
> repo produces. The script is now **store-first** — COT from
> `$COTDATA_STORE` via `cotdata.get_cot` (code stitching included), daily
> closes from `$MARKETDATA_STORE` via `marketdata.get_bars` (propadj, then
> backadj, logged) — and downloads only where the stores are absent. §4 is
> untouched; this changes where bytes come from, not what is computed. On a
> store machine (the Mac, the Windows producer) the run needs no network at
> all, and store prices are Norgate back-adjusted series, which retires the
> stooq splicing caveat below for those runs.

The git repo carries no data (the store is external and gitignored), so a
cloud session holds only code. This session's sandbox additionally **cannot
download**: the proxy answers 403 to CONNECT for `www.cftc.gov`, and
`stooq.com`, `query1.finance.yahoo.com`, `fred.stlouisfed.org` and
`publicreporting.cftc.gov` are all unreachable (probed 2026-08-23). That is
the same block the NPF variance check hit, and the same remedy applies where
no store is mounted: execution handed to a trusted-network session via a
one-shot trigger, per the CLAUDE.md precedent. **A machine with the stores is
the better executor.**

Price caveats the executor must carry into the results file (download-path
runs only; store-path runs replace them with the Norgate series' own terms):

- stooq continuous futures are spliced, not back-adjusted; roll carry
  inflates adverse-move and forward-return magnitudes in high-carry markets
  (nat gas above all). Acceptable for a first look; flagged, not fixed.
- If a market's price series fails to resolve, the script logs it and the
  market drops from the priced panel; the log line belongs in the results.
- If no price source resolves at all, the script reports COT-only
  capitulation descriptives and states the run is blocked. A blocked run is
  reported as blocked, never as a null.

## 6. Instructions to the executing session

1. Check out this branch (`claude/offside-capitulation-evaluation-b55iv9`).
2. `pip install pandas numpy requests xlrd` (plus `pyyaml python-dateutil`
   and `crucible-marketdata` for the store path), then run
   `python scripts/offside_capitulation_check.py` with `COTDATA_STORE` and
   `MARKETDATA_STORE` exported if this machine has the stores (preferred —
   no network needed); without them the script downloads. Run `--selftest`
   first; it must pass before a live run is attempted.
3. Commit the full stdout as `docs/analysis/2026-08-23-offside-capitulation-run.txt`
   and write `docs/analysis/2026-08-23-offside-capitulation-results.md`:
   the tables, the §4 pre-committed reading applied literally, per-market
   sign counts, price-source log, and any deviation from this document.
4. Push to the same branch. Do not edit §§1–5 of this file, the script's
   frozen parameters, or anything in `src/`. If the run fails, commit the
   failure output and say what blocked it.
