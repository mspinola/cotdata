@echo off
REM cotdata COT update wrapper for Windows Task Scheduler.
REM Copy this file somewhere outside the repo (e.g. C:\Users\you\cotdata\scheduler\)
REM and replace the two <...> placeholders below with your real paths:
REM   <STORE> = your data store        e.g. C:\Users\you\cotdata_store  (or \\Mac\code\cotdata_store)
REM   <VENV>  = your cotdata virtualenv e.g. C:\Users\you\code\cotdata\.venv
REM --cot-all is idempotent (HEAD-checks each CFTC zip), so re-running is cheap and
REM running before the Friday release lands is a harmless no-op. See
REM docs/WINDOWS_SCHEDULING.md for the full setup.
set COTDATA_STORE=<STORE>
"<VENV>\Scripts\cotdata-update.exe" --cot-all
