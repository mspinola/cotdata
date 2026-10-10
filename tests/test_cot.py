"""Tests for the consumer COT read API (cotdata.cot.get_cot): report dispatch,
symbol/code lookup, and the predecessor-code stitching (scale + de-dup) that
splices an instrument's earlier CFTC listing behind its current one."""
from unittest import mock

import pandas as pd
import pytest

from cotdata import cot
from cotdata.registry import REGISTRY

_CODE_COL = "CFTC_Contract_Market_Code"


def _df(dates, oi, code):
    return pd.DataFrame(
        {"Open_Interest_All": oi, _CODE_COL: [code] * len(dates)},
        index=pd.DatetimeIndex(dates),
    )


def test_unknown_report_raises():
    with pytest.raises(ValueError, match="Unknown report type"):
        cot.get_cot("ES", report="bogus")


def test_simple_symbol_returns_primary_by_symbol_code_key():
    """A symbol with no hist_codes or sum_codes (GC) reads exactly `<internal>_<cftc_code>`
    from the requested report and returns it unchanged."""
    primary = _df(["2020-01-01"], [10], REGISTRY["GC"].cftc_code)
    with mock.patch("cotdata.cot.store.read_cot_legacy", return_value=primary) as m:
        out = cot.get_cot("GC")
    m.assert_called_once_with(f"GC_{REGISTRY['GC'].cftc_code}")
    pd.testing.assert_frame_equal(out, primary)


def test_report_dispatch_uses_matching_store_reader():
    """report='disagg' must read from the disagg store, not legacy."""
    with mock.patch("cotdata.cot.store.read_cot_disagg",
                    return_value=_df(["2020-01-01"], [1], REGISTRY["GC"].cftc_code)) as m:
        cot.get_cot("GC", report="disagg")
    m.assert_called_once_with(f"GC_{REGISTRY['GC'].cftc_code}")


def test_lookup_by_primary_cftc_code_resolves_symbol():
    """Passing the CFTC code instead of the internal symbol resolves to the symbol
    and reads the same `<internal>_<code>` key."""
    primary = _df(["2020-01-01"], [10], REGISTRY["GC"].cftc_code)
    with mock.patch("cotdata.cot.store.read_cot_legacy", return_value=primary) as m:
        cot.get_cot(REGISTRY["GC"].cftc_code)          # "088691"
    m.assert_called_once_with(f"GC_{REGISTRY['GC'].cftc_code}")


def test_hist_code_stitching_scales_and_dedups_by_larger_oi():
    """LBR splices predecessor 058643 (scale 4.0) behind primary 058644:
      - predecessor numeric columns are scaled by 4.0 (a contract-size bridge),
      - predecessor rows are re-stamped with the primary code,
      - overlapping dates keep the row with the larger SCALED open interest,
        whichever code it came from (tests/test_cot_stitch.py covers ties and
        the no-OI fallback),
      - the result is sorted ascending."""
    lbr = REGISTRY["LBR"]
    primary_key = f"LBR_{lbr.cftc_code}"            # LBR_058644
    hist_key = "LBR_058643"

    primary = _df(["2020-01-08", "2020-01-15", "2020-01-22"], [100, 200, 300], lbr.cftc_code)
    hist = _df(["2020-01-01", "2020-01-08", "2020-01-15"], [10, 999, 20], "058643")

    def reader(key):
        return {primary_key: primary, hist_key: hist}.get(key, pd.DataFrame())

    with mock.patch("cotdata.cot.store.read_cot_legacy", side_effect=reader):
        out = cot.get_cot("LBR")

    assert list(out.index) == list(pd.DatetimeIndex(
        ["2020-01-01", "2020-01-08", "2020-01-15", "2020-01-22"]))
    assert out.loc["2020-01-01", "Open_Interest_All"] == 40      # 10 * 4.0 (scaled predecessor)
    assert out.loc["2020-01-08", "Open_Interest_All"] == 3996    # 999*4 > 100: predecessor wins
    assert out.loc["2020-01-15", "Open_Interest_All"] == 200     # 200 > 20*4: primary wins
    assert out.loc["2020-01-22", "Open_Interest_All"] == 300
    assert (out[_CODE_COL] == lbr.cftc_code).all()               # predecessor re-stamped to primary code
