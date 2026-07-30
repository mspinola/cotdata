"""Guard: the current-state COT write path must stay byte-identical as the vintage
layer is added (acceptance §7). The golden parquet was generated from the tree BEFORE
the vintage subsystem landed; if this fails, the additive change stopped being additive.

The golden frame is deterministic and defined here; regenerate the fixture with:

    PYTHONPATH=src python tests/_gen_golden.py
"""
import hashlib
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


def test_current_cot_legacy_output_byte_identical(store_env):
    from cotdata import store

    store.write_cot_legacy("GOLD_088691", golden_frame(), source="cftc")
    produced = (store_env / "cot_legacy" / "GOLD_088691.parquet").read_bytes()

    assert GOLDEN.exists(), "golden baseline missing — run tests/_gen_golden.py on a clean tree"
    expected = GOLDEN.read_bytes()
    assert hashlib.sha256(produced).hexdigest() == hashlib.sha256(expected).hexdigest(), (
        "current/ cot_legacy output changed — the vintage layer is no longer additive"
    )
