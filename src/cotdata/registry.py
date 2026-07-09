"""The single symbol registry: internal ↔ Norgate ↔ CFTC code ↔ asset class ↔
positioning-index lookback. Replaces the scattered maps (CotSymbolCodeMap, the
databento_mapping, the Pine `lb` switch).

Seeded with the equities to start. TODO: populate all 41 by merging
cot-analyzer's CotSymbolCodeMap (cftc_code) with the per-symbol lookback switch
from the CMR Pine strategy. lookback_weeks below are the CMR values.
"""
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class Symbol:
    internal: str                 # pipeline root, e.g. "ES"
    norgate: str                  # Norgate continuous symbol, e.g. "&ES"
    asset_class: str              # "Equities", "Metals", "Grains", ...
    lookback_weeks: int           # CMR positioning-index lookback
    is_equity: bool               # equity-index rule vs commodity rule (is_setup / neutral)
    cftc_code: Optional[str] = None  # CFTC contract code for COT (TODO: fill from CotSymbolCodeMap)


REGISTRY: Dict[str, Symbol] = {
    "ES":  Symbol("ES",  "&ES",  "Equities", 27, True),
    "NQ":  Symbol("NQ",  "&NQ",  "Equities", 27, True),
    "YM":  Symbol("YM",  "&YM",  "Equities", 27, True),
    "RTY": Symbol("RTY", "&RTY", "Equities", 27, True),
    # --- TODO: metals (GC 24, SI 24, HG 86, PL 16, PA 220), energies (CL 40,
    #     NG 68, RB 40, HO 40), grains (ZC 86, ZS 126, ZW 10, ZM 30, ZL 34),
    #     currencies (6E 90, 6A 80, 6B 30, ...), softs, livestock, crypto, rates.
}


def symbol(internal: str) -> Symbol:
    return REGISTRY[internal]


def all_symbols() -> List[Symbol]:
    return list(REGISTRY.values())
