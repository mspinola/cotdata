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


def _sample_reconstructed():
    """Sample carrying the v2 reconstruction columns, with one 'raw' fallback row
    (row 2) where no individual contracts were available."""
    df = _sample()
    df["Volume_Reconstructed"] = [15, 14, 10, 18, 20]  # row idx 2 == front-month (raw)
    df["Volume_Source"] = ["reconstructed", "reconstructed", "raw",
                           "reconstructed", "reconstructed"]
    return df


def test_default_volume_view_is_byte_identical(store_env):
    """volume='front' (default) must not change the pre-v2 output shape."""
    from cotdata import store, get_prices
    store.write_prices("ES", "backadj", _sample_reconstructed(), source="test")

    df = get_prices("ES", "backadj")  # default volume='front'
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume",
                                "Open Interest", "Delivery Month"]
    assert "Volume_Source" not in df.columns
    assert "Volume_Reconstructed" not in df.columns
    assert df["Volume"].tolist() == [10] * 5          # untouched front-month


def test_reconstructed_volume_view(store_env):
    """volume='reconstructed' swaps Volume in, keeps per-row raw fallback, and
    surfaces Volume_Source for audit."""
    from cotdata import store, get_prices
    store.write_prices("ES", "backadj", _sample_reconstructed(), source="test")

    df = get_prices("ES", "backadj", volume="reconstructed")
    assert "Volume_Source" in df.columns
    assert "Volume_Reconstructed" not in df.columns   # internal — not leaked
    # reconstructed values flow into Volume; the 'raw' row keeps front-month (10)
    assert df["Volume"].tolist() == [15, 14, 10, 18, 20]
    assert df["Volume_Source"].tolist() == ["reconstructed", "reconstructed",
                                            "raw", "reconstructed", "reconstructed"]


def test_reconstructed_view_falls_back_on_pre_v2_store(store_env):
    """A store written before reconstruction existed → reconstructed view returns
    front-month volume labelled 'raw', never NaN."""
    from cotdata import store, get_prices
    store.write_prices("ES", "backadj", _sample(), source="test")  # no recon cols

    df = get_prices("ES", "backadj", volume="reconstructed")
    assert df["Volume"].tolist() == [10] * 5
    assert (df["Volume_Source"] == "raw").all()


def test_invalid_volume_arg_raises(store_env):
    from cotdata import store, get_prices
    store.write_prices("ES", "backadj", _sample(), source="test")
    with pytest.raises(ValueError):
        get_prices("ES", "backadj", volume="bogus")


def test_schema_version_and_require_schema(store_env):
    from cotdata import store, schema_version, require_schema
    import cotdata.config as cfg

    # Empty store → no manifest yet → load_manifest defaults to config.SCHEMA_VERSION
    assert schema_version() == cfg.SCHEMA_VERSION

    store.write_prices("ES", "backadj", _sample(), source="test")
    assert schema_version() == cfg.SCHEMA_VERSION      # stamped by the write
    require_schema(cfg.SCHEMA_VERSION)                 # satisfied → no raise
    with pytest.raises(RuntimeError):
        require_schema(cfg.SCHEMA_VERSION + 1)         # store too old
