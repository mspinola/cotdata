#!/usr/bin/env bash
# verify-replicas.sh — confirm the latest producer run reached BOTH replicas and
# BOTH stores: one local (Mac, over SMB) and one remote (dash VPS, over SSH), each
# holding a COT store and a bar store since ADR-0007.
#
# Four checks, not two. Bars moved to their own store with their own sync pass, so
# a COT-only check would pass green while no bar has reached a replica in weeks --
# which is exactly the failure this file was extended to catch.
#
# Run it AFTER the producer's scheduled run, e.g. from launchd/cron.
# Exits 0 on PASS, 1 on FAIL (naming every laggard, not just the first).
#
# ── The two stores need two different freshness signals ─────────────────────
# COT store: cotdata rewrites `status.json` on EVERY run, new data or not. So
#   "status.json mtime is today" is a clean did-this-replica-update-today signal.
# Bar store: marketdata has no status.json, and it rewrites `manifest.json` only
#   when a bar or a spec is actually WRITTEN. A weekend, a holiday, or a deferred
#   `--require-final` run legitimately writes nothing, so demanding "today" here
#   would fail every Saturday. The check is a staleness WINDOW instead
#   (BAR_MAX_AGE_DAYS, default 4 — Friday's write is still fresh on Tuesday).
# Both syncs preserve timestamps (rsync -a, robocopy), so a replica's mtime is the
# PRODUCER's write time, not the copy time. That is what makes either check mean
# anything, and it is what makes the cross-check at the end possible.
#
# Configure by editing the block below or exporting the vars before calling.
# Markers are plain text (not <angle brackets>) so an unedited copy still parses.
set -uo pipefail

# ── config ──────────────────────────────────────────────────────────────────
LOCAL_COT="${LOCAL_COT:-REPLACE_WITH_LOCAL_COTDATA_STORE}"          # e.g. $HOME/code/cotdata_store
LOCAL_BARS="${LOCAL_BARS:-REPLACE_WITH_LOCAL_MARKETDATA_STORE}"     # e.g. $HOME/code/marketdata_store
LOCAL_COT_CHECK="${LOCAL_COT_CHECK:-REPLACE_WITH_LOCAL_COTDATA_UPDATE}"       # .../.venv/bin/cotdata-update
LOCAL_BAR_CHECK="${LOCAL_BAR_CHECK:-REPLACE_WITH_LOCAL_MARKETDATA_UPDATE}"    # .../.venv/bin/marketdata-update
REMOTE="${REMOTE:-REPLACE_WITH_REMOTE_SSH_TARGET}"                             # ssh target, e.g. deploy@dash.example.com
REMOTE_COT="${REMOTE_COT:-REPLACE_WITH_REMOTE_COTDATA_STORE}"       # e.g. /srv/cotdata_store
REMOTE_BARS="${REMOTE_BARS:-REPLACE_WITH_REMOTE_MARKETDATA_STORE}"  # e.g. /srv/marketdata_store
REMOTE_COT_CHECK="${REMOTE_COT_CHECK:-REPLACE_WITH_REMOTE_COTDATA_UPDATE}"    # cotdata-update ON the remote
REMOTE_BAR_CHECK="${REMOTE_BAR_CHECK:-REPLACE_WITH_REMOTE_MARKETDATA_UPDATE}" # marketdata-update ON the remote
BAR_MAX_AGE_DAYS="${BAR_MAX_AGE_DAYS:-4}"                           # see the note above
SSH_KEY="${SSH_KEY:-}"                                              # optional: private key; empty = default key/agent
# ────────────────────────────────────────────────────────────────────────────

TODAY=$(date +%F)
NOW=$(date +%s)
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=8)
[ -n "$SSH_KEY" ] && SSH_OPTS+=(-i "$SSH_KEY")

pass=0; fail=0

# date -r <file> prints the file's mtime on both macOS (BSD) and Linux (GNU).
mtime_local()  { date -r "$1" +%s 2>/dev/null; }
mtime_remote() { ssh "${SSH_OPTS[@]}" "$REMOTE" "date -r '$1' +%s" 2>/dev/null; }

# Format an EPOCH, and note this is a different flag on each platform: BSD reads
# `date -r` as either a file or a seconds count, GNU reads it as a file only and
# wants `date -d @seconds` for the number. This file lives under mac/ but the
# fallback costs one line and stops it breaking the day someone runs it on Linux.
fmt_epoch() { date -r "$1" "${2:-+%F}" 2>/dev/null || date -d "@$1" "${2:-+%F}" 2>/dev/null; }

check_today() {  # $1 = mtime epoch (may be empty), $2 = label
  local when; when=$([ -n "$1" ] && fmt_epoch "$1" || echo "")
  if [ "$when" = "$TODAY" ]; then
    echo "  PASS: $2 status.json written today ($when)"; pass=$((pass + 1))
  else
    echo "  FAIL: $2 status.json last written '${when:-unknown}', not today ($TODAY)"; fail=$((fail + 1))
  fi
}

check_age() {  # $1 = mtime epoch (may be empty), $2 = label
  if [ -z "$1" ]; then
    echo "  FAIL: $2 manifest.json missing or unreadable — no bars have reached this replica"
    fail=$((fail + 1)); return
  fi
  local age_days when
  age_days=$(( (NOW - $1) / 86400 ))
  when=$(fmt_epoch "$1")
  if [ "$age_days" -le "$BAR_MAX_AGE_DAYS" ]; then
    echo "  PASS: $2 manifest.json written $when (${age_days}d old, window ${BAR_MAX_AGE_DAYS}d)"
    pass=$((pass + 1))
  else
    echo "  FAIL: $2 manifest.json written $when — ${age_days}d old, past the ${BAR_MAX_AGE_DAYS}d window"
    fail=$((fail + 1))
  fi
}

echo "=== store replica verification — $TODAY ==="
echo
echo "[local COT]  $LOCAL_COT"
check_today "$(mtime_local "$LOCAL_COT/status.json")" "local COT"
COTDATA_STORE="$LOCAL_COT" "$LOCAL_COT_CHECK" --check 2>&1 | sed 's/^/    /'
echo
echo "[local bars] $LOCAL_BARS"
L_BAR_MTIME=$(mtime_local "$LOCAL_BARS/manifest.json")
check_age "$L_BAR_MTIME" "local bar"
# --check exits 1 on an EMPTY bar store, the failure this file exists to catch.
MARKETDATA_STORE="$LOCAL_BARS" "$LOCAL_BAR_CHECK" --check 2>&1 | sed 's/^/    /'
echo
echo "[remote COT]  $REMOTE:$REMOTE_COT"
check_today "$(mtime_remote "$REMOTE_COT/status.json")" "remote COT"
ssh "${SSH_OPTS[@]}" "$REMOTE" "COTDATA_STORE=$REMOTE_COT $REMOTE_COT_CHECK --check" 2>&1 | sed 's/^/    /'
echo
echo "[remote bars] $REMOTE:$REMOTE_BARS"
R_BAR_MTIME=$(mtime_remote "$REMOTE_BARS/manifest.json")
check_age "$R_BAR_MTIME" "remote bar"
ssh "${SSH_OPTS[@]}" "$REMOTE" "MARKETDATA_STORE=$REMOTE_BARS $REMOTE_BAR_CHECK --check" 2>&1 | sed 's/^/    /'
echo

# Cross-check, free and strictly sharper than either window on its own. Both syncs
# preserve timestamps, so BOTH replicas should carry the producer's own mtime,
# identical to the second. If they differ, one push is behind the other — and that
# stays true inside the staleness window, where neither replica looks wrong alone.
if [ -n "$L_BAR_MTIME" ] && [ -n "$R_BAR_MTIME" ] && [ "$L_BAR_MTIME" != "$R_BAR_MTIME" ]; then
  echo "  WARN: the two bar replicas hold DIFFERENT manifest mtimes"
  echo "          local  $(fmt_epoch "$L_BAR_MTIME" '+%F %T')"
  echo "          remote $(fmt_epoch "$R_BAR_MTIME" '+%F %T')"
  echo "        One of the two bar pushes is behind. Both syncs preserve timestamps,"
  echo "        so a matching producer run should land the same mtime on both."
  echo
fi

if [ "$fail" -eq 0 ] && [ "$pass" -eq 4 ]; then
  echo "RESULT: PASS — both replicas hold a current COT store and a current bar store."
  exit 0
else
  echo "RESULT: FAIL — $fail of 4 checks did not pass. Check the producer tasks and their chained syncs."
  exit 1
fi
