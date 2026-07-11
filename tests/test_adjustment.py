"""Diagnose Norgate price adjustment: is plain '&ES' back-adjusted or unadjusted?

Back-adjusted continuous = gap-free at rolls (contracts stitched with an offset).
Unadjusted continuous    = shows the real calendar-spread GAP at every roll.

A close-based CMR stop on UNADJUSTED data would false-trigger on roll gaps, so we
need the back-adjusted series. This checks '&ES' vs '&ES_CCB' and measures the
overnight move at roll dates (where Delivery Month changes).

Run on Windows:  python test_adjustment.py
"""
import numpy as np
import pandas as pd
import pytest

# Producer-only diagnostic: norgatedata is the Windows Norgate SDK. Skip cleanly
# under pytest on non-producer machines (Mac/Linux/CI) instead of erroring
# collection; on the producer it's installed, so this returns the real module.
norgatedata = pytest.importorskip("norgatedata")

pd.set_option("display.width", 140)
pd.set_option("display.max_columns", 20)


def fetch(sym):
    df = norgatedata.price_timeseries(
        sym,
        padding_setting=norgatedata.PaddingType.NONE,
        timeseriesformat="pandas-dataframe",
        start_date="2015-01-01",
    )
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    return df


def roll_gap_report(label, df):
    """At each Delivery Month change, report the overnight Close move vs typical range."""
    if "Delivery Month" not in df.columns:
        print(f"  {label}: no Delivery Month column — can't locate rolls")
        return
    dm = df["Delivery Month"]
    roll_mask = dm.ne(dm.shift()) & dm.shift().notna()
    rolls = df.index[roll_mask]
    prev_close = df["Close"].shift(1)
    overnight = (df["Close"] - prev_close).abs()
    daily_range = (df["High"] - df["Low"])
    typ_range = daily_range.median()
    roll_gaps = overnight[roll_mask]
    print(f"  {label}: {len(rolls)} rolls | median roll overnight move = {roll_gaps.median():.2f} pts "
          f"({roll_gaps.median() / typ_range:.2f}x typical daily range {typ_range:.2f})")
    print(f"       max roll move = {roll_gaps.max():.2f} pts on {roll_gaps.idxmax().date() if len(roll_gaps) else 'n/a'}")
    # show a few biggest roll gaps
    biggest = roll_gaps.sort_values(ascending=False).head(5)
    for dt, g in biggest.items():
        print(f"         {dt.date()}: overnight {g:.2f} pts (Close {prev_close[dt]:.2f} -> {df['Close'][dt]:.2f}, DM {int(dm.shift()[dt])}->{int(dm[dt])})")


def main():
    print("=" * 70)
    print("1. Which ES continuous symbols exist?")
    print("=" * 70)
    try:
        cont = norgatedata.database_symbols("Continuous Futures")
        es_syms = [s for s in cont if s.lstrip("&").upper().startswith("ES")]
        print("  ES-related continuous symbols:", es_syms)
    except Exception as e:
        print("  database_symbols failed:", e)

    print()
    print("=" * 70)
    print("2. Fetch '&ES' and '&ES_CCB', compare")
    print("=" * 70)
    a = fetch("&ES")
    print(f"  &ES     : {len(a)} bars {a.index.min().date()}..{a.index.max().date()}")
    try:
        b = fetch("&ES_CCB")
        print(f"  &ES_CCB : {len(b)} bars {b.index.min().date()}..{b.index.max().date()}")
        join = a[["Close"]].join(b[["Close"]], rsuffix="_CCB", how="inner")
        diff = (join["Close"] - join["Close_CCB"]).abs()
        print(f"  Close differs on {int((diff > 0.01).sum())}/{len(join)} shared days "
              f"(max diff {diff.max():.2f} pts)")
        print("  → if they differ, they are DIFFERENT series (one adj, one unadj)")
        have_ccb = True
    except Exception as e:
        print(f"  &ES_CCB : NOT AVAILABLE — {e}")
        have_ccb = False

    print()
    print("=" * 70)
    print("3. Roll-gap test (the decisive check)")
    print("=" * 70)
    print(" Back-adjusted → roll overnight move ≈ typical daily range (stitched).")
    print(" Unadjusted    → roll overnight move ≫ typical (real calendar-spread gap).")
    print()
    roll_gap_report("&ES    ", a)
    if have_ccb:
        roll_gap_report("&ES_CCB", b)

    print()
    print("=" * 70)
    print("VERDICT")
    print("=" * 70)
    print(" If '&ES' roll moves are ≫ typical range → '&ES' is UNADJUSTED; switch the")
    print(" producer's backadj symbol to '&ES_CCB'. If '&ES' roll moves ≈ typical →")
    print(" '&ES' is already back-adjusted and the current producer is fine.")

if __name__ == "__main__":
    main()
