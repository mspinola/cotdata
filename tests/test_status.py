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
            "001602": {"last_date": "2026-07-07", "n_rows": 1488, "updated_at": "2026-07-14T04:00:00Z"},
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
    lagging = s["domains"]["prices"]["lagging"]
    # NQ_backadj (2026-07-05) is 9 days behind the domain newest (2026-07-14).
    assert [name for name, _, _ in lagging] == ["NQ_backadj"]
    assert lagging[0][2] == 9


def test_empty_store_report():
    out = status.format_report({"schema_version": 2}, root="/tmp/store", today=dt.date(2026, 7, 15))
    assert "store is empty" in out


def test_format_report_contains_domain_and_lag_warning():
    out = status.format_report(_manifest(), root="/store", today=dt.date(2026, 7, 15))
    assert "prices" in out and "829" not in out  # our synthetic totals, not the real store
    assert "250" in out                            # prices row total
    assert "NQ_backadj" in out                     # lag warning lists the stale entry
    assert "schema_version 2" in out


def test_schema_mismatch_note():
    out = status.format_report({"schema_version": 1, "prices": {}}, today=dt.date(2026, 7, 15))
    assert "target" in out  # warns that on-disk schema is behind the library target


def test_run_summary_ok_and_failed():
    line = status.run_summary("prices update", ok=["ES", "NQ"], failed=[("GC", "boom")],
                              total_rows=1234, seconds=12.0, newest="2026-07-14")
    assert "2/3 OK" in line
    assert "1 failed" in line
    assert "1,234 rows" in line
    assert "newest 2026-07-14" in line
    assert "✗ GC: boom" in line
