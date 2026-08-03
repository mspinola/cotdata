"""CFTC Supplemental (Commodity Index Trader) ingestion.

Offline. The fixtures under ``tests/fixtures/cit/`` are REAL CFTC bytes trimmed to three
markets and four weeks (see ``tests/_gen_cit_fixtures.py``), because most of what can go
wrong here is a property of CFTC's own formatting rather than of our schema: the
``NComm_Postions_Spread_All_NoCIT`` typo, the space-padded ``"CBT "`` code columns, the
``_NoCIT`` suffixes, and the 2013 rename of the as-of date column. Two years are kept so
the rename itself is under test.

Handoff: docs/handoffs/2026-08-03-cit-supplemental.md §5.
"""
import datetime as dt
import zipfile
from pathlib import Path

import pandas as pd
import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "cit"
CIT_2026 = FIXTURES / "dea_cit_txt_2026.zip"
CIT_2012 = FIXTURES / "dea_cit_txt_2012.zip"


@pytest.fixture()
def store_env(tmp_path, monkeypatch):
    monkeypatch.setenv("COTDATA_STORE", str(tmp_path))
    return tmp_path


def _canon(path=CIT_2026):
    from cotdata.providers import cftc_cit
    from cotdata.vintage_ingest import canonicalize_supplemental
    return canonicalize_supplemental(cftc_cit._parse_zip(path))


# ── Parse ───────────────────────────────────────────────────────────────────
def test_parse_is_lossless_and_normalises_the_date_column(store_env):
    """54 CFTC columns in, 54 out — same contract as the disagg provider. The as-of date
    is renamed to the repo's Report_Date_as_MM_DD_YYYY, which is what makes the canonical
    melt read a real date rather than falling back to the frame's positional index."""
    from cotdata.providers import cftc_cit
    df = cftc_cit._parse_zip(CIT_2026)
    assert len(df.columns) == 54
    assert cftc_cit.REPORT_DATE in df.columns
    assert not [c for c in df.columns if c.startswith("As_of_Date_In_Form_2")]
    assert df[cftc_cit.CONTRACT_CODE].iloc[0] == "001602"   # zero-padded, not 1602
    assert df[cftc_cit.REPORT_DATE].dt.year.eq(2026).all()
    # CFTC's own header typos survive: they are the real column names.
    assert "NComm_Postions_Spread_All_NoCIT" in df.columns


def test_both_spellings_of_the_as_of_date_column_parse(store_env):
    """CFTC renamed As_of_Date_In_Form_MM/DD/YYYY to ...YYYY-MM-DD in 2013. The rename was
    cosmetic — the VALUES were already ISO in 2012 — so both must land on the same
    normalised column rather than one silently going missing."""
    from cotdata.providers import cftc_cit
    old = cftc_cit._parse_zip(CIT_2012)
    new = cftc_cit._parse_zip(CIT_2026)
    assert cftc_cit.REPORT_DATE in old.columns and cftc_cit.REPORT_DATE in new.columns
    assert old[cftc_cit.REPORT_DATE].dt.year.eq(2012).all()
    assert str(old[cftc_cit.REPORT_DATE].dtype).startswith("datetime64")


def test_an_unrecognised_date_column_raises_rather_than_falling_back(store_env, tmp_path):
    """Silently falling back to the frame index would date every row by position, which
    reads as data rather than as a schema break."""
    from cotdata.providers import cftc_cit
    with zipfile.ZipFile(CIT_2026) as zf:
        member = zf.namelist()[0]
        text = zf.read(member).decode()
    broken = tmp_path / "broken.zip"
    with zipfile.ZipFile(broken, "w") as zf:
        zf.writestr(member, text.replace("As_of_Date_In_Form_YYYY-MM-DD", "Report_Week"))
    with pytest.raises(ValueError, match="date column"):
        cftc_cit._parse_zip(broken)


# ── The combined flag ───────────────────────────────────────────────────────
def test_every_supplemental_row_is_combined(store_env):
    """There is no futures-only Supplemental. Measured: its Open_Interest_All matches the
    Legacy futures-and-options-combined file on 390/390 2026 market-weeks and the
    futures-only file on 0/390."""
    c = _canon()
    assert set(c["combined"]) == {True}
    assert set(c["report_type"]) == {"supplemental"}
    assert set(_canon(CIT_2012)["combined"]) == {True}


def test_combined_is_asserted_not_inferred(store_env):
    """The file carries no FutOnly_or_Combined column, so _combined_flag's default would
    silently produce combined=False — a guessed value in the natural key. If CFTC ever
    adds the column saying otherwise, this must fail loudly instead of rotting."""
    from cotdata import vintage_ingest as vi
    from cotdata.providers import cftc_cit
    wide = cftc_cit._parse_zip(CIT_2026)
    assert "FutOnly_or_Combined" not in wide.columns   # the reason the assert exists

    wide = wide.copy()
    wide["FutOnly_or_Combined"] = "FutOnly"
    with pytest.raises(vi.ValidationError, match="only ever been futures-and-options"):
        vi.canonicalize_supplemental(wide)


# ── Vocabulary ──────────────────────────────────────────────────────────────
def test_vocabulary_is_isolated_per_report_type(store_env):
    """Supplemental's 'commercial' is not Disaggregated's producer_merchant, and its
    categories must not validate under another report type in either direction."""
    from cotdata import vintage_ingest as vi

    c = _canon()
    vi.validate(c)                                   # valid as supplemental

    mislabelled = c.copy()
    mislabelled["report_type"] = "disaggregated"
    with pytest.raises(vi.ValidationError, match="outside vocabulary for 'disaggregated'"):
        vi.validate(mislabelled)

    # ...and the reverse: a disaggregated category name under report_type=supplemental
    intruder = c.copy()
    intruder.loc[intruder.index[0], "category"] = "swap"
    with pytest.raises(vi.ValidationError, match="outside vocabulary for 'supplemental'"):
        vi.validate(intruder)


def test_index_trader_is_not_mapped_onto_a_disaggregated_name(store_env):
    from cotdata import vintage_ingest as vi
    assert vi.CATEGORIES["supplemental"] == {
        "commercial", "noncommercial", "index_trader", "nonreportable"}
    assert not (vi.CATEGORIES["supplemental"] & {"swap", "producer_merchant",
                                                 "managed_money", "other_reportable"})
    assert set(_canon()["category"]) == vi.CATEGORIES["supplemental"]


# ── Values ──────────────────────────────────────────────────────────────────
def test_categories_carry_the_no_cit_columns_not_the_legacy_ones(store_env):
    """The whole point of the report: 'commercial' here is commercial NET of index
    traders, so it must read Comm_Positions_Long_All_NoCIT. Reading a Legacy-named
    column would double-count the index book."""
    from cotdata.providers import cftc_cit
    wide = cftc_cit._parse_zip(CIT_2026)
    c = _canon()
    row = wide.iloc[0]
    key = (pd.Timestamp(row[cftc_cit.REPORT_DATE]).normalize(), "001602")
    sel = c[(c["report_date"] == key[0]) & (c["market_code"] == key[1])]
    got = dict(zip(sel["category"], sel["long_contracts"]))
    assert got["commercial"] == row["Comm_Positions_Long_All_NoCIT"]
    assert got["noncommercial"] == row["NComm_Positions_Long_All_NoCIT"]
    assert got["index_trader"] == row["CIT_Positions_Long_All"]
    assert got["nonreportable"] == row["NonRept_Positions_Long_All"]
    # only non-commercial has a spreading column in this report
    spread = dict(zip(sel["category"], sel["spread_contracts"]))
    assert spread["noncommercial"] == row["NComm_Postions_Spread_All_NoCIT"]
    assert all(pd.isna(spread[k]) for k in ("commercial", "index_trader", "nonreportable"))


def test_concentration_ratios_are_null_because_the_report_omits_them(store_env):
    c = _canon()
    for f in ("cr4_net_long", "cr4_net_short", "cr8_net_long", "cr8_net_short"):
        assert c[f].isna().all()


def test_open_interest_identity_stays_inside_the_rounding_tolerance(store_env):
    """Combined reporting publishes delta-weighted option equivalents rounded to whole
    contracts, independently per category, so the category total misses open interest by
    a contract or two on roughly half the rows. That is rounding, not a breach, and must
    not produce a warning."""
    from cotdata import vintage_ingest as vi
    c = _canon()
    assert vi.validate(c) == []
    g = c.groupby(["report_date", "market_code"], sort=False)
    agg = g.agg(oi=("open_interest", "max"), L=("long_contracts", "sum"),
                S=("short_contracts", "sum"), SP=("spread_contracts", "sum"))
    tol = vi.rounding_tolerance(4)
    assert ((agg.L + agg.SP - agg.oi).abs() <= tol).all()
    assert ((agg.S + agg.SP - agg.oi).abs() <= tol).all()


# ── Vintage capture + ingest ────────────────────────────────────────────────
def test_capture_includes_supplemental_from_2006(store_env):
    """History starts in January 2006, and unlike disagg/TFF there is no 2006-2016 bundle
    and no 404 window: every year from 2006 is served as its own zip."""
    from cotdata import vintage
    assert [s.report_type for s in vintage.annual_sources(2005)] == ["legacy"]
    assert "supplemental" in [s.report_type for s in vintage.annual_sources(2006)]
    src = next(s for s in vintage.annual_sources(2026) if s.report_type == "supplemental")
    assert src.url.endswith("dea_cit_txt_2026.zip")
    assert src.source_kind == "annual_zip" and src.report_year == 2026


def test_vintage_round_trip_is_a_no_op_and_a_change_emits_one_revision(store_env):
    from cotdata import vintage_ingest as vi
    c = _canon()
    first = vi.ingest_canonical(c, snapshot_id="cit-1")
    assert first["observations"] == 4 * 12 and first["revisions"] == 0
    assert first["warnings"] == []

    again = vi.ingest_canonical(c, snapshot_id="cit-1-regenerated")
    assert again["observations"] == 0 and again["revisions"] == 0

    revised = c.copy()
    i = revised.index[(revised["category"] == "index_trader").to_numpy().argmax()]
    old = revised.loc[i, "long_contracts"]
    revised.loc[i, "long_contracts"] = old + 500
    r = vi.ingest_canonical(revised, snapshot_id="cit-2")
    assert r["observations"] == 1 and r["revisions"] == 1
    rev = vi.read_revisions().iloc[-1]
    assert rev["report_type"] == "supplemental" and rev["category"] == "index_trader"
    assert rev["field"] == "long_contracts" and rev["delta"] == 500.0


def test_the_cli_ingests_a_supplemental_snapshot(store_env):
    """Drives the real capture->ingest path: a recorded snapshot pointing at retained raw
    bytes must find a canonicaliser and drain to parse_status=ok, not to 'skipped'."""
    from cotdata import vintage, vintage_cli
    from cotdata import vintage_ingest as vi

    raw = store_env / "vintage" / "raw" / "annual_zip" / "2026"
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "cit.zip").write_bytes(CIT_2026.read_bytes())
    vintage._write_manifest({"schema_version": 1, "snapshots": [{
        "snapshot_id": "cit-s1", "report_type": "supplemental",
        "source_kind": "annual_zip",
        "local_path": "vintage/raw/annual_zip/2026/cit.zip", "parse_status": "pending",
        "retrieved_at": "2026-07-31T21:00:00Z", "report_year": 2026,
    }]})
    assert vintage_cli.main(["ingest", "--pending"]) == 0
    assert vintage.read_snapshots()[0]["parse_status"] == "ok"

    obs = vi.read_observations()
    assert len(obs) == 48
    assert set(obs["report_type"]) == {"supplemental"}
    assert obs["observed_at"].eq(pd.Timestamp("2026-07-31T21:00:00")).all()


# ── No cross-report mixing ──────────────────────────────────────────────────
def test_a_query_for_one_report_type_never_returns_another(store_env):
    """Supplemental and Legacy share the market code 001602, share report dates, and
    share the category label 'commercial'. Only report_type separates them, and it is in
    the natural key precisely so they cannot collapse into one series."""
    from cotdata import vintage_ingest as vi

    legacy_wide = pd.DataFrame({
        "Market_and_Exchange_Names": ["WHEAT-SRW - CHICAGO BOARD OF TRADE"],
        "CFTC_Contract_Market_Code": ["001602"],
        "Open_Interest_All": [463502],
        "Comm_Positions_Long_All": [111111], "Comm_Positions_Short_All": [122222],
        "NonComm_Positions_Long_All": [33333], "NonComm_Positions_Short_All": [44444],
        "NonRept_Positions_Long_All": [5555], "NonRept_Positions_Short_All": [6666],
        "Traders_Comm_Long_All": [50], "Traders_Comm_Short_All": [55],
        "Traders_NonComm_Long_All": [60], "Traders_NonComm_Short_All": [45],
    }, index=pd.DatetimeIndex(["2026-07-28"], name="Report_Date_as_MM_DD_YYYY"))

    vi.ingest_canonical(vi.canonicalize_legacy(legacy_wide), snapshot_id="lg-1")
    vi.ingest_canonical(_canon(), snapshot_id="cit-1")

    both = vi.asof("2026-12-31", report_date="2026-07-28", market_code="001602")
    assert set(both["report_type"]) == {"legacy", "supplemental"}

    only_sup = vi.asof("2026-12-31", report_date="2026-07-28", market_code="001602",
                       report_type="supplemental")
    assert set(only_sup["report_type"]) == {"supplemental"}
    assert set(only_sup["combined"]) == {True}
    # the shared 'commercial' label carries different values and a different combined flag
    lg = both[(both["report_type"] == "legacy") & (both["category"] == "commercial")]
    sp = both[(both["report_type"] == "supplemental") & (both["category"] == "commercial")]
    assert lg["long_contracts"].iloc[0] == 111111
    assert sp["long_contracts"].iloc[0] != 111111
    assert bool(lg["combined"].iloc[0]) is False and bool(sp["combined"].iloc[0]) is True


# ── Coverage ────────────────────────────────────────────────────────────────
def test_coverage_artifact_matches_the_set_derived_from_the_data(store_env):
    from cotdata import vintage_ingest as vi
    from cotdata.providers import cftc_cit

    vi.ingest_canonical(_canon(CIT_2012), snapshot_id="cit-2012",
                        observed_at=dt.datetime(2013, 1, 4, tzinfo=dt.timezone.utc))
    vi.ingest_canonical(_canon(CIT_2026), snapshot_id="cit-2026",
                        observed_at=dt.datetime(2026, 7, 31, tzinfo=dt.timezone.utc))

    path, cov = vi.write_coverage("supplemental")
    assert path.exists()
    pd.testing.assert_frame_equal(vi.read_coverage("supplemental"), cov)

    # the artifact must agree with the covered set read straight off the parsed files
    from_files = pd.concat([cftc_cit._parse_zip(CIT_2012), cftc_cit._parse_zip(CIT_2026)],
                           ignore_index=True)
    expected = cftc_cit.coverage(from_files)
    assert (sorted(map(tuple, cov[["report_year", "market_code"]].to_numpy()))
            == sorted(map(tuple, expected[["report_year", "market_code"]].to_numpy())))
    assert cov["weeks"].tolist() == expected["weeks"].tolist()


def test_coverage_reports_an_entry_rather_than_leaving_it_silent(store_env):
    """Soybean Meal entered in 2013, taking the real universe from 12 markets to 13. An
    entry is a break in anything pooled across the universe, so it has to be visible."""
    from cotdata import vintage_ingest as vi

    cov = pd.DataFrame([
        {"report_type": "supplemental", "report_year": 2012, "market_code": "001602",
         "market_name": "WHEAT", "first_report_date": pd.Timestamp("2012-01-03"),
         "last_report_date": pd.Timestamp("2012-12-31"), "weeks": 52},
        {"report_type": "supplemental", "report_year": 2013, "market_code": "001602",
         "market_name": "WHEAT-SRW", "first_report_date": pd.Timestamp("2013-01-08"),
         "last_report_date": pd.Timestamp("2013-12-31"), "weeks": 52},
        {"report_type": "supplemental", "report_year": 2013, "market_code": "026603",
         "market_name": "SOYBEAN MEAL", "first_report_date": pd.Timestamp("2013-01-08"),
         "last_report_date": pd.Timestamp("2013-12-31"), "weeks": 52},
    ])
    assert vi.coverage_changes(cov) == [
        {"report_year": 2013, "change": "enter", "market_code": "026603",
         "market_name": "SOYBEAN MEAL"}]

    # a market LEAVING is reported too, and named from the year it was last seen
    gone = cov[cov["report_year"] == 2012].copy()
    gone = pd.concat([gone, cov[(cov["report_year"] == 2013)
                                & (cov["market_code"] == "026603")]])
    assert vi.coverage_changes(gone) == [
        {"report_year": 2013, "change": "enter", "market_code": "026603",
         "market_name": "SOYBEAN MEAL"},
        {"report_year": 2013, "change": "exit", "market_code": "001602",
         "market_name": "WHEAT"}]


def test_coverage_cli_writes_the_artifact(store_env, capsys):
    from cotdata import vintage_cli
    from cotdata import vintage_ingest as vi
    vi.ingest_canonical(_canon(), snapshot_id="cit-1")
    assert vintage_cli.main(["coverage", "--report-type", "supplemental"]) == 0
    assert vi.coverage_path("supplemental").exists()
    out = capsys.readouterr().out
    assert "13 markets per year" not in out          # the fixture holds 3
    assert "3-3 markets per year" in out
    assert "covered set is constant" in out


# ── Store / producer plumbing ───────────────────────────────────────────────
def test_supplemental_is_declared_on_the_cot_half(store_env):
    from cotdata import store
    assert store.half_for("cot_supplemental") == "cot"
    assert "cot_supplemental" in store._empty_manifest()


def test_store_round_trip_and_get_cot(store_env):
    from cotdata import cot, store
    from cotdata.providers import cftc_cit
    df = cftc_cit._parse_zip(CIT_2026).set_index(cftc_cit.REPORT_DATE)
    store.write_cot_supplemental("ZW_001602", df, source="cftc_cit")
    assert len(cot.get_cot("ZW", report="supplemental")) == len(df)
    assert cot.get_cot("ZW", report="legacy").empty      # different domain, not mixed
    with pytest.raises(ValueError, match="Unknown report type"):
        cot.get_cot("ZW", report="cit")


def test_cotdata_prices_refuses_the_supplemental_action(store_env):
    """One host does one job: the price half must not become a second COT producer."""
    from cotdata import update
    with pytest.raises(SystemExit):
        update.main_prices(["--cot-supplemental"])
