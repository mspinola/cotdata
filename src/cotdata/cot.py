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
    """Weekly COT for an internal symbol OR a raw CFTC code. Empty if absent.

    If the resolved symbol declares hist_codes (predecessor exchange listings of
    the same contract), they're stitched in chronologically to fill gaps the
    primary code doesn't cover. The primary code wins on overlapping report dates,
    and the stitched-in rows are relabelled to the primary code so the series
    presents as a single contract to code-keyed consumers (e.g. CotIndexer).
    """
    sym = REGISTRY.get(name)
    if sym is None:                       # allow lookup by primary CFTC code, not just symbol
        sym = next((s for s in REGISTRY.values() if s.cftc_code == name), None)
    
    read_fn = store.read_cot_disagg if report == "disagg" else store.read_cot_legacy

    if sym is None or not sym.cftc_code:
        return read_fn(name)
    primary = read_fn(sym.cftc_code)
    if not sym.hist_codes:
        return primary
    frames = [primary]                    # primary first → wins de-duplication on overlaps
    for hc, scale in hist_code_scales(sym.hist_codes):
        h = read_fn(hc)
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
