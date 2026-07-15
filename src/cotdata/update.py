"""Producer CLI:  cotdata-update --prices --symbols ES NQ
                  cotdata-update --cot-all
                  cotdata-update --check        # read-only store status
Writes to $COTDATA_STORE. Schedule prices nightly (after the Norgate Data
Updater) and COT weekly (Friday, after the CFTC release)."""
import argparse
import datetime as _dt

from . import config


def main() -> None:
    p = argparse.ArgumentParser(description="cotdata producer — fetch sources into the store.")
    p.add_argument("--prices", action="store_true", help="Update Norgate price bars (Windows).")
    p.add_argument("--metadata", action="store_true", help="Update Norgate contract metadata (Windows).")
    p.add_argument("--cot-legacy", action="store_true", help="Update CFTC COT Legacy (cross-platform).")
    p.add_argument("--cot-disagg", action="store_true", help="Update CFTC COT Disaggregated Futures-Only (cross-platform).")
    p.add_argument("--cot-tff", action="store_true", help="Update Traders in Financial Futures (TFF) COT (cross-platform).")
    p.add_argument("--cot-all", action="store_true", help="Update all COT pipelines (Legacy, Disagg, TFF).")
    p.add_argument("--symbols", nargs="+", default=None, help="Internal symbols; default = all in registry.")
    p.add_argument("--full", action="store_true",
                   help="Full rebuild of reconstructed volume (ignore the incremental "
                        "60-day window). Use after a reconstruction-logic change.")
    p.add_argument("--check", action="store_true",
                   help="Print store status (row counts, newest data, staleness) from "
                        "the manifest and exit. Read-only, cross-platform, no network.")
    args = p.parse_args()

    config.store_root()  # fail fast if COTDATA_STORE unset

    if args.check:
        from . import status
        status.print_check()
        return

    if not (args.prices or args.metadata or args.cot_legacy or args.cot_disagg or args.cot_tff or args.cot_all):
        p.error("nothing to do — pass --check, --prices, --metadata, --cot-legacy, --cot-disagg, --cot-tff, or --cot-all")

    if args.prices or args.metadata:
        from .providers import norgate

    kinds = []
    last_run = None
    if args.prices:
        last_run = norgate.update(symbols=args.symbols, full=args.full)
        kinds.append("prices")

    if args.metadata:
        norgate.update_metadata(symbols=args.symbols)
        kinds.append("metadata")

    if args.cot_legacy or args.cot_all:
        from .providers import cftc
        cftc.update()
        kinds.append("cot_legacy")

    if args.cot_disagg or args.cot_all:
        from .providers import cftc_disagg
        cftc_disagg.update()
        kinds.append("cot_disagg")

    if args.cot_tff or args.cot_all:
        from .providers import cftc_tff
        cftc_tff.update()
        kinds.append("cot_tff")

    # Structured heartbeat for downstream tools: rebuild status.json from the now-
    # updated manifest. Pollers detect new data via newest_data[<domain>].
    from . import status
    run = dict(last_run or {})
    run["kinds"] = kinds
    run["at"] = _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    path = status.write_status_file(last_run=run)
    print(f"status written -> {path}")

if __name__ == "__main__":
    main()
