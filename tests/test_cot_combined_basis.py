"""The futures-and-options COMBINED basis of Legacy, Disaggregated and TFF.

CFTC publishes every one of those three reports twice: futures-only and combined. Until
2026-10-31 cotdata fetched only the futures-only files (plus the Supplemental, which is
combined by nature). The combined files now land in three domains of their own,
``cot_*_combined``, and the vintage layer captures both bases of every annual zip and all
seven current-week statics. What these tests pin:

- the two bases never share a directory, a reader, or a canonical row;
- every entry point that used to mean "futures-only" still does, with no argument;
- the Supplemental cannot be asked for on a basis it is not published on.
"""
import datetime as dt
import sys
from unittest import mock

import pandas as pd
import pytest


@pytest.fixture
def store_env(tmp_path, monkeypatch):
    monkeypatch.setenv("COTDATA_STORE", str(tmp_path))
    return tmp_path


# ── providers: URL tables ───────────────────────────────────────────────────
def test_legacy_zip_names_follow_the_2004_rename_on_both_bases():
    from cotdata.providers import cftc
    assert cftc.zip_filename(2003) == "deafut_xls_2003.zip"
    assert cftc.zip_filename(2004) == "dea_fut_xls_2004.zip"
    assert cftc.zip_filename(2003, combined=True) == "deacom_xls_2003.zip"
    assert cftc.zip_filename(2004, combined=True) == "dea_com_xls_2004.zip"
    assert cftc.zip_url(2026, combined=True).endswith("/history/dea_com_xls_2026.zip")
    assert cftc.FIRST_YEAR == 1986 and cftc.FIRST_YEAR_COMBINED == 1995


def test_disagg_and_tff_combined_urls_are_the_com_variants():
    from cotdata.providers import cftc_disagg, cftc_tff
    assert cftc_disagg.URL_PREFIX_COMBINED.endswith("/com_disagg_txt_")
    assert cftc_disagg.BUNDLE_URL_COMBINED.endswith("/com_disagg_txt_hist_2006_2016.zip")
    assert cftc_tff.URL_PREFIX_COMBINED.endswith("/com_fin_txt_")
    # CFTC reverses the prefix on the TFF bundles: fin_fut / fin_com, not fut_fin / com_fin.
    assert cftc_tff.BUNDLE_URL.endswith("/fin_fut_txt_2006_2016.zip")
    assert cftc_tff.BUNDLE_URL_COMBINED.endswith("/fin_com_txt_2006_2016.zip")


@pytest.mark.parametrize("mod_name", ["cftc_disagg", "cftc_tff"])
def test_a_file_whose_own_basis_flag_disagrees_is_refused(mod_name):
    """The file states its basis; a URL mix-up must not file one basis as the other."""
    import importlib
    mod = importlib.import_module(f"cotdata.providers.{mod_name}")
    says_combined = pd.DataFrame({"FutOnly_or_Combined": ["Combined", "Combined"]})
    says_futonly = pd.DataFrame({"FutOnly_or_Combined": ["FutOnly"]})
    mod._assert_basis(says_combined, True, "x")
    mod._assert_basis(says_futonly, False, "x")
    with pytest.raises(ValueError, match="fetching the futures-only basis"):
        mod._assert_basis(says_combined, False, "x")
    with pytest.raises(ValueError, match="fetching the combined basis"):
        mod._assert_basis(says_futonly, True, "x")
    mod._assert_basis(pd.DataFrame({"Other": [1]}), True, "x")  # no flag: URL is the evidence


def test_providers_write_the_combined_domain_only_when_asked(store_env, monkeypatch):
    """One flag selects zip name, cache directory and store writer together."""
    from cotdata import store
    from cotdata.providers import cftc
    wide = pd.DataFrame({
        cftc.MARKET_NAME: ["E-MINI S&P 500 - CME"], cftc.REPORT_DATE: [pd.Timestamp("2026-01-06")],
        cftc.CONTRACT_CODE: ["13874A"], cftc.OPEN_INTEREST: [100],
        cftc.COMM_LONG: [50], cftc.COMM_SHORT: [40], cftc.NONCOMM_LONG: [30],
        cftc.NONCOMM_SHORT: [40], cftc.NONREPT_LONG: [20], cftc.NONREPT_SHORT: [20],
        cftc.TRADERS_TOT: [10], cftc.TRADERS_COMM_LONG: [3], cftc.TRADERS_COMM_SHORT: [3],
        cftc.TRADERS_NONCOMM_LONG: [4], cftc.TRADERS_NONCOMM_SHORT: [4]})
    asked = []

    def fake_download(year, combined=False):
        asked.append((year, combined))
        return store_env / f"{cftc.zip_filename(year, combined)}"

    monkeypatch.setattr(cftc, "_download_year", fake_download)
    monkeypatch.setattr(cftc, "_parse_zip", lambda p: wide)
    r = cftc.update(codes=["13874A"], first_year=2026, last_year=2026, combined=True)
    assert r == {"kind": "cot_legacy_combined", "ok": True, "wrote": 1}
    assert asked == [(2026, True)]
    assert not store.read_cot_legacy_combined("ES_13874A").empty
    assert store.read_cot_legacy("ES_13874A").empty          # futures-only untouched
    assert (store_env / "cot_legacy_combined" / "ES_13874A.parquet").exists()
    assert not (store_env / "cot_legacy").exists()


def test_legacy_combined_history_starts_in_1995_by_default(store_env, monkeypatch):
    from cotdata.providers import cftc
    years = []
    monkeypatch.setattr(cftc, "_download_year",
                        lambda y, combined=False: years.append((y, combined)) or None)
    cftc.update(codes=["13874A"], last_year=1996, combined=True)
    assert years == [(1995, True), (1996, True)]
    years.clear()
    cftc.update(codes=["13874A"], last_year=1987)
    assert years == [(1986, False), (1987, False)]


# ── store: three domains on the cot half ────────────────────────────────────
def test_combined_domains_are_declared_on_the_cot_half_and_in_the_empty_manifest(store_env):
    from cotdata import store
    for d in ("cot_legacy_combined", "cot_disagg_combined", "cot_tff_combined"):
        assert store.half_for(d) == "cot"
        assert d in store._DOMAIN_DIRS
    m = store.load_manifest()
    assert m["cot_legacy_combined"] == {} and m["cot_tff_combined"] == {}


def test_combined_round_trip_is_isolated_from_futures_only(store_env):
    from cotdata import store
    idx = pd.date_range("2026-01-06", periods=2, freq="W-TUE", name="Report_Date_as_MM_DD_YYYY")
    fut = pd.DataFrame({"Open_Interest_All": [100, 110]}, index=idx)
    com = pd.DataFrame({"Open_Interest_All": [130, 145]}, index=idx)
    store.write_cot_disagg("GC_088691", fut, source="cftc_disagg")
    store.write_cot_disagg_combined("GC_088691", com, source="cftc_disagg")
    assert store.read_cot_disagg("GC_088691")["Open_Interest_All"].tolist() == [100, 110]
    assert store.read_cot_disagg_combined("GC_088691")["Open_Interest_All"].tolist() == [130, 145]
    m = store.load_manifest()
    assert "GC_088691" in m["cot_disagg"] and "GC_088691" in m["cot_disagg_combined"]
    # and --check walks the new domains
    from cotdata.status import _DOMAINS
    assert {"cot_legacy_combined", "cot_disagg_combined", "cot_tff_combined"} <= set(_DOMAINS)


# ── get_cot: basis is an argument, default is the natural one ───────────────
def test_get_cot_defaults_to_each_reports_natural_basis(store_env, monkeypatch):
    from cotdata import cot, store
    hits = []
    for name in ("read_cot_legacy", "read_cot_disagg", "read_cot_tff", "read_cot_supplemental",
                 "read_cot_legacy_combined", "read_cot_disagg_combined", "read_cot_tff_combined"):
        monkeypatch.setattr(store, name, lambda n, _name=name: hits.append(_name) or pd.DataFrame())

    def reader_used(**kw):
        # ES stitches predecessor and sum codes, so one get_cot makes several reads; all
        # of them must come from ONE store domain.
        hits.clear()
        cot.get_cot(kw.pop("name", "ES"), **kw)
        assert hits, "nothing read"
        return set(hits)

    assert reader_used() == {"read_cot_legacy"}
    assert reader_used(report="disagg") == {"read_cot_disagg"}
    assert reader_used(report="tff") == {"read_cot_tff"}
    assert reader_used(name="ZC", report="supplemental") == {"read_cot_supplemental"}
    assert reader_used(combined=True) == {"read_cot_legacy_combined"}
    assert reader_used(report="disagg", combined=True) == {"read_cot_disagg_combined"}
    assert reader_used(report="tff", combined=True) == {"read_cot_tff_combined"}
    assert reader_used(name="ZC", report="supplemental", combined=True) == {"read_cot_supplemental"}
    assert reader_used(combined=False) == {"read_cot_legacy"}


def test_get_cot_refuses_a_futures_only_supplemental(store_env):
    from cotdata import cot
    with pytest.raises(ValueError, match="combined basis"):
        cot.get_cot("ZC", report="supplemental", combined=False)


# ── cotdata-update: three flags, --cot-all means seven ──────────────────────
def _argv(monkeypatch, tmp_path, *args):
    monkeypatch.setenv("COTDATA_STORE", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["cotdata-update", *args])


def _ok(kind):
    return {"kind": kind, "ok": True, "wrote": 1}


def test_cot_all_runs_all_seven_and_combined_flags_run_one(tmp_path, monkeypatch):
    from cotdata import update
    with mock.patch("cotdata.providers.cftc.update", return_value=_ok("cot_legacy")) as leg, \
         mock.patch("cotdata.providers.cftc_disagg.update", return_value=_ok("cot_disagg")) as dis, \
         mock.patch("cotdata.providers.cftc_tff.update", return_value=_ok("cot_tff")) as tff, \
         mock.patch("cotdata.providers.cftc_cit.update",
                    return_value={**_ok("cot_supplemental"), "coverage": None}) as cit:
        _argv(monkeypatch, tmp_path, "--cot-all")
        update.main()
        for m in (leg, dis, tff):
            assert m.call_args_list == [mock.call(), mock.call(combined=True)]
        assert cit.call_args_list == [mock.call()]

        for m in (leg, dis, tff, cit):
            m.reset_mock()
        _argv(monkeypatch, tmp_path, "--cot-tff-combined")
        update.main()
        assert tff.call_args_list == [mock.call(combined=True)]
        assert leg.call_count == dis.call_count == cit.call_count == 0

    import json
    status = json.loads((tmp_path / "status.json").read_text())
    assert status["last_run"]["kinds"] == ["cot_tff_combined"]


def test_a_combined_hard_failure_exits_nonzero(tmp_path, monkeypatch):
    from cotdata import update
    _argv(monkeypatch, tmp_path, "--cot-disagg-combined")
    with mock.patch("cotdata.providers.cftc_disagg.update",
                    return_value={"kind": "cot_disagg_combined", "ok": False, "wrote": 0}):
        with pytest.raises(SystemExit) as ei:
            update.main()
    assert "cot_disagg_combined" in str(ei.value)


# ── vintage: both bases of every annual zip, all seven statics ──────────────
def test_annual_sources_carry_both_bases_with_the_right_start_years(store_env):
    from cotdata import vintage

    def bases(year):
        return {(s.report_type, s.combined) for s in vintage.annual_sources(year)}

    assert bases(1994) == {("legacy", False)}
    assert bases(1995) == {("legacy", False), ("legacy", True)}
    assert bases(2009) == {("legacy", False), ("legacy", True), ("supplemental", True)}
    assert bases(2010) == {("legacy", False), ("legacy", True),
                           ("disaggregated", False), ("disaggregated", True),
                           ("tff", False), ("tff", True), ("supplemental", True)}
    urls = {(s.report_type, s.combined): s.url for s in vintage.annual_sources(2026)}
    assert urls[("legacy", True)].endswith("/dea_com_xls_2026.zip")
    assert urls[("disaggregated", True)].endswith("/com_disagg_txt_2026.zip")
    assert urls[("tff", True)].endswith("/com_fin_txt_2026.zip")
    assert urls[("legacy", False)].endswith("/dea_fut_xls_2026.zip")   # unchanged
    pre = {s.url for s in vintage.annual_sources(2003)}
    assert "https://www.cftc.gov/files/dea/history/deacom_xls_2003.zip" in pre


def test_all_seven_weekly_statics_are_captured_and_the_legacy_one_is_unchanged(store_env):
    from cotdata import vintage
    assert vintage.WEEKLY_STATICS[0] is vintage.WEEKLY_STATIC
    assert vintage.WEEKLY_STATIC.url.endswith("/newcot/deafut.txt")
    names = {s.url.rsplit("/", 1)[-1]: (s.report_type, s.combined) for s in vintage.WEEKLY_STATICS}
    assert names == {
        "deafut.txt": ("legacy", False), "deacom.txt": ("legacy", True),
        "f_disagg.txt": ("disaggregated", False), "c_disagg.txt": ("disaggregated", True),
        "FinFutWk.txt": ("tff", False), "FinComWk.txt": ("tff", True),
        "deacit.txt": ("supplemental", True)}
    assert all(s.source_kind == "weekly_static" and s.report_year is None
               for s in vintage.WEEKLY_STATICS)


def test_default_fetch_set_includes_the_statics_and_both_bases(store_env):
    from cotdata import vintage
    seen = []

    def spy(url, *, etag=None, last_modified=None):
        seen.append(url)
        raise AssertionError("no network in tests")

    vintage.fetch(http_get=spy, rate_limit_s=0,
                  now_fn=lambda: dt.datetime(2026, 7, 30, tzinfo=dt.timezone.utc))
    assert sum(u.endswith("/newcot/" + u.rsplit("/", 1)[-1]) for u in seen) == 7
    assert "https://www.cftc.gov/files/dea/history/dea_com_xls_2026.zip" in seen
    assert "https://www.cftc.gov/files/dea/history/dea_com_xls_2025.zip" in seen  # tripwire year


def test_snapshot_records_carry_the_basis(store_env):
    from cotdata import vintage
    com = next(s for s in vintage.annual_sources(2026)
               if s.report_type == "legacy" and s.combined)
    fut = next(s for s in vintage.annual_sources(2026)
               if s.report_type == "legacy" and not s.combined)

    def http(url, *, etag=None, last_modified=None):
        if url.endswith("deafut.txt"):
            raise OSError("down")
        return vintage.HttpResult(200, url.encode() + b"\x00" * 2048, etag='"e"',
                                  last_modified="Fri, 24 Jul 2026 19:27:59 GMT")

    res = vintage.fetch(sources=[fut, com, vintage.WEEKLY_STATIC], http_get=http, rate_limit_s=0,
                        now_fn=lambda: dt.datetime(2026, 7, 30, tzinfo=dt.timezone.utc))
    by_url = {r["source_url"]: r for r in res["records"]}
    assert by_url[fut.url]["combined"] is False
    assert by_url[com.url]["combined"] is True
    assert by_url[vintage.WEEKLY_STATIC.url]["combined"] is False   # the failure record too
    assert by_url[vintage.WEEKLY_STATIC.url]["note"].startswith("fetch failed")


# ── vintage ingest: the basis reaches the canonical rows and coverage ───────
def _legacy_wide(oi):
    idx = pd.DatetimeIndex([pd.Timestamp("2026-01-06"), pd.Timestamp("2026-01-13")],
                           name="Report_Date_as_MM_DD_YYYY")
    return pd.DataFrame({
        "CFTC_Contract_Market_Code": ["13874A", "13874A"],
        "Market_and_Exchange_Names": ["E-MINI S&P 500 - CME"] * 2,
        "Open_Interest_All": [oi, oi], "Comm_Positions_Long_All": [oi // 2, oi // 2],
        "Comm_Positions_Short_All": [oi // 2, oi // 2],
        "NonComm_Positions_Long_All": [oi // 4, oi // 4],
        "NonComm_Positions_Short_All": [oi // 4, oi // 4],
        "NonRept_Positions_Long_All": [oi // 4, oi // 4],
        "NonRept_Positions_Short_All": [oi // 4, oi // 4],
        "Traders_Comm_Long_All": [3, 3], "Traders_Comm_Short_All": [3, 3],
        "Traders_NonComm_Long_All": [4, 4], "Traders_NonComm_Short_All": [4, 4]}, index=idx)


def test_canonicaliser_passes_the_snapshot_basis_to_legacy(store_env, monkeypatch):
    from cotdata import vintage_cli
    from cotdata.providers import cftc
    monkeypatch.setattr(cftc, "_parse_zip", lambda p: _legacy_wide(100).reset_index())
    canon = vintage_cli._canonicalisers()
    assert set(canon["legacy"]("ignored", True)["combined"]) == {True}
    assert set(canon["legacy"]("ignored", False)["combined"]) == {False}


def test_canonicaliser_refuses_a_disagg_file_filed_under_the_wrong_basis(store_env, monkeypatch):
    from cotdata import vintage_cli, vintage_ingest
    from cotdata.providers import cftc_disagg
    monkeypatch.setattr(cftc_disagg, "_parse_zip", lambda p: "wide")
    monkeypatch.setattr(vintage_ingest, "canonicalize_disagg",
                        lambda wide: pd.DataFrame({"combined": [True, True]}))
    canon = vintage_cli._canonicalisers()
    assert len(canon["disaggregated"]("p", True)) == 2
    with pytest.raises(vintage_ingest.ValidationError, match="refusing to file one basis"):
        canon["disaggregated"]("p", False)


def test_both_bases_ingest_as_separate_rows_and_coverage_is_per_basis(store_env):
    from cotdata import vintage_ingest as vi
    fut = vi.canonicalize_legacy(_legacy_wide(100))
    com = vi.canonicalize_legacy(_legacy_wide(140), combined=True)
    vi.ingest_canonical(fut, snapshot_id="fut-1")
    r = vi.ingest_canonical(com, snapshot_id="com-1")
    assert r["revisions"] == 0            # a different basis is a new key, not a restatement
    obs = vi.read_observations()
    assert len(obs) == len(fut) + len(com)
    t = pd.Timestamp.utcnow() + pd.Timedelta(hours=1)
    assert set(vi.asof(t, report_type="legacy")["combined"]) == {False, True}
    only_com = vi.asof(t, report_type="legacy", combined=True)
    assert set(only_com["combined"]) == {True}
    assert only_com["open_interest"].eq(140).all()

    nat = vi.derive_coverage("legacy")                 # natural basis: futures-only, as before
    assert nat["weeks"].tolist() == [2]
    assert vi.coverage_path("legacy").name == "legacy.parquet"
    assert vi.coverage_path("legacy", combined=True).name == "legacy_combined.parquet"
    assert vi.coverage_path("supplemental").name == "supplemental.parquet"
    path, cov = vi.write_coverage("legacy", combined=True)
    assert path.name == "legacy_combined.parquet" and cov["weeks"].tolist() == [2]
    with pytest.raises(ValueError, match="combined basis"):
        vi.derive_coverage("supplemental", combined=False)


def test_coverage_cli_accepts_combined_and_leaves_supplemental_alone(store_env, capsys):
    from cotdata import vintage_cli
    from cotdata import vintage_ingest as vi
    vi.ingest_canonical(vi.canonicalize_legacy(_legacy_wide(140), combined=True), snapshot_id="c")
    assert vintage_cli.main(["coverage", "--report-type", "legacy", "--combined"]) == 0
    assert "legacy (combined) ->" in capsys.readouterr().out
    assert vintage_cli.main(["coverage", "--report-type", "legacy"]) == 0   # nothing futures-only
    assert "no legacy observations" in capsys.readouterr().out
    assert vintage_cli.main(["coverage"]) == 0                                # supplemental default
