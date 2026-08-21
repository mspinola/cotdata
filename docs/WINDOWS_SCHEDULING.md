# Scheduling cotdata on Windows (Task Scheduler)

New to Python and cotdata on Windows? Start with the [Windows Setup Guide](WINDOWS_SETUP.md) — install Python, create the venv, and confirm `cotdata-update --cot-legacy` works by hand before automating it.

> [!IMPORTANT]
> **The price task runs a different package.** ADR-0007 moved ALL bar production to
> [`crucible-marketdata`](https://pypi.org/project/crucible-marketdata/), so the nightly job is
> `marketdata-update --bars --domain futures --require-final` against `$MARKETDATA_STORE`, not
> `cotdata-prices --prices`. It is still scheduled here, on the same box, at the same time,
> with the same repeating-trigger poll — every operational fact below about NDU, the
> interactive session, deferred exit codes and retry is unchanged. Only the command and the
> store variable moved.
>
> **Upgrading?** Edit `run-prices.cmd` to the new command and give it `MARKETDATA_STORE`. A
> wrapper still calling `cotdata-prices` now fails to resolve the command at all — that entry
> point no longer exists — which Task Scheduler shows as a failed run. Loud, not silent.

## Goal

**Prices daily**, and **COT caught within minutes of its Friday ~3:30pm ET release** while surviving holiday delays. Two properties make this simple:

- **Idempotent.** `cotdata-update --cot-*` HEAD-checks each CFTC year zip and skips it if unchanged, so re-running is cheap. Running before the release lands is a harmless no-op; the first run *after* it lands picks it up.
- **Fails loudly.** A COT run exits non-zero only on a hard fetch error (source unreachable) — *not* when there's simply no new data yet, which exits 0. So a repeating trigger polls it cheaply: an ordinary "nothing new" run is indistinguishable from success, and a real error stands out. The bars task is the exception, and the difference matters — `--require-final` *defers* with a **non-zero** exit, so its exit code is a gate answer rather than a health signal. See [Polling with a repeating trigger](#polling-with-a-repeating-trigger).

## Wrapper scripts

Create **three** wrapper scripts — they run *different* commands from *different* packages: `marketdata-update` for the futures bars, `marketdata-update` again for the equities bars (a separate task, for the reasons below), and `cotdata-cot` for the COT. (`cotdata-cot` is an alias of `cotdata-update`; it used to be half of a scoped pair, and the other half went with the price producers.)

> **Ready-made templates:** copy [`docs/examples/windows/run-prices.cmd`](examples/windows/run-prices.cmd) and [`run-cot.cmd`](examples/windows/run-cot.cmd) out of the repo into your `<DIR>` (e.g. `C:\Users\you\cotdata\scheduler\`) rather than retyping them — then just fill in the placeholders. Keep them outside the repo so a `git pull` never clobbers your edited paths.

> **Fill in your real paths.** Inside the `.cmd` files, overwrite the plain-text markers — `REPLACE_WITH_STORE_PATH` (your synced store, e.g. `\\Mac\code\cotdata_store`) and `REPLACE_WITH_VENV_PATH` (your virtualenv, e.g. `C:\Users\you\code\cotdata\.venv`) in the producer wrappers, and the per-store `REPLACE_WITH_COTDATA_*` / `REPLACE_WITH_MARKETDATA_*` pairs in [`sync-store.cmd`](examples/windows/sync-store.cmd) and [`push-to-server.cmd`](examples/windows/push-to-server.cmd), which mirror **two** stores since ADR-0007 (see [SYNCING.md](SYNCING.md)). No marker is a prefix of another, so a find-and-replace is safe. **Don't use angle-bracket placeholders like `<STORE>` inside a `.cmd`** — cmd reads `<` and `>` as redirection and the script fails with "The syntax of the command is incorrect," even on `REM` comment lines. The `<DIR>` notation in the *task commands* further down is fine to substitute since those are quoted or typed at the prompt.

`run-prices.cmd` — bars (with `--require-final`, so it runs only once Norgate's **Final** prices are in, not interim bars). Note `MARKETDATA_STORE`: a *different* directory from `COTDATA_STORE`, not an alias for it.

```bat
@echo off
setlocal
set "MARKETDATA_STORE=REPLACE_WITH_MARKETDATA_STORE_PATH"
set "MDEXE=REPLACE_WITH_VENV_PATH\Scripts\marketdata-update.exe"

"%MDEXE%" --bars --domain futures --require-final
if errorlevel 1 exit /b %ERRORLEVEL%

"%MDEXE%" --metadata
exit /b %ERRORLEVEL%
```

> **Those two `exit /b` lines are load-bearing.** A `.cmd` exits with the code of its LAST
> command, so running `--metadata` after `--bars` with no guard lets its exit 0 overwrite a
> deferral's exit 1 — the gate answering correctly and the wrapper discarding the answer.
> Under a repeating trigger the run still repeats, so this no longer strands the task until
> tomorrow. What it costs you is the only per-run signal that says *this repeat did nothing*:
> every deferred repeat would report success, and the history would stop distinguishing a
> night that captured from a night that never did.
>
> Skipping `--metadata` on a defer matters for a more practical reason: the repeats run every
> 15 minutes for five hours. Guarded, each one is a short trailing-window read and a date
> compare. Unguarded, each is a full contract-spec fetch of every symbol against NDU — and in
> a wrapper that chains the replica syncs, a `robocopy /MIR` and an `rsync --delete` against
> both replicas as well, twenty times a night for no new data.
>
> `if errorlevel 1` tests `>= 1` and needs no variable expansion, so it is safe. `|| exit /b
> %ERRORLEVEL%` would **not** be: cmd expands `%ERRORLEVEL%` when it parses the line, before
> the command on that line has run, so it returns the *previous* command's code.

`run-cot.cmd` — COT (note the **different** command, `--cot-all`):

```bat
@echo off
set COTDATA_STORE=REPLACE_WITH_STORE_PATH
"REPLACE_WITH_VENV_PATH\Scripts\cotdata-cot.exe" --cot-all
```

Using the full venv `\Scripts\marketdata-update.exe` / `\Scripts\cotdata-cot.exe` path (rather than relying on the command being on `PATH`) matters here: Task Scheduler runs with a different, often bare, environment than your interactive shell, so a bare command name that resolves fine in Command Prompt can fail to resolve under the scheduler.

`run-equities.cmd` — the **equities/ETF** bars, from Yahoo rather than Norgate. Copy
[`docs/examples/windows/run-equities.cmd`](examples/windows/run-equities.cmd). It is a
separate task from the futures bars rather than a step inside `run-prices.cmd`, and the
file's header block gives the three reasons in full. The short version: `run-prices.cmd`
exits at its first command once the futures half has captured, so anything chained behind
it is unreachable on the repeats; a single flaky Yahoo symbol fails the whole equities run
and would otherwise abort the futures sync; and Yahoo needs no finals gate, so it can run
at 17:30 and be clear of the 20:55 task. **It takes no `--require-final`** — marketdata
refuses that flag on the equities domain rather than ignoring it.

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

Create four tasks (plus an optional fifth if you enable vintage capture) — times are the **machine's local** time; convert from ET if it isn't on Eastern:

> **The bars task is named `cotdata prices` for historical reasons.** It was created before ADR-0007 moved bar production to `marketdata`, and renaming a live task loses its run history, so the name stayed. It runs `marketdata-update`, not anything in cotdata. Every `schtasks` line below uses the real name so you can paste it; on a fresh box `marketdata bars` is the better name and everything here still applies unchanged.

```bat
:: 1) Bars — fire at the Continuous Futures Final (~8:55pm ET), then repeat every 15 min
::    for 5 h. --require-final makes each repeat a cheap no-op until Norgate has actually
::    pulled the Finals. /RI and /DU are what make this a poll: without them the task gets
::    exactly one attempt per night. See "Polling with a repeating trigger" below.
schtasks /Create /TN "cotdata prices" /TR "<DIR>\run-prices.cmd" /SC DAILY /ST 20:55 /RI 15 /DU 0005:00

:: 2) COT — daily morning catch-up for holiday-delayed releases and as a safety net
schtasks /Create /TN "cotdata COT (catch-up)" /TR "<DIR>\run-cot.cmd" /SC DAILY /ST 08:10

:: 3) Equities bars — Yahoo has the session's daily bar shortly after the 16:00 ET
::    close, so this needs neither a finals gate nor a repetition. Weekdays only, and
::    17:30 keeps it clear of the 20:55 futures task so the two never run their
::    replica syncs at the same time. Its retry lives INSIDE run-equities.cmd.
schtasks /Create /TN "marketdata equities" /TR "<DIR>\run-equities.cmd" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 17:30

:: 4) Vintage (OPTIONAL) — as-published capture, ~90 min after the 15:30 ET release.
::    Daily is deliberate: almost every request 304s, so it is nearly free, and it
::    tightens the observed release date from a 7-day bound to a 1-day one.
schtasks /Create /TN "cotdata vintage" /TR "<DIR>\run-vintage.cmd" /SC DAILY /ST 17:00
```

> **Substitute `<DIR>` before running these** — with the real folder holding your `.cmd` files, e.g. `C:\Users\you\code\cotdata\scheduler`. `schtasks` takes the quoted `/TR` value as a literal string and **does not check the file exists**, so a leftover `"<DIR>\run-cot.cmd"` is accepted without error and creates a task that fails only when it fires. Verify each task points somewhere real:
> ```powershell
> Get-ScheduledTask -TaskName "cotdata*" | Select-Object TaskName, @{n='Action';e={$_.Actions.Execute}}
> ```
> Every `Action` should be a full path to an existing `.cmd`. Fix a stray placeholder in place with `schtasks /Change /TN "cotdata COT (catch-up)" /TR "C:\real\path\run-cot.cmd"`.

> [!CAUTION]
> **Do not repoint the `cotdata prices` task at a chain wrapper on the strength of
> crowdmon's scheduling page.** That page
> ([`crowdmon/docs/WINDOWS_SCHEDULING.md`](https://github.com/mspinola/crowdmon/blob/main/docs/WINDOWS_SCHEDULING.md))
> instructs `schtasks /Change /TN "cotdata prices" /TR "...\run-nightly.cmd"` so a panel
> publish can be chained behind the bars. crowdmon was **deprecated on 2026-08-07** and the
> chain was never installed on any box: there is no `run-nightly.cmd` and no
> `run-publish.cmd` for it to call. Running that command would point this box's futures
> producer at a file that does not exist, and it fails quietly — the task reports whatever
> the missing wrapper returns and the store simply stops advancing.
>
> The page is still online because crowdmon's `DEPRECATED.md` §2 keeps every file for
> citation. Treat it as a record of a design, not as instructions.

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

**Event-driven bars with `--require-final`.** The producer reads two Norgate databases: **Continuous Futures** (the `&ES` / `_CCB` series) and **Futures** (the individual `ES-2026H` contracts used to reconstruct volume). Their **Final** prices land ~8:40pm ET (Futures) and ~8:55pm ET (Continuous Futures), but your Norgate Data Updater still has to *pull* them on its next poll. Rather than guess a fixed time, `--require-final` asks a **data** question: does Norgate hold a NEWER settled bar than the store already does, for a quorum of liquid reference symbols? That needs no wall-clock cutoff and no trading calendar, so it is immune to Norgate's publish-time drift — an early publish is caught early and a late one simply defers. (It replaced a fixed `--final-cutoff`, which broke in production on 2026-07-27 when Norgate finalized one database at 8:49pm and the check demanded 8:55pm for both. See [design/finals_ready_data_driven.md](design/finals_ready_data_driven.md).) Until it is ready the run **defers with a non-zero exit** having fetched nothing, so the repeating trigger below turns "fire at 8:55pm" into "run the moment NDU has the Finals."

### Polling with a repeating trigger

This is the easiest setting on the page to get wrong, because the wrong version looks right in the GUI and then fails silently for months.

> [!WARNING]
> **"If the task fails, restart every N minutes" does not fire on a non-zero exit code from your script.** It covers the scheduler failing to *launch* the action. A run whose action returns 1 is recorded as event 102, *"Task Scheduler successfully finished"*, and no restart is scheduled.
>
> Earlier revisions of this page recommended restart-on-failure as the retry mechanism for the bars task. That advice was wrong, and it was wrong in production. Measured on the reference box, which had `RestartCount 20` / `RestartInterval PT15M` set on that task: from 2026-08-12 to 08-15 the action returned exit 1 (a `--require-final` defer) and the task was launched **exactly once each night** — four consecutive nights, no retry, no bars captured. Events 111 and 322–324, the restart and queue events, never appeared at all. The store caught up on 08-16, when a run finally found Norgate ahead of it.
>
> That self-heal is why the failure stayed invisible. The data was never permanently wrong, only a day late, so `--check` a week later looked fine and nothing ever alerted.

A **repetition on the trigger** fires on schedule regardless of what the previous run returned, which is exactly what a poll needs. `schtasks` sets it on a daily schedule with `/RI` (interval in minutes) and `/DU` (duration, `HHHH:MM`):

```bat
:: convert an existing task in place
schtasks /Change /TN "cotdata prices" /RI 15 /DU 0005:00
```

That produces `<Repetition><Interval>PT15M</Interval><Duration>PT5H</Duration></Repetition>` on the trigger — the same shape the Friday COT poller above already uses, and the reason that one has always worked while the bars task did not.

(GUI equivalent: task → **Triggers** tab → edit the trigger → check *"Repeat task every: 15 minutes for a duration of: 5 hours."* Note that this is the **Triggers** tab. Restart-on-failure lives on the **Settings** tab, which is a different mechanism and not the one you want.)

**Pick the duration from when the source could plausibly arrive**, not from how long you feel like retrying. Five hours from 8:55pm covers NDU pulling the Finals on any normal night with room for a late publish; the Friday COT poller needs only 45 minutes because the CFTC release time varies by minutes rather than hours. On a genuine no-session day every repeat defers and the window simply closes, harmlessly.

**Leave `MultipleInstances` at `IgnoreNew`** (the default) so a run that overruns its interval is not joined by a second copy. Two concurrent bar runs would race on the same parquet — and in a wrapper that chains the replica syncs, on the same `robocopy /MIR` and `rsync --delete`.

**The COT tasks are a separate judgement.** The Friday poller already repeats and is correct as it stands. The daily catch-up has no repetition and needs none to do its job: `cotdata-cot --cot-all` exits 0 on a pre-release no-op, so there is nothing to poll *for*. Its restart-on-failure setting only ever mattered for transient CFTC fetch errors — which, per the warning above, it never actually retried. If you want that retry, give it a short repetition (`/RI 10 /DU 0001:00`); otherwise drop the restart setting, so it stops implying a guarantee it does not provide.

**View / manage the jobs** any time in the Windows **Task Scheduler** GUI — press `Win + R` and run `taskschd.msc`, or open *Task Scheduler* from the Start menu — then look under **Task Scheduler Library** for the `cotdata …` tasks.

## Testing your tasks

Test in three layers: fire the task, read the result, then confirm it actually wrote data. The third layer is the one that matters — for cotdata the exit code alone is not a reliable success signal (see below).

### 1. Fire the task on demand

Don't wait for the trigger — run it now:

```bat
schtasks /Run /TN "cotdata prices"
```

(Or in `taskschd.msc`: right-click the task → **Run**.) Running the *task* rather than the `.cmd` by hand is the stronger test: it exercises the scheduler's own account, environment, and working directory, which is where scheduled runs usually differ from your interactive shell.

### 2. Read what happened

```bat
schtasks /Query /TN "cotdata prices" /V /FO LIST
```

Check **Last Run Time** and **Last Result**. For per-run detail, enable history once (right-click the task or the library root → **Enable All Tasks History**) and read the task's **History** tab.

### 3. Confirm data actually landed (the authoritative check)

**Don't trust `Last Result` in either direction.** A `--require-final` price run *defers with a non-zero exit* when Norgate's Finals aren't in yet — by design (see [Task shows success but wrote nothing](#task-shows-success-but-wrote-nothing)). Under a repeating trigger that is the *normal* end state for a healthy night: the repeat that captured succeeded hours ago, and every repeat after it defers, so the **last** result you see is almost always non-zero. A red `Last Result` on the bars task is therefore not evidence of anything on its own. Check the store instead:

```bat
cotdata-update --check
```

Confirm the relevant `newest data` date advanced (and `last write (UTC)` is recent). A weekend `prices` row showing Friday's date "2d behind" is correct — markets were closed. COT rows tracking the latest Tuesday date are current, since COT is Tuesday-dated and released the following Friday. Per-entry `⚠ … behind` warnings for individual delisted or thin contracts are expected and don't mean the run failed; judge the run by the aggregate `newest data` dates.

### Testing the timing and conditions

- **A daytime bars run usually only proves the wrapper resolves** — if last night captured, the store already holds the newest settled bar, so a daytime run finds nothing newer and defers. The exception is worth knowing: if last night's run *failed or never fired*, a daytime run finds Norgate ahead of the store and captures immediately, so a missed night self-heals at the next trigger rather than waiting for the evening. To exercise the write path on demand regardless, run `marketdata-update --bars --domain futures` by hand (no `--require-final`), or fire the task after ~8:55pm ET.
- **Test the trigger itself** by moving it a couple of minutes out, watching it fire, then setting it back:
  ```bat
  schtasks /Change /TN "cotdata prices" /ST 14:20
  :: watch it run, then restore
  schtasks /Change /TN "cotdata prices" /ST 20:55
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

`marketdata-update` exits non-zero on a hard fetch error, but a **deferred** `--require-final` run (NDU hasn't pulled the Finals yet) also exits non-zero — that's by design, not a bug, and the [repeating trigger](#polling-with-a-repeating-trigger) is what turns those into a working poll loop. Don't "fix" this by making the wrapper swallow the exit code: the repeats would still fire, but you would lose the only per-run signal separating a defer from a capture, and every repeat would do a full metadata fetch and replica sync instead of a cheap gate check.

To confirm a run actually wrote data, check the **marketdata** store rather than trusting Task Scheduler's Last Run Result alone: `marketdata-update --check`, and confirm the futures `last_date` advanced. **Not** cotdata's `status.json` — this line used to say `newest_data.prices` there, which is the retired domain ADR-0007 left behind. It is frozen at the cutover date, so it can never confirm anything about a bar run, and watching it shows a stall that is not happening (or hides one that is). See [Operations](../README.md#operations) in the README.

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

### "Restart on failure" is set but nothing ever retries

Working as designed, and not the design you wanted. That setting fires when the scheduler cannot *launch* the action, not when your script exits non-zero — see [Polling with a repeating trigger](#polling-with-a-repeating-trigger) for the measurement and the fix. If the bars task keeps missing the Finals window, check the **Triggers** tab for a repetition rather than the **Settings** tab for a restart count.

Worth knowing separately: `schtasks /Create` cannot set restart-on-failure at all — it is PowerShell-only (`Set-ScheduledTask` / `New-ScheduledTaskSettingsSet`) or GUI-only — so a task scripted purely with `schtasks` never had it in the first place. `schtasks` *can* set trigger repetition, with `/RI` and `/DU`.

### Reference

- [Task Scheduler error and success constants](https://docs.microsoft.com/en-us/windows/win32/taskschd/task-scheduler-error-and-success-constants) — decode a numeric Last Run Result code
- Event Viewer: `eventvwr.msc` → Windows Logs → Application, filter by source `TaskScheduler`
