"""Proportional (ratio) back-adjustment derived on read — get_prices(..., 'propadj').

Models the DC / Class III Milk failure mode: a low-priced contract whose ADDITIVE
back-adjustment (Norgate _CCB) accumulates roll gaps below zero, which breaks
price-based stops and R-multiples. propadj must recover a strictly-positive series
that preserves percentage returns and stays sign-identical to backadj at rolls.
"""
import numpy as np
import pandas as pd
import pytest


@pytest.fixture()
def store_env(tmp_path, monkeypatch):
    monkeypatch.setenv("COTDATA_STORE", str(tmp_path))
    return tmp_path


def _milk_like(with_delivery_month=True):
    """Three contract segments (two rolls) of a ~$2–4 low-priced contract.

    Offsets O = B − U are piecewise-constant (−3.0, −1.5, 0), so the additive
    close B goes negative in the oldest segment while the unadjusted close U is
    always positive — exactly DC's situation, in miniature.
    """
    idx = pd.date_range("2020-01-01", periods=9, freq="D", name="Date")
    u_close = np.array([2.0, 2.1, 2.2, 3.0, 3.1, 3.2, 4.0, 4.1, 4.2])
    offset = np.array([-3.0] * 3 + [-1.5] * 3 + [0.0] * 3)
    dm = ["202003"] * 3 + ["202006"] * 3 + ["202009"] * 3

    U = pd.DataFrame({
        "Open": u_close - 0.05, "High": u_close + 0.10,
        "Low": u_close - 0.10, "Close": u_close,
        "Volume": [10] * 9, "Open Interest": [100] * 9,
    }, index=idx)
    B = U.copy()
    for c in ("Open", "High", "Low", "Close"):
        B[c] = U[c] + offset
    if with_delivery_month:
        U["Delivery Month"] = dm
        B["Delivery Month"] = dm
    return U, B


def _write(sym="DC", **kw):
    from cotdata import store
    U, B = _milk_like(**kw)
    store.write_prices(sym, "unadj", U, source="test")
    store.write_prices(sym, "backadj", B, source="test")
    return U, B


def test_backadj_goes_negative_but_propadj_is_strictly_positive(store_env):
    from cotdata import get_prices
    _write()
    assert (get_prices("DC", "backadj")["Close"] <= 0).any()      # the problem
    p = get_prices("DC", "propadj")
    assert (p[["Open", "High", "Low", "Close"]] > 0).all().all()  # the fix


def test_anchored_to_actual_recent_price(store_env):
    """Most-recent segment keeps actual (unadjusted) prices — factor == 1."""
    from cotdata import get_prices
    U, _ = _write()
    p = get_prices("DC", "propadj")
    assert p["Close"].iloc[-3:].tolist() == pytest.approx(U["Close"].iloc[-3:].tolist())


def test_preserves_within_segment_pct_returns(store_env):
    from cotdata import get_prices
    U, _ = _write()
    p = get_prices("DC", "propadj")
    dm = p["Delivery Month"]
    non_roll = ~(dm.ne(dm.shift()) & dm.shift().notna())
    err = (p["Close"].pct_change() - U["Close"].pct_change())[non_roll].abs()
    assert err.max() < 1e-12


def test_sign_identical_to_backadj_including_rolls(store_env):
    """Ratio- and additive-adjustment remove the same roll gaps, so every daily
    move — including across rolls — must agree in direction."""
    from cotdata import get_prices
    _write()
    p = get_prices("DC", "propadj")["Close"].diff()
    b = get_prices("DC", "backadj")["Close"].diff()
    both = p.notna() & b.notna() & (b.abs() > 1e-12)
    assert (np.sign(p[both]) == np.sign(b[both])).all()


def test_hand_computed_factors(store_env):
    """Regression guard on the exact ratio construction.

    spread(roll) = O[r−1] − O[r]; k = (U[r−1] + spread) / U[r−1]; segment factor =
    product of k for rolls at/after it, most-recent segment anchored to 1.
      roll@day3: spread = −3.0−(−1.5) = −1.5, U_prev = 2.2 → k1 = 0.7/2.2
      roll@day6: spread = −1.5−0    = −1.5, U_prev = 3.2 → k2 = 1.7/3.2
      factor[seg2]=1, factor[seg1]=k2, factor[seg0]=k1*k2
    """
    from cotdata import get_prices
    _write()
    c = get_prices("DC", "propadj")["Close"]
    k1, k2 = 0.7 / 2.2, 1.7 / 3.2
    assert c.iloc[0] == pytest.approx(2.0 * k1 * k2)   # oldest segment
    assert c.iloc[3] == pytest.approx(3.0 * k2)        # middle segment
    assert c.iloc[6] == pytest.approx(4.0)             # anchor segment, factor 1


def test_ohlc_ordering_preserved(store_env):
    from cotdata import get_prices
    _write()
    p = get_prices("DC", "propadj")
    assert (p["High"] >= p["Close"]).all() and (p["Close"] >= p["Low"]).all()
    assert (p["High"] >= p["Open"]).all() and (p["Open"] >= p["Low"]).all()


def test_falls_back_to_offset_jumps_without_delivery_month(store_env):
    """Rolls are still detected from offset steps when Delivery Month is absent."""
    from cotdata import get_prices
    _write(with_delivery_month=False)
    p = get_prices("DC", "propadj")
    assert (p["Close"] > 0).all()
    assert p["Close"].iloc[0] == pytest.approx(2.0 * (0.7 / 2.2) * (1.7 / 3.2))


def test_empty_when_either_series_missing(store_env):
    from cotdata import get_prices, store
    U, _ = _milk_like()
    store.write_prices("DC", "unadj", U, source="test")  # no backadj written
    assert get_prices("DC", "propadj").empty


def test_reconstructed_volume_view_still_works_on_propadj(store_env):
    """The volume view composes with the derived adjustment."""
    from cotdata import get_prices, store
    U, B = _milk_like()
    U["Volume_Reconstructed"] = [15, 14, 10, 18, 20, 22, 25, 24, 26]
    U["Volume_Source"] = ["reconstructed"] * 9
    store.write_prices("DC", "unadj", U, source="test")
    store.write_prices("DC", "backadj", B, source="test")
    p = get_prices("DC", "propadj", volume="reconstructed")
    assert p["Volume"].tolist() == [15, 14, 10, 18, 20, 22, 25, 24, 26]
    assert "Volume_Source" in p.columns
