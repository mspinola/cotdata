@echo off
REM cotdata price update wrapper for Windows Task Scheduler.
REM Copy this file into your scheduler folder and overwrite the two markers below.
REM Do NOT put angle brackets in a .cmd file: cmd reads them as redirection and
REM the file fails with "The syntax of the command is incorrect" even on comment
REM lines, which is why these are plain-text markers you replace.
REM   REPLACE_WITH_STORE_PATH = your data store   e.g. C:\Users\you\cotdata_store
REM   REPLACE_WITH_VENV_PATH  = your cotdata venv  e.g. C:\Users\you\code\cotdata\.venv
REM cotdata-prices runs the PRICE half only and refuses --cot-all, so this box
REM cannot become a second COT producer racing whatever already does that job.
REM --require-final defers (exits non-zero) until Norgate's Final prices are in;
REM pair it with Task Scheduler "restart on failure". See docs/WINDOWS_SCHEDULING.md.
set COTDATA_STORE=REPLACE_WITH_STORE_PATH
"REPLACE_WITH_VENV_PATH\Scripts\cotdata-prices.exe" --prices --metadata --require-final
