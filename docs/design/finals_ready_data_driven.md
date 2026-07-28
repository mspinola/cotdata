# Data-driven `finals_ready` (replace the fixed-clock cutoff)

Status: DRAFT / spec. Blocked on one Windows-only probe (see "Open question").

## Problem

`--require-final` gates the nightly Norgate price capture so it never stores an
interim (non-settled) bar. Today it does so with a fixed **local-clock cutoff**
(`--final-cutoff`, default `20:55`): `finals_ready` is true only once *both* the
`Futures` and `Continuous Futures` databases were refreshed at/after today's cutoff
(`providers/norgate.py::_finals_ready`).

This is calibration, not robustness, and it broke in production on 2026-07-27:

- Norgate's evening publish finalized the **Futures** database at **8:49 PM** and did
  not touch it again that night; **Continuous Futures** finalized at 8:55 PM.
- The check requires *both* `>= 20:55`. Futures at 8:49 `< 8:55` → never ready → the
  task deferred (exit 1) on every attempt, the chained store sync was skipped, and
  prices went stale (stuck on the prior Friday's bar).

The failure mode is intrinsic to a wall-clock cutoff: it must sit *below* the earliest
evening-final refresh and *above* any daytime interim refresh, and Norgate's publish
time drifts night to night. If Norgate finalizes at 20:44, a `20:45` cutoff misses it;
if you lower the cutoff to catch early nights, you risk accepting a pre-settlement bar.
There is no single clock value that is safe.

## Available Norgate API (v1.0.77)

`norgatedata` exposes **no** holiday / trading-calendar / session function (confirmed on
PyPI). It does expose data-content signals we are not yet using:

- `last_quoted_date(symbol, datetimeformat='iso')` — the latest trading date that has a
  bar for `symbol`. For a continuous symbol (never expires) this is the newest available
  daily bar.
- `second_last_quoted_date(symbol)` — the prior trading day's date.
- `last_price_update_time(symbol)` — local PC time the symbol was last refreshed.
- `last_database_update_time(db)` — local PC time the database was refreshed (current
  approach).
- `status()` — is NDU running.

`last_quoted_date` is the key: it lets us ask "**has Norgate's latest bar advanced to the
session we expect?**" instead of "was the file touched after a magic minute?" That is
immune to publish-time drift.

## Proposed design

Replace the clock-threshold with a **data + session** check:

```
finals_ready(now) := last_quoted_date(ref) >= expected_last_session(now)
```

- `ref` = one (or a small quorum) of liquid continuous symbols that trade every US
  futures session (e.g. `&ES`, `&CL`). Use the min across the quorum so a single lagging
  symbol cannot green-light the whole book.
- `expected_last_session(now)` = the most recent trading day whose session has **closed**
  as of `now`. Weekend/holiday aware.

Why this is robust where the cutoff is not:

- **Publish-time drift disappears.** If Norgate finalizes early, `last_quoted_date`
  advances early and we are ready early. If it finalizes late, the bar is simply not
  there yet and the run defers — retries then catch it whenever it lands. The gate no
  longer depends on Norgate hitting a target minute.
- **The only remaining clock element is coarse and stable.** `expected_last_session`
  needs to know a session has *closed*, i.e. "we are past the exchange's daily settlement
  window." That threshold has hours of slack (settlement is done well before the evening
  publish), unlike the razor-thin publish cutoff. It is a property of the market, not of
  Norgate's schedule.

### Two pieces to nail down

1. **`expected_last_session` / the trading calendar.** `norgatedata` has no calendar API,
   so options are:
   - `pandas_market_calendars` (mature, has CME/ICE calendars) — adds a dependency to a
     public package. Cleanest correctness.
   - A maintained holiday list in-repo (the NDU app shows NYSE/exchange holidays 6 months
     each way; not exposed to Python) — no dependency, but must be maintained.
   - Derive from Norgate: treat `expected_last_session` as "the max `last_quoted_date`
     across a broad basket," and require the ref quorum to have caught up to it. Avoids a
     calendar entirely, at the cost of a softer definition.
   A wrong calendar only causes a **false defer** (safe: it self-heals next session), never
   a false capture, so this is a robustness/convenience tradeoff, not a correctness risk.

2. **Interim/preliminary bars — the open question below.**

## Open question (Windows probe required)

Does Norgate ever expose a **provisional current-day bar before final settlement**?
- If **no** (today's bar appears only once settled): `last_quoted_date == today` is a
  sufficient, clean "finals are in" signal, and the design above is complete.
- If **yes** (staged/preliminary then final settlement): bar *presence* is not enough,
  because the value can still be revised. We would then keep a **coarse** "past settlement
  window" guard (e.g. only trust today's bar after ~18:00 ET) on top of the date check —
  still far more forgiving than the current publish-time cutoff.

The probe (`scripts/probe_norgate_finals.py`, run on the Windows producer) answers this by
sampling `last_quoted_date` and the tail of `price_timeseries` for a couple of continuous
symbols during the day and again after the evening publish, and by dumping `dir(norgatedata)`
to confirm no calendar function exists in the installed build.

## Compatibility / rollout

- `cotdata-prices` is a public CLI. Keep `--final-cutoff` accepted (as the fallback
  "settlement window" guard, or deprecated-but-honored) so no caller breaks.
- Keep the pure-core split: `_finals_ready(...)` stays a norgatedata-free, unit-tested
  function operating on plain dates/times; `finals_ready(...)` does the norgatedata I/O.
- Default behavior change (clock → data) is the point, but gate it so it can be rolled
  back with a flag if a first-night surprise appears.

## Test plan

Unit-test the pure core on any OS (no norgatedata): weekend/holiday rollover, early vs
late publish, ref-quorum lag, and the interim guard if needed. Live-validate `finals_ready`
against the real feed on Windows across a few evenings before flipping the default.
