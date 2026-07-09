"""Store round-trip + consumer API, using a tmp store (no network)."""
import pandas as pd
import pytest


@pytest.fixture()
def store_env(tmp_path, monkeypatch):
    monkeypatch.setenv("COTDATA_STORE", str(tmp_path))
    # re-import under the patched env not needed: config reads env at call time
    return tmp_path


def _sample():
    idx = pd.date_range("2020-01-01", periods=5, freq="D", name="Date")
    return pd.DataFrame({
        "Open": [1, 2, 3, 4, 5], "High": [2, 3, 4, 5, 6],
        "Low": [0, 1, 2, 3, 4], "Close": [1.5, 2.5, 3.5, 4.5, 5.5],
        "Volume": [10] * 5, "Open Interest": [100] * 5,
        "Delivery Month": ["202003"] * 3 + ["202006"] * 2,
    }, index=idx)


def test_prices_roundtrip_and_manifest(store_env):
    from cotdata import store, get_prices, load_manifest
    store.write_prices("ES", "backadj", _sample(), source="test")

    df = get_prices("ES", "backadj")
    assert list(df.columns)[:6] == ["Open", "High", "Low", "Close", "Volume", "Open Interest"]
    assert df.index.name == "Date" and len(df) == 5

    m = load_manifest()
    assert m["prices"]["ES_backadj"]["n_rows"] == 5
    assert m["prices"]["ES_backadj"]["source"] == "test"
    assert m["prices"]["ES_backadj"]["last_date"] == "2020-01-05"


def test_roll_dates_from_delivery_month(store_env):
    from cotdata import store, roll_dates
    store.write_prices("ES", "backadj", _sample(), source="test")
    rolls = roll_dates("ES", "backadj")
    # first bar + the delivery-month change on day 4
    assert len(rolls) == 2
    assert pd.Timestamp("2020-01-04") in rolls


def test_missing_symbol_returns_empty(store_env):
    from cotdata import get_prices
    assert get_prices("ZZ", "backadj").empty
