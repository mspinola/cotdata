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


def _clock(start="2026-07-30T16:00:00+00:00"):
    t = [dt.datetime.fromisoformat(start)]

    def now():
        t[0] += dt.timedelta(seconds=1)
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
    quarantined = list(mpath.parent.glob("manifest.json.corrupt.*"))
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
