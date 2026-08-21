@echo off
REM Store sync wrapper for Windows Task Scheduler — BOTH stores.
REM Run this AFTER the producer tasks, not on its own timer, so it fires at a
REM known-consistent moment rather than possibly mid-run.
REM
REM Since ADR-0007 this box produces two stores and this script mirrors both:
REM   $COTDATA_STORE     CFTC positioning  (cotdata-cot --cot-all)
REM   $MARKETDATA_STORE  bars + specs      (marketdata-update --bars / --metadata)
REM They are mirrored in SEPARATE passes with SEPARATE exclusions. Do not merge
REM them into one robocopy over a shared parent: the two stores disagree about
REM manifest.json (see the pass-2 note below), and one exclusion list cannot be
REM right for both.
REM
REM Copy this file into your scheduler folder and overwrite the four markers below.
REM Do NOT put angle brackets in a .cmd file: cmd reads them as redirection and the
REM file fails with "The syntax of the command is incorrect" even on comment lines.
REM   COT_SRC   = your local COT store    e.g. C:\Users\you\cotdata_store
REM   COT_DEST  = its sync target         e.g. \\mac\code\cotdata_store
REM   BAR_SRC   = your local bar store    e.g. C:\Users\you\code\marketdata_store
REM   BAR_DEST  = its sync target         e.g. \\mac\code\marketdata_store
REM See docs/SYNCING.md for what each exclusion is for.

setlocal

set "COT_SRC=REPLACE_WITH_COTDATA_STORE_PATH"
set "COT_DEST=REPLACE_WITH_COTDATA_DEST_PATH"
set "BAR_SRC=REPLACE_WITH_MARKETDATA_STORE_PATH"
set "BAR_DEST=REPLACE_WITH_MARKETDATA_DEST_PATH"

REM ---------------------------------------------------------------------------
REM Pass 1 — the COT store.
REM
REM /MIR mirrors (copies new + deletes removed). /XD excludes directories:
REM   _cache        cotdata's cache of downloaded CFTC zips: producer-internal and
REM                 free to rebuild, and most of the bytes.
REM   _raw          pre-ADR-0007 leftover (databento's paid bronze store, now
REM                 marketdata's). Kept so a store built before the move still
REM                 excludes it; a fresh cotdata store has no such directory.
REM   citpy         consumer-owned, not written by any producer, so /MIR removes it
REM                 and no producer run brings it back. Kept as a backstop: such
REM                 files belong outside the store. See docs/SYNCING.md.
REM /XF excludes the legacy aggregate manifest: nothing writes it, and it is the one
REM file a sync would resolve last-writer-wins across two halves. cotdata's live
REM bookkeeping is the per-half files under manifests/, which are disjoint.
REM vintage/ IS carried here, deliberately and in full (including vintage/raw). Those
REM bytes are irreplaceable -- CFTC serves current state only, so a lost vintage cannot
REM be re-fetched -- and the Mac is the natural second copy. It costs roughly 1 GB/year.
REM Note the provenance index is vintage\snapshots.json, NOT manifest.json: /XF below
REM matches by NAME AT ANY DEPTH, so had it been called manifest.json this sync would
REM have silently delivered raw bytes with no index.
REM /XF also drops partial-write temps (*.tmp from parquet/JSON writes, *.part from
REM in-flight raw downloads) so a sync mid-capture never lands a truncated file.
robocopy "%COT_SRC%" "%COT_DEST%" /MIR /R:2 /W:5 /NFL /NDL /NP ^
  /XD _cache _raw citpy ^
  /XF manifest.json *.tmp *.part
set "COT_RC=%ERRORLEVEL%"

REM ---------------------------------------------------------------------------
REM Pass 2 — the bar store. A DIFFERENT exclusion list, and the difference is
REM load-bearing.
REM
REM   manifest.json is NOT excluded here. In the COT store it is a dead legacy
REM   aggregate; in the bar store it is the ONLY manifest -- marketdata keeps one
REM   file at the store root, not a manifests\ directory. Carrying pass 1's /XF
REM   over to this pass would strip the bar store's whole index in transit and
REM   deliver parquet the replica cannot enumerate. This is the same name-at-any-
REM   depth trap docs/SYNCING.md documents for vintage\snapshots.json, and it is
REM   why the two stores get two passes rather than one shared exclusion list.
REM
REM   _cache and citpy are not excluded because the bar store has neither: no
REM   marketdata provider writes a download cache, and citpy is a cotdata-store
REM   consumer artefact. Listing them would be harmless but misleading.
REM
REM   _raw IS excluded: it holds databento's append-only PAID raw store
REM   ($MARKETDATA_DATABENTO_RAW, else _raw\databento under the bar store). A
REM   replica has no use for it and re-fetching it costs money.
REM
REM Both stores commit parquet with an atomic replace, so *.tmp / *.part are the
REM same partial-write guard as above.
robocopy "%BAR_SRC%" "%BAR_DEST%" /MIR /R:2 /W:5 /NFL /NDL /NP ^
  /XD _raw ^
  /XF *.tmp *.part
set "BAR_RC=%ERRORLEVEL%"

REM Pass 2 runs even when pass 1 failed, deliberately. They mirror independent
REM stores, so aborting on the first failure would mean a COT hiccup silently
REM stops bars reaching the replica for as long as it lasts -- one broken thing
REM presenting as two. Each pass reports its own code and the exit is the worst.

REM robocopy uses exit codes 0-7 for SUCCESS (1 = files copied, 2 = extras present,
REM 3 = both, and so on) and 8+ for failure. Task Scheduler treats any non-zero as a
REM failure, so without this normalisation every successful sync would be reported
REM as an error and "restart on failure" would loop.
if %COT_RC% GEQ 8 goto :cot_failed
if %BAR_RC% GEQ 8 goto :bar_failed
echo sync ok ^(cot robocopy %COT_RC%, bars robocopy %BAR_RC%^)
exit /b 0

:cot_failed
echo COT sync FAILED with robocopy code %COT_RC%
if %BAR_RC% GEQ 8 echo bar sync ALSO FAILED with robocopy code %BAR_RC%
exit /b %COT_RC%

:bar_failed
echo bar sync FAILED with robocopy code %BAR_RC% ^(cot sync ok, code %COT_RC%^)
exit /b %BAR_RC%
