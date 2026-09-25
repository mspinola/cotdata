@echo off
REM marketdata SERIES build wrapper: stage 2 of the TradingView breadth producer.
REM Copy this file into your scheduler folder and overwrite the three markers below.
REM Do NOT put angle brackets in a .cmd file: cmd reads them as redirection and
REM the file fails with "The syntax of the command is incorrect" even on comment
REM lines, which is why these are plain-text markers you replace.
REM   REPLACE_WITH_MARKETDATA_STORE_PATH = your BAR store  e.g. C:\Users\you\marketdata_store
REM   REPLACE_WITH_VENV_PATH             = your venv       e.g. C:\Users\you\code\marketdata\.venv
REM   REPLACE_WITH_SCHEDULER_DIR         = this folder     e.g. C:\Users\you\cotdata\scheduler
REM
REM WHAT RUNS THIS, AND WHY IT IS NOT A TASK SCHEDULER TASK
REM ------------------------------------------------------------------------
REM The series domain (the FOMO share, new 52-week highs and lows, the put/call
REM ratios) comes from TradingView, which has no data API. The feed reaches this
REM box through a claude.ai connector, so stage 1 is a Claude Code Desktop LOCAL
REM ROUTINE: Claude calls the connector once per registry series symbol and writes
REM each result VERBATIM under MARKETDATA_STORE\_raw\tradingview\. The routine's
REM last step is this file. See series-routine.md beside it for the routine's
REM exact instructions and the allow rules it runs under.
REM
REM Stage 2 is the one command below. marketdata-update --build-tradingview reads
REM ONLY those local files: it validates them (the connector's JSON shape, the
REM registry range per kind, strictly increasing stamps, the registry anchors),
REM refuses any bar that disagrees with a bar the store already holds (the store
REM is never rewritten by a build), appends only what the store lacks, and gates
REM the newest bar on BOTH sides of the expected session, which is the latest
REM weekday whose 16:30 ET close has passed. Older is refused as STALE and the
REM later routine is the retry. Newer is refused as UNSETTLED: fired before the
REM close, the connector serves the day in progress, and on the put/call ratios it
REM serves it a bar ahead of the breadth counts. That Close still moves, and since
REM a stored bar is never rewritten, storing it once would refuse every later
REM build of the same session until someone cleaned the store by hand. Neither
REM side writes anything.
REM
REM WHY THERE IS NO RETRY LOOP IN HERE
REM ------------------------------------------------------------------------
REM run-equities.cmd retries because its fetch hits Yahoo. This build hits
REM nothing: a refusal is either a bad raw file, which a retry cannot fix, or a
REM stale one, which only a later connector pull fixes. So the retry is the
REM SECOND ROUTINE, scheduled later the same evening, and on a good night it
REM finds the store current, writes nothing, and exits 0.
REM
REM WHEN IT RUNS, AND WHY THOSE TIMES
REM ------------------------------------------------------------------------
REM Two routines, weekdays: about 18:30 and 19:45 ET. Both sit AFTER the 17:30
REM equities task and its in-file retries (done by 17:50) and BEFORE the 20:55
REM futures task, whose repeating trigger fires every 15 minutes for five hours
REM and syncs both replicas at whichever repeat captures. This wrapper ends by
REM calling the same two sync scripts, and two mirror passes running
REM concurrently against the same replicas is a race nobody wants to debug, so
REM the series routines stay out of the futures window entirely.
REM
REM EXIT CODES, AND WHY A REFUSAL NO LONGER STOPS THE SYNC
REM ------------------------------------------------------------------------
REM   0  nothing refused.
REM   2  PARTIAL: some symbols refused, the rest are in the store and correct.
REM   1  nothing usable came of the run: every symbol refused, or a hard error.
REM
REM This wrapper syncs on 0 and on 2, and stops only on 1. It used to stop on any
REM non-zero code, and on 2026-09-25 that cost a week of data: TradingView restated
REM two put/call closes, the build refused those two symbols exactly as designed,
REM and the wrapper exited before its syncs -- so thirteen breadth series sat
REM correct on this box and five sessions stale on both replicas until a human
REM noticed. A refusal has to stop the SYMBOL, not the delivery of every other one.
REM The refusal is still visible: the wrapper exits 2, which is non-zero, and the
REM build has already printed the refused symbol and the reason.
REM
REM Testing a specific code needs equality, not `if errorlevel`, which is true for
REM N or any larger code, and so would treat 2 as 1. Two chained `if not` tests
REM are the cmd idiom, and a string compare avoids any numeric parsing surprise
REM on an empty ERRORLEVEL.
REM
REM `if errorlevel 1` tests >= 1 and needs no expansion, so it is safe below.
REM `|| exit /b %ERRORLEVEL%` would NOT be: cmd expands %ERRORLEVEL% when it parses
REM the line, which is BEFORE the command on that line has run, so it would return
REM the previous command's code. On its own line, after the command, it is correct.
setlocal
set "MARKETDATA_STORE=REPLACE_WITH_MARKETDATA_STORE_PATH"
set "MDEXE=REPLACE_WITH_VENV_PATH\Scripts\marketdata-update.exe"

REM Unscoped: every registry series symbol. A symbol with no raw files is a
REM refusal, not a skip, because the routine pulls every one of them every night
REM and a missing one means it did not.
"%MDEXE%" --build-tradingview
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" if not "%RC%"=="2" exit /b %RC%

REM ---------------------------------------------------------------------------
REM Chained replica syncs, same discipline and same order as run-prices.cmd: the
REM Mac sync first, the VPS push second, so the Mac replica is current even on a
REM day the VPS is unreachable. Both scripts mirror BOTH stores, so the COT and
REM futures passes here are cheap no-op re-scans. Reached on an "already current"
REM build too (exit 0, nothing written): a no-op mirror is cheap, and skipping it
REM would need the wrapper to tell the two exit-0 cases apart. Reached on a PARTIAL
REM build as well (exit 2), which is the whole point of the code: the store is a
REM valid state whatever refused, so mirroring it can only make a replica fresher.
REM
REM The raw JSON under _raw\tradingview never rides along: both scripts exclude
REM _raw by name at any depth, for databento's paid raw store. Keep the directory
REM name exactly _raw or both exclusions silently stop applying.
call "REPLACE_WITH_SCHEDULER_DIR\sync-store.cmd"
if errorlevel 1 exit /b %ERRORLEVEL%

call "REPLACE_WITH_SCHEDULER_DIR\push-to-server.cmd"
if errorlevel 1 exit /b %ERRORLEVEL%

REM The build's code, not the push's: a partial build that synced cleanly is still
REM a run with a refusal in it, and the operator and the verifier both want to see
REM that rather than a 0.
exit /b %RC%
