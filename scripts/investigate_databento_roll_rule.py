#!/usr/bin/env python
"""ADR-0006 follow-up: which databento continuous roll rule tracks Norgate?

The parity check (validate_databento_vs_norgate.py) found that databento's `.n.0`
continuous (open-interest roll) diverges from Norgate for the monthly-contract
commodities and livestock: the two place their rolls on almost entirely different
dates, so the back-adjusted daily-change shape disagrees (CL corr 0.78, HE 0.67,
DC 0.33). The financial and metals symbols that roll quarterly agree fine.

databento offers three continuous roll rules, selected by the middle letter of the
continuous symbol:
    c = calendar     (roll on the expiration calendar)
    n = open interest (what the producer currently uses)
    v = volume       (roll when the next contract's volume takes over)

This spike pulls each rule's daily bars for a few symbols (ohlcv only, so it is
cheap: one small request per rule, no statistics), reads the roll dates off the
`instrument_id` changes, and scores each rule against Norgate's own roll dates
(from the `Delivery Month` column in the Norgate store). The rule with the highest
match rate and smallest date offset is the candidate to switch `_FEEDS` to.

It reads the paid API, so it is NOT run in CI and is not wired into the producer.
Run it on a machine with DATABENTO_API_KEY set and a Norgate-built store to read.

The slow part is the API pull, so roll dates are cached per (root, rule) on disk: a
re-run, or extra --tol-days values, cost no extra pulls. Pull a symbol once, then sweep
tolerances for free.

Since ADR-0007 the Norgate store is a $MARKETDATA_STORE (`bars/futures/norgate/`);
the pre-move cotdata `prices/` layout is still read, so an older synced copy works.

Usage:
    DATABENTO_API_KEY=... python scripts/investigate_databento_roll_rule.py \
        --norgate-store ~/code/marketdata_store \
        --symbols CL ZS NG HE --tol-days 3 7 10      # sweep, one pull per rule
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from cotdata.providers.databento import GLBX_HISTORY_FLOOR, _client_from_env, _fetch
from cotdata.registry import all_symbols

_DEFAULT_SYMBOLS = ["CL", "ZS", "NG", "HE"]
_DEFAULT_RULES = ["c", "n", "v"]


def norgate_roll_dates(store: str, symbol: str) -> pd.DatetimeIndex:
    """Roll dates from a Norgate-built store: the first session on each new front
    contract, read from the `Delivery Month` column of the backadj parquet."""
    for layout in ("bars/futures/norgate", "prices"):   # marketdata, then pre-ADR-0007
        p = Path(store) / layout / f"{symbol}_backadj.parquet"
        if p.exists():
            break
    else:
        return pd.DatetimeIndex([])
    df = pd.read_parquet(p)
    if "Delivery Month" not in df.columns:
        return pd.DatetimeIndex([])
    idx = pd.to_datetime(df.index).tz_localize(None).normalize()
    dm = df["Delivery Month"].astype(str)
    is_new = dm.ne(dm.shift(1)) & dm.shift(1).notna()
    return pd.DatetimeIndex(idx[is_new.values])


def databento_roll_dates(client, dataset: str, root: str, rule: str) -> pd.DatetimeIndex:
    """Roll dates for one continuous roll rule: the first session on each new
    `instrument_id` in the `{root}.{rule}.0` daily bars."""
    end = (pd.Timestamp.now().normalize() - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    raw = _fetch(client, dataset, f"{root}.{rule}.0", "ohlcv-1d", GLBX_HISTORY_FLOOR, end)
    if raw is None or raw.empty or "instrument_id" not in raw.columns:
        return pd.DatetimeIndex([])
    idx = pd.to_datetime(raw.index).tz_convert(None).normalize()
    df = pd.DataFrame({"iid": raw["instrument_id"].values}, index=idx).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    is_new = df["iid"].ne(df["iid"].shift(1)) & df["iid"].shift(1).notna()
    return pd.DatetimeIndex(df.index[is_new.values])


def roll_dates_cached(client, dataset, root, rule, cache_dir: Path, refresh: bool):
    """databento_roll_dates with an on-disk cache. The API pull is the only slow part and
    roll dates are historical (a past roll never moves), so once pulled a (root, rule) is
    reused across tolerance sweeps and re-runs. `--refresh` forces a fresh pull."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    fp = cache_dir / f"{root}.{rule}.roll.json"
    if fp.exists() and not refresh:
        rolls = json.loads(fp.read_text()).get("rolls", [])
        return pd.DatetimeIndex(pd.to_datetime(rolls)), True   # (dates, from_cache)
    dates = databento_roll_dates(client, dataset, root, rule)
    fp.write_text(json.dumps({"root": root, "rule": rule, "dataset": dataset,
                              "rolls": [str(d.date()) for d in dates]}))
    return dates, False


def score(dbrolls: pd.DatetimeIndex, ngrolls: pd.DatetimeIndex, tol_days: int):
    """Of the Norgate rolls that fall inside databento's covered span, how many have a
    databento roll within tol_days? Norgate rolls before databento's history floor have no
    possible match and are excluded; databento rolls are NOT clipped, so a Norgate roll near
    the edge can still match a databento roll just outside the window.
    Returns (n_db, n_ng_in_span, matched, match_rate, median_abs_offset_days)."""
    if len(dbrolls) == 0 or len(ngrolls) == 0:
        return len(dbrolls), 0, 0, float("nan"), float("nan")
    db_sorted = dbrolls.sort_values()
    ng = ngrolls[(ngrolls >= db_sorted.min()) & (ngrolls <= db_sorted.max())]
    if len(ng) == 0:
        return len(db_sorted), 0, 0, float("nan"), float("nan")
    offsets, matched = [], 0
    for d in ng:
        nearest = min((abs((d - x).days) for x in db_sorted), default=None)
        if nearest is not None and nearest <= tol_days:
            matched += 1
            offsets.append(nearest)
    rate = matched / len(ng)
    med = float(pd.Series(offsets).median()) if offsets else float("nan")
    return len(db_sorted), len(ng), matched, rate, med


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--norgate-store", required=True,
                    help="A Norgate-built store — a $MARKETDATA_STORE since ADR-0007.")
    ap.add_argument("--symbols", nargs="+", default=_DEFAULT_SYMBOLS)
    ap.add_argument("--rules", nargs="+", default=_DEFAULT_RULES, help="c=calendar n=OI v=volume")
    ap.add_argument("--tol-days", nargs="+", type=int, default=[3],
                    help="Match window(s) in days. Pass several (e.g. 3 7 10) to sweep them "
                         "in one run — with the cache, extra tolerances cost no extra pulls.")
    ap.add_argument("--dataset", default="GLBX.MDP3")
    ap.add_argument("--cache-dir", default=".rollrule_cache",
                    help="Where to cache the pulled roll dates so re-runs and tolerance "
                         "sweeps skip the slow API pull (default ./.rollrule_cache).")
    ap.add_argument("--refresh", action="store_true", help="Ignore the cache and re-pull.")
    args = ap.parse_args()

    tols = sorted(set(args.tol_days))
    cache_dir = Path(args.cache_dir)
    roots = {s.internal: s.databento for s in all_symbols()}
    client = _client_from_env()

    winners = {}
    for sym in args.symbols:
        root = roots.get(sym)
        if not root:
            print(f"\n{sym}: no databento root in the registry (databento: null?) — skipping")
            continue
        ng = norgate_roll_dates(args.norgate_store, sym)
        if len(ng) == 0:
            print(f"\n{sym}: no Norgate roll dates found in {args.norgate_store} — skipping")
            continue
        print(f"\n{sym}  (databento root {root!r}, Norgate rolls total {len(ng)})")
        tol_hdr = "  ".join(f"rate@{t}d" for t in tols)
        print(f"  {'rule':5} {'db_rolls':>8} {'ng_ovl':>7}  {tol_hdr}  {'med|off|d':>9}")
        best = None  # (rule, rate_at_widest_tol, med) — judged on the widest tolerance
        for rule in args.rules:
            try:
                db, cached = roll_dates_cached(client, args.dataset, root, rule, cache_dir, args.refresh)
            except Exception as e:  # noqa: BLE001 — one bad rule should not sink the run
                print(f"  {rule:5} FETCH FAILED — {e}")
                continue
            rates = [score(db, ng, t) for t in tols]
            n_db, n_ng = rates[0][0], rates[0][1]
            rate_cells = "  ".join(f"{r[3]:>6.3f}" for r in rates)
            med = rates[-1][4]
            tag = "" if not cached else " (cached)"
            print(f"  {rule:5} {n_db:>8} {n_ng:>7}  {rate_cells}  {med:>9.1f}{tag}")
            wide = rates[-1][3]
            if wide == wide and (best is None or wide > best[1] or (wide == best[1] and med < best[2])):
                best = (rule, wide, med)
        if best:
            winners[sym] = best[0]
            print(f"  -> best match: rule '{best[0]}' (rate {best[1]:.3f} @ {tols[-1]}d, "
                  f"median offset {best[2]:.1f}d)")

    if winners:
        print("\n=== recommended roll rule per symbol ===")
        for sym, rule in winners.items():
            print(f"  {sym:5} {rule}")
        rules_used = set(winners.values())
        if len(rules_used) == 1:
            print(f"\nAll tested symbols favor the same rule: '{rules_used.pop()}'. "
                  f"If it beats '.n' broadly, switch the producer's _FEEDS root.")
        else:
            print("\nDifferent symbols favor different rules — a single global rule may not fit; "
                  "consider a per-symbol roll-rule field in the registry.")
    else:
        print("\nNo symbols scored — check DATABENTO_API_KEY and the Norgate store path.")
        sys.exit(1)


if __name__ == "__main__":
    main()
