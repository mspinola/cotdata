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
set MARKETDATA_STORE=REPLACE_WITH_MARKETDATA_STORE_PATH
"REPLACE_WITH_VENV_PATH\Scripts\marketdata-update.exe" --bars --domain futures --require-final
"REPLACE_WITH_VENV_PATH\Scripts\marketdata-update.exe" --metadata
