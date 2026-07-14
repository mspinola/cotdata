"""CFTC COT Disaggregated Futures-Only producer — cross-platform.

Downloads fut_disagg_txt_{year}.zip, parses losslessly (preserving all Traders_*
and detailed entity columns), and writes per-code weekly positioning tables to
the store via store.write_cot_disagg().
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

URL_PREFIX = "https://www.cftc.gov/files/dea/history/fut_disagg_txt_"
FIRST_YEAR = 2006  # Disaggregated futures history start

# CFTC TXT column names we must coerce/standardize (others pass through losslessly)
REPORT_DATE = "Report_Date_as_MM_DD_YYYY"
CONTRACT_CODE = "CFTC_Contract_Market_Code"


def _cache_dir() -> Path:
    d = config.store_root() / "_cache" / "cot_disagg"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _standardize_code(val) -> str:
    """CFTC contract codes → 6-digit zero-padded string (matches CotSymbolCodeMap)."""
    s = str(val).strip()
    return s.zfill(6) if s.isdigit() else s


def _download_url(url: str, filename: str):
    """Download a zip to the cache; skip if the server copy isn't newer."""
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
        print(f"  {filename} (disagg): download failed — {e}")
        return zip_path if zip_path.exists() else None


def _parse_zip(zip_path: Path) -> pd.DataFrame:
    """Extract the .txt (CSV) from a year zip → full lossless DataFrame."""
    with zipfile.ZipFile(zip_path) as zf:
        with zf.open(zf.namelist()[0]) as fh:
            # The .txt files are actually CSVs. low_memory=False prevents dtype warnings.
            df = pd.read_csv(fh, low_memory=False)
            
    # Strip any trailing whitespace from column names BEFORE accessing them
    df.columns = df.columns.str.strip()
    
    # CFTC sometimes changes the date column name in Disaggregated reports
    if REPORT_DATE not in df.columns:
        date_cols = [c for c in df.columns if "Report_Date" in c]
        if date_cols:
            df.rename(columns={date_cols[0]: REPORT_DATE}, inplace=True)
        else:
            print(f"AVAILABLE COLUMNS: {list(df.columns)}")
            
    # Coerce the key schema columns to match the Legacy schema format
    df[CONTRACT_CODE] = df[CONTRACT_CODE].apply(_standardize_code)
    df[REPORT_DATE] = pd.to_datetime(df[REPORT_DATE]).dt.tz_localize(None)
    
    # Parquet cannot serialize mixed-type object columns (e.g. Traders_Tot_Old contains ints and strings)
    for col in df.select_dtypes(include=['object']).columns:
        if col not in [CONTRACT_CODE, REPORT_DATE]:
            df[col] = df[col].astype(str)
            
    return df


def update(codes=None, first_year: int = FIRST_YEAR, last_year=None) -> None:
    """Download + parse CFTC Disaggregated futures COT; write full per-code history.

    codes: iterable of CFTC codes; default = all registry codes.
    Rebuilds the complete per-code table each run.
    """
    last_year = last_year or dt.date.today().year
    if codes:
        want = set(codes)
    else:
        want = {s.cftc_code for s in all_symbols() if s.cftc_code}
        for s in all_symbols():      # predecessor codes (migrated-contract history)
            want.update(code for code, _ in hist_code_scales(s.hist_codes))

    frames = []
    
    # The CFTC bundles 2006-2016 in a single historical file
    if first_year <= 2016:
        url = "https://www.cftc.gov/files/dea/history/fut_disagg_txt_hist_2006_2016.zip"
        zp = _download_url(url, "fut_disagg_txt_hist_2006_2016.zip")
        if zp:
            try:
                df = _parse_zip(zp)
                # Filter down to the requested year range so we don't accidentally pull earlier
                # than first_year if the user explicitly wanted a shorter window.
                df = df[(df[REPORT_DATE].dt.year >= first_year) & (df[REPORT_DATE].dt.year <= 2016)]
                frames.append(df)
            except Exception as e:
                print(f"  hist_2006_2016 (disagg): parse failed — {e}")

    # Fetch individual years for 2017+ (or first_year if it's > 2016)
    for year in range(max(2017, first_year), last_year + 1):
        zp = _download_url(f"{URL_PREFIX}{year}.zip", f"fut_disagg_txt_{year}.zip")
        if not zp:
            continue
        try:
            frames.append(_parse_zip(zp))
        except Exception as e:  # noqa: BLE001
            print(f"  {year} (disagg): parse failed — {e}")

    if not frames:
        print("cftc_disagg: no data parsed")
        return

    allrows = pd.concat(frames, ignore_index=True)
    
    # Parquet cannot serialize mixed-type object columns after concat (due to NaNs)
    for col in allrows.select_dtypes(include=['object']).columns:
        if col not in [CONTRACT_CODE, REPORT_DATE]:
            allrows[col] = allrows[col].astype(str)

    for code in sorted(want):
        sub = allrows[allrows[CONTRACT_CODE] == code].copy()
        if sub.empty:
            continue
        
        # Index by report date (DatetimeIndex → manifest last_date)
        sub = sub.sort_values(REPORT_DATE).set_index(REPORT_DATE)
        store.write_cot_disagg(code, sub, source="cftc_disagg")
        print(f"{code}: {len(sub):5d} weeks (disagg) -> store")
