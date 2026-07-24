"""Unit tests for the ADR-0006 item-6 validation harness (scripts/validate_databento_vs_norgate.py).

The harness itself needs real Norgate + databento stores to run; here we verify its
comparison logic against synthetic backadj frames: that an anchor-only difference
passes, a scale (unit) mismatch fails, per-day outliers are counted, roll dates are
read from Delivery Month, and read_backadj round-trips a store parquet.
"""
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate_databento_vs_norgate.py"
_spec = importlib.util.spec_from_file_location("validate_databento_vs_norgate", _SCRIPT)
val = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(val)


_DATES = pd.date_range("2020-01-01", periods=250, freq="B")
_RNG = np.random.default_rng(0)
_CHANGES = _RNG.normal(0, 1.0, len(_DATES))
_NG_CLOSE = 100 + np.cumsum(_CHANGES)


def _frame(close, dm=None):
    df = pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close},
                      index=pd.DatetimeIndex(_DATES, name="Date"))
    if dm is not None:
        df["Delivery Month"] = dm
    return df


def _pass_thresholds(m):
    return val.evaluate(m, corr_min=0.999, scale_band=0.02, rel_tol=0.10, max_outliers_per_yr=8)


def test_anchor_only_difference_passes():
    # databento = norgate + a constant (a floating back-adjust anchor) → identical
    # daily changes, different level. This is the expected clean case.
    ng = _frame(_NG_CLOSE)
    db = _frame(_NG_CLOSE + 7.5)
    m = val.compare(ng, db, "ES")

    assert m["change_corr"] > 0.99999
    assert m["scale_ratio"] == pytest.approx(1.0, abs=1e-9)
    assert m["change_max_rel_diff"] < 1e-9
    assert m["level_mean_abs"] == pytest.approx(7.5)
    assert _pass_thresholds(m) == []


def test_scale_mismatch_fails():
    # databento daily changes are 1.5x norgate's (e.g. a settlement/unit scale bug):
    # perfectly correlated but the wrong size → caught by scale_ratio, not corr.
    ng = _frame(_NG_CLOSE)
    db = _frame(100 + np.cumsum(_CHANGES * 1.5))
    m = val.compare(ng, db, "ES")

    assert m["change_corr"] > 0.999               # still correlated
    assert m["scale_ratio"] == pytest.approx(1.5, abs=0.05)
    fails = _pass_thresholds(m)
    assert any("scale_ratio" in f for f in fails)


def test_per_day_outliers_are_counted_and_gate_fails():
    # Inject a 4-point discrepancy every 12th day (~20/yr, each ~4x a typical daily
    # move) — the kind of thing a roll-date mismatch or a bad settlement would cause.
    ng = _frame(_NG_CLOSE)
    db_changes = _CHANGES.copy()
    db_changes[::12] += 4.0
    db = _frame(100 + np.cumsum(db_changes))
    m = val.compare(ng, db, "ES")

    assert m["outlier_days"] >= 15
    assert m["change_max_rel_diff"] > 1.0         # worst day dwarfs a typical move
    assert _pass_thresholds(m) != []              # too many outlier days → fail
    # Loosening the outlier budget past what occurred clears that specific gate.
    loose = val.evaluate(m, corr_min=0.0, scale_band=1.0, rel_tol=0.10, max_outliers_per_yr=1000)
    assert loose == []


def test_insufficient_overlap_reports_error():
    ng = _frame(_NG_CLOSE).iloc[:2]
    db = _frame(_NG_CLOSE).iloc[100:102]          # no common dates
    m = val.compare(ng, db, "ES")
    assert m.get("error") == "insufficient overlap"
    assert _pass_thresholds(m) == ["insufficient overlap"]


def test_roll_dates_from_delivery_month():
    dm = ["A"] * 100 + ["B"] * 150
    f = _frame(_NG_CLOSE, dm=dm)
    rolls = val._roll_dates(f)
    assert len(rolls) == 1 and rolls[0] == _DATES[100]
    # roll counts surface in the comparison metrics
    m = val.compare(f, _frame(_NG_CLOSE + 1, dm=dm), "ES")
    assert m["rolls_norgate"] == 1 and m["rolls_common"] == 1


def test_read_backadj_roundtrip(tmp_path):
    prices = tmp_path / "prices"
    prices.mkdir()
    _frame(_NG_CLOSE).to_parquet(prices / "ES_backadj.parquet")
    got = val.read_backadj(str(tmp_path), "ES")
    assert not got.empty and got.index.name == "Date" and got.index.tz is None
    assert val.read_backadj(str(tmp_path), "NOPE").empty
