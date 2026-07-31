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
                        include_weekly=not args.no_weekly,
                        include_prior_year=not args.no_prior_year)
    print(f"vintage fetch: {res['checks']} source(s) checked, "
          f"{res['new_files']} new raw file(s) retained.")
    for rec in res["records"]:
        print(f"  {rec['report_type']:<13} {rec['source_kind']:<13} "
              f"{rec.get('report_year') or '':<6} {rec.get('expectation') or '':<22} "
              f"{rec.get('outcome') or '':<13} {rec.get('note') or 'NEW'}")
    # Deliberately NOT a non-zero exit. The wrapper aborts the whole run on a non-zero
    # fetch, so alerting here would skip the ingest that turns a restatement into readable
    # revision rows, precisely the thing you want when the tripwire fires. The alert is
    # persisted on the snapshot and re-raised by `ingest`, which is the step that can exit
    # non-zero safely because everything downstream of it has already run.
    for rec in res["tripwire_alerts"]:
        print(f"  *** TRIPWIRE {rec['report_type']} {rec.get('report_year')}: "
              f"{rec['tripwire_alert']}")
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
            # No canonicaliser for this report type yet. Mark it SKIPPED rather than
            # leaving it pending: a snapshot that never drains is re-selected by
            # --pending on every future run, and if it ever carried restatement_suspect
            # the alert below would then re-fire forever, which is how an alert gets
            # ignored. Skipped snapshots surface once and then go quiet.
            # Raw bytes are retained, so adding a canonicaliser later just means
            # re-marking these pending and re-running ingest — nothing is lost.
            vintage.update_snapshot(
                s["snapshot_id"], parse_status="skipped",
                parse_error=f"no canonicaliser for {s.get('report_type')}/"
                            f"{s.get('source_kind')} yet")
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

    # Surface revisions rather than only recording them. A scheduled run's stdout goes
    # nowhere, so a silent exit-0 after detecting a retroactive restatement would defeat
    # the point of the subsystem. Anything noteworthy exits non-zero, which Task Scheduler
    # and cron both report as a failed run, and prints the detail for the log.
    # Scoped to the snapshots THIS run processed, not to every snapshot ever recorded.
    # A store-wide scan would keep firing on every subsequent run once a single
    # restatement had been seen, and an alert that never clears is one that gets ignored.
    suspects = [s for s in snaps if s.get("restatement_suspect")]
    # Tripwire alerts overlap with `suspects` on the content-changed case and add the
    # "detector went blind" case, which has no changed sha and so no suspect flag. Both
    # need the same exit path: this is the only step that can exit non-zero without
    # skipping downstream work.
    alerts = [s for s in snaps if s.get("tripwire_alert")]
    if total_rev or suspects or alerts:
        _report_revisions(total_rev, suspects, alerts)
        parts = [f"{total_rev} revision(s)"]
        if suspects:
            parts.append(f"{len(suspects)} closed-year restatement suspect(s)")
        if alerts:
            parts.append(f"{len(alerts)} frozen-year tripwire alert(s)")
        raise SystemExit(
            "cotdata-vintage: " + ", ".join(parts)
            + " — review with 'cotdata-vintage diff'. Non-zero so a scheduler surfaces this; "
              "the data IS committed, this is a notification, not a failure.")
    return 0


def _report_revisions(total_rev: int, suspects: list, alerts: list = ()) -> None:
    from . import vintage_ingest
    if alerts:
        print("\n*** FROZEN-YEAR TRIPWIRE ***")
        for s in alerts[-5:]:
            print(f"    {s.get('report_type')} {s.get('report_year')} "
                  f"[{s.get('expectation')} -> {s.get('outcome')}] at {s.get('retrieved_at')}")
            print(f"      {s.get('tripwire_alert')}")
    if suspects:
        print("\n*** CLOSED-YEAR RESTATEMENT SUSPECT ***")
        for s in suspects[-5:]:
            print(f"    {s.get('report_type')} {s.get('report_year')} "
                  f"changed content at {s.get('retrieved_at')}")
        print("    A closed year should be frozen. This is the retroactive-restatement")
        print("    signature the vintage store exists to detect.")
    if not total_rev:
        return
    rev = vintage_ingest.read_revisions()
    if rev.empty:
        return
    recent = rev.sort_values("detected_at").tail(20)
    print(f"\n{len(rev)} revision row(s) recorded; most recent:")
    cols = ["report_date", "market_code", "category", "field", "old_value",
            "new_value", "age_days"]
    print(recent[[c for c in cols if c in recent.columns]].to_string(index=False))
    deep = recent[recent["age_days"] > 30] if "age_days" in recent.columns else None
    if deep is not None and not deep.empty:
        print(f"\n{len(deep)} of these reach back more than 30 days — revisions inside the")
        print("calibration window rewrite the baseline every historical reading used.")


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


def _cmd_flow(args) -> int:
    from . import vintage_flow

    if args.source == "current":
        canonical = vintage_flow.from_current_store(args.market)
        print(f"vintage flow: {args.market} from the CURRENT-STATE store. NOT "
              f"point-in-time. Revisions are already applied, so this is for looking at "
              f"history and validating the schema, never for evaluating a rule.")
    else:
        canonical = vintage_flow.from_vintage(as_of=args.as_of, market_code=args.market)
        if canonical.empty:
            print("vintage flow: no vintage observations yet. The vintage series begins "
                  "at first capture; use --source current for history.")
            return 0

    z = vintage_flow.zero_sum_check(canonical)
    unbalanced = int((~z["balanced"]).sum())
    print(f"  zero-sum: {len(z) - unbalanced}/{len(z)} weeks balanced"
          + (f"  *** {unbalanced} UNBALANCED ***" if unbalanced else ""))

    fl = vintage_flow.decompose(canonical, min_frac_oi=args.min_frac_oi)
    if args.category:
        fl = fl[fl["category"] == args.category]
    if fl.empty:
        print("  no weeks to decompose.")
        return 0
    off = fl[fl["days_elapsed"] != 7]
    if len(off):
        print(f"  {len(off)} of {len(fl)} intervals are not 7 days (COT was FORTNIGHTLY "
              f"before 1992-10-13, and holidays shift the rest). Those rows are not a "
              f"weekly change and are not comparable to one.")
    print(f"\n{fl['state'].value_counts().to_string()}\n")
    cols = ["report_date", "category", "d_long", "d_short", "d_net", "d_oi",
            "days_elapsed", "state", "oi_corroborates"]
    print(fl.tail(args.last)[cols].to_string(index=False))
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
    f.add_argument("--no-prior-year", action="store_true", dest="no_prior_year",
                   help="Skip the prior year, disabling the frozen-year restatement "
                        "tripwire. Saves one ~7 MB weekly transfer and nothing on disk.")
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

    fw = sub.add_parser("flow", help="Weekly flow decomposition per market/category.")
    fw.add_argument("--market", required=True, help="CFTC market code, e.g. 088691.")
    fw.add_argument("--source", choices=("vintage", "current"), default="vintage",
                    help="'vintage' is point-in-time and starts at first capture; "
                         "'current' reads the existing store back to 1986 but has "
                         "revisions already applied, so it is NOT point-in-time.")
    fw.add_argument("--as-of", default=None, dest="as_of",
                    help="Point-in-time timestamp (vintage source only).")
    fw.add_argument("--category", default=None, help="e.g. noncommercial.")
    fw.add_argument("--min-frac-oi", type=float, default=0.0, dest="min_frac_oi",
                    help="Dead zone as a fraction of prior open interest; weeks under it "
                         "on both legs are 'quiet'. Default 0.0 (no dead zone).")
    fw.add_argument("--last", type=int, default=20, help="Rows to print (default 20).")
    fw.set_defaults(func=_cmd_flow)

    args = p.parse_args(argv)
    config.store_root()  # fail fast if COTDATA_STORE unset
    return args.func(args)


# ── schedule ────────────────────────────────────────────────────────────────
def _cmd_sched_sync(args) -> int:
    from . import vintage_schedule
    cal = vintage_schedule.sync_release_schedule()
    print(f"schedule sync: {cal['scheduled']} release date(s) from the published "
          f"calendar ({cal['holiday_delayed']} holiday-delayed).")
    res = vintage_schedule.sync()
    print(f"schedule sync: {res['announcements']} announcement row(s) scraped.")
    print("run 'cotdata-schedule backfill' to apply them to stored observations.")
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
