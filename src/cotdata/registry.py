"""The single symbol registry: internal ↔ Norgate ↔ CFTC code ↔ asset class.
Replaces the scattered maps (CotSymbolCodeMap, the databento_mapping).

Scope = FIXED IDENTITY facts only (never change). TUNABLE strategy parameters
(positioning-index CustomLookbackWeeks, thresholds, TV chart symbols) stay in
cot-analyzer/config/params.yaml — the data layer must not carry strategy knobs.

Sources:
  • cftc_code   — cot-analyzer CotSymbolCodeMap
  • asset_class — CotIndexer asset classes
  • norgate     — '&' + CME root (e.g., "&ES"); required by norgatedata.price_timeseries().
is_equity is True only for the four equity indices (a fixed classification the
equity-vs-commodity rules key off; derivable as asset_class == "Equities").
"""
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class Symbol:
    internal: str                 # pipeline root, e.g. "ES"
    norgate: str                  # Norgate continuous symbol, e.g. "&ES"
    asset_class: str
    is_equity: bool
    cftc_code: Optional[str] = None
    # Predecessor CFTC codes from earlier exchange listings of the SAME contract,
    # stitched in chronologically behind cftc_code by get_cot (primary wins on
    # overlaps). Used when a contract migrated exchanges and its COT history is
    # split across codes (e.g. Russell 2000: CME → ICE → CME).
    hist_codes: tuple = ()


def _s(internal, asset_class, cftc, is_eq=False, norgate=None, hist_codes=()):
    return Symbol(internal, norgate or f"&{internal}", asset_class, is_eq, cftc, hist_codes)


REGISTRY: Dict[str, Symbol] = {s.internal: s for s in [
    # ── Equities (is_equity=True) ────────────────────────────────────────────
    _s("ES",  "Equities", "13874A", is_eq=True),
    _s("NQ",  "Equities", "209742", is_eq=True),
    _s("YM",  "Equities", "124603", is_eq=True),
    # Russell 2000 e-mini migrated CME→ICE(2008)→CME(2017); the ICE years live
    # under 23977A. Stitching it fills the 2008-2017 hole in code 239742.
    _s("RTY", "Equities", "239742", is_eq=True, hist_codes=("23977A",)),
    # ── Metals ───────────────────────────────────────────────────────────────
    _s("GC",  "Metals", "088691"),
    _s("SI",  "Metals", "084691"),
    _s("HG",  "Metals", "085692"),
    _s("PL",  "Metals", "076651"),
    _s("PA",  "Metals", "075651"),
    # ── Energies ─────────────────────────────────────────────────────────────
    _s("CL",  "Energies", "067651"),
    _s("RB",  "Energies", "111659"),
    _s("HO",  "Energies", "022651"),
    _s("NG",  "Energies", "023651"),
    # ── Grains ───────────────────────────────────────────────────────────────
    _s("ZC",  "Grains", "002602"),
    _s("ZS",  "Grains", "005602"),
    _s("ZM",  "Grains", "026603"),
    _s("ZL",  "Grains", "007601"),
    _s("ZW",  "Grains", "001602"),
    # ── Currencies ───────────────────────────────────────────────────────────
    _s("6E",  "Currencies", "099741"),
    _s("6A",  "Currencies", "232741"),
    _s("6B",  "Currencies", "096742"),
    _s("6C",  "Currencies", "090741"),
    _s("6J",  "Currencies", "097741"),
    _s("6S",  "Currencies", "092741"),
    _s("6M",  "Currencies", "095741"),
    _s("6N",  "Currencies", "112741"),
    _s("DX",  "Currencies", "098662"),   # US Dollar Index (ICE) — one clean USD instrument
    # ── Fixed Income ─────────────────────────────────────────────────────────
    _s("ZN",  "Fixed Income", "043602"),
    _s("ZT",  "Fixed Income", "042601"),
    _s("ZF",  "Fixed Income", "044601"),
    _s("ZB",  "Fixed Income", "020601"),
    # ── Softs ────────────────────────────────────────────────────────────────
    _s("SB",  "Softs", "080732"),
    _s("CT",  "Softs", "033661"),
    _s("CC",  "Softs", "073732"),
    _s("KC",  "Softs", "083731"),
    _s("OJ",  "Softs", "040701"),
    _s("LBR", "Softs", "058644"),
    # ── Live Stock ───────────────────────────────────────────────────────────
    _s("LE",  "Live Stock", "057642"),
    _s("HE",  "Live Stock", "054642"),
    _s("GF",  "Live Stock", "061641"),
    # ── Crypto ───────────────────────────────────────────────────────────────
    _s("BTC", "Crypto", "133741"),
    _s("ETH", "Crypto", "146021"),
]}


def symbol(internal: str) -> Symbol:
    return REGISTRY[internal]


def all_symbols() -> List[Symbol]:
    return list(REGISTRY.values())


def by_asset_class(asset_class: str) -> List[Symbol]:
    return [s for s in REGISTRY.values() if s.asset_class == asset_class]
