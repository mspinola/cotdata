# Scheduling cotdata on Windows (Task Scheduler)

New to Python and cotdata on Windows? Start with the [Windows Setup Guide](WINDOWS_SETUP.md) — install Python, create the venv, and confirm `cotdata-update --cot-legacy` works by hand before automating it.

> [!IMPORTANT]
> **The price task runs a different package.** ADR-0007 moved ALL bar production to
> [`crucible-marketdata`](https://pypi.org/project/crucible-marketdata/), so the nightly job is
> `marketdata-update --bars --domain futures --require-final` against `$MARKETDATA_STORE`, not
> `cotdata-prices --prices`. It is still scheduled here, on the same box, at the same time,
> with the same restart-on-failure trick — every operational fact below about NDU, the
> interactive session, deferred exit codes and retry is unchanged. Only the command and the
> store variable moved.
>
> **Upgrading?** Edit `run-prices.cmd` to the new command and give it `MARKETDATA_STORE`. A
> wrapper still calling `cotdata-prices` now fails to resolve the command at all — that entry
> point no longer exists — which Task Scheduler shows as a failed run. Loud, not silent.

## Goal

**Prices daily**, and **COT caught within minutes of its Friday ~3:30pm ET release** while surviving holiday delays. Two properties make this simple:

- **Idempotent.** `cotdata-update --cot-*` HEAD-checks each CFTC year zip and skips it if unchanged, so re-running is cheap. Running before the release lands is a harmless no-op; the first run *after* it lands picks it up.
- **Fails loudly.** A run exits non-zero only on a hard fetch error (source unreachable) — *not* when there's simply no new data yet. So Task Scheduler's "restart on failure" retries real errors without firing on ordinary "nothing new" runs.

## Wrapper scripts

Create **two** wrapper scripts — they run *different* commands from *different* packages: `marketdata-update` for the bars, `cotdata-cot` for the COT. (`cotdata-cot` is an alias of `cotdata-update`; it used to be half of a scoped pair, and the other half went with the price producers.)

> **Ready-made templates:** copy [`docs/examples/windows/run-prices.cmd`](examples/windows/run-prices.cmd) and [`run-cot.cmd`](examples/windows/run-cot.cmd) out of the repo into your `<DIR>` (e.g. `C:\Users\you\cotdata\scheduler\`) rather than retyping them — then just fill in the placeholders. Keep them outside the repo so a `git pull` never clobbers your edited paths.

> **Fill in your real paths.** Inside the `.cmd` files, overwrite the plain-text markers `REPLACE_WITH_STORE_PATH` (your synced store, e.g. `\\Mac\code\cotdata_store`) and `REPLACE_WITH_VENV_PATH` (your virtualenv, e.g. `C:\Users\you\code\cotdata\.venv`). **Don't use angle-bracket placeholders like `<STORE>` inside a `.cmd`** — cmd reads `<` and `>` as redirection and the script fails with "The syntax of the command is incorrect," even on `REM` comment lines. The `<DIR>` notation in the *task commands* further down is fine to substitute since those are quoted or typed at the prompt.

`run-prices.cmd` — bars (with `--require-final`, so it runs only once Norgate's **Final** prices are in, not interim bars). Note `MARKETDATA_STORE`: a *different* directory from `COTDATA_STORE`, not an alias for it.

```bat
@echo off
set MARKETDATA_STORE=REPLACE_WITH_MARKETDATA_STORE_PATH
"REPLACE_WITH_VENV_PATH\Scripts\marketdata-update.exe" --bars --domain futures --require-final
"REPLACE_WITH_VENV_PATH\Scripts\marketdata-update.exe" --metadata
```

`run-cot.cmd` — COT (note the **different** command, `--cot-all`):

```bat
@echo off
set COTDATA_STORE=REPLACE_WITH_STORE_PATH
"REPLACE_WITH_VENV_PATH\Scripts\cotdata-cot.exe" --cot-all
```

Using the full venv `\Scripts\marketdata-update.exe` / `\Scripts\cotdata-cot.exe` path (rather than relying on the command being on `PATH`) matters here: Task Scheduler runs with a different, often bare, environment than your interactive shell, so a bare command name that resolves fine in Command Prompt can fail to resolve under the scheduler.

`run-vintage.cmd` — **optional**, the as-published (vintage) capture. Copy
[`docs/examples/windows/run-vintage.cmd`](examples/windows/run-vintage.cmd); it runs
`cotdata-vintage fetch` then `ingest --pending`, both exit-code guarded. Two things to know
before enabling it:

- **It belongs on the producer.** Capture fetches from CFTC, so it is a producer action —
  and a vintage tree written on a mirrored replica is deleted by the next sync, which is
  unrecoverable because CFTC serves current state only. See [SYNCING.md](SYNCING.md).
- **Schedule it daily, not weekly.** Nearly every request returns 304, so a daily run costs
  almost nothing while catching holiday-shifted and backlog releases with no schedule logic.



## Creating the tasks

Create three tasks (plus an optional fourth if you enable vintage capture) — times are the **machine's local** time; convert from ET if it isn't on Eastern:

```bat
:: 1) Bars — fire at the Continuous Futures Final (~8:55pm ET); --require-final + restart
::    below keep retrying (cheap no-ops) until Norgate has actually pulled the Finals.
schtasks /Create /TN "marketdata bars" /TR "<DIR>\run-prices.cmd" /SC DAILY /ST 20:55

:: 2) COT — daily morning catch-up for holiday-delayed releases and as a safety net
schtasks /Create /TN "cotdata COT (catch-up)" /TR "<DIR>\run-cot.cmd" /SC DAILY /ST 08:10

:: 3) Vintage (OPTIONAL) — as-published capture, ~90 min after the 15:30 ET release.
::    Daily is deliberate: almost every request 304s, so it is nearly free, and it
::    tightens the observed release date from a 7-day bound to a 1-day one.
schtasks /Create /TN "cotdata vintage" /TR "<DIR>\run-vintage.cmd" /SC DAILY /ST 17:00
```

> **Substitute `<DIR>` before running these** — with the real folder holding your `.cmd` files, e.g. `C:\Users\you\code\cotdata\scheduler`. `schtasks` takes the quoted `/TR` value as a literal string and **does not check the file exists**, so a leftover `"<DIR>\run-cot.cmd"` is accepted without error and creates a task that fails only when it fires. Verify each task points somewhere real:
> ```powershell
> Get-ScheduledTask -TaskName "cotdata*" | Select-Object TaskName, @{n='Action';e={$_.Actions.Execute}}
> ```
> Every `Action` should be a full path to an existing `.cmd`. Fix a stray placeholder in place with `schtasks /Change /TN "cotdata COT (catch-up)" /TR "C:\real\path\run-cot.cmd"`.

> **Prices task — two settings you must check now**, before this task will work unattended. Open it in `taskschd.msc` → Properties:
> 1. **General tab → "Run only when user is logged on"** (the default — keep it). The prices task talks to the Norgate Data Updater, which only exists in your interactive desktop session; "run whether user is logged in or not" runs where NDU is invisible and the run fails. See [Norgate Data Updater needs an interactive session](#norgate-data-updater-needs-an-interactive-session).
> 2. **Conditions tab → uncheck "Start the task only if the computer is on AC power"** (checked by default) if this is ever on a laptop — otherwise runs are silently skipped on battery. See [Task doesn't fire at all](#task-doesnt-fire-at-all).
>
> Neither applies to the COT tasks — those are a plain CFTC download with no Norgate dependency, so they run fine non-interactively.

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

**Event-driven bars with `--require-final`.** The producer reads two Norgate databases: **Continuous Futures** (the `&ES` / `_CCB` series) and **Futures** (the individual `ES-2026H` contracts used to reconstruct volume). Their **Final** prices land ~8:40pm ET (Futures) and ~8:55pm ET (Continuous Futures), but your Norgate Data Updater still has to *pull* them on its next poll. Rather than guess a fixed time, `--require-final` asks a **data** question: does Norgate hold a NEWER settled bar than the store already does, for a quorum of liquid reference symbols? That needs no wall-clock cutoff and no trading calendar, so it is immune to Norgate's publish-time drift — an early publish is caught early and a late one simply defers. (It replaced a fixed `--final-cutoff`, which broke in production on 2026-07-27 when Norgate finalized one database at 8:49pm and the check demanded 8:55pm for both. See [design/finals_ready_data_driven.md](design/finals_ready_data_driven.md).) Until it is ready the run **defers with a non-zero exit** having fetched nothing, so the restart setting below turns "fire at 8:55pm" into "run the moment NDU has the Finals."

**Retry / wait via restart-on-failure.** Give each task a *restart on failure* — it does double duty: it retries transient fetch errors, and (for the price task) waits out the gap between 8:55pm and NDU actually pulling the Finals (each retry is a short trailing-window read and a date compare, exiting immediately until ready). On a genuine no-session day the retries simply exhaust, harmlessly. `schtasks` can't set this, so use PowerShell (applies to all three tasks):

```powershell
$s = New-ScheduledTaskSettingsSet -RestartInterval (New-TimeSpan -Minutes 10) -RestartCount 6
foreach ($t in "marketdata bars","cotdata COT (Fri release)","cotdata COT (catch-up)") {
    Set-ScheduledTask -TaskName $t -Settings $s
}
```

(GUI equivalent: each task → **Settings** tab → *"If the task fails, restart every: 10 minutes"*, *"Attempt to restart up to: 6 times."*)

**View / manage the jobs** any time in the Windows **Task Scheduler** GUI — press `Win + R` and run `taskschd.msc`, or open *Task Scheduler* from the Start menu — then look under **Task Scheduler Library** for the `cotdata …` tasks.

## Testing your tasks

Test in three layers: fire the task, read the result, then confirm it actually wrote data. The third layer is the one that matters — for cotdata the exit code alone is not a reliable success signal (see below).

### 1. Fire the task on demand

Don't wait for the trigger — run it now:

```bat
schtasks /Run /TN "marketdata bars"
```

(Or in `taskschd.msc`: right-click the task → **Run**.) Running the *task* rather than the `.cmd` by hand is the stronger test: it exercises the scheduler's own account, environment, and working directory, which is where scheduled runs usually differ from your interactive shell.

### 2. Read what happened

```bat
schtasks /Query /TN "marketdata bars" /V /FO LIST
```

Check **Last Run Time** and **Last Result**. For per-run detail, enable history once (right-click the task or the library root → **Enable All Tasks History**) and read the task's **History** tab.

### 3. Confirm data actually landed (the authoritative check)

**Don't trust `Last Result: 0x0` alone.** A `--require-final` price run *defers with a non-zero exit* when Norgate's Finals aren't in yet — by design, so restart-on-failure turns it into a poll loop (see [Task shows success but wrote nothing](#task-shows-success-but-wrote-nothing)). So the exit code misleads in both directions. Check the store instead:

```bat
cotdata-update --check
```

Confirm the relevant `newest data` date advanced (and `last write (UTC)` is recent). A weekend `prices` row showing Friday's date "2d behind" is correct — markets were closed. COT rows tracking the latest Tuesday date are current, since COT is Tuesday-dated and released the following Friday. Per-entry `⚠ … behind` warnings for individual delisted or thin contracts are expected and don't mean the run failed; judge the run by the aggregate `newest data` dates.

### Testing the timing and conditions

- **A daytime bars run usually only proves the wrapper resolves** — if last night captured, the store already holds the newest settled bar, so a daytime run finds nothing newer and defers. The exception is worth knowing: if last night's run *failed or never fired*, a daytime run finds Norgate ahead of the store and captures immediately, so a missed night self-heals at the next trigger rather than waiting for the evening. To exercise the write path on demand regardless, run `marketdata-update --bars --domain futures` by hand (no `--require-final`), or fire the task after ~8:55pm ET.
- **Test the trigger itself** by moving it a couple of minutes out, watching it fire, then setting it back:
  ```bat
  schtasks /Change /TN "marketdata bars" /ST 14:20
  :: watch it run, then restore
  schtasks /Change /TN "marketdata bars" /ST 20:55
  ```
  This catches the two silent killers below — a disabled task, or the default *"only if on AC power"* condition skipping runs on a laptop.
- **Keep a permanent record** by having the wrapper redirect output to a log file (see [Diagnosing a silent failure](#diagnosing-a-silent-failure)).

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

### The store is on an RDP redirected drive (`\\tsclient\...`)

A `COTDATA_STORE` beginning `\\tsclient\` is not a network share. It is Remote Desktop
**client drive redirection**, a virtual channel that exists only while an RDP session is
connected.

Two consequences, both of which look like unrelated failures:

- **The path disappears when you disconnect.** A task that fires at 20:55 with nobody
  connected cannot resolve the drive at all, so it fails before touching Norgate. Combined
  with the interactive-session requirement above, the machine now needs an *active RDP
  session* at run time, not merely a logged-in console session.
- **Atomic writes are weaker than on a local disk.** The store commits parquet with
  `os.replace` into the same directory, which relies on filesystem rename semantics. Over
  a redirected channel those guarantees are not the same as on NTFS, so an interrupted
  write is likelier to leave a partial file.

**Fix:** point `COTDATA_STORE` at a local disk on the producer machine and sync outward
as a separate step (Syncthing, rclone, robocopy on a timer, a scheduled push). The
producer then depends on nothing but its own filesystem, and the sync failing is a
visible, separate problem rather than a silent missing write.

### Norgate Data Updater needs an interactive session

**The single most common cotdata-on-Task-Scheduler failure.** The `norgatedata` package doesn't call a remote API — it talks locally to the **Norgate Data Updater (NDU)** app, which is a GUI program that has to be running and authenticated *in your desktop session*.

If a task's General tab has **"Run whether user is logged in or not"** checked, Windows runs it in a non-interactive session (effectively no desktop), and it cannot reach an NDU instance running in your logged-in session — `marketdata-update --bars` will fail even though NDU looks fine when you check it yourself.

**Fix:** for the prices task, use **"Run only when user is logged on"** (the default) so it executes in your interactive session alongside NDU. This does mean the machine needs to be logged in (not just powered on) at run time — screen lock is fine, logged-out is not.

### Task shows success but wrote nothing

`marketdata-update` exits non-zero on a hard fetch error, but a **deferred** `--require-final` run (NDU hasn't pulled the Finals yet) also exits non-zero — that's by design, not a bug, and the restart-on-failure setting is what turns those into a working poll loop. Don't "fix" this by making the wrapper swallow the exit code; that breaks retry.

To confirm a run actually wrote data, check `status.json` in the store (`newest_data.prices` advancing) rather than trusting Task Scheduler's Last Run Result alone — see [Operations](../README.md#operations) in the README.

### Task Scheduler can't find `marketdata-update` / `cotdata-cot`

Always call the fully-qualified `<VENV>\Scripts\marketdata-update.exe` (or `cotdata-cot.exe`) inside the wrapper `.cmd`, never a bare command name. The scheduler's environment doesn't inherit your interactive shell's activated venv or `PATH` changes.

### Diagnosing a silent failure

1. Enable history: right-click the task → **Enable All Tasks History** (or Task Scheduler root → **Enable All Tasks History** from the Action menu)
2. Check the task's **History** tab for the actual run and its result code
3. Run the wrapper `.cmd` by hand from Command Prompt first — if it fails there too, it's a script/environment issue, not a scheduler issue
4. Check **Event Viewer → Windows Logs → Application** for `TaskScheduler` source entries around the run time
5. Have the wrapper redirect output to a log file for a permanent record:
   ```bat
   "<VENV>\Scripts\cotdata-cot.exe" --cot-all >> "<DIR>\cot.log" 2>&1
   ```

### Restart settings not taking effect

`schtasks /Create` cannot set restart-on-failure — it's a PowerShell-only (`Set-ScheduledTask` / `New-ScheduledTaskSettingsSet`) or GUI-only setting. If the price task keeps missing the Finals window, verify the Settings tab actually shows a restart interval and count, not just the trigger time.

### Reference

- [Task Scheduler error and success constants](https://docs.microsoft.com/en-us/windows/win32/taskschd/task-scheduler-error-and-success-constants) — decode a numeric Last Run Result code
- Event Viewer: `eventvwr.msc` → Windows Logs → Application, filter by source `TaskScheduler`
