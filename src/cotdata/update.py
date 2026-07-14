"""Producer CLI:  cotdata-update --prices --symbols ES NQ
                  cotdata-update --cot
Writes to $COTDATA_STORE. Schedule prices nightly (after the Norgate Data
Updater) and COT weekly (Friday, after the CFTC release)."""
import argparse

from . import config


def main() -> None:
    p = argparse.ArgumentParser(description="cotdata producer — fetch sources into the store.")
    p.add_argument("--prices", action="store_true", help="Update Norgate price bars (Windows).")
    p.add_argument("--cot-legacy", action="store_true", help="Update CFTC COT Legacy (cross-platform).")
    p.add_argument("--cot-disagg", action="store_true", help="Update CFTC COT Disaggregated Futures-Only (cross-platform).")
    p.add_argument("--cot-tff", action="store_true", help="Update Traders in Financial Futures (TFF) COT (cross-platform).")
    p.add_argument("--cot-all", action="store_true", help="Update all COT pipelines (Legacy, Disagg, TFF).")
    p.add_argument("--symbols", nargs="+", default=None, help="Internal symbols; default = all in registry.")
    args = p.parse_args()

    config.store_root()  # fail fast if COTDATA_STORE unset
    
    if not (args.prices or args.cot_legacy or args.cot_disagg or args.cot_tff or args.cot_all):
        p.error("nothing to do — pass --prices, --cot-legacy, --cot-disagg, --cot-tff, or --cot-all")

    if args.prices:
        from .providers import norgate
        norgate.update(symbols=args.symbols)
        
    if args.cot_legacy or args.cot_all:
        from .providers import cftc
        cftc.update()
        
    if args.cot_disagg or args.cot_all:
        from .providers import cftc_disagg
        cftc_disagg.update()
        
    if args.cot_tff or args.cot_all:
        from .providers import cftc_tff
        cftc_tff.update()

if __name__ == "__main__":
    main()
