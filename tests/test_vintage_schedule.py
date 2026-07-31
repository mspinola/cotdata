"""Release-date resolution + backfill (§4.6, §8): precedence, derivation, and the
Oct–Dec 2025 backlog week resolving to its true announced release date.
"""
import datetime as dt
from pathlib import Path

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


def _weekly_static_bytes(report_date="2026-07-21"):
    """A headerless positional CSV shaped like deafut.txt: field[2] is the ISO report
    date, identical on every row (measured: one distinct date per file)."""
    rows = [f'"WHEAT-SRW - CHICAGO BOARD OF TRADE",260721,{report_date},001602,CBT ,00,001 ,455433',
            f'"GOLD - COMMODITY EXCHANGE INC.",260721,{report_date},088691,CMX ,00,001 ,500000']
    return ("\n".join(rows) + "\n").encode()


def test_report_date_read_from_one_field(store_env, tmp_path):
    from cotdata.vintage_schedule import report_date_of_weekly_static
    p = tmp_path / "wk.txt"
    p.write_bytes(_weekly_static_bytes("2026-07-21"))
    assert report_date_of_weekly_static(p) == dt.date(2026, 7, 21)


def test_last_modified_converts_to_et_publication_date():
    from cotdata.vintage_schedule import _last_modified_to_release_date
    # 19:27:59 UTC on a Friday = 15:27 ET the SAME day (the ~15:30 ET release)
    assert _last_modified_to_release_date("Fri, 24 Jul 2026 19:27:59 GMT") == dt.date(2026, 7, 24)
    # a UTC timestamp after midnight belongs to the PREVIOUS ET day
    assert _last_modified_to_release_date("Sat, 25 Jul 2026 02:00:00 GMT") == dt.date(2026, 7, 24)
    assert _last_modified_to_release_date("not a date") is None


def test_published_beats_observed_end_to_end(store_env):
    """A retained weekly static gives a true publication date that outranks the
    poll-derived `observed` bound."""
    from cotdata import vintage
    from cotdata import vintage_ingest as vi
    from cotdata import vintage_schedule as vs

    # capture a weekly static whose Last-Modified is the real publication moment
    lm = "Fri, 24 Jul 2026 19:27:59 GMT"

    def http(url, *, etag=None, last_modified=None):
        from cotdata.vintage import HttpResult
        return HttpResult(200, _weekly_static_bytes("2026-07-21") + b"\x00" * 2048,
                          etag='"e"', last_modified=lm)

    vintage.fetch(sources=[vintage.WEEKLY_STATIC], http_get=http, rate_limit_s=0,
                  now_fn=lambda: dt.datetime(2026, 7, 27, 21, tzinfo=dt.timezone.utc))
    # and an observation captured 3 days after the report date (would resolve `observed`)
    _ingest_one("2026-07-21", observed_at=dt.datetime(2026, 7, 24, 21, tzinfo=dt.timezone.utc))

    derived = vs.published_from_snapshots()
    assert len(derived) == 1
    assert pd.Timestamp(derived.iloc[0]["report_date"]).date() == dt.date(2026, 7, 21)
    assert pd.Timestamp(derived.iloc[0]["release_date"]).date() == dt.date(2026, 7, 24)

    counts = vs.backfill()  # production path folds published in automatically
    obs = vi.read_observations()
    assert set(obs["release_date_source"]) == {"published"}
    assert counts["published"] == len(obs) and counts["observed"] == 0


def test_sync_published_is_idempotent(store_env):
    from cotdata import vintage
    from cotdata import vintage_schedule as vs

    def http(url, *, etag=None, last_modified=None):
        from cotdata.vintage import HttpResult
        return HttpResult(200, _weekly_static_bytes() + b"\x00" * 2048, etag='"e"',
                          last_modified="Fri, 24 Jul 2026 19:27:59 GMT")

    vintage.fetch(sources=[vintage.WEEKLY_STATIC], http_get=http, rate_limit_s=0,
                  now_fn=lambda: dt.datetime(2026, 7, 27, 21, tzinfo=dt.timezone.utc))
    assert vs.sync_published() == {"published": 1}
    vs.sync_published()
    assert len(vs.read_release_schedule()) == 1  # no duplicate row


_SCHED_FIXTURE = Path(__file__).parent / "fixtures" / "cftc_release_schedule_2026.html"


def test_parses_the_real_cftc_release_schedule():
    """Against a trimmed copy of the live page, so the parser is pinned to real markup."""
    from cotdata.vintage_schedule import parse_release_schedule
    df = parse_release_schedule(_SCHED_FIXTURE.read_text())

    assert len(df) == 52                        # one release per week
    assert set(df["source"]) == {"scheduled"}
    # every report date is a Tuesday; every release is a Friday unless holiday-delayed
    assert all(pd.Timestamp(d).weekday() == 1 for d in df["report_date"])
    normal = df[~df["note"].str.contains("holiday")]
    assert all(pd.Timestamp(d).weekday() == 4 for d in normal["release_date"])
    # the six 2026 federal-holiday delays, which is the whole point of seeding this
    delayed = df[df["note"].str.contains("holiday")]
    assert len(delayed) == 6
    assert all(pd.Timestamp(d).weekday() == 0 for d in delayed["release_date"])  # Mondays


def test_release_schedule_agrees_with_the_published_timestamp():
    """Independent cross-check: the calendar and the weekly-static Last-Modified must
    give the same release date for the same report date."""
    from cotdata.vintage_schedule import (
        _last_modified_to_release_date,
        parse_release_schedule,
    )
    df = parse_release_schedule(_SCHED_FIXTURE.read_text())
    row = df[df["report_date"] == pd.Timestamp("2026-07-21")].iloc[0]
    from_header = _last_modified_to_release_date("Fri, 24 Jul 2026 19:27:59 GMT")
    assert pd.Timestamp(row["release_date"]).date() == from_header == dt.date(2026, 7, 24)


def test_report_date_for_release_handles_holiday_shift():
    from cotdata.vintage_schedule import report_date_for_release
    # normal: Friday release reports the Tuesday three days earlier
    assert report_date_for_release(dt.date(2026, 7, 24)) == dt.date(2026, 7, 21)
    # delayed: a Monday release still reports the PREVIOUS week's Tuesday, not a new one
    assert report_date_for_release(dt.date(2026, 7, 6)) == dt.date(2026, 6, 30)
    assert report_date_for_release(dt.date(2026, 6, 22)) == dt.date(2026, 6, 16)


def test_parse_raises_rather_than_returning_empty_on_layout_change():
    """A silent empty parse is indistinguishable from 'CFTC published nothing', and would
    quietly leave every row on `derived`."""
    from cotdata.vintage_schedule import parse_release_schedule
    with pytest.raises(ValueError, match="Release Schedule"):
        parse_release_schedule("<html><body>no heading here</body></html>")
    with pytest.raises(ValueError, match="no <table>"):
        parse_release_schedule("<h3>2026 Release Schedule</h3><p>nothing</p>")


def test_scheduled_upgrades_derived_rows(store_env):
    """End to end: a report date with no other evidence goes from `derived` to
    `scheduled`, and a holiday week gets its date CORRECTED, not just relabelled."""
    from cotdata import vintage_ingest as vi
    from cotdata import vintage_schedule as vs

    # report 2026-06-30: derived would say Friday 2026-07-03; the calendar says Monday
    # 2026-07-06, because Independence Day delayed it.
    _ingest_one("2026-06-30", observed_at=dt.datetime(2026, 7, 30, tzinfo=dt.timezone.utc))
    assert vs.derive_release_date("2026-06-30") == dt.date(2026, 7, 3)   # the wrong guess

    schedule = vs.parse_release_schedule(_SCHED_FIXTURE.read_text())
    counts = vs.backfill(schedule=schedule)

    obs = vi.read_observations()
    assert counts["scheduled"] == len(obs) and counts["derived"] == 0
    assert set(obs["release_date_source"]) == {"scheduled"}
    assert pd.Timestamp(obs.iloc[0]["release_date"]).date() == dt.date(2026, 7, 6)


def test_announcement_parse_is_best_effort():
    from cotdata.vintage_schedule import _parse_announcements
    html = "<ul><li>January 5, 2026: revised gold report</li><li></li></ul>"
    rows = _parse_announcements(html, url="http://x", scraped_at=pd.Timestamp.now("UTC"))
    assert len(rows) == 1  # empty <li> skipped
    assert rows[0]["raw_text"].startswith("January 5, 2026")
    assert pd.Timestamp(rows[0]["announcement_date"]).date() == dt.date(2026, 1, 5)
