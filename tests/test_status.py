import datetime as dt

from cotdata import status


def _manifest():
    return {
        "schema_version": 2,
        "prices": {
            "ES_backadj": {"last_date": "2026-07-14", "n_rows": 100, "updated_at": "2026-07-15T10:00:00Z"},
            "ES_unadj":   {"last_date": "2026-07-14", "n_rows": 100, "updated_at": "2026-07-15T10:00:00Z"},
            "NQ_backadj": {"last_date": "2026-07-05", "n_rows": 50,  "updated_at": "2026-07-06T10:00:00Z"},
        },
        "cot_legacy": {
            "001602": {"last_date": "2026-07-07", "n_rows": 1488, "updated_at": "2026-07-15T04:00:00Z"},
            # Skipped by the latest pass: 9 days behind its peers' write time. Lives
            # in a LIVE domain on purpose -- `prices` is retired, and the lag check
            # is deliberately silent there (see the retired-domain tests below).
            "099999": {"last_date": "2026-07-01", "n_rows": 12, "updated_at": "2026-07-06T04:00:00Z"},
        },
    }


def test_summarize_counts_and_newest():
    s = status.summarize(_manifest(), today=dt.date(2026, 7, 15))
    p = s["domains"]["prices"]
    assert p["entries"] == 3
    assert p["rows"] == 250
    assert p["newest"] == "2026-07-14"
    assert p["oldest"] == "2026-07-05"
    assert p["behind_today"] == 1
    assert s["schema_version"] == 2


def test_summarize_flags_lagging_entry():
    s = status.summarize(_manifest(), today=dt.date(2026, 7, 15))
    lagging = s["domains"]["cot_legacy"]["lagging"]
    # 099999 was last WRITTEN 2026-07-06, 9 days behind the domain's newest
    # write (2026-07-15) — i.e. the producer skipped it while its peers succeeded.
    assert [name for name, _, _ in lagging] == ["099999"]
    assert lagging[0][2] == 9


def test_old_data_is_not_lagging_if_it_was_written_this_pass():
    """Lag is about the PRODUCER skipping an entry, not about the data being old.

    Two legitimate old-but-correct cases, both seen in the real store: thin markets
    that drop out of the CFTC report for months and reappear (NKD, ZO), and retired
    hist_code contracts frozen by design. Both are rewritten every pass, so both
    carry a current updated_at and neither is a problem.
    """
    m = {
        "schema_version": 2,
        "cot_legacy": {
            "RTY_current": {"last_date": "2026-07-07", "n_rows": 100,
                            "updated_at": "2026-07-15T04:00:00Z"},
            "RTY_23977A":  {"last_date": "2018-06-05", "n_rows": 500,   # retired code
                            "updated_at": "2026-07-15T04:00:00Z"},
            "ZO_004603":   {"last_date": "2026-06-02", "n_rows": 900,   # intermittent
                            "updated_at": "2026-07-15T04:00:00Z"},
            "NQ_skipped":  {"last_date": "2026-07-07", "n_rows": 100,   # genuinely missed
                            "updated_at": "2026-06-20T04:00:00Z"},
        },
    }
    lagging = status.summarize(m, dt.date(2026, 7, 15))["domains"]["cot_legacy"]["lagging"]
    # Only the entry the producer actually skipped, even though two others carry
    # far older data.
    assert {n for n, _, _ in lagging} == {"NQ_skipped"}


def test_ignore_lag_still_suppresses_by_name():
    m = {
        "schema_version": 2,
        "cot_legacy": {
            "RTY_current": {"last_date": "2026-07-07", "n_rows": 100,
                            "updated_at": "2026-07-15T04:00:00Z"},
            "RTY_23977A":  {"last_date": "2018-06-05", "n_rows": 500,
                            "updated_at": "2026-06-01T04:00:00Z"},
        },
    }
    today = dt.date(2026, 7, 15)
    assert {n for n, _, _ in status.summarize(m, today)["domains"]["cot_legacy"]["lagging"]} \
        == {"RTY_23977A"}
    filt = status.summarize(m, today, ignore_lag={"RTY_23977A"})["domains"]["cot_legacy"]["lagging"]
    assert filt == []


def test_unusable_write_timestamp_is_reported_not_assumed_fine():
    """"Cannot check" must never render as "all current" — that is the exact failure
    this check exists to catch."""
    m = {"schema_version": 2,
         "cot_legacy": {"A": {"last_date": "2026-07-07", "n_rows": 1, "updated_at": "x"},
                        "B": {"last_date": "2026-07-07", "n_rows": 1}}}
    lagging = status.summarize(m, dt.date(2026, 7, 15))["domains"]["cot_legacy"]["lagging"]
    assert {n for n, _, _ in lagging} == {"A", "B"}


def test_empty_store_report():
    out = status.format_report({"schema_version": 2}, root="/tmp/store", today=dt.date(2026, 7, 15))
    assert "store is empty" in out


def test_format_report_contains_domain_and_lag_warning():
    out = status.format_report(_manifest(), root="/store", today=dt.date(2026, 7, 15))
    assert "prices" in out and "829" not in out  # our synthetic totals, not the real store
    assert "250" in out                            # prices row total
    assert "099999" in out                         # lag warning lists the stale entry
    assert "schema_version 2" in out


def test_schema_mismatch_note():
    out = status.format_report({"schema_version": 1, "prices": {}}, today=dt.date(2026, 7, 15))
    assert "target" in out  # warns that on-disk schema is behind the library target


def test_build_status_doc_flat_map_and_domains():
    doc = status.build_status_doc(_manifest(), today=dt.date(2026, 7, 15))
    # flat newest_data map is the primary polling primitive
    assert doc["newest_data"]["prices"] == "2026-07-14"
    assert doc["newest_data"]["cot_legacy"] == "2026-07-07"
    assert doc["domains"]["prices"]["rows"] == 250
    # `prices` is retired: it still reports entries/rows/newest, but never lag.
    assert doc["domains"]["prices"]["lagging"] == 0
    assert doc["domains"]["cot_legacy"]["lagging"] == 1     # 099999 is stale
    assert doc["schema_version"] == 2
    assert "generated_at" in doc


def test_write_status_file_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("COTDATA_STORE", str(tmp_path))
    import json

    from cotdata import status as st
    from cotdata import store
    # seed the store manifest via a real write
    idx = __import__("pandas").date_range("2026-07-10", periods=3, freq="D",
                                          name="Report_Date")
    df = __import__("pandas").DataFrame({"Open_Interest_All": [1, 2, 3]}, index=idx)
    store.write_cot_legacy("ES_13874A", df, source="test")

    path = st.write_status_file(last_run={"kinds": ["cot_legacy"], "ok": ["ES"], "failed": []})
    assert path.endswith("status.json")
    doc = json.loads((tmp_path / "status.json").read_text())
    assert doc["newest_data"]["cot_legacy"] == "2026-07-12"
    assert doc["last_run"]["kinds"] == ["cot_legacy"]
    assert doc["domains"]["cot_legacy"]["entries"] == 1


def test_run_summary_ok_and_failed():
    line = status.run_summary("prices update", ok=["ES", "NQ"], failed=[("GC", "boom")],
                              total_rows=1234, seconds=12.0, newest="2026-07-14")
    assert "2/3 OK" in line
    assert "1 failed" in line
    assert "1,234 rows" in line
    assert "newest 2026-07-14" in line
    assert "✗ GC: boom" in line


# ── retired domains ───────────────────────────────────────────────────────
# ADR-0007 moved bars and contract specs to marketdata. The files stay in a
# pre-move store, so --check keeps showing them — but it must not let them read
# as healthy. `lagging` is measured WITHIN a domain, so a uniformly frozen tree
# scores zero and looks exactly like a domain that ran cleanly minutes ago.
# On 2026-08-21 a consumer read newest_data.prices from such a store, got a
# 14-day-old date with lagging: 0, and had no signal it was reading a dead tree.

def test_the_retired_domains_are_flagged_as_retired():
    s = status.summarize(_manifest(), today=dt.date(2026, 7, 15))
    assert s["domains"]["prices"]["retired"] is True
    assert s["domains"]["cot_legacy"]["retired"] is False


def test_a_retired_domain_is_never_reported_as_lagging():
    """Not because it is healthy — because the within-domain check cannot say
    anything true about a domain with no producer pass. NQ_backadj is 9 days behind
    its peers and would be flagged in any live domain."""
    s = status.summarize(_manifest(), today=dt.date(2026, 7, 15))
    assert s["domains"]["prices"]["lagging"] == []


def test_the_report_names_the_retired_domains_and_says_where_the_data_went():
    out = status.format_report(_manifest(), today=dt.date(2026, 7, 15))
    assert "RETIRED" in out
    assert "marketdata" in out


def test_the_all_clear_does_not_speak_for_the_retired_domains():
    """A store whose live domains are all current still holds a frozen `prices`
    tree. The all-clear has to say WHICH entries it is vouching for, or it vouches
    for the dead one too -- which is how a 14-day-old tree reads as healthy."""
    m = _manifest()
    del m["cot_legacy"]["099999"]          # no lag left in any live domain
    out = status.format_report(m, today=dt.date(2026, 7, 15))
    assert "every entry in the live domains" in out
    assert "RETIRED" in out


def test_status_json_carries_retired_so_a_poller_can_see_it():
    """The machine-readable contract needs the flag too: a poller keyed on
    newest_data[<domain>] for a frozen domain waits forever without ever erroring."""
    doc = status.build_status_doc(_manifest(), today=dt.date(2026, 7, 15))
    assert doc["domains"]["prices"]["retired"] is True
    assert doc["domains"]["cot_legacy"]["retired"] is False


def test_a_live_domain_still_reports_lagging_entries():
    """Guard against the retired suppression leaking into live domains."""
    m = _manifest()
    m["cot_legacy"]["999999"] = {"last_date": "2026-07-07", "n_rows": 10,
                                 "updated_at": "2026-06-01T04:00:00Z"}
    s = status.summarize(m, today=dt.date(2026, 7, 15))
    assert {n for n, _, _ in s["domains"]["cot_legacy"]["lagging"]} == {"999999", "099999"}
    # ...while the retired domain stays silent even with a stale entry of its own.
    assert s["domains"]["prices"]["lagging"] == []
