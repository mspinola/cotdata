"""get_cot parallel listings (sum_codes): the full-size contract ADDED to the mini.

Synthetic TFF-shaped frames: a primary (mini) and one listing (big) at scale 5 before
2020-01-14 and scale 2 from it, overlapping on two weeks and each reporting alone on one.
"""
import numpy as np
import pandas as pd
import pytest

from cotdata import cot
from cotdata.registry import Symbol

D = pd.DatetimeIndex(["2020-01-07", "2020-01-14", "2020-01-21"])


def _tff(dates, oi, dl, ds, traders, name, code):
    n = len(dates)
    return pd.DataFrame({
        "Market_and_Exchange_Names": [name] * n,
        "CFTC_Contract_Market_Code": [code] * n,
        "Open_Interest_All": oi,
        "Dealer_Positions_Long_All": dl,
        "Dealer_Positions_Short_All": ds,
        "Change_in_Open_Interest_All": ["x"] * n,          # stored as text in the TFF files
        "Change_in_Dealer_Long_All": ["x"] * n,
        "Pct_of_Open_Interest_All": [100.0] * n,
        "Pct_of_OI_Dealer_Long_All": [round(100 * a / b, 1) for a, b in zip(dl, oi)],
        "Traders_Tot_All": traders,
        "Traders_Dealer_Long_All": [str(t) for t in traders],   # text in the TFF files
        "Conc_Gross_LE_4_TDR_Long_All": [40.0] * n,
        "Contract_Units": ["(x $50)"] * n,
    }, index=pd.DatetimeIndex(dates, name="Report_Date_as_MM_DD_YYYY"))


@pytest.fixture
def summed(monkeypatch):
    sym = Symbol(internal="XX", asset_class="Equities", is_equity=True, cftc_code="MINI",
                 sum_codes=(("BIG", 5.0), ("BIG", 2.0, "2020-01-14")))
    monkeypatch.setitem(cot.REGISTRY, "XX", sym)
    frames = {
        "XX_MINI": _tff(D[1:], [100, 200], [10, 20], [5, 10], [50, 60], "MINI NAME", "MINI"),
        "XX_BIG": _tff(D[:2], [30, 40], [3, 4], [1, 2], [70, 20], "BIG NAME", "BIG"),
    }
    monkeypatch.setattr("cotdata.store.read_cot_tff",
                        lambda n: frames.get(n, pd.DataFrame()).copy())
    return frames


def test_counts_are_scaled_and_summed_with_dated_scale(summed):
    out = cot.get_cot("XX", report="tff")
    assert list(out.index) == list(D)
    # 01-07: big alone at scale 5 -> 30*5
    assert out.loc[D[0], "Open_Interest_All"] == 150
    assert out.loc[D[0], "Dealer_Positions_Long_All"] == 15
    # 01-14: mini 100 + big 40 at scale 2 (dated entry takes over) -> 180
    assert out.loc[D[1], "Open_Interest_All"] == 180
    assert out.loc[D[1], "Dealer_Positions_Short_All"] == 5 + 2 * 2
    # 01-21: mini alone
    assert out.loc[D[2], "Open_Interest_All"] == 200


def test_pct_and_change_recomputed_from_sums(summed):
    out = cot.get_cot("XX", report="tff")
    assert (out["Pct_of_Open_Interest_All"] == 100.0).all()
    np.testing.assert_allclose(out["Pct_of_OI_Dealer_Long_All"].values,
                               100 * np.array([15, 18, 20]) / np.array([150, 180, 200]))   # 01-14: 10 + 4*2
    assert np.isnan(out.loc[D[0], "Change_in_Open_Interest_All"])
    assert out.loc[D[1], "Change_in_Open_Interest_All"] == 30
    assert out.loc[D[2], "Change_in_Dealer_Long_All"] == 20 - 18


def test_traders_take_the_max_across_listings(summed):
    out = cot.get_cot("XX", report="tff")
    assert out["Traders_Tot_All"].tolist() == [70, 50, 60]          # 01-14: max(50, 20)
    assert out["Traders_Dealer_Long_All"].tolist() == [70, 50, 60]  # coerced from text


def test_metadata_and_code_follow_the_primary(summed):
    out = cot.get_cot("XX", report="tff")
    assert (out["CFTC_Contract_Market_Code"] == "MINI").all()
    assert out["Market_and_Exchange_Names"].tolist() == ["BIG NAME", "MINI NAME", "MINI NAME"]
    assert np.isnan(out.loc[D[0], "Conc_Gross_LE_4_TDR_Long_All"])   # primary absent
    assert out.loc[D[1], "Conc_Gross_LE_4_TDR_Long_All"] == 40.0


def test_missing_listing_file_leaves_primary_untouched(monkeypatch):
    sym = Symbol(internal="YY", asset_class="Equities", is_equity=True, cftc_code="MINI",
                 sum_codes=(("BIG", 5.0),))
    monkeypatch.setitem(cot.REGISTRY, "YY", sym)
    prim = _tff(D, [1, 2, 3], [1, 1, 1], [0, 0, 0], [9, 9, 9], "P", "MINI")
    monkeypatch.setattr("cotdata.store.read_cot_tff",
                        lambda n: prim.copy() if n == "YY_MINI" else pd.DataFrame())
    pd.testing.assert_frame_equal(cot.get_cot("YY", report="tff"), prim)


def test_hist_scale_leaves_trader_counts_alone(monkeypatch):
    """The predecessor scale applies to contract counts only (legacy Traders_* are numeric)."""
    sym = Symbol(internal="ZZ", asset_class="Grains", is_equity=False, cftc_code="NEW",
                 hist_codes=(("OLD", 0.2),))
    monkeypatch.setitem(cot.REGISTRY, "ZZ", sym)
    old = pd.DataFrame({"CFTC_Contract_Market_Code": ["OLD"], "Open_Interest_All": [1000.0],
                        "Comm_Positions_Long_All": [500.0], "Traders_Tot_All": [366.0]},
                       index=pd.DatetimeIndex(["1997-12-30"]))
    new = pd.DataFrame({"CFTC_Contract_Market_Code": ["NEW"], "Open_Interest_All": [210.0],
                        "Comm_Positions_Long_All": [100.0], "Traders_Tot_All": [345.0]},
                       index=pd.DatetimeIndex(["1998-01-06"]))
    monkeypatch.setattr("cotdata.store.read_cot_legacy",
                        lambda n: {"ZZ_NEW": new, "ZZ_OLD": old}.get(n, pd.DataFrame()).copy())
    out = cot.get_cot("ZZ")
    assert out.loc["1997-12-30", "Open_Interest_All"] == 200
    assert out.loc["1997-12-30", "Comm_Positions_Long_All"] == 100
    assert out.loc["1997-12-30", "Traders_Tot_All"] == 366
