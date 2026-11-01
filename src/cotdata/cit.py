"""Index traders against the Legacy report: the Supplemental joined to Legacy COMBINED.

The Supplemental (Commodity Index Trader) report is the Legacy report with one extra cut.
Its ``_NoCIT`` columns are Legacy's commercial, non-commercial and spreading buckets with
the index traders removed, and its open interest and non-reportable columns are Legacy's,
exactly, on the futures-and-options COMBINED basis (390/390 market-weeks in the 2026
control, 0/390 against futures-only). So for the 13 agricultural markets it covers, the
two reports joined on market and week give a partition neither publishes alone:

    Legacy bucket  =  bucket ex index (Supplemental ``_NoCIT``)  +  index traders from it

per side, for commercial, non-commercial and non-commercial spreading, with non-reportable
untouched (index traders are reportable by definition). Measured derivation:
``docs/analysis/2026-08-03-cit-supplemental-measurements.md`` sections 1 and 5.

Two things this module does NOT do:

- It never reads ``cot_legacy/``. Futures-only Legacy differs from the Supplemental by the
  whole option book, nothing lines up, and the join would be wrong on every row. The Legacy
  side is ``get_cot(name, "legacy", combined=True)``, which is why this needs a store with
  the combined domain (``cotdata-update --cot-legacy-combined``, in the store since
  ADR-0003).
- It does not read Legacy's spreading column, because the store does not keep it
  (``providers/cftc.TARGET_COLS`` is a 15-column slice). The index position carved out of
  spreading is therefore the RESIDUAL ``cit - from_comm - from_noncomm`` rather than an
  independent difference, which makes it exact by construction and uncheckable here.
  The three-way split was established from the raw files (the measurements doc), where
  it holds to rounding.

Rounding: a combined report rounds delta-weighted option equivalents to whole contracts
per category, so a difference of two published buckets can be off by one from the true
carve-out. The two identities that involve no option rounding on either side, open
interest and non-reportable, must hold exactly, and ``decompose`` refuses a market where
they do not: that is a basis, stitching or vintage error, never a market fact.
"""
from __future__ import annotations

import pandas as pd

from . import cot

# Legacy (combined) column -> Supplemental ex-index column -> output name of the carve-out.
_CARVED = (
    ("Comm_Positions_Long_All", "Comm_Positions_Long_All_NoCIT", "comm_long"),
    ("Comm_Positions_Short_All", "Comm_Positions_Short_All_NoCIT", "comm_short"),
    ("NonComm_Positions_Long_All", "NComm_Positions_Long_All_NoCIT", "noncomm_long"),
    ("NonComm_Positions_Short_All", "NComm_Positions_Short_All_NoCIT", "noncomm_short"),
)
# Published on both reports and expected to agree to the contract.
_EXACT = (
    ("Open_Interest_All", "Open_Interest_All", "oi"),
    ("NonRept_Positions_Long_All", "NonRept_Positions_Long_All", "nonrept_long"),
    ("NonRept_Positions_Short_All", "NonRept_Positions_Short_All", "nonrept_short"),
)
_CIT = {"long": "CIT_Positions_Long_All", "short": "CIT_Positions_Short_All"}
# CFTC's own header typo ("Postions"), preserved by the lossless parse.
_SPREAD_NOCIT = "NComm_Postions_Spread_All_NoCIT"

#: Output columns, in order. ``*_nocit`` and ``cit_*`` are the Supplemental's own numbers;
#: ``comm_*``, ``noncomm_*`` are Legacy combined as published; ``cit_from_*`` are the
#: carve-outs; ``*_residual`` are the exact identities and should read zero.
COLUMNS = [
    "oi",
    "cit_long", "cit_short",
    "comm_long", "comm_short", "noncomm_long", "noncomm_short",
    "comm_long_nocit", "comm_short_nocit", "noncomm_long_nocit", "noncomm_short_nocit",
    "spread_nocit", "nonrept_long", "nonrept_short",
    "cit_from_comm_long", "cit_from_comm_short",
    "cit_from_noncomm_long", "cit_from_noncomm_short",
    "cit_from_spread_long", "cit_from_spread_short",
    "oi_residual", "nonrept_long_residual", "nonrept_short_residual",
]


def decompose(name: str, *, strict: bool = True) -> pd.DataFrame:
    """Join a market's Supplemental rows to its Legacy COMBINED rows, week by week.

    name: the internal symbol (or primary CFTC code), one of the 13 Supplemental
    markets. Returns one row per week present in BOTH reports, columns ``COLUMNS``,
    indexed by report date. Weeks on one side only are dropped; ``check`` counts them.

    strict (default): raise ``ValueError`` when either report has no rows for the market,
    when the two share no week, or when open interest or non-reportable disagree on any
    shared week. Those are the identities that involve no option rounding, so a breach
    means the Legacy side is not the combined basis, a predecessor code was stitched over
    a Supplemental week, or the two are different vintages. ``strict=False`` returns the
    frame with the residual columns populated so the breach can be inspected.
    """
    supp = cot.get_cot(name, report="supplemental")
    legacy = cot.get_cot(name, report="legacy", combined=True)
    if strict:
        if supp.empty:
            raise ValueError(f"{name}: no Supplemental rows. The report covers 13 "
                             f"agricultural markets from 2006; see `cotdata-vintage coverage`.")
        if legacy.empty:
            raise ValueError(f"{name}: no Legacy COMBINED rows (cot_legacy_combined/). The "
                             f"join is only defined on that basis; fill it with "
                             f"`cotdata-update --cot-legacy-combined`.")
    if supp.empty or legacy.empty:
        return pd.DataFrame(columns=COLUMNS, index=pd.DatetimeIndex([]), dtype=float)

    supp = supp[~supp.index.duplicated(keep="first")]
    legacy = legacy[~legacy.index.duplicated(keep="first")]
    idx = supp.index.intersection(legacy.index).sort_values()
    if strict and len(idx) == 0:
        raise ValueError(f"{name}: the Supplemental ({supp.index.min().date()} to "
                         f"{supp.index.max().date()}) and Legacy combined "
                         f"({legacy.index.min().date()} to {legacy.index.max().date()}) "
                         f"share no report week")
    s = supp.loc[idx]
    lg = legacy.loc[idx]

    out = pd.DataFrame(index=idx)
    for lcol, scol, nm in _EXACT:
        out[nm] = pd.to_numeric(s[scol], errors="coerce").astype(float)
        out[f"{nm}_residual"] = pd.to_numeric(lg[lcol], errors="coerce").astype(float) - out[nm]
    for side, col in _CIT.items():
        out[f"cit_{side}"] = pd.to_numeric(s[col], errors="coerce").astype(float)
    for lcol, scol, nm in _CARVED:
        out[nm] = pd.to_numeric(lg[lcol], errors="coerce").astype(float)
        out[f"{nm}_nocit"] = pd.to_numeric(s[scol], errors="coerce").astype(float)
        out[f"cit_from_{nm}"] = out[nm] - out[f"{nm}_nocit"]
    out["spread_nocit"] = pd.to_numeric(s[_SPREAD_NOCIT], errors="coerce").astype(float)
    for side in ("long", "short"):
        out[f"cit_from_spread_{side}"] = (out[f"cit_{side}"]
                                          - out[f"cit_from_comm_{side}"]
                                          - out[f"cit_from_noncomm_{side}"])
    out = out[COLUMNS]

    if strict:
        for nm in ("oi", "nonrept_long", "nonrept_short"):
            bad = out[f"{nm}_residual"].ne(0.0)
            if bad.any():
                first = out.index[bad.values][0].date()
                raise ValueError(
                    f"{name}: {nm} differs between the Supplemental and Legacy combined on "
                    f"{int(bad.sum())} of {len(out)} shared weeks (first {first}, Legacy minus "
                    f"Supplemental {out.loc[bad, f'{nm}_residual'].iloc[0]:+.0f}). The two "
                    f"agree exactly on this column when both are the combined basis and the "
                    f"same vintage, so this is a basis, stitching or vintage error.")
    return out


def check(name: str) -> dict:
    """Describe how well the join holds for one market, without raising.

    Returns counts a caller can print or assert on: shared weeks, weeks present on one
    side only, the number of shared weeks where open interest and non-reportable match
    exactly, and the number of carve-outs that came out negative (a negative is a rounding
    artifact at most one contract deep when the true carve-out is zero, anything larger is
    a defect). Reads both reports once more; it is a diagnostic, not a hot path.
    """
    supp = cot.get_cot(name, report="supplemental")
    legacy = cot.get_cot(name, report="legacy", combined=True)
    si = supp.index[~supp.index.duplicated()] if not supp.empty else pd.DatetimeIndex([])
    li = legacy.index[~legacy.index.duplicated()] if not legacy.empty else pd.DatetimeIndex([])
    shared = si.intersection(li)
    out = {
        "market": name,
        "supplemental_weeks": int(len(si)),
        "legacy_combined_weeks": int(len(li)),
        "shared_weeks": int(len(shared)),
        "supplemental_only_weeks": int(len(si.difference(li))),
        "legacy_only_weeks": int(len(li.difference(si))),
        "oi_exact": 0, "nonrept_exact": 0,
        "negative_carveouts": 0, "min_carveout": None,
    }
    if len(shared) == 0:
        return out
    d = decompose(name, strict=False)
    out["oi_exact"] = int(d["oi_residual"].eq(0.0).sum())
    out["nonrept_exact"] = int((d["nonrept_long_residual"].eq(0.0)
                                & d["nonrept_short_residual"].eq(0.0)).sum())
    carve = d[[c for c in COLUMNS if c.startswith("cit_from_")]]
    out["negative_carveouts"] = int((carve < 0).sum().sum())
    out["min_carveout"] = float(carve.min().min())
    return out
