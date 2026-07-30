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
setlocal
set COTDATA_STORE=REPLACE_WITH_STORE_PATH
REM Optional: identify yourself to CFTC. Defaults to the repo URL if unset.
REM set COTDATA_USER_AGENT=cotdata-vintage/0.1 (+contact you@example.com)

REM Default path = current year's three annual reports + the Legacy weekly static.
REM The weekly static is fetched for its HTTP Last-Modified, which is a true
REM publication timestamp rather than a polling-interval approximation.
"REPLACE_WITH_VENV_PATH\Scripts\cotdata-vintage.exe" fetch
if %ERRORLEVEL% NEQ 0 (
  echo vintage fetch FAILED with code %ERRORLEVEL%
  exit /b %ERRORLEVEL%
)

REM Parse whatever was just retained into change-only observations + revisions.
REM Safe to re-run: re-ingesting identical bytes writes zero rows.
"REPLACE_WITH_VENV_PATH\Scripts\cotdata-vintage.exe" ingest --pending
if %ERRORLEVEL% NEQ 0 (
  echo vintage ingest FAILED with code %ERRORLEVEL%
  exit /b %ERRORLEVEL%
)

echo vintage ok
exit /b 0
