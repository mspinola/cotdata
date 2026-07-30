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


def test_announcement_parse_is_best_effort():
    from cotdata.vintage_schedule import _parse_announcements
    html = "<ul><li>January 5, 2026: revised gold report</li><li></li></ul>"
    rows = _parse_announcements(html, url="http://x", scraped_at=pd.Timestamp.now("UTC"))
    assert len(rows) == 1  # empty <li> skipped
    assert rows[0]["raw_text"].startswith("January 5, 2026")
    assert pd.Timestamp(rows[0]["announcement_date"]).date() == dt.date(2026, 1, 5)
