"""Release-schedule + Special-Announcements ingestion, and the release-date backfill.

The annual CFTC zips carry only ``report_date``. A release date that embeds no lookahead
must be *resolved* and its provenance recorded (§4.6), in precedence order:

    observed  > announced > scheduled > derived > unknown

- ``observed``  is stamped at CAPTURE time from the weekly-static Last-Modified (a true
  publication timestamp — spike 2026-07-30). Not manufactured here.
- ``announced`` comes from the Special Announcements page (holiday shifts, and the
  Oct–Dec 2025 appropriations-lapse backlog where release trails report_date by weeks).
- ``scheduled`` comes from the CFTC published release schedule (normal weeks).
- ``derived``   is ``report_date + 3d`` (weekend-adjusted) — the fallback that fails on
  exactly the weeks that matter, which is why the source flag exists: downstream code
  must be able to exclude ``derived`` rows from strict PIT evaluation.

Scraping is best-effort (store raw text always). The resolution logic is a pure function
so it is fully testable offline. See docs/design/cot_vintage.md.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd

from . import vintage
from . import vintage_ingest as vi

# `published` outranks `observed`: it is the weekly-static HTTP Last-Modified, a TRUE
# publication timestamp (spike 2026-07-30), whereas `observed` is only the first time WE
# saw the report — accurate to the polling interval. They must not share a bucket, so it
# stays possible to tell later which weeks carry a real publication time.
# NOTE: nothing populates `published` yet — mapping a weekly static to its report_date
# requires parsing that file, which handoff §10 defers. The slot and precedence are here
# so the wiring lands without a taxonomy migration.
PRECEDENCE = ("published", "observed", "announced", "scheduled", "derived", "unknown")


# ── Paths ───────────────────────────────────────────────────────────────────
def release_schedule_path() -> Path:
    return vintage.vintage_root() / "release_schedule.parquet"


def announcements_path() -> Path:
    return vintage.vintage_root() / "announcements.parquet"


# ── Derivation + resolution (pure) ──────────────────────────────────────────
def derive_release_date(report_date) -> dt.date:
    """Fallback: report_date + 3 days, pushed off a weekend. Matches the normal
    Tuesday→Friday COT cadence. Deliberately holiday-naive — that is what ``announced``
    and ``scheduled`` are for."""
    d = (pd.Timestamp(report_date).normalize() + pd.Timedelta(days=3)).date()
    while d.weekday() >= 5:  # Sat/Sun → next Monday
        d += dt.timedelta(days=1)
    return d


def resolve_release_date(report_date, *, published=None, observed=None, announced=None,
                         scheduled=None) -> tuple[dt.date | None, str]:
    """Return ``(release_date, source)`` by the §4.6 precedence. Each argument is an
    optional resolved date for this report_date; the first present wins."""
    for value, source in ((published, "published"), (observed, "observed"),
                          (announced, "announced"), (scheduled, "scheduled")):
        if value is not None and not pd.isna(value):
            return pd.Timestamp(value).date(), source
    return derive_release_date(report_date), "derived"


# ── Store I/O ───────────────────────────────────────────────────────────────
def _write(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    df.to_parquet(tmp)
    tmp.replace(path)


def read_release_schedule() -> pd.DataFrame:
    p = release_schedule_path()
    return pd.read_parquet(p) if p.exists() else pd.DataFrame(
        columns=["report_date", "release_date", "source", "note", "ingested_at"])


def read_announcements() -> pd.DataFrame:
    p = announcements_path()
    return pd.read_parquet(p) if p.exists() else pd.DataFrame(
        columns=["announcement_date", "raw_text", "affected_report_types",
                 "affected_markets", "affected_date_from", "affected_date_to",
                 "url", "scraped_at"])


def write_release_schedule(df: pd.DataFrame) -> None:
    _write(release_schedule_path(), df)


def write_announcements(df: pd.DataFrame) -> None:
    _write(announcements_path(), df)


# ── Backfill ────────────────────────────────────────────────────────────────
def _schedule_map(schedule: pd.DataFrame) -> dict:
    """report_date -> (release_date, source) from the schedule table. `announced` rows
    win over `scheduled` rows for the same report_date."""
    out: dict = {}
    for _, r in schedule.iterrows():
        rd = pd.Timestamp(r["report_date"]).normalize()
        src = r.get("source", "scheduled")
        prev = out.get(rd)
        if prev is None or (prev[1] == "scheduled" and src == "announced"):
            out[rd] = (pd.Timestamp(r["release_date"]).date(), src)
    return out


def backfill(*, schedule: pd.DataFrame | None = None,
             observed_window_days: int = 4) -> dict:
    """Resolve ``release_date`` / ``release_date_source`` across all stored observations
    and write them back in place. Returns coverage counts keyed by source.

    ``observed`` is honoured only when the recorded ``observed_at`` is within
    ``observed_window_days`` of ``report_date`` — i.e. the row was captured live, not
    bulk-ingested long afterwards (a bulk ingest's observed_at is not a release date).
    """
    if schedule is None:
        schedule = read_release_schedule()
    smap = _schedule_map(schedule)

    obs_dir = vi._obs_dir()
    counts = dict.fromkeys(PRECEDENCE, 0)
    if not obs_dir.exists():
        return counts

    for part in sorted(obs_dir.glob("report_year=*/observations.parquet")):
        df = pd.read_parquet(part)
        if df.empty:
            continue
        # FIRST sighting per report_date, not the row's own observed_at. A row is one
        # VINTAGE of a natural key: a revised row's observed_at can be months after
        # publication, so using it per-row would make every `observed` release date
        # systematically late by however long that row went unrevised.
        norm_rd = df["report_date"].map(lambda x: pd.Timestamp(x).normalize())
        first_seen = df.assign(_rd=norm_rd).groupby("_rd")["observed_at"].min()

        rel_dates, rel_srcs = [], []
        for _, r in df.iterrows():
            rd = pd.Timestamp(r["report_date"]).normalize()
            observed = None
            oa = first_seen.get(rd)
            if oa is not None and not pd.isna(oa):
                oa = vi._naive_utc(oa)
                # Directional, NOT abs(): a report cannot be observed BEFORE its own
                # as-of date, so a negative offset means clock skew or a timezone bug.
                # abs() would silently absorb that as a valid release date.
                delta = (oa.normalize() - rd).days
                if 0 <= delta <= observed_window_days:
                    observed = oa
            sched = smap.get(rd)
            announced = sched[0] if sched and sched[1] == "announced" else None
            scheduled = sched[0] if sched and sched[1] == "scheduled" else None
            rdate, src = resolve_release_date(
                rd, observed=observed, announced=announced, scheduled=scheduled)
            rel_dates.append(pd.Timestamp(rdate) if rdate is not None else pd.NaT)
            rel_srcs.append(src)
            counts[src] += 1
        df["release_date"] = rel_dates
        df["release_date_source"] = rel_srcs
        tmp = part.with_suffix(".parquet.tmp")
        df.to_parquet(tmp)
        tmp.replace(part)
    return counts


# ── Best-effort scrape (network; injectable for offline use) ────────────────
RELEASE_SCHEDULE_URL = ("https://www.cftc.gov/MarketReports/CommitmentsofTraders/"
                        "ReleaseSchedule/index.htm")
ANNOUNCEMENTS_URL = ("https://www.cftc.gov/MarketReports/CommitmentsofTraders/"
                     "HistoricalSpecialAnnouncements/index.htm")


def _fetch_html(url: str) -> str:
    import requests
    r = requests.get(url, headers={"User-Agent": vintage.user_agent()}, timeout=120)
    r.raise_for_status()
    return r.text


def sync(*, fetch_html=_fetch_html, now=None) -> dict:
    """Scrape the announcements page into ``announcements.parquet`` (raw text always
    retained). Structured extraction is best-effort; attribution only needs the text
    and date. Returns ``{"announcements": n}``. The release schedule is seeded by the
    caller (small, and its HTML layout changes) rather than scraped heuristically here.
    """
    now = now or dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    html = fetch_html(ANNOUNCEMENTS_URL)
    rows = _parse_announcements(html, url=ANNOUNCEMENTS_URL, scraped_at=now)
    if rows:
        existing = read_announcements()
        merged = pd.concat([existing, pd.DataFrame(rows)], ignore_index=True)
        merged = merged.drop_duplicates(subset=["announcement_date", "raw_text"])
        write_announcements(merged)
    return {"announcements": len(rows)}


def _parse_announcements(html: str, *, url: str, scraped_at) -> list[dict]:
    """Best-effort: pull list items / paragraphs mentioning a date. Never raises on
    unrecognised markup — a layout change must degrade to fewer rows, not a crash."""
    import re
    rows = []
    for m in re.finditer(r"<li[^>]*>(.*?)</li>", html, flags=re.S | re.I):
        text = re.sub(r"<[^>]+>", " ", m.group(1))
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        date = None
        dm = re.search(r"([A-Z][a-z]+ \d{1,2}, \d{4})", text)
        if dm:
            try:
                date = pd.Timestamp(dm.group(1)).date()
            except (ValueError, TypeError):
                date = None
        rows.append({
            "announcement_date": pd.Timestamp(date) if date else pd.NaT,
            "raw_text": text, "affected_report_types": None,
            "affected_markets": None, "affected_date_from": pd.NaT,
            "affected_date_to": pd.NaT, "url": url,
            "scraped_at": pd.Timestamp(scraped_at),
        })
    return rows
