"""CFTC COT producer — cross-platform (just HTTPS zips from cftc.gov).

Downloads dea_fut_xls_{year}.zip (Legacy futures report), parses, and writes a
per-code weekly positioning table to the store via store.write_cot(). The
positioning-INDEX + trading signals stay in cot-analyzer (CotIndexer/metrics);
cotdata owns only the raw weekly data.

Self-contained — no cot-analyzer imports. Ported from cot-analyzer/src/core/etl.py.
"""
import datetime as dt
import io
import zipfile
from email.utils import parsedate_to_datetime
from pathlib import Path

import pandas as pd
import requests

from .. import config, store
from ..registry import all_symbols, hist_code_scales

URL_PREFIX = "https://www.cftc.gov/files/dea/history/dea_fut_xls_"
FIRST_YEAR = 1986  # CFTC Legacy futures history start

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

TARGET_COLS = [MARKET_NAME, REPORT_DATE, CONTRACT_CODE, OPEN_INTEREST,
               COMM_LONG, COMM_SHORT, NONCOMM_LONG, NONCOMM_SHORT,
               NONREPT_LONG, NONREPT_SHORT]


def _cache_dir() -> Path:
    d = config.store_root() / "_cache" / "cot_legacy"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _standardize_code(val) -> str:
    """CFTC contract codes → 6-digit zero-padded string (matches CotSymbolCodeMap)."""
    s = str(val).strip()
    return s.zfill(6) if s.isdigit() else s


def _download_year(year: int) -> Path | None:
    # CFTC changed their naming convention in 2004.
    # 1986 - 2003: deafut_xls_{year}.zip
    # 2004 - Present: dea_fut_xls_{year}.zip
    if year < 2004:
        url = f"https://www.cftc.gov/files/dea/history/deafut_xls_{year}.zip"
        filename = f"deafut_xls_{year}.zip"
    else:
        url = f"{URL_PREFIX}{year}.zip"
        filename = f"dea_fut_xls_{year}.zip"

    zip_path = _cache_dir() / filename
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
    df[REPORT_DATE] = pd.to_datetime(df[REPORT_DATE]).dt.tz_localize(None)
    return df


def update(codes=None, first_year: int = FIRST_YEAR, last_year=None) -> None:
    """Download + parse CFTC Legacy futures COT; write full per-code history.

    codes: iterable of CFTC codes; default = all registry codes.
    Rebuilds the complete per-code table each run (parse is cheap; downloads are
    cached and skip when unchanged). Incremental append is a future optimization.
    """
    last_year = last_year or dt.date.today().year
    if codes:
        want = set(codes)
    else:
        want = {s.cftc_code for s in all_symbols() if s.cftc_code}
        for s in all_symbols():      # predecessor codes (migrated-contract history)
            want.update(code for code, _ in hist_code_scales(s.hist_codes))

    frames = []
    for year in range(first_year, last_year + 1):
        zp = _download_year(year)
        if zp is None:
            continue
        try:
            frames.append(_parse_zip(zp))
        except Exception as e:  # noqa: BLE001
            print(f"  {year}: parse failed — {e}")

    if not frames:
        print("cftc: no data parsed")
        return

    allrows = pd.concat(frames, ignore_index=True)
    for code in sorted(want):
        sub = allrows[allrows[CONTRACT_CODE] == code].copy()
        if sub.empty:
            print(f"{code}: no rows")
            continue
        sub = sub.sort_values(REPORT_DATE).set_index(REPORT_DATE)
        store.write_cot_legacy(code, sub, source="cftc")
        print(f"{code}: {len(sub):5d} weeks (legacy) -> store")
