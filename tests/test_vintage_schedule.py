"""Release-date resolution + backfill (§4.6, §8): precedence, derivation, and the
Oct–Dec 2025 backlog week resolving to its true announced release date.
"""
import datetime as dt

import pandas as pd
import pytest


@pytest.fixture()
def store_env(tmp_path, monkeypatch):
    monkeypatch.setenv("COTDATA_STORE", str(tmp_path))
    return tmp_path


def test_release_date_precedence():
    from cotdata.vintage_schedule import resolve_release_date
    rd = "2026-07-21"
    obs, ann, sched = "2026-07-24", "2026-07-25", "2026-07-26"
    assert resolve_release_date(rd, observed=obs, announced=ann, scheduled=sched) \
        == (dt.date(2026, 7, 24), "observed")
    assert resolve_release_date(rd, announced=ann, scheduled=sched) \
        == (dt.date(2026, 7, 25), "announced")
    assert resolve_release_date(rd, scheduled=sched) == (dt.date(2026, 7, 26), "scheduled")
    # nothing resolved → derived (report_date + 3d, weekend-adjusted)
    val, src = resolve_release_date(rd)
    assert src == "derived" and val == dt.date(2026, 7, 24)


def test_derive_pushes_off_weekend():
    from cotdata.vintage_schedule import derive_release_date
    # 2026-07-16 (Thu) + 3 = Sun 07-19 → pushed to Mon 07-20
    assert derive_release_date("2026-07-16") == dt.date(2026, 7, 20)


def _ingest_one(report_date, observed_at):
    from cotdata import vintage_ingest as vi
    idx = pd.to_datetime([report_date])
    idx.name = "Report_Date_as_MM_DD_YYYY"
    wide = pd.DataFrame({
        "Market_and_Exchange_Names": ["GOLD"], "CFTC_Contract_Market_Code": ["088691"],
        "Open_Interest_All": [500000],
        "Comm_Positions_Long_All": [200000], "Comm_Positions_Short_All": [250000],
        "NonComm_Positions_Long_All": [150000], "NonComm_Positions_Short_All": [90000],
        "NonRept_Positions_Long_All": [40000], "NonRept_Positions_Short_All": [30000],
        "Traders_Comm_Long_All": [50], "Traders_Comm_Short_All": [55],
        "Traders_NonComm_Long_All": [60], "Traders_NonComm_Short_All": [45],
    }, index=idx)
    vi.ingest_canonical(vi.canonicalize_legacy(wide), snapshot_id="s1", observed_at=observed_at)


def test_backlog_week_resolves_to_announced(store_env):
    """A report_date in the Oct–Nov 2025 appropriations-lapse backlog resolves to its
    true (late) announced release date, flagged `announced` — not `derived`."""
    from cotdata import vintage_schedule as vs
    # bulk-ingested long after the fact, so observed_at is NOT a release date
    _ingest_one("2025-10-07", observed_at=dt.datetime(2026, 7, 30, tzinfo=dt.timezone.utc))

    schedule = pd.DataFrame([{
        "report_date": pd.Timestamp("2025-10-07"),
        "release_date": pd.Timestamp("2025-11-14"),  # republished weeks late
        "source": "announced", "note": "appropriations-lapse backlog",
        "ingested_at": pd.Timestamp.now("UTC"),
    }])
    counts = vs.backfill(schedule=schedule)
    assert counts["announced"] == 3 and counts["derived"] == 0  # 3 category rows

    from cotdata import vintage_ingest as vi
    row = vi.read_observations().iloc[0]
    assert pd.Timestamp(row["release_date"]).date() == dt.date(2025, 11, 14)
    assert row["release_date_source"] == "announced"


def test_backfill_derives_when_no_schedule(store_env):
    from cotdata import vintage_schedule as vs
    _ingest_one("2025-10-07", observed_at=dt.datetime(2026, 7, 30, tzinfo=dt.timezone.utc))
    counts = vs.backfill(schedule=pd.DataFrame(
        columns=["report_date", "release_date", "source", "note", "ingested_at"]))
    assert counts["derived"] == 3 and counts["announced"] == 0


def test_backfill_is_idempotent_and_does_not_downgrade_observed(store_env):
    """Re-running backfill must not downgrade an `observed` release date to `scheduled`.
    Precedence is enforced on every write (full re-resolve), so a live-captured row whose
    observed_at is near its report_date stays `observed` even when a schedule entry exists."""
    from cotdata import vintage_ingest as vi
    from cotdata import vintage_schedule as vs
    # captured live: observed_at 3 days after report_date (within the 4-day window)
    _ingest_one("2026-07-21", observed_at=dt.datetime(2026, 7, 24, 16, tzinfo=dt.timezone.utc))
    schedule = pd.DataFrame([{
        "report_date": pd.Timestamp("2026-07-21"),
        "release_date": pd.Timestamp("2026-07-25"),  # a competing `scheduled` date
        "source": "scheduled", "note": "", "ingested_at": pd.Timestamp.now("UTC"),
    }])

    c1 = vs.backfill(schedule=schedule)
    obs1 = vi.read_observations()
    src1 = set(obs1["release_date_source"])
    rel1 = pd.Timestamp(obs1.iloc[0]["release_date"]).date()

    c2 = vs.backfill(schedule=schedule)  # re-run
    obs2 = vi.read_observations()

    assert c1 == c2  # idempotent counts
    assert src1 == {"observed"}                      # observed wins over scheduled
    assert rel1 == dt.date(2026, 7, 24)              # = observed_at, not the scheduled date
    assert set(obs2["release_date_source"]) == {"observed"}  # not downgraded on re-run
    assert list(obs1["release_date"]) == list(obs2["release_date"])


def test_observed_uses_first_sighting_not_the_revised_row(store_env):
    """A later revision must not drag the `observed` release date forward. The release
    date is the FIRST time the report was seen, not when a given vintage was written."""
    from cotdata import vintage_ingest as vi
    from cotdata import vintage_schedule as vs
    # first seen 3 days after report_date (a live capture)…
    _ingest_one("2026-07-21", observed_at=dt.datetime(2026, 7, 24, 16, tzinfo=dt.timezone.utc))
    # …then revised months later
    idx = pd.to_datetime(["2026-07-21"])
    idx.name = "Report_Date_as_MM_DD_YYYY"
    revised = pd.DataFrame({
        "Market_and_Exchange_Names": ["GOLD"], "CFTC_Contract_Market_Code": ["088691"],
        "Open_Interest_All": [500000],
        "Comm_Positions_Long_All": [200000], "Comm_Positions_Short_All": [999999],
        "NonComm_Positions_Long_All": [150000], "NonComm_Positions_Short_All": [90000],
        "NonRept_Positions_Long_All": [40000], "NonRept_Positions_Short_All": [30000],
        "Traders_Comm_Long_All": [50], "Traders_Comm_Short_All": [55],
        "Traders_NonComm_Long_All": [60], "Traders_NonComm_Short_All": [45],
    }, index=idx)
    vi.ingest_canonical(vi.canonicalize_legacy(revised), snapshot_id="s2",
                        observed_at=dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc))

    counts = vs.backfill(schedule=pd.DataFrame(
        columns=["report_date", "release_date", "source", "note", "ingested_at"]))
    obs = vi.read_observations()
    # every row (including the revised vintage) carries the FIRST-sighting release date
    assert set(obs["release_date_source"]) == {"observed"}
    assert {pd.Timestamp(d).date() for d in obs["release_date"]} == {dt.date(2026, 7, 24)}
    assert counts["observed"] == len(obs) and counts["derived"] == 0


def test_observed_window_rejects_negative_offset(store_env):
    """observed_at before report_date is impossible (clock skew / tz bug) and must NOT
    be absorbed as a valid `observed` release date."""
    from cotdata import vintage_schedule as vs
    # "observed" two days BEFORE the report's as-of date
    _ingest_one("2026-07-21", observed_at=dt.datetime(2026, 7, 19, tzinfo=dt.timezone.utc))
    counts = vs.backfill(schedule=pd.DataFrame(
        columns=["report_date", "release_date", "source", "note", "ingested_at"]))
    assert counts["observed"] == 0 and counts["derived"] == 3  # fell through to derived


def test_published_outranks_observed():
    from cotdata.vintage_schedule import resolve_release_date
    val, src = resolve_release_date("2026-07-21", published="2026-07-24",
                                    observed="2026-07-25", scheduled="2026-07-26")
    assert src == "published" and val == dt.date(2026, 7, 24)


def test_announcement_parse_is_best_effort():
    from cotdata.vintage_schedule import _parse_announcements
    html = "<ul><li>January 5, 2026: revised gold report</li><li></li></ul>"
    rows = _parse_announcements(html, url="http://x", scraped_at=pd.Timestamp.now("UTC"))
    assert len(rows) == 1  # empty <li> skipped
    assert rows[0]["raw_text"].startswith("January 5, 2026")
    assert pd.Timestamp(rows[0]["announcement_date"]).date() == dt.date(2026, 1, 5)
