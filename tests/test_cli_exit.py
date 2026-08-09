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


def test_exits_nonzero_when_the_databento_build_fails(tmp_path, monkeypatch):
    """The price half is databento only now: ADR-0007 moved the Norgate and Yahoo
    producers (and the --require-final finals gate that covered them) to marketdata.
    What this file protects is unchanged — a scheduler must be able to tell a failed
    run from a quiet one."""
    _argv(monkeypatch, tmp_path, "--build-databento")
    from cotdata import update
    with mock.patch("cotdata.providers.databento.build",
                    return_value={"kind": "build_databento", "ok": False, "wrote": 0}):
        with pytest.raises(SystemExit) as ei:
            update.main()
    assert ei.value.code not in (0, None)


def test_exits_zero_on_databento_build_success(tmp_path, monkeypatch):
    _argv(monkeypatch, tmp_path, "--build-databento")
    from cotdata import update
    with mock.patch("cotdata.providers.databento.build",
                    return_value={"kind": "build_databento", "ok": True, "wrote": 3}):
        update.main()  # must not raise SystemExit


def test_retired_price_flags_are_refused_not_ignored(tmp_path, monkeypatch):
    """A scheduler line still carrying --prices must fail loudly.

    argparse rejects an unknown flag, so this is really a guard against quietly
    re-adding one as a no-op alias: a nightly job that keeps exiting 0 while
    fetching nothing is a store that silently stops being updated.
    """
    from cotdata import update
    for flag in ("--prices", "--prices-yahoo", "--metadata", "--require-final"):
        _argv(monkeypatch, tmp_path, flag)
        with pytest.raises(SystemExit) as ei:
            update.main()
        assert ei.value.code not in (0, None)
