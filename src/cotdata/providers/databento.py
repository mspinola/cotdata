"""Databento provider — DORMANT. NOT in the live EOD path.

Retained deliberately for:
  1. the intraday news-failure work (Norgate has no intraday; Databento is the
     source for the release-window reaction refinement of the CMR trigger), and
  2. cross-checking Norgate's settlement close against Databento statistics.

MIGRATION: lift the Databento fetch from cot-analyzer/src/core/market_data.py
(GLBX.MDP3 ohlcv-1d + statistics). NOTE the settlement fix already applied there:
use StatType.SETTLEMENT_PRICE == 3 (NOT 7 = LOWEST_OFFER), date by ts_ref.
Requires DATABENTO_API_KEY. Keep behind the `databento` optional extra so the
live consumers never pull the SDK.
"""


def fetch_intraday(*args, **kwargs):
    raise NotImplementedError(
        "Dormant. Port the intraday path from cot-analyzer market_data.py when "
        "the news-failure release-window work begins."
    )
