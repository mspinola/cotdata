@echo off
REM cotdata store push to the REMOTE Linux dash server, over rsync + SSH.
REM Chained after the producer task (like sync-store.cmd) so it fires at a
REM known-consistent moment rather than on a timer that might land mid-run.
REM
REM Why not robocopy here: robocopy cannot speak SSH, and the dash server is a
REM remote VPS, so SMB is off the table (never expose SMB over the internet).
REM This uses rsync, which needs a packaged rsync ON WINDOWS. Two common ones:
REM   cwRsync -> cygwin-style paths, e.g.  /cygdrive/c/Users/you/cotdata_store
REM   WSL     -> /mnt/c-style paths,  e.g.  /mnt/c/Users/you/cotdata_store
REM This file is written for cwRsync. For WSL, prefix the rsync lines with `wsl `,
REM swap /cygdrive/c for /mnt/c, and drop the RSYNC= path (use bare `rsync`).
REM
REM Overwrite the markers below. Do NOT use angle brackets in a .cmd file: cmd
REM reads them as redirection and the file fails even on comment lines.
REM   REPLACE_WITH_STORE_PATH_CYG = source store, cygdrive form
REM                                 e.g. /cygdrive/c/Users/you/cotdata_store
REM   REPLACE_WITH_SSH_KEY_CYG    = batch SSH private key, cygdrive form
REM                                 e.g. /cygdrive/c/Users/you/.ssh/cotdata_push
REM   REPLACE_WITH_REMOTE         = user@host:/path/to/store  (no trailing slash)
REM                                 e.g. deploy@dash.example.com:/srv/cotdata_store
REM See docs/SYNCING.md ("Dash store") for the exclusions and the one-time cutover.

setlocal
set "RSYNC=C:\Program Files\cwRsync\bin\rsync.exe"
set "SRC=REPLACE_WITH_STORE_PATH_CYG"
set "KEY=REPLACE_WITH_SSH_KEY_CYG"
set "DEST=REPLACE_WITH_REMOTE"
set "SSH=ssh -i %KEY% -o BatchMode=yes -o StrictHostKeyChecking=accept-new"

REM Data first, manifests last, so a manifest never announces parquet that has not
REM landed (harmless if reversed; get_prices reads parquet directly). --delete makes
REM this a true mirror. The exclusions match the Mac push:
REM   _cache, _raw   producer-internal; _raw/databento (the paid databento bronze)
REM                  rides under _raw and so is excluded, per ADR-0006.
REM   citpy          consumer-owned notes on the server; excluding it from --delete
REM                  is what stops the mirror from wiping them.
REM   manifest.json  legacy aggregate, resolved last-writer-wins across halves.
"%RSYNC%" -az --delete ^
  --exclude "_cache/" --exclude "_raw/" --exclude "citpy/" --exclude "manifest.json" ^
  --exclude "manifests/" ^
  -e "%SSH%" "%SRC%/" "%DEST%/"
if %ERRORLEVEL% NEQ 0 ( echo push FAILED, rsync code %ERRORLEVEL% & exit /b %ERRORLEVEL% )

REM manifests/ last, without --delete: the per-half files are disjoint and merge.
"%RSYNC%" -az -e "%SSH%" "%SRC%/manifests/" "%DEST%/manifests/"
if %ERRORLEVEL% NEQ 0 ( echo manifests push FAILED, rsync code %ERRORLEVEL% & exit /b %ERRORLEVEL% )

REM rsync exits 0 on success and non-zero on error, which Task Scheduler already
REM treats as failure, so no robocopy-style exit-code normalisation is needed.
echo push ok
exit /b 0
