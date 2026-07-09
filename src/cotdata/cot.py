"""Consumer COT API. Reads weekly CFTC positioning the producer wrote.

NOTE: the positioning-INDEX computation and the trading signals stay in
cot-analyzer (CotIndexer / metrics) — cotdata owns only the raw data. This
returns the cleaned weekly COT table; the indexer builds indices on top.
"""
import pandas as pd

from . import store
from .registry import REGISTRY


def get_cot(name: str) -> pd.DataFrame:
    """Weekly COT for an internal symbol (or a raw CFTC code). Empty if absent."""
    code = name
    if name in REGISTRY and REGISTRY[name].cftc_code:
        code = REGISTRY[name].cftc_code
    return store.read_cot(code)
