#!/usr/bin/env bash
# cotdata price update wrapper for cron / systemd (Linux, databento producer).
# Copy this file next to your crontab's <DIR> and replace the <...> placeholders:
#   <STORE> = your data store          e.g. /srv/cotdata_store
#   <KEY>   = your Databento API key   e.g. db-...
#   <VENV>  = your cotdata virtualenv  e.g. /opt/cotdata/.venv
# Stage 1 (--ingest-databento) is the paid pull; Stage 2 (--build-databento) is a
# free local rebuild. See docs/LINUX_SCHEDULING.md for the crontab and flock setup.
set -euo pipefail
export COTDATA_STORE=<STORE>
export COTDATA_PRICE_SOURCE=databento
export DATABENTO_API_KEY=<KEY>
BIN=<VENV>/bin/cotdata-update
"$BIN" --ingest-databento     # Stage 1 (paid): raw .n.0/.n.1 -> raw store
"$BIN" --build-databento      # Stage 2 (free): back-adjusted prices
"$BIN" --prices-yahoo         # softs / lumber / MSCI fallback
