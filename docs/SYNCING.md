# Syncing the store between machines

The store is a producer/consumer contract: one machine writes, others only read. When
the producer and the consumers are different machines, something has to move the files.

This page is about **what** to move and what to leave behind. The transport is the easy
part and comes last.

> [!IMPORTANT]
> **Two stores since ADR-0007, and they do not share an exclusion list.** Every bar —
> Norgate, databento and Yahoo alike — moved to
> [`crucible-marketdata`](https://pypi.org/project/crucible-marketdata/) and its own
> `$MARKETDATA_STORE`. The Windows box produces both and pushes both to the same replicas,
> so the topology, transports and auth gotchas below are unchanged. What is **not**
> unchanged is what you exclude:
>
> | | COT store | bar store |
> |---|---|---|
> | live bookkeeping | `manifests/<half>.json` | `manifest.json` (root) |
> | root `manifest.json` | dead legacy aggregate — **exclude** | the only index — **must be carried** |
> | `_cache/` | cotdata's CFTC zip cache — exclude | does not exist |
> | `citpy/` | consumer-owned — exclude | does not exist |
> | `_raw/` | pre-ADR-0007 leftover — exclude | databento's PAID bronze — exclude |
> | `vintage/` | irreplaceable, carry to the Mac | does not exist |
>
> The `manifest.json` row is the trap. Both `robocopy /XF` and `rsync --exclude` match by
> **name at any depth**, so carrying the COT store's exclusion list over to the bar store
> strips the bar store's whole index in transit and delivers a replica full of parquet it
> cannot enumerate. That is the same failure this page already documents for
> `vintage/snapshots.json`, one directory over. Mirror the two stores in **separate passes**;
> do not point one `robocopy /MIR` at a shared parent folder.
>
> Where a command names `cotdata-prices --prices`, read
> `marketdata-update --bars --domain futures --require-final`.
>
> A cotdata store built before the move still has `prices/`, `metadata/` and `_raw/` sitting
> in it. Nothing writes them any more; leaving them is harmless and deleting them is safe
> once the bar store is confirmed synced.

## This deployment: one Norgate producer, two replicas

A single Windows server is the only producer (Norgate prices, CFTC COT). It feeds two
read-only replicas: the Mac research store over SMB, and a remote Linux dash server over
SSH. Both are Norgate, so the dashboard shows exactly what research shows. databento was
built and validated as a provider-different alternative but is wired to neither consumer;
see the dash section below and [`databento_norgate_parity.md`](databento_norgate_parity.md)
(ADR-0006, Accepted).

### Research store: standalone Windows server to Mac over SMB

The Mac's `~/code/cotdata_store`, read on the Mac for research. Its producer used to be a
Windows VM hosted inside the Mac, with the store on a folder shared between guest and host,
so nothing had to move. It is now a **standalone physical Windows server**, and that shared
folder was deliberately not carried over, so a real network sync replaced it.

- **Producer:** the Windows server (Norgate prices, CFTC COT), one-directional.
- **Consumer:** the Mac, read-only.
- **Transport:** two `robocopy /MIR` passes, one per store
  ([`examples/windows/sync-store.cmd`](examples/windows/sync-store.cmd)), pushing to SMB
  shares the Mac exports for `~/code/cotdata_store` and `~/code/marketdata_store`, reached
  from the server as `\\<mac>\cotdata_store` and `\\<mac>\marketdata_store` (use the Mac's
  LAN IP if its name will not resolve from a headless server). The second pass runs even
  when the first fails: the stores are independent, so aborting early would let a COT
  hiccup silently stop bars reaching the Mac for as long as it lasted.
- **Trigger:** chained onto the end of the producer task behind an `errorlevel` guard, so
  it fires only after a successful run rather than on a timer (a deferred
  `--require-final` bar run exits non-zero and is skipped).
- **Auth gotcha:** a task set to run whether the user is logged on or not has no cached
  SMB credentials in its non-interactive session, so the UNC write fails access-denied
  even though it works logged on. Store one on the server with `cmdkey /add`, or chain the
  sync onto the logged-on prices task instead.

### Dash store: Windows Norgate to a remote Linux server over SSH

The public dash (cot-analyzer) reads a Norgate store synced from the same Windows producer,
not a databento store. databento was built and validated as a provider-different
alternative, but its monthly-commodity roll calendar produces a materially different series
([`databento_norgate_parity.md`](databento_norgate_parity.md)), so the server stays on
synced Norgate: the dashboard then shows exactly what local research shows, at lower
maintenance than a per-symbol roll-rule table.

- **Producer:** the same Windows server (Norgate prices, CFTC COT).
- **Consumer:** a remote Ubuntu VPS, read-only.
- **Transport:** an `rsync` push over SSH, chained onto the producer task, using a packaged
  rsync on Windows (cwRsync or WSL). robocopy cannot speak SSH, and SMB must never be
  exposed over the internet, so the Mac's SMB path does not carry here. See
  [`examples/windows/push-to-server.cmd`](examples/windows/push-to-server.cmd), which
  pushes both stores to two remote paths. Per store the exclusions match the Mac push —
  `_cache/`, `_raw/`, `citpy/`, `manifest.json`, `*.tmp` for the COT store; `_raw/` and
  `*.tmp` only for the bar store, whose root `manifest.json` is pushed last on its own
  line rather than excluded. Either way the PAID databento bronze under `_raw/databento/`
  never leaves the Windows box.
- **Auth:** key-based SSH only. A scheduled task cannot type a passphrase, so use a
  dedicated key with `ssh -o BatchMode=yes`, never a password prompt.
- **cwRsync gotcha:** a Cygwin rsync (what `choco install rsync` gives you) must drive the
  ssh that *ships with it*, not the native Windows OpenSSH. Native ssh corrupts rsync's
  binary stream and fails with `connection unexpectedly closed (0 bytes received so far)`,
  even though a plain `ssh host echo ok` works fine. That Cygwin ssh also has no HOME, so
  give it an explicit writable `-o UserKnownHostsFile=`, and use cygdrive (`/cygdrive/c/…`)
  paths throughout, including the key. The example script wires all three.

**Provider cutover (one-time, and now historical).** The server previously held a
databento-built store, so its `prices/` and `manifests/prices.json` carry databento data
under the very keys the Norgate push writes. `sync_preflight.py` will (correctly) refuse: it
sees the same `prices/<SYM>_<adj>.parquet` produced by a different source on each side, the
94-collision case from "Check before you mirror". That refusal is the tool working, not a
misconfiguration. **ADR-0007 removed the possibility**: the vendor is a directory in
marketdata's layout (`bars/futures/norgate/` beside `bars/futures/databento/`), so two
vendors cannot contend for one path at all. Resolve the legacy case once by clearing the
server's `prices/` and `manifests/`
before the first Norgate push, so the store is rebuilt as a clean Norgate replica; every
push after that is a same-source mirror with nothing to collide.

**citpy on the server.** cot-analyzer's `run-local.sh` sets
`COTMETRICS_CITPY=$COTDATA_STORE/citpy`, but that is not Norgate producer output: it is
reproducible output of the `~/code/citpy` tool, read via `COTMETRICS_CITPY`. The push
excludes `citpy/`, which keeps it off the Windows producer's books and stops `--delete`
from removing it on the server. Regenerate it on the server, or point `COTMETRICS_CITPY`
at the citpy tool's own output directory, rather than mirroring it through this store.

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
| bars | `marketdata-update --bars --domain futures --require-final` | Run only when user is logged on (NDU needs a desktop session) |
| COT | `cotdata-cot --cot-all` | Run whether user is logged in or not |

The bar task writes `$MARKETDATA_STORE` and the COT task writes `$COTDATA_STORE`. Two
producers writing two *disjoint* stores is not the two-producer hazard above — that one is
about two producers writing the *same* files.

## What NOT to sync

This is the part that matters, and on a real store it is most of the bytes.

**One table per store.** They are not interchangeable — see the `manifest.json` rows.

`$COTDATA_STORE`:

| Entry | Sync? | Why |
|---|---|---|
| `cot_legacy/`, `cot_disagg/`, `cot_tff/` | **yes** | the data |
| `manifests/` | **yes** | per-half bookkeeping, disjoint and mergeable |
| `vintage/` | **yes**, to the Mac | irreplaceable; see below for why the dash skips it |
| `status.json` | yes | the producer's own view, and the freshness signal a replica check reads |
| `manifest.json` | **NO** | legacy aggregate, nothing writes it (see below) |
| `_cache/` | **NO** | cotdata's own download cache of CFTC source zips, producer-internal, free to rebuild |
| `prices/`, `metadata/` | **NO** (legacy) | pre-ADR-0007 leftovers; bars and specs live in the bar store now |
| `_raw/` | **NO** (legacy) | pre-ADR-0007 leftover, databento's PAID raw store (marketdata's now) |
| anything a consumer added by hand | **NO** | no producer creates it, so a mirror deletes it (see below) |

`$MARKETDATA_STORE`:

| Entry | Sync? | Why |
|---|---|---|
| `bars/` | **yes** | the data — `bars/<domain>/<source>/<symbol>_<tier>.parquet` |
| `metadata/` | **yes** | contract specs |
| `manifest.json` | **YES** | the bar store's ONLY index. Not the COT store's legacy file — carry it, and carry it LAST |
| `_raw/` | **NO** | databento's append-only PAID raw store, producer-internal |
| `_cache/`, `citpy/`, `vintage/` | n/a | the bar store has none of these |

On one real store the `_cache/` and `_raw/` exclusions dropped the payload from 270 MB to
about 82 MB. The `manifest.json` rows are correctness rather than savings, and they point
opposite ways: excluding it is right for one store and destroys the other.

### `_cache/` holds source archives, not derived data

cotdata's own CFTC providers write it: `_cache/cot_legacy`, `_cache/cot_disagg` and
`_cache/cot_tff` hold the downloaded year zips (`dea_fut_xls_2004.zip` and so on) that
`--cot-*` HEAD-checks to decide whether anything changed. (A store built before ADR-0007
also has a `_cache/databento` from when that provider lived here; nothing writes it now.)

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

### `manifest.json` is legacy — **in the COT store only**

Nothing writes cotdata's aggregate any more (see ADR-0007). It held both producer halves
in one file, which is exactly the shape a file sync resolves last-writer-wins: a producer
pushing an aggregate containing only its own half would silently drop the other half's
entries on arrival. The per-half files under `manifests/` are disjoint and merge correctly.

Run `cotdata-update --migrate-manifests` once per store, then delete `manifest.json`.

**The bar store's `manifest.json` is the opposite of legacy.** marketdata keeps one live
manifest at its root and has no `manifests/` directory at all. Excluding it — by copying
the COT store's exclusion list, which is the obvious thing to do — delivers a replica
holding every parquet and no index. It is a quiet failure: the files are all there, so
disk usage and a directory listing both look right, and only a read notices. Both
transports match exclusions by **name at any depth**, so there is no `/manifests/` prefix
to make the rule safe. Two stores, two passes, two lists.

### `vintage/` is irreplaceable, so where it is WRITTEN matters

The vintage tree (`vintage/raw/`, `observations/`, `revisions/`, `snapshots.json`) records
CFTC data *as published*. CFTC serves current state only and there is no vintage archive,
so a deleted vintage snapshot can never be re-fetched. Treat it as write-once.

**Capture on the producer, never on a replica.** Vintage capture fetches from CFTC, so it
is a producer action and belongs beside the COT half (`run-vintage.cmd`, chained after
`run-cot.cmd`, daily). Written on the producer it propagates outward like any other store
content. Written on a **replica** it is destroyed by the next `/MIR` or `--delete` pass,
for the same reason `citpy` is (below): the source has no such directory, so the mirror
removes it. `citpy` is regenerable; vintage data is not.

If a replica genuinely must capture, point `COTDATA_VINTAGE_ROOT` at a path **outside**
the mirrored store. That box then holds the only copy, so give it its own backup.

Per replica in this deployment:

| Target | Carries `vintage/`? | Why |
|---|---|---|
| Mac (research) | **Yes, in full** | Natural second copy of irreplaceable bytes, ~1 GB/yr, and research may query revisions |
| Linux dash VPS | **No** | cot-analyzer reads bars and COT only; it would carry ~1 GB/yr of archives it never opens |

**Naming gotcha, already handled:** the vintage provenance index is `snapshots.json`, not
`manifest.json`. Both sync scripts exclude `manifest.json` *unanchored* (robocopy `/XF`
and rsync `--exclude` both match by name at any depth), so a `vintage/manifest.json` would
have been stripped in transit and the replica would receive raw archives with no index.
Do not rename it back.

Exclude `*.part` alongside `*.tmp`: raw downloads land via a `.part` file plus an atomic
replace, and a sync running mid-capture must not carry the partial.

## Check before you mirror

`--delete` and `/MIR` are silent when they destroy something. Run the preflight first:

```bash
python docs/examples/sync_preflight.py SRC_STORE DEST_STORE
```

Exit 0 means DEST holds nothing SRC does not produce. Exit 1 lists what a mirror would
remove and refuses. It reads only.

Run it **once per pair** — `$COTDATA_STORE` against its target, then `$MARKETDATA_STORE`
against its own. It detects which layout each store is (a bar store has `bars/` and a live
root `manifest.json`; a COT store has `manifests/`) and prints the verdict. Handing it one
of each exits 2 with `CANNOT JUDGE` rather than guessing: that pairing is far more likely
to be two paths swapped than an intention, and the mirror it would otherwise green-light
deletes the entire destination.

It checks two things the eye does not. **Entries only on DEST**, which a mirror deletes.
And **the same key produced by different sources on each side**: cotdata's price path was
`prices/<SYM>_<adj>.parquet` with no source component, so a Norgate `ES_backadj` and a
databento `ES_backadj` were the same file, and a sync resolved them last-writer-wins with
nothing in the output to say so. In the bar store that particular collision cannot happen
— the vendor is a directory — but the single-table domains (`metadata/contract_specs`)
still carry no source in their path, so the check runs there too.

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

Both example transports do this per store: the COT push holds back `manifests/`, and the
bar push holds back the root `manifest.json`, each sent on a final pass **without**
`--delete` so the mirror cannot remove the replica's copy in the window before the new one
lands.

In practice this is a nicety rather than a hazard, because `get_cot`
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

On the replica, once per store:

```bash
cotdata-update --check         # reads $COTDATA_STORE
marketdata-update --check      # reads $MARKETDATA_STORE; exits 1 on an empty store
```

Compare `newest data` and `last write (UTC)` against the producer's output. The lag
column is measured on write time, so an entry the producer skipped shows up as behind
even when its data looks fine.

If the replica shows entries the producer does not have, the sync is additive rather than
mirroring and stale files are accumulating. If it shows *fewer*, the sync has not
completed or an exclusion is too broad.

To automate this across replicas, [`examples/mac/verify-replicas.sh`](examples/mac/verify-replicas.sh)
checks a local store and a remote one (over SSH) in one pass — **four checks, two stores
per replica** — and exits non-zero naming every laggard. Wire it to launchd/cron a little
after the producer's run.

**The two stores need two different freshness signals**, and the difference is not a
detail you can round off:

- **COT.** cotdata rewrites `status.json` on *every* run, new data or not, so "mtime is
  today" is a clean did-this-replica-update-today test.
- **Bars.** marketdata has no `status.json`, and it rewrites `manifest.json` only when a
  bar or a spec is actually written. A weekend, a holiday, or a deferred `--require-final`
  run legitimately writes nothing, so demanding "today" here fails every Saturday — and an
  alarm that cries wolf weekly stops being read by the second month. The script uses a
  staleness **window** instead (`BAR_MAX_AGE_DAYS`, default 4: Friday's write is still
  fresh on Tuesday).

Both transports preserve timestamps, so a replica's mtime is the *producer's* write time
rather than the copy time — which is what makes either test mean anything, and what lets
the script also compare the **two replicas against each other**. A matching producer run
lands the same mtime on both; different mtimes mean one push is behind, and inside the
staleness window neither replica looks wrong on its own.
