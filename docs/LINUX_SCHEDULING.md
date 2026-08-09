# Scheduling cotdata on Linux (cron)

For the cross-platform Databento producer path (no Norgate/Windows required) — see [Cross-platform prices without Norgate](../README.md#cross-platform-prices-without-norgate-databento) in the README for the one-time `--ingest-databento` / `--build-databento` setup before automating it here.

## Goal

A databento server schedules **prices nightly** and **COT soon after its Friday ~3:30pm ET release**, with a daily catch-up for holiday delays. Two properties hold:

- **Idempotent.** `--cot-all` HEAD-checks each CFTC year zip and skips it if unchanged. `--ingest-databento` resumes from the last fetched date, so a re-run pulls only new days. Running before new data lands is a harmless no-op.
- **Fails loudly.** A run exits non-zero only on a hard fetch error (source unreachable), not when there is simply nothing new. Because ingest is resumable and COT is idempotent, a failed or missed run is picked up by the next one, so no explicit retry logic is needed.

## Wrapper scripts

Cron runs with a bare environment, so put the config and the venv path in a wrapper script (one per command, mirroring the Windows pair).

> **Ready-made templates:** copy [`docs/examples/linux/run-prices.sh`](examples/linux/run-prices.sh) and [`run-cot.sh`](examples/linux/run-cot.sh) out of the repo into your `<DIR>`, `chmod +x` them, and fill in the placeholders — keep them outside the repo so a `git pull` never clobbers your edited paths.

Inside the scripts, overwrite the plain-text markers: `REPLACE_WITH_STORE_PATH` = your store, `REPLACE_WITH_VENV_PATH` = your virtualenv, `REPLACE_WITH_DATABENTO_KEY` = your Databento key. (They're plain markers, not `<...>` placeholders, because an unedited `<...>` would be read as a shell redirection and the script would fail.) The `<DIR>` in the crontab lines below is normal fill-in notation.

`run-prices.sh` — the two-stage databento build:

```bash
#!/usr/bin/env bash
set -euo pipefail
export COTDATA_STORE=REPLACE_WITH_STORE_PATH
export DATABENTO_API_KEY=REPLACE_WITH_DATABENTO_KEY
BIN=REPLACE_WITH_VENV_PATH/bin/cotdata-prices
"$BIN" --ingest-databento     # Stage 1 (paid): raw .n.0/.n.1 to raw store
"$BIN" --build-databento      # Stage 2 (free): back-adjusted prices
```

Databento is the only price producer left in this package. ADR-0007 moved the Norgate and
Yahoo bar producers to [`marketdata`](https://pypi.org/project/crucible-marketdata/), so the
markets databento does not cover — ICE softs, lumber, the MSCI ETF proxies — are fetched by
`marketdata-update --bars` against `$MARKETDATA_STORE` on whichever box produces that store,
not by this script.

`run-cot.sh` — COT (note the different command):

```bash
#!/usr/bin/env bash
set -euo pipefail
export COTDATA_STORE=REPLACE_WITH_STORE_PATH
REPLACE_WITH_VENV_PATH/bin/cotdata-cot --cot-all
```

Make them executable:

```bash
chmod +x run-prices.sh run-cot.sh
```

## Crontab entries

Add the jobs with `crontab -e`. Cron uses the **server's local** timezone, so convert the ET times below if it is not on Eastern (or set the server to a known zone). `flock` stops a slow run from overlapping the next, and the redirect keeps a log:

```cron
# Prices — nightly (Mon-Sat). GLBX settlements are disseminated the morning after the
# session, so an early-morning run captures the prior session's finalized settlement.
30 6 * * 1-6     flock -n /tmp/cotdata-prices.lock <DIR>/run-prices.sh >> <DIR>/prices.log 2>&1

# COT — daily morning catch-up (holiday-delayed releases and a safety net).
10 8 * * *       flock -n /tmp/cotdata-cot.lock <DIR>/run-cot.sh >> <DIR>/cot.log 2>&1

# COT — Friday release window: every 2 min across the ~3:30pm ET release (times in ET,
# each run a cheap no-op until the zip changes). Convert to the server's local time.
*/2 15-16 * * 5  flock -n /tmp/cotdata-cot.lock <DIR>/run-cot.sh >> <DIR>/cot.log 2>&1
```

Set `MAILTO=you@example.com` at the top of the crontab to have cron email the output of any run that writes to stderr or exits non-zero. For tighter alerting, point your monitoring at the log files or the store's `status.json` (see [Operations](../README.md#operations)), or run the wrappers from a systemd timer with `OnFailure=`. Check coverage and freshness any time with `cotdata-update --check`.

**Monitoring:** after any run, `status.json` reflects `newest_data.<domain>` and `last_run.symbols_failed` — poll it to confirm the Friday COT actually advanced, or to alert on failures (see [Operations](../README.md#operations)).

> The Friday window intentionally over-polls (every 2 minutes across a 45-minute window); idempotency makes every run after the release lands a no-op. If you'd rather actively wait out *late* releases, a wrapper can loop until `status.json`'s `newest_data.cot_legacy` reaches the expected Tuesday — but daily catch-up already covers holiday slips with far less machinery.

## Troubleshooting

### Cron job runs manually but not on schedule

Cron's environment is far barer than an interactive shell — no `PATH` beyond `/usr/bin:/bin`, no `.bashrc`/`.profile` sourced, no venv activation. This is exactly why the wrapper scripts above call the venv's binary by full path (`<VENV>/bin/cotdata-prices`, `<VENV>/bin/cotdata-cot`) rather than a bare command name, and `export` every variable instead of relying on a login shell to have set them. If a script works when you run it by hand but not under cron, the first thing to check is whether it depends on something your interactive shell set up implicitly.

### Job silently does nothing

Check `<DIR>/prices.log` or `<DIR>/cot.log` first — the wrappers redirect both stdout and stderr there. If the log is empty or missing entirely, cron likely never ran the job: check `grep CRON /var/log/syslog` (Debian/Ubuntu) or `journalctl -u cron` (systemd) for the scheduled time to confirm cron invoked it at all.

### Overlapping runs / stale lock

`flock -n` fails fast (doesn't block) if another instance already holds the lock file, so a slow `run-prices.sh` won't stack with the next scheduled run — the second invocation just no-ops and exits. The lock releases automatically when the holding process exits, including on a crash, so a stale lock that blocks forever generally indicates a *hung*, still-running process, not manual cleanup — check with `ps aux | grep cotdata` before deleting anything under `/tmp`.

### Permission denied running the wrapper

Confirm `chmod +x` was applied to both `.sh` files, and that the shebang (`#!/usr/bin/env bash`) resolves — run `which bash` to confirm it's on `PATH` for the cron user (usually is, but matters more on minimal containers).

### Timezone confusion on the Friday window

`*/2 15-16 * * 5` assumes the server clock is US Eastern. Check with `timedatectl` (systemd) or `date`; if the server runs UTC, convert 3:25-4:10pm ET to the equivalent UTC hours (accounting for EST/EDT) before setting the crontab, or set `CRON_TZ=America/New_York` above the line if your cron implementation supports per-line `CRON_TZ` (modern `cronie`/Debian cron do).

### `DATABENTO_API_KEY` not picked up

Because cron strips the environment, a key that's only set in `~/.bashrc` or a shell profile is invisible to the job. It must be `export`ed inside the wrapper script itself, as shown above — not just present in your interactive shell's environment.
