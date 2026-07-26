@echo off
REM cotdata store sync wrapper for Windows Task Scheduler.
REM Run this AFTER the producer tasks, not on its own timer, so it fires at a
REM known-consistent moment rather than possibly mid-run.
REM
REM Copy this file into your scheduler folder and overwrite the two markers below.
REM Do NOT put angle brackets in a .cmd file: cmd reads them as redirection and the
REM file fails with "The syntax of the command is incorrect" even on comment lines.
REM   REPLACE_WITH_STORE_PATH = your local store   e.g. C:\Users\you\cotdata_store
REM   REPLACE_WITH_DEST_PATH  = the sync target    e.g. Z:\cotdata_store  or
REM                             \\mac\code\cotdata_store  or a Syncthing folder
REM See docs/SYNCING.md for what each exclusion is for.

setlocal

REM /MIR mirrors (copies new + deletes removed). /XD excludes directories:
REM   _cache, _raw  databento producer-internal, rebuildable, ~70%% of the bytes
REM   citpy         written by COTMETRICS on the consumer, not by cotdata —
REM                 mirroring would delete or clobber locally-derived output
REM /XF excludes the legacy aggregate manifest: nothing writes it, and it is the one
REM file a sync would resolve last-writer-wins across two halves.
robocopy "REPLACE_WITH_STORE_PATH" "REPLACE_WITH_DEST_PATH" /MIR /R:2 /W:5 /NFL /NDL /NP ^
  /XD _cache _raw citpy ^
  /XF manifest.json

REM robocopy uses exit codes 0-7 for SUCCESS (1 = files copied, 2 = extras present,
REM 3 = both, and so on) and 8+ for failure. Task Scheduler treats any non-zero as a
REM failure, so without this every successful sync would be reported as an error and
REM "restart on failure" would loop.
if %ERRORLEVEL% GEQ 8 (
  echo sync FAILED with robocopy code %ERRORLEVEL%
  exit /b %ERRORLEVEL%
)
echo sync ok ^(robocopy code %ERRORLEVEL%^)
exit /b 0
