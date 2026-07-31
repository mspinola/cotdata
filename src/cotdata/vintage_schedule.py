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

import csv
import datetime as dt
import re
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from . import config, vintage
from . import vintage_ingest as vi

# `published` outranks `observed`: it is the weekly-static HTTP Last-Modified, a TRUE
# publication timestamp (spike 2026-07-30), whereas `observed` is only the first time WE
# saw the report — accurate to the polling interval. They must not share a bucket, so it
# stays possible to tell later which weeks carry a real publication time.
# Populated by published_from_snapshots() below, folded into backfill automatically.
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


# ── `published`: true publication time from the weekly static ───────────────
# The weekly static is a headerless positional CSV covering exactly ONE report date
# (measured 2026-07-30: 365 rows, 129 columns, a single distinct value in field 2), so
# mapping a retained snapshot to its report_date reads one field rather than parsing the
# file. Its HTTP Last-Modified is a true publication timestamp, which is what makes this
# strictly better than `observed` (accurate only to the polling interval).
_WEEKLY_STATIC_DATE_FIELD = 2
PUBLISH_TZ = "America/New_York"  # CFTC publishes on ET; convert before taking a date


def report_date_of_weekly_static(path) -> dt.date | None:
    """The single report_date a retained weekly-static file covers, or None."""
    try:
        with open(path, newline="") as fh:
            for row in csv.reader(fh):
                if len(row) > _WEEKLY_STATIC_DATE_FIELD:
                    try:
                        return pd.Timestamp(row[_WEEKLY_STATIC_DATE_FIELD]).date()
                    except (ValueError, TypeError):
                        return None
    except OSError:
        return None
    return None


def _last_modified_to_release_date(lm: str) -> dt.date | None:
    """RFC 2822 Last-Modified -> publication DATE in ET."""
    try:
        ts = parsedate_to_datetime(lm)
    except (TypeError, ValueError):
        return None
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=dt.timezone.utc)
    return ts.astimezone(ZoneInfo(PUBLISH_TZ)).date()


def published_from_snapshots(snapshots=None, *, store_root=None) -> pd.DataFrame:
    """Derive `published` release-schedule rows from retained weekly-static snapshots.

    Forward-only by nature: the weekly static holds one week and is overwritten, so this
    covers weeks captured from the first run onward and can never reach back. Historical
    weeks stay on announced/scheduled/derived.
    """
    from . import vintage
    if snapshots is None:
        snapshots = vintage.read_snapshots()
    root = Path(store_root) if store_root else config.store_root()

    rows, seen = [], set()
    for s in snapshots:
        if s.get("source_kind") != "weekly_static":
            continue
        lp, lm = s.get("local_path"), s.get("http_last_modified")
        if not lp or not lm:
            continue
        p = Path(lp)
        if not p.is_absolute():
            p = root / lp
        rd = report_date_of_weekly_static(p)
        rel = _last_modified_to_release_date(lm)
        if rd is None or rel is None or rd in seen:
            continue
        seen.add(rd)
        rows.append({
            "report_date": pd.Timestamp(rd),
            "release_date": pd.Timestamp(rel),
            "source": "published",
            "note": f"weekly-static Last-Modified ({lm})",
            "ingested_at": pd.Timestamp(s.get("retrieved_at") or pd.Timestamp.now("UTC")),
        })
    if not rows:
        return pd.DataFrame(columns=["report_date", "release_date", "source", "note",
                                     "ingested_at"])
    return pd.DataFrame(rows)


def sync_published() -> dict:
    """Merge derived `published` rows into release_schedule.parquet. Idempotent."""
    derived = published_from_snapshots()
    if derived.empty:
        return {"published": 0}
    existing = read_release_schedule()
    merged = pd.concat([existing, derived], ignore_index=True) if not existing.empty else derived
    merged = merged.drop_duplicates(subset=["report_date", "source"], keep="last")
    write_release_schedule(merged)
    return {"published": len(derived)}


# ── `scheduled`: the CFTC published release calendar ────────────────────────
# The page lists RELEASE dates for one year, month by month, marking holiday-delayed ones
# with an asterisk ("*Delayed release date due to a federal holiday."). Those asterisks are
# the entire value here: on a normal week `derived` (report_date + 3, weekend-adjusted)
# already lands on the right Friday, so seeding the calendar changes few dates. What it
# changes is (a) the handful of holiday weeks where derived is wrong by one to three days,
# and (b) the PROVENANCE of every matched row, from `derived` (a guess) to `scheduled` (a
# published fact) — which is what lets strict point-in-time evaluation trust the date.
_MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}


def _strip_tags(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def report_date_for_release(release: dt.date) -> dt.date:
    """The Tuesday whose positions a given release reports.

    COT is Tuesday-dated and published the following Friday. A federal holiday pushes the
    RELEASE later but never moves the as-of date, so the report date is the latest Tuesday
    at least three days before the release. That rule handles both the normal Friday case
    (Tuesday + 3) and a delayed Monday release (the previous week's Tuesday), without
    needing a holiday calendar of our own.
    """
    d = pd.Timestamp(release).date() - dt.timedelta(days=3)
    while d.weekday() != 1:  # 1 == Tuesday
        d -= dt.timedelta(days=1)
    return d


def parse_release_schedule(html: str) -> pd.DataFrame:
    """Parse the CFTC release-schedule page into schedule rows.

    Tag-tolerant by design: the year lives inside nested markup
    (``<h3><strong>2026 Release Schedule</strong></h3>``), so it is read from tag-stripped
    text rather than from an assumed tag shape. Raises rather than returning an empty
    frame if the year or table cannot be found — a silent empty parse would look exactly
    like "CFTC published nothing", and would quietly leave every row on `derived`.
    """
    text = _strip_tags(html)
    ym = re.search(r"(20\d{2})\s+Release Schedule", text, re.I)
    if not ym:
        raise ValueError("release schedule: could not find the '<YYYY> Release Schedule' "
                         "heading; the page layout has probably changed.")
    year = int(ym.group(1))

    tm = re.search(r"<table.*?</table>", html, re.S | re.I)
    if not tm:
        raise ValueError("release schedule: no <table> found on the page.")
    cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tm.group(0), re.S | re.I)

    rows, month = [], None
    for cell in cells:
        val = _strip_tags(cell).replace("\xa0", " ").replace("&nbsp;", " ").strip()
        if not val:
            continue
        if val in _MONTHS:
            month = _MONTHS[val]
            continue
        m = re.fullmatch(r"(\d{1,2})\s*(\*?)", val)
        if not m or month is None:
            continue
        release = dt.date(year, month, int(m.group(1)))
        delayed = bool(m.group(2))
        rows.append({
            "report_date": pd.Timestamp(report_date_for_release(release)),
            "release_date": pd.Timestamp(release),
            "source": "scheduled",
            "note": ("delayed by a federal holiday" if delayed
                     else "published CFTC release schedule"),
            "ingested_at": pd.Timestamp.now("UTC"),
        })
    if not rows:
        raise ValueError("release schedule: table found but no dates parsed out of it.")
    return pd.DataFrame(rows)


def sync_release_schedule(*, fetch_html=None) -> dict:
    """Fetch and store the published release calendar. Idempotent.

    Covers ONE year: the page only ever shows the current schedule, and CFTC does not
    publish past calendars here, so earlier years cannot be recovered this way and stay
    on `announced` or `derived`.
    """
    fetch_html = fetch_html or _fetch_html
    parsed = parse_release_schedule(fetch_html(RELEASE_SCHEDULE_URL))
    existing = read_release_schedule()
    merged = (pd.concat([existing, parsed], ignore_index=True)
              if not existing.empty else parsed)
    merged = merged.drop_duplicates(subset=["report_date", "source"], keep="last")
    write_release_schedule(merged)
    delayed = int((parsed["note"] == "delayed by a federal holiday").sum())
    return {"scheduled": len(parsed), "holiday_delayed": delayed}


# ── Backfill ────────────────────────────────────────────────────────────────
_SOURCE_RANK = {"published": 3, "announced": 2, "scheduled": 1}


def _schedule_map(schedule: pd.DataFrame) -> dict:
    """report_date -> (release_date, source) from the schedule table, keeping the
    highest-precedence source per date (published > announced > scheduled)."""
    out: dict = {}
    for _, r in schedule.iterrows():
        rd = pd.Timestamp(r["report_date"]).normalize()
        src = r.get("source", "scheduled")
        prev = out.get(rd)
        if prev is None or _SOURCE_RANK.get(src, 0) > _SOURCE_RANK.get(prev[1], 0):
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
        # Production path: fold in `published` rows derived from retained weekly statics,
        # so a plain `cotdata-schedule backfill` picks up true publication timestamps
        # without a separate step. Tests pass an explicit schedule to isolate precedence.
        stored = read_release_schedule()
        derived = published_from_snapshots()
        parts = [d for d in (stored, derived) if not d.empty]
        schedule = pd.concat(parts, ignore_index=True) if parts else stored
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
            published = sched[0] if sched and sched[1] == "published" else None
            announced = sched[0] if sched and sched[1] == "announced" else None
            scheduled = sched[0] if sched and sched[1] == "scheduled" else None
            rdate, src = resolve_release_date(
                rd, published=published, observed=observed,
                announced=announced, scheduled=scheduled)
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
        fresh = pd.DataFrame(rows)
        existing = read_announcements()
        # Skip the concat when there is nothing to merge into. Concatenating an all-empty
        # frame is deprecated in pandas and changes dtype inference, so an empty store
        # would otherwise warn now and silently shift column dtypes later.
        merged = pd.concat([existing, fresh], ignore_index=True) if not existing.empty else fresh
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
