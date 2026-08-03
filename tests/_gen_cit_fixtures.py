"""Regenerate the committed Supplemental (CIT) fixtures from the real CFTC archives.

Run manually, with network access, after a CFTC schema change:

    python tests/_gen_cit_fixtures.py /path/to/downloaded/zips

The fixtures are REAL CFTC bytes trimmed to three markets and a few weeks, not synthetic
rows, because the properties the tests pin are properties of CFTC's own formatting: the
"Postions"/"Spead" header typos, the space-padded code columns, the ``_NoCIT`` suffixes,
and the 2013 rename of the as-of date column. A synthetic fixture would encode whatever
this module happened to believe about those, which is exactly what the tests exist to
check.

Two years are kept for one reason: 2012 carries ``As_of_Date_In_Form_MM/DD/YYYY`` and
2026 carries ``As_of_Date_In_Form_YYYY-MM-DD``, so the pair is the header rename.
"""
import csv
import sys
import zipfile
from pathlib import Path

import pandas as pd

FIXTURES = Path(__file__).parent / "fixtures" / "cit"
# Cocoa and Wheat-SRW because they are the two markets the crowdmon fragility question
# actually turns on; Corn as a third, larger book.
KEEP_CODES = {"073732", "001602", "002602"}
KEEP_WEEKS = 4


def trim(src_zip: Path, dest_zip: Path) -> None:
    """Select whole SOURCE LINES and re-emit them verbatim.

    Deliberately not a pandas read/write round trip. That path re-quotes every field and
    drops the zero padding on ``"001602"`` and the space padding on ``"CBT "``, which are
    the exact byte-level quirks the parser is being tested against — the fixture would
    then agree with the parser about a file CFTC does not publish.
    """
    with zipfile.ZipFile(src_zip) as zf:
        member = zf.namelist()[0]
        text = zf.read(member).decode("utf-8", errors="strict")
    lines = text.splitlines(keepends=True)
    header = [h.strip().strip('"') for h in next(csv.reader([lines[0]]))]
    code_i = header.index("CFTC_Contract_Market_Code")
    date_i = next(i for i, h in enumerate(header) if h.startswith("As_of_Date_In_Form_")
                  and h != "As_of_Date_In_Form_YYMMDD")

    def fields(line):
        return next(csv.reader([line]))

    parsed = [(fields(ln), ln) for ln in lines[1:] if ln.strip()]
    dates = sorted({pd.Timestamp(f[date_i].strip()) for f, _ in parsed})[-KEEP_WEEKS:]
    kept = [ln for f, ln in parsed
            if f[code_i].strip().zfill(6) in KEEP_CODES
            and pd.Timestamp(f[date_i].strip()) in dates]

    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(member, lines[0] + "".join(kept))
    print(f"{dest_zip.name}: {len(kept)} rows, {len(dates)} weeks, "
          f"{len({fields(ln)[code_i].strip() for ln in kept})} markets, member={member}")


if __name__ == "__main__":
    src = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    for year in (2012, 2026):
        trim(src / f"dea_cit_txt_{year}.zip", FIXTURES / f"dea_cit_txt_{year}.zip")
