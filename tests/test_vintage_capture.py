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
    http = _FakeHttp(_only(LEGACY_2026, (200, b"ZIPBYTES-week1", '"etag1"', "Fri, 24 Jul 2026 19:27:59 GMT")))

    res = _fetch(vintage, http, _clock())

    assert res["new_files"] == 1 and res["checks"] == 1
    rec = res["records"][0]
    # raw bytes retained on disk, immutably, under the year partition
    raw = store_env / rec["local_path"]
    assert raw.exists() and raw.read_bytes() == b"ZIPBYTES-week1"
    assert raw.parent == store_env / "vintage" / "raw" / "annual_zip" / "2026"
    # provenance recorded: sha, size, etag, last-modified, status
    import hashlib
    assert rec["content_sha256"] == hashlib.sha256(b"ZIPBYTES-week1").hexdigest()
    assert rec["byte_size"] == len(b"ZIPBYTES-week1")
    assert rec["http_etag"] == '"etag1"'
    assert rec["http_last_modified"] == "Fri, 24 Jul 2026 19:27:59 GMT"
    assert rec["parse_status"] == "pending"
    # and persisted in the vintage manifest
    assert len(vintage.read_snapshots()) == 1


def test_304_records_check_without_new_file(store_env):
    from cotdata import vintage
    http = _FakeHttp(_only(
        LEGACY_2026,
        (200, b"ZIPBYTES-week1", '"etag1"', "Fri, 24 Jul 2026 19:27:59 GMT"),
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
        (200, b"SAME", '"e1"', "lm1"),
        (200, b"SAME", '"e2"', "lm2"),  # server didn't honour conditional GET, but bytes match
    ))
    now = _clock()
    _fetch(vintage, http, now)
    res2 = _fetch(vintage, http, now)

    assert res2["new_files"] == 0
    assert res2["records"][0]["note"] == "unchanged bytes (deduped)"
    assert len(list((store_env / "vintage" / "raw").rglob("*.zip"))) == 1


def test_changed_bytes_writes_second_immutable_snapshot(store_env):
    from cotdata import vintage
    http = _FakeHttp(_only(
        LEGACY_2026,
        (200, b"week1", '"e1"', "lm1"),
        (200, b"week2-revised", '"e2"', "lm2"),
    ))
    now = _clock()
    _fetch(vintage, http, now)
    _fetch(vintage, http, now)

    raws = sorted((store_env / "vintage" / "raw").rglob("*.zip"))
    assert len(raws) == 2  # both vintages retained; neither overwritten
    assert {p.read_bytes() for p in raws} == {b"week1", b"week2-revised"}
