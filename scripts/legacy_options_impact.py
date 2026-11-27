"""Measure what the options book changes in a Legacy positioning read, every market.

Reproducer for docs/analysis/2026-11-27-legacy-options-impact.md. Reads ``cot_legacy/``
(futures-only) and ``cot_legacy_combined/`` (futures-and-options combined) from
$COTDATA_STORE and prints, as Markdown:

1. per market, the options share of combined open interest, the size of the commercial
   options position against the commercial futures position, and how far a rolling
   positioning index of commercial net moves between the two bases;
2. the same index gap split by mechanism: on raw net (no denominator), where only the
   options POSITION can move it, and on net over open interest, where the options share
   of the DENOMINATOR moves it too;
3. the non-commercial bucket, same counts;
4. gold in detail: options share by year, when the large gaps happened, the last twelve
   report weeks, and how a percentile-rank index compares with the min-max one.

The index is the rolling min-max range percentage, ``100 * (x - min) / (max - min)`` over
a window of ``lookback + 1`` rows, which is the formula of
``cotmetrics.indicators.rolling_cot_index``. It is restated here rather than imported
because cotdata sits upstream of cotmetrics.

Descriptive only. Nothing here reads a price or renders a verdict.

    COTDATA_STORE=... python scripts/legacy_options_impact.py
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from cotdata import cot, store
from cotdata.registry import REGISTRY

START = pd.Timestamp("2016-01-01")
LOOKBACKS = (52, 156)
EXTREME_HI, EXTREME_LO = 90.0, 10.0
INSIDE_LO, INSIDE_HI = 20.0, 80.0
BUCKETS = {
    "comm": ("Comm_Positions_Long_All", "Comm_Positions_Short_All"),
    "noncomm": ("NonComm_Positions_Long_All", "NonComm_Positions_Short_All"),
}
OI = "Open_Interest_All"


def _md(df: pd.DataFrame, floatfmt: str = "{:.1f}") -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(str(c) for c in cols) + " |", "|" + "---|" * len(cols)]
    for row in df.itertuples(index=False):     # itertuples keeps each column's own dtype
        cells = []
        for v in row:
            if isinstance(v, (float, np.floating)):
                cells.append("" if np.isnan(v) else floatfmt.format(v))
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def index(s: pd.Series, lookback: int) -> pd.Series:
    """Rolling min-max index, 0 to 100, over lookback + 1 rows. NaN on a flat window."""
    lo = s.rolling(lookback + 1).min()
    hi = s.rolling(lookback + 1).max()
    return 100.0 * (s - lo) / (hi - lo).replace(0.0, np.nan)


def prank(s: pd.Series, lookback: int) -> pd.Series:
    """Percentile-rank index: share of the prior lookback rows strictly below the current."""
    return s.rolling(lookback + 1).apply(lambda x: 100.0 * (x[:-1] < x[-1]).mean(), raw=True)


def strict_disagreement(a: pd.Series, b: pd.Series) -> pd.Series:
    """One basis at an extreme while the other sits clearly inside the range.

    A plain "extreme on one, not on the other" count is dominated by boundary flicker
    (91 against 89). This counts only weeks where the two would be read differently.
    """
    ea = (a >= EXTREME_HI) | (a <= EXTREME_LO)
    eb = (b >= EXTREME_HI) | (b <= EXTREME_LO)
    return (ea & b.between(INSIDE_LO, INSIDE_HI)) | (eb & a.between(INSIDE_LO, INSIDE_HI))


def load(name: str) -> tuple[pd.DataFrame, pd.DataFrame] | None:
    f = cot.get_cot(name, report="legacy")
    c = cot.get_cot(name, report="legacy", combined=True)
    if f.empty or c.empty:
        return None
    f = f[~f.index.duplicated(keep="first")].sort_index()
    c = c[~c.index.duplicated(keep="first")].sort_index()
    ix = f.index.intersection(c.index)
    if int((ix >= START).sum()) < 260 or int((ix < START).sum()) < max(LOOKBACKS) + 1:
        return None                     # need five years to judge and a warm 3y window
    return f.loc[ix], c.loc[ix]


def nets(f: pd.DataFrame, c: pd.DataFrame, bucket: str) -> tuple[pd.Series, pd.Series]:
    long_col, short_col = BUCKETS[bucket]
    return ((f[long_col] - f[short_col]).astype(float),
            (c[long_col] - c[short_col]).astype(float))


def measure(name: str, f: pd.DataFrame, c: pd.DataFrame) -> dict:
    foi, coi = f[OI].astype(float), c[OI].astype(float)
    k = f.index >= START
    share = (coi - foi) / coi
    out = {"market": name, "weeks": int(k.sum()),
           "opt_share_med": 100 * float(share[k].median()),
           "opt_share_min": 100 * float(share[k].min()),
           "opt_share_max": 100 * float(share[k].max()),
           "opt_share_now": 100 * float(share.iloc[-1])}
    for bucket in BUCKETS:
        nf, nc = nets(f, c, bucket)
        opt = nc - nf                      # the options-only net, futures-equivalent contracts
        out[f"{bucket}_optnet_pct_futnet"] = 100 * float(
            (opt / nf.abs().replace(0.0, np.nan))[k].median())
        out[f"{bucket}_optnet_same_sign_pct"] = 100 * float(
            (np.sign(opt) == np.sign(nf))[k].mean())
        for lb in LOOKBACKS:
            for tag, a, b in (("raw", nf, nc), ("norm", nf / foi, nc / coi)):
                ia, ib = index(a, lb), index(b, lb)
                gap = (ib - ia)[k].dropna().abs()
                key = f"{bucket}_{tag}{lb}"
                out[f"{key}_med"] = float(gap.median())
                out[f"{key}_p95"] = float(gap.quantile(0.95))
                out[f"{key}_strict"] = int(strict_disagreement(ia[k], ib[k]).sum())
                out[f"{key}_now_fut"] = float(ia.iloc[-1])
                out[f"{key}_now_comb"] = float(ib.iloc[-1])
    return out


def gold_detail() -> None:
    loaded = load("GC")
    if loaded is None:
        print("GC not in the store on both bases; section skipped.\n")
        return
    f, c = loaded
    foi, coi = f[OI].astype(float), c[OI].astype(float)
    nf, nc = nets(f, c, "comm")
    g = pd.DataFrame({
        "opt_share_pct": 100 * (coi - foi) / coi,
        "comm_net_fut": nf, "comm_opt_net": nc - nf,
        "net_pct_oi_fut": 100 * nf / foi, "net_pct_oi_comb": 100 * nc / coi,
        "idx156_fut": index(nf / foi, 156), "idx156_comb": index(nc / coi, 156),
        "idx52_fut": index(nf / foi, 52), "idx52_comb": index(nc / coi, 52),
        "raw156_fut": index(nf, 156), "raw156_comb": index(nc, 156),
        "prank156_fut": prank(nf / foi, 156), "prank156_comb": prank(nc / coi, 156),
    })
    gg = g[g.index >= START]
    gap = gg["idx156_comb"] - gg["idx156_fut"]

    print("### 4a. Options share of combined open interest, and the large gaps, by year\n")
    by = pd.DataFrame({
        "opt_share_median_pct": gg["opt_share_pct"].groupby(gg.index.year).median(),
        "comm_opt_net_median": gg["comm_opt_net"].groupby(gg.index.year).median(),
        "comm_fut_net_median": gg["comm_net_fut"].groupby(gg.index.year).median(),
        "weeks_gap_over_20": (gap.abs() > 20).groupby(gap.index.year).sum().astype("int64"),
        "mean_gap_those_weeks": gap[gap.abs() > 20].groupby(gap[gap.abs() > 20].index.year).mean(),
    })
    by.insert(0, "year", [str(y) for y in by.index])
    print(_md(by))
    print()
    print(f"Weeks with a 3-year normalized index gap over 20 points: "
          f"{int((gap.abs() > 20).sum())} of {len(gap.dropna())}.")
    raw_gap = (gg["raw156_comb"] - gg["raw156_fut"]).abs()
    print(f"Same on raw net: {int((raw_gap > 20).sum())}; largest raw gap {raw_gap.max():.1f}.")
    print(f"Correlation of the 13-week change in options share with the 13-week change in "
          f"the gap (combined minus futures-only): "
          f"{gg['opt_share_pct'].diff(13).corr(gap.diff(13)):.2f}\n")

    print("### 4b. The last twelve report weeks\n")
    tail = gg.tail(12).copy()
    tail.insert(0, "week", [d.date().isoformat() for d in tail.index])
    print(_md(tail[["week", "opt_share_pct", "comm_net_fut", "comm_opt_net", "net_pct_oi_fut",
                    "net_pct_oi_comb", "idx156_fut", "idx156_comb", "idx52_fut", "idx52_comb",
                    "prank156_fut", "prank156_comb"]]))
    print()

    print("### 4c. Min-max against percentile rank, 3-year window, since 2016\n")
    pg = (gg["prank156_comb"] - gg["prank156_fut"]).abs()
    mg = gap.abs()
    cmp = pd.DataFrame([
        {"index": "min-max (cotmetrics)", "median_gap": mg.median(), "p95_gap": mg.quantile(0.95),
         "max_gap": mg.max(), "pct_weeks_gap_over_20": 100 * float((mg > 20).mean())},
        {"index": "percentile rank", "median_gap": pg.median(), "p95_gap": pg.quantile(0.95),
         "max_gap": pg.max(), "pct_weeks_gap_over_20": 100 * float((pg > 20).mean())},
    ])
    print(_md(cmp))
    print()


def main() -> int:
    rows, skipped = [], []
    newest = None
    for name in sorted(REGISTRY):
        loaded = load(name)
        if loaded is None:
            skipped.append(name)
            continue
        f, c = loaded
        newest = f.index.max() if newest is None else max(newest, f.index.max())
        rows.append(measure(name, f, c))
    if not rows:
        print("No market has both Legacy bases in this store.", file=sys.stderr)
        return 1
    df = pd.DataFrame(rows).set_index("market")

    print("# What the options book changes in a Legacy positioning read: measurements\n")
    print(f"Store: {store.config.store_root()}  schema {store.schema_version()}  "
          f"newest report week: {newest.date()}  markets: {len(df)}  "
          f"judged from {START.date()}\n")
    if skipped:
        print(f"Skipped (not on both bases, or too short): {', '.join(skipped)}\n")

    order = df.sort_values(["comm_norm156_strict", "comm_norm156_med"], ascending=False).index

    print("## 1. Commercial net, 3-year index, normalized by open interest\n")
    t = df.loc[order, ["weeks", "opt_share_med", "opt_share_min", "opt_share_max", "opt_share_now",
                       "comm_optnet_pct_futnet", "comm_optnet_same_sign_pct", "comm_norm156_med", "comm_norm156_p95",
                       "comm_norm156_strict", "comm_norm156_now_fut", "comm_norm156_now_comb"]]
    print(_md(t.reset_index()))
    print()

    print("## 2. The same gap by mechanism: weeks of strict disagreement, commercial net\n")
    t = df.loc[order, ["comm_norm156_strict", "comm_raw156_strict", "comm_norm52_strict",
                       "comm_raw52_strict", "comm_norm156_p95", "comm_raw156_p95",
                       "comm_norm52_p95", "comm_raw52_p95",
                       "comm_norm52_now_fut", "comm_norm52_now_comb"]]
    print(_md(t.reset_index()))
    print()

    print("## 3. Non-commercial net, same counts\n")
    t = df.loc[order, ["noncomm_optnet_pct_futnet", "noncomm_norm156_med", "noncomm_norm156_p95",
                       "noncomm_norm156_strict", "noncomm_raw156_strict", "noncomm_norm52_strict",
                       "noncomm_raw52_strict", "noncomm_norm156_now_fut", "noncomm_norm156_now_comb"]]
    print(_md(t.reset_index()))
    print()

    print("## 4. Gold in detail\n")
    gold_detail()

    tot = df[[c for c in df.columns if c.endswith("_strict")]].sum()
    print("## 5. Totals across markets\n")
    print(f"- market-weeks judged: {int(df['weeks'].sum())}")
    for col, v in tot.items():
        print(f"- {col}: {int(v)}")
    for thr in (0, 4, 10):
        n = int((df["comm_norm156_strict"] > thr).sum())
        print(f"- markets with more than {thr} strict weeks (commercial, normalized, 3-year): {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
