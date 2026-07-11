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
import yaml
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


@dataclass(frozen=True)
class Symbol:
    internal: str                 # pipeline root, e.g. "ES"
    norgate: str                  # Norgate continuous symbol, e.g. "&ES"
    asset_class: str
    is_equity: bool
    cftc_code: Optional[str] = None
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
                cftc_code=attrs["cftc_code"],
                hist_codes=_coerce_hist_codes(attrs.get("hist_codes")),
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
