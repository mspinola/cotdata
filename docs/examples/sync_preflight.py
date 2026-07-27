"""Refuse a destructive store sync before it runs.

    python sync_preflight.py SRC_STORE DEST_STORE

`SRC` is the producer, `DEST` the replica about to be mirrored onto. Answers one
question: **would a `--delete` mirror destroy something DEST owns?**

Exit 0 = safe. Exit 1 = do not sync, with the reason.

Why this exists
---------------
`docs/SYNCING.md` opens with "prefer one producer" because a file-level mirror
resolves every shared path last-writer-wins. That guidance is easy to nod at and
hard to check by eye, and the failure is silent: rsync `--delete` and `robocopy
/MIR` remove destination files the source lacks, and they do it quietly.

The case this was written for, found on a live pair on 2026-07-26: a Mac store
holding 94 Norgate-sourced price entries, while a 25-hour databento ingest ran
against the same store. cotdata's price path is `prices/<SYM>_<adj>.parquet` with
**no source component**, so once Stage 2 built those 41 databento symbols they would
have landed on the same paths Norgate already owned, and all 41 were a subset of
Norgate's 47. Mirroring from the Norgate producer would have deleted or overwritten
them. Neither store was wrong; the topology was.

Reads only. Never writes, never deletes, never syncs.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HALVES = ("cot", "prices")
# Directories a producer owns. Anything else under the root is either excluded by
# the sync (see SYNCING.md) or consumer-owned and out of scope here.
DATA_DIRS = ("prices", "metadata", "cot_legacy", "cot_disagg", "cot_tff")


def load_manifest(root: Path) -> dict:
    """Merge the per-half manifests, falling back to the legacy aggregate per DOMAIN.

    Per domain, not per half: a store can be part migrated, and a half file existing
    does not mean every domain in it is present.
    """
    merged: dict = {}
    legacy_path = root / "manifest.json"
    legacy = json.loads(legacy_path.read_text()) if legacy_path.exists() else {}
    for half in HALVES:
        p = root / "manifests" / f"{half}.json"
        if p.exists():
            for domain, entries in json.loads(p.read_text()).items():
                if isinstance(entries, dict):
                    merged.setdefault(domain, {}).update(entries)
    for domain, entries in legacy.items():
        if isinstance(entries, dict) and domain not in merged:
            merged[domain] = entries
    return merged


def sources(manifest: dict) -> dict:
    """{(domain, key): source} across every domain."""
    return {(d, k): (e or {}).get("source")
            for d, entries in manifest.items() if isinstance(entries, dict)
            for k, e in entries.items()}


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[2].strip())
        return 2
    src, dest = Path(argv[0]).expanduser(), Path(argv[1]).expanduser()
    for label, root in (("SRC", src), ("DEST", dest)):
        if not root.is_dir():
            print(f"{label} is not a directory: {root}")
            return 2

    s_man, d_man = load_manifest(src), load_manifest(dest)
    s_src, d_src = sources(s_man), sources(d_man)
    problems = []

    print(f"SRC  {src}")
    print(f"DEST {dest}\n")
    print(f"{'domain':<12} {'src':>6} {'dest':>6}  {'dest-only':>9}  sources")
    for domain in sorted(set(s_man) | set(d_man)):
        s_e = s_man.get(domain, {}) if isinstance(s_man.get(domain), dict) else {}
        d_e = d_man.get(domain, {}) if isinstance(d_man.get(domain), dict) else {}
        only = set(d_e) - set(s_e)
        src_names = {v for (dom, _), v in d_src.items() if dom == domain and v}
        print(f"{domain:<12} {len(s_e):>6} {len(d_e):>6}  {len(only):>9}  "
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
        problems.append(
            (f"{len(clashes)} entries are produced by DIFFERENT sources on each side. "
             f"cotdata's price path carries no source component, so these collide on "
             f"the same file and the sync resolves them last-writer-wins",
             [f"{d}/{k}: src={s_src[(d, k)]} dest={d_src[(d, k)]}" for d, k in clashes[:8]]))

    # Files on disk with no manifest entry: a mirror deletes these too.
    for d in DATA_DIRS:
        sp, dp = src / d, dest / d
        if dp.is_dir():
            s_files = {f.name for f in sp.glob("*.parquet")} if sp.is_dir() else set()
            d_files = {f.name for f in dp.glob("*.parquet")}
            only = d_files - s_files
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
