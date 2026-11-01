"""`cotdata.cit`: the Supplemental joined to Legacy COMBINED, week by week.

Offline, against a store written in tmp_path with the real writers. GC is used as the
market because it has no predecessor or parallel listings, so the join is between exactly
one file per report; the Supplemental does not cover gold, which does not matter here
because the reader is keyed on the file name, not on the covered set.
"""
import numpy as np
import pandas as pd
import pytest

from cotdata import cit, store
from cotdata.registry import REGISTRY

GC = REGISTRY["GC"]
KEY = f"GC_{GC.cftc_code}"
WEEKS = pd.DatetimeIndex(["2026-01-06", "2026-01-13", "2026-01-20", "2026-01-27"])


def _supplemental(idx=WEEKS, oi=(1000, 1100, 1200, 1300)):
    n = len(idx)
    return pd.DataFrame({
        "Market_and_Exchange_Names": ["GOLD - COMMODITY EXCHANGE INC."] * n,
        "CFTC_Contract_Market_Code": [GC.cftc_code] * n,
        "Open_Interest_All": list(oi),
        "NComm_Positions_Long_All_NoCIT": [300] * n,
        "NComm_Positions_Short_All_NoCIT": [200] * n,
        "NComm_Postions_Spread_All_NoCIT": [50] * n,
        "Comm_Positions_Long_All_NoCIT": [250] * n,
        "Comm_Positions_Short_All_NoCIT": [400] * n,
        "NonRept_Positions_Long_All": [100] * n,
        "NonRept_Positions_Short_All": [90] * n,
        "CIT_Positions_Long_All": [160] * n,   # 60 from comm + 70 from noncomm + 30 spread
        "CIT_Positions_Short_All": [25] * n,   # 10 + 15 + 0
    }, index=idx)


def _legacy_combined(idx=WEEKS, oi=(1000, 1100, 1200, 1300), nonrept_long=100):
    n = len(idx)
    return pd.DataFrame({
        "Market_and_Exchange_Names": ["GOLD - COMMODITY EXCHANGE INC."] * n,
        "CFTC_Contract_Market_Code": [GC.cftc_code] * n,
        "Open_Interest_All": list(oi),
        "NonComm_Positions_Long_All": [370] * n,    # 300 ex index + 70 index
        "NonComm_Positions_Short_All": [215] * n,   # 200 + 15
        "Comm_Positions_Long_All": [310] * n,       # 250 + 60
        "Comm_Positions_Short_All": [410] * n,      # 400 + 10
        "NonRept_Positions_Long_All": [nonrept_long] * n,
        "NonRept_Positions_Short_All": [90] * n,
        "Traders_Tot_All": [50] * n,
        "Traders_Comm_Long_All": [10] * n, "Traders_Comm_Short_All": [12] * n,
        "Traders_NonComm_Long_All": [20] * n, "Traders_NonComm_Short_All": [8] * n,
    }, index=idx)


@pytest.fixture()
def store_env(tmp_path, monkeypatch):
    monkeypatch.setenv("COTDATA_STORE", str(tmp_path))
    return tmp_path


def _write(supp=None, legacy=None):
    if supp is not None:
        store.write_cot_supplemental(KEY, supp, source="test")
    if legacy is not None:
        store.write_cot_legacy_combined(KEY, legacy, source="test")


def test_decompose_recovers_the_carve_out_per_bucket(store_env):
    _write(_supplemental(), _legacy_combined())
    d = cit.decompose("GC")
    assert list(d.columns) == cit.COLUMNS
    assert list(d.index) == list(WEEKS)
    row = d.iloc[0]
    assert row["cit_from_comm_long"] == 60 and row["cit_from_comm_short"] == 10
    assert row["cit_from_noncomm_long"] == 70 and row["cit_from_noncomm_short"] == 15
    # Spreading is the residual: Legacy's spread column is not in the store.
    assert row["cit_from_spread_long"] == 30 and row["cit_from_spread_short"] == 0
    assert (d["oi_residual"] == 0).all()
    assert (d["nonrept_long_residual"] == 0).all() and (d["nonrept_short_residual"] == 0).all()
    # The Supplemental's own numbers pass through unchanged.
    assert row["oi"] == 1000 and row["cit_long"] == 160 and row["spread_nocit"] == 50
    assert row["comm_long"] == 310 and row["comm_long_nocit"] == 250


def test_decompose_reads_legacy_combined_never_futures_only(store_env):
    """A store with futures-only Legacy and no combined domain must refuse, even though
    the futures-only file exists: that join is wrong on every row."""
    store.write_cot_legacy(KEY, _legacy_combined(), source="test")
    _write(supp=_supplemental())
    with pytest.raises(ValueError, match="cot_legacy_combined"):
        cit.decompose("GC")


def test_decompose_refuses_a_market_the_supplemental_does_not_cover(store_env):
    _write(legacy=_legacy_combined())
    with pytest.raises(ValueError, match="no Supplemental rows"):
        cit.decompose("GC")


def test_decompose_refuses_an_open_interest_mismatch(store_env):
    """Open interest is published on both reports and agrees exactly on the combined
    basis; a mismatch is a basis or vintage error and strict mode names the week."""
    _write(_supplemental(), _legacy_combined(oi=(1000, 1100, 1250, 1300)))
    with pytest.raises(ValueError, match="oi differs .* 1 of 4 shared weeks .*2026-01-20"):
        cit.decompose("GC")
    d = cit.decompose("GC", strict=False)
    assert d["oi_residual"].tolist() == [0, 0, 50, 0]


def test_decompose_refuses_a_nonreportable_mismatch(store_env):
    _write(_supplemental(), _legacy_combined(nonrept_long=101))
    with pytest.raises(ValueError, match="nonrept_long differs .* 4 of 4"):
        cit.decompose("GC")


def test_decompose_keeps_only_shared_weeks(store_env):
    longer = pd.DatetimeIndex(["2025-12-30", *WEEKS.strftime("%Y-%m-%d"), "2026-02-03"])
    _write(_supplemental(), _legacy_combined(idx=longer, oi=(900, 1000, 1100, 1200, 1300, 1400)))
    d = cit.decompose("GC")
    assert list(d.index) == list(WEEKS)


def test_decompose_refuses_disjoint_weeks(store_env):
    other = pd.DatetimeIndex(["2020-01-07", "2020-01-14", "2020-01-21", "2020-01-28"])
    _write(_supplemental(), _legacy_combined(idx=other))
    with pytest.raises(ValueError, match="share no report week"):
        cit.decompose("GC")


def test_check_counts_without_raising(store_env):
    longer = pd.DatetimeIndex(["2025-12-30", *WEEKS.strftime("%Y-%m-%d")])
    _write(_supplemental(), _legacy_combined(idx=longer, oi=(900, 1000, 1100, 1250, 1300)))
    c = cit.check("GC")
    assert c["shared_weeks"] == 4 and c["legacy_only_weeks"] == 1
    assert c["supplemental_only_weeks"] == 0
    assert c["oi_exact"] == 3 and c["nonrept_exact"] == 4
    assert c["negative_carveouts"] == 0 and c["min_carveout"] == 0.0


def test_check_on_an_empty_side_reports_zero_shared(store_env):
    _write(supp=_supplemental())
    c = cit.check("GC")
    assert c["shared_weeks"] == 0 and c["legacy_combined_weeks"] == 0
    assert c["supplemental_weeks"] == 4 and c["min_carveout"] is None


def test_check_flags_negative_carveouts(store_env):
    supp = _supplemental()
    supp["Comm_Positions_Long_All_NoCIT"] = 311   # one more than Legacy's 310
    _write(supp, _legacy_combined())
    c = cit.check("GC")
    assert c["negative_carveouts"] == 4 and c["min_carveout"] == -1.0
    assert np.isfinite(c["min_carveout"])
