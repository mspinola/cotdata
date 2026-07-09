"""The single symbol registry: internal ↔ Norgate ↔ CFTC code ↔ asset class ↔
positioning-index lookback. Replaces the scattered maps (CotSymbolCodeMap, the
databento_mapping, the Pine `lb` switch).

Sources:
  • cftc_code      — cot-analyzer CotSymbolCodeMap
  • lookback_weeks — CMR "COT Squeeze" Pine `lb` switch
  • asset_class    — CotIndexer asset classes
  • norgate        — '&' + CME root (VERIFY a few against Norgate's symbol
                     directory; some may differ, e.g. currencies/lumber).
is_equity is True only for the four equity indices (drives the is_setup /
COT-neutral equity rule vs the commodity rule).
"""
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class Symbol:
    internal: str                 # pipeline root, e.g. "ES"
    norgate: str                  # Norgate continuous symbol, e.g. "&ES"
    asset_class: str
    lookback_weeks: int           # CMR positioning-index lookback
    is_equity: bool
    cftc_code: Optional[str] = None


def _s(internal, asset_class, lb, cftc, is_eq=False, norgate=None):
    return Symbol(internal, norgate or f"&{internal}", asset_class, lb, is_eq, cftc)


REGISTRY: Dict[str, Symbol] = {s.internal: s for s in [
    # ── Equities (is_equity=True) ────────────────────────────────────────────
    _s("ES",  "Equities", 27, "13874A", is_eq=True),
    _s("NQ",  "Equities", 27, "209742", is_eq=True),
    _s("YM",  "Equities", 27, "124603", is_eq=True),
    _s("RTY", "Equities", 27, "239742", is_eq=True),
    # ── Metals ───────────────────────────────────────────────────────────────
    _s("GC",  "Metals", 24,  "088691"),
    _s("SI",  "Metals", 24,  "084691"),
    _s("HG",  "Metals", 86,  "085692"),
    _s("PL",  "Metals", 16,  "076651"),
    _s("PA",  "Metals", 220, "075651"),
    # ── Energies ─────────────────────────────────────────────────────────────
    _s("CL",  "Energies", 40, "067651"),
    _s("RB",  "Energies", 40, "111659"),
    _s("HO",  "Energies", 40, "022651"),
    _s("NG",  "Energies", 68, "023651"),
    # ── Grains ───────────────────────────────────────────────────────────────
    _s("ZC",  "Grains", 86,  "002602"),
    _s("ZS",  "Grains", 126, "005602"),
    _s("ZM",  "Grains", 30,  "026603"),
    _s("ZL",  "Grains", 34,  "007601"),
    _s("ZW",  "Grains", 10,  "001602"),
    # ── Currencies ───────────────────────────────────────────────────────────
    _s("6E",  "Currencies", 90, "099741"),
    _s("6A",  "Currencies", 80, "232741"),
    _s("6B",  "Currencies", 30, "096742"),
    _s("6C",  "Currencies", 30, "090741"),
    _s("6J",  "Currencies", 30, "097741"),
    _s("6S",  "Currencies", 30, "092741"),
    _s("6M",  "Currencies", 26, "095741"),
    _s("6N",  "Currencies", 26, "112741"),
    # ── Fixed Income ─────────────────────────────────────────────────────────
    _s("ZN",  "Fixed Income", 97, "043602"),
    _s("ZT",  "Fixed Income", 97, "042601"),
    _s("ZF",  "Fixed Income", 97, "044601"),
    _s("ZB",  "Fixed Income", 97, "020601"),
    # ── Softs ────────────────────────────────────────────────────────────────
    _s("SB",  "Softs", 128, "080732"),
    _s("CT",  "Softs", 40,  "033661"),
    _s("CC",  "Softs", 10,  "073732"),
    _s("KC",  "Softs", 8,   "083731"),
    _s("OJ",  "Softs", 8,   "040701"),
    _s("LBR", "Softs", 26,  "058644"),
    # ── Live Stock ───────────────────────────────────────────────────────────
    _s("LE",  "Live Stock", 13, "057642"),
    _s("HE",  "Live Stock", 13, "054642"),
    _s("GF",  "Live Stock", 26, "061641"),
    # ── Crypto ───────────────────────────────────────────────────────────────
    _s("BTC", "Crypto", 24, "133741"),
    _s("ETH", "Crypto", 26, "146021"),
]}


def symbol(internal: str) -> Symbol:
    return REGISTRY[internal]


def all_symbols() -> List[Symbol]:
    return list(REGISTRY.values())


def by_asset_class(asset_class: str) -> List[Symbol]:
    return [s for s in REGISTRY.values() if s.asset_class == asset_class]
