@echo off
REM cotdata price update wrapper for Windows Task Scheduler.
REM Copy this file somewhere outside the repo (e.g. C:\Users\you\cotdata\scheduler\)
REM and replace the two <...> placeholders below with your real paths:
REM   <STORE> = your data store        e.g. C:\Users\you\cotdata_store  (or \\Mac\code\cotdata_store)
REM   <VENV>  = your cotdata virtualenv e.g. C:\Users\you\code\cotdata\.venv
REM --require-final defers (exits non-zero) until Norgate's Final prices are in;
REM pair it with Task Scheduler "restart on failure" so it polls. See
REM docs/WINDOWS_SCHEDULING.md for the full setup.
set COTDATA_STORE=<STORE>
"<VENV>\Scripts\cotdata-update.exe" --prices --metadata --require-final
