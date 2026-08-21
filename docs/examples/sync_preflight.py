"""Refuse a destructive store sync before it runs.

    python sync_preflight.py SRC_STORE DEST_STORE

`SRC` is the producer, `DEST` the replica about to be mirrored onto. Answers one
question: **would a `--delete` mirror destroy something DEST owns?**

Exit 0 = safe. Exit 1 = do not sync, with the reason. Exit 2 = could not judge.

Works on either store, and tells them apart
-------------------------------------------
Since ADR-0007 this deployment mirrors two stores with two different shapes, and
the difference is not cosmetic:

* a **cotdata** store keeps per-half bookkeeping in ``manifests/<half>.json`` and
  its root ``manifest.json`` is a dead legacy aggregate;
* a **marketdata** store keeps one live ``manifest.json`` at the root, and its
  data lives under ``bars/<domain>/<source>/``.

Reading a marketdata store with cotdata's rules half-works, which is the bad kind
of wrong: the manifest loads (via the legacy fallback) so the summary looks
plausible, while the on-disk check never descends into ``bars/`` and therefore
reports zero orphaned files no matter how many there are. So the layout is
detected per store, and comparing one of each is refused rather than guessed at.

Why this exists
---------------
`docs/SYNCING.md` opens with "prefer one producer" because a file-level mirror
resolves every shared path last-writer-wins. That guidance is easy to nod at and
hard to check by eye, and the failure is silent: rsync `--delete` and `robocopy
/MIR` remove destination files the source lacks, and they do it quietly.

The case this was written for, found on a live pair on 2026-07-26: a Mac store
holding 94 Norgate-sourced price entries, while a 25-hour databento ingest ran
against the same store. cotdata's price path was `prices/<SYM>_<adj>.parquet` with
**no source component**, so once Stage 2 built those 41 databento symbols they would
have landed on the same paths Norgate already owned, and all 41 were a subset of
Norgate's 47. Mirroring from the Norgate producer would have deleted or overwritten
them. Neither store was wrong; the topology was.

That exact collision cannot recur in a marketdata store — the vendor is a
directory (`bars/futures/norgate/` beside `bars/futures/databento/`), so two
vendors cannot contend for one path. The check still runs there, because the
single-table domains (`metadata/contract_specs.parquet`) have no source in their
path and can still collide.

Reads only. Never writes, never deletes, never syncs.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

COTDATA, MARKETDATA = "cotdata", "marketdata"

COT_HALVES = ("cot", "prices")
# Directories a cotdata producer owns. Anything else under the root is either
# excluded by the sync (see SYNCING.md) or consumer-owned and out of scope here.
# `prices` and `metadata` are pre-ADR-0007 leftovers, still listed so a store that
# has not been cleaned up yet is still checked rather than silently skipped.
COT_DATA_DIRS = ("prices", "metadata", "cot_legacy", "cot_disagg", "cot_tff")


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def detect_layout(root: Path) -> str:
    """Which package produced this store.

    Structural evidence first (a directory that only one layout has), manifest
    keys second, and cotdata as the default so a store that predates the split
    keeps its old reading.
    """
    if (root / "bars").is_dir():
        return MARKETDATA
    if (root / "manifests").is_dir():
        return COTDATA
    return MARKETDATA if "bars" in _read_json(root / "manifest.json") else COTDATA


def load_manifest(root: Path, layout: str) -> dict:
    """Domain -> {entry: record}, with the non-dict store-level flags dropped.

    marketdata keeps one manifest and no halves. cotdata merges the per-half files
    and falls back to the legacy aggregate per DOMAIN, not per half: a store can be
    part migrated, and a half file existing does not mean every domain in it is
    present.
    """
    root_json = _read_json(root / "manifest.json")
    if layout == MARKETDATA:
        # schema_version / universe_is_point_in_time sit beside the domains.
        return {d: e for d, e in root_json.items() if isinstance(e, dict)}

    merged: dict = {}
    for half in COT_HALVES:
        for domain, entries in _read_json(root / "manifests" / f"{half}.json").items():
            if isinstance(entries, dict):
                merged.setdefault(domain, {}).update(entries)
    for domain, entries in root_json.items():
        if isinstance(entries, dict) and domain not in merged:
            merged[domain] = entries
    return merged


def data_files(root: Path, layout: str) -> dict:
    """{directory label: {file key}} for the parquet actually on disk.

    Keys are relative to the directory, so a marketdata bar file is
    ``futures/norgate/ES_backadj.parquet`` — the vendor stays in the key, which is
    what makes a same-name-different-vendor pair two entries here rather than one.
    """
    if layout == MARKETDATA:
        out = {}
        bars = root / "bars"
        if bars.is_dir():
            out["bars"] = {str(f.relative_to(bars)) for f in bars.rglob("*.parquet")}
        meta = root / "metadata"
        if meta.is_dir():
            out["metadata"] = {f.name for f in meta.glob("*.parquet")}
        return out
    return {d: {f.name for f in (root / d).glob("*.parquet")}
            for d in COT_DATA_DIRS if (root / d).is_dir()}


def sources(manifest: dict) -> dict:
    """{(domain, key): source} across every domain."""
    return {(d, k): (e or {}).get("source")
            for d, entries in manifest.items() if isinstance(entries, dict)
            for k, e in entries.items()}


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 2:
        print("usage: python sync_preflight.py SRC_STORE DEST_STORE")
        return 2
    src, dest = Path(argv[0]).expanduser(), Path(argv[1]).expanduser()
    for label, root in (("SRC", src), ("DEST", dest)):
        if not root.is_dir():
            print(f"{label} is not a directory: {root}")
            return 2

    s_layout, d_layout = detect_layout(src), detect_layout(dest)
    print(f"SRC  {src}  [{s_layout}]")
    print(f"DEST {dest}  [{d_layout}]\n")
    if s_layout != d_layout:
        # Not a judgement call to make on the user's behalf: mirroring a COT store
        # onto a bar store would delete the whole of the other one, and it is far
        # more likely to be two swapped paths than an intention.
        print(f"CANNOT JUDGE: SRC is a {s_layout} store and DEST is a {d_layout} "
              f"store. These are different layouts holding different data, and a "
              f"mirror between them would delete everything on DEST.\n"
              f"Since ADR-0007 each store syncs to its OWN target — check the two "
              f"paths, then run this once per pair.")
        return 2

    layout = s_layout
    s_man, d_man = load_manifest(src, layout), load_manifest(dest, layout)
    s_src, d_src = sources(s_man), sources(d_man)
    problems = []

    width = max([len("domain")] + [len(d) for d in set(s_man) | set(d_man)])
    print(f"{'domain':<{width}} {'src':>6} {'dest':>6}  {'dest-only':>9}  sources")
    for domain in sorted(set(s_man) | set(d_man)):
        s_e = s_man.get(domain, {}) if isinstance(s_man.get(domain), dict) else {}
        d_e = d_man.get(domain, {}) if isinstance(d_man.get(domain), dict) else {}
        only = set(d_e) - set(s_e)
        src_names = {v for (dom, _), v in d_src.items() if dom == domain and v}
        print(f"{domain:<{width}} {len(s_e):>6} {len(d_e):>6}  {len(only):>9}  "
              f"{', '.join(sorted(src_names)) or '-'}")
        if only:
            problems.append(
                (f"{domain}: {len(only)} entries exist only on DEST and a --delete "
                 f"mirror would remove them",
                 sorted(only)[:8]))

    # The collision that has no visible symptom: same key, different producer.
    clashes = sorted(k for k in set(s_src) & set(d_src)
                     if s_src[k] and d_src[k] and s_src[k] != d_src[k])
    if clashes:
        where = ("cotdata's price path carries no source component"
                 if layout == COTDATA else
                 "a marketdata single-table domain carries no source component")
        problems.append(
            (f"{len(clashes)} entries are produced by DIFFERENT sources on each side. "
             f"{where}, so these collide on the same file and the sync resolves them "
             f"last-writer-wins",
             [f"{d}/{k}: src={s_src[(d, k)]} dest={d_src[(d, k)]}" for d, k in clashes[:8]]))

    # Files on disk with no counterpart on SRC: a mirror deletes these too.
    s_files, d_files = data_files(src, layout), data_files(dest, layout)
    for d, names in sorted(d_files.items()):
        only = names - s_files.get(d, set())
        if only:
            problems.append((f"{d}/: {len(only)} parquet files exist only on DEST",
                             sorted(only)[:8]))

    print()
    if not problems:
        print("SAFE: DEST holds nothing SRC does not produce. A one-directional "
              "mirror will not destroy anything.")
        return 0

    print("DO NOT SYNC. A --delete mirror would destroy data:\n")
    for msg, sample in problems:
        print(f"  * {msg}")
        for item in sample:
            print(f"      {item}")
        print()
    print("Fix the topology, not the flags. Either give the second producer its own "
          "store, or put the source in the path so the two cannot collide. Excluding "
          "the paths by hand works until someone forgets.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
