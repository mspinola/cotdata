#!/usr/bin/env python
"""Probe norgatedata's data-content signals for the data-driven finals_ready redesign.

Run on the WINDOWS producer (needs norgatedata + a live Norgate subscription).

Run it TWICE and compare the output:
  1. during the trading day (before Norgate's evening publish), and
  2. after the evening publish (e.g. ~9:30 PM ET).

The key question: does `last_quoted_date` for a continuous symbol show TODAY's date
only after settlement, or already during the day (a provisional bar)? That decides
whether bar-presence alone is a sufficient "finals are in" signal. See
docs/design/finals_ready_data_driven.md.
"""
import datetime as dt

import norgatedata

REF_SYMBOLS = ["&ES", "&CL", "&ZC"]  # liquid continuous: S&P, WTI, corn — trade every session
FINAL_DATABASES = ["Futures", "Continuous Futures"]

print(f"# probe at {dt.datetime.now().isoformat()}  (local PC time)")
print(f"norgatedata version: {getattr(norgatedata, '__version__', '?')}")
try:
    print(f"NDU running (status()): {norgatedata.status()}")
except Exception as e:  # noqa: BLE001
    print(f"status() ERROR: {e!r}")

# 1) Confirm whether this build has ANY calendar/holiday/session function.
api = sorted(x for x in dir(norgatedata) if not x.startswith("_"))
cal = [x for x in api if any(k in x.lower() for k in
       ("holiday", "calendar", "session", "business", "market_day", "trading_day", "busday"))]
print(f"\ncalendar-ish functions in this build: {cal or 'NONE'}")
print(f"full API: {api}")

# 2) Database refresh times — the current (fragile) signal.
print("\n-- last_database_update_time --")
for db in FINAL_DATABASES:
    try:
        print(f"  {db}: {norgatedata.last_database_update_time(db)}")
    except Exception as e:  # noqa: BLE001
        print(f"  {db}: ERROR {e!r}")

# 3) Data-content signals per ref symbol — the proposed signal.
print("\n-- per-symbol date/time signals --")
for s in REF_SYMBOLS:
    try:
        lq = norgatedata.last_quoted_date(s, datetimeformat="iso")
        slq = norgatedata.second_last_quoted_date(s, datetimeformat="iso")
        lpu = norgatedata.last_price_update_time(s)
        print(f"  {s}: last_quoted_date={lq}  second_last={slq}  last_price_update_time={lpu}")
    except Exception as e:  # noqa: BLE001
        print(f"  {s}: ERROR {e!r}")

# 4) The actual tail — does today's bar exist yet, and do its values look settled?
print("\n-- price_timeseries tail (last 4 bars) --")
for s in REF_SYMBOLS:
    try:
        df = norgatedata.price_timeseries(
            s,
            padding_setting=norgatedata.PaddingType.NONE,
            timeseriesformat="pandas-dataframe",
            start_date="2026-07-20",
        )
        print(f"  {s}:")
        print("    " + df.tail(4).to_string().replace("\n", "\n    "))
    except Exception as e:  # noqa: BLE001
        print(f"  {s}: ERROR {e!r}")
