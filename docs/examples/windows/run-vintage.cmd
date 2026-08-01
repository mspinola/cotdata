@echo off
REM cotdata VINTAGE capture wrapper for Windows Task Scheduler.
REM
REM ============================================================================
REM  EDIT EXACTLY TWO LINES, just below `setlocal`. Nothing else in this file.
REM ============================================================================
REM
REM An earlier version of this wrapper repeated the two paths inline, NINETEEN times
REM between them. On 2026-07-31 that cost a whole week of capture: the task reported
REM success at 17:00, wrote nothing anywhere, and the cause was a single unreplaced marker
REM in the preflight block while the other seventeen were correct. Both executables existed
REM at exactly the path the operator expected, which is what made it so hard to see.
REM
REM So the paths are now set ONCE into %VENV% and %COTDATA_STORE%, and a guard below
REM refuses to run if either was left as its marker. A find-and-replace that misses one
REM occurrence is not an operator mistake worth diagnosing; it is a file worth fixing.
REM
REM Do NOT put angle brackets in a .cmd file: cmd reads them as redirection and the file
REM fails with "The syntax of the command is incorrect" even on comment lines, which is why
REM these are plain-text markers you replace rather than bracketed placeholders.
REM
REM WHY THIS RUNS ON THE PRODUCER, not on a replica: vintage capture fetches from
REM CFTC, so it is a producer action and belongs beside the COT half. It also has to
REM be here for safety -- the replicas are fed by robocopy /MIR, which DELETES
REM destination-only files, so a vintage tree written on the Mac would be wiped by the
REM next sync. Written here, it propagates outward like any other store content.
REM (A replica that must capture anyway needs COTDATA_VINTAGE_ROOT pointing outside
REM the mirrored store. See docs/SYNCING.md.)
REM
REM SCHEDULE: DAILY, not weekly. Almost everything returns 304, so a daily run costs
REM close to nothing, and it buys three things a weekly run does not:
REM   - holiday-shifted releases are caught with no schedule logic
REM   - backlog catch-up publications are caught
REM   - observed_at tightens from a 7-day bound to a 1-day bound, which directly
REM     improves release-date quality (observed is the top of the precedence order)
REM ~17:00 ET puts capture within about ninety minutes of the 15:30 ET publication.
REM
REM Give the trigger a REPEAT-UNTIL-SUCCESS, the same as the COT release task. A daily
REM trigger with no repeat means one failure costs a day; with a repeat it costs minutes.
REM
REM ORDERING: run this AFTER run-cot.cmd, then chain the sync scripts after it, so a
REM single sync carries both the current-state update and the new vintage snapshot.
REM
REM NOTIFICATION: Task Scheduler discards stdout, so everything below is ALSO appended to
REM a log file, and `ingest` exits NON-ZERO whenever it records a revision or spots a
REM closed-year restatement. That is a notification, not a failure -- the data is already
REM committed. Task Scheduler's own "Send an e-mail" / "Display a message" actions are
REM DEPRECATED and non-functional on Windows 8 / Server 2012 and later, so a non-zero exit
REM shows up only passively as Last Run Result. This script therefore does its own
REM notifying: on any run that records a revision it writes a MARKER FILE,
REM
REM     <store>\vintage\REVISIONS_<yyyy-MM-dd>.txt
REM
REM holding that run's output. It lives inside vintage\, so the existing robocopy /MIR push
REM carries it to the Mac and the alert shows up on the machine you actually work on.
REM
REM IF THE TASK REPORTS SUCCESS BUT NOTHING APPEARS, read these two, in this order:
REM   1. vintage-preflight.log, beside this script. Written when a path check fails, which
REM      is the one failure that produces no other trace anywhere.
REM   2. Last Run Result on the task itself:
REM        0x2  a path marker was never replaced
REM        0x3  the store path does not exist
REM        0x2331 (9009)  the venv has no cotdata-vintage.exe / cotdata-schedule.exe
REM      All three mean this script exited before doing anything, and Task Scheduler still
REM      calls that a successful run.
setlocal

REM ==== THE TWO LINES TO EDIT =================================================
set COTDATA_STORE=REPLACE_WITH_STORE_PATH
set VENV=REPLACE_WITH_VENV_PATH
REM   COTDATA_STORE = your data store   e.g. C:\Users\you\cotdata_store
REM   VENV          = your cotdata venv  e.g. C:\Users\you\code\cotdata\.venv
REM   No trailing backslash on either. `where cotdata-vintage` prints VENV\Scripts\...
REM ============================================================================

REM PREFLIGHT LOG, deliberately beside THIS SCRIPT rather than inside the store.
REM
REM Learned the hard way 2026-07-31: a preflight failure echoed only to stdout is INVISIBLE.
REM Task Scheduler discards stdout, so the run does nothing, writes nothing, and still
REM reports success. History shows a clean Task Started / Action completed / Task completed,
REM and the only trace is the Last Run Result code, which nobody reads because the task
REM looks fine. The check fired correctly and its explanation went to the one place that
REM cannot be read.
REM
REM It must NOT live inside the store, because the likeliest thing preflight catches IS a
REM wrong store path, and a log written under a wrong path is equally lost. %~dp0 is the
REM folder this .cmd sits in: it exists by definition, and it is where you already are.
set PREFLIGHT_LOG=%~dp0vintage-preflight.log

set VINTAGE_LOG=%COTDATA_STORE%\vintage\run.log
set VINTAGE_RUNOUT=%COTDATA_STORE%\vintage\.last_ingest.tmp

REM Locale-independent date. %DATE% is formatted per the machine's regional settings, so it
REM is not safe in a filename; PowerShell gives a stable yyyy-MM-dd on any box.
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set TODAY=%%i
set VINTAGE_MARKER=%COTDATA_STORE%\vintage\REVISIONS_%TODAY%.txt

REM ---- Preflight -------------------------------------------------------------------
REM Runs BEFORE the mkdir below. A half-edited copy would otherwise create a stray folder
REM literally named "REPLACE_WITH_STORE_PATH\vintage" in the task's working directory,
REM write a whole capture into it, and sync nothing, which is indistinguishable from a task
REM that ran and did nothing.
REM Matched on the PREFIX "REPLACE_WITH" rather than on either full marker, and that is
REM load-bearing rather than stylistic. The obvious way to write this guard is
REM `if "%COTDATA_STORE%"=="REPLACE_WITH_STORE_PATH"`, but then a global find-and-replace
REM of the marker (the single most likely way anyone edits this file) rewrites the guard's
REM own comparison string too, the test becomes "does the path equal itself", and it fires
REM on every run. A guard destroyed by the exact operation it exists to protect is worse
REM than no guard. The prefix is not itself a marker, so nothing rewrites it.
echo %COTDATA_STORE%| findstr /b /c:"REPLACE_WITH" >nul
if not errorlevel 1 (
  echo [%DATE% %TIME%] PREFLIGHT FAILED>> "%PREFLIGHT_LOG%"
  echo   COTDATA_STORE was never edited: it is still a placeholder.>> "%PREFLIGHT_LOG%"
  echo   Edit the two marked lines near the top of "%~f0".>> "%PREFLIGHT_LOG%"
  echo PREFLIGHT FAILED: COTDATA_STORE not set. See "%PREFLIGHT_LOG%"
  exit /b 2
)
echo %VENV%| findstr /b /c:"REPLACE_WITH" >nul
if not errorlevel 1 (
  echo [%DATE% %TIME%] PREFLIGHT FAILED>> "%PREFLIGHT_LOG%"
  echo   VENV was never edited: it is still a placeholder.>> "%PREFLIGHT_LOG%"
  echo   Run `where cotdata-vintage` in your activated venv; VENV is that path>> "%PREFLIGHT_LOG%"
  echo   minus the trailing \Scripts\cotdata-vintage.exe.>> "%PREFLIGHT_LOG%"
  echo PREFLIGHT FAILED: VENV not set. See "%PREFLIGHT_LOG%"
  exit /b 2
)
if not exist "%COTDATA_STORE%" (
  echo [%DATE% %TIME%] PREFLIGHT FAILED>> "%PREFLIGHT_LOG%"
  echo   store path does not exist: %COTDATA_STORE%>> "%PREFLIGHT_LOG%"
  echo PREFLIGHT FAILED: store path does not exist. See "%PREFLIGHT_LOG%"
  exit /b 3
)
REM cotdata-vintage and cotdata-schedule are NEWER than the rest of the CLI, so a venv
REM installed before they existed has cotdata-update but not these two, and every step
REM below would fail with an unhelpful "cannot find path". Both are checked: they are
REM separate console entry points, so one can exist without the other.
if not exist "%VENV%\Scripts\cotdata-vintage.exe" (
  echo [%DATE% %TIME%] PREFLIGHT FAILED>> "%PREFLIGHT_LOG%"
  echo   cotdata-vintage.exe not found in %VENV%\Scripts\>> "%PREFLIGHT_LOG%"
  echo   fix: cd to your cotdata checkout, then: git pull ^&^& .venv\Scripts\pip install -e .>> "%PREFLIGHT_LOG%"
  echo PREFLIGHT FAILED: cotdata-vintage.exe not found. See "%PREFLIGHT_LOG%"
  exit /b 9009
)
if not exist "%VENV%\Scripts\cotdata-schedule.exe" (
  echo [%DATE% %TIME%] PREFLIGHT FAILED>> "%PREFLIGHT_LOG%"
  echo   cotdata-schedule.exe not found in %VENV%\Scripts\>> "%PREFLIGHT_LOG%"
  echo   fix: cd to your cotdata checkout, then: git pull ^&^& .venv\Scripts\pip install -e .>> "%PREFLIGHT_LOG%"
  echo PREFLIGHT FAILED: cotdata-schedule.exe not found. See "%PREFLIGHT_LOG%"
  exit /b 9009
)

REM Preflight passed, so the store path is real and this dir can be created safely. cmd
REM opens a >> target BEFORE running its command, so without this the first redirect below
REM fails with "cannot find the path specified", the program never runs, and the task exits
REM having created nothing.
if not exist "%COTDATA_STORE%\vintage" mkdir "%COTDATA_STORE%\vintage"
REM ----------------------------------------------------------------------------------
REM Optional: identify yourself to CFTC. Defaults to the repo URL if unset.
REM set COTDATA_USER_AGENT=cotdata-vintage/0.1 (+contact you@example.com)

REM Default path = current year's three annual reports, the PRIOR year's three, and the
REM Legacy weekly static.
REM
REM The prior year is the FROZEN-YEAR TRIPWIRE. CFTC regenerates a rolling two-year window
REM and nothing older, so the prior year is re-served weekly but byte-identical: the one
REM place a content check on closed data is free. Expected result every week is exactly
REM "unchanged bytes (deduped)". Anything else is the retroactive-restatement signature and
REM raises an alert that `ingest` below turns into a non-zero exit and a marker file.
REM Costs about 7 MB of transfer per week (the other six days 304) and nothing on disk.
REM Pass --no-prior-year to turn it off.
REM
REM The FIRST run after upgrading takes minutes rather than seconds and reports roughly
REM 140,000 observations, because the prior year has never been captured. That is a one-off.
REM
REM The weekly static is fetched for its HTTP Last-Modified, which is a true
REM publication timestamp rather than a polling-interval approximation.
"%VENV%\Scripts\cotdata-vintage.exe" fetch >> "%VINTAGE_LOG%" 2>&1
if %ERRORLEVEL% NEQ 0 (
  echo vintage fetch FAILED with code %ERRORLEVEL%
  exit /b %ERRORLEVEL%
)

REM Parse whatever was just retained into change-only observations + revisions.
REM Safe to re-run: re-ingesting identical bytes writes zero rows.
REM Captured to its OWN file first, so the marker can hold just THIS run's output rather
REM than the whole appended history, then folded into the running log.
"%VENV%\Scripts\cotdata-vintage.exe" ingest --pending > "%VINTAGE_RUNOUT%" 2>&1
REM NON-ZERO HERE MEANS "revisions were recorded", not "the run broke". The data is
REM already written. Remember it, keep going, and re-raise at the end so the scheduler
REM shows the task as attention-needed.
set VINTAGE_REVISED=%ERRORLEVEL%
type "%VINTAGE_RUNOUT%" >> "%VINTAGE_LOG%"

REM Write the marker HERE, not at the end. The revision is already committed at this
REM point, so an unrelated failure in a later step must not be able to suppress the
REM alert. Appends rather than overwrites, so two revision runs on one day both survive.
if NOT "%VINTAGE_REVISED%"=="0" (
  echo ================================================================ >> "%VINTAGE_MARKER%"
  echo cotdata vintage: REVISIONS RECORDED %TODAY% >> "%VINTAGE_MARKER%"
  echo Expand with:  cotdata-vintage diff >> "%VINTAGE_MARKER%"
  echo ================================================================ >> "%VINTAGE_MARKER%"
  type "%VINTAGE_RUNOUT%" >> "%VINTAGE_MARKER%"
  echo. >> "%VINTAGE_MARKER%"
)

REM Resolve release dates. `published` reads the true publication timestamp out of the
REM weekly static just captured (its HTTP Last-Modified), which beats a poll-derived
REM `observed` bound; backfill then applies the precedence across all observations.
REM Both are idempotent, so re-running is a cheap no-op.
"%VENV%\Scripts\cotdata-schedule.exe" published >> "%VINTAGE_LOG%" 2>&1
if %ERRORLEVEL% NEQ 0 (
  echo schedule published FAILED with code %ERRORLEVEL%
  exit /b %ERRORLEVEL%
)

"%VENV%\Scripts\cotdata-schedule.exe" backfill >> "%VINTAGE_LOG%" 2>&1
if %ERRORLEVEL% NEQ 0 (
  echo schedule backfill FAILED with code %ERRORLEVEL%
  exit /b %ERRORLEVEL%
)

if NOT "%VINTAGE_REVISED%"=="0" (
  echo vintage ok, but REVISIONS WERE RECORDED
  echo   marker: "%VINTAGE_MARKER%"   ^(syncs to the Mac with the store^)
  echo   log:    "%VINTAGE_LOG%"
  type "%VINTAGE_RUNOUT%"
  del "%VINTAGE_RUNOUT%" 2>nul
  exit /b %VINTAGE_REVISED%
)
del "%VINTAGE_RUNOUT%" 2>nul
echo vintage ok, no revisions
exit /b 0
