"""Consumer COT API. Reads weekly CFTC positioning the producer wrote.

NOTE: the positioning-INDEX computation and the trading signals stay in
cot-analyzer (CotIndexer / metrics) — cotdata owns only the raw data. This
returns the cleaned weekly COT table; the indexer builds indices on top.
"""
import pandas as pd

from . import store
from .registry import REGISTRY, hist_code_scales

_CODE_COL = "CFTC_Contract_Market_Code"   # Legacy schema contract-code column


def get_cot(name: str, report: str = "legacy") -> pd.DataFrame:
    """Read a symbol's weekly COT history with predecessor code stitching.

    name: the internal pipeline symbol, e.g. 'ES' or 'GC'.
    report: 'legacy' (default), 'disagg' (commodity futures only), or 'tff' (financial futures only).

    Returns:
    DataFrame indexed by date. The columns depend on the `report` requested:
      - legacy: Open_Interest_All, NonComm_Positions_Long_All, Comm_Positions_Long_All, etc.
      - disagg: Open_Interest_All, Prod_Merc_Positions_Long_All, M_Money_Positions_Long_All, etc.
      - tff: Open_Interest_All, Dealer_Positions_Long_All, Lev_Money_Positions_Long_All, etc.
    If no data exists, returns an empty DataFrame.
    """
    sym = REGISTRY.get(name)
    if sym is None:                       # allow lookup by primary CFTC code, not just symbol
        sym = next((s for s in REGISTRY.values() if s.cftc_code == name), None)

    read_fn = {
        "legacy": store.read_cot_legacy,
        "disagg": store.read_cot_disagg,
        "tff": store.read_cot_tff,
    }.get(report)
    if not read_fn:
        raise ValueError(f"Unknown report type: {report}. Expected 'legacy', 'disagg', or 'tff'")

    if sym is None or not sym.cftc_code:
        return read_fn(name)
    primary = read_fn(f"{sym.internal}_{sym.cftc_code}")
    if not sym.hist_codes:
        return primary
    frames = [primary]                    # primary first → wins de-duplication on overlaps
    for hc, scale in hist_code_scales(sym.hist_codes):
        h = read_fn(f"{sym.internal}_{hc}")
        if h.empty:
            continue
        h = h.copy()
        if scale != 1.0:                  # bridge a contract-size change (position/OI counts)
            num = h.select_dtypes("number").columns
            h[num] = h[num] * scale
        if _CODE_COL in h.columns:        # present predecessor rows under the primary code
            h[_CODE_COL] = sym.cftc_code
        frames.append(h)
    frames = [f for f in frames if not f.empty]
    if not frames:
        return primary
    combined = pd.concat(frames)
    combined = combined[~combined.index.duplicated(keep="first")].sort_index()
    return combined
