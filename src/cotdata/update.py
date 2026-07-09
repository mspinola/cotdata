"""Producer CLI:  cotdata-update --prices --symbols ES NQ
                  cotdata-update --cot
Writes to $COTDATA_STORE. Schedule prices nightly (after the Norgate Data
Updater) and COT weekly (Friday, after the CFTC release)."""
import argparse

from . import config


def main() -> None:
    p = argparse.ArgumentParser(description="cotdata producer — fetch sources into the store.")
    p.add_argument("--prices", action="store_true", help="Update Norgate price bars (Windows).")
    p.add_argument("--cot", action="store_true", help="Update CFTC COT (cross-platform).")
    p.add_argument("--symbols", nargs="+", default=None, help="Internal symbols; default = all in registry.")
    args = p.parse_args()

    config.store_root()  # fail fast if COTDATA_STORE unset
    if not (args.prices or args.cot):
        p.error("nothing to do — pass --prices and/or --cot")

    if args.prices:
        from .providers import norgate
        norgate.update(symbols=args.symbols)
    if args.cot:
        from .providers import cftc
        cftc.update()


if __name__ == "__main__":
    main()
