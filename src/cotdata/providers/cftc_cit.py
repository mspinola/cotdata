"""CFTC COT Supplemental (Commodity Index Trader) producer — cross-platform.

Downloads dea_cit_txt_{year}.zip, parses losslessly, and writes per-code weekly
positioning tables to the store via store.write_cot_supplemental().

Three properties of this report that the other three producers do not have to think
about, all MEASURED against the real 2006-2026 archives (docs/analysis/2026-08-03-cit-
supplemental-measurements.md) rather than taken from the CFTC prose:

1. **It is futures-and-options-COMBINED, and there is no futures-only variant.** Its
   ``Open_Interest_All`` matches the Legacy *combined* file (annualof.xls) on 390/390
   2026 market-weeks and the Legacy *futures-only* file on 0/390. Nothing in the file
   itself says so — unlike Disaggregated and TFF it carries no ``FutOnly_or_Combined``
   column — which is exactly why ``canonicalize_supplemental`` asserts the flag instead
   of inferring it.

2. **The date column is named differently, and the name changed mid-history.** It is
   ``As_of_Date_In_Form_YYYY-MM-DD`` from 2013 and ``As_of_Date_In_Form_MM/DD/YYYY``
   from 2006 to 2012. The rename is cosmetic: the VALUES are ISO ``YYYY-MM-DD`` in
   every year including the ones whose header claims MM/DD/YYYY, so the 2013 change
   fixed a mislabelled header rather than a format. Both are renamed to the repo's
   ``Report_Date_as_MM_DD_YYYY`` here so downstream code sees one convention.

3. **There is no 2006-2016 history bundle.** Disaggregated and TFF are served that way;
   every Supplemental year 2006 through the current one is an individual zip.
"""
import datetime as dt
import zipfile
from email.utils import parsedate_to_datetime
from pathlib import Path

import pandas as pd
import requests

from .. import config, store
from ..registry import all_symbols, hist_code_scales

URL_PREFIX = "https://www.cftc.gov/files/dea/history/dea_cit_txt_"
FIRST_YEAR = 2006  # Supplemental history start (January 2006)

REPORT_DATE = "Report_Date_as_MM_DD_YYYY"
CONTRACT_CODE = "CFTC_Contract_Market_Code"

# The two names CFTC has given the as-of date column. Order matters only in that both
# are accepted; a file carrying both would be a schema change worth failing on, which is
# why _parse_zip raises rather than picking one.
_DATE_COLS = ("As_of_Date_In_Form_YYYY-MM-DD", "As_of_Date_In_Form_MM/DD/YYYY")


def _cache_dir() -> Path:
    d = config.store_root() / "_cache" / "cot_supplemental"
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
        print(f"  {filename} (supplemental): download failed — {e}")
        return zip_path if zip_path.exists() else None


def _parse_zip(zip_path: Path) -> pd.DataFrame:
    """Extract the .txt (CSV) from a year zip → full lossless DataFrame."""
    with zipfile.ZipFile(zip_path) as zf:
        with zf.open(zf.namelist()[0]) as fh:
            df = pd.read_csv(fh, low_memory=False)

    # Strip trailing whitespace from column names BEFORE accessing them. CFTC's own
    # header row carries stray spaces (and two long-standing typos, "Postions" and
    # "Spead", which are left alone — they are the real names).
    df.columns = df.columns.str.strip()

    present = [c for c in _DATE_COLS if c in df.columns]
    if len(present) != 1:
        raise ValueError(
            f"expected exactly one of {list(_DATE_COLS)} in the Supplemental file, found "
            f"{present or 'none'}. CFTC changed the date column again: map it explicitly "
            f"rather than letting the report date fall back to a positional index.")
    df.rename(columns={present[0]: REPORT_DATE}, inplace=True)

    df[CONTRACT_CODE] = df[CONTRACT_CODE].apply(_standardize_code)
    df[REPORT_DATE] = pd.to_datetime(df[REPORT_DATE], format="mixed").dt.tz_localize(None)
    if not df.empty and df[REPORT_DATE].max() > pd.Timestamp.today().normalize() + pd.Timedelta(days=7):
        raise ValueError(f"Date parsing sanity check failed: found future date {df[REPORT_DATE].max()}")

    # Parquet cannot serialize mixed-type object columns (CFTC_Market_Code and
    # CFTC_Commodity_Code arrive space-padded, Contract_Units is free text).
    for col in df.select_dtypes(include=["object"]).columns:
        if col not in [CONTRACT_CODE, REPORT_DATE]:
            df[col] = df[col].astype(str)

    return df


def coverage(frame: pd.DataFrame) -> pd.DataFrame:
    """Which markets the Supplemental actually covered, per report year.

    Derived from the data, never from a hardcoded list. The covered set is NOT constant:
    Soybean Meal (026603) entered in 2013, taking the count from 12 to 13, which is why
    both counts are quoted in the wild. Returns one row per (report_year, market_code)
    with the market name and the weeks observed.
    """
    df = frame[[REPORT_DATE, CONTRACT_CODE, "Market_and_Exchange_Names"]].copy()
    df["report_year"] = df[REPORT_DATE].dt.year
    df["market_name"] = df["Market_and_Exchange_Names"].astype(str).str.strip()
    g = df.groupby(["report_year", CONTRACT_CODE], sort=True)
    out = g.agg(market_name=("market_name", lambda s: sorted(s.unique())[-1]),
                first_report_date=(REPORT_DATE, "min"),
                last_report_date=(REPORT_DATE, "max"),
                weeks=(REPORT_DATE, "nunique")).reset_index()
    return out.rename(columns={CONTRACT_CODE: "market_code"})


def update(codes=None, first_year: int = FIRST_YEAR, last_year=None) -> dict:
    """Download + parse the CFTC Supplemental COT; write full per-code history.

    codes: iterable of CFTC codes; default = all registry codes. Only 13 markets are
    ever present, so most registry codes simply produce nothing — that is coverage, not
    an error. Rebuilds the complete per-code table each run. Returns
    ``{"kind", "ok", "wrote", "coverage"}``; ``ok`` is False only on a hard failure to
    fetch the current year.
    """
    code_to_sym = {}
    for s in all_symbols():
        if s.cftc_code:
            code_to_sym[s.cftc_code] = s.internal
        for hc, _ in hist_code_scales(s.hist_codes):
            code_to_sym[hc] = s.internal

    last_year = last_year or dt.date.today().year
    want = set(codes) if codes else set(code_to_sym.keys())

    frames = []
    latest_ok = True
    for year in range(max(FIRST_YEAR, first_year), last_year + 1):
        zp = _download_url(f"{URL_PREFIX}{year}.zip", f"dea_cit_txt_{year}.zip")
        if not zp:
            if year == last_year:
                latest_ok = False  # couldn't fetch current year — may have missed a release
            continue
        try:
            frames.append(_parse_zip(zp))
        except Exception as e:  # noqa: BLE001
            print(f"  {year} (supplemental): parse failed — {e}")
            if year == last_year:
                latest_ok = False

    if not frames:
        print("cftc_cit: no data parsed")
        return {"kind": "cot_supplemental", "ok": False, "wrote": 0, "coverage": None}

    allrows = pd.concat(frames, ignore_index=True)
    for col in allrows.select_dtypes(include=["object"]).columns:
        if col not in [CONTRACT_CODE, REPORT_DATE]:
            allrows[col] = allrows[col].astype(str)

    cov = coverage(allrows)

    wrote = 0
    for code in sorted(want):
        sub = allrows[allrows[CONTRACT_CODE] == code].copy()
        if sub.empty:
            continue
        sub = sub.sort_values(REPORT_DATE).set_index(REPORT_DATE)
        sym_name = code_to_sym.get(code)
        file_name = f"{sym_name}_{code}" if sym_name else code
        store.write_cot_supplemental(file_name, sub, source="cftc_cit")
        wrote += 1
        print(f"{file_name}: {len(sub):5d} weeks (supplemental) -> store")
    return {"kind": "cot_supplemental", "ok": latest_ok, "wrote": wrote, "coverage": cov}
