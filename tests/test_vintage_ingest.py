"""Vintage ingest (commit 2): change-only observations, field-level revisions, PIT asof.

Offline — canonical frames are built directly, matching the repo's synthetic-fixture
idiom. Covers handoff §8: idempotent ingest, byte-change/data-same, single-field
revision, PIT query, revision depth, holiday week, and validation failure.
"""
import datetime as dt

import pandas as pd
import pytest


@pytest.fixture()
def store_env(tmp_path, monkeypatch):
    monkeypatch.setenv("COTDATA_STORE", str(tmp_path))
    return tmp_path


def _wide(report_date, *, code="088691", comm_long=200000, comm_short=250000,
          oi=500000):
    """One-market, one-week Legacy wide frame (the shape providers/cftc.py emits)."""
    idx = pd.to_datetime([report_date])
    idx.name = "Report_Date_as_MM_DD_YYYY"
    return pd.DataFrame({
        "Market_and_Exchange_Names": ["GOLD"],
        "CFTC_Contract_Market_Code": [code],
        "Open_Interest_All": [oi],
        "Comm_Positions_Long_All": [comm_long],
        "Comm_Positions_Short_All": [comm_short],
        "NonComm_Positions_Long_All": [150000],
        "NonComm_Positions_Short_All": [90000],
        "NonRept_Positions_Long_All": [40000],
        "NonRept_Positions_Short_All": [30000],
        "Traders_Tot_All": [280],
        "Traders_Comm_Long_All": [50], "Traders_Comm_Short_All": [55],
        "Traders_NonComm_Long_All": [60], "Traders_NonComm_Short_All": [45],
    }, index=idx)


def _canon(report_date, **kw):
    from cotdata.vintage_ingest import canonicalize_legacy
    return canonicalize_legacy(_wide(report_date, **kw))


def test_idempotent_ingest(store_env):
    from cotdata import vintage_ingest as vi
    c = _canon("2026-07-21")
    r1 = vi.ingest_canonical(c, snapshot_id="s1")
    assert r1["observations"] == 3 and r1["revisions"] == 0  # 3 categories, first sighting
    r2 = vi.ingest_canonical(c, snapshot_id="s1")
    assert r2["observations"] == 0 and r2["revisions"] == 0  # nothing changed


def test_byte_change_data_same_is_noop(store_env):
    """A regenerated archive (new snapshot id) with identical values must produce no
    new observations and no revisions."""
    from cotdata import vintage_ingest as vi
    c = _canon("2026-07-21")
    vi.ingest_canonical(c, snapshot_id="s1")
    r = vi.ingest_canonical(c, snapshot_id="s2-regenerated")
    assert r["observations"] == 0 and r["revisions"] == 0


def test_single_field_revision(store_env):
    from cotdata import vintage_ingest as vi
    vi.ingest_canonical(_canon("2026-07-21", comm_short=250000), snapshot_id="s1")
    r = vi.ingest_canonical(_canon("2026-07-21", comm_short=251000), snapshot_id="s2")
    # only the commercial category row changed → 1 new obs, 1 revision
    assert r["observations"] == 1 and r["revisions"] == 1
    rev = vi.read_revisions()
    assert len(rev) == 1
    row = rev.iloc[0]
    assert row["field"] == "short_contracts"
    assert row["old_value"] == "250000" and row["new_value"] == "251000"
    assert row["category"] == "commercial"
    assert row["delta"] == 1000.0


def test_pit_query_returns_pre_revision_value(store_env):
    from cotdata import vintage_ingest as vi
    t1 = dt.datetime(2026, 7, 24, 16, 0, tzinfo=dt.timezone.utc)
    t2 = dt.datetime(2026, 7, 31, 16, 0, tzinfo=dt.timezone.utc)
    vi.ingest_canonical(_canon("2026-07-21", comm_short=250000), snapshot_id="s1", observed_at=t1)
    vi.ingest_canonical(_canon("2026-07-21", comm_short=251000), snapshot_id="s2", observed_at=t2)

    between = dt.datetime(2026, 7, 28, tzinfo=dt.timezone.utc)
    df = vi.asof(between, report_date="2026-07-21", market_code="088691")
    comm = df[df["category"] == "commercial"].iloc[0]
    assert comm["short_contracts"] == 250000  # pre-revision value

    after = dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc)
    df2 = vi.asof(after, report_date="2026-07-21", market_code="088691")
    assert df2[df2["category"] == "commercial"].iloc[0]["short_contracts"] == 251000


def test_revision_depth_age_days(store_env):
    """A restatement of a year-old report gets the correct revision depth."""
    from cotdata import vintage_ingest as vi
    t1 = dt.datetime(2025, 7, 18, tzinfo=dt.timezone.utc)
    t2 = dt.datetime(2026, 7, 30, tzinfo=dt.timezone.utc)
    vi.ingest_canonical(_canon("2025-07-15", comm_short=250000), snapshot_id="s1", observed_at=t1)
    vi.ingest_canonical(_canon("2025-07-15", comm_short=260000), snapshot_id="s2", observed_at=t2)
    rev = vi.read_revisions()
    expected = (pd.Timestamp("2026-07-30") - pd.Timestamp("2025-07-15")).days
    assert int(rev.iloc[0]["age_days"]) == expected == 380


def test_holiday_week_not_normalised_to_tuesday(store_env):
    """A Monday as-of date is stored as Monday, not rounded to Tuesday."""
    from cotdata import vintage_ingest as vi
    vi.ingest_canonical(_canon("2025-12-29"), snapshot_id="s1")  # 2025-12-29 is a Monday
    obs = vi.read_observations()
    stored = pd.Timestamp(obs.iloc[0]["report_date"])
    assert stored.weekday() == 0 and stored.date() == dt.date(2025, 12, 29)


def test_validation_failure_raises_before_writing(store_env):
    from cotdata import vintage_ingest as vi
    bad = _canon("2026-07-21")
    bad.loc[0, "category"] = "bogus"  # outside the legacy controlled vocabulary
    with pytest.raises(vi.ValidationError):
        vi.ingest_canonical(bad, snapshot_id="s1")
    assert vi.read_observations().empty  # nothing partially written


def test_asof_tiebreak_is_deterministic(store_env):
    """Two snapshots sharing an observed_at must resolve deterministically: the
    lexicographically greater snapshot_id wins, NOT file/append order. Append order is
    set opposite to the winner so a naive first-occurrence pick would return 'a-early'."""
    from cotdata import vintage_ingest as vi
    t = dt.datetime(2026, 7, 24, 16, 0, tzinfo=dt.timezone.utc)
    # appended first (lower index): 'a-early' = 111111
    vi.ingest_canonical(_canon("2026-07-21", comm_short=111111), snapshot_id="a-early", observed_at=t)
    # appended second (higher index): 'z-late' = 222222, same observed_at → a genuine tie
    vi.ingest_canonical(_canon("2026-07-21", comm_short=222222), snapshot_id="z-late", observed_at=t)

    comm = vi.asof(t, report_date="2026-07-21", market_code="088691")
    comm = comm[comm["category"] == "commercial"].iloc[0]
    # naive first-occurrence would pick a-early (111111); deterministic picks z-late
    assert comm["snapshot_id"] == "z-late" and comm["short_contracts"] == 222222
    # stable across repeated calls
    again = vi.asof(t, report_date="2026-07-21", market_code="088691")
    assert again[again["category"] == "commercial"].iloc[0]["snapshot_id"] == "z-late"


def test_norm_is_independent_of_numpy_scalar_formatting():
    """The hash must not depend on numpy/pandas str() behaviour: np scalars unwrap to
    Python natives, and int-valued floats unify with ints."""
    np = pytest.importorskip("numpy")
    from cotdata.vintage_ingest import _norm, row_sha256
    assert _norm(np.int64(250000)) == _norm(250000) == _norm(np.float64(250000.0)) == "250000"
    assert _norm(np.bool_(True)) == _norm(True) == "1"
    assert _norm(None) == _norm(pd.NA) == _norm(float("nan")) == ""
    # a non-integer ratio formats explicitly, not via numpy's repr
    assert _norm(np.float64(0.4375)) == _norm(0.4375) == "0.4375"
    # whole-row: numpy-typed and python-typed rows hash identically
    a = {"long_contracts": np.int64(10), "cr4_net_long": np.float64(0.25)}
    b = {"long_contracts": 10, "cr4_net_long": 0.25}
    assert row_sha256(a) == row_sha256(b)


def test_asof_returns_one_row_per_key_with_duplicate_index(store_env):
    """_latest_by_key must not round-trip through .loc on a duplicated index — that
    returns every matching label and yields several rows per natural key."""
    from cotdata import vintage_ingest as vi
    t = dt.datetime(2026, 7, 24, 16, tzinfo=dt.timezone.utc)
    vi.ingest_canonical(_canon("2026-07-21"), snapshot_id="s1", observed_at=t)
    obs = vi.read_observations()
    dup = pd.concat([obs, obs])  # duplicated index labels, as a naive concat would give
    latest = vi._latest_by_key(dup)
    assert len(latest) == 3  # 3 categories, not 6
    assert latest.groupby(vi.NATURAL_KEY, dropna=False).size().max() == 1


def test_hash_change_without_field_diff_raises(store_env, monkeypatch):
    """The consistency invariant: if the stored hash says 'changed' but no VALUE_FIELDS
    differ, refuse to write an observation with no revision detail."""
    from cotdata import vintage_ingest as vi
    vi.ingest_canonical(_canon("2026-07-21"), snapshot_id="s1")
    # corrupt the comparison: make every freshly computed hash differ, while the actual
    # field values stay identical — exactly the drift the invariant guards against.
    monkeypatch.setattr(vi, "row_sha256", lambda row: "deadbeef" * 8)
    with pytest.raises(vi.ConsistencyError, match="no VALUE_FIELDS differ"):
        vi.ingest_canonical(_canon("2026-07-21"), snapshot_id="s2")


def test_concurrent_ingest_lock_fails_loudly(store_env):
    """A second writer must error, not silently last-writer-wins over the first."""
    from cotdata import vintage
    from cotdata import vintage_ingest as vi
    with vi._WriteLock(vintage.vintage_root()):
        with pytest.raises(RuntimeError, match="single-writer"):
            vi.ingest_canonical(_canon("2026-07-21"), snapshot_id="s1")


def test_cli_ingest_exits_nonzero_when_revisions_are_recorded(store_env, capsys):
    """A scheduled run's stdout goes nowhere, so a silent exit-0 after detecting a
    restatement would defeat the subsystem. Revisions must surface as a non-zero exit."""
    from cotdata import vintage, vintage_cli
    from cotdata import vintage_ingest as vi

    # two vintages of the same report date, second one revised
    vi.ingest_canonical(_canon("2026-07-21", comm_short=250000), snapshot_id="s1",
                        observed_at=dt.datetime(2026, 7, 24, tzinfo=dt.timezone.utc))
    vi.ingest_canonical(_canon("2026-07-21", comm_short=251000), snapshot_id="s2",
                        observed_at=dt.datetime(2026, 7, 31, tzinfo=dt.timezone.utc))
    assert not vi.read_revisions().empty

    # drive the real CLI path: a snapshot whose raw file re-parses to the revised values
    raw = store_env / "vintage" / "raw" / "annual_zip" / "2026"
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "f.zip").write_bytes(b"x")
    vintage._write_manifest({"schema_version": 1, "snapshots": [{
        "snapshot_id": "s3", "report_type": "legacy", "source_kind": "annual_zip",
        "local_path": "vintage/raw/annual_zip/2026/f.zip", "parse_status": "pending",
        "restatement_suspect": True, "report_year": 2025,
        "retrieved_at": "2026-07-31T21:00:00Z",
    }]})

    with pytest.raises(SystemExit) as exc:
        vintage_cli.main(["ingest", "--pending"])
    msg = str(exc.value)
    assert "restatement suspect" in msg and "cotdata-vintage diff" in msg
    assert "notification, not a failure" in msg  # the data IS committed
    assert "RESTATEMENT SUSPECT" in capsys.readouterr().out


def test_restatement_alert_does_not_fire_forever(store_env):
    """A suspect recorded in an EARLIER run must not keep failing every later run —
    an alert that never clears is one that gets switched off."""
    from cotdata import vintage, vintage_cli
    vintage._write_manifest({"schema_version": 1, "snapshots": [{
        "snapshot_id": "old", "report_type": "legacy", "source_kind": "annual_zip",
        "local_path": "vintage/raw/annual_zip/2025/old.zip", "parse_status": "ok",
        "restatement_suspect": True, "report_year": 2025,
        "retrieved_at": "2026-01-01T00:00:00Z",
    }]})
    # --pending selects nothing (that snapshot is already parse_status=ok), so a later
    # run is quiet even though the store still remembers the suspect.
    assert vintage_cli.main(["ingest", "--pending"]) == 0


def test_restatement_suspect_is_reported_prominently(store_env, capsys):
    from cotdata import vintage_cli
    suspects = [{"report_type": "legacy", "report_year": 2025,
                 "retrieved_at": "2026-07-31T21:00:00Z"}]
    vintage_cli._report_revisions(0, suspects)
    out = capsys.readouterr().out
    assert "CLOSED-YEAR RESTATEMENT SUSPECT" in out
    assert "legacy 2025" in out


def test_oi_over_sum_warns_not_raises(store_env):
    from cotdata import vintage_ingest as vi
    # commercial long+short (200000+250000) already < OI; force a breach on OI instead
    c = _canon("2026-07-21", comm_long=400000, comm_short=400000, oi=100000)
    res = vi.ingest_canonical(c, snapshot_id="s1")
    assert res["warnings"]  # soft warning emitted
    assert res["observations"] == 3  # still ingested
