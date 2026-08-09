"""Producer CLI:  cotdata-update --cot-all
                  cotdata-update --check        # read-only store status
                  cotdata-update --reconcile    # prune stale manifest ghosts
Writes to $COTDATA_STORE. Schedule COT weekly (Friday, after the CFTC release).

ADR-0007 is complete: every price producer left this package. Norgate, Yahoo and
databento all live in `marketdata` now, against `$MARKETDATA_STORE` — run
`marketdata-update --bars` (or `--ingest-databento` / `--build-databento`) there.
This CLI fetches CFTC positioning and nothing else."""
import argparse
import datetime as _dt

from . import config


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="cotdata producer — fetch sources into the store.")
    p.add_argument("--cot-legacy", action="store_true", help="Update CFTC COT Legacy (cross-platform).")
    p.add_argument("--cot-disagg", action="store_true", help="Update CFTC COT Disaggregated Futures-Only (cross-platform).")
    p.add_argument("--cot-tff", action="store_true", help="Update Traders in Financial Futures (TFF) COT (cross-platform).")
    p.add_argument("--cot-supplemental", action="store_true",
                   help="Update the CFTC Supplemental (Commodity Index Trader) COT "
                        "(cross-platform). 13 agricultural markets, and futures-and-options "
                        "COMBINED: its open interest is not comparable with the other three.")
    p.add_argument("--cot-all", action="store_true",
                   help="Update all COT pipelines (Legacy, Disagg, TFF, Supplemental).")
    p.add_argument("--symbols", nargs="+", default=None, help="Internal symbols; default = all in registry.")
    p.add_argument("--check", action="store_true",
                   help="Print store status (row counts, newest data, staleness) from "
                        "the manifest and exit. Read-only, cross-platform, no network.")
    p.add_argument("--migrate-manifests", action="store_true",
                   help="One-shot: split a legacy manifest.json into the per-half "
                        "manifests/ files, then exit. Idempotent, read-only on data. "
                        "Run once per store after upgrading.")
    p.add_argument("--reconcile", action="store_true",
                   help="Prune manifest entries whose parquet file is missing (ghosts "
                        "from old naming), refresh status.json, and exit. Never touches data.")
    return p


def main(argv=None) -> None:
    p = _parser()
    args = p.parse_args(argv)

    config.store_root()  # fail fast if COTDATA_STORE unset

    if args.check:
        from . import status
        status.print_check()
        return

    if args.migrate_manifests:
        from . import store
        added = store.migrate_manifests()
        total = sum(added.values())
        if not total:
            print("manifest migration: nothing to do (already migrated, or no legacy "
                  "manifest.json).")
        else:
            for half, n in sorted(added.items()):
                print(f"  {half:<8} +{n} entries -> manifests/{half}.json")
            print(f"manifest migration: moved {total} entries out of the legacy "
                  f"aggregate. manifest.json is no longer written and can be deleted "
                  f"once every consumer is on this version.")
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

    if not (args.cot_legacy or args.cot_disagg or args.cot_tff
            or args.cot_supplemental or args.cot_all):
        p.error("nothing to do — pass --check, --cot-legacy, --cot-disagg, --cot-tff, "
                "--cot-supplemental or --cot-all. Every price producer moved to "
                "marketdata (ADR-0007): use 'marketdata-update --bars', or "
                "'--ingest-databento'/'--build-databento' there.")

    kinds = []
    failed_kinds = []  # domains that hard-failed → non-zero exit so a scheduler retries
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

    if args.cot_supplemental or args.cot_all:
        from .providers import cftc_cit
        r = cftc_cit.update()
        kinds.append("cot_supplemental")
        if not (r or {}).get("ok", True):
            failed_kinds.append("cot_supplemental")
        # Coverage is printed rather than merely written, because a market entering or
        # leaving is the kind of change that is invisible in a per-market read and
        # breaks every pooled statistic computed over the universe.
        cov = (r or {}).get("coverage")
        if cov is not None and not cov.empty:
            per_year = cov.groupby("report_year")["market_code"].nunique()
            print(f"cot_supplemental coverage: {int(per_year.min())}-{int(per_year.max())} "
                  f"markets per year over {len(per_year)} year(s).")

    # Structured heartbeat for downstream tools: rebuild status.json from the now-
    # updated manifest. Pollers detect new data via newest_data[<domain>].
    from . import status
    # No `deferred` state: the CFTC zips are either reachable or they are not.
    # "Not published yet" belongs to the bar producers, and the gate that expresses
    # it moved with them (`marketdata-update --bars --require-final`).
    run = {"kinds": kinds, "failed": failed_kinds,
           "at": _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"}
    path = status.write_status_file(last_run=run)
    print(f"status written -> {path}")

    # Exit non-zero on a hard failure (source unreachable) so Task Scheduler / cron
    # retries. Ordinary "no new data yet" is NOT a failure.
    if failed_kinds:
        raise SystemExit(f"cotdata-update: failed: {', '.join(failed_kinds)}")

if __name__ == "__main__":
    main()


def main_cot(argv=None) -> None:
    """`cotdata-cot`: an alias for `cotdata-update`, kept because the scheduled jobs
    call it by name (see docs/examples/*/run-cot.*).

    It used to be half of a pair. `cotdata-prices` was the other, and each refused the
    other's flags so a price box could not quietly become a second COT producer racing
    the first. With every price producer moved to marketdata there is no other half to
    refuse, so the machinery is gone and only the name survives."""
    main(argv)
