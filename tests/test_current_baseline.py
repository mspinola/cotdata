"""Guard: the current-state COT write path must be unchanged by the vintage layer
(acceptance §7). The golden parquet was generated from the tree BEFORE the vintage
subsystem landed, so it pins what a consumer read back then; if this fails, the additive
change stopped being additive.

**This compares CONTENT, not bytes, and that distinction is the whole lesson here.** The
first version of this guard hashed the parquet bytes. It passed locally and failed on all
five CI Python versions with a *different* hash on each, because parquet encoding is a
property of the pandas/pyarrow build (writer metadata, compression, dictionary encoding),
not of this repo's code. A byte hash therefore asserts something the code does not
control and cannot keep true. What consumers actually depend on is the data that comes
back out, which is what is checked below.

dtypes are compared loosely for the same reason: CI spans Python 3.10-3.14 and so spans
pandas 2 and 3, which disagree about the string dtype. Values, columns, order and index
are all compared exactly.

The golden frame is deterministic and defined here; regenerate the fixture with:

    PYTHONPATH=src python tests/_gen_golden.py
"""
from pathlib import Path

import pandas as pd
import pytest

GOLDEN = Path(__file__).parent / "fixtures" / "golden" / "cot_legacy_golden.parquet"


def golden_frame() -> pd.DataFrame:
    """A fixed, realistic Legacy-COT slice (two weeks, one market)."""
    idx = pd.to_datetime(["2026-07-14", "2026-07-21"])
    idx.name = "Report_Date_as_MM_DD_YYYY"
    return pd.DataFrame(
        {
            "Market_and_Exchange_Names": ["GOLD - COMMODITY EXCHANGE INC."] * 2,
            "CFTC_Contract_Market_Code": ["088691", "088691"],
            "Open_Interest_All": [500000, 505000],
            "Comm_Positions_Long_All": [200000, 201000],
            "Comm_Positions_Short_All": [250000, 251000],
            "NonComm_Positions_Long_All": [150000, 151000],
            "NonComm_Positions_Short_All": [90000, 91000],
            "NonRept_Positions_Long_All": [40000, 41000],
            "NonRept_Positions_Short_All": [30000, 31000],
            "Traders_Tot_All": [280, 281],
            "Traders_Comm_Long_All": [50, 51],
            "Traders_Comm_Short_All": [55, 56],
            "Traders_NonComm_Long_All": [60, 61],
            "Traders_NonComm_Short_All": [45, 46],
        },
        index=idx,
    )


@pytest.fixture()
def store_env(tmp_path, monkeypatch):
    monkeypatch.setenv("COTDATA_STORE", str(tmp_path))
    return tmp_path


def test_current_cot_legacy_output_matches_pre_vintage_baseline(store_env):
    """What a consumer reads back must equal the pre-vintage baseline, exactly."""
    from cotdata import store

    assert GOLDEN.exists(), "golden baseline missing — run tests/_gen_golden.py on a clean tree"
    store.write_cot_legacy("GOLD_088691", golden_frame(), source="cftc")

    produced = store.read_cot_legacy("GOLD_088691")
    expected = pd.read_parquet(GOLDEN)

    pd.testing.assert_frame_equal(
        produced, expected,
        check_dtype=False,       # CI spans pandas 2 and 3, which disagree on the string dtype
        check_index_type=False,  # ...and on datetime resolution: pandas 3 writes us, 2 reads ns.
                                 # check_dtype does NOT cover the index, which needs this.
        check_freq=False,
        obj="current/ cot_legacy output changed — the vintage layer is no longer additive",
    )
    # Values, columns, order and index CONTENT are still compared exactly above; only the
    # storage dtypes are relaxed, and only because they are the library's choice not ours.
    assert list(produced.columns) == list(expected.columns)
    assert [str(d.date()) for d in produced.index] == [str(d.date()) for d in expected.index]


def test_current_write_path_is_deterministic_within_an_environment(store_env):
    """Byte-stability is still a real property WITHIN one environment, and it is the thing
    an atomic write must not break. Checked here rather than against a committed fixture,
    since across environments the bytes legitimately differ."""
    from cotdata import store

    store.write_cot_legacy("GOLD_088691", golden_frame(), source="cftc")
    first = (store_env / "cot_legacy" / "GOLD_088691.parquet").read_bytes()
    store.write_cot_legacy("GOLD_088691", golden_frame(), source="cftc")
    second = (store_env / "cot_legacy" / "GOLD_088691.parquet").read_bytes()

    assert first == second, "rewriting identical data produced different bytes"
