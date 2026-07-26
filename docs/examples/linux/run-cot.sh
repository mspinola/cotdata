#!/usr/bin/env bash
# cotdata COT update wrapper for cron / systemd (Linux, cross-platform).
# Copy this file next to your crontab's <DIR> and replace the <...> placeholders:
#   <STORE> = your data store         e.g. /srv/cotdata_store
#   <VENV>  = your cotdata virtualenv e.g. /opt/cotdata/.venv
# --cot-all is idempotent (HEAD-checks each CFTC zip), so re-running is cheap.
# See docs/LINUX_SCHEDULING.md for the crontab and flock setup.
set -euo pipefail
export COTDATA_STORE=<STORE>
<VENV>/bin/cotdata-update --cot-all
