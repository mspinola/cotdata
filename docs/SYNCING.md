# Syncing the store between machines

The store is a producer/consumer contract: one machine writes, others only read. When
the producer and the consumers are different machines, something has to move the files.

This page is about **what** to move and what to leave behind. The transport is the easy
part and comes last.

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
| `_cache/` | **NO** | databento provider cache, producer-internal, rebuildable |
| `_raw/` | **NO** | databento append-only raw store, producer-internal |
| `citpy/` | **NO** | written by **cotmetrics**, not cotdata (see below) |
| `manifest.json` | **NO** | legacy aggregate, nothing writes it (see below) |

On one real store those exclusions dropped the payload from 270 MB to about 82 MB, and
two of them are correctness issues rather than savings.

### `citpy/` is not cotdata's

`cotmetrics` writes it, defaulting to `$COTDATA_STORE/citpy` when `COTMETRICS_CITPY` is
unset. It is derived on the *consumer* machine and does not exist on a pure producer. A
mirroring sync would therefore either delete it or overwrite locally-derived output with
nothing. Exclude it, or point `COTMETRICS_CITPY` somewhere outside the store.

### `manifest.json` is legacy

Nothing writes it any more (see ADR-0007). It held both producer halves in one file,
which is exactly the shape a file sync resolves last-writer-wins: a producer pushing an
aggregate containing only its own half would silently drop the other half's entries on
arrival. The per-half files under `manifests/` are disjoint and merge correctly.

Run `cotdata-update --migrate-manifests` once per store, then delete `manifest.json`.

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
