#!/usr/bin/env bash
# Pull BOTH stores from the producer machine onto a read-only replica.
# The consumer decides when to fetch, so this is the alternative to the producer
# pushing (see examples/windows/sync-store.cmd).
#
# Since ADR-0007 the producer writes two stores and this script pulls both:
#   COT store  CFTC positioning  (cotdata-cot --cot-all)
#   bar store  bars + specs      (marketdata-update --bars / --metadata)
# Separate passes with separate exclusions. They disagree about manifest.json --
# legacy in one, the only index in the other -- so one list cannot serve both.
#
# Copy this next to your launchd/cron config and overwrite the markers below:
#   REPLACE_WITH_PRODUCER_HOST           = ssh target  e.g. matt@windows-box
#   REPLACE_WITH_REMOTE_COTDATA_STORE    = COT store ON that host  e.g. /c/Users/matt/cotdata_store
#   REPLACE_WITH_LOCAL_COTDATA_STORE     = COT store on THIS mac   e.g. /Users/you/code/cotdata_store
#   REPLACE_WITH_REMOTE_MARKETDATA_STORE = bar store ON that host  e.g. /c/Users/matt/code/marketdata_store
#   REPLACE_WITH_LOCAL_MARKETDATA_STORE  = bar store on THIS mac   e.g. /Users/you/code/marketdata_store
# The marker names match verify-replicas.sh deliberately, and none is a prefix of
# another: a find-and-replace over the shorter name would otherwise mangle the longer.
# (Plain-text markers, not angle-bracket placeholders: an unedited <...> would be
# read as a shell redirection.)
#
# rsync must exist on BOTH ends. On Windows that means WSL or a packaged rsync. With
# only OpenSSH, `scp -r` works and the payload is small, but you lose deletion
# handling. See docs/SYNCING.md.
#
# Note there is no `set -e`. Both stores are pulled even when the first fails: they
# are independent, and aborting early would let a COT hiccup silently stop bars
# reaching this Mac for as long as it lasted. The exit code below still reports it.
set -uo pipefail

HOST="REPLACE_WITH_PRODUCER_HOST"
COT_SRC="$HOST:REPLACE_WITH_REMOTE_COTDATA_STORE"
COT_DEST="REPLACE_WITH_LOCAL_COTDATA_STORE"
BAR_SRC="$HOST:REPLACE_WITH_REMOTE_MARKETDATA_STORE"
BAR_DEST="REPLACE_WITH_LOCAL_MARKETDATA_STORE"

rc=0

# ── COT store ───────────────────────────────────────────────────────────────
# What each exclusion is for:
#   _cache         cotdata's cache of downloaded CFTC zips: producer-internal and
#                  free to rebuild, and most of the bytes.
#   _raw           pre-ADR-0007 leftover (databento's paid bronze store, now
#                  marketdata's). Kept so a store built before the move still
#                  excludes it.
#   citpy          consumer-owned, not written by any producer, so --delete removes
#                  it and no producer run brings it back. Kept as a backstop: such
#                  files belong outside the store. See docs/SYNCING.md.
#   manifest.json  legacy aggregate, nothing writes it, and it is the one file a
#                  sync resolves last-writer-wins across both producer halves
COT_EXCLUDES=(
  --exclude '_cache/'
  --exclude '_raw/'
  --exclude 'citpy/'
  --exclude 'manifest.json'
)

# Two passes so a manifest never arrives before the data it describes. Harmless if
# reversed (readers open parquet directly), but free to get right.
rsync -az --delete "${COT_EXCLUDES[@]}" --exclude 'manifests/' "$COT_SRC/" "$COT_DEST/" || rc=$?
rsync -az "$COT_SRC/manifests/" "$COT_DEST/manifests/" || rc=$?

# ── bar store ───────────────────────────────────────────────────────────────
# A DIFFERENT list, and the difference is load-bearing:
#
#   manifest.json is the bar store's ONLY index -- marketdata keeps one file at the
#   store root, not a manifests/ directory. It is held out of the --delete pass so
#   the local copy is not removed before the new one lands, then pulled on its own
#   line. Reusing COT_EXCLUDES here would have excluded it outright, leaving a bar
#   store this Mac cannot enumerate. rsync --exclude matches by NAME AT ANY DEPTH,
#   the same trap docs/SYNCING.md documents for vintage/snapshots.json.
#
#   _cache and citpy are absent from the bar store: no marketdata provider writes a
#   download cache, and citpy is a cotdata-store consumer artefact. _raw IS
#   excluded -- databento's append-only PAID raw store
#   ($MARKETDATA_DATABENTO_RAW, else _raw/databento under the bar store) -- because
#   a replica has no use for it and re-fetching costs money (ADR-0006).
BAR_EXCLUDES=(
  --exclude '_raw/'
)

rsync -az --delete "${BAR_EXCLUDES[@]}" --exclude 'manifest.json' "$BAR_SRC/" "$BAR_DEST/" || rc=$?
rsync -az "$BAR_SRC/manifest.json" "$BAR_DEST/manifest.json" || rc=$?

# Confirm what landed, per store. Compare against the producer's own --check
# output: cotdata's lag column is measured on WRITE time, so an entry the producer
# skipped shows as behind even when its data looks fine.
COTDATA_STORE="$COT_DEST" cotdata-update --check || rc=$?
echo
# `marketdata-update --check` exits 1 on an EMPTY store, which is the failure this
# whole change exists to catch: bars that never arrive at all.
MARKETDATA_STORE="$BAR_DEST" marketdata-update --check || rc=$?

exit "$rc"
