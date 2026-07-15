"""cotdata-update exit codes: non-zero on a hard fetch failure so a scheduler
(Windows Task Scheduler / cron) can retry, zero on success or 'no new data'."""
import sys
from unittest import mock

import pytest


def _argv(monkeypatch, tmp_path, *args):
    monkeypatch.setenv("COTDATA_STORE", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["cotdata-update", *args])


def test_exits_nonzero_when_cot_hard_fails(tmp_path, monkeypatch):
    _argv(monkeypatch, tmp_path, "--cot-legacy")
    from cotdata import update
    with mock.patch("cotdata.providers.cftc.update",
                    return_value={"kind": "cot_legacy", "ok": False, "wrote": 0}):
        with pytest.raises(SystemExit) as ei:
            update.main()
    assert ei.value.code not in (0, None)  # non-zero exit


def test_exits_zero_on_cot_success(tmp_path, monkeypatch):
    _argv(monkeypatch, tmp_path, "--cot-legacy")
    from cotdata import update
    with mock.patch("cotdata.providers.cftc.update",
                    return_value={"kind": "cot_legacy", "ok": True, "wrote": 5}):
        update.main()  # must not raise SystemExit


def test_exits_nonzero_when_prices_have_failures(tmp_path, monkeypatch):
    _argv(monkeypatch, tmp_path, "--prices")
    from cotdata import update
    with mock.patch("cotdata.providers.norgate.update",
                    return_value={"kind": "prices", "symbols_failed": ["GC"], "ok": []}):
        with pytest.raises(SystemExit) as ei:
            update.main()
    assert ei.value.code not in (0, None)


def test_require_final_defers_when_not_ready(tmp_path, monkeypatch):
    _argv(monkeypatch, tmp_path, "--prices", "--require-final")
    from cotdata import update
    with mock.patch("cotdata.providers.norgate.finals_ready", return_value=(False, {"Futures": None})), \
         mock.patch("cotdata.providers.norgate.update") as m_update:
        with pytest.raises(SystemExit) as ei:
            update.main()
    assert ei.value.code not in (0, None)   # non-zero -> scheduler retries
    m_update.assert_not_called()            # did NOT capture interim prices


def test_require_final_runs_when_ready(tmp_path, monkeypatch):
    _argv(monkeypatch, tmp_path, "--prices", "--require-final")
    from cotdata import update
    with mock.patch("cotdata.providers.norgate.finals_ready", return_value=(True, {})), \
         mock.patch("cotdata.providers.norgate.update",
                    return_value={"kind": "prices", "symbols_failed": [], "ok": ["ES"]}) as m_update:
        update.main()                       # must not raise
    m_update.assert_called_once()
