"""Is it safe to delete the legacy manifest.json on this store?

    python check_manifest_migration.py

Answers one question: does every DOMAIN in the legacy aggregate also appear in the
per-half files? If yes, the legacy file is redundant and can go. If no, deleting it
loses those entries, because `load_manifest` falls back to it per domain.

The presence of `manifests/` is NOT the test. A store can be half migrated: found on
a real store where `manifests/prices.json` held `prices` but not `metadata`, because
the price producer had run on the new code and the metadata producer had not.

Reads only. Deletes nothing.
"""
import json
import sys

from cotdata import config, store


def main() -> int:
    legacy_path = config.manifest_path()
    if not legacy_path.exists():
        print(f"No legacy manifest at {legacy_path}. Nothing to delete.")
        return 0

    legacy = json.loads(legacy_path.read_text())
    covered = {}
    for half in store.HALVES:
        p = config.manifest_path_for(half)
        if not p.exists():
            print(f"  missing: manifests/{half}.json")
            continue
        for domain, entries in json.loads(p.read_text()).items():
            if isinstance(entries, dict):
                covered.setdefault(domain, set()).update(entries)

    problems = []
    print(f"legacy {legacy_path}")
    for domain, entries in sorted(legacy.items()):
        if not isinstance(entries, dict):
            continue
        have = covered.get(domain, set())
        missing = set(entries) - have
        mark = "ok" if not missing else f"MISSING {len(missing)}"
        print(f"  {domain:<12} {len(entries):>4} entries  ->  {mark}")
        if missing:
            problems.append((domain, sorted(missing)))

    print()
    if problems:
        print("NOT SAFE TO DELETE. These would be lost:")
        for domain, names in problems:
            print(f"  {domain}: {', '.join(names[:8])}"
                  + (f", ... (+{len(names) - 8})" if len(names) > 8 else ""))
        print("\nRun `cotdata-update --migrate-manifests` first, then re-run this.")
        return 1

    print("SAFE TO DELETE: every legacy domain and entry is covered by manifests/.")
    print("Back it up first anyway, then remove manifest.json.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
