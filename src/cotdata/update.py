"""Producer CLI:  cotdata-update --prices --symbols ES NQ
                  cotdata-update --cot-all
                  cotdata-update --check        # read-only store status
                  cotdata-update --reconcile    # prune stale manifest ghosts
Writes to $COTDATA_STORE. Schedule prices nightly (after the Norgate Data
Updater) and COT weekly (Friday, after the CFTC release)."""
import argparse
import datetime as _dt

from . import config


def main() -> None:
    p = argparse.ArgumentParser(description="cotdata producer — fetch sources into the store.")
    p.add_argument("--prices", action="store_true", help="Update Norgate price bars (Windows).")
    p.add_argument("--metadata", action="store_true", help="Update Norgate contract metadata (Windows).")
    p.add_argument("--prices-yahoo", action="store_true",
                   help="Update prices from Yahoo Finance for registry symbols that resolve to "
                        "yfinance on this deployment (cross-platform; research-grade).")
    p.add_argument("--ingest-databento", action="store_true",
                   help="Databento Stage 1 (paid API, cross-platform): fetch raw .n.0/.n.1 "
                        "ohlcv-1d + statistics into the append-only raw store ($COTDATA_DATABENTO_RAW). "
                        "Resumable — re-runs only pull new dates. Needs DATABENTO_API_KEY.")
    p.add_argument("--build-databento", action="store_true",
                   help="Databento Stage 2 (free, no API): build back-adjusted prices from the "
                        "raw store into the cotdata store. Run after --ingest-databento.")
    p.add_argument("--cot-legacy", action="store_true", help="Update CFTC COT Legacy (cross-platform).")
    p.add_argument("--cot-disagg", action="store_true", help="Update CFTC COT Disaggregated Futures-Only (cross-platform).")
    p.add_argument("--cot-tff", action="store_true", help="Update Traders in Financial Futures (TFF) COT (cross-platform).")
    p.add_argument("--cot-all", action="store_true", help="Update all COT pipelines (Legacy, Disagg, TFF).")
    p.add_argument("--symbols", nargs="+", default=None, help="Internal symbols; default = all in registry.")
    p.add_argument("--full", action="store_true",
                   help="Full rebuild of reconstructed volume (ignore the incremental "
                        "60-day window). Use after a reconstruction-logic change.")
    p.add_argument("--require-final", action="store_true",
                   help="For --prices: only fetch once Norgate's FINAL futures prices are "
                        "in (last_database_update_time for 'Futures' and 'Continuous Futures' "
                        ">= --final-cutoff today). Otherwise defer with a non-zero exit so a "
                        "scheduler retries. Avoids capturing interim (non-final) bars.")
    p.add_argument("--final-cutoff", default="20:55", metavar="HH:MM",
                   help="Local time after which Norgate's futures Finals are expected "
                        "(default 20:55, ≈ Continuous Futures Final in ET).")
    p.add_argument("--check", action="store_true",
                   help="Print store status (row counts, newest data, staleness) from "
                        "the manifest and exit. Read-only, cross-platform, no network.")
    p.add_argument("--reconcile", action="store_true",
                   help="Prune manifest entries whose parquet file is missing (ghosts "
                        "from old naming), refresh status.json, and exit. Never touches data.")
    args = p.parse_args()

    config.store_root()  # fail fast if COTDATA_STORE unset

    if args.check:
        from . import status
        status.print_check()
        return

    if args.reconcile:
        from . import status, store
        pruned = store.reconcile_manifest()
        if not pruned:
            print("manifest reconcile: nothing to prune (all entries have files).")
        else:
            total = sum(len(v) for v in pruned.values())
            print(f"manifest reconcile: pruned {total} ghost entr{'y' if total == 1 else 'ies'} "
                  f"with no parquet file:")
            for domain, names in sorted(pruned.items()):
                print(f"  {domain}: {len(names)} removed — {', '.join(names[:8])}"
                      + (f", … (+{len(names) - 8})" if len(names) > 8 else ""))
            status.write_status_file(last_run={"kinds": ["reconcile"],
                                               "at": _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"})
        return

    if not (args.prices or args.metadata or args.prices_yahoo or args.ingest_databento
            or args.build_databento or args.cot_legacy or args.cot_disagg or args.cot_tff or args.cot_all):
        p.error("nothing to do — pass --check, --prices, --prices-yahoo, --ingest-databento, "
                "--build-databento, --metadata, --cot-legacy, --cot-disagg, --cot-tff, or --cot-all")

    if args.prices or args.metadata:
        from .providers import norgate

    kinds = []
    last_run = None
    failed_kinds = []  # domains that hard-failed → non-zero exit so a scheduler retries
    deferred = []      # work skipped because inputs aren't ready yet (also non-zero exit)
    if args.prices:
        ready = True
        if args.require_final:
            ready, detail = norgate.finals_ready(args.final_cutoff)
            if not ready:
                print(f"prices: Norgate Finals not in yet (--require-final, cutoff "
                      f"{args.final_cutoff}) — deferring. {detail}")
                deferred.append("prices")
        if ready:
            last_run = norgate.update(symbols=args.symbols, full=args.full)
            kinds.append("prices")
            if last_run and last_run.get("symbols_failed"):
                failed_kinds.append("prices")

    if args.metadata:
        norgate.update_metadata(symbols=args.symbols)
        kinds.append("metadata")

    if args.prices_yahoo:
        from .providers import yfinance as yprov
        r = yprov.update(symbols=args.symbols)
        kinds.append("prices_yahoo")
        if not (r or {}).get("ok", True):
            failed_kinds.append("prices_yahoo")

    if args.ingest_databento:
        from .providers import databento
        r = databento.ingest(symbols=args.symbols)
        kinds.append("ingest_databento")
        if not (r or {}).get("ok", True):
            failed_kinds.append("ingest_databento")

    if args.build_databento:
        from .providers import databento
        r = databento.build(symbols=args.symbols)
        kinds.append("build_databento")
        if not (r or {}).get("ok", True):
            failed_kinds.append("build_databento")

    if args.cot_legacy or args.cot_all:
        from .providers import cftc
        r = cftc.update()
        kinds.append("cot_legacy")
        if not (r or {}).get("ok", True):
            failed_kinds.append("cot_legacy")

    if args.cot_disagg or args.cot_all:
        from .providers import cftc_disagg
        r = cftc_disagg.update()
        kinds.append("cot_disagg")
        if not (r or {}).get("ok", True):
            failed_kinds.append("cot_disagg")

    if args.cot_tff or args.cot_all:
        from .providers import cftc_tff
        r = cftc_tff.update()
        kinds.append("cot_tff")
        if not (r or {}).get("ok", True):
            failed_kinds.append("cot_tff")

    # Structured heartbeat for downstream tools: rebuild status.json from the now-
    # updated manifest. Pollers detect new data via newest_data[<domain>].
    from . import status
    run = dict(last_run or {})
    run["kinds"] = kinds
    run["failed"] = failed_kinds
    run["deferred"] = deferred
    run["at"] = _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    path = status.write_status_file(last_run=run)
    print(f"status written -> {path}")

    # Exit non-zero so Task Scheduler / cron retries, either on a hard failure
    # (source unreachable) or when --require-final deferred because the Finals
    # aren't in yet. Ordinary "no new data yet" is NOT a failure.
    if failed_kinds or deferred:
        parts = ([f"failed: {', '.join(failed_kinds)}"] if failed_kinds else []) + \
                ([f"deferred: {', '.join(deferred)}"] if deferred else [])
        raise SystemExit("cotdata-update: " + " | ".join(parts))

if __name__ == "__main__":
    main()
