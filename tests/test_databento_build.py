"""Stage-2 databento build (ADR-0006): additive back-adjustment from the raw store.

Populates a raw store directly (as ingest would leave it), runs build(), and reads
the result back through the real consumer API (cotdata.get_prices) to verify unadj,
settlement override, Open Interest, and the Norgate-style additive back-adjustment.
"""
from pathlib import Path

import pandas as pd
import pytest

import cotdata
from cotdata.providers.databento import build


def _write_ohlcv(raw, symbol, feed, dates, close, sym, instrument_id=None):
    idx = pd.DatetimeIndex(pd.to_datetime(dates), name="Date")
    n = len(close)
    data = {"open": close, "high": [c + 0.5 for c in close], "low": [c - 0.5 for c in close],
            "close": close, "volume": [1000] * n,
            "symbol": sym if isinstance(sym, list) else [sym] * n}
    if instrument_id is not None:
        data["instrument_id"] = instrument_id
    df = pd.DataFrame(data, index=idx)
    p = Path(raw) / "ohlcv" / f"{symbol}{feed}.parquet"
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(p)


def _write_stats(raw, symbol, feed, dates, settle=None, oi=None):
    idx = pd.to_datetime(dates)
    frames = []
    if settle is not None:
        frames.append(pd.DataFrame(
            {"ts_event": idx, "ts_ref": idx, "stat_type": 3, "price": settle,
             "quantity": float("nan")}))
    if oi is not None:
        frames.append(pd.DataFrame(
            {"ts_event": idx, "ts_ref": pd.NaT, "stat_type": 9, "price": float("nan"),
             "quantity": oi}))
    df = pd.concat(frames, ignore_index=True)
    p = Path(raw) / "statistics" / f"{symbol}{feed}.parquet"
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(p)


@pytest.fixture
def stores(tmp_path, monkeypatch):
    raw, store_dir = tmp_path / "raw", tmp_path / "store"
    monkeypatch.setenv("COTDATA_DATABENTO_RAW", str(raw))
    monkeypatch.setenv("COTDATA_STORE", str(store_dir))
    return raw, store_dir


def test_build_back_adjusts_the_roll_gap(stores):
    raw, _ = stores
    dates = pd.date_range("2020-01-01", periods=6, freq="D")
    # Front contract A on d1-3, rolls to B at d3's close; B on d4-6.
    _write_ohlcv(raw, "ES", ".n.0", dates, [100, 101, 102, 110, 111, 112], ["A", "A", "A", "B", "B", "B"])
    # Second contract carries B on d1-3 (so n1[d3] is B's price the day of the roll).
    _write_ohlcv(raw, "ES", ".n.1", dates, [103, 104, 105, 113, 114, 115], ["B", "B", "B", "C", "C", "C"])

    res = build(["ES"])
    assert res["ok"] and res["wrote"] == 1

    unadj = cotdata.get_prices("ES", adjustment="unadj")
    backadj = cotdata.get_prices("ES", adjustment="backadj")

    # unadj keeps the raw front prices (roll gap intact: 102 -> 110).
    assert list(unadj["Close"]) == [100, 101, 102, 110, 111, 112]
    # Roll gap on d3 = n1 - n0 = 105 - 102 = +3, so every price up to & incl. d3 shifts +3.
    assert list(backadj["Close"]) == [103, 104, 105, 110, 111, 112]
    # The whole bar shifts by the same offset; the newest segment is untouched.
    assert backadj["High"].iloc[0] == unadj["High"].iloc[0] + 3
    assert backadj["Low"].iloc[2] == unadj["Low"].iloc[2] + 3
    assert backadj["Close"].iloc[-1] == unadj["Close"].iloc[-1]


def test_build_detects_rolls_from_instrument_id_not_symbol(stores):
    # The real databento shape: for a continuous series the `symbol` column is a
    # CONSTANT alias ("ES.n.0"), and instrument_id is the resolved contract that
    # changes at the roll. Roll detection must key on instrument_id, not symbol.
    raw, _ = stores
    dates = pd.date_range("2020-01-01", periods=6, freq="D")
    _write_ohlcv(raw, "ES", ".n.0", dates, [100, 101, 102, 110, 111, 112],
                 sym="ES.n.0", instrument_id=[10, 10, 10, 20, 20, 20])
    _write_ohlcv(raw, "ES", ".n.1", dates, [103, 104, 105, 113, 114, 115],
                 sym="ES.n.1", instrument_id=[20, 20, 20, 30, 30, 30])

    build(["ES"])
    unadj = cotdata.get_prices("ES", adjustment="unadj")
    backadj = cotdata.get_prices("ES", adjustment="backadj")
    # The constant `symbol` alias would find no rolls; instrument_id finds the d3 roll,
    # gap = 105 - 102 = +3, applied to the pre-roll segment.
    assert list(unadj["Close"]) == [100, 101, 102, 110, 111, 112]
    assert list(backadj["Close"]) == [103, 104, 105, 110, 111, 112]


def test_build_uses_settlement_and_open_interest(stores):
    raw, _ = stores
    dates = pd.date_range("2020-01-01", periods=6, freq="D")
    _write_ohlcv(raw, "ES", ".n.0", dates, [100, 101, 102, 110, 111, 112], ["A", "A", "A", "B", "B", "B"])
    _write_ohlcv(raw, "ES", ".n.1", dates, [103, 104, 105, 113, 114, 115], ["B", "B", "B", "C", "C", "C"])
    # Settlement (stat_type 3) sits 0.5 above the last-trade close; OI (stat_type 9) = 5000.
    _write_stats(raw, "ES", ".n.0", dates, settle=[100.5, 101.5, 102.5, 110.5, 111.5, 112.5], oi=[5000] * 6)
    _write_stats(raw, "ES", ".n.1", dates, settle=[103.5, 104.5, 105.5, 113.5, 114.5, 115.5])

    build(["ES"])
    unadj = cotdata.get_prices("ES", adjustment="unadj")
    backadj = cotdata.get_prices("ES", adjustment="backadj")

    # Close is the settlement, not the ohlcv last trade; OI comes from stat_type 9.
    assert list(unadj["Close"]) == [100.5, 101.5, 102.5, 110.5, 111.5, 112.5]
    assert list(unadj["Open Interest"]) == [5000] * 6
    # Gap measured on settlements: 105.5 - 102.5 = +3.0.
    assert list(backadj["Close"]) == [103.5, 104.5, 105.5, 110.5, 111.5, 112.5]


def test_build_no_rolls_leaves_series_unadjusted(stores, capsys):
    raw, _ = stores
    dates = pd.date_range("2020-01-01", periods=5, freq="D")
    _write_ohlcv(raw, "ES", ".n.0", dates, [100, 101, 102, 103, 104], ["A"] * 5)
    _write_ohlcv(raw, "ES", ".n.1", dates, [110, 111, 112, 113, 114], ["B"] * 5)

    build(["ES"])
    unadj = cotdata.get_prices("ES", adjustment="unadj")
    backadj = cotdata.get_prices("ES", adjustment="backadj")

    assert list(backadj["Close"]) == list(unadj["Close"])   # no roll → no adjustment
    assert "no rolls detected" in capsys.readouterr().out


def test_build_applies_norgate_unit_scale(stores):
    # SI/HG/6J are cents / IMM-x100 in the toolchain's Norgate convention; databento gives
    # true dollars. The build scales the price columns x100 so the store is a drop-in, and
    # leaves Volume / Open Interest (counts) untouched.
    raw, _ = stores
    dates = pd.date_range("2020-01-01", periods=4, freq="D")
    _write_ohlcv(raw, "SI", ".n.0", dates, [25.0, 25.1, 25.2, 25.3], "A")   # $/oz
    _write_stats(raw, "SI", ".n.0", dates, oi=[7000] * 4)

    build(["SI"])
    unadj = cotdata.get_prices("SI", adjustment="unadj")
    assert list(unadj["Close"]) == [2500.0, 2510.0, 2520.0, 2530.0]         # x100 -> cents
    assert unadj["High"].iloc[0] == (25.0 + 0.5) * 100
    assert list(unadj["Open Interest"]) == [7000] * 4                       # counts unscaled
    assert list(unadj["Volume"]) == [1000] * 4


def test_build_leaves_unscaled_symbol_in_native_units(stores):
    # A symbol not in _PRICE_SCALE (ES) is written as-is — the scale map is a deny-by-default
    # allowlist, so a new symbol never gets silently x100'd.
    raw, _ = stores
    dates = pd.date_range("2020-01-01", periods=3, freq="D")
    _write_ohlcv(raw, "ES", ".n.0", dates, [4000.0, 4001.0, 4002.0], "A")
    build(["ES"])
    assert list(cotdata.get_prices("ES", adjustment="unadj")["Close"]) == [4000.0, 4001.0, 4002.0]


def test_build_skips_symbol_missing_from_raw_store(stores):
    # Nothing written for CL → build reports it skipped (and it's not 'ok').
    res = build(["CL"])
    assert res["wrote"] == 0 and res["ok"] is False


# ── windowed n.1 statistics: same back-adjustment at a fraction of the download ──
class _WResp:
    def __init__(self, df):
        self._df = df

    def to_df(self):
        return self._df


class _WClient:
    def __init__(self, frames):
        self.frames = frames
        self.calls = []

    @property
    def timeseries(client_self):
        class _TS:
            def get_range(ts_self, *, dataset, symbols, stype_in, schema, start, end):
                client_self.calls.append((symbols[0], schema, start, end))
                fr = client_self.frames.get((symbols[0], schema))
                if fr is None or fr.empty:
                    return _WResp(pd.DataFrame())
                naive = fr.index.tz_convert(None)
                mask = (naive >= pd.Timestamp(start)) & (naive < pd.Timestamp(end))
                return _WResp(fr[mask])
        return _TS()


def _roll_frames(dates):
    idx = pd.to_datetime(dates).tz_localize("UTC")
    idx.name = "ts_event"

    def ohlcv(close, iid):
        return pd.DataFrame({"open": close, "high": close, "low": close, "close": close,
                             "volume": [1000] * len(close), "instrument_id": iid,
                             "symbol": ["ES.n"] * len(close)}, index=idx)

    def stats(settle):
        return pd.DataFrame({"ts_ref": idx, "stat_type": 3, "price": settle,
                             "quantity": float("nan")}, index=idx)

    return {
        ("ES.n.0", "ohlcv-1d"): ohlcv([100, 101, 102, 110, 111, 112], [10, 10, 10, 20, 20, 20]),
        ("ES.n.1", "ohlcv-1d"): ohlcv([103, 104, 105, 113, 114, 115], [20, 20, 20, 30, 30, 30]),
        ("ES.n.0", "statistics"): stats([100.5, 101.5, 102.5, 110.5, 111.5, 112.5]),
        ("ES.n.1", "statistics"): stats([103.5, 104.5, 105.5, 113.5, 114.5, 115.5]),
    }


def test_windowed_n1_stats_matches_full_backadj(tmp_path, monkeypatch):
    import cotdata
    from cotdata.providers.databento import ingest
    dates = pd.date_range("2020-01-01", periods=6)          # roll at 2020-01-03 (id 10→20)

    def run(tag, window):
        root = tmp_path / tag
        monkeypatch.setenv("COTDATA_DATABENTO_RAW", str(root / "raw"))
        monkeypatch.setenv("COTDATA_STORE", str(root / "store"))
        client = _WClient(_roll_frames(dates))
        ingest(symbols=["ES"], client=client, end="2020-01-07", cold_start="2020-01-01",
               n1_stats_window=window)
        build(["ES"])
        return cotdata.get_prices("ES", adjustment="backadj"), client

    full_bad, full_client = run("full", None)
    win_bad, win_client = run("win", 1)

    # Identical back-adjustment (gap = n1_settle - n0_settle = 105.5 - 102.5 = +3 either way).
    assert list(win_bad["Close"]) == list(full_bad["Close"]) == [103.5, 104.5, 105.5, 110.5, 111.5, 112.5]

    # ...but the n.1 statistics fetch was a narrow roll window, not the full range.
    def n1_stat_spans(c):
        return [(s, e) for (sym, sch, s, e) in c.calls if sym == "ES.n.1" and sch == "statistics"]
    assert n1_stat_spans(full_client) == [("2020-01-01", "2020-01-07")]   # one full-range pull
    win_spans = n1_stat_spans(win_client)
    assert win_spans and all(s >= "2020-01-02" and e <= "2020-01-05" for s, e in win_spans)
