"""Stage-1 databento ingest (ADR-0006): raw landing store + resume manifest.

Exercised with an injected fake client shaped like ``databento.Historical`` — no
API key, no network. Verifies the raw files, the fetched-range manifest, the
resume-from-last_date behaviour, and that databento-null symbols are skipped.
"""
import json

import pandas as pd
import pytest

from cotdata.providers.databento import ingest


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
