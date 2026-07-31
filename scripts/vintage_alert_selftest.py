"""Prove the vintage revision ALERT fires, without waiting for CFTC to revise anything.

The quiet path is easy to verify (run the task, it exits 0). The loud path is not: it
only triggers when CFTC actually restates something, which may be months away — and an
alert nobody has ever seen fire is an alert nobody should trust.

This forces a revision in a THROWAWAY store and runs the real CLI path over it, so you
can confirm the exit code and the marker-file contents your scheduled task depends on.
It never touches $COTDATA_STORE.

    python scripts/vintage_alert_selftest.py

Expected: "SELFTEST PASSED", exit 0. Any other outcome means the notification wiring in
run-vintage.cmd would not have alerted you to a real restatement.
"""
from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
import tempfile
from pathlib import Path

WIDE_COLS = {
    "Market_and_Exchange_Names": ["GOLD"],
    "CFTC_Contract_Market_Code": ["088691"],
    "Open_Interest_All": [500000],
    "Comm_Positions_Long_All": [200000],
    "NonComm_Positions_Long_All": [150000],
    "NonComm_Positions_Short_All": [90000],
    "NonRept_Positions_Long_All": [40000],
    "NonRept_Positions_Short_All": [30000],
    "Traders_Comm_Long_All": [50], "Traders_Comm_Short_All": [55],
    "Traders_NonComm_Long_All": [60], "Traders_NonComm_Short_All": [45],
}


def _wide(report_date: str, comm_short: int):
    import pandas as pd
    idx = pd.to_datetime([report_date])
    idx.name = "Report_Date_as_MM_DD_YYYY"
    return pd.DataFrame({**WIDE_COLS, "Comm_Positions_Short_All": [comm_short]}, index=idx)


def main() -> int:
    store = Path(tempfile.mkdtemp(prefix="cotdata_vintage_selftest_"))
    os.environ["COTDATA_STORE"] = str(store)
    print(f"scratch store: {store}\n(your real $COTDATA_STORE is untouched)\n")

    from cotdata import vintage
    from cotdata import vintage_ingest as vi

    # Week 1: the report as first published.
    vi.ingest_canonical(vi.canonicalize_legacy(_wide("2026-07-21", 250_000)),
                        snapshot_id="selftest-week1",
                        observed_at=dt.datetime(2026, 7, 24, tzinfo=dt.timezone.utc))
    # Week 2: CFTC restates one field. This is the event the alert exists for.
    vi.ingest_canonical(vi.canonicalize_legacy(_wide("2026-07-21", 251_000)),
                        snapshot_id="selftest-week2",
                        observed_at=dt.datetime(2026, 7, 31, tzinfo=dt.timezone.utc))

    revisions = vi.read_revisions()
    if revisions.empty:
        print("FAIL: no revision was recorded from a changed field.")
        return 1
    print(f"forced {len(revisions)} revision row(s):")
    cols = [c for c in ["report_date", "market_code", "category", "field",
                        "old_value", "new_value", "age_days"] if c in revisions.columns]
    print(revisions[cols].to_string(index=False))

    print("\n--- what 'cotdata-vintage diff' shows you ---")
    sys.stdout.flush()  # subprocesses write straight to the terminal; keep ordering sane
    subprocess.run(
        [sys.executable, "-c",
         "from cotdata import vintage_cli; vintage_cli.main(['diff'])"],
        env={**os.environ, "COTDATA_STORE": str(store)},
    )

    # Drive the same CLI entry point run-vintage.cmd calls, so the exit code proves what
    # the .cmd would actually observe. source_kind is weekly_static deliberately: ingest
    # only parses annual zips, so this skips the parse step and keeps the demonstration
    # about the ALERT rather than about a fake fixture failing to unzip. A real suspect
    # would sit on an annual zip; the notification path is identical either way.
    vintage._write_manifest({"schema_version": 1, "snapshots": [{
        "snapshot_id": "selftest-week2", "report_type": "legacy",
        "source_kind": "weekly_static", "report_year": 2025,
        "local_path": "vintage/raw/weekly_static/2026/selftest.txt",
        "parse_status": "pending", "restatement_suspect": True,
        "retrieved_at": "2026-07-31T21:00:00Z",
    }]})

    sys.stdout.flush()
    proc = subprocess.run(
        [sys.executable, "-c",
         "from cotdata import vintage_cli; import sys; sys.exit(vintage_cli.main(['ingest','--pending']))"],
        capture_output=True, text=True, env={**os.environ, "COTDATA_STORE": str(store)},
    )
    output = proc.stdout + proc.stderr
    print("\n--- what your marker file would contain ---")
    print(output.strip())
    print("--- end ---\n")

    problems = []
    if proc.returncode == 0:
        problems.append("ingest exited 0; the scheduled task would have looked clean")
    if "RESTATEMENT SUSPECT" not in output:
        problems.append("closed-year restatement was not announced in the output")
    if "cotdata-vintage diff" not in output:
        problems.append("output does not tell you how to inspect the revision")

    if problems:
        print("SELFTEST FAILED:")
        for p in problems:
            print(f"  - {p}")
        return 1

    print(f"SELFTEST PASSED — ingest exited {proc.returncode} (non-zero), and the output "
          f"names the restatement.\nrun-vintage.cmd writes exactly that text to "
          f"vintage\\REVISIONS_<date>.txt and exits non-zero.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
