"""The manifest seam, and what is left of it now that one half is empty.

ADR-0007 step 1 split the manifest per producer half, because `_touch_manifest` is a
read-modify-write and two producers sharing one file eventually lose an entry. cotdata
had two producers by design: the CFTC downloader and the price producer.

**It has one now.** Every price producer moved to marketdata, so nothing writes the
`prices` half any more, and the `cotdata-prices` entry point that enforced the split is
gone with it. What survives is the part these tests exist for: a REAL store on disk
still carries `prices` and `metadata` entries from before the move, and the code has to
keep reading, migrating and reconciling them rather than stranding them. So `prices`
appears throughout this file as LEGACY data written by hand, never through a writer —
there is no writer.

Nothing writes the legacy aggregate `manifest.json` either. It is read only as a
per-domain fallback for a store that has not run `--migrate-manifests` yet.
"""
import json

import pandas as pd
import pytest


@pytest.fixture()
def store_env(tmp_path, monkeypatch):
    monkeypatch.setenv("COTDATA_STORE", str(tmp_path))
    return tmp_path


def _legacy_prices_half(root, entries=None):
    """Write a `manifests/prices.json` by hand, as a pre-ADR-0007 store carries it.

    By hand because there is no writer any more — which is the condition under test.
    """
    import json as _json
    path = root / "manifests" / "prices.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json.dumps({
        "schema_version": 2,
        "prices": entries if entries is not None else {"ES_backadj": {"n_rows": 3}},
    }))
    return path


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


def test_the_retired_price_domains_are_still_declared():
    """`prices` and `metadata` have no writer left, and are still on the map on
    purpose: `migrate_manifests` SKIPS an undeclared domain, so undeclaring them would
    strand a real store's existing entries in the legacy aggregate forever, and
    `reconcile_manifest` would resolve their directory by fallback instead of by
    declaration."""
    from cotdata import store
    assert store.half_for("prices") == "prices"
    assert store.half_for("metadata") == "prices"
    assert store.half_for("cot_legacy") == "cot"
    assert store.half_for("cot_tff") == "cot"


def test_nothing_can_write_the_price_half_any_more():
    """The other side of the same coin: declared for reading, with no way to write."""
    from cotdata import store
    for gone in ("write_prices", "read_prices", "write_metadata", "read_metadata"):
        assert not hasattr(store, gone), gone


# ── writes ────────────────────────────────────────────────────────────────
def test_a_cot_write_never_touches_the_price_half(store_env):
    """The split's remaining job. The price half is now a file only a previous version
    wrote, so the COT producer must leave it exactly alone — a read-modify-write that
    reached across would destroy data no producer can regenerate here."""
    from cotdata import config, store
    legacy = _legacy_prices_half(store_env)
    before = legacy.read_text()

    store.write_cot_legacy("ES_13874A", _cot(), source="test")

    cot_half = json.loads(config.manifest_path_for("cot").read_text())
    assert "cot_legacy" in cot_half and "prices" not in cot_half
    assert legacy.read_text() == before          # byte-for-byte untouched


def test_a_clobbered_legacy_file_cannot_lose_an_entry(store_env):
    """The hazard, simulated. A file-level sync between two stores overwrites the
    shared aggregate wholesale. Both entries must survive, because a migrated store
    never consults that file."""
    from cotdata import config, store
    _legacy_prices_half(store_env)
    store.write_cot_legacy("ES_13874A", _cot(), source="test")

    # A stale sync rewrites the aggregate from an older copy.
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
        {"schema_version": 2, "cot_legacy": {"ES_13874A": {"n_rows": 1}}}))
    store.write_cot_legacy("ES_13874A", _cot(), source="test")
    assert store.load_manifest()["cot_legacy"]["ES_13874A"]["n_rows"] == 2


def test_a_half_that_never_ran_still_resolves_from_legacy(store_env):
    """The real shape of an un-migrated store today: the COT producer has run on the
    new code, and the prices left behind by the old one are only in the aggregate."""
    from cotdata import config, store
    config.manifest_path().parent.mkdir(parents=True, exist_ok=True)
    config.manifest_path().write_text(json.dumps(
        {"schema_version": 2, "prices": {"GC_backadj": {"n_rows": 5}}}))
    store.write_cot_legacy("ES_13874A", _cot(), source="test")

    m = store.load_manifest()
    assert m["cot_legacy"]["ES_13874A"]["n_rows"] == 2  # from the half
    assert m["prices"]["GC_backadj"]["n_rows"] == 5     # from legacy


def test_empty_store_returns_the_empty_shape(store_env):
    """COT domains only. A store created from here on never gets a `prices` key,
    because nothing here can write one."""
    from cotdata import store
    m = store.load_manifest()
    assert m["cot_legacy"] == {} and m["cot_supplemental"] == {}
    assert "prices" not in m and "metadata" not in m
    assert m["schema_version"] >= 1


def test_manifest_path_for_rejects_an_unknown_half(store_env):
    from cotdata import config
    with pytest.raises(ValueError, match="unknown manifest half"):
        config.manifest_path_for("bars")


def test_status_check_reads_the_merged_manifest(store_env):
    """--check works off the manifest only, so it must see both a live COT write and
    the legacy price entries a real store still carries."""
    from cotdata import status, store
    _legacy_prices_half(store_env)
    store.write_cot_legacy("ES_13874A", _cot(), source="test")
    s = status.summarize(store.load_manifest())
    assert "prices" in s["domains"] and "cot_legacy" in s["domains"]


# ── the entry points that are left ────────────────────────────────────────
def test_cotdata_cot_is_an_alias_and_not_a_scoped_half(store_env):
    """`cotdata-cot` survives because the scheduled jobs call it by name, but it no
    longer SCOPES anything: there is no other half to refuse. It must behave exactly
    like `cotdata-update`, so a wrapper script keeps working unchanged."""
    from cotdata import update

    assert not hasattr(update, "main_prices")       # the price half is gone
    assert not hasattr(update, "_reject_other_half")
    assert not hasattr(update, "_HALF_ACTIONS")

    update.main_cot(["--check"])                    # read-only, no network
    update.main(["--check"])


def test_the_retired_price_flags_are_refused_not_ignored(store_env):
    """A scheduler line still carrying a price action must fail loudly. argparse
    rejects an unknown flag, so this really guards against re-adding one as a silent
    no-op: a nightly job that keeps exiting 0 while fetching nothing is a store that
    quietly stops being updated."""
    from cotdata import update
    for flag in ("--prices", "--metadata", "--ingest-databento", "--build-databento"):
        with pytest.raises(SystemExit) as ei:
            update.main([flag])
        assert ei.value.code not in (0, None), flag


# ── dropping the legacy aggregate ─────────────────────────────────────────
def test_writes_no_longer_touch_the_legacy_aggregate(store_env):
    """A single file holding both halves is unsafe two ways: concurrent producers
    lose each other's entries, and a file-level sync between two stores resolves it
    last-writer-wins. Nothing writes it any more."""
    from cotdata import config, store
    store.write_cot_legacy("ES_13874A", _cot(), source="test")
    assert config.manifest_path_for("cot").exists()
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
    store.write_cot_legacy("ES_13874A", _cot(), source="test")   # 2 rows, current
    config.manifest_path().write_text(json.dumps(
        {"schema_version": 2, "cot_legacy": {"ES_13874A": {"n_rows": 999}}}))  # stale

    assert store.migrate_manifests() == {"prices": 0, "cot": 0}
    assert store.load_manifest()["cot_legacy"]["ES_13874A"]["n_rows"] == 2
    assert store.migrate_manifests() == {"prices": 0, "cot": 0}   # re-run is a no-op


def test_an_incomplete_half_file_still_falls_back_per_domain(store_env):
    """Found on a real store. manifests/prices.json held `prices` but not `metadata`,
    because the price producer had run on the new code while the metadata producer
    had not. A per-HALF fallback rule hid `metadata` entirely until the migration
    ran, so the fallback is per DOMAIN.

    Still exercised with the price domains, because that is still the shape a real
    store has: a half file holding one retired domain and not the other."""
    from cotdata import config, store
    config.manifest_path().parent.mkdir(parents=True, exist_ok=True)
    config.manifest_path().write_text(json.dumps({
        "schema_version": 2,
        "prices": {"OLD_backadj": {"n_rows": 1}},
        "metadata": {"contract_specs": {"n_rows": 47}},
    }))
    _legacy_prices_half(store_env)                  # prices half only, no metadata

    m = store.load_manifest()
    assert "ES_backadj" in m["prices"]                       # from the half file
    assert m["metadata"]["contract_specs"]["n_rows"] == 47   # still from legacy


def test_legacy_is_read_only_for_a_domain_that_has_not_migrated(store_env):
    """A store can be part migrated: one domain moved to its half file, another still
    only in the aggregate. Once a domain has a half file the aggregate is not consulted
    for it at all, so a stale entry there cannot come back."""
    from cotdata import config, store
    config.manifest_path().parent.mkdir(parents=True, exist_ok=True)
    config.manifest_path().write_text(json.dumps({
        "schema_version": 2,
        "prices": {"OLD_backadj": {"n_rows": 1}},
        "cot_legacy": {"GC_088691": {"n_rows": 5}},
    }))
    _legacy_prices_half(store_env)                  # prices migrated, cot not

    m = store.load_manifest()
    assert m["cot_legacy"]["GC_088691"]["n_rows"] == 5   # from legacy, cot unmigrated
    assert "ES_backadj" in m["prices"]                   # from the migrated half
    assert "OLD_backadj" not in m["prices"]              # legacy prices NOT consulted


def test_fully_migrated_store_ignores_the_legacy_file(store_env):
    from cotdata import config, store
    _legacy_prices_half(store_env)
    store.write_cot_legacy("GC_088691", _cot(), source="test")
    config.manifest_path().write_text(json.dumps(
        {"schema_version": 2, "prices": {"GHOST": {"n_rows": 1}},
         "cot_legacy": {"GHOST": {"n_rows": 1}}}))

    m = store.load_manifest()
    assert "GHOST" not in m["prices"] and "GHOST" not in m["cot_legacy"]
