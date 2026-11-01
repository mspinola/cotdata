"""CFTC COT producer — cross-platform (just HTTPS zips from cftc.gov).

Downloads dea_fut_xls_{year}.zip (Legacy futures-only report), parses, and writes a
per-code weekly positioning table to the store via store.write_cot_legacy(). With
``combined=True`` it downloads dea_com_xls_{year}.zip (the futures-and-options
COMBINED basis of the same report) into store.write_cot_legacy_combined() instead.
The two bases are different series for the same market and week and never share a
directory. The positioning-INDEX + trading signals stay in cot-analyzer
(CotIndexer/metrics); cotdata owns only the raw weekly data.

Unlike the Disaggregated and TFF files, the Legacy files carry NO ``FutOnly_or_Combined``
column (checked on dea_com_xls_2026 and deacom_xls_2003, 126 columns, same names as the
futures-only file). The URL is the only evidence of basis, so the two bases are kept
apart by construction here: separate zip names, separate cache directories, separate
store writers, selected together from one flag.

Self-contained — no cot-analyzer imports. Ported from cot-analyzer/src/core/etl.py.
"""
from __future__ import annotations  # PEP 604 unions (Path | None) on Python 3.9

import datetime as dt
import io
import zipfile
from email.utils import parsedate_to_datetime
from pathlib import Path

import pandas as pd
import requests

from .. import config, store
from ..registry import all_symbols, codes_for

URL_PREFIX = "https://www.cftc.gov/files/dea/history/dea_fut_xls_"
FIRST_YEAR = 1986  # CFTC Legacy futures-only history start
# The combined basis starts later: CFTC's "new format" futures-and-options files begin in
# March 1995 (deacom_xls_1995.zip is the first annual zip served; verified live
# 2026-10-31, as were the 2003/2004 naming boundary and the 2026 file).
FIRST_YEAR_COMBINED = 1995

# CFTC XLS column names (Legacy futures report)
MARKET_NAME = "Market_and_Exchange_Names"
REPORT_DATE = "Report_Date_as_MM_DD_YYYY"
CONTRACT_CODE = "CFTC_Contract_Market_Code"
OPEN_INTEREST = "Open_Interest_All"
COMM_LONG = "Comm_Positions_Long_All"
COMM_SHORT = "Comm_Positions_Short_All"
NONCOMM_LONG = "NonComm_Positions_Long_All"
NONCOMM_SHORT = "NonComm_Positions_Short_All"
NONREPT_LONG = "NonRept_Positions_Long_All"
NONREPT_SHORT = "NonRept_Positions_Short_All"

TRADERS_TOT = "Traders_Tot_All"
TRADERS_COMM_LONG = "Traders_Comm_Long_All"
TRADERS_COMM_SHORT = "Traders_Comm_Short_All"
TRADERS_NONCOMM_LONG = "Traders_NonComm_Long_All"
TRADERS_NONCOMM_SHORT = "Traders_NonComm_Short_All"

TARGET_COLS = [MARKET_NAME, REPORT_DATE, CONTRACT_CODE, OPEN_INTEREST,
               COMM_LONG, COMM_SHORT, NONCOMM_LONG, NONCOMM_SHORT,
               NONREPT_LONG, NONREPT_SHORT,
               TRADERS_TOT, TRADERS_COMM_LONG, TRADERS_COMM_SHORT,
               TRADERS_NONCOMM_LONG, TRADERS_NONCOMM_SHORT]


def _cache_dir(combined: bool = False) -> Path:
    d = config.store_root() / "_cache" / ("cot_legacy_combined" if combined else "cot_legacy")
    d.mkdir(parents=True, exist_ok=True)
    return d


def zip_filename(year: int, combined: bool = False) -> str:
    """The annual Legacy zip's filename on cftc.gov for one year and basis.

    CFTC changed the naming convention in 2004, on both bases:
      futures-only  1986-2003 deafut_xls_{year}.zip    2004-  dea_fut_xls_{year}.zip
      combined      1995-2003 deacom_xls_{year}.zip    2004-  dea_com_xls_{year}.zip
    """
    if combined:
        return f"deacom_xls_{year}.zip" if year < 2004 else f"dea_com_xls_{year}.zip"
    return f"deafut_xls_{year}.zip" if year < 2004 else f"dea_fut_xls_{year}.zip"


def zip_url(year: int, combined: bool = False) -> str:
    return f"https://www.cftc.gov/files/dea/history/{zip_filename(year, combined)}"


def _standardize_code(val) -> str:
    """CFTC contract codes → 6-digit zero-padded string (matches CotSymbolCodeMap)."""
    s = str(val).strip()
    return s.zfill(6) if s.isdigit() else s


def _download_year(year: int, combined: bool = False) -> Path | None:
    url = zip_url(year, combined)
    filename = zip_filename(year, combined)

    zip_path = _cache_dir(combined) / filename
    try:
        if zip_path.exists():
            head = requests.head(url, timeout=30)
            server_mtime = head.headers.get("Last-Modified")
            if server_mtime and zip_path.stat().st_mtime >= parsedate_to_datetime(server_mtime).timestamp():
                return zip_path  # up to date
        r = requests.get(url, timeout=180)
        r.raise_for_status()
        zip_path.write_bytes(r.content)
        return zip_path
    except Exception as e:  # noqa: BLE001
        print(f"  {year}: download failed — {e}")
        return zip_path if zip_path.exists() else None


def _parse_zip(zip_path: Path) -> pd.DataFrame:
    """Extract the .xls from a year zip → cleaned target columns."""
    with zipfile.ZipFile(zip_path) as zf:
        with zf.open(zf.namelist()[0]) as fh:
            data = fh.read()
    df = pd.read_excel(io.BytesIO(data), usecols=TARGET_COLS)  # .xls → needs xlrd
    df[CONTRACT_CODE] = df[CONTRACT_CODE].apply(_standardize_code)
    df[REPORT_DATE] = pd.to_datetime(df[REPORT_DATE], format='mixed').dt.tz_localize(None)
    if not df.empty and df[REPORT_DATE].max() > pd.Timestamp.today().normalize() + pd.Timedelta(days=7):
        raise ValueError(f"Date parsing sanity check failed: found future date {df[REPORT_DATE].max()}")
    return df


def update(codes=None, first_year: int | None = None, last_year=None, *,
           combined: bool = False) -> dict:
    """Download + parse CFTC Legacy COT; write full per-code history.

    codes: iterable of CFTC codes; default = all registry codes.
    combined: False (default) fetches the futures-only files into ``cot_legacy``; True
    fetches the futures-and-options combined files into ``cot_legacy_combined``.
    first_year: defaults to the basis's own history start (1986 / 1995).
    Rebuilds the complete per-code table each run (parse is cheap; downloads are
    cached and skip when unchanged). Incremental append is a future optimization.

    Returns a result dict {"kind", "ok", "wrote"}. ``ok`` is False only on a hard
    failure to fetch the *current* year (the file a new release lands in) — so a
    scheduler can retry a real outage without treating "no new data yet" as failure.
    """
    if first_year is None:
        first_year = FIRST_YEAR_COMBINED if combined else FIRST_YEAR
    kind = "cot_legacy_combined" if combined else "cot_legacy"
    tag = "legacy combined" if combined else "legacy"
    write = store.write_cot_legacy_combined if combined else store.write_cot_legacy
    code_to_sym = {}
    for s in all_symbols():
        for c in codes_for(s):            # primary + hist_codes + sum_codes
            code_to_sym[c] = s.internal

    last_year = last_year or dt.date.today().year
    if codes:
        want = set(codes)
    else:
        want = set(code_to_sym.keys())

    frames = []
    latest_ok = True
    for year in range(first_year, last_year + 1):
        zp = _download_year(year, combined)
        if zp is None:
            if year == last_year:
                latest_ok = False  # couldn't fetch the current year — may have missed a release
            continue
        try:
            frames.append(_parse_zip(zp))
        except Exception as e:  # noqa: BLE001
            print(f"  {year}: parse failed — {e}")
            if year == last_year:
                latest_ok = False

    if not frames:
        print(f"cftc ({tag}): no data parsed")
        return {"kind": kind, "ok": False, "wrote": 0}

    allrows = pd.concat(frames, ignore_index=True)
    wrote = 0
    for code in sorted(want):
        sub = allrows[allrows[CONTRACT_CODE] == code].copy()
        if sub.empty:
            print(f"{code}: no rows")
            continue
        sub = sub.sort_values(REPORT_DATE).set_index(REPORT_DATE)
        sym_name = code_to_sym.get(code)
        file_name = f"{sym_name}_{code}" if sym_name else code
        write(file_name, sub, source="cftc")
        wrote += 1
        print(f"{file_name}: {len(sub):5d} weeks ({tag}) -> store")
    return {"kind": kind, "ok": latest_ok, "wrote": wrote}
