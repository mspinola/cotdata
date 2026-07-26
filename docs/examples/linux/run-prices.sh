#!/usr/bin/env bash
# cotdata price update wrapper for cron / systemd (Linux, databento producer).
# Copy this file next to your crontab dir and overwrite the markers below:
#   REPLACE_WITH_STORE_PATH    = your data store          e.g. /srv/cotdata_store
#   REPLACE_WITH_DATABENTO_KEY = your Databento API key   e.g. db-...
#   REPLACE_WITH_VENV_PATH     = your cotdata venv         e.g. /opt/cotdata/.venv
# (Plain-text markers, not angle-bracket placeholders: an unedited <...> would be
# read as a shell redirection. Stage 1 is the paid pull, Stage 2 a free rebuild.)
# cotdata-prices runs the PRICE half only and refuses --cot-all.
# See docs/LINUX_SCHEDULING.md for the crontab and flock setup.
set -euo pipefail
export COTDATA_STORE=REPLACE_WITH_STORE_PATH
export COTDATA_PRICE_SOURCE=databento
export DATABENTO_API_KEY=REPLACE_WITH_DATABENTO_KEY
BIN=REPLACE_WITH_VENV_PATH/bin/cotdata-prices
"$BIN" --ingest-databento     # Stage 1 (paid): raw .n.0/.n.1 to raw store
"$BIN" --build-databento      # Stage 2 (free): back-adjusted prices
"$BIN" --prices-yahoo         # softs / lumber / MSCI fallback
