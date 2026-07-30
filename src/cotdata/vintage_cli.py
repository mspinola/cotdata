"""`cotdata-vintage` / `cotdata-schedule` — capture and query the COT vintage store.

Subcommands (handoff §7, adapted to the repo's hyphenated entry-point style):

    cotdata-vintage fetch   [--year YYYY | --all] [--no-weekly]
    cotdata-vintage ingest  [--snapshot ID | --pending]
    cotdata-vintage diff    [--since DATE] [--market CODE] [--report-type T]
    cotdata-vintage asof    --as-of TIMESTAMP --report-date DATE [--market CODE]
    cotdata-schedule sync
    cotdata-schedule backfill
"""
import argparse

from . import config


# ── vintage ─────────────────────────────────────────────────────────────────
def _cmd_fetch(args) -> int:
    from . import vintage
    res = vintage.fetch(year=args.year, all_years=args.all,
                        include_weekly=not args.no_weekly)
    print(f"vintage fetch: {res['checks']} source(s) checked, "
          f"{res['new_files']} new raw file(s) retained.")
    for rec in res["records"]:
        print(f"  {rec['report_type']:<13} {rec['source_kind']:<13} {rec.get('note') or 'NEW'}")
    return 0


def _cmd_ingest(args) -> int:
    from pathlib import Path

    from . import vintage, vintage_ingest
    from .providers import cftc

    snaps = vintage.read_snapshots()
    if args.snapshot:
        snaps = [s for s in snaps if s.get("snapshot_id") == args.snapshot]
    elif args.pending:
        snaps = [s for s in snaps if s.get("parse_status") == "pending"]
    # Only the Legacy annual zip is wired end-to-end this pass; disagg/TFF canonicalizers
    # are a follow-on. Skip (don't fail) snapshots we can't yet parse.
    total_obs = total_rev = 0
    for s in snaps:
        if s.get("report_type") != "legacy" or s.get("source_kind") != "annual_zip":
            continue
        path = config.store_root() / s["local_path"]
        try:
            wide = cftc._parse_zip(Path(path))
            wide = wide.set_index(cftc.REPORT_DATE)
            canonical = vintage_ingest.canonicalize_legacy(wide)
            res = vintage_ingest.ingest_canonical(canonical, snapshot_id=s["snapshot_id"])
            vintage.update_snapshot(s["snapshot_id"], parse_status="ok", parse_error=None)
            total_obs += res["observations"]
            total_rev += res["revisions"]
            print(f"  {s['snapshot_id']}: +{res['observations']} obs, +{res['revisions']} rev")
        except Exception as e:  # noqa: BLE001 — record the failure, don't abort the batch
            vintage.update_snapshot(s["snapshot_id"], parse_status="failed", parse_error=str(e))
            print(f"  {s['snapshot_id']}: FAILED — {e}")
    print(f"vintage ingest: {total_obs} new observation(s), {total_rev} revision(s).")
    return 0


def _cmd_diff(args) -> int:
    from . import vintage_ingest
    rev = vintage_ingest.read_revisions()
    if rev.empty:
        print("vintage diff: no revisions recorded yet.")
        return 0
    if args.since:
        import pandas as pd
        rev = rev[rev["detected_at"] >= pd.Timestamp(args.since)]
    if args.market:
        rev = rev[rev["market_code"] == args.market]
    if args.report_type:
        rev = rev[rev["report_type"] == args.report_type]
    print(f"vintage diff: {len(rev)} revision row(s).")
    cols = ["report_date", "market_code", "category", "field", "old_value",
            "new_value", "age_days"]
    with __import__("pandas").option_context("display.max_rows", 50):
        print(rev[cols].to_string(index=False))
    return 0


def _cmd_asof(args) -> int:
    from . import vintage_ingest
    df = vintage_ingest.asof(args.as_of, report_date=args.report_date, market_code=args.market)
    print(f"vintage asof {args.as_of}: {len(df)} row(s) known at that time.")
    if not df.empty:
        cols = ["report_date", "market_code", "category", "long_contracts",
                "short_contracts", "open_interest", "observed_at", "snapshot_id"]
        print(df[cols].to_string(index=False))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="cotdata-vintage",
        description="Capture and query the COT vintage (as-published) store.")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="Capture raw CFTC files into the immutable landing zone.")
    f.add_argument("--year", type=int, default=None, help="Report year (default: current).")
    f.add_argument("--all", action="store_true", help="Every year 1986→present (cold-start).")
    f.add_argument("--no-weekly", action="store_true", help="Skip the current-week static.")
    f.set_defaults(func=_cmd_fetch)

    ing = sub.add_parser("ingest", help="Parse retained raw files into change-only observations.")
    g = ing.add_mutually_exclusive_group()
    g.add_argument("--snapshot", default=None, help="Ingest one snapshot id.")
    g.add_argument("--pending", action="store_true", help="Ingest all parse_status=pending.")
    ing.set_defaults(func=_cmd_ingest)

    d = sub.add_parser("diff", help="Show recorded field-level revisions.")
    d.add_argument("--since", default=None, help="Only revisions detected on/after DATE.")
    d.add_argument("--market", default=None, help="Filter by CFTC market code.")
    d.add_argument("--report-type", default=None, dest="report_type")
    d.set_defaults(func=_cmd_diff)

    a = sub.add_parser("asof", help="Reconstruct the dataset as known at a past timestamp.")
    a.add_argument("--as-of", required=True, dest="as_of", help="Point-in-time timestamp.")
    a.add_argument("--report-date", default=None, dest="report_date")
    a.add_argument("--market", default=None)
    a.set_defaults(func=_cmd_asof)

    args = p.parse_args(argv)
    config.store_root()  # fail fast if COTDATA_STORE unset
    return args.func(args)


# ── schedule ────────────────────────────────────────────────────────────────
def _cmd_sched_sync(args) -> int:
    from . import vintage_schedule
    res = vintage_schedule.sync()
    print(f"schedule sync: {res['announcements']} announcement row(s) scraped.")
    return 0


def _cmd_sched_published(args) -> int:
    from . import vintage_schedule
    res = vintage_schedule.sync_published()
    print(f"schedule published: {res['published']} week(s) resolved from retained "
          f"weekly-static Last-Modified headers (true publication timestamps).")
    return 0


def _cmd_sched_backfill(args) -> int:
    from . import vintage_schedule
    counts = vintage_schedule.backfill()
    total = sum(counts.values())
    print(f"schedule backfill: resolved release_date for {total} observation row(s):")
    for src in vintage_schedule.PRECEDENCE:
        if counts.get(src):
            print(f"  {src:<10} {counts[src]}")
    return 0


def main_schedule(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="cotdata-schedule",
        description="Sync the CFTC release schedule / announcements and backfill release_date.")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync", help="Scrape Special Announcements into the store.")
    s.set_defaults(func=_cmd_sched_sync)
    pub = sub.add_parser("published",
                         help="Derive true publication dates from retained weekly statics.")
    pub.set_defaults(func=_cmd_sched_published)
    b = sub.add_parser("backfill", help="Resolve release_date/source across all observations.")
    b.set_defaults(func=_cmd_sched_backfill)
    args = p.parse_args(argv)
    config.store_root()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
