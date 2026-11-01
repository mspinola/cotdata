"""Measure the Supplemental-against-Legacy-combined join on every covered market.

Reproducer for docs/analysis/2026-10-31-cit-legacy-combined-decomposition.md. Reads
``cot_supplemental/`` and ``cot_legacy_combined/`` from $COTDATA_STORE and prints, as
Markdown:

1. per market, how the two exact identities (open interest, non-reportable) hold and how
   many carve-outs came out negative (``cotdata.cit.check``);
2. the share of the index book sourced from each Legacy bucket, median per year, both
   sides, pooled over markets (the drift table of the 2026-08-03 measurements doc,
   recomputed from the store rather than from raw files);
3. per market, the median fraction of Legacy's commercial and non-commercial longs that is
   index money, full period and the last three years;
4. per market, how far the ex-index series sit from the Legacy series they are carved
   from: correlation of OI-normalised net levels and of 4-week changes, and sign agreement.

Descriptive only. Nothing here reads a price or renders a verdict.

    COTDATA_STORE=... python scripts/cit_legacy_decomposition.py
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from cotdata import cit, cot, store

MARKETS = ["ZC", "ZW", "KE", "ZS", "ZM", "ZL", "CT", "CC", "SB", "KC", "HE", "LE", "GF"]
ERAS = [2006, 2010, 2015, 2020, 2026]


def _md(df: pd.DataFrame, floatfmt: str = "{:.3f}") -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(str(c) for c in cols) + " |",
             "|" + "---|" * len(cols)]
    for _, row in df.iterrows():
        cells = []
        for v in row.tolist():
            if isinstance(v, float):
                cells.append("" if np.isnan(v) else floatfmt.format(v))
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> int:
    frames = {}
    rows = []
    for m in MARKETS:
        c = cit.check(m)
        rows.append(c)
        frames[m] = cit.decompose(m)
    chk = pd.DataFrame(rows).set_index("market")
    newest = max(f.index.max() for f in frames.values()).date()
    print("# Supplemental joined to Legacy combined: measurements\n")
    print(f"Store: {store.config.store_root()}  schema {store.schema_version()}  "
          f"newest shared report week: {newest}\n")

    print("## 1. The exact identities, per market\n")
    chk2 = chk.copy()
    chk2["oi_exact_pct"] = 100.0 * chk2["oi_exact"] / chk2["shared_weeks"]
    chk2["nonrept_exact_pct"] = 100.0 * chk2["nonrept_exact"] / chk2["shared_weeks"]
    first = {m: f.index.min().date() for m, f in frames.items()}
    last = {m: f.index.max().date() for m, f in frames.items()}
    chk2["first_shared"] = pd.Series(first)
    chk2["last_shared"] = pd.Series(last)
    print(_md(chk2[["supplemental_weeks", "legacy_combined_weeks", "shared_weeks",
                    "supplemental_only_weeks", "legacy_only_weeks", "oi_exact_pct",
                    "nonrept_exact_pct", "negative_carveouts", "min_carveout",
                    "first_shared", "last_shared"]].reset_index(), "{:.1f}"))
    print()

    allrows = pd.concat([f.assign(market=m) for m, f in frames.items()])
    allrows["year"] = allrows.index.year

    print("## 2. Where the index book comes from, median share per year, pooled\n")
    out = []
    for side in ("long", "short"):
        tot = allrows[f"cit_{side}"].replace(0, np.nan)
        sh = pd.DataFrame({
            "year": allrows["year"],
            "from_comm": allrows[f"cit_from_comm_{side}"] / tot,
            "from_noncomm": allrows[f"cit_from_noncomm_{side}"] / tot,
            "from_spread": allrows[f"cit_from_spread_{side}"] / tot,
        })
        med = sh.groupby("year").median(numeric_only=True)
        med.insert(0, "side", side)
        out.append(med.reset_index())
    print(_md(pd.concat(out), "{:.3f}"))
    print()

    print("## 3. Index fraction of Legacy's own buckets, per market (median of weekly ratios)\n")
    recent = allrows.index >= (allrows.index.max() - pd.DateOffset(years=3))
    rows = []
    for m in MARKETS:
        f = allrows[allrows["market"] == m]
        r = f[recent[allrows["market"] == m]]
        rows.append({
            "market": m,
            "comm_long_index_frac": (f["cit_from_comm_long"] / f["comm_long"]).median(),
            "comm_long_index_frac_3y": (r["cit_from_comm_long"] / r["comm_long"]).median(),
            "noncomm_long_index_frac": (f["cit_from_noncomm_long"] / f["noncomm_long"]).median(),
            "noncomm_long_index_frac_3y": (r["cit_from_noncomm_long"] / r["noncomm_long"]).median(),
            "cit_net_pct_oi": (100 * (f["cit_long"] - f["cit_short"]) / f["oi"]).median(),
            "cit_short_over_long": (f["cit_short"] / f["cit_long"].replace(0, np.nan)).median(),
        })
    print(_md(pd.DataFrame(rows), "{:.3f}"))
    print()

    print("## 4. Ex-index series against the Legacy series they are carved from\n")
    print("Net positions as a fraction of combined OI. `corr_level` is the Pearson correlation "
          "of the two weekly series, `corr_d4` of their 4-week changes, `sign_agree` the share "
          "of weeks where both nets have the same sign, `gap_pct_oi` the median absolute "
          "difference in percentage points of OI.\n")
    rows = []
    for m in MARKETS:
        f = allrows[allrows["market"] == m]
        for bucket in ("comm", "noncomm"):
            lg = (f[f"{bucket}_long"] - f[f"{bucket}_short"]) / f["oi"]
            ex = (f[f"{bucket}_long_nocit"] - f[f"{bucket}_short_nocit"]) / f["oi"]
            rows.append({
                "market": m, "bucket": bucket,
                "corr_level": lg.corr(ex),
                "corr_d4": lg.diff(4).corr(ex.diff(4)),
                "sign_agree": float((np.sign(lg) == np.sign(ex)).mean()),
                "gap_pct_oi": float((100 * (lg - ex).abs()).median()),
                "legacy_mean_pct_oi": float(100 * lg.mean()),
                "exindex_mean_pct_oi": float(100 * ex.mean()),
            })
    print(_md(pd.DataFrame(rows), "{:.3f}"))
    print()

    print("## 5. Store rows read\n")
    for m in MARKETS:
        s = cot.get_cot(m, report="supplemental")
        print(f"- {m}: supplemental {len(s)} rows {s.index.min().date()} to "
              f"{s.index.max().date()}; legacy combined "
              f"{rows_desc(cot.get_cot(m, report='legacy', combined=True))}")
    return 0


def rows_desc(df: pd.DataFrame) -> str:
    return f"{len(df)} rows {df.index.min().date()} to {df.index.max().date()}"


if __name__ == "__main__":
    sys.exit(main())
