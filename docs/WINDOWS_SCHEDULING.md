# Scheduling cotdata on Windows (Task Scheduler)

New to Python and cotdata on Windows? Start with the [Windows Setup Guide](WINDOWS_SETUP.md) — install Python, create the venv, and confirm `cotdata-update --cot-legacy` works by hand before automating it.

## Goal

**Prices daily**, and **COT caught within minutes of its Friday ~3:30pm ET release** while surviving holiday delays. Two properties make this simple:

- **Idempotent.** `cotdata-update --cot-*` HEAD-checks each CFTC year zip and skips it if unchanged, so re-running is cheap. Running before the release lands is a harmless no-op; the first run *after* it lands picks it up.
- **Fails loudly.** A run exits non-zero only on a hard fetch error (source unreachable) — *not* when there's simply no new data yet. So Task Scheduler's "restart on failure" retries real errors without firing on ordinary "nothing new" runs.

## Wrapper scripts

Create **two** wrapper scripts — they run *different* commands. Each sets `COTDATA_STORE` and calls the venv's `cotdata-update`.

> **Ready-made templates:** copy [`docs/examples/windows/run-prices.cmd`](examples/windows/run-prices.cmd) and [`run-cot.cmd`](examples/windows/run-cot.cmd) out of the repo into your `<DIR>` (e.g. `C:\Users\you\cotdata\scheduler\`) rather than retyping them — then just fill in the placeholders. Keep them outside the repo so a `git pull` never clobbers your edited paths.

> **Replace the `<...>` placeholders with your real paths** — in *both* the wrapper files *and* the task commands further down. `<STORE>` = your synced store, `<VENV>` = your virtualenv, `<DIR>` = the folder holding these `.cmd` files. Example values: `<STORE>` = `\\Mac\code\cotdata_store`, `<VENV>` = `C:\Users\you\code\cotdata\.venv`.

`run-prices.cmd` — prices (with `--require-final`, so it runs only once Norgate's **Final** prices are in, not interim bars):

```bat
@echo off
set COTDATA_STORE=<STORE>
"<VENV>\Scripts\cotdata-update.exe" --prices --metadata --require-final
```

`run-cot.cmd` — COT (note the **different** command, `--cot-all`):

```bat
@echo off
set COTDATA_STORE=<STORE>
"<VENV>\Scripts\cotdata-update.exe" --cot-all
```

Using the full `<VENV>\Scripts\cotdata-update.exe` path (rather than relying on `cotdata-update` being on `PATH`) matters here: Task Scheduler runs with a different, often bare, environment than your interactive shell, so a bare command name that resolves fine in Command Prompt can fail to resolve under the scheduler.

## Creating the tasks

Create three tasks — times are the **machine's local** time; convert from ET if it isn't on Eastern:

```bat
:: 1) Prices — fire at the Continuous Futures Final (~8:55pm ET); --require-final + restart
::    below keep retrying (cheap no-ops) until Norgate has actually pulled the Finals.
schtasks /Create /TN "cotdata prices" /TR "<DIR>\run-prices.cmd" /SC DAILY /ST 20:55

:: 2) COT — daily morning catch-up for holiday-delayed releases and as a safety net
schtasks /Create /TN "cotdata COT (catch-up)" /TR "<DIR>\run-cot.cmd" /SC DAILY /ST 08:10
```

The **Friday release window** needs a *repeating* trigger, which `schtasks` can't express on a weekly schedule (`/ET` and `/DU` are MINUTE/HOURLY only). Create it in PowerShell instead — weekly on Friday at 3:25pm ET, repeating every 2 min for 45 minutes so it catches the ~3:30 release within a couple of minutes:

```powershell
$act = New-ScheduledTaskAction -Execute "<DIR>\run-cot.cmd"
$trg = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Friday -At 3:25PM
# borrow a repetition pattern (schtasks/New-ScheduledTaskTrigger can't set it directly on a weekly trigger):
$rep = (New-ScheduledTaskTrigger -Once -At 3:25PM `
        -RepetitionInterval (New-TimeSpan -Minutes 2) `
        -RepetitionDuration (New-TimeSpan -Minutes 45)).Repetition
$trg.Repetition = $rep
Register-ScheduledTask -TaskName "cotdata COT (Fri release)" -Action $act -Trigger $trg
```

(Or in the Task Scheduler GUI: New Task → Trigger *Weekly, Friday, 3:25pm* → check *"Repeat task every: 2 minutes for a duration of: 45 minutes."*)

**Event-driven prices with `--require-final`.** cotdata reads two Norgate databases: **Continuous Futures** (the `&ES` / `_CCB` series) and **Futures** (the individual `ES-2026H` contracts used to reconstruct volume). Their **Final** prices land ~8:40pm ET (Futures) and ~8:55pm ET (Continuous Futures), but your Norgate Data Updater still has to *pull* them on its next poll. Rather than guess a fixed time, `--require-final` checks `norgatedata.last_database_update_time()` for both databases and only fetches once each has been refreshed at/after `--final-cutoff` (default `20:55` local — set it to your machine's local equivalent of 8:55pm ET). Until then it **defers with a non-zero exit**, so the restart setting below turns "fire at 8:55pm" into "run the moment NDU has the Finals."

**Retry / wait via restart-on-failure.** Give each task a *restart on failure* — it does double duty: it retries transient fetch errors, and (for the price task) waits out the gap between 8:55pm and NDU actually pulling the Finals (each retry is a cheap `last_database_update_time` check that exits immediately until ready). On a genuine no-session day the retries simply exhaust, harmlessly. `schtasks` can't set this, so use PowerShell (applies to all three tasks):

```powershell
$s = New-ScheduledTaskSettingsSet -RestartInterval (New-TimeSpan -Minutes 10) -RestartCount 6
foreach ($t in "cotdata prices","cotdata COT (Fri release)","cotdata COT (catch-up)") {
    Set-ScheduledTask -TaskName $t -Settings $s
}
```

(GUI equivalent: each task → **Settings** tab → *"If the task fails, restart every: 10 minutes"*, *"Attempt to restart up to: 6 times."*)

**View / manage the jobs** any time in the Windows **Task Scheduler** GUI — press `Win + R` and run `taskschd.msc`, or open *Task Scheduler* from the Start menu — then look under **Task Scheduler Library** for the `cotdata …` tasks.

## Troubleshooting

### Task doesn't fire at all

Before suspecting cotdata or Norgate, rule out the task simply never running:

- **General tab** → confirm **Enabled** is checked (a task can silently sit disabled after an edit)
- **Triggers tab** → confirm the trigger isn't greyed out / expired
- **Conditions tab** → **"Start the task only if the computer is on AC power"** is checked by default and will silently skip runs on an unplugged laptop — uncheck it for an always-on desktop/server producer, since a missed 8:55pm price run has no user-visible symptom other than `status.json` not advancing

If it's unclear whether the trigger is the problem, right-click the task → **Run** to fire it immediately: success there points at the trigger/condition config, failure points at the script/environment instead.

### Can't write to the store over a network path

If `COTDATA_STORE` is a UNC path (e.g. `\\Mac\code\cotdata_store`, as in the placeholder example above) rather than a local drive, the account the task runs as needs its own access to that share — a mapped drive letter set up in your interactive session does **not** carry over to the task's context, and SYSTEM has no credentials for a remote share at all.

**Fix:** on the task's **General** tab, run it as your own domain/local account (not SYSTEM) with **"Run only when user is logged on"**, and confirm that account can read/write the UNC path directly (test with `dir \\Mac\code\cotdata_store` from a fresh Command Prompt). If credentials are needed, use `net use \\Mac\code\cotdata_store /user:domain\you` once interactively, or store the data locally and sync separately instead of writing directly to the share from the scheduled task.

### Norgate Data Updater needs an interactive session

**The single most common cotdata-on-Task-Scheduler failure.** The `norgatedata` package doesn't call a remote API — it talks locally to the **Norgate Data Updater (NDU)** app, which is a GUI program that has to be running and authenticated *in your desktop session*.

If a task's General tab has **"Run whether user is logged in or not"** checked, Windows runs it in a non-interactive session (effectively no desktop), and it cannot reach an NDU instance running in your logged-in session — `cotdata-update --prices` will fail even though NDU looks fine when you check it yourself.

**Fix:** for the prices task, use **"Run only when user is logged on"** (the default) so it executes in your interactive session alongside NDU. This does mean the machine needs to be logged in (not just powered on) at run time — screen lock is fine, logged-out is not.

### Task shows success but wrote nothing

`cotdata-update` exits non-zero on a hard fetch error, but a **deferred** `--require-final` run (NDU hasn't pulled the Finals yet) also exits non-zero — that's by design, not a bug, and the restart-on-failure setting is what turns those into a working poll loop. Don't "fix" this by making the wrapper swallow the exit code; that breaks retry.

To confirm a run actually wrote data, check `status.json` in the store (`newest_data.prices` advancing) rather than trusting Task Scheduler's Last Run Result alone — see [Operations](../README.md#operations) in the README.

### Task Scheduler can't find `cotdata-update`

Always call the fully-qualified `<VENV>\Scripts\cotdata-update.exe` inside the wrapper `.cmd`, never a bare `cotdata-update`. The scheduler's environment doesn't inherit your interactive shell's activated venv or `PATH` changes.

### Diagnosing a silent failure

1. Enable history: right-click the task → **Enable All Tasks History** (or Task Scheduler root → **Enable All Tasks History** from the Action menu)
2. Check the task's **History** tab for the actual run and its result code
3. Run the wrapper `.cmd` by hand from Command Prompt first — if it fails there too, it's a script/environment issue, not a scheduler issue
4. Check **Event Viewer → Windows Logs → Application** for `TaskScheduler` source entries around the run time
5. Have the wrapper redirect output to a log file for a permanent record:
   ```bat
   "<VENV>\Scripts\cotdata-update.exe" --cot-all >> "<DIR>\cot.log" 2>&1
   ```

### Restart settings not taking effect

`schtasks /Create` cannot set restart-on-failure — it's a PowerShell-only (`Set-ScheduledTask` / `New-ScheduledTaskSettingsSet`) or GUI-only setting. If the price task keeps missing the Finals window, verify the Settings tab actually shows a restart interval and count, not just the trigger time.

### Reference

- [Task Scheduler error and success constants](https://docs.microsoft.com/en-us/windows/win32/taskschd/task-scheduler-error-and-success-constants) — decode a numeric Last Run Result code
- Event Viewer: `eventvwr.msc` → Windows Logs → Application, filter by source `TaskScheduler`
