@echo off
REM cotdata store push to the REMOTE Linux dash server, over rsync + SSH.
REM Chained after the producer task (like sync-store.cmd) so it fires at a
REM known-consistent moment rather than on a timer that might land mid-run.
REM
REM Why not robocopy here: robocopy cannot speak SSH, and the dash server is a
REM remote VPS, so SMB is off the table (never expose SMB over the internet).
REM This uses rsync, which needs a packaged rsync ON WINDOWS. cwRsync (a Cygwin
REM build) is the tested one; `choco install rsync` installs exactly that, to
REM   rsync.exe at  C:\ProgramData\chocolatey\bin\rsync.exe
REM   ssh.exe  at  C:\ProgramData\chocolatey\lib\rsync\tools\bin\ssh.exe
REM (WSL also works: prefix the rsync lines with `wsl `, use /mnt/c paths, and a
REM  native rsync/ssh inside the distro. The Cygwin gotchas below do not apply.)
REM
REM -- Three Cygwin-rsync gotchas this file already handles ---------------------
REM 1. Use the ssh that SHIPS WITH rsync, never the native Windows OpenSSH
REM    (C:\Windows\System32\OpenSSH\ssh.exe). A Cygwin rsync driving native ssh
REM    corrupts rsync's binary stream and dies with
REM      "connection unexpectedly closed (0 bytes received so far)".
REM    Point SSH_EXE at the bundled Cygwin ssh instead.
REM 2. That Cygwin ssh has no HOME, so it cannot write the default known_hosts and
REM    warns "Failed to add the host ... (/known_hosts)". Give it an explicit
REM    writable UserKnownHostsFile (created on first connect).
REM 3. All local paths are cygdrive form: /cygdrive/c/... , including the key.
REM ----------------------------------------------------------------------------
REM
REM Overwrite the markers below. Do NOT use angle brackets in a .cmd file: cmd
REM reads them as redirection and the file fails even on comment lines.
REM   REPLACE_WITH_SSH_EXE_CYG    = the ssh that ships with rsync, cygdrive form
REM                                 e.g. /cygdrive/c/ProgramData/chocolatey/lib/rsync/tools/bin/ssh.exe
REM   REPLACE_WITH_SSH_KEY_CYG    = batch SSH private key, cygdrive form
REM                                 e.g. /cygdrive/c/Users/you/.ssh/cotdata_push
REM   REPLACE_WITH_KNOWN_HOSTS_CYG= a writable known_hosts, cygdrive form
REM                                 e.g. /cygdrive/c/Users/you/.ssh/known_hosts
REM   REPLACE_WITH_STORE_PATH_CYG = source store, cygdrive form
REM                                 e.g. /cygdrive/c/Users/you/cotdata_store
REM   REPLACE_WITH_REMOTE         = user@host:/path/to/store  (no trailing slash)
REM                                 e.g. deploy@dash.example.com:/srv/cotdata_store
REM See docs/SYNCING.md ("Dash store") for the exclusions and the one-time cutover.

setlocal
set "RSYNC=C:\ProgramData\chocolatey\bin\rsync.exe"
set "SSH_EXE=REPLACE_WITH_SSH_EXE_CYG"
set "KEY=REPLACE_WITH_SSH_KEY_CYG"
set "KNOWN=REPLACE_WITH_KNOWN_HOSTS_CYG"
set "SRC=REPLACE_WITH_STORE_PATH_CYG"
set "DEST=REPLACE_WITH_REMOTE"
set "SSH=%SSH_EXE% -i %KEY% -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=%KNOWN%"

REM Data first, manifests last, so a manifest never announces parquet that has not
REM landed (harmless if reversed; get_prices reads parquet directly). --delete makes
REM this a true mirror. The exclusions match the Mac push:
REM   _cache, _raw   producer-internal; _raw/databento (the paid databento bronze)
REM                  rides under _raw and so is excluded, per ADR-0006.
REM   citpy          consumer-owned on the server; excluding it from --delete is
REM                  what stops the mirror from wiping it.
REM   manifest.json  legacy aggregate, resolved last-writer-wins across halves.
REM   *.tmp, *.part  a producer's partial-write temps (atomic write via os.replace,
REM                  and in-flight raw downloads); never propagate a half-written file.
REM   vintage/       NOT pushed here, unlike the Mac sync. cot-analyzer reads prices
REM                  and COT, never the vintage tree, so the dash would carry roughly
REM                  1 GB/year of raw CFTC archives it never opens. The Mac keeps the
REM                  second copy instead. Drop this exclusion if something on the dash
REM                  ever consumes revisions -- and if so consider excluding only
REM                  "vintage/raw/" so the small derived tables still ride along.
"%RSYNC%" -az --delete ^
  --exclude "_cache/" --exclude "_raw/" --exclude "citpy/" ^
  --exclude "manifest.json" --exclude "*.tmp" --exclude "*.part" ^
  --exclude "manifests/" --exclude "vintage/" ^
  -e "%SSH%" "%SRC%/" "%DEST%/"
if %ERRORLEVEL% NEQ 0 ( echo push FAILED, rsync code %ERRORLEVEL% & exit /b %ERRORLEVEL% )

REM manifests/ last, without --delete: the per-half files are disjoint and merge.
"%RSYNC%" -az -e "%SSH%" "%SRC%/manifests/" "%DEST%/manifests/"
if %ERRORLEVEL% NEQ 0 ( echo manifests push FAILED, rsync code %ERRORLEVEL% & exit /b %ERRORLEVEL% )

REM rsync exits 0 on success and non-zero on error, which Task Scheduler already
REM treats as failure, so no robocopy-style exit-code normalisation is needed.
echo push ok
exit /b 0
