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


def _cot_sample():
    idx = pd.date_range("2020-01-07", periods=3, freq="W-TUE", name="Report_Date")
    return pd.DataFrame({"Open_Interest_All": [100, 110, 120],
                         "NonComm_Positions_Long_All": [10, 12, 14]}, index=idx)


def test_cot_roundtrip_and_manifest(store_env):
    """A write lands its parquet AND its manifest entry, with the provenance a consumer
    reads. The price equivalent of this test is in marketdata now: every bar producer,
    and the store-level pair they wrote through, left with ADR-0007."""
    from cotdata import load_manifest, store
    store.write_cot_legacy("ES_13874A", _cot_sample(), source="test")

    df = store.read_cot_legacy("ES_13874A")
    assert list(df.columns) == ["Open_Interest_All", "NonComm_Positions_Long_All"]
    assert len(df) == 3
    assert store.read_cot_legacy("ZZ_000000").empty      # absent table -> empty

    m = load_manifest()
    assert m["cot_legacy"]["ES_13874A"]["n_rows"] == 3
    assert m["cot_legacy"]["ES_13874A"]["source"] == "test"
    assert m["cot_legacy"]["ES_13874A"]["last_date"] == "2020-01-21"


def test_reconcile_prunes_ghosts_keeps_real(store_env):
    from cotdata import store
    # a real cot_legacy entry — writes a parquet file AND a manifest entry
    idx = pd.date_range("2026-01-06", periods=3, freq="7D", name="Report_Date")
    store.write_cot_legacy("ES_001602", pd.DataFrame({"Open_Interest_All": [1, 2, 3]}, index=idx), source="test")

    # inject a bare-code ghost (no file) and a retired 'cot' domain (no dir/files)
    m = store.load_manifest()
    m["cot_legacy"]["001602"] = {"last_date": "2018-06-05", "n_rows": 10, "source": "cftc", "updated_at": "x"}
    m["cot"] = {"099741": {"last_date": "2018-01-01", "n_rows": 5, "source": "cftc", "updated_at": "x"}}
    store._write_manifest(m)

    pruned = store.reconcile_manifest()
    assert pruned["cot_legacy"] == ["001602"]   # bare ghost pruned
    assert "cot" in pruned                       # dead domain pruned

    m2 = store.load_manifest()
    assert "ES_001602" in m2["cot_legacy"]       # real prefixed entry kept
    assert "001602" not in m2["cot_legacy"]      # ghost gone
    assert "cot" not in m2                        # emptied domain removed


def test_reconcile_noop_when_clean(store_env):
    from cotdata import store
    idx = pd.date_range("2026-01-06", periods=2, freq="7D", name="Report_Date")
    store.write_cot_legacy("ES_001602", pd.DataFrame({"x": [1, 2]}, index=idx), source="test")
    assert store.reconcile_manifest() == {}      # nothing to prune


def test_consumer_bar_api_is_gone(store_env):
    """ADR-0007 §7.5: this package answers positioning questions, not price ones.

    Asserted rather than assumed. A re-export costs one line to add back, and a
    consumer that finds `cotdata.get_prices` importable again will use it — landing
    on a store the nightly job no longer fills, which reads as stale data rather
    than as a wrong import. Bars come from `marketdata.get_bars`.
    """
    import importlib

    import cotdata

    for name in ("get_prices", "roll_dates"):
        assert not hasattr(cotdata, name)
        assert name not in cotdata.__all__
    for mod in ("cotdata.prices", "cotdata.providers.norgate",
                "cotdata.providers.yfinance", "cotdata.providers.databento"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(mod)


def test_no_price_surface_of_any_kind_survives(store_env):
    """Contract specs went with the bars (§7.2), and the databento producer that was
    the last writer of `prices/` followed them. Both domains stay DECLARED so a
    pre-ADR-0007 store still migrates and reconciles its entries instead of stranding
    them — but there is no writer, and no reader, for either.
    """
    from cotdata import store

    for domain in ("prices", "metadata"):
        assert store.half_for(domain) == "prices"     # still mapped, for old stores
    for name in ("write_prices", "read_prices",
                 "write_metadata", "upsert_metadata", "read_metadata"):
        assert not hasattr(store, name), name


def test_schema_version_and_require_schema(store_env):
    import cotdata.config as cfg
    from cotdata import require_schema, schema_version, store

    # Empty store → no manifest yet → load_manifest defaults to config.SCHEMA_VERSION
    assert schema_version() == cfg.SCHEMA_VERSION

    store.write_cot_legacy("ES_13874A", _cot_sample(), source="test")
    assert schema_version() == cfg.SCHEMA_VERSION      # stamped by the write
    require_schema(cfg.SCHEMA_VERSION)                 # satisfied → no raise
    with pytest.raises(RuntimeError):
        require_schema(cfg.SCHEMA_VERSION + 1)         # store too old
