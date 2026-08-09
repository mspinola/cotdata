"""The COT / price seam: one manifest per producer half (ADR-0007 step 1).

`_touch_manifest` is a read-modify-write. Two producers sharing one manifest.json
eventually lose an entry, and cotdata has two producers by design: the CFTC downloader
(any OS) and the price producer (Windows for Norgate). Splitting the manifest by half
means they never touch the same file.

Nothing writes the legacy aggregate any more. It is read only as a per-half fallback
for a store that has not run `--migrate-manifests` yet.
"""
import json

import pandas as pd
import pytest


@pytest.fixture()
def store_env(tmp_path, monkeypatch):
    monkeypatch.setenv("COTDATA_STORE", str(tmp_path))
    return tmp_path


def _prices():
    idx = pd.date_range("2020-01-01", periods=3, freq="D", name="Date")
    return pd.DataFrame({"Open": [1, 2, 3], "High": [2, 3, 4], "Low": [0, 1, 2],
                         "Close": [1.5, 2.5, 3.5]}, index=idx)


def _cot():
    idx = pd.date_range("2020-01-07", periods=2, freq="W-TUE", name="Date")
    return pd.DataFrame({"OPEN_INTEREST_XLS": [10, 20]}, index=idx)


# ── the seam itself ───────────────────────────────────────────────────────
def test_every_domain_declares_a_half():
    from cotdata import store
    from cotdata.status import _DOMAINS
    for domain in _DOMAINS:
        assert store.half_for(domain) in store.HALVES, domain


def test_an_undeclared_domain_refuses_to_write():
    """Adding a domain must force a decision about which side it belongs on."""
    from cotdata import store
    with pytest.raises(ValueError, match="not assigned to a producer half"):
        store.half_for("options_oi")


def test_price_and_cot_domains_land_on_opposite_sides():
    from cotdata import store
    assert store.half_for("prices") == "prices"
    assert store.half_for("metadata") == "prices"
    assert store.half_for("cot_legacy") == "cot"
    assert store.half_for("cot_tff") == "cot"


# ── writes ────────────────────────────────────────────────────────────────
def test_the_two_halves_do_not_share_a_file(store_env):
    from cotdata import config, store
    store.write_prices("ES", "backadj", _prices(), source="test")
    store.write_cot_legacy("ES_13874A", _cot(), source="test")

    prices_half = json.loads(config.manifest_path_for("prices").read_text())
    cot_half = json.loads(config.manifest_path_for("cot").read_text())
    assert "prices" in prices_half and "cot_legacy" not in prices_half
    assert "cot_legacy" in cot_half and "prices" not in cot_half


def test_a_clobbered_legacy_file_cannot_lose_an_entry(store_env):
    """The hazard, simulated. A concurrent producer (or a file-level sync between two
    stores) overwrites the shared aggregate wholesale. Both entries must survive,
    because a migrated store never consults that file."""
    from cotdata import config, store
    store.write_prices("ES", "backadj", _prices(), source="test")
    store.write_cot_legacy("ES_13874A", _cot(), source="test")

    # A racing producer rewrites the aggregate from its own stale copy.
    config.manifest_path().write_text(json.dumps({"schema_version": 2, "prices": {}}))

    m = store.load_manifest()
    assert "ES_backadj" in m["prices"]
    assert "ES_13874A" in m["cot_legacy"]


# ── merged read ───────────────────────────────────────────────────────────
def test_legacy_only_store_still_reads(store_env):
    """A store written by an older cotdata has no per-half files at all."""
    from cotdata import config, store
    config.manifest_path().parent.mkdir(parents=True, exist_ok=True)
    config.manifest_path().write_text(json.dumps(
        {"schema_version": 2, "prices": {"GC_backadj": {"n_rows": 7}}}))
    m = store.load_manifest()
    assert m["prices"]["GC_backadj"]["n_rows"] == 7
    assert m["schema_version"] == 2


def test_half_data_wins_over_stale_legacy(store_env):
    from cotdata import config, store
    config.manifest_path().parent.mkdir(parents=True, exist_ok=True)
    config.manifest_path().write_text(json.dumps(
        {"schema_version": 2, "prices": {"ES_backadj": {"n_rows": 1}}}))
    store.write_prices("ES", "backadj", _prices(), source="test")
    assert store.load_manifest()["prices"]["ES_backadj"]["n_rows"] == 3


def test_a_half_that_never_ran_still_resolves_from_legacy(store_env):
    """Mid-transition: the price producer has run on the new code, the COT one has not."""
    from cotdata import config, store
    config.manifest_path().parent.mkdir(parents=True, exist_ok=True)
    config.manifest_path().write_text(json.dumps(
        {"schema_version": 2, "cot_legacy": {"GC_088691": {"n_rows": 5}}}))
    store.write_prices("ES", "backadj", _prices(), source="test")

    m = store.load_manifest()
    assert m["prices"]["ES_backadj"]["n_rows"] == 3     # from the half
    assert m["cot_legacy"]["GC_088691"]["n_rows"] == 5  # from legacy


def test_empty_store_returns_the_empty_shape(store_env):
    from cotdata import store
    m = store.load_manifest()
    assert m["prices"] == {} and m["cot_legacy"] == {}
    assert m["schema_version"] >= 1


def test_manifest_path_for_rejects_an_unknown_half(store_env):
    from cotdata import config
    with pytest.raises(ValueError, match="unknown manifest half"):
        config.manifest_path_for("bars")


def test_status_check_reads_the_merged_manifest(store_env):
    """--check works off the manifest only, so it must see per-half writes."""
    from cotdata import status, store
    store.write_prices("ES", "backadj", _prices(), source="test")
    store.write_cot_legacy("ES_13874A", _cot(), source="test")
    s = status.summarize(store.load_manifest())
    assert "prices" in s["domains"] and "cot_legacy" in s["domains"]


# ── half-scoped CLI entry points ──────────────────────────────────────────
def test_cot_entry_point_refuses_price_actions(store_env, capsys):
    """A price host that could also run --cot-all becomes a second COT producer
    racing the first, which is exactly what the split manifests exist to contain."""
    from cotdata import update
    with pytest.raises(SystemExit):
        update.main_cot(["--build-databento"])
    assert "belong(s) to the prices half" in capsys.readouterr().err


def test_prices_entry_point_refuses_cot_actions(store_env, capsys):
    from cotdata import update
    with pytest.raises(SystemExit):
        update.main_prices(["--cot-all"])
    assert "belong(s) to the cot half" in capsys.readouterr().err


def test_read_only_actions_work_from_either_half(store_env):
    from cotdata import store, update
    store.write_prices("ES", "backadj", _prices(), source="test")
    update.main_cot(["--check"])
    update.main_prices(["--check"])


def test_combined_entry_point_applies_no_half_restriction():
    """cotdata-update keeps working for a single-machine deployment, so passing both
    halves' actions must not be rejected the way the scoped entry points do."""
    import argparse

    from cotdata import update
    parser = argparse.ArgumentParser()
    both = argparse.Namespace(build_databento=True, cot_all=True,
                              ingest_databento=False, cot_legacy=False,
                              cot_disagg=False, cot_tff=False)
    # Each scoped half rejects the other's action ...
    for half in ("cot", "prices"):
        with pytest.raises(SystemExit):
            update._reject_other_half(parser, both, half)
    # ... and main() only calls it when a half is set, which is what leaves
    # cotdata-update unrestricted.
    assert "half" in update.main.__code__.co_varnames


def test_every_action_flag_is_assigned_to_a_half():
    """A new action must be classified, or the entry points silently allow it.

    Read off the PARSER, not a list copied beside it. The copied list was the bug:
    it stayed green through this change while naming three flags that no longer
    exist, so it could not have caught a fourth being added either. Now a new flag
    fails here until it is put on one side of the seam or declared a non-action.
    """
    from cotdata import update
    flags = {a.dest for a in update._parser()._actions} - update._NON_ACTIONS
    assigned = set(update._HALF_ACTIONS["cot"]) | set(update._HALF_ACTIONS["prices"])
    assert flags == assigned


# ── dropping the legacy aggregate ─────────────────────────────────────────
def test_writes_no_longer_touch_the_legacy_aggregate(store_env):
    """A single file holding both halves is unsafe two ways: concurrent producers
    lose each other's entries, and a file-level sync between two stores resolves it
    last-writer-wins. Nothing writes it any more."""
    from cotdata import config, store
    store.write_prices("ES", "backadj", _prices(), source="test")
    assert config.manifest_path_for("prices").exists()
    assert not config.manifest_path().exists()


def test_migrate_splits_a_legacy_manifest(store_env):
    from cotdata import config, store
    config.manifest_path().parent.mkdir(parents=True, exist_ok=True)
    config.manifest_path().write_text(json.dumps({
        "schema_version": 2,
        "prices": {"ES_backadj": {"n_rows": 3}},
        "metadata": {"contract_specs": {"n_rows": 47}},
        "cot_legacy": {"GC_088691": {"n_rows": 5}},
        "cot_tff": {"ES_13874A": {"n_rows": 9}},
    }))
    added = store.migrate_manifests()
    assert added == {"prices": 2, "cot": 2}

    prices = json.loads(config.manifest_path_for("prices").read_text())
    cot = json.loads(config.manifest_path_for("cot").read_text())
    assert set(prices) == {"prices", "metadata", "schema_version"}
    assert set(cot) == {"cot_legacy", "cot_tff", "schema_version"}


def test_migrate_is_idempotent_and_never_resurrects_stale_entries(store_env):
    from cotdata import config, store
    store.write_prices("ES", "backadj", _prices(), source="test")   # 3 rows, current
    config.manifest_path().write_text(json.dumps(
        {"schema_version": 2, "prices": {"ES_backadj": {"n_rows": 999}}}))  # stale

    assert store.migrate_manifests() == {"prices": 0, "cot": 0}
    assert store.load_manifest()["prices"]["ES_backadj"]["n_rows"] == 3
    assert store.migrate_manifests() == {"prices": 0, "cot": 0}   # re-run is a no-op


def test_an_incomplete_half_file_still_falls_back_per_domain(store_env):
    """Found on a real store. manifests/prices.json held `prices` but not `metadata`,
    because the price producer had run on the new code while the metadata producer
    had not. A per-HALF fallback rule hid `metadata` entirely until the migration
    ran, so the fallback is per DOMAIN."""
    from cotdata import config, store
    config.manifest_path().parent.mkdir(parents=True, exist_ok=True)
    config.manifest_path().write_text(json.dumps({
        "schema_version": 2,
        "prices": {"OLD_backadj": {"n_rows": 1}},
        "metadata": {"contract_specs": {"n_rows": 47}},
    }))
    store.write_prices("ES", "backadj", _prices(), source="test")  # prices half only

    m = store.load_manifest()
    assert "ES_backadj" in m["prices"]                       # from the half file
    assert m["metadata"]["contract_specs"]["n_rows"] == 47   # still from legacy


def test_legacy_is_read_only_for_a_domain_that_has_not_migrated(store_env):
    """A store can be part migrated: prices written by the new code, COT still only
    in the aggregate."""
    from cotdata import config, store
    config.manifest_path().parent.mkdir(parents=True, exist_ok=True)
    config.manifest_path().write_text(json.dumps({
        "schema_version": 2,
        "prices": {"OLD_backadj": {"n_rows": 1}},
        "cot_legacy": {"GC_088691": {"n_rows": 5}},
    }))
    store.write_prices("ES", "backadj", _prices(), source="test")

    m = store.load_manifest()
    assert m["cot_legacy"]["GC_088691"]["n_rows"] == 5   # from legacy, cot unmigrated
    assert "ES_backadj" in m["prices"]                   # from the migrated half
    assert "OLD_backadj" not in m["prices"]              # legacy prices NOT consulted


def test_fully_migrated_store_ignores_the_legacy_file(store_env):
    from cotdata import config, store
    store.write_prices("ES", "backadj", _prices(), source="test")
    store.write_cot_legacy("GC_088691", _cot(), source="test")
    config.manifest_path().write_text(json.dumps(
        {"schema_version": 2, "prices": {"GHOST": {"n_rows": 1}},
         "cot_legacy": {"GHOST": {"n_rows": 1}}}))

    m = store.load_manifest()
    assert "GHOST" not in m["prices"] and "GHOST" not in m["cot_legacy"]
