"""The single symbol registry: internal ↔ Norgate ↔ CFTC code ↔ asset class.
Replaces the scattered maps (CotSymbolCodeMap, the databento_mapping).

Scope = FIXED IDENTITY facts only (never change). TUNABLE strategy parameters
(positioning-index CustomLookbackWeeks, thresholds, TV chart symbols) stay in
cot-analyzer/config/params.yaml — the data layer must not carry strategy knobs.

The table lives in registry.yaml (asset_class -> symbol -> attrs), loaded at
import. Point $COTDATA_REGISTRY at an alternate file to override it.

Sources:
  • cftc_code   — cot-analyzer CotSymbolCodeMap
  • asset_class — CotIndexer asset classes
  • norgate     — '&' + CME root (e.g., "&ES"); required by norgatedata.price_timeseries().
is_equity is True only for the four equity indices (a fixed classification the
equity-vs-commodity rules key off); it is derived from asset_class == "Equities"
unless a symbol overrides it explicitly.
"""
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import yaml


@dataclass(frozen=True)
class Symbol:
    internal: str                 # pipeline root, e.g. "ES"
    norgate: Optional[str]        # Norgate continuous symbol, e.g. "&ES"; None when
                                  # Norgate has no series (norgate: null in YAML) —
                                  # the Norgate producer skips these (priced elsewhere)
    asset_class: str
    is_equity: bool
    report_type: str = "disagg"   # "tff" for financials, "disagg" for commodities
    cftc_code: Optional[str] = None
    # Optional Yahoo Finance ticker for markets Norgate/databento don't cover (the
    # yfinance provider prices these; e.g. "EEM"/"EFA" as ETF proxies for MSCI EM/EAFE).
    yahoo: Optional[str] = None
    # Databento GLBX.MDP3 continuous root — the databento producer queries
    # "<databento>.n.0". Defaults to the internal symbol; None (registry
    # `databento: null`) when GLBX carries no series for the market (ICE softs,
    # lumber, MSCI intl). None is the capability signal the databento producer
    # filters on, and the deployment falls back to yfinance where a yahoo ticker exists.
    databento: Optional[str] = None
    # Optional per-symbol price-source override (norgate | databento | yfinance).
    # Normally unset: the source is the deployment default (COTDATA_PRICE_SOURCE)
    # resolved against capability (see resolve_source). Set only to pin one market
    # to a specific vendor regardless of the deployment default.
    price_source: Optional[str] = None
    # Predecessor CFTC codes from earlier exchange/contract listings of the SAME
    # instrument, stitched in chronologically behind cftc_code by get_cot. Each
    # entry is a bare code string (scale 1.0) or a (code, scale) tuple. Kept as a
    # tuple of hashable entries so Symbol (frozen) stays hashable.
    hist_codes: tuple = ()


def hist_code_scales(hist_codes) -> List[tuple]:
    """Normalize hist_codes entries (str or (code, scale)) → list of (code, scale)."""
    out = []
    for h in hist_codes:
        out.append((h[0], float(h[1])) if isinstance(h, (list, tuple)) else (h, 1.0))
    return out


def _coerce_hist_codes(raw) -> tuple:
    """YAML encodes scaled entries as lists ([code, scale]); store them as tuples
    so Symbol stays hashable (a list nested in the tuple would break hashing)."""
    return tuple(tuple(h) if isinstance(h, list) else h for h in (raw or []))


# The price vendors a symbol can be sourced from. 'yfinance' is the universal
# research-grade fallback when the deployment's preferred vendor can't serve a market.
PRICE_SOURCES = ("norgate", "databento", "yfinance")


def _validate_source(value, internal) -> Optional[str]:
    """Validate an explicit price_source override from the YAML (None if unset)."""
    if value is None:
        return None
    v = str(value).strip().lower()
    if v not in PRICE_SOURCES:
        raise ValueError(
            f"cotdata registry: symbol '{internal}' has price_source={value!r}; "
            f"expected one of {PRICE_SOURCES}.")
    return v


def load_registry(yaml_path=None) -> Dict[str, Symbol]:
    """Build the symbol registry from YAML.

    Path resolution: explicit ``yaml_path`` arg, else $COTDATA_REGISTRY, else the
    packaged registry.yaml beside this module. Raises with a clear message on a
    missing/malformed file or an invalid entry, since the whole package depends
    on this at import.
    """
    yaml_path = yaml_path or os.environ.get(
        "COTDATA_REGISTRY", Path(__file__).parent / "registry.yaml")
    try:
        with open(yaml_path, "r") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError as e:
        raise FileNotFoundError(
            f"cotdata registry file not found: {yaml_path}. Set $COTDATA_REGISTRY "
            f"to a valid registry YAML, or restore the packaged registry.yaml.") from e
    except yaml.YAMLError as e:
        raise ValueError(f"cotdata registry YAML is malformed ({yaml_path}): {e}") from e

    if not isinstance(data, dict):
        raise ValueError(
            f"cotdata registry YAML must be a mapping of asset_class -> symbols "
            f"({yaml_path}); got {type(data).__name__}.")

    registry: Dict[str, Symbol] = {}
    for asset_class, symbols in data.items():
        if not isinstance(symbols, dict):
            raise ValueError(
                f"cotdata registry: asset class '{asset_class}' must map to a dict of "
                f"symbols, got {type(symbols).__name__}.")
        for internal, attrs in symbols.items():
            attrs = attrs or {}
            if not isinstance(attrs, dict):
                raise ValueError(
                    f"cotdata registry: symbol '{internal}' must map to a dict of "
                    f"attrs (e.g. 'cftc_code: ...'), got {type(attrs).__name__}.")
            if internal in registry:
                raise ValueError(
                    f"cotdata registry: duplicate symbol '{internal}' "
                    f"(re-declared under asset class '{asset_class}').")
            if not attrs.get("cftc_code"):
                raise ValueError(
                    f"cotdata registry: symbol '{internal}' is missing cftc_code.")
            registry[internal] = Symbol(
                internal=internal,
                norgate=attrs.get("norgate", f"&{internal}"),
                asset_class=asset_class,
                # Derived from the class so the two can't drift; an explicit
                # is_equity in the YAML still wins if a symbol ever needs it.
                is_equity=attrs.get("is_equity", asset_class == "Equities"),
                # Financials use TFF (Traders in Financial Futures), Commodities use Disaggregated
                report_type=attrs.get("report_type", "tff" if asset_class in ("Equities", "FX", "Rates") else "disagg"),
                cftc_code=attrs["cftc_code"],
                hist_codes=_coerce_hist_codes(attrs.get("hist_codes")),
                yahoo=attrs.get("yahoo"),
                # Defaults to the internal root; explicit `databento: null` marks a
                # market GLBX doesn't carry (capability signal for the producer).
                databento=attrs.get("databento", internal),
                price_source=_validate_source(attrs.get("price_source"), internal),
            )
    return registry


# Built once at import from the default location.
REGISTRY: Dict[str, Symbol] = load_registry()


def symbol(internal: str) -> Symbol:
    return REGISTRY[internal]


def all_symbols() -> List[Symbol]:
    return list(REGISTRY.values())


def by_asset_class(asset_class: str) -> List[Symbol]:
    return [s for s in REGISTRY.values() if s.asset_class == asset_class]


# ── Price-source selection ───────────────────────────────────────────────────
# Which vendor prices a symbol is a DEPLOYMENT choice, not a fixed identity fact:
# the same ES is Norgate for local research and databento on the public-dash server.
# So it is resolved at runtime from three inputs, not baked per-symbol in the shared
# registry: (1) a deployment default (COTDATA_PRICE_SOURCE), (2) per-symbol capability
# (which vendors carry a series — the norgate/databento/yahoo mappings), and (3) an
# optional per-symbol override. See ADR-0006.

def _can_serve(sym: Symbol, source: str) -> bool:
    """Whether `source` has a price series for `sym` (its vendor mapping is present)."""
    if source == "norgate":
        return sym.norgate is not None
    if source == "databento":
        return sym.databento is not None
    if source == "yfinance":
        return sym.yahoo is not None
    raise ValueError(f"unknown price source {source!r}; expected one of {PRICE_SOURCES}")


def resolve_source(sym: Symbol, default: str = "norgate") -> Optional[str]:
    """The vendor that prices `sym` on a deployment whose default source is `default`.

    An explicit `sym.price_source` override wins. Otherwise use `default` when that
    vendor can serve the symbol, else fall back to yfinance where a yahoo ticker
    exists. Returns None when nothing can price it (the caller skips the symbol)."""
    if sym.price_source:
        return sym.price_source
    if _can_serve(sym, default):
        return default
    if _can_serve(sym, "yfinance"):
        return "yfinance"
    return None


def default_price_source() -> str:
    """Deployment-wide default price vendor from $COTDATA_PRICE_SOURCE ('norgate' if
    unset). Local research leaves it unset; the databento server sets it to 'databento'."""
    src = os.environ.get("COTDATA_PRICE_SOURCE", "norgate").strip().lower()
    if src not in PRICE_SOURCES:
        raise ValueError(
            f"COTDATA_PRICE_SOURCE={src!r} is invalid; expected one of {PRICE_SOURCES}.")
    return src
