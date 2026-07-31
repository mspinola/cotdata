"""Disaggregated and TFF canonicalisation.

These two reports, not Legacy, carry the categories the whole system keys on (Managed
Money, Leveraged Funds), plus three fields Legacy never populates: per-category spreading,
per-category trader counts, and CR4/CR8 concentration. Synthetic frames, matching the
repo's test idiom; the real-data numbers are recorded in docs/design/cot_vintage.md.
"""
import pandas as pd
import pytest


def _disagg_wide(**over):
    """One market, one week, shaped like providers/cftc_disagg._parse_zip output.

    Balanced by construction: each side sums to 900 and open interest is 900 + spreading,
    which is the identity CFTC's own files satisfy exactly.
    """
    row = {
        "Report_Date_as_MM_DD_YYYY": "2026-07-21",
        "CFTC_Contract_Market_Code": "088691",
        "Market_and_Exchange_Names": "GOLD - COMMODITY EXCHANGE INC.",
        "FutOnly_or_Combined": "FutOnly",
        "Open_Interest_All": 1000,
        "Prod_Merc_Positions_Long_All": 100, "Prod_Merc_Positions_Short_All": 400,
        "Traders_Prod_Merc_Long_All": "12", "Traders_Prod_Merc_Short_All": "14",
        # CFTC's own double-underscore typo on Short and Spread but not Long.
        "Swap_Positions_Long_All": 200, "Swap__Positions_Short_All": 150,
        "Swap__Positions_Spread_All": 30,
        "Traders_Swap_Long_All": "5", "Traders_Swap_Short_All": "6",
        "M_Money_Positions_Long_All": 400, "M_Money_Positions_Short_All": 100,
        "M_Money_Positions_Spread_All": 50,
        "Traders_M_Money_Long_All": "40", "Traders_M_Money_Short_All": ".",
        "Other_Rept_Positions_Long_All": 150, "Other_Rept_Positions_Short_All": 200,
        "Other_Rept_Positions_Spread_All": 20,
        "Traders_Other_Rept_Long_All": "7", "Traders_Other_Rept_Short_All": "8",
        "NonRept_Positions_Long_All": 50, "NonRept_Positions_Short_All": 50,
        "Conc_Net_LE_4_TDR_Long_All": 20.5, "Conc_Net_LE_4_TDR_Short_All": 18.0,
        "Conc_Net_LE_8_TDR_Long_All": 31.0, "Conc_Net_LE_8_TDR_Short_All": 27.5,
    }
    row.update(over)
    return pd.DataFrame([row])


def _tff_wide(**over):
    row = {
        "Report_Date_as_MM_DD_YYYY": "2026-07-21",
        "CFTC_Contract_Market_Code": "13874+",
        "Market_and_Exchange_Names": "S&P 500 Consolidated - CHICAGO MERCANTILE EXCHANGE",
        "FutOnly_or_Combined": "FutOnly",
        "Open_Interest_All": 500,
        "Dealer_Positions_Long_All": 50, "Dealer_Positions_Short_All": 200,
        "Dealer_Positions_Spread_All": 10,
        "Traders_Dealer_Long_All": "9", "Traders_Dealer_Short_All": "11",
        "Asset_Mgr_Positions_Long_All": 200, "Asset_Mgr_Positions_Short_All": 60,
        "Asset_Mgr_Positions_Spread_All": 5,
        "Traders_Asset_Mgr_Long_All": "20", "Traders_Asset_Mgr_Short_All": "8",
        "Lev_Money_Positions_Long_All": 120, "Lev_Money_Positions_Short_All": 150,
        "Lev_Money_Positions_Spread_All": 15,
        "Traders_Lev_Money_Long_All": "51", "Traders_Lev_Money_Short_All": "44",
        "Other_Rept_Positions_Long_All": 30, "Other_Rept_Positions_Short_All": 40,
        "Other_Rept_Positions_Spread_All": 8,
        "Traders_Other_Rept_Long_All": "4", "Traders_Other_Rept_Short_All": "5",
        "NonRept_Positions_Long_All": 20, "NonRept_Positions_Short_All": 20,
        "Conc_Net_LE_4_TDR_Long_All": 15.0, "Conc_Net_LE_4_TDR_Short_All": 22.0,
        "Conc_Net_LE_8_TDR_Long_All": 25.0, "Conc_Net_LE_8_TDR_Short_All": 41.9,
    }
    row.update(over)
    return pd.DataFrame([row])


def test_disagg_maps_every_category_in_the_controlled_vocabulary():
    from cotdata import vintage_ingest as vi
    c = vi.canonicalize_disagg(_disagg_wide())
    assert set(c["category"]) == vi.CATEGORIES["disaggregated"]
    mm = c[c.category == "managed_money"].iloc[0]
    assert (mm.long_contracts, mm.short_contracts, mm.spread_contracts) == (400, 100, 50)
    assert mm.report_type == "disaggregated"
    assert mm.market_code == "088691"


def test_tff_maps_every_category_in_the_controlled_vocabulary():
    from cotdata import vintage_ingest as vi
    c = vi.canonicalize_tff(_tff_wide())
    assert set(c["category"]) == vi.CATEGORIES["tff"]
    lev = c[c.category == "leveraged"].iloc[0]
    assert (lev.long_contracts, lev.short_contracts, lev.spread_contracts) == (120, 150, 15)
    assert lev.report_type == "tff"


def test_cftc_double_underscore_typo_is_tolerated_both_ways():
    """Swap__Positions_Short_All has two underscores in CFTC's header and Long has one.
    The day they fix it must not be the day Swap Dealer positions start ingesting as
    nulls, which would then read as a revision when the value came back."""
    from cotdata import vintage_ingest as vi
    fixed = _disagg_wide().rename(columns={
        "Swap__Positions_Short_All": "Swap_Positions_Short_All",
        "Swap__Positions_Spread_All": "Swap_Positions_Spread_All"})
    swap = vi.canonicalize_disagg(fixed)
    swap = swap[swap.category == "swap"].iloc[0]
    assert (swap.long_contracts, swap.short_contracts, swap.spread_contracts) == (200, 150, 30)


def test_a_renamed_column_raises_instead_of_ingesting_nulls():
    """Silent nulls are the worst outcome: they get WRITTEN as observations, and the next
    real value is then recorded as a revision that never happened."""
    from cotdata import vintage_ingest as vi
    broken = _disagg_wide().rename(
        columns={"M_Money_Positions_Long_All": "MMoney_Long_All_v2"})
    with pytest.raises(vi.ValidationError, match="M_Money_Positions_Long_All"):
        vi.canonicalize_disagg(broken)


def test_suppressed_trader_count_becomes_null_not_the_literal_dot():
    """CFTC writes '.' where a count would identify traders. Measured on the 2026 file,
    3,578 of 7,847 Managed Money long counts are suppressed, so this is routine."""
    from cotdata import vintage_ingest as vi
    c = vi.canonicalize_disagg(_disagg_wide())
    mm = c[c.category == "managed_money"].iloc[0]
    assert mm.trader_count_long == 40
    assert pd.isna(mm.trader_count_short)
    assert vi._norm(mm.trader_count_short) == ""  # and never reaches the hash as a string


def test_concentration_ratios_and_spreading_populate_unlike_legacy():
    from cotdata import vintage_ingest as vi
    c = vi.canonicalize_disagg(_disagg_wide())
    assert (c["cr4_net_long"] == 20.5).all()      # per market, repeated per category row
    assert (c["cr8_net_short"] == 27.5).all()
    assert c["spread_contracts"].notna().sum() == 3   # not prod_merc, not nonreportable


def test_combined_is_read_from_the_file_not_hardcoded():
    from cotdata import vintage_ingest as vi
    assert vi.canonicalize_disagg(_disagg_wide())["combined"].eq(False).all()
    combined = _disagg_wide(FutOnly_or_Combined="Combined")
    assert vi.canonicalize_disagg(combined)["combined"].eq(True).all()


def test_a_file_mixing_futures_only_and_combined_is_refused():
    """They are different series and must never share a time series (module spec section 3)."""
    from cotdata import vintage_ingest as vi
    mixed = pd.concat([_disagg_wide(), _disagg_wide(FutOnly_or_Combined="Combined")],
                      ignore_index=True)
    with pytest.raises(vi.ValidationError, match="futures-only and combined"):
        vi.canonicalize_disagg(mixed)


def test_disagg_satisfies_the_zero_sum_identity_exactly():
    """Unlike Legacy, spreading IS captured here, so the sides reconcile to open interest
    with no gap at all. Real data: 7,847 of 7,847 weeks, gap zero everywhere."""
    from cotdata import vintage_flow as vf
    from cotdata import vintage_ingest as vi
    z = vf.zero_sum_check(vi.canonicalize_disagg(_disagg_wide()))
    assert bool(z["balanced"].iloc[0]) is True
    assert int(z["oi_gap"].iloc[0]) == 0


def test_rounding_tolerance_is_derived_from_the_category_count():
    """CFTC's Consolidated contracts (market codes ending '+') aggregate several contract
    sizes onto one unit, so each category figure is independently rounded. Summing n of
    them admits at most n contracts of error. Not a fitted constant."""
    from cotdata import vintage_ingest as vi
    assert vi.rounding_tolerance(3) == 3     # legacy
    assert vi.rounding_tolerance(5) == 5     # disagg / tff
    off_by_one = _tff_wide(Open_Interest_All=500 + 38 + 1)   # sides sum to 500+38
    assert vi.validate(vi.canonicalize_tff(off_by_one)) == []


def test_a_breach_larger_than_the_tolerance_still_warns():
    from cotdata import vintage_ingest as vi
    broken = _tff_wide(Lev_Money_Positions_Long_All=99999)
    assert vi.validate(vi.canonicalize_tff(broken))


def test_change_only_and_revisions_work_for_the_new_report_types(tmp_path, monkeypatch):
    """The bitemporal machinery is report-type agnostic, but nothing had exercised it for
    anything except Legacy until now."""
    monkeypatch.setenv("COTDATA_STORE", str(tmp_path))
    from cotdata import vintage_ingest as vi
    first = vi.canonicalize_disagg(_disagg_wide())
    r1 = vi.ingest_canonical(first, snapshot_id="s1")
    assert (r1["observations"], r1["revisions"]) == (5, 0)

    r2 = vi.ingest_canonical(vi.canonicalize_disagg(_disagg_wide()), snapshot_id="s2")
    assert (r2["observations"], r2["revisions"]) == (0, 0)   # idempotent

    revised = _disagg_wide(M_Money_Positions_Long_All=444)
    r3 = vi.ingest_canonical(vi.canonicalize_disagg(revised), snapshot_id="s3")
    assert r3["observations"] == 1
    rev = vi.read_revisions()
    assert list(rev["category"]) == ["managed_money"]
    assert list(rev["field"]) == ["long_contracts"]
    assert (rev["old_value"].iloc[0], rev["new_value"].iloc[0]) == ("400", "444")


def test_tff_and_disagg_rows_never_collide_in_the_natural_key(tmp_path, monkeypatch):
    """report_type is in the key, so the same market code under two reports stays two
    series. S&P 500 appears in both Legacy and TFF."""
    monkeypatch.setenv("COTDATA_STORE", str(tmp_path))
    from cotdata import vintage_ingest as vi
    same_code = _disagg_wide(CFTC_Contract_Market_Code="13874+")
    vi.ingest_canonical(vi.canonicalize_disagg(same_code), snapshot_id="s1")
    r = vi.ingest_canonical(vi.canonicalize_tff(_tff_wide()), snapshot_id="s2")
    assert (r["observations"], r["revisions"]) == (5, 0)   # 5 new, nothing overwritten
    assert len(vi.read_observations()) == 10
