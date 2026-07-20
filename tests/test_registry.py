import hashlib
import textwrap

import pytest

from cotdata.registry import (
    Symbol, symbol, all_symbols, by_asset_class, hist_code_scales, load_registry,
)


# ── loading & basic shape ────────────────────────────────────────────────────
def test_registry_loads_all_symbols():
    symbols = all_symbols()
    assert len(symbols) == 49, f"Expected 49 symbols, got {len(symbols)}"


def test_basic_symbol_loading():
    es = symbol("ES")
    assert es.internal == "ES"
    assert es.norgate == "&ES"
    assert es.asset_class == "Equities"
    assert es.is_equity is True
    assert es.cftc_code == "13874A"
    assert es.hist_codes == ()


def test_yahoo_only_market_has_no_norgate_symbol():
    """MSCI MME/MFS are priced off ETF proxies (EEM/EFA); Norgate carries no series
    for them, so norgate is None (registry `norgate: null`) — that's the signal the
    Norgate producer filters on. A defaulted '&MME' would send it fetching a
    nonexistent &MME_CCB."""
    for s in ("MME", "MFS"):
        assert symbol(s).norgate is None, s
        assert symbol(s).yahoo in ("EEM", "EFA")
    # Norgate-covered markets still default to '&<internal>'.
    assert symbol("ES").norgate == "&ES"


def test_symbol_with_simple_hist_codes():
    rty = symbol("RTY")
    assert rty.asset_class == "Equities"
    assert rty.is_equity is True
    assert rty.hist_codes == ("23977A",)


def test_symbol_with_complex_hist_codes_and_scaling():
    lbr = symbol("LBR")
    assert lbr.asset_class == "Softs"
    assert lbr.is_equity is False
    # Scaled entries load as (code, scale) TUPLES (not lists) so Symbol stays hashable.
    assert lbr.hist_codes == (("058643", 4.0),)
    assert isinstance(lbr.hist_codes[0], tuple)
    assert hist_code_scales(lbr.hist_codes) == [("058643", 4.0)]


def test_hist_code_scales_normalization():
    assert hist_code_scales(["23977A"]) == [("23977A", 1.0)]
    assert hist_code_scales([["058643", 4.0]]) == [("058643", 4.0)]
    assert hist_code_scales([("123456", 2.5)]) == [("123456", 2.5)]


def test_by_asset_class():
    equities = by_asset_class("Equities")
    assert len(equities) == 8          # ES NQ YM RTY + held-out EMD, NKD, MME, MFS
    assert all(eq.is_equity for eq in equities)

    dairy = by_asset_class("Dairy")    # new held-out class
    assert [d.internal for d in dairy] == ["DC"]

    crypto = by_asset_class("Crypto")
    assert [c.internal for c in crypto] == ["BTC", "ETH"]


# ── hashability (frozen dataclass must stay hashable, incl. scaled hist_codes) ─
def test_symbols_are_hashable():
    # Would raise TypeError: unhashable type 'list' if hist_codes held a list.
    assert len(set(all_symbols())) == 49
    assert symbol("LBR") in {symbol("LBR")}
    hash(symbol("LBR"))  # scaled hist_codes — the tricky one


# ── is_equity is derived from asset_class, not duplicated in YAML ─────────────
def test_is_equity_derived_from_asset_class():
    assert symbol("ES").is_equity is True
    assert symbol("GC").is_equity is False
    for s in all_symbols():
        assert s.is_equity == (s.asset_class == "Equities"), s.internal


# ── $COTDATA_REGISTRY / explicit-path override (the point of the refactor) ────
_MINI_YAML = textwrap.dedent("""
    Metals:
      GC:
        cftc_code: "088691"
      LBR:
        cftc_code: "058644"
        hist_codes:
          - ["058643", 4.0]
    Equities:
      ES:
        cftc_code: "13874A"
""")


def _write(tmp_path, text):
    p = tmp_path / "registry.yaml"
    p.write_text(text)
    return p


def test_override_via_explicit_path(tmp_path):
    reg = load_registry(_write(tmp_path, _MINI_YAML))
    assert set(reg) == {"GC", "LBR", "ES"}
    assert reg["ES"].is_equity is True          # derived
    assert reg["GC"].is_equity is False
    assert reg["LBR"].hist_codes == (("058643", 4.0),)


def test_override_via_env(tmp_path, monkeypatch):
    monkeypatch.setenv("COTDATA_REGISTRY", str(_write(tmp_path, _MINI_YAML)))
    reg = load_registry()
    assert set(reg) == {"GC", "LBR", "ES"}


# ── failure modes surface clear errors (built at import — must not be opaque) ──
def test_missing_file_raises_helpful_error():
    with pytest.raises(FileNotFoundError, match="COTDATA_REGISTRY"):
        load_registry("/no/such/registry.yaml")


def test_malformed_yaml_raises(tmp_path):
    with pytest.raises(ValueError, match="malformed"):
        load_registry(_write(tmp_path, "Metals:\n  GC:\n  - broken: ["))


def test_non_mapping_yaml_raises(tmp_path):
    with pytest.raises(ValueError, match="mapping of asset_class"):
        load_registry(_write(tmp_path, "- just\n- a\n- list\n"))


def test_duplicate_symbol_raises(tmp_path):
    dup = "Metals:\n  GC:\n    cftc_code: \"1\"\nEnergies:\n  GC:\n    cftc_code: \"2\"\n"
    with pytest.raises(ValueError, match="duplicate symbol 'GC'"):
        load_registry(_write(tmp_path, dup))


def test_missing_cftc_code_raises(tmp_path):
    with pytest.raises(ValueError, match="missing cftc_code"):
        load_registry(_write(tmp_path, "Metals:\n  GC:\n    norgate: \"&GC\"\n"))


def test_scalar_attrs_raises(tmp_path):
    # ES: "13874A" — value is a bare code, not a dict of attrs. Must be a clean
    # ValueError, not a raw AttributeError from attrs.get(...).
    with pytest.raises(ValueError, match="must map to a dict of attrs"):
        load_registry(_write(tmp_path, "Equities:\n  ES: \"13874A\"\n"))


# ── golden identity lock: adding/changing a symbol must be a deliberate edit ──
def test_golden_identity_checksum():
    """A checksum over (internal, cftc_code, asset_class) for every symbol. If
    this fails you changed an identity fact — update the expected hash ON PURPOSE
    (registry.yaml carries FIXED identity only; unintended drift is a bug)."""
    ident = sorted((s.internal, s.cftc_code, s.asset_class) for s in all_symbols())
    digest = hashlib.md5(repr(ident).encode()).hexdigest()
    assert digest == "e6b0b92caa0f25f68dea62024bddf899", (
        "registry identity facts changed; update the expected checksum if intended")
