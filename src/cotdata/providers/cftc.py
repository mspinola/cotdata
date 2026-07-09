"""CFTC COT producer — cross-platform (just HTTPS zips from cftc.gov).

MIGRATION: lift the download/parse logic from cot-analyzer/src/core/etl.py
(dea_fut_xls_{year}.zip → Legacy futures-only), write the cleaned weekly table
per CFTC code via store.write_cot(). The positioning-INDEX + signals stay in
cot-analyzer's CotIndexer/metrics — cotdata owns only the raw data.
"""
from .. import store


def update(codes=None) -> None:
    """Download + parse CFTC Legacy futures COT, write weekly tables to the store.

    TODO: port from cot-analyzer etl.py:
      url = https://www.cftc.gov/files/dea/history/dea_fut_xls_{year}.zip
      → read xls, standardize contract codes, keep Comm/NonComm/Nonreportable
        long/short + OI → store.write_cot(code, weekly_df, source="cftc").
    """
    raise NotImplementedError(
        "Port cot-analyzer/src/core/etl.py into cotdata.providers.cftc.update()."
    )
