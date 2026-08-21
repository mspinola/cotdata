@echo off
REM marketdata EQUITIES bar update wrapper for Windows Task Scheduler.
REM Copy this file into your scheduler folder and overwrite the two markers below.
REM Do NOT put angle brackets in a .cmd file: cmd reads them as redirection and
REM the file fails with "The syntax of the command is incorrect" even on comment
REM lines, which is why these are plain-text markers you replace.
REM   REPLACE_WITH_MARKETDATA_STORE_PATH = your BAR store  e.g. C:\Users\you\marketdata_store
REM   REPLACE_WITH_VENV_PATH             = your venv       e.g. C:\Users\you\code\marketdata\.venv
REM
REM WHY THIS IS A SEPARATE TASK AND NOT A STEP IN run-prices.cmd
REM ------------------------------------------------------------------------
REM Three reasons, any one of which is sufficient.
REM
REM 1. run-prices.cmd's first command is the gated futures fetch, which defers
REM    with exit 1 the moment the store already holds Norgate's newest settled
REM    session -- and the wrapper carries that code straight out, by design. Under
REM    the repeating trigger that task polls every 15 min for 5 h, so on every
REM    repeat after the futures half has captured, the wrapper exits at line one.
REM    Anything chained behind it is unreachable on those repeats: equities would
REM    get exactly one attempt per night with no retry.
REM
REM 2. The two halves fail differently. `--bars --domain equities` returns ok only
REM    when failed == 0 (providers/yfinance.py: `"ok": failed == 0`), so a single
REM    flaky Yahoo symbol fails the whole run. Chained into run-prices.cmd that
REM    transient would abort the sync and push, and the futures bars written that
REM    night would sit on the producer and never reach the replicas. One vendor
REM    hiccup should not strand the other vendor's good data.
REM
REM 3. Nothing here needs Norgate's finals. Yahoo has the session's daily bar
REM    shortly after the 16:00 ET close, so this task runs at 17:30 ET and is done
REM    -- retries included -- before run-prices.cmd starts. That separation is
REM    deliberate: both wrappers end by calling sync-store.cmd (robocopy /MIR) and
REM    push-to-server.cmd (rsync --delete) against the same replicas, and two of
REM    those running concurrently is a race nobody wants to debug.
REM
REM WHY NO --require-final
REM ------------------------------------------------------------------------
REM It is futures-only and marketdata REFUSES it here rather than ignoring it:
REM update.py exits 2 with "--require-final is futures-only. yfinance publishes no
REM settled-versus-interim distinction". There is no settled/interim flag to gate
REM on, so the protection comes from cadence instead of a gate. It does: the
REM yfinance provider fetches period="max" and store.write_bars replaces the whole
REM parquet, so every run restates the full history. If a run ever captures an
REM in-progress or later-corrected bar, the NEXT day's run overwrites it. That
REM self-healing is why this is DAILY and not weekly or monthly -- on a monthly
REM cadence a bad capture would sit in the store for a month, unmarked, because
REM the store keeps no per-bar record of whether a value was provisional.
REM
REM WHY NO --metadata
REM ------------------------------------------------------------------------
REM --metadata fetches FUTURES contract specs from Norgate. It is unrelated to the
REM equities half, and run-prices.cmd already runs it nightly.
REM
REM WHY THE RETRY IS IN HERE AND NOT IN THE TASK
REM ------------------------------------------------------------------------
REM Task Scheduler's "if the task fails, restart every N minutes" does NOT fire on
REM a non-zero exit code from the action. It covers the engine failing to LAUNCH
REM the action. A run whose action returns 1 is logged as event 102, "Task
REM Scheduler successfully finished", and no restart is scheduled -- measured on
REM the reference box, where the futures task deferred on four consecutive nights
REM (2026-08-12..15) and was launched exactly once on each. See
REM docs/WINDOWS_SCHEDULING.md, "Polling with a repeating trigger". So the retry
REM has to live where it can actually run: here.
REM
REM A repetition trigger -- the futures task's answer -- would be the wrong shape
REM here. Equities have no --require-final gate, so a repeat after a success would
REM re-fetch every symbol and re-run both replica syncs rather than deferring
REM cheaply the way the futures task does.
REM
REM powershell Start-Sleep, not `timeout /t`: timeout reads the console and fails
REM with "Input redirection is not supported" under a scheduled task, which has
REM none. `ping -n` is the other classic dodge; Start-Sleep just says what it does.
REM
REM `if errorlevel 1` tests >= 1 and needs no expansion, so it is safe here.
REM `|| exit /b %ERRORLEVEL%` would NOT be: cmd expands %ERRORLEVEL% when it parses
REM the line, which is BEFORE the command on that line has run, so it would return
REM the previous command's code. On its own line, after the command, it is correct.
setlocal
set "MARKETDATA_STORE=REPLACE_WITH_MARKETDATA_STORE_PATH"
set "MDEXE=REPLACE_WITH_VENV_PATH\Scripts\marketdata-update.exe"

REM Unscoped: every equities symbol the registry carries. --symbols would freeze
REM the universe at whatever was in the store the day this file was written, and a
REM symbol added to registry.yaml would then never be fetched by the only task
REM that fetches equities.
set "ATTEMPTS=3"
set "ATTEMPT=0"

:fetch
set /a ATTEMPT+=1
"%MDEXE%" --bars --domain equities
if not errorlevel 1 goto :fetched
REM Capture the code BEFORE anything else can clear it. `if` does not disturb
REM ERRORLEVEL, so the test above is safe to run first.
set "RC=%ERRORLEVEL%"
if %ATTEMPT% GEQ %ATTEMPTS% (
  echo equities fetch failed after %ATTEMPT% attempts, last code %RC% -- not syncing
  exit /b %RC%
)
echo equities fetch failed with code %RC%, retrying in 5 min ^(attempt %ATTEMPT% of %ATTEMPTS%^)
powershell -NoProfile -NonInteractive -Command "Start-Sleep -Seconds 300"
goto :fetch

:fetched

REM ---------------------------------------------------------------------------
REM Chained replica syncs, same discipline and same order as run-prices.cmd: the
REM local-network sync first, the remote push second, so the near replica is
REM current even on a day the server is unreachable. Both scripts mirror BOTH
REM stores (ADR-0007), so the COT and futures passes here are cheap no-op rescans.
REM
REM The guard above means these are reached only when every symbol wrote. A
REM partial equities fetch is not mirrored: the retry loop re-runs the whole
REM full-history fetch, and the sync goes with the run that finally succeeds.
call "REPLACE_WITH_SCHEDULER_DIR\sync-store.cmd"
if errorlevel 1 exit /b %ERRORLEVEL%

call "REPLACE_WITH_SCHEDULER_DIR\push-to-server.cmd"
exit /b %ERRORLEVEL%
