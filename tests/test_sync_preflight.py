"""docs/examples/sync_preflight.py — the guard that refuses a destructive mirror.

It is a docs example rather than shipped code, but it is the one thing standing
between a `robocopy /MIR` and a store, and since ADR-0007 it has to read TWO store
layouts. A preflight that reads a bar store with cotdata's rules does not fail
loudly — it prints a plausible summary and reports zero orphans forever — so the
layout handling is worth pinning down.
"""
import importlib.util
import json
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "docs" / "examples" / "sync_preflight.py"


@pytest.fixture(scope="module")
def pf():
    spec = importlib.util.spec_from_file_location("sync_preflight", _SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _entry(source="norgate", rows=10):
    return {"last_date": "2026-08-20", "n_rows": rows, "source": source, "updated_at": "x"}


def make_cot_store(root: Path, entries=("ES_13874A",), source="cftc"):
    (root / "manifests").mkdir(parents=True, exist_ok=True)
    (root / "cot_legacy").mkdir(exist_ok=True)
    (root / "manifests" / "cot.json").write_text(json.dumps(
        {"cot_legacy": {name: _entry(source) for name in entries}}))
    for name in entries:
        (root / "cot_legacy" / f"{name}.parquet").write_bytes(b"")
    return root


def make_bar_store(root: Path, entries=(("futures", "norgate", "ES_backadj"),),
                   source=None):
    (root / "bars").mkdir(parents=True, exist_ok=True)
    man = {"schema_version": 2, "universe_is_point_in_time": False, "bars": {}}
    for domain, vendor, name in entries:
        d = root / "bars" / domain / vendor
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.parquet").write_bytes(b"")
        man["bars"][f"{domain}/{vendor}/{name}"] = _entry(source or vendor)
    (root / "manifest.json").write_text(json.dumps(man))
    return root


# ── layout detection ────────────────────────────────────────────────────────

def test_detects_each_layout_from_structure(pf, tmp_path):
    assert pf.detect_layout(make_cot_store(tmp_path / "cot")) == pf.COTDATA
    assert pf.detect_layout(make_bar_store(tmp_path / "bars")) == pf.MARKETDATA


def test_detects_marketdata_from_the_manifest_when_bars_dir_is_absent(pf, tmp_path):
    """A store whose manifest exists but whose bars/ has not been created yet —
    e.g. a replica mid-first-sync, manifest last. Structure is missing, so the
    manifest key has to carry the decision."""
    root = tmp_path / "empty-ish"
    root.mkdir()
    (root / "manifest.json").write_text(json.dumps({"schema_version": 2, "bars": {}}))
    assert pf.detect_layout(root) == pf.MARKETDATA


def test_unrecognisable_store_reads_as_cotdata(pf, tmp_path):
    """The default matters: a pre-split store has neither manifests/ nor bars/,
    and it must keep its old reading rather than be judged by the new rules."""
    root = tmp_path / "legacy"
    root.mkdir()
    (root / "manifest.json").write_text(json.dumps({"cot_legacy": {"ES": _entry()}}))
    assert pf.detect_layout(root) == pf.COTDATA


# ── the marketdata manifest is live, not legacy ─────────────────────────────

def test_marketdata_manifest_is_read_without_the_legacy_fallback(pf, tmp_path):
    """cotdata's loader treats root manifest.json as a per-domain FALLBACK. Applied
    to a bar store it happens to work, so this pins the intent: the bar store's
    manifest is the real thing, and the store-level flags beside the domains are
    dropped rather than mistaken for a domain."""
    root = make_bar_store(tmp_path / "bars")
    m = pf.load_manifest(root, pf.MARKETDATA)
    assert set(m) == {"bars"}                       # not schema_version / the pit flag
    assert "futures/norgate/ES_backadj" in m["bars"]


def test_cotdata_manifest_merge_still_prefers_halves_over_legacy(pf, tmp_path):
    root = make_cot_store(tmp_path / "cot", entries=("ES_13874A",))
    (root / "manifest.json").write_text(json.dumps(
        {"cot_legacy": {"STALE": _entry()}, "cot_tff": {"ZN_043602": _entry()}}))
    m = pf.load_manifest(root, pf.COTDATA)
    assert set(m["cot_legacy"]) == {"ES_13874A"}    # half file wins for its domain
    assert "cot_tff" in m                           # legacy fills a domain it lacks


# ── on-disk orphans, the check that silently did nothing for bar stores ─────

def test_orphaned_bar_parquet_is_found_under_the_nested_layout(pf, tmp_path, capsys):
    """The regression this rewrite exists for: bars live at
    bars/<domain>/<vendor>/, so a flat glob finds nothing and every bar store
    reports clean no matter what a mirror would delete."""
    src = make_bar_store(tmp_path / "src")
    dest = make_bar_store(tmp_path / "dest")
    d = dest / "bars" / "futures" / "norgate"
    (d / "CL_backadj.parquet").write_bytes(b"")     # on disk, not in either manifest

    assert pf.main([str(src), str(dest)]) == 1
    out = capsys.readouterr().out
    assert "bars/: 1 parquet files exist only on DEST" in out
    assert "futures/norgate/CL_backadj.parquet" in out


def test_same_name_under_different_vendors_is_not_an_orphan(pf, tmp_path):
    """The vendor is part of the key, so norgate/ES_backadj and databento/ES_backadj
    are two files — which is exactly why the 2026-07-26 collision cannot recur in
    this layout. If the key were the bare filename they would cancel out and a real
    orphan would be hidden."""
    src = make_bar_store(tmp_path / "src",
                         entries=(("futures", "norgate", "ES_backadj"),))
    dest = make_bar_store(tmp_path / "dest",
                          entries=(("futures", "databento", "ES_backadj"),))
    files = pf.data_files(dest, pf.MARKETDATA)
    assert files["bars"] == {"futures/databento/ES_backadj.parquet"}
    assert pf.main([str(src), str(dest)]) == 1      # genuinely dest-only, refused


def test_matching_bar_stores_are_safe(pf, tmp_path, capsys):
    src = make_bar_store(tmp_path / "src")
    dest = make_bar_store(tmp_path / "dest")
    assert pf.main([str(src), str(dest)]) == 0
    assert "SAFE" in capsys.readouterr().out


# ── cross-layout comparison is refused, not guessed ─────────────────────────

def test_comparing_a_cot_store_with_a_bar_store_refuses(pf, tmp_path, capsys):
    """Two swapped paths is far likelier than an intention, and the mirror it would
    green-light deletes the whole destination."""
    cot, bars = make_cot_store(tmp_path / "cot"), make_bar_store(tmp_path / "bars")
    assert pf.main([str(cot), str(bars)]) == 2      # not 0 (safe) and not 1 (refused)
    out = capsys.readouterr().out
    assert "CANNOT JUDGE" in out
    assert "cotdata" in out and "marketdata" in out


# ── the original checks still hold ──────────────────────────────────────────

def test_dest_only_manifest_entries_are_refused(pf, tmp_path, capsys):
    src = make_cot_store(tmp_path / "src", entries=("ES_13874A",))
    dest = make_cot_store(tmp_path / "dest", entries=("ES_13874A", "CL_067651"))
    assert pf.main([str(src), str(dest)]) == 1
    assert "exist only on DEST" in capsys.readouterr().out


def test_same_key_different_source_is_flagged(pf, tmp_path, capsys):
    """The collision with no visible symptom. In a bar store only the single-table
    domains can still do this — the vendor is in the path for bars."""
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    for root, vendor in ((src, "norgate"), (dest, "databento")):
        make_bar_store(root)
        m = json.loads((root / "manifest.json").read_text())
        m["metadata"] = {"contract_specs": _entry(vendor)}
        (root / "manifest.json").write_text(json.dumps(m))

    assert pf.main([str(src), str(dest)]) == 1
    out = capsys.readouterr().out
    assert "DIFFERENT sources on each side" in out
    assert "metadata/contract_specs: src=norgate dest=databento" in out


def test_bad_arguments_and_missing_directories_exit_2(pf, tmp_path):
    assert pf.main([]) == 2
    assert pf.main([str(tmp_path), str(tmp_path / "nope")]) == 2
