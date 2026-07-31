@echo off
REM cotdata VINTAGE capture wrapper for Windows Task Scheduler.
REM Copy this file into your scheduler folder and overwrite the two markers below.
REM Do NOT put angle brackets in a .cmd file: cmd reads them as redirection and
REM the file fails with "The syntax of the command is incorrect" even on comment
REM lines, which is why these are plain-text markers you replace.
REM   REPLACE_WITH_STORE_PATH = your data store   e.g. C:\Users\you\cotdata_store
REM   REPLACE_WITH_VENV_PATH  = your cotdata venv  e.g. C:\Users\you\code\cotdata\.venv
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
REM ORDERING: run this AFTER run-cot.cmd, then chain the sync scripts after it, so a
REM single sync carries both the current-state update and the new vintage snapshot.
REM NOTIFICATION: Task Scheduler discards stdout, so everything below is ALSO appended to
REM a log file, and `ingest` exits NON-ZERO whenever it records a revision or spots a
REM closed-year restatement. That is a notification, not a failure -- the data is already
REM committed.
REM
REM Task Scheduler's own "Send an e-mail" / "Display a message" actions are DEPRECATED and
REM non-functional on Windows 8 / Server 2012 and later, so a non-zero exit on its own only
REM shows up passively as Last Run Result 0x1 in the Task Scheduler UI. This script therefore
REM does its own notifying: on any run that records a revision it writes a MARKER FILE,
REM
REM     <store>\vintage\REVISIONS_<yyyy-MM-dd>.txt
REM
REM holding that run's output. It lives inside vintage\, so the existing robocopy /MIR push
REM carries it to the Mac and the alert shows up on the machine you actually work on.
setlocal
set COTDATA_STORE=REPLACE_WITH_STORE_PATH
set VINTAGE_LOG=REPLACE_WITH_STORE_PATH\vintage\run.log
set VINTAGE_RUNOUT=REPLACE_WITH_STORE_PATH\vintage\.last_ingest.tmp

REM Locale-independent date. %DATE% is formatted per the machine's regional settings, so it
REM is not safe in a filename; PowerShell gives a stable yyyy-MM-dd on any box.
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set TODAY=%%i
set VINTAGE_MARKER=REPLACE_WITH_STORE_PATH\vintage\REVISIONS_%TODAY%.txt

REM The store may not exist yet on a first run; the vintage dir must, to hold the log.
if not exist "REPLACE_WITH_STORE_PATH\vintage" mkdir "REPLACE_WITH_STORE_PATH\vintage"
REM Optional: identify yourself to CFTC. Defaults to the repo URL if unset.
REM set COTDATA_USER_AGENT=cotdata-vintage/0.1 (+contact you@example.com)

REM Default path = current year's three annual reports + the Legacy weekly static.
REM The weekly static is fetched for its HTTP Last-Modified, which is a true
REM publication timestamp rather than a polling-interval approximation.
"REPLACE_WITH_VENV_PATH\Scripts\cotdata-vintage.exe" fetch >> "%VINTAGE_LOG%" 2>&1
if %ERRORLEVEL% NEQ 0 (
  echo vintage fetch FAILED with code %ERRORLEVEL%
  exit /b %ERRORLEVEL%
)

REM Parse whatever was just retained into change-only observations + revisions.
REM Safe to re-run: re-ingesting identical bytes writes zero rows.
REM Captured to its OWN file first, so the marker can hold just THIS run's output rather
REM than the whole appended history, then folded into the running log.
"REPLACE_WITH_VENV_PATH\Scripts\cotdata-vintage.exe" ingest --pending > "%VINTAGE_RUNOUT%" 2>&1
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
"REPLACE_WITH_VENV_PATH\Scripts\cotdata-schedule.exe" published >> "%VINTAGE_LOG%" 2>&1
if %ERRORLEVEL% NEQ 0 (
  echo schedule published FAILED with code %ERRORLEVEL%
  exit /b %ERRORLEVEL%
)

"REPLACE_WITH_VENV_PATH\Scripts\cotdata-schedule.exe" backfill >> "%VINTAGE_LOG%" 2>&1
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
