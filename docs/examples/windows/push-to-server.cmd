@echo off
REM Store push to the REMOTE Linux dash server, over rsync + SSH — BOTH stores.
REM Chained after the producer tasks (like sync-store.cmd) so it fires at a
REM known-consistent moment rather than on a timer that might land mid-run.
REM
REM Since ADR-0007 this box produces two stores and this script pushes both:
REM   $COTDATA_STORE     CFTC positioning  (cotdata-cot --cot-all)
REM   $MARKETDATA_STORE  bars + specs      (marketdata-update --bars / --metadata)
REM Separate passes, separate exclusions -- the two stores disagree about
REM manifest.json (see the bar-store block below), so one list cannot serve both.
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
REM   REPLACE_WITH_COTDATA_STORE_CYG    = source COT store, cygdrive form
REM                                 e.g. /cygdrive/c/Users/you/cotdata_store
REM   REPLACE_WITH_MARKETDATA_STORE_CYG = source bar store, cygdrive form
REM                                 e.g. /cygdrive/c/Users/you/code/marketdata_store
REM   REPLACE_WITH_COT_REMOTE     = user@host:/path/to/cotdata_store    (no trailing slash)
REM   REPLACE_WITH_BAR_REMOTE     = user@host:/path/to/marketdata_store (no trailing slash)
REM See docs/SYNCING.md ("Dash store") for the exclusions and the one-time cutover.

setlocal
set "RSYNC=C:\ProgramData\chocolatey\bin\rsync.exe"
set "SSH_EXE=REPLACE_WITH_SSH_EXE_CYG"
set "KEY=REPLACE_WITH_SSH_KEY_CYG"
set "KNOWN=REPLACE_WITH_KNOWN_HOSTS_CYG"
set "COT_SRC=REPLACE_WITH_COTDATA_STORE_CYG"
set "COT_DEST=REPLACE_WITH_COT_REMOTE"
set "BAR_SRC=REPLACE_WITH_MARKETDATA_STORE_CYG"
set "BAR_DEST=REPLACE_WITH_BAR_REMOTE"
set "SSH=%SSH_EXE% -i %KEY% -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=%KNOWN%"

set "RC=0"
call :push_cot
call :push_bars

REM Both stores are attempted even when the first fails, deliberately: they are
REM independent, and aborting early would let a COT hiccup silently stop bars
REM reaching the dash for as long as it lasted -- one broken thing presenting as
REM two. rsync exits 0 on success and non-zero on error, which Task Scheduler
REM already treats as failure, so no robocopy-style normalisation is needed.
REM If both fail, RC carries the bar push's code -- the echo lines above name
REM each store separately, so the log is not ambiguous about which broke.
if not "%RC%"=="0" exit /b %RC%
echo push ok ^(cot + bars^)
exit /b 0


REM ===========================================================================
:push_cot
REM Data first, manifests last, so a manifest never announces parquet that has not
REM landed (harmless if reversed; readers open parquet directly). --delete makes
REM this a true mirror. The exclusions match the Mac push:
REM   _cache         cotdata's download cache of CFTC source zips, producer-internal
REM                  and free to rebuild.
REM   _raw           pre-ADR-0007 leftover -- databento's paid bronze store, owned by
REM                  marketdata now. Kept so a store built before the move still
REM                  excludes it.
REM   citpy          consumer-owned on the server; excluding it from --delete is
REM                  what stops the mirror from wiping it.
REM   manifest.json  legacy aggregate, resolved last-writer-wins across halves.
REM   *.tmp, *.part  a producer's partial-write temps (atomic write via os.replace,
REM                  and in-flight raw downloads); never propagate a half-written file.
REM   vintage/       NOT pushed here, unlike the Mac sync. cot-analyzer reads bars
REM                  and COT, never the vintage tree, so the dash would carry roughly
REM                  1 GB/year of raw CFTC archives it never opens. The Mac keeps the
REM                  second copy instead. Drop this exclusion if something on the dash
REM                  ever consumes revisions -- and if so consider excluding only
REM                  "vintage/raw/" so the small derived tables still ride along.
"%RSYNC%" -az --delete ^
  --exclude "_cache/" --exclude "_raw/" --exclude "citpy/" ^
  --exclude "manifest.json" --exclude "*.tmp" --exclude "*.part" ^
  --exclude "manifests/" --exclude "vintage/" ^
  -e "%SSH%" "%COT_SRC%/" "%COT_DEST%/"
if errorlevel 1 goto :cot_failed

REM manifests/ last, without --delete: the per-half files are disjoint and merge.
"%RSYNC%" -az -e "%SSH%" "%COT_SRC%/manifests/" "%COT_DEST%/manifests/"
if errorlevel 1 goto :cot_failed
echo cot push ok
goto :eof

:cot_failed
REM Capture the code BEFORE echoing it: an intervening command can clear
REM ERRORLEVEL, and the point of this line is to carry rsync's own code out.
set "RC=%ERRORLEVEL%"
echo COT push FAILED, rsync code %RC%
goto :eof


REM ===========================================================================
:push_bars
REM The bar store, with its OWN exclusions. Two differences from the COT push,
REM both load-bearing:
REM
REM   manifest.json is the bar store's ONLY index -- marketdata keeps one file at
REM   the store root, not a manifests/ directory. It is excluded from the --delete
REM   pass so the server's copy is not removed before the new one lands, then
REM   pushed on its own line afterwards. Reusing the COT push's exclusion list
REM   here would have excluded it outright and delivered the dash a bar store it
REM   cannot enumerate. rsync --exclude matches by name at any depth, the same
REM   trap docs/SYNCING.md documents for vintage/snapshots.json.
REM
REM   _cache and citpy are absent from the bar store -- no marketdata provider
REM   writes a download cache, and citpy is a cotdata-store consumer artefact --
REM   so listing them would be misleading rather than merely redundant. _raw IS
REM   excluded: it is databento's append-only PAID raw store
REM   ($MARKETDATA_DATABENTO_RAW, else _raw/databento under the bar store), which
REM   a replica has no use for and which costs money to re-fetch (ADR-0006).
REM
REM Note there is no vendor collision to guard against here: marketdata puts the
REM source in the path (bars/futures/norgate/ beside bars/futures/databento/), so
REM two vendors cannot contend for one file the way they could under cotdata's old
REM prices/<SYM>_<adj>.parquet. Push whichever vendors the dash should read.
"%RSYNC%" -az --delete ^
  --exclude "_raw/" --exclude "*.tmp" --exclude "*.part" ^
  --exclude "manifest.json" ^
  -e "%SSH%" "%BAR_SRC%/" "%BAR_DEST%/"
if errorlevel 1 goto :bar_failed

REM The manifest last, on its own, for the same reason manifests/ goes last above.
"%RSYNC%" -az -e "%SSH%" "%BAR_SRC%/manifest.json" "%BAR_DEST%/manifest.json"
if errorlevel 1 goto :bar_failed
echo bar push ok
goto :eof

:bar_failed
REM Capture the code BEFORE echoing it: an intervening command can clear
REM ERRORLEVEL, and the point of this line is to carry rsync's own code out.
set "RC=%ERRORLEVEL%"
echo bar push FAILED, rsync code %RC%
goto :eof
