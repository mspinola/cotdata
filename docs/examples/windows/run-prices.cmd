@echo off
REM marketdata bar update wrapper for Windows Task Scheduler.
REM Copy this file into your scheduler folder and overwrite the two markers below.
REM Do NOT put angle brackets in a .cmd file: cmd reads them as redirection and
REM the file fails with "The syntax of the command is incorrect" even on comment
REM lines, which is why these are plain-text markers you replace.
REM   REPLACE_WITH_MARKETDATA_STORE_PATH = your BAR store  e.g. C:\Users\you\marketdata_store
REM   REPLACE_WITH_VENV_PATH             = your venv       e.g. C:\Users\you\code\cotdata\.venv
REM
REM ADR-0007 moved bar production out of cotdata: this now runs marketdata-update
REM against MARKETDATA_STORE, which is a DIFFERENT directory from COTDATA_STORE,
REM not an alias for it. The COT half still runs from run-cot.cmd, so this box
REM cannot become a second COT producer racing whatever already does that job.
REM
REM --require-final defers (exits non-zero, having fetched nothing) until Norgate
REM holds a newer settled bar than the store does; pair it with Task Scheduler
REM "restart on failure". See docs/WINDOWS_SCHEDULING.md.
REM
REM THE EXIT CODE IS THE WHOLE POINT, so read the two lines that carry it before
REM editing this file. A .cmd exits with the code of its LAST command. An earlier
REM version of this wrapper ran --metadata after --bars with no guard, so a
REM deferral (exit 1) was overwritten by --metadata's exit 0: Task Scheduler
REM recorded SUCCESS, restart-on-failure never fired, and the task sat until the
REM next day having fetched nothing. The gate worked and the wrapper discarded its
REM answer -- exactly the stale-bar failure --require-final exists to prevent.
REM
REM `if errorlevel 1` tests >= 1 and needs no expansion, so it is safe here.
REM `|| exit /b %ERRORLEVEL%` would NOT be: cmd expands %ERRORLEVEL% when it parses
REM the line, which is BEFORE the command on that line has run, so it would return
REM the previous command's code. On its own line, after the command, it is correct.
setlocal
set "MARKETDATA_STORE=REPLACE_WITH_MARKETDATA_STORE_PATH"
set "MDEXE=REPLACE_WITH_VENV_PATH\Scripts\marketdata-update.exe"

"%MDEXE%" --bars --domain futures --require-final
REM Stop here on a defer OR a failure, and keep the code. Skipping --metadata on a
REM defer is deliberate as well as convenient: restart-on-failure turns this task
REM into a poll loop, and each retry should be the cheap gate check rather than a
REM full contract-spec fetch of every symbol against NDU.
if errorlevel 1 exit /b %ERRORLEVEL%

"%MDEXE%" --metadata
exit /b %ERRORLEVEL%
