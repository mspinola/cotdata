# Syncing the store between machines

The store is a producer/consumer contract: one machine writes, others only read. When
the producer and the consumers are different machines, something has to move the files.

This page is about **what** to move and what to leave behind. The transport is the easy
part and comes last.

## This deployment: two stores, three machines

Because a store carries a single price half (`manifests/prices.json`) written by one
producer, this deployment keeps **two separate stores** rather than merging price sources.

### Research store: standalone Windows server to Mac over SMB

The Mac's `~/code/cotdata_store`, read on the Mac for research. Its producer used to be a
Windows VM hosted inside the Mac, with the store on a folder shared between guest and host,
so nothing had to move. It is now a **standalone physical Windows server**, and that shared
folder was deliberately not carried over, so a real network sync replaced it.

- **Producer:** the Windows server (Norgate prices, CFTC COT), one-directional.
- **Consumer:** the Mac, read-only, `COTDATA_PRICE_SOURCE` unset (Norgate default).
- **Transport:** `robocopy /MIR` ([`examples/windows/sync-store.cmd`](examples/windows/sync-store.cmd))
  pushing to an SMB share the Mac exports for `~/code/cotdata_store`, reached from the
  server as `\\<mac>\cotdata_store` (use the Mac's LAN IP if its name will not resolve
  from a headless server).
- **Trigger:** chained onto the end of the producer task behind an `errorlevel` guard, so
  it fires only after a successful run rather than on a timer (a deferred
  `--require-final` prices run exits non-zero and is skipped).
- **Auth gotcha:** a task set to run whether the user is logged on or not has no cached
  SMB credentials in its non-interactive session, so the UNC write fails access-denied
  even though it works logged on. Store one on the server with `cmdkey /add`, or chain the
  sync onto the logged-on prices task instead.

### Databento store: Linux server to the public dash

A distinct store on a **Linux server**, running the daily databento updates and serving the
public dash (cot-analyzer with `COTDATA_PRICE_SOURCE=databento`). It does not overlap the
research store: the Mac never reads databento prices.

- **Seeding (one-time):** the slow from-inception databento download was done on the Mac
  and rsync'd to Linux. Transfer **both** `_raw/databento/` and `_cache/databento/`;
  re-fetching either costs databento money, so this is not free to rebuild the way
  `_cache/cot_*` is. (Doing the initial download on Linux directly would have been simpler,
  given how long it takes.)
- **Daily:** Linux runs the databento update from then on.

These two databento directories are producer-internal and are already excluded from the
Windows -> Mac push, so the seed staged on the Mac never interferes with the research sync.
Once the seed is confirmed on Linux and daily updates run there, the Mac may delete its own
`_raw/databento/` and `_cache/databento/`, but not before.

## Prefer one producer

The simplest topology by a wide margin is **one machine writes everything, the rest are
read-only replicas**. Norgate needs Windows, so if you have a Windows box, give it both
jobs and let the sync be strictly one-directional.

That removes an entire class of problem. Two producers writing into two stores that are
later merged by a file sync means every shared file is resolved last-writer-wins, and
whichever side syncs second wins silently.

COT does not need Norgate, so do not make it inherit the Norgate Data Updater's
interactive-session requirement. Give it its own task:

| Task | Command | Task Scheduler "General" |
|---|---|---|
| prices | `cotdata-prices --prices --metadata --require-final` | Run only when user is logged on (NDU needs a desktop session) |
| COT | `cotdata-cot --cot-all` | Run whether user is logged in or not |

## What NOT to sync

This is the part that matters, and on a real store it is most of the bytes.

| Directory | Sync? | Why |
|---|---|---|
| `prices/` | **yes** | the data |
| `cot_legacy/`, `cot_disagg/`, `cot_tff/` | **yes** | the data |
| `metadata/` | **yes** | contract specs |
| `manifests/` | **yes** | per-half bookkeeping |
| `status.json` | yes | the producer's own view, useful on the replica |
| `_cache/` | **NO** | cotdata's own download cache of CFTC source zips, producer-internal, free to rebuild |
| `_raw/` | **NO** | databento append-only raw store, producer-internal |
| anything a consumer added by hand | **NO** | no producer creates it, so a mirror deletes it (see below) |
| `manifest.json` | **NO** | legacy aggregate, nothing writes it (see below) |

On one real store the `_cache/` and `_raw/` exclusions dropped the payload from 270 MB to
about 82 MB. The other two are correctness issues rather than savings.

### `_cache/` holds source archives, not derived data

cotdata's own CFTC providers write it: `_cache/cot_legacy`, `_cache/cot_disagg` and
`_cache/cot_tff` hold the downloaded year zips (`dea_fut_xls_2004.zip` and so on) that
`--cot-*` HEAD-checks to decide whether anything changed. `_cache/databento` is the
equivalent for that provider.

It is producer-internal and **free** to rebuild, since the CFTC download costs nothing.
Do not confuse it with `_raw/`, which is the *paid* databento raw store. Both are
excluded, but for different reasons: `_cache/` because a replica has no use for source
archives, `_raw/` because a replica has no use for them either and re-fetching would cost
money.

### Do not keep consumer-owned files in the store

The store belongs to its producer. Every directory in it is something a producer writes,
which is what makes a one-directional mirror safe: the replica can be reconstructed by
running the producer again.

A consumer that drops its own files in gets neither half of that guarantee. A pure
producer has no such directory, so a mirroring sync **deletes it**, and no producer run
brings it back.

The real case was `citpy/`, a hand-refreshed copy of a separate tool's output, read via
cotmetrics' `COTMETRICS_CITPY`. It was excluded from the sync, which worked, but the
exclusion was the weaker fix on two counts: it had to be remembered in every transport
config, and the copy could silently drift from the tool that produced it. Pointing
`COTMETRICS_CITPY` at that tool's own output directory removed both problems and the
directory left the store.

If you have anything similar, exclude it today and move it out of the store. Then no
future transport, and no colleague configuring one, can reach it.

### `manifest.json` is legacy

Nothing writes it any more (see ADR-0007). It held both producer halves in one file,
which is exactly the shape a file sync resolves last-writer-wins: a producer pushing an
aggregate containing only its own half would silently drop the other half's entries on
arrival. The per-half files under `manifests/` are disjoint and merge correctly.

Run `cotdata-update --migrate-manifests` once per store, then delete `manifest.json`.

## Check before you mirror

`--delete` and `/MIR` are silent when they destroy something. Run the preflight first:

```bash
python docs/examples/sync_preflight.py SRC_STORE DEST_STORE
```

Exit 0 means DEST holds nothing SRC does not produce. Exit 1 lists what a mirror would
remove and refuses. It reads only.

It checks two things the eye does not. **Entries only on DEST**, which a mirror deletes.
And **the same key produced by different sources on each side**: cotdata's price path is
`prices/<SYM>_<adj>.parquet` with no source component, so a Norgate `ES_backadj` and a
databento `ES_backadj` are the same file, and a sync resolves them last-writer-wins with
nothing in the output to say so.

On a real pair on 2026-07-26 that second check found **94 collisions** between a
Norgate-sourced research store and a databento-sourced server store. Neither store was
wrong. They are not mirrors of each other, and the preflight says so before a transport
does something irreversible.

The fix for a refusal is the topology, not the exclusion list. Give each producer its
own store, or put the source in the path. Excluding paths by hand works until someone
forgets, and the forgetting is silent.

### Migration check

Before deleting a legacy `manifest.json`, confirm the per-half files actually cover it:

```bash
python docs/examples/check_manifest_migration.py
```

The presence of `manifests/` is not the test. A store can be part migrated, and
`load_manifest` falls back to the legacy file **per domain**. Found on a real store where
`manifests/prices.json` held `prices` but not `metadata`.

## Ordering: data before manifests

If a manifest arrives before the parquet it describes, a consumer briefly sees an entry
pointing at data that has not landed. The other order is harmless: data present but not
yet announced.

In practice this is a nicety rather than a hazard, because `get_prices` and `get_cot`
read parquet directly and the manifest is status only. But if your transport lets you
control ordering, sync the data directories first and `manifests/` last.

Individual writes are already safe: the store commits every parquet with an atomic
`os.replace`, so a sync copying concurrently sees either the old file or the new one,
never a partial. What a mid-run sync *can* catch is a partially updated **set** of files,
which is why running the sync after the producer task rather than on a timer is better.

## Transports

### Avoid Dropbox and Google Drive

Three specific problems, all of which put junk inside the store:

- **Conflict copies.** Both create `something (conflicted copy).parquet` when they see
  divergence. Those land in the store, where any directory scan treats them as real.
- **Placeholder files.** Files On-Demand and Smart Sync leave non-local stubs.
  `read_parquet` on a stub either stalls fetching or fails, on the machine doing research.
- **Continuous sync.** They will happily replicate the store mid-producer-run.

### Recommended: push after the producer runs

Run the sync as a step immediately after the producer task, so it fires at a
known-consistent moment rather than on a timer that might land mid-run.

See [`examples/windows/sync-store.cmd`](examples/windows/sync-store.cmd), which uses
`robocopy /MIR` with the exclusions above. `robocopy` is built into Windows, is
incremental, and its destination can be a mapped drive, a UNC path, or a local folder
that something else (Syncthing, an rclone remote) watches.

**Watch the exit code.** `robocopy` returns 0-7 for success (1 means files were copied,
3 means copied plus extras) and 8 or above for failure. A wrapper that does not normalise
that will make Task Scheduler report every successful sync as a failure. The example
script handles it.

### Alternative: pull from the consumer

If you would rather the consumer decide when to fetch, see
[`examples/mac/pull-store.sh`](examples/mac/pull-store.sh), which uses `rsync` over SSH.

Note `rsync` needs to exist on **both** ends, so on Windows that means WSL or a packaged
rsync. If you only have OpenSSH, `scp -r` works and the payload is small enough that
non-incremental copying is tolerable, but you lose deletion handling.

### Alternative: Syncthing

Peer-to-peer, no cloud middleman, no placeholder files, handles many-file datasets, and
its conflict handling is explicit rather than silently duplicating. Put the exclusions in
a `.stignore` at the store root. Reasonable if you want something continuous and
unattended, at the cost of a daemon on both machines.

## Verifying a sync worked

On the replica:

```bash
cotdata-update --check
```

Compare `newest data` and `last write (UTC)` against the producer's output. The lag
column is measured on write time, so an entry the producer skipped shows up as behind
even when its data looks fine.

If the replica shows entries the producer does not have, the sync is additive rather than
mirroring and stale files are accumulating. If it shows *fewer*, the sync has not
completed or an exclusion is too broad.
