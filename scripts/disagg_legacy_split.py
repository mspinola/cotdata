"""Measure whether the Disaggregated report is an exact split of the Legacy buckets.

Reproducer for docs/design/cross-report-comparability.md section 3. Reads ``cot_disagg/``
and ``cot_legacy/`` (futures-only, the default basis) from $COTDATA_STORE through
``cotdata.get_cot`` for every registry symbol that has a Disaggregated series, joins the two
on report week, and prints, per market, the share of shared weeks on which each identity
holds to the contract:

    oi       Open_Interest_All, Disaggregated == Legacy
    nr_l/s   NonRept long / short, Disaggregated == Legacy
    comm_l   Prod_Merc long + Swap long + Swap spread == Legacy Comm long
    comm_s   Prod_Merc short + Swap short + Swap spread == Legacy Comm short
    nc_l     M_Money long + Other_Rept long == Legacy NonComm long
    nc_s     M_Money short + Other_Rept short == Legacy NonComm short
    nc_sp    M_Money spread + Other_Rept spread == Legacy NonComm spread, where the Legacy
             side is the closure residual OI - Comm long - NonComm long - NonRept long,
             because the store's Legacy table carries no non-commercial spreading column
    all      every position identity above on the same week
    tdr_l    trader counts, Prod_Merc + Swap long traders == Legacy Comm long traders.
             Expected to FAIL: a trader is counted once per Legacy bucket but may appear
             in more than one Disaggregated category (swap long and swap spread, say), so
             counts do not add. Printed as the contrast, not as an identity.

Then lists every week on which a position identity fails, and re-tests those weeks against
the primary code's own Legacy file (``store.read_cot_legacy``, no predecessor stitching), to
separate a report fact from which CFTC code ``get_cot`` chose for an overlap week.
Descriptive only, reads no price.

With ``--combined`` it runs the same identities on the futures-and-options combined series
(``cot_disagg_combined/`` against ``cot_legacy_combined/``), where they close only to the
rounding of delta-weighted option equivalents, so read ``max_abs`` there rather than the
exact-match share.

    COTDATA_STORE=... python scripts/disagg_legacy_split.py
    COTDATA_STORE=... python scripts/disagg_legacy_split.py --combined
"""
from __future__ import annotations

import sys

import pandas as pd

from cotdata import get_cot, registry, store

POSITIONS = ["comm_l", "comm_s", "nc_l", "nc_s", "nc_sp"]


def _n(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(df[col], errors="coerce")


def identities(sym: str, leg: pd.DataFrame | None = None, combined: bool = False,
               residuals: bool = False) -> pd.DataFrame | None:
    """Per-week booleans for every identity, or None when the symbol has no Disaggregated.

    ``leg`` overrides the Legacy side (default: ``get_cot(sym, "legacy")``, the stitched
    series), so a failing week can be re-tested against the primary code's own file.
    ``residuals=True`` returns the signed Disaggregated-minus-Legacy residuals instead.
    """
    try:
        d = get_cot(sym, "disagg", combined=combined)
    except (FileNotFoundError, KeyError, ValueError):
        return None
    if d is None or d.empty:
        return None
    if leg is None:
        leg = get_cot(sym, "legacy", combined=combined)
    idx = d.index.intersection(leg.index)
    d, leg = d.loc[idx].sort_index(), leg.loc[idx].sort_index()

    oi = _n(leg, "Open_Interest_All")
    cl, cs = _n(leg, "Comm_Positions_Long_All"), _n(leg, "Comm_Positions_Short_All")
    nl, ns = _n(leg, "NonComm_Positions_Long_All"), _n(leg, "NonComm_Positions_Short_All")
    rl, rs = _n(leg, "NonRept_Positions_Long_All"), _n(leg, "NonRept_Positions_Short_All")
    nc_spread = oi - cl - nl - rl                       # Legacy has no spreading column

    swap_sp = _n(d, "Swap__Positions_Spread_All")
    res = pd.DataFrame(index=idx)                       # Disaggregated side minus Legacy side
    res["oi"] = _n(d, "Open_Interest_All") - oi
    res["nr_l"] = _n(d, "NonRept_Positions_Long_All") - rl
    res["nr_s"] = _n(d, "NonRept_Positions_Short_All") - rs
    res["comm_l"] = (_n(d, "Prod_Merc_Positions_Long_All") + _n(d, "Swap_Positions_Long_All")
                     + swap_sp) - cl
    res["comm_s"] = (_n(d, "Prod_Merc_Positions_Short_All") + _n(d, "Swap__Positions_Short_All")
                     + swap_sp) - cs
    res["nc_l"] = (_n(d, "M_Money_Positions_Long_All")
                   + _n(d, "Other_Rept_Positions_Long_All")) - nl
    res["nc_s"] = (_n(d, "M_Money_Positions_Short_All")
                   + _n(d, "Other_Rept_Positions_Short_All")) - ns
    res["nc_sp"] = (_n(d, "M_Money_Positions_Spread_All")
                    + _n(d, "Other_Rept_Positions_Spread_All")) - nc_spread
    res["tdr_l"] = (_n(d, "Traders_Prod_Merc_Long_All") + _n(d, "Traders_Swap_Long_All")
                    ) - _n(leg, "Traders_Comm_Long_All")
    if residuals:
        return res
    out = res == 0
    out["all"] = out[POSITIONS].all(axis=1)
    out = out[[c for c in res.columns if c != "tdr_l"] + ["all", "tdr_l"]]
    return out


def main() -> int:
    combined = "--combined" in sys.argv[1:]
    rows, fails = [], []
    for s in registry.all_symbols():
        ids = identities(s.internal, combined=combined)
        if ids is None or ids.empty:
            continue
        row = {"sym": s.internal, "class": s.asset_class, "weeks": len(ids),
               "from": ids.index.min().date(), "to": ids.index.max().date()}
        row.update({k: round(100 * ids[k].mean(), 2) for k in ids.columns})
        res = identities(s.internal, combined=combined, residuals=True)
        row["max_abs"] = int(res[POSITIONS].abs().max().max())
        rows.append(row)
        bad = ids.index[~ids["all"]]
        if len(bad) and not combined:
            # Re-test against the primary code's own Legacy file, no predecessor stitching:
            # does the failure belong to the reports or to which code get_cot picked?
            raw = store.read_cot_legacy(f"{s.internal}_{s.cftc_code}")
            same = identities(s.internal, raw)
            fails.append((s.internal, bad, ids.loc[bad, "oi"],
                          same.loc[same.index.intersection(bad), "all"]))
    if not rows:
        print("no Disaggregated series found; is COTDATA_STORE set?", file=sys.stderr)
        return 1
    t = pd.DataFrame(rows).sort_values(["class", "sym"])
    basis = "futures-and-options combined" if combined else "futures-only"
    print(f"Store: {store.config.store_root()}  schema {store.schema_version()}  "
          f"basis: {basis}\n")
    print("Percent of shared report weeks on which each identity holds exactly "
          "(max_abs: largest |residual| in contracts over the position identities):\n")
    print(t.to_string(index=False))
    print(f"\nmarkets: {len(t)}, all position identities 100% on "
          f"{int((t['all'] == 100).sum())}")
    for sym, bad, oi_ok, same in fails:
        print(f"\n{sym}: {len(bad)} failing weeks, {bad.min().date()} to {bad.max().date()}; "
              f"open interest also disagrees on {int((~oi_ok).sum())} of them. Against the "
              f"primary code's own Legacy file (no hist_codes stitch) every position identity "
              f"holds on {int(same.sum())} of {len(same)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
