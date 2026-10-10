"""get_cot predecessor stitching: overlap weeks go to the larger scaled open interest.

The 2017 Russell hand-off is the motivating case. CME (primary, 239742) relisted on
2017-08-15 with 13k open interest while ICE (predecessor, 23977A) still carried 567k;
"primary wins" kept the 13k slice for four weeks. The rule is now larger SCALED OI,
primary on ties, and plain primary-wins when no OI column exists.
"""
import pandas as pd
import pytest

from cotdata import cot
from cotdata.registry import Symbol


def _frame(dates, oi, comm_long, code):
    return pd.DataFrame({
        "CFTC_Contract_Market_Code": code,
        "Open_Interest_All": oi,
        "Comm_Positions_Long_All": comm_long,
    }, index=pd.DatetimeIndex(dates, name="Report_Date_as_MM_DD_YYYY"))


@pytest.fixture
def stitched(monkeypatch):
    """Register a synthetic symbol with one scaled predecessor and route the store."""
    sym = Symbol(internal="XX", asset_class="test", is_equity=False, cftc_code="NEW111",
                 hist_codes=(("OLD111", 4.0),))
    monkeypatch.setitem(cot.REGISTRY, "XX", sym)

    frames = {
        # primary: relists in week 2 small, dominates by week 4
        "XX_NEW111": _frame(["2017-08-08", "2017-08-15", "2017-08-22", "2017-08-29"],
                            [100, 50, 400, 500], [10, 5, 40, 50], "NEW111"),
        # predecessor (pre-scale): 4x smaller units, so 30 -> 120 after scaling
        "XX_OLD111": _frame(["2017-08-01", "2017-08-08", "2017-08-15", "2017-08-22"],
                            [40, 25, 30, 20], [4, 2, 3, 2], "OLD111"),
    }

    def read(name):
        return frames.get(name, pd.DataFrame()).copy()

    monkeypatch.setattr("cotdata.store.read_cot_legacy", read)
    return frames


def test_overlap_keeps_larger_scaled_oi(stitched):
    out = cot.get_cot("XX")
    assert list(out.index.strftime("%Y-%m-%d")) == [
        "2017-08-01", "2017-08-08", "2017-08-15", "2017-08-22", "2017-08-29"]
    # 08-01: predecessor only, scaled 40 -> 160
    assert out.loc["2017-08-01", "Open_Interest_All"] == 160
    # 08-08: primary 100 vs scaled predecessor 100 -> tie -> primary
    assert out.loc["2017-08-08", "Open_Interest_All"] == 100
    assert out.loc["2017-08-08", "Comm_Positions_Long_All"] == 10
    # 08-15: primary 50 vs scaled predecessor 120 -> predecessor (the Russell case)
    assert out.loc["2017-08-15", "Open_Interest_All"] == 120
    assert out.loc["2017-08-15", "Comm_Positions_Long_All"] == 12
    # 08-22: primary 400 vs scaled predecessor 80 -> primary
    assert out.loc["2017-08-22", "Open_Interest_All"] == 400
    # 08-29: primary only
    assert out.loc["2017-08-29", "Open_Interest_All"] == 500


def test_predecessor_rows_relabelled_to_primary_code(stitched):
    out = cot.get_cot("XX")
    assert (out["CFTC_Contract_Market_Code"] == "NEW111").all()


def test_result_is_sorted_and_unique(stitched):
    out = cot.get_cot("XX")
    assert out.index.is_monotonic_increasing
    assert not out.index.duplicated().any()


def test_no_oi_column_falls_back_to_primary_wins(monkeypatch):
    sym = Symbol(internal="YY", asset_class="test", is_equity=False, cftc_code="NEW222",
                 hist_codes=("OLD222",))
    monkeypatch.setitem(cot.REGISTRY, "YY", sym)
    idx = pd.DatetimeIndex(["2020-01-07"])
    frames = {
        "YY_NEW222": pd.DataFrame({"Comm_Positions_Long_All": [1]}, index=idx),
        "YY_OLD222": pd.DataFrame({"Comm_Positions_Long_All": [999]}, index=idx),
    }
    monkeypatch.setattr("cotdata.store.read_cot_legacy",
                        lambda n: frames.get(n, pd.DataFrame()).copy())
    out = cot.get_cot("YY")
    assert out.loc["2020-01-07", "Comm_Positions_Long_All"] == 1
