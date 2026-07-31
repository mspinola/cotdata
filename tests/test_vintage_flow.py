"""Flow decomposition (module spec §6.4) and the canonical-schema smoke test.

Offline and synthetic, matching the repo's test idiom. The real-data sweep that these
encode is recorded in docs/design/cot_vintage.md §7: 95 markets, 149,412 weeks, every
one balanced.
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


def test_the_four_states_are_labelled_by_the_dominant_leg():
    """The spec's table is written with a '~0' leg, which never happens in real data.
    Dominant-leg resolution is what makes it decidable, and it must pick the right one."""
    from cotdata import vintage_flow as vf
    # start 100/100, then: +5000 long | -4000 short | +6000 short | -3000 long
    can = _rows(WEEKS,
                longs=[100, 5100, 5100, 5100, 2100],
                shorts=[100, 100, -3900, 2100, 2100])
    fl = vf.decompose(can)
    assert list(fl["state"]) == [vf.NEW_LONGS, vf.SHORT_COVERING,
                                 vf.NEW_SHORTS, vf.LONG_LIQUIDATION]


def test_first_observation_of_a_series_is_dropped():
    """There is no such thing as the weekly change of the first week ever seen."""
    from cotdata import vintage_flow as vf
    fl = vf.decompose(_rows(WEEKS, longs=[1, 2, 3, 4, 5], shorts=[1, 1, 1, 1, 1]))
    assert len(fl) == len(WEEKS) - 1
    assert fl["report_date"].min() == pd.Timestamp(WEEKS[1])


def test_series_do_not_leak_across_markets_or_categories():
    """A diff that crossed a group boundary would invent an enormous fake flow."""
    from cotdata import vintage_flow as vf
    a = _rows(WEEKS[:2], longs=[10, 20], shorts=[10, 10], market="088691")
    b = _rows(WEEKS[:2], longs=[9000, 9010], shorts=[10, 10], market="001602")
    fl = vf.decompose(pd.concat([a, b], ignore_index=True))
    assert set(fl["d_long"]) == {10.0}  # not 8980, which is the cross-market diff


def test_duplicate_vintages_for_one_week_are_refused():
    """Two rows for one (key, date) means two VINTAGES. Diffing them would report a
    revision as though it were a flow, which is the exact confusion the vintage layer
    exists to prevent."""
    from cotdata import vintage_flow as vf
    can = pd.concat([_rows(WEEKS[:2], longs=[10, 20], shorts=[10, 10]),
                     _rows(WEEKS[1:2], longs=[25], shorts=[10])], ignore_index=True)
    with pytest.raises(vf.FlowError, match="duplicate"):
        vf.decompose(can)


def test_days_elapsed_exposes_a_gap_rather_than_assuming_seven():
    """COT was FORTNIGHTLY until 1992-10-13, and holiday weeks shift the rest. A caller
    comparing flow magnitudes must be able to see that a 'weekly' change spans 15 days."""
    from cotdata import vintage_flow as vf
    can = _rows(["1992-01-15", "1992-01-31", "1992-02-14"],
                longs=[100, 200, 300], shorts=[100, 100, 100])
    fl = vf.decompose(can)
    assert list(fl["days_elapsed"]) == [16, 14]


def test_dead_zone_is_off_by_default_and_scales_with_prior_open_interest():
    from cotdata import vintage_flow as vf
    can = _rows(WEEKS[:2], longs=[1000, 1005], shorts=[1000, 1000], oi=[100_000, 100_000])
    assert vf.decompose(can)["state"].iloc[0] == vf.NEW_LONGS         # 5 contracts, but no
    #                                                                  dead zone by default
    quiet = vf.decompose(can, min_frac_oi=0.001)                      # 0.1% of 100k = 100
    assert quiet["state"].iloc[0] == vf.QUIET


def test_open_interest_corroborates_or_contradicts_the_label():
    """New positioning should create contracts; exits should destroy them. When it does
    not, the label describes a transfer between categories, not new or closed risk."""
    from cotdata import vintage_flow as vf
    rising = _rows(WEEKS[:2], longs=[100, 5100], shorts=[100, 100], oi=[1000, 9000])
    falling = _rows(WEEKS[:2], longs=[100, 5100], shorts=[100, 100], oi=[9000, 1000])
    assert vf.decompose(rising)["oi_corroborates"].iloc[0] is True
    assert vf.decompose(falling)["oi_corroborates"].iloc[0] is False


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
        vf.decompose(can)


def test_a_week_where_nothing_moved_is_quiet_not_long_liquidation():
    """Found by adversarial review. `long_dominates & (d_long <= 0)` swallows d_long == 0,
    so every flat week was labelled long_liquidation. Measured over the real 2026 Legacy
    file that was 3,308 of 29,787 transitions (11.1%), which made long_liquidation the
    modal state with a third of its bucket being weeks where nothing happened.

    Zero is not a small number, it is the absence of a change, so this must NOT depend on
    the min_frac_oi dead zone, which is off by default."""
    from cotdata import vintage_flow as vf
    can = _rows(WEEKS[:3], longs=[100, 100, 100], shorts=[50, 50, 50])
    assert list(vf.decompose(can)["state"]) == [vf.QUIET, vf.QUIET]


def test_a_one_contract_move_is_still_classified():
    """The quiet rule must catch only exact zero, or it becomes an unstated threshold."""
    from cotdata import vintage_flow as vf
    can = _rows(WEEKS[:2], longs=[100, 101], shorts=[50, 50])
    assert vf.decompose(can)["state"].iloc[0] == vf.NEW_LONGS
