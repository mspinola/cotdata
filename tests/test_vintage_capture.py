"""Vintage raw-snapshot capture (commit 1): immutable landing zone + provenance.

Fully offline — the HTTP layer is injected. Covers acceptance §9.1 (every fetch
recorded, raw bytes retained) and the dedupe rules of §3.4 / §4.1.
"""
import datetime as dt

import pytest


@pytest.fixture()
def store_env(tmp_path, monkeypatch):
    monkeypatch.setenv("COTDATA_STORE", str(tmp_path))
    return tmp_path


class _FakeHttp:
    """Deterministic HTTP: maps url -> queue of (status, content, etag, last_modified)."""

    def __init__(self, responses):
        self._responses = {u: list(v) for u, v in responses.items()}
        self.calls = []

    def __call__(self, url, *, etag=None, last_modified=None):
        from cotdata.vintage import HttpResult
        self.calls.append((url, etag, last_modified))
        status, content, et, lm = self._responses[url].pop(0)
        if status == 304:
            return HttpResult(304, None)
        return HttpResult(status, content, etag=et, last_modified=lm)


def _clock(start="2026-07-30T16:00:00+00:00", step=dt.timedelta(seconds=1)):
    """A now_fn advancing one ``step`` per call, i.e. once per source per fetch.

    The default second-sized step keeps a multi-fetch test inside one notional run. Pass
    ``step=dt.timedelta(days=1)`` to model the DAILY scheduled task, which is the cadence
    the frozen-year tripwire has to be correct against: CFTC regenerates weekly, so six of
    every seven daily runs legitimately see no new bytes.
    """
    t = [dt.datetime.fromisoformat(start)]

    def now():
        t[0] += step
        return t[0]
    return now


def _body(marker: bytes) -> bytes:
    """A payload above the minimum-size floor. Real annual zips are megabytes; the floor
    exists to refuse truncated/empty responses, so fixtures must look plausibly sized."""
    return marker + b"\x00" * 2048


def _only(url, *tuples):
    return {url: list(tuples)}


LEGACY_2026 = "https://www.cftc.gov/files/dea/history/dea_fut_xls_2026.zip"


def _legacy_only():
    """A single-source capture list, so tests exercise one URL deterministically."""
    from cotdata.vintage import Source
    return [Source("legacy", "annual_zip", "zip", LEGACY_2026, 2026)]


def _fetch(vintage, http, now):
    return vintage.fetch(sources=_legacy_only(), http_get=http, rate_limit_s=0, now_fn=now)


def test_fetch_retains_raw_bytes_and_records_provenance(store_env):
    from cotdata import vintage
    http = _FakeHttp(_only(LEGACY_2026, (200, _body(b"ZIPBYTES-week1"), '"etag1"', "Fri, 24 Jul 2026 19:27:59 GMT")))

    res = _fetch(vintage, http, _clock())

    assert res["new_files"] == 1 and res["checks"] == 1
    rec = res["records"][0]
    # raw bytes retained on disk, immutably, under the year partition
    raw = store_env / rec["local_path"]
    assert raw.exists() and raw.read_bytes() == _body(b"ZIPBYTES-week1")
    assert raw.parent == store_env / "vintage" / "raw" / "annual_zip" / "2026"
    # provenance recorded: sha, size, etag, last-modified, status
    import hashlib
    assert rec["content_sha256"] == hashlib.sha256(_body(b"ZIPBYTES-week1")).hexdigest()
    assert rec["byte_size"] == len(_body(b"ZIPBYTES-week1"))
    assert rec["http_etag"] == '"etag1"'
    assert rec["http_last_modified"] == "Fri, 24 Jul 2026 19:27:59 GMT"
    assert rec["parse_status"] == "pending"
    # and persisted in the vintage manifest
    assert len(vintage.read_snapshots()) == 1


def test_304_records_check_without_new_file(store_env):
    from cotdata import vintage
    http = _FakeHttp(_only(
        LEGACY_2026,
        (200, _body(b"ZIPBYTES-week1"), '"etag1"', "Fri, 24 Jul 2026 19:27:59 GMT"),
        (304, None, None, None),
    ))
    now = _clock()
    _fetch(vintage, http, now)
    res2 = _fetch(vintage, http, now)

    assert res2["new_files"] == 0
    rec = res2["records"][0]
    assert rec["http_status"] == 304 and rec["note"] == "304 not-modified"
    # conditional GET actually sent the prior validators
    assert http.calls[-1] == (LEGACY_2026, '"etag1"', "Fri, 24 Jul 2026 19:27:59 GMT")
    # exactly one raw file on disk; the check is recorded but retains nothing new
    raws = list((store_env / "vintage" / "raw").rglob("*.zip"))
    assert len(raws) == 1
    assert len(vintage.read_snapshots()) == 2


def test_byte_identical_regeneration_is_deduped(store_env):
    """A regenerated zip with identical bytes (200, no 304) must not write a second
    raw file — a changed download is not itself a revision (§3.4)."""
    from cotdata import vintage
    http = _FakeHttp(_only(
        LEGACY_2026,
        (200, _body(b"SAME"), '"e1"', "lm1"),
        (200, _body(b"SAME"), '"e2"', "lm2"),  # server didn't honour conditional GET, but bytes match
    ))
    now = _clock()
    _fetch(vintage, http, now)
    res2 = _fetch(vintage, http, now)

    assert res2["new_files"] == 0
    assert res2["records"][0]["note"] == "unchanged bytes (deduped)"
    assert len(list((store_env / "vintage" / "raw").rglob("*.zip"))) == 1


def test_corrupt_manifest_raises_and_quarantines_rather_than_overwriting(store_env):
    """A corrupt manifest must never be silently replaced by an empty one: it is the only
    map from snapshot_id to url/sha/parse-status, and the raw bytes outlive it."""
    from cotdata import vintage
    http = _FakeHttp(_only(LEGACY_2026, (200, _body(b"ZIPBYTES-week1") * 100, '"e1"', "lm1")))
    _fetch(vintage, http, _clock())
    mpath = vintage.manifest_path()
    mpath.write_text("{ this is not json")

    with pytest.raises(vintage.CorruptManifestError, match="unreadable"):
        vintage.read_snapshots()
    # original moved aside, not destroyed; no empty manifest left in its place
    quarantined = list(mpath.parent.glob("snapshots.json.corrupt.*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text() == "{ this is not json"
    assert not mpath.exists()
    # and the retained raw bytes are untouched
    assert len(list((store_env / "vintage" / "raw").rglob("*.zip"))) == 1


def test_raw_write_is_atomic_no_part_files_left(store_env):
    from cotdata import vintage
    http = _FakeHttp(_only(LEGACY_2026, (200, b"Z" * 5000, '"e1"', "lm1")))
    _fetch(vintage, http, _clock())
    assert not list((store_env / "vintage" / "raw").rglob("*.part"))
    raw = list((store_env / "vintage" / "raw").rglob("*.zip"))[0]
    assert raw.read_bytes() == b"Z" * 5000


def test_304_snapshot_ids_are_unique_within_one_second(store_env):
    """Several sources returning 304 in the same second must not share a snapshot_id —
    update_snapshot patches every record with a matching id."""
    from cotdata import vintage
    u1 = "https://www.cftc.gov/files/dea/history/dea_fut_xls_2026.zip"
    u2 = "https://www.cftc.gov/files/dea/history/fut_disagg_txt_2026.zip"
    srcs = [vintage.Source("legacy", "annual_zip", "zip", u1, 2026),
            vintage.Source("disaggregated", "annual_zip", "zip", u2, 2026)]
    http = _FakeHttp({u1: [(304, None, None, None)], u2: [(304, None, None, None)]})
    # a frozen clock: every source is stamped the same whole second
    res = vintage.fetch(sources=srcs, http_get=http, rate_limit_s=0,
                        now_fn=lambda: dt.datetime(2026, 7, 30, 16, 0, tzinfo=dt.timezone.utc))
    ids = [r["snapshot_id"] for r in res["records"]]
    assert len(set(ids)) == 2, f"snapshot_id collision: {ids}"


def test_one_failing_source_does_not_kill_the_run(store_env):
    """A 404 mid-run must be recorded and skipped, not abort the remaining sources."""
    from cotdata import vintage
    u1 = "https://www.cftc.gov/files/dea/history/dea_fut_xls_1986.zip"
    u2 = "https://www.cftc.gov/files/dea/history/dea_fut_xls_2026.zip"

    class _Http(_FakeHttp):
        def __call__(self, url, *, etag=None, last_modified=None):
            if url == u1:
                raise RuntimeError("404 Not Found")
            return super().__call__(url, etag=etag, last_modified=last_modified)

    srcs = [vintage.Source("legacy", "annual_zip", "zip", u1, 1986),
            vintage.Source("legacy", "annual_zip", "zip", u2, 2026)]
    http = _Http({u2: [(200, b"GOOD" * 500, '"e"', "lm")]})
    res = vintage.fetch(sources=srcs, http_get=http, rate_limit_s=0, now_fn=_clock())

    assert res["failed"] == 1 and res["new_files"] == 1 and res["checks"] == 2
    notes = [r.get("note") or "" for r in res["records"]]
    assert any("fetch failed" in n for n in notes)
    # the failure is persisted, so it is visible and retryable
    assert any("fetch failed" in (s.get("note") or "") for s in vintage.read_snapshots())


def test_truncated_response_is_refused(store_env):
    """An implausibly small 200 body is not retained as a legitimate snapshot."""
    from cotdata import vintage
    http = _FakeHttp(_only(LEGACY_2026, (200, b"", '"e"', "lm")))
    res = vintage.fetch(sources=_legacy_only(), http_get=http, rate_limit_s=0, now_fn=_clock())
    assert res["failed"] == 1 and res["new_files"] == 0
    assert not list((store_env / "vintage" / "raw").rglob("*.zip"))


def test_manifest_persisted_after_each_source(store_env):
    """The manifest is written per source, so an interrupted run keeps what it captured."""
    from cotdata import vintage
    u1, u2 = LEGACY_2026, "https://www.cftc.gov/files/dea/history/fut_disagg_txt_2026.zip"
    seen = []

    def http(url, *, etag=None, last_modified=None):
        from cotdata.vintage import HttpResult
        if url == u2:  # simulate a crash after the first source was captured
            assert len(vintage.read_snapshots()) == 1, "first source not yet persisted!"
            seen.append(url)
            raise KeyboardInterrupt("simulated crash")
        return HttpResult(200, b"A" * 5000, etag='"e"', last_modified="lm")

    srcs = [vintage.Source("legacy", "annual_zip", "zip", u1, 2026),
            vintage.Source("disaggregated", "annual_zip", "zip", u2, 2026)]
    with pytest.raises(KeyboardInterrupt):
        vintage.fetch(sources=srcs, http_get=http, rate_limit_s=0, now_fn=_clock())
    assert seen == [u2]
    assert len(vintage.read_snapshots()) == 1  # survived the crash


def test_weekly_static_partitions_by_capture_year(store_env):
    from cotdata import vintage
    http = _FakeHttp({vintage.WEEKLY_STATIC.url: [(200, b"T" * 5000, '"e"', "lm")]})
    vintage.fetch(sources=[vintage.WEEKLY_STATIC], http_get=http, rate_limit_s=0,
                  now_fn=_clock("2026-07-30T16:00:00+00:00"))
    raws = list((store_env / "vintage" / "raw" / "weekly_static").rglob("*.txt"))
    assert len(raws) == 1 and raws[0].parent.name == "2026"  # not "current"


def test_closed_year_sha_change_is_flagged_as_restatement_suspect(store_env):
    """A closed year is frozen, so a content change is the retroactive-restatement
    signature. It must be flagged, not silently retained as an ordinary new vintage."""
    from cotdata import vintage
    url = "https://www.cftc.gov/files/dea/history/dea_fut_xls_2025.zip"
    srcs = [vintage.Source("legacy", "annual_zip", "zip", url, 2025)]  # closed year
    http = _FakeHttp({url: [(200, _body(b"jan-final"), '"e1"', "lm1"),
                            (200, _body(b"RESTATED"), '"e2"', "lm2")]})
    now = _clock()  # clock is in 2026, so 2025 is closed
    r1 = vintage.fetch(sources=srcs, http_get=http, rate_limit_s=0, now_fn=now)
    r2 = vintage.fetch(sources=srcs, http_get=http, rate_limit_s=0, now_fn=now)

    assert r1["records"][0]["restatement_suspect"] is False  # first sighting, no prior
    assert r2["records"][0]["restatement_suspect"] is True   # frozen year changed


def test_current_year_change_is_not_a_restatement_suspect(store_env):
    """The current year legitimately gains a report every week."""
    from cotdata import vintage
    http = _FakeHttp(_only(LEGACY_2026,
                           (200, _body(b"week1"), '"e1"', "lm1"),
                           (200, _body(b"week2"), '"e2"', "lm2")))
    now = _clock()  # 2026, and the source's report_year is 2026
    _fetch(vintage, http, now)
    res2 = _fetch(vintage, http, now)
    assert res2["records"][0]["restatement_suspect"] is False


def test_vintage_root_override_keeps_tree_outside_a_mirrored_store(store_env, tmp_path, monkeypatch):
    """COTDATA_VINTAGE_ROOT must relocate the whole tree — the escape hatch for a replica
    whose store is mirrored with robocopy /MIR (which would delete a store-local tree)."""
    from cotdata import vintage
    outside = tmp_path / "vintage_elsewhere"
    monkeypatch.setenv("COTDATA_VINTAGE_ROOT", str(outside))
    http = _FakeHttp(_only(LEGACY_2026, (200, _body(b"week1"), '"e1"', "lm1")))
    res = _fetch(vintage, http, _clock())

    assert res["new_files"] == 1
    assert (outside / "snapshots.json").exists()
    assert list(outside.rglob("*.zip"))
    assert not (store_env / "vintage").exists()  # nothing written into the synced store


def test_changed_bytes_writes_second_immutable_snapshot(store_env):
    from cotdata import vintage
    http = _FakeHttp(_only(
        LEGACY_2026,
        (200, _body(b"week1"), '"e1"', "lm1"),
        (200, _body(b"week2-revised"), '"e2"', "lm2"),
    ))
    now = _clock()
    _fetch(vintage, http, now)
    _fetch(vintage, http, now)

    raws = sorted((store_env / "vintage" / "raw").rglob("*.zip"))
    assert len(raws) == 2  # both vintages retained; neither overwritten
    assert {p.read_bytes() for p in raws} == {_body(b"week1"), _body(b"week2-revised")}


# ── Frozen-year tripwire ────────────────────────────────────────────────────
# CFTC regenerates a ROLLING TWO-YEAR window (current + prior). The prior year is
# therefore re-served every week but byte-identical, which is the one place we get a free
# weekly CONTENT check on closed data, and the only automated detector for a 2008-style
# retroactive restatement. These tests pin the three regimes and the edge-trigger rule.

LEGACY_2025 = "https://www.cftc.gov/files/dea/history/dea_fut_xls_2025.zip"
LEGACY_2019 = "https://www.cftc.gov/files/dea/history/dea_fut_xls_2019.zip"


def _one(url, year):
    from cotdata.vintage import Source
    return [Source("legacy", "annual_zip", "zip", url, year)]


def test_expectation_follows_the_rolling_two_year_regeneration_window():
    from cotdata import vintage
    assert vintage.capture_expectation(2026, 2026) == vintage.EXPECT_CHURN
    assert vintage.capture_expectation(2025, 2026) == vintage.EXPECT_FROZEN_IN_WINDOW
    assert vintage.capture_expectation(2024, 2026) == vintage.EXPECT_FROZEN_OUT_OF_WINDOW
    assert vintage.capture_expectation(None, 2026) == vintage.EXPECT_WEEKLY


def test_prior_year_dedupe_is_the_expected_outcome_and_never_alerts(store_env):
    """The nominal weekly result for the frozen prior year: CFTC re-touches Last-Modified,
    re-serves identical bytes, we dedupe. Silence here is the tripwire working."""
    from cotdata import vintage
    body = _body(b"2025-final")
    http = _FakeHttp({LEGACY_2025: [(200, body, None, "lm1"), (200, body, None, "lm2")]})
    now = _clock()
    vintage.fetch(sources=_one(LEGACY_2025, 2025), http_get=http, rate_limit_s=0, now_fn=now)
    r2 = vintage.fetch(sources=_one(LEGACY_2025, 2025), http_get=http, rate_limit_s=0, now_fn=now)

    rec = r2["records"][0]
    assert rec["expectation"] == vintage.EXPECT_FROZEN_IN_WINDOW
    assert rec["outcome"] == vintage.OUTCOME_DEDUPED
    assert rec["tripwire_alert"] is None
    assert r2["tripwire_alerts"] == []


def test_prior_year_content_change_alerts(store_env):
    """Anything other than a dedupe on the frozen prior year is the alert the user asked
    for. A changed sha there is the restatement signature."""
    from cotdata import vintage
    http = _FakeHttp({LEGACY_2025: [(200, _body(b"2025-final"), None, "lm1"),
                                    (200, _body(b"2025-RESTATED"), None, "lm2")]})
    now = _clock()
    vintage.fetch(sources=_one(LEGACY_2025, 2025), http_get=http, rate_limit_s=0, now_fn=now)
    r2 = vintage.fetch(sources=_one(LEGACY_2025, 2025), http_get=http, rate_limit_s=0, now_fn=now)

    rec = r2["records"][0]
    assert rec["outcome"] == vintage.OUTCOME_CHANGED
    assert rec["tripwire_alert"] and "restatement" in rec["tripwire_alert"]
    assert len(r2["tripwire_alerts"]) == 1


def test_the_ordinary_weekly_304_gap_is_not_blindness(store_env):
    """REGRESSION, found in production on 2026-08-01. CFTC regenerates the prior year
    WEEKLY; the capture task runs DAILY. So the healthy pattern is one 200 followed by six
    304s, and an edge-trigger keyed on 'did the last run see bytes?' fires on the first
    304 after every re-serve. It did: three alerts every Saturday, on all three prior-year
    sources, saying the detector had gone blind while it was working perfectly.

    Three full weeks of the healthy pattern must be completely silent."""
    from cotdata import vintage
    body = _body(b"2025-final")
    queue = []
    for day in range(21):
        queue.append((200, body, None, f"lm{day // 7}") if day % 7 == 0
                     else (304, None, None, None))
    http = _FakeHttp({LEGACY_2025: queue})
    now = _clock(step=dt.timedelta(days=1))
    src = _one(LEGACY_2025, 2025)

    alerts = []
    for _ in range(21):
        res = vintage.fetch(sources=src, http_get=http, rate_limit_s=0, now_fn=now)
        alerts += res["tripwire_alerts"]

    assert alerts == []


def test_prior_year_going_quiet_alerts_once_not_forever(store_env):
    """If CFTC stops re-serving the prior year the content check really has gone BLIND.
    Worth knowing, but it is a standing condition: a level-triggered alert would fire every
    day forever, which is how an alert gets ignored.

    So it fires once per quiet period, when that period outlasts a full weekly cycle plus
    slack. Fifteen daily runs after the last delivery is one alert, not fourteen and not
    two — and the one it fires is the day the silence passes BLIND_AFTER_DAYS."""
    from cotdata import vintage
    body = _body(b"2025-final")
    http = _FakeHttp({LEGACY_2025: [(200, body, None, "lm1")]
                                   + [(304, None, None, None)] * 15})
    now = _clock(step=dt.timedelta(days=1))
    src = _one(LEGACY_2025, 2025)

    fired = []
    for day in range(16):
        res = vintage.fetch(sources=src, http_get=http, rate_limit_s=0, now_fn=now)
        if res["records"][0]["tripwire_alert"]:
            fired.append((day, res["records"][0]))

    assert len(fired) == 1                                   # edge, not level
    day, rec = fired[0]
    assert day == vintage.BLIND_AFTER_DAYS + 1               # first run past the threshold
    assert rec["outcome"] == vintage.OUTCOME_NOT_MODIFIED
    assert "gone blind" in rec["tripwire_alert"]


def test_the_blind_alert_re_arms_for_a_second_quiet_period(store_env):
    """One alert per quiet period, so a year that goes quiet, comes back, then goes quiet
    again is two events. The edge is the period, not the previous record's outcome."""
    from cotdata import vintage
    body = _body(b"2025-final")
    quiet = [(304, None, None, None)] * 12
    http = _FakeHttp({LEGACY_2025: [(200, body, None, "lm1")] + quiet
                                   + [(200, body, None, "lm2")] + quiet})
    now = _clock(step=dt.timedelta(days=1))
    src = _one(LEGACY_2025, 2025)

    alerts = []
    for _ in range(26):
        res = vintage.fetch(sources=src, http_get=http, rate_limit_s=0, now_fn=now)
        alerts += res["tripwire_alerts"]

    assert len(alerts) == 2


def test_older_frozen_year_304_is_expected_and_silent(store_env):
    """Outside the regeneration window a 304 is correct. Alerting on it would bury the
    prior-year signal under ~35 years of noise on every --all sweep."""
    from cotdata import vintage
    http = _FakeHttp({LEGACY_2019: [(200, _body(b"2019"), None, "lm1"),
                                    (304, None, None, None)]})
    now = _clock()
    src = _one(LEGACY_2019, 2019)
    vintage.fetch(sources=src, http_get=http, rate_limit_s=0, now_fn=now)
    r2 = vintage.fetch(sources=src, http_get=http, rate_limit_s=0, now_fn=now)

    assert r2["records"][0]["expectation"] == vintage.EXPECT_FROZEN_OUT_OF_WINDOW
    assert r2["records"][0]["tripwire_alert"] is None


def test_current_year_new_bytes_never_alert(store_env):
    """New data every week is the whole point of the current year."""
    from cotdata import vintage
    http = _FakeHttp(_only(LEGACY_2026, (200, _body(b"w1"), None, "lm1"),
                           (200, _body(b"w2"), None, "lm2")))
    now = _clock()
    _fetch(vintage, http, now)
    r2 = _fetch(vintage, http, now)
    assert r2["records"][0]["expectation"] == vintage.EXPECT_CHURN
    assert r2["tripwire_alerts"] == []


def test_default_fetch_includes_the_prior_year_so_the_tripwire_actually_runs(store_env):
    """The tripwire is worth nothing if it only fires when someone remembers to run
    --all by hand, so the prior year is in the DEFAULT scheduled set."""
    from cotdata import vintage
    got = {(s.report_year, s.report_type) for s in _default_sources(vintage, 2026)}
    assert (2026, "legacy") in got
    assert (2025, "legacy") in got
    assert (2024, "legacy") not in got
    assert (None, "legacy") in got  # the weekly static


def test_explicit_year_is_taken_at_face_value(store_env):
    """A targeted capture is not the scheduled sweep: --year 2019 means 2019."""
    from cotdata import vintage
    years = {s.report_year for s in _default_sources(vintage, 2026, year=2019)}
    assert years == {2019, None}


def _default_sources(vintage, this_year, **kw):
    """Capture the source list `fetch` derives, without any network at all."""
    seen = []

    def spy(url, *, etag=None, last_modified=None):
        raise AssertionError("no request expected")

    import datetime as dt

    def now():
        return dt.datetime(this_year, 7, 30, tzinfo=dt.timezone.utc)

    # fetch() records a failure rather than raising, so the spy's AssertionError still
    # lets every derived source land in the returned records.
    res = vintage.fetch(http_get=spy, rate_limit_s=0, now_fn=now, **kw)
    for rec in res["records"]:
        seen.append(vintage.Source(rec["report_type"], rec["source_kind"], "zip",
                                   rec["source_url"], rec["report_year"]))
    return seen


def test_a_fetch_failure_does_not_defeat_dedupe_or_fire_a_false_tripwire(store_env):
    """Found by adversarial review, reproduced before fixing.

    A failure record carries no sha, no etag and no Last-Modified. Comparing the next
    fetch against it means no If-Modified-Since is sent, the dedupe test cannot match, and
    byte-identical content is classified as CHANGED. On a frozen year that turns one
    network blip into a false restatement alert, which is the single alarm this whole
    subsystem exists to raise, and re-retains megabytes already on disk."""
    from cotdata import vintage
    body = _body(b"2025-frozen")

    class _Flaky:
        def __init__(self):
            self.n = 0

        def __call__(self, url, *, etag=None, last_modified=None):
            from cotdata.vintage import HttpResult
            self.n += 1
            if self.n == 3:
                raise ConnectionError("network down")
            if self.n == 2:
                return HttpResult(304, None)
            return HttpResult(200, body, etag=None, last_modified="lm1")

    http, now, src = _Flaky(), _clock(), _one(LEGACY_2025, 2025)
    for _ in range(3):
        vintage.fetch(sources=src, http_get=http, rate_limit_s=0, now_fn=now)
    recovered = vintage.fetch(sources=src, http_get=http, rate_limit_s=0, now_fn=now)

    rec = recovered["records"][0]
    assert rec["outcome"] == vintage.OUTCOME_DEDUPED       # not "changed"
    assert rec["tripwire_alert"] is None                   # no false restatement alert
    assert not rec.get("restatement_suspect")  # absent on a dedupe, which is falsy
    assert recovered["new_files"] == 0                     # nothing re-retained
    assert len(list((store_env / "vintage" / "raw").rglob("*.zip"))) == 1


def test_a_304_after_a_failure_still_resolves_its_prior_file(store_env):
    """The 304 branch reuses the previous snapshot's sha and local_path. Taken from a
    failure record those are both None, leaving a snapshot that points nowhere."""
    from cotdata import vintage
    from cotdata.vintage import HttpResult
    body = _body(b"frozen")
    seq = [lambda: HttpResult(200, body, etag=None, last_modified="lm1"),
           lambda: (_ for _ in ()).throw(ConnectionError("down")),
           lambda: HttpResult(304, None)]
    n = [0]

    def http(url, *, etag=None, last_modified=None):
        n[0] += 1
        return seq[n[0] - 1]()

    now, src = _clock(), _one(LEGACY_2025, 2025)
    for _ in range(3):
        res = vintage.fetch(sources=src, http_get=http, rate_limit_s=0, now_fn=now)
    rec = res["records"][0]
    assert rec["content_sha256"] is not None
    assert rec["local_path"] is not None


def test_a_failure_as_the_FIRST_record_does_not_fire_a_false_tripwire(store_env):
    """Second review pass. The first fix re-pointed restatement_suspect at the
    content-bearing snapshot but left the FIRST/CHANGED classification keyed to `prev`, so
    when the very first record for a URL was a fetch failure the recovering fetch still
    fired the false restatement alert, with outcome and restatement_suspect disagreeing."""
    from cotdata import vintage
    from cotdata.vintage import HttpResult
    body = _body(b"2025-frozen")
    n = [0]

    def http(url, *, etag=None, last_modified=None):
        n[0] += 1
        if n[0] == 1:
            raise ConnectionError("network down on the very first attempt")
        return HttpResult(200, body, etag=None, last_modified="lm1")

    now, src = _clock(), _one(LEGACY_2025, 2025)
    vintage.fetch(sources=src, http_get=http, rate_limit_s=0, now_fn=now)
    res = vintage.fetch(sources=src, http_get=http, rate_limit_s=0, now_fn=now)

    rec = res["records"][0]
    assert rec["outcome"] == vintage.OUTCOME_FIRST     # never seen bytes before, so FIRST
    assert rec["tripwire_alert"] is None
    assert not rec["restatement_suspect"]              # and the two signals agree


def test_the_blind_alert_fires_at_the_year_rollover(store_env):
    """Second review pass. The rollover is the single most likely moment for CFTC's
    regeneration window to shift, so a year that churns all through 2025 and is then
    dropped on the January morning it becomes frozen-in-window is the worst possible blind
    spot. It must still be caught, and it is: the silence simply runs past the threshold.

    Elapsed time covers this without the special case an outcome-keyed edge needed. What it
    also does, correctly, is stay quiet one week in: at that point a January silence is
    indistinguishable from the ordinary gap between two weekly re-serves."""
    import datetime as dt

    from cotdata import vintage
    from cotdata.vintage import HttpResult
    n = [0]

    def http(url, *, etag=None, last_modified=None):
        n[0] += 1
        if n[0] == 1:
            return HttpResult(200, _body(b"dec"), etag=None, last_modified="lm1")
        return HttpResult(304, None)

    src = _one(LEGACY_2025, 2025)
    at = lambda d: (lambda: dt.datetime(2026 if d > 26 else 2025,       # noqa: E731
                                        1 if d > 26 else 12,
                                        d if d <= 26 else d - 26, 17,
                                        tzinfo=dt.timezone.utc))
    r1 = vintage.fetch(sources=src, http_get=http, rate_limit_s=0, now_fn=at(26))  # Dec 26
    assert r1["records"][0]["expectation"] == vintage.EXPECT_CHURN   # 2025 in 2025

    r2 = vintage.fetch(sources=src, http_get=http, rate_limit_s=0, now_fn=at(28))  # Jan 2
    assert r2["records"][0]["expectation"] == vintage.EXPECT_FROZEN_IN_WINDOW  # 2025 in 2026
    assert r2["records"][0]["outcome"] == vintage.OUTCOME_NOT_MODIFIED
    assert r2["records"][0]["tripwire_alert"] is None     # 7 days: still an ordinary gap

    r3 = vintage.fetch(sources=src, http_get=http, rate_limit_s=0, now_fn=at(32))  # Jan 6
    rec = r3["records"][0]
    assert rec["expectation"] == vintage.EXPECT_FROZEN_IN_WINDOW
    assert rec["tripwire_alert"] and "gone blind" in rec["tripwire_alert"]


def test_a_fetch_failure_is_not_a_tripwire_condition(store_env):
    """Connectivity is not provenance. A dropped connection says nothing about whether
    CFTC restated anything, and routing it into the frozen-year alarm turned one blip into
    a restatement alert on the daily run. Failures are counted by fetch instead."""
    from cotdata import vintage
    from cotdata.vintage import HttpResult
    body = _body(b"frozen")
    n = [0]

    def http(url, *, etag=None, last_modified=None):
        n[0] += 1
        if n[0] <= 2:
            return HttpResult(200, body, etag=None, last_modified="lm1")
        raise ConnectionError("blip")

    now, src = _clock(), _one(LEGACY_2025, 2025)
    vintage.fetch(sources=src, http_get=http, rate_limit_s=0, now_fn=now)   # first
    vintage.fetch(sources=src, http_get=http, rate_limit_s=0, now_fn=now)   # deduped
    res = vintage.fetch(sources=src, http_get=http, rate_limit_s=0, now_fn=now)  # fails

    assert res["records"][0]["outcome"] == vintage.OUTCOME_FAILED
    assert res["tripwire_alerts"] == []      # no alarm
    assert res["failed"] == 1                # but it IS counted


def test_disagg_and_tff_start_in_2010_not_2006():
    """cftc.gov serves 404 for fut_disagg_txt_2006..2009 and fut_fin_txt_2006..2009
    (verified live), so 2006 made every `fetch --all` record eight permanent failure
    snapshots that could never succeed."""
    from cotdata import vintage
    assert [s.report_type for s in vintage.annual_sources(2009)] == ["legacy"]
    assert {s.report_type for s in vintage.annual_sources(2010)} == {
        "legacy", "disaggregated", "tff"}
