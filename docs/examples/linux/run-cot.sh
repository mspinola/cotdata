#!/usr/bin/env bash
# cotdata COT update wrapper for cron / systemd (Linux, cross-platform).
# Copy this file next to your crontab dir and overwrite the markers below:
#   REPLACE_WITH_STORE_PATH = your data store         e.g. /srv/cotdata_store
#   REPLACE_WITH_VENV_PATH  = your cotdata venv        e.g. /opt/cotdata/.venv
# (Plain-text markers, not angle-bracket placeholders: an unedited <...> would be
# read as a shell redirection.) --cot-all is idempotent, so re-running is cheap.
# cotdata-cot runs the COT half only and refuses the price flags, so one host does
# one job. Use cotdata-update if a single machine must do both.
# See docs/LINUX_SCHEDULING.md for the crontab and flock setup.
set -euo pipefail
export COTDATA_STORE=REPLACE_WITH_STORE_PATH
REPLACE_WITH_VENV_PATH/bin/cotdata-cot --cot-all
