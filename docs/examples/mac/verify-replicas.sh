#!/usr/bin/env bash
# verify-replicas.sh — confirm the latest producer run reached BOTH store replicas
# TODAY: one local store and one remote store read over SSH.
#
# How it works: the producer rewrites `status.json` on every push, and the sync
# carries it, so if a replica's status.json mtime date == the day this runs, that
# replica received today's push. Run it AFTER the producer's scheduled run, e.g.
# from launchd/cron. Exits 0 on PASS (both current), 1 on FAIL (names the laggard).
#
# Configure by editing the block below or exporting the vars before calling.
# Markers are plain text (not <angle brackets>) so an unedited copy still parses.
set -uo pipefail

# ── config ──────────────────────────────────────────────────────────────────
LOCAL_STORE="${LOCAL_STORE:-REPLACE_WITH_LOCAL_STORE}"             # e.g. $HOME/code/cotdata_store
LOCAL_CHECK="${LOCAL_CHECK:-REPLACE_WITH_LOCAL_COTDATA_UPDATE}"    # cotdata-update path, e.g. .../.venv/bin/cotdata-update
REMOTE="${REMOTE:-REPLACE_WITH_REMOTE}"                            # ssh target, e.g. deploy@dash.example.com
REMOTE_STORE="${REMOTE_STORE:-REPLACE_WITH_REMOTE_STORE}"          # e.g. /srv/cotdata_store
REMOTE_CHECK="${REMOTE_CHECK:-REPLACE_WITH_REMOTE_COTDATA_UPDATE}" # cotdata-update path ON the remote
SSH_KEY="${SSH_KEY:-}"                                             # optional: private key; empty = default key/agent
# ────────────────────────────────────────────────────────────────────────────

TODAY=$(date +%F)
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=8)
[ -n "$SSH_KEY" ] && SSH_OPTS+=(-i "$SSH_KEY")

pass=0; fail=0
check_date() {  # $1 = status.json mtime date (YYYY-MM-DD), $2 = label
  if [ "$1" = "$TODAY" ]; then
    echo "  PASS: $2 status.json written today ($1)"; pass=$((pass + 1))
  else
    echo "  FAIL: $2 status.json last written '${1:-unknown}', not today ($TODAY)"; fail=$((fail + 1))
  fi
}

echo "=== cotdata replica verification — $TODAY ==="
echo
echo "[local] $LOCAL_STORE"
# date -r <file> prints the file's mtime on both macOS (BSD) and Linux (GNU).
check_date "$(date -r "$LOCAL_STORE/status.json" +%F 2>/dev/null)" "local"
COTDATA_STORE="$LOCAL_STORE" "$LOCAL_CHECK" --check 2>&1 | sed 's/^/    /'
echo
echo "[remote] $REMOTE:$REMOTE_STORE"
check_date "$(ssh "${SSH_OPTS[@]}" "$REMOTE" "date -r '$REMOTE_STORE/status.json' +%F" 2>/dev/null)" "remote"
ssh "${SSH_OPTS[@]}" "$REMOTE" "COTDATA_STORE=$REMOTE_STORE $REMOTE_CHECK --check" 2>&1 | sed 's/^/    /'
echo

if [ "$fail" -eq 0 ] && [ "$pass" -eq 2 ]; then
  echo "RESULT: PASS — both replicas received today's producer push."
  exit 0
else
  echo "RESULT: FAIL — $fail replica(s) did not update today. Check the producer task and its chained sync."
  exit 1
fi
