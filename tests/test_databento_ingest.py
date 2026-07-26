"""Stage-1 databento ingest (ADR-0006): raw landing store + resume manifest.

Exercised with an injected fake client shaped like ``databento.Historical`` — no
API key, no network. Verifies the raw files, the fetched-range manifest, the
resume-from-last_date behaviour, and that databento-null symbols are skipped.
"""
import json

import pandas as pd
import pytest

from cotdata.providers.databento import _ingest_manifest_path, ingest, reconcile_manifest


# ── a databento.Historical-shaped fake ───────────────────────────────────────
class _FakeResp:
    def __init__(self, df):
        self._df = df

    def to_df(self):
        return self._df


class _FakeTS:
    def __init__(self, owner):
        self.owner = owner

    def get_range(self, *, dataset, symbols, stype_in, schema, start, end):
        self.owner.calls.append((symbols[0], schema, start, end))
        frame = self.owner.frames.get((symbols[0], schema))
        if frame is None or frame.empty:
            return _FakeResp(pd.DataFrame())
        s, e = pd.Timestamp(start), pd.Timestamp(end)
        # databento returns ts_event as the index for both schemas.
        naive = frame.index.tz_convert(None)
        mask = (naive >= s) & (naive <= e)
        return _FakeResp(frame[mask])


class _FakeClient:
    def __init__(self, frames):
        self.frames = frames
        self.calls = []

    @property
    def timeseries(self):
        return _FakeTS(self)


def _ohlcv(dates, base):
    idx = pd.to_datetime(dates).tz_localize("UTC")
    idx.name = "ts_event"
    n = len(idx)
    return pd.DataFrame(
        {"open": [base] * n, "high": [base + 1] * n, "low": [base - 1] * n,
         "close": [base + 0.5] * n, "volume": [1000] * n, "symbol": ["ES.FUT"] * n},
        index=idx)


def _stats(dates, price):
    idx = pd.to_datetime(dates).tz_localize("UTC")
    idx.name = "ts_event"
    n = len(idx)
    return pd.DataFrame(
        {"ts_ref": idx, "stat_type": [3] * n, "price": [price] * n, "quantity": [0] * n},
        index=idx)


def _frames(dates):
    return {
        ("ES.n.0", "ohlcv-1d"): _ohlcv(dates, 100),
        ("ES.n.1", "ohlcv-1d"): _ohlcv(dates, 101),
        ("ES.n.0", "statistics"): _stats(dates, 100.5),
        ("ES.n.1", "statistics"): _stats(dates, 101.5),
    }


# ── tests ─────────────────────────────────────────────────────────────────────
def test_ingest_writes_raw_files_and_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("COTDATA_DATABENTO_RAW", str(tmp_path))
    dates = pd.date_range("2020-01-01", periods=5, freq="D")
    client = _FakeClient(_frames(dates))

    res = ingest(symbols=["ES"], client=client, end="2020-01-05", cold_start="2020-01-01")

    assert res["ok"] and res["symbols"] == 1
    for feed in (".n.0", ".n.1"):
        assert (tmp_path / "ohlcv" / f"ES{feed}.parquet").exists()
        assert (tmp_path / "statistics" / f"ES{feed}.parquet").exists()

    ohlcv = pd.read_parquet(tmp_path / "ohlcv" / "ES.n.0.parquet")
    assert len(ohlcv) == 5
    assert ohlcv.index.tz is None                      # bronze is tz-naive
    assert ohlcv.index.is_monotonic_increasing

    man = json.loads((tmp_path / "ingest_manifest.json").read_text())
    assert man["ES.n.0:ohlcv-1d"]["last_date"] == "2020-01-05"
    assert man["ES.n.0:ohlcv-1d"]["first_date"] == "2020-01-01"
    assert man["ES.n.0:ohlcv-1d"]["n_rows"] == 5


def test_ingest_pages_statistics_into_year_chunks(tmp_path, monkeypatch):
    # A from-inception statistics pull is paged into _STATS_CHUNK_DAYS windows so no single
    # get_range is large enough to time out; ohlcv (tiny) stays one request. Shrink the
    # window so a short fixture range still pages.
    import cotdata.providers.databento as dbmod
    monkeypatch.setenv("COTDATA_DATABENTO_RAW", str(tmp_path))
    monkeypatch.setattr(dbmod, "_STATS_CHUNK_DAYS", 2)
    dates = pd.date_range("2020-01-01", periods=6)
    client = _FakeClient(_frames(dates))

    dbmod.ingest(symbols=["ES"], client=client, end="2020-01-06", cold_start="2020-01-01")

    stat_calls = [c for c in client.calls if c[0] == "ES.n.0" and c[1] == "statistics"]
    ohlcv_calls = [c for c in client.calls if c[0] == "ES.n.0" and c[1] == "ohlcv-1d"]
    assert len(stat_calls) >= 3          # 5-day span paged by 2-day windows
    assert len(ohlcv_calls) == 1         # ohlcv is not paged
    # Overlapping fake boundaries notwithstanding, the raw store dedupes to one row per day.
    combined = pd.read_parquet(tmp_path / "statistics" / "ES.n.0.parquet")
    assert len(combined) == 6
    man = json.loads((tmp_path / "ingest_manifest.json").read_text())
    assert man["ES.n.0:statistics"]["last_date"] == "2020-01-06"


class _FlakyStatsTS(_FakeTS):
    """get_range that raises on the statistics window starting on ``fail_start`` until the
    owner's ``heal`` flag flips — models a mid-history page failure that a re-run recovers."""
    def get_range(self, *, dataset, symbols, stype_in, schema, start, end):
        if (schema == "statistics" and start == self.owner.fail_start
                and not self.owner.heal):
            raise TimeoutError("read timed out")
        return super().get_range(dataset=dataset, symbols=symbols, stype_in=stype_in,
                                 schema=schema, start=start, end=end)


class _FlakyStatsClient(_FakeClient):
    def __init__(self, frames, fail_start):
        super().__init__(frames)
        self.fail_start, self.heal = fail_start, False

    @property
    def timeseries(self):
        return _FlakyStatsTS(self)


def test_ingest_resumes_mid_history_after_a_page_failure(tmp_path, monkeypatch):
    # A page failure part-way through a paged statistics pull must leave the manifest at the
    # last good page, so a re-run resumes there rather than re-pulling from inception.
    import cotdata.providers.databento as dbmod
    monkeypatch.setenv("COTDATA_DATABENTO_RAW", str(tmp_path))
    monkeypatch.setattr(dbmod, "_STATS_CHUNK_DAYS", 2)
    dates = pd.date_range("2020-01-01", periods=8)
    client = _FlakyStatsClient(_frames(dates), fail_start="2020-01-05")

    dbmod.ingest(symbols=["ES"], client=client, end="2020-01-08", cold_start="2020-01-01")
    man = json.loads((tmp_path / "ingest_manifest.json").read_text())
    # The windows before 2020-01-05 landed; the 2020-01-05 window failed, so the watermark
    # stops before the end rather than being advanced past the gap.
    assert man["ES.n.0:statistics"]["last_date"] < "2020-01-08"

    # Re-run with the flake healed: resume from the failed window, finish the pull.
    client.heal = True
    client.calls.clear()
    dbmod.ingest(symbols=["ES"], client=client, end="2020-01-08", cold_start="2020-01-01")
    resumed = [c for c in client.calls if c[0] == "ES.n.0" and c[1] == "statistics"]
    assert resumed and resumed[0][2] >= "2020-01-04"     # did NOT restart from 2020-01-01
    man = json.loads((tmp_path / "ingest_manifest.json").read_text())
    assert man["ES.n.0:statistics"]["last_date"] == "2020-01-08"
    combined = pd.read_parquet(tmp_path / "statistics" / "ES.n.0.parquet")
    assert len(combined) == 8


def test_ingest_resumes_from_last_date(tmp_path, monkeypatch):
    monkeypatch.setenv("COTDATA_DATABENTO_RAW", str(tmp_path))

    # First pull: 5 days.
    ingest(symbols=["ES"], client=_FakeClient(_frames(pd.date_range("2020-01-01", periods=5))),
           end="2020-01-05", cold_start="2020-01-01")

    # Second pull: source now has 8 days; a fresh client so we can inspect its calls.
    client2 = _FakeClient(_frames(pd.date_range("2020-01-01", periods=8)))
    ingest(symbols=["ES"], client=client2, end="2020-01-08", cold_start="2020-01-01")

    # Resume: the ohlcv .n.0 call must start the day AFTER the stored last_date.
    ohlcv_calls = [c for c in client2.calls if c[0] == "ES.n.0" and c[1] == "ohlcv-1d"]
    assert ohlcv_calls and ohlcv_calls[0][2] == "2020-01-06"

    combined = pd.read_parquet(tmp_path / "ohlcv" / "ES.n.0.parquet")
    assert len(combined) == 8                          # 5 + 3 appended, no dupes
    man = json.loads((tmp_path / "ingest_manifest.json").read_text())
    assert man["ES.n.0:ohlcv-1d"]["last_date"] == "2020-01-08"
    assert man["ES.n.0:ohlcv-1d"]["n_rows"] == 8


def test_ingest_noop_when_already_current(tmp_path, monkeypatch):
    monkeypatch.setenv("COTDATA_DATABENTO_RAW", str(tmp_path))
    ingest(symbols=["ES"], client=_FakeClient(_frames(pd.date_range("2020-01-01", periods=5))),
           end="2020-01-05", cold_start="2020-01-01")

    client2 = _FakeClient(_frames(pd.date_range("2020-01-01", periods=5)))
    ingest(symbols=["ES"], client=client2, end="2020-01-05", cold_start="2020-01-01")
    assert client2.calls == []                         # start would be > end → nothing fetched


def test_ingest_skips_databento_null_symbol(tmp_path, monkeypatch):
    monkeypatch.setenv("COTDATA_DATABENTO_RAW", str(tmp_path))
    client = _FakeClient({})
    res = ingest(symbols=["CC"], client=client, end="2020-01-05")   # CC is databento: null
    assert res["symbols"] == 0
    assert client.calls == []


def test_ingest_skips_when_start_equals_end(tmp_path, monkeypatch):
    # Regression: an up-to-date symbol computes start == end, which databento rejects
    # (422 data_time_range_start_on_or_after_end). The guard must skip, not fetch.
    monkeypatch.setenv("COTDATA_DATABENTO_RAW", str(tmp_path))
    ingest(symbols=["ES"], client=_FakeClient(_frames(pd.date_range("2020-01-01", periods=5))),
           end="2020-01-05", cold_start="2020-01-01")               # stores last_date = 2020-01-05
    # end == stored last_date + 1 → start (last+1) == end → must skip, no API call.
    client2 = _FakeClient(_frames(pd.date_range("2020-01-01", periods=6)))
    ingest(symbols=["ES"], client=client2, end="2020-01-06", cold_start="2020-01-01")
    assert client2.calls == []


def test_fetch_retries_transient_failures_then_succeeds():
    from cotdata.providers.databento import _fetch
    n = {"calls": 0}

    class _TS:
        def get_range(self, **k):
            n["calls"] += 1
            if n["calls"] < 3:
                raise ConnectionError("Response ended prematurely")
            return _FakeResp(pd.DataFrame({"x": [1]}))

    class _C:
        timeseries = _TS()

    df = _fetch(_C(), "GLBX.MDP3", "ES.n.0", "ohlcv-1d", "2020-01-01", "2020-01-05",
                retries=3, backoff=0)
    assert n["calls"] == 3 and not df.empty


def test_fetch_raises_after_exhausting_retries():
    from cotdata.providers.databento import _fetch

    class _TS:
        def get_range(self, **k):
            raise TimeoutError("read timed out")

    class _C:
        timeseries = _TS()

    with pytest.raises(TimeoutError):
        _fetch(_C(), "GLBX.MDP3", "ES.n.0", "ohlcv-1d", "2020-01-01", "2020-01-05",
               retries=2, backoff=0)


class _FakeBatch:
    """databento client.batch stand-in: submit → 'done' → download CSV files."""
    def __init__(self, dates):
        self.dates = dates
        self.submitted = []

    def submit_job(self, **k):
        self.submitted.append(k)
        return {"id": "j1", "state": "queued"}

    def list_jobs(self):
        return [{"id": "j1", "state": "done"}]

    def download(self, *, job_id, output_dir):
        import os
        k = self.submitted[-1]
        schema, syms = k["schema"], k["symbols"]
        rows = []
        for sym in syms:
            for i, d in enumerate(self.dates):
                base = {"ts_event": d.isoformat(), "instrument_id": 10, "symbol": sym}
                if schema == "ohlcv-1d":
                    rows.append({**base, "open": 100 + i, "high": 100 + i, "low": 100 + i,
                                 "close": 100 + i, "volume": 1000})
                else:
                    rows.append({**base, "ts_ref": d.isoformat(), "stat_type": 3,
                                 "price": 100.5 + i, "quantity": 0})
        p = os.path.join(output_dir, f"out.{schema}.csv")
        pd.DataFrame(rows).to_csv(p, index=False)
        return [p]


class _FakeBatchClient:
    def __init__(self, dates):
        self.batch = _FakeBatch(dates)


def test_ingest_batch_writes_raw_and_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("COTDATA_DATABENTO_RAW", str(tmp_path))
    dates = pd.date_range("2020-01-01", periods=4)
    from cotdata.providers.databento import ingest_batch

    res = ingest_batch(symbols=["ES"], client=_FakeBatchClient(dates),
                       end="2020-01-05", cold_start="2020-01-01")
    assert res["ok"] and res["symbols"] == 1

    man = json.loads((tmp_path / "ingest_manifest.json").read_text())
    assert man["ES.n.0:ohlcv-1d"]["last_date"] == "2020-01-04"
    assert man["ES.n.0:ohlcv-1d"]["batch"] is True
    assert man["ES.n.0:statistics"]["last_date"] == "2020-01-04"

    ohlcv = pd.read_parquet(tmp_path / "ohlcv" / "ES.n.0.parquet")
    assert len(ohlcv) == 4 and ohlcv.index.name == "Date" and ohlcv.index.tz is None
    stats = pd.read_parquet(tmp_path / "statistics" / "ES.n.0.parquet")
    assert (stats["stat_type"] == 3).all()


def test_reconcile_manifest_rebuilds_from_raw_store(tmp_path, monkeypatch):
    monkeypatch.setenv("COTDATA_DATABENTO_RAW", str(tmp_path))
    dates = pd.date_range("2020-01-01", periods=5)
    ingest(symbols=["ES"], client=_FakeClient(_frames(dates)), end="2020-01-05", cold_start="2020-01-01")

    # Simulate a run that wrote the raw parquets but never persisted the manifest.
    _ingest_manifest_path().unlink()

    recorded = reconcile_manifest()
    assert recorded, "reconcile should record the on-disk raw tables"
    man = json.loads(_ingest_manifest_path().read_text())
    assert man["ES.n.0:ohlcv-1d"]["last_date"] == "2020-01-05"
    assert man["ES.n.0:ohlcv-1d"]["n_rows"] == 5
    assert man["ES.n.0:ohlcv-1d"]["reconciled"] is True
    assert man["ES.n.0:statistics"]["last_date"] == "2020-01-05"

    # With the manifest rebuilt, a re-ingest sees everything current -> no API calls.
    client2 = _FakeClient(_frames(dates))
    ingest(symbols=["ES"], client=client2, end="2020-01-05", cold_start="2020-01-01")
    assert client2.calls == []
