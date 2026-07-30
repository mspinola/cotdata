"""`cotdata-vintage` — capture and query the COT vintage store.

Subcommands (mirrors handoff §7, adapted to the repo's hyphenated entry-point style):

    cotdata-vintage fetch  [--year YYYY | --all] [--no-weekly]

COMMIT 1 ships ``fetch`` (raw snapshot capture) only. ``ingest``, ``diff``, ``asof``
and the ``cotdata-schedule`` commands land with the substantive subsystem in commit 2.
"""
import argparse

from . import config


def _cmd_fetch(args) -> int:
    from . import vintage
    res = vintage.fetch(year=args.year, all_years=args.all,
                        include_weekly=not args.no_weekly)
    print(f"vintage fetch: {res['checks']} source(s) checked, "
          f"{res['new_files']} new raw file(s) retained.")
    for rec in res["records"]:
        tag = rec.get("note") or "NEW"
        print(f"  {rec['report_type']:<13} {rec['source_kind']:<13} {tag}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="cotdata-vintage",
        description="Capture and query the COT vintage (as-published) store.")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="Capture raw CFTC files into the immutable landing zone.")
    f.add_argument("--year", type=int, default=None,
                   help="Report year to capture (default: current year).")
    f.add_argument("--all", action="store_true",
                   help="Capture every year 1986→present (cold-start raw backfill).")
    f.add_argument("--no-weekly", action="store_true",
                   help="Skip the current-week static (whose Last-Modified is the true "
                        "publication timestamp).")
    f.set_defaults(func=_cmd_fetch)

    args = p.parse_args(argv)
    config.store_root()  # fail fast if COTDATA_STORE unset
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
