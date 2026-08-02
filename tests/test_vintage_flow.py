"""The canonical-schema zero-sum smoke test.

Offline and synthetic, matching the repo's test idiom. The real-data sweep that these
encode is recorded in docs/design/cot_vintage.md §7: 95 markets, 149,412 weeks, every
one balanced.

**Flow decomposition used to be tested here and has moved.** It was measurably the same
function as `crowdmon.futures.flow.decompose` (100.000000% label agreement on 135,835
transitions, at `tolerance=1.0` with the gap rule off), so the copy went rather than the
tests. Every behaviour those tests pinned is pinned on the consuming side:

| was tested here | now |
|---|---|
| the four states by dominant leg | `crowdmon/tests/test_flow.py::test_each_pure_state` |
| first observation of a series is dropped | `..::test_first_observation_of_a_series_is_dropped_not_labelled` |
| series do not leak across market or `combined` | `..::test_deltas_never_cross_{a_market,the_combined}_boundary` |
| duplicate vintages for one week are refused | `..::test_two_vintages_of_one_week_are_refused` |
| a flat week is `quiet`, not `long_liquidation` | `..::test_neither_leg_moving_is_quiet_not_liquidation` |
| open interest corroborates or contradicts | `..::test_open_interest_corroborates_or_contradicts_the_label` |
| the equivalence itself | `crowdmon/tests/test_flow_equivalence.py` |

The one behaviour NOT carried over is `min_frac_oi`, the optional dead zone. It defaulted
to 0.0 (off), nothing set it, and the consuming implementation resolves the same problem
with a dominance `tolerance` whose sensitivity is swept and reported. It is a deliberate
drop, not an oversight.
"""
import pandas as pd
import pytest


def _rows(dates, *, category="noncommercial", longs=(), shorts=(), oi=None,
          market="088691"):
    oi = oi if oi is not None else [1_000_000] * len(dates)
    return pd.DataFrame([{
        "report_date": pd.Timestamp(d), "market_code": market, "report_type": "legacy",
        "combined": False, "category": category,
        "long_contracts": lo, "short_contracts": sh, "open_interest": o,
    } for d, lo, sh, o in zip(dates, longs, shorts, oi)])


WEEKS = ["2026-01-06", "2026-01-13", "2026-01-20", "2026-01-27", "2026-02-03"]


def test_zero_sum_identity_holds_and_the_gap_is_spreading():
    """Every long is somebody's short, so the side totals must match. They fall short of
    open interest by the non-commercial SPREADING column, which providers/cftc.py does
    not capture: an expected, measurable gap, not a break."""
    from cotdata import vintage_flow as vf
    can = pd.concat([
        _rows(WEEKS[:1], category="noncommercial", longs=[600], shorts=[100], oi=[1000]),
        _rows(WEEKS[:1], category="commercial", longs=[300], shorts=[700], oi=[1000]),
        _rows(WEEKS[:1], category="nonreportable", longs=[50], shorts=[150], oi=[1000]),
    ], ignore_index=True)
    z = vf.zero_sum_check(can)
    assert len(z) == 1
    assert bool(z["balanced"].iloc[0]) is True     # 950 long == 950 short
    assert int(z["oi_gap"].iloc[0]) == 50          # the uncaptured spreading


def test_zero_sum_check_catches_a_broken_category_mapping():
    from cotdata import vintage_flow as vf
    can = pd.concat([
        _rows(WEEKS[:1], category="noncommercial", longs=[600], shorts=[100], oi=[1000]),
        _rows(WEEKS[:1], category="commercial", longs=[300], shorts=[700], oi=[1000]),
        _rows(WEEKS[:1], category="nonreportable", longs=[50], shorts=[999], oi=[1000]),
    ], ignore_index=True)
    assert bool(vf.zero_sum_check(can)["balanced"].iloc[0]) is False


def test_missing_columns_raise_rather_than_silently_producing_nothing():
    from cotdata import vintage_flow as vf
    can = _rows(WEEKS[:2], longs=[1, 2], shorts=[1, 1]).drop(columns=["short_contracts"])
    with pytest.raises(vf.FlowError, match="missing columns"):
        vf.zero_sum_check(can)


def test_decompose_is_gone_and_stays_gone():
    """A regression guard against re-adding it here out of habit.

    It was re-derived once already: `crowdmon.futures.flow` was built on 2026-08-01 in
    apparent ignorance that this module had held the same classifier since 2026-07-30.
    The next session to want a flow decomposition should import the one that exists rather
    than write a third.
    """
    from cotdata import vintage_flow as vf
    assert not hasattr(vf, "decompose")
    assert not hasattr(vf, "FLOW_STATES")
