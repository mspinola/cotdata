# Scheduling cotdata on Linux (cron)

> **Only COT is scheduled here now.** ADR-0007 moved every price producer to
> [`marketdata`](https://pypi.org/project/crucible-marketdata/), databento included, so
> the nightly price job on this box is `marketdata-update --ingest-databento` /
> `--build-databento` against `$MARKETDATA_STORE`. See that package's README. The COT
> half below is unchanged.
>
> **Upgrading?** Delete the old `run-prices.sh` — it calls `cotdata-prices`, which no
> longer exists, so the job will fail every night until it is replaced or removed.

## Goal

**COT soon after its Friday ~3:30pm ET release**, with a daily catch-up for holiday delays. Two properties hold:

- **Idempotent.** `--cot-all` HEAD-checks each CFTC year zip and skips it if unchanged. Running before new data lands is a harmless no-op.
- **Fails loudly.** A run exits non-zero only on a hard fetch error (source unreachable), not when there is simply nothing new. Because COT is idempotent, a failed or missed run is picked up by the next one, so no explicit retry logic is needed.

## Wrapper scripts

Cron runs with a bare environment, so put the config and the venv path in a wrapper script.

> **Ready-made template:** copy [`run-cot.sh`](examples/linux/run-cot.sh) out of the repo into your `<DIR>`, `chmod +x` it, and fill in the placeholders — keep it outside the repo so a `git pull` never clobbers your edited paths.

Inside it, overwrite the plain-text markers: `REPLACE_WITH_STORE_PATH` = your store, `REPLACE_WITH_VENV_PATH` = your virtualenv. (They're plain markers, not `<...>` placeholders, because an unedited `<...>` would be read as a shell redirection and the script would fail.) The `<DIR>` in the crontab lines below is normal fill-in notation.

`run-cot.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
export COTDATA_STORE=REPLACE_WITH_STORE_PATH
REPLACE_WITH_VENV_PATH/bin/cotdata-cot --cot-all
```

Make it executable:

```bash
chmod +x run-cot.sh
```

## Crontab entries

Add the jobs with `crontab -e`. Cron uses the **server's local** timezone, so convert the ET times below if it is not on Eastern (or set the server to a known zone). `flock` stops a slow run from overlapping the next, and the redirect keeps a log:

```cron
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

Cron's environment is far barer than an interactive shell — no `PATH` beyond `/usr/bin:/bin`, no `.bashrc`/`.profile` sourced, no venv activation. This is exactly why the wrapper script above calls the venv's binary by full path (`<VENV>/bin/cotdata-cot`) rather than a bare command name, and `export` every variable instead of relying on a login shell to have set them. If a script works when you run it by hand but not under cron, the first thing to check is whether it depends on something your interactive shell set up implicitly.

### Job silently does nothing

Check `<DIR>/cot.log` first — the wrapper redirects both stdout and stderr there. If the log is empty or missing entirely, cron likely never ran the job: check `grep CRON /var/log/syslog` (Debian/Ubuntu) or `journalctl -u cron` (systemd) for the scheduled time to confirm cron invoked it at all.

### Overlapping runs / stale lock

`flock -n` fails fast (doesn't block) if another instance already holds the lock file, so a slow `run-cot.sh` won't stack with the next scheduled run — the second invocation just no-ops and exits. The lock releases automatically when the holding process exits, including on a crash, so a stale lock that blocks forever generally indicates a *hung*, still-running process, not manual cleanup — check with `ps aux | grep cotdata` before deleting anything under `/tmp`.

### Permission denied running the wrapper

Confirm `chmod +x` was applied to the `.sh` file, and that the shebang (`#!/usr/bin/env bash`) resolves — run `which bash` to confirm it's on `PATH` for the cron user (usually is, but matters more on minimal containers).

### Timezone confusion on the Friday window

`*/2 15-16 * * 5` assumes the server clock is US Eastern. Check with `timedatectl` (systemd) or `date`; if the server runs UTC, convert 3:25-4:10pm ET to the equivalent UTC hours (accounting for EST/EDT) before setting the crontab, or set `CRON_TZ=America/New_York` above the line if your cron implementation supports per-line `CRON_TZ` (modern `cronie`/Debian cron do).

### `DATABENTO_API_KEY` not picked up

Because cron strips the environment, a key that's only set in `~/.bashrc` or a shell profile is invisible to the job. It must be `export`ed inside the wrapper script itself, as shown above — not just present in your interactive shell's environment.
