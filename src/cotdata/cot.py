"""Consumer COT API. Reads weekly CFTC positioning the producer wrote.

NOTE: the positioning-INDEX computation and the trading signals stay in
cot-analyzer (CotIndexer / metrics) — cotdata owns only the raw data. This
returns the cleaned weekly COT table; the indexer builds indices on top.
"""
import re

import numpy as np
import pandas as pd

from . import store
from .registry import REGISTRY, hist_code_scales, sum_code_scales

_CODE_COL = "CFTC_Contract_Market_Code"   # Legacy schema contract-code column
_OI_COL = "Open_Interest_All"             # present in all four report schemas
# Columns denominated in CONTRACTS, the only ones a contract-size scale applies to.
# Trader counts, concentration ratios and percent-of-OI columns are invariant to the
# unit and must not be scaled (the hist scale once multiplied legacy Traders_*
# by the lumber 4.0 and the grain 0.2, so corn showed 72 traders in 1997).
_COUNT_RE = re.compile(r"^Open_Interest_All$|_Positions_|^Change_in_")
_PCT_RE = re.compile(r"^Pct_of_OI_(.+)_(Long|Short|Spread)_All$")
_CHANGE_RE = re.compile(r"^Change_in_(.+)_(Long|Short|Spread)_All$")


def _count_cols(df: pd.DataFrame) -> list:
    return [c for c in df.columns
            if _COUNT_RE.search(c) and pd.api.types.is_numeric_dtype(df[c])]


def _scale_counts(df: pd.DataFrame, scale) -> pd.DataFrame:
    """Multiply contract-denominated columns by `scale` (a float or a per-row Series)."""
    cols = _count_cols(df)
    if cols:
        df[cols] = df[cols].mul(scale, axis=0) if isinstance(scale, pd.Series) \
            else df[cols] * scale
    return df


def _row_scales(index: pd.DatetimeIndex, entries) -> pd.Series:
    """Per-row scale for one listing from its (scale, from_date) entries. The undated
    entry covers everything; each dated entry takes over from its date. Rows no entry
    covers come back NaN and are dropped by the caller."""
    s = pd.Series(np.nan, index=index, dtype=float)
    for scale, start in sorted(entries, key=lambda e: (e[1] is not None, e[1] or "")):
        if start is None:
            s[:] = scale
        else:
            s[index >= pd.Timestamp(start)] = scale
    return s


def _add_listings(base: pd.DataFrame, sym, read_fn) -> pd.DataFrame:
    """Add the symbol's parallel listings (sum_codes) to `base`, week by week.

    Contract-denominated columns are scaled into the primary's units and summed
    (weeks a listing reports alone extend the series). Trader counts take the max
    across listings, a lower bound on distinct traders. Percent-of-OI and
    Change_in_* columns are recomputed from the sums as floats. Concentration
    ratios and the text metadata stay the primary's, NaN where the primary has no
    row. The code column is the primary's throughout.
    """
    by_code = {}
    for code, scale, start in sum_code_scales(sym.sum_codes):
        by_code.setdefault(code, []).append((scale, start))
    extras = []
    for code, entries in by_code.items():
        f = read_fn(f"{sym.internal}_{code}")
        if f.empty:
            continue
        f = f[~f.index.duplicated(keep="first")].copy()
        sc = _row_scales(f.index, entries)
        f = _scale_counts(f[sc.notna()], sc.dropna())
        extras.append(f)
    if not extras:
        return base
    frames = ([base] if not base.empty else []) + extras
    idx = frames[0].index
    for f in frames[1:]:
        idx = idx.union(f.index)
    idx = idx.sort_values()

    out = base.reindex(idx)
    meta = [c for c in out.columns if not pd.api.types.is_numeric_dtype(out[c])
            and not c.startswith(("Change_in_", "Traders_"))]
    for f in extras:                      # text metadata from a listing where primary is absent
        out[meta] = out[meta].combine_first(f.reindex(idx)[meta]) if meta else out[meta]
    if _CODE_COL in out.columns:
        out[_CODE_COL] = sym.cftc_code

    counts = sorted({c for f in frames for c in _count_cols(f)
                     if not c.startswith("Change_in_")})
    total = sum(f.reindex(idx)[[c for c in counts if c in f.columns]]
                 .reindex(columns=counts).fillna(0.0) for f in frames)
    out[counts] = total

    traders = sorted({c for f in frames for c in f.columns if c.startswith("Traders_")})
    for c in traders:
        cols = [pd.to_numeric(f.reindex(idx)[c], errors="coerce") for f in frames if c in f.columns]
        out[c] = pd.concat(cols, axis=1).max(axis=1)

    for c in list(out.columns):
        if c == "Pct_of_Open_Interest_All":
            out[c] = 100.0
        elif (m := _PCT_RE.match(c)):
            pos = f"{m.group(1)}_Positions_{m.group(2)}_All"
            out[c] = 100.0 * out[pos] / out[_OI_COL] if pos in out.columns else np.nan
        elif c == "Change_in_Open_Interest_All":
            out[c] = out[_OI_COL].diff()
        elif (m := _CHANGE_RE.match(c)):
            pos = f"{m.group(1)}_Positions_{m.group(2)}_All"
            out[c] = out[pos].diff() if pos in out.columns else np.nan
    return out


def get_cot(name: str, report: str = "legacy") -> pd.DataFrame:
    """Read a symbol's weekly COT history with predecessor code stitching.

    name: the internal pipeline symbol, e.g. 'ES' or 'GC'.
    report: 'legacy' (default), 'disagg' (commodity futures only), 'tff' (financial
    futures only), or 'supplemental' (13 agricultural markets, index traders split out).

    Returns:
    DataFrame indexed by date. The columns depend on the `report` requested:
      - legacy: Open_Interest_All, NonComm_Positions_Long_All, Comm_Positions_Long_All, etc.
      - disagg: Open_Interest_All, Prod_Merc_Positions_Long_All, M_Money_Positions_Long_All, etc.
      - tff: Open_Interest_All, Dealer_Positions_Long_All, Lev_Money_Positions_Long_All, etc.
      - supplemental: Open_Interest_All, CIT_Positions_Long_All,
        NComm_Positions_Long_All_NoCIT, Comm_Positions_Long_All_NoCIT, etc.
    If no data exists, returns an empty DataFrame.

    'supplemental' is futures-and-options COMBINED and the other three are futures-only,
    so its Open_Interest_All is a different quantity for the same market and week. Do not
    difference or ratio across reports without accounting for that.
    """
    sym = REGISTRY.get(name)
    if sym is None:                       # allow lookup by primary CFTC code, not just symbol
        sym = next((s for s in REGISTRY.values() if s.cftc_code == name), None)

    read_fn = {
        "legacy": store.read_cot_legacy,
        "disagg": store.read_cot_disagg,
        "tff": store.read_cot_tff,
        "supplemental": store.read_cot_supplemental,
    }.get(report)
    if not read_fn:
        raise ValueError(f"Unknown report type: {report}. Expected 'legacy', 'disagg', "
                         f"'tff', or 'supplemental'")

    if sym is None or not sym.cftc_code:
        return read_fn(name)
    primary = read_fn(f"{sym.internal}_{sym.cftc_code}")
    stitched = _stitch_predecessors(primary, sym, read_fn) if sym.hist_codes else primary
    return _add_listings(stitched, sym, read_fn) if sym.sum_codes else stitched


def _stitch_predecessors(primary: pd.DataFrame, sym, read_fn) -> pd.DataFrame:
    """Splice hist_codes predecessors chronologically behind the primary code."""
    frames = [primary]                    # primary first → wins ties on overlap weeks
    for hc, scale in hist_code_scales(sym.hist_codes):
        h = read_fn(f"{sym.internal}_{hc}")
        if h.empty:
            continue
        h = h.copy()
        if scale != 1.0:                  # bridge a contract-size change (contract counts only)
            h = _scale_counts(h, scale)
        if _CODE_COL in h.columns:        # present predecessor rows under the primary code
            h[_CODE_COL] = sym.cftc_code
        frames.append(h)
    frames = [f for f in frames if not f.empty]
    if len(frames) < 2:                   # nothing to stitch (predecessor files absent,
        return primary                    # e.g. before the producer's next run): primary as-is
    combined = pd.concat(frames)
    # Overlap weeks (a hand-off between codes) keep the row with the larger SCALED
    # open interest, not the primary code. At the 2017 Russell hand-off the primary
    # (CME) reported 13k contracts against ICE's 567k for four weeks, and "primary
    # wins" printed that 2% slice of the market as the market. A stable sort keeps
    # concat order (primary first) as the tie-break, and frames without an OI column
    # degrade to exactly the old primary-wins behaviour.
    if _OI_COL in combined.columns:
        combined = combined.sort_values(_OI_COL, ascending=False, kind="stable",
                                        na_position="last")
    combined = combined[~combined.index.duplicated(keep="first")].sort_index()
    return combined
