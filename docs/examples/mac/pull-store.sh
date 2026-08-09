#!/usr/bin/env bash
# Pull the cotdata store from the producer machine onto a read-only replica.
# The consumer decides when to fetch, so this is the alternative to the producer
# pushing (see examples/windows/sync-store.cmd).
#
# Copy this next to your launchd/cron config and overwrite the markers below:
#   REPLACE_WITH_PRODUCER_HOST = ssh target        e.g. matt@windows-box
#   REPLACE_WITH_REMOTE_STORE  = store ON that host e.g. /c/Users/matt/cotdata_store
#   REPLACE_WITH_LOCAL_STORE   = store on THIS mac  e.g. /Users/you/code/cotdata_store
# (Plain-text markers, not angle-bracket placeholders: an unedited <...> would be
# read as a shell redirection.)
#
# rsync must exist on BOTH ends. On Windows that means WSL or a packaged rsync. With
# only OpenSSH, `scp -r` works and the payload is small, but you lose deletion
# handling. See docs/SYNCING.md.
set -euo pipefail

SRC="REPLACE_WITH_PRODUCER_HOST:REPLACE_WITH_REMOTE_STORE"
DEST="REPLACE_WITH_LOCAL_STORE"

# What each exclusion is for:
#   _cache, _raw   producer-internal, most of the bytes. _cache is cotdata's cache
#                  of downloaded CFTC zips. _raw is a pre-ADR-0007 leftover (databento's
#                  paid bronze store, now marketdata's) — still excluded.
#   citpy          consumer-owned, not written by any producer, so --delete removes
#                  it and no producer run brings it back. Kept as a backstop: such
#                  files belong outside the store. See docs/SYNCING.md.
#   manifest.json  legacy aggregate, nothing writes it, and it is the one file a
#                  sync resolves last-writer-wins across both producer halves
EXCLUDES=(
  --exclude '_cache/'
  --exclude '_raw/'
  --exclude 'citpy/'
  --exclude 'manifest.json'
)

# Two passes so a manifest never arrives before the data it describes. Harmless if
# reversed (readers open parquet directly), but free to get right.
rsync -az --delete "${EXCLUDES[@]}" --exclude 'manifests/' "$SRC/" "$DEST/"
rsync -az "$SRC/manifests/" "$DEST/manifests/"

# Confirm what landed. Compare against the producer's own --check output: the lag
# column is measured on WRITE time, so an entry the producer skipped shows as behind
# even when its data looks fine.
export COTDATA_STORE="$DEST"
cotdata-update --check
