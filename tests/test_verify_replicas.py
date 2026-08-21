"""docs/examples/mac/verify-replicas.sh — the alarm that says a sync stopped.

Runs the REAL script, with `ssh` and the two `--check` binaries stubbed on PATH.
Worth testing rather than eyeballing for the same reason the script exists: its
whole job is to be the thing that notices, and a verifier that silently passes is
indistinguishable from a working sync until someone reads a stale chart.

ADR-0007 split the data across two stores, so the case that matters here is a
CURRENT COT store beside a STALE (or absent) bar store — green under the old
two-check version, red under this one.
"""
import os
import subprocess
import time
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "docs" / "examples" / "mac" / "verify-replicas.sh"
DAY = 86400


@pytest.fixture()
def rig(tmp_path):
    """A local pair of stores plus a stubbed `ssh` standing in for the remote pair.

    The stub answers the two things the script asks a remote: an mtime probe
    (`date -r FILE +%s`) and a `--check` run. Remote mtimes come from files in
    `remote/`, so a test moves them the same way it moves the local ones.
    """
    local_cot = tmp_path / "local_cot"
    local_bars = tmp_path / "local_bars"
    remote = tmp_path / "remote"
    for p in (local_cot, local_bars, remote / "cot", remote / "bars"):
        p.mkdir(parents=True)
    (local_cot / "status.json").write_text("{}")
    (local_bars / "manifest.json").write_text("{}")
    (remote / "cot" / "status.json").write_text("{}")
    (remote / "bars" / "manifest.json").write_text("{}")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # The stub resolves a remote path to its local stand-in under remote/, so the
    # script's real `date -r` runs against a real file and the platform handling
    # is exercised rather than mocked away.
    (bin_dir / "ssh").write_text(
        "#!/usr/bin/env bash\n"
        'cmd="${@: -1}"\n'
        'case "$cmd" in\n'
        '  *"date -r"*) f=$(printf %s "$cmd" | sed "s/.*date -r .\\(.*\\). +%s.*/\\1/")\n'
        '               exec date -r "$f" +%s ;;\n'
        '  *) echo "stub --check ok" ;;\n'
        'esac\n')
    for name in ("cotdata-update", "marketdata-update"):
        (bin_dir / name).write_text("#!/usr/bin/env bash\necho 'stub --check ok'\n")
    for f in bin_dir.iterdir():
        f.chmod(0o755)

    env = dict(os.environ)
    env.update({
        "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
        "LOCAL_COT": str(local_cot), "LOCAL_BARS": str(local_bars),
        "LOCAL_COT_CHECK": str(bin_dir / "cotdata-update"),
        "LOCAL_BAR_CHECK": str(bin_dir / "marketdata-update"),
        "REMOTE": "stub@example.invalid",
        "REMOTE_COT": str(remote / "cot"), "REMOTE_BARS": str(remote / "bars"),
        "REMOTE_COT_CHECK": "cotdata-update", "REMOTE_BAR_CHECK": "marketdata-update",
    })
    return {"env": env, "local_cot": local_cot, "local_bars": local_bars,
            "remote": remote}


def run(rig):
    return subprocess.run(["bash", str(_SCRIPT)], env=rig["env"],
                          capture_output=True, text=True)


def age(path: Path, days: float):
    when = time.time() - days * DAY
    os.utime(path, (when, when))


def test_all_current_passes(rig):
    r = run(rig)
    assert r.returncode == 0, r.stdout
    assert "RESULT: PASS" in r.stdout


def test_stale_bar_store_fails_while_cot_is_current(rig):
    """The exact failure ADR-0007 introduced and the old two-check version could
    not see: COT arriving on schedule while no bar has landed in a fortnight."""
    age(rig["local_bars"] / "manifest.json", 14)
    age(rig["remote"] / "bars" / "manifest.json", 14)
    r = run(rig)
    assert r.returncode == 1
    assert "local bar manifest.json written" in r.stdout
    assert "past the 4d window" in r.stdout
    assert "PASS: local COT" in r.stdout       # the COT half really is fine
    assert "RESULT: FAIL" in r.stdout


def test_a_weekend_old_bar_store_still_passes(rig):
    """Not a bug to be fixed by tightening the window. marketdata writes its
    manifest only when a bar is actually written, so a Friday-to-Tuesday gap is
    the normal state of a correctly working sync — a `today` test here would cry
    wolf every weekend and get ignored by the second month."""
    age(rig["local_bars"] / "manifest.json", 3)
    age(rig["remote"] / "bars" / "manifest.json", 3)
    r = run(rig)
    assert r.returncode == 0, r.stdout
    assert "3d old, window 4d" in r.stdout


def test_window_is_configurable(rig):
    age(rig["local_bars"] / "manifest.json", 6)
    age(rig["remote"] / "bars" / "manifest.json", 6)
    assert run(rig).returncode == 1
    rig["env"]["BAR_MAX_AGE_DAYS"] = "10"
    assert run(rig).returncode == 0


def test_missing_bar_manifest_names_the_real_problem(rig):
    """A replica that never received a bar store at all. Distinct from stale, and
    the likelier state right after the migration."""
    (rig["local_bars"] / "manifest.json").unlink()
    r = run(rig)
    assert r.returncode == 1
    assert "no bars have reached this replica" in r.stdout


def test_stale_cot_store_still_fails(rig):
    """The original check, unchanged: cotdata rewrites status.json every run, so
    anything but today means the producer or its sync did not run."""
    age(rig["local_cot"] / "status.json", 2)
    r = run(rig)
    assert r.returncode == 1
    assert "FAIL: local COT status.json last written" in r.stdout


def test_divergent_replicas_warn_even_when_both_are_inside_the_window(rig):
    """Both syncs preserve timestamps, so a matching producer run lands the SAME
    mtime on both replicas. Different mtimes mean one push is behind — and inside
    the staleness window neither replica looks wrong on its own."""
    age(rig["remote"] / "bars" / "manifest.json", 2)
    r = run(rig)
    assert r.returncode == 0                   # both still inside the window
    assert "WARN: the two bar replicas hold DIFFERENT manifest mtimes" in r.stdout


def test_matching_replicas_do_not_warn(rig):
    same = time.time() - DAY
    for p in (rig["local_bars"] / "manifest.json", rig["remote"] / "bars" / "manifest.json"):
        os.utime(p, (same, same))
    r = run(rig)
    assert r.returncode == 0
    assert "WARN" not in r.stdout
