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


# ── `announced`: release dates CFTC republished after a disruption ──────────
# When a disruption moves publication, CFTC does not describe the new dates in prose. It
# publishes a TABLE on the Special Announcements page:
#
#     COT Report Date | Original Publish Date | New Publish Date
#     09/30/2025      | 10/03/2025            | 11/19/2025+
#
# That is the whole reason this tier is buildable. An earlier pass recorded the `announced`
# tier as unreachable, on the grounds that extracting a (report_date, release_date) pair
# from free-text announcement prose would be guessing, and that a guessed date is worse
# than an honest `derived` one because it carries a provenance flag claiming it was
# announced. The reasoning was right; the premise was not checked. Measured 2026-08-02: the
# Oct–Dec 2025 appropriations-lapse backlog, the named target and §6's single largest PIT
# hole, is published as an exact two-column mapping.
#
# So this parses TABLES and refuses PROSE, which keeps the original objection intact. Of
# the ~100 announcements on the page (2008 onward), the great majority are prose: holiday
# shifts, reporting-firm corrections, a National Day of Mourning closure. None of those
# yield an exact pair without inference, so none are read here and their weeks stay on
# `scheduled` or `derived` where they belong.
#
# Header matching is load-bearing rather than defensive. The page carries five tables and
# only two are release dates; the others are a contract-rename table (Contract / Exchange /
# Old Name / New Name) and two market lists. A parser that took "the tables on the page"
# would file contract renames as publication dates.
_ANNOUNCED_REQUIRED_HEADERS = ("cot report date", "new publish date")
_MDY = re.compile(r"\b(\d{1,2}/\d{1,2}/20\d{2})\b")
_ANNOUNCEMENT_HEADING = re.compile(r"([A-Z][a-z]+ \d{1,2},\s*\d{4})\s*:")


def _table_rows(table_html: str) -> list[list[str]]:
    """Cell text per ``<tr>``. Row-wise, not a flat cell list, because a flat list plus
    "group into threes" desynchronises on the footnote markers: the 2025-11-18 table puts
    its ``+`` in a cell of its own, so every subsequent triple would shift by one and the
    dates would be silently transposed."""
    rows = []
    for rm in re.finditer(r"<tr[^>]*>(.*?)</tr>", table_html, re.S | re.I):
        cells = [_strip_tags(c).replace("\xa0", " ").strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", rm.group(1), re.S | re.I)]
        rows.append(cells)
    return rows


def _announcement_date_before(html: str, pos: int) -> dt.date | None:
    """The date of the announcement a table belongs to: the nearest ``Month D, YYYY:``
    heading before it. Used to order two tables that cover the same weeks, since CFTC
    republishes a superseding table rather than editing the previous one."""
    text = _strip_tags(html[:pos])
    matches = _ANNOUNCEMENT_HEADING.findall(text)
    if not matches:
        return None
    try:
        return pd.Timestamp(matches[-1]).date()
    except (ValueError, TypeError):
        return None


def parse_announced_release_dates(html: str) -> pd.DataFrame:
    """Parse republished release dates out of the Special Announcements page.

    Raises rather than returning an empty frame when no release-date table is found. The
    page is cumulative history back to 2008, so the 2025 tables cannot legitimately vanish;
    an empty parse means the layout moved, and a silent empty here is indistinguishable
    from "CFTC never rescheduled anything", which would leave the backlog weeks on
    `derived` while reporting success. Same reasoning as ``parse_release_schedule``.
    """
    rows, tables_seen = [], 0
    for m in re.finditer(r"<table.*?</table>", html, re.S | re.I):
        trs = _table_rows(m.group(0))
        if not trs:
            continue
        header = " | ".join(trs[0]).lower()
        if not all(h in header for h in _ANNOUNCED_REQUIRED_HEADERS):
            continue
        tables_seen += 1
        announced_on = _announcement_date_before(html, m.start())
        for cells in trs[1:]:
            dates = [d for c in cells for d in _MDY.findall(c)]
            # Exactly three: report date, original publish date, new publish date. A row
            # with any other count is a footnote or a spanned cell, and guessing which
            # column is which from a short row is the failure this whole tier avoids.
            if len(dates) != 3:
                continue
            report = pd.Timestamp(dates[0]).date()
            release = pd.Timestamp(dates[2]).date()
            # A republication moves a date later, never earlier. If this fails the columns
            # are not what the header said, so drop the row rather than record a release
            # that precedes its own report date.
            if release < report:
                continue
            rows.append({
                "report_date": pd.Timestamp(report),
                "release_date": pd.Timestamp(release),
                "source": "announced",
                "note": ("CFTC special announcement"
                         + (f" {announced_on:%Y-%m-%d}" if announced_on else "")),
                "announced_on": pd.Timestamp(announced_on) if announced_on else pd.NaT,
                "ingested_at": pd.Timestamp.now("UTC"),
            })
    if not tables_seen:
        raise ValueError(
            "special announcements: no table with the "
            f"{' / '.join(_ANNOUNCED_REQUIRED_HEADERS)} headers was found. The page is "
            "cumulative, so this means the layout changed rather than that nothing was "
            "ever rescheduled.")
    if not rows:
        raise ValueError("special announcements: release-date table found but no rows "
                         "parsed out of it.")
    out = pd.DataFrame(rows)
    # ONLY the newest table, not the newest row per week. A table is a whole replacement
    # PLAN, not a set of independent per-week corrections: each one ends with a row marked
    # "COT publication returns to normal schedule", so its final row is a claim about
    # everything after it too.
    #
    # Merging row-wise looks more thorough and is wrong. Measured on the live page
    # 2026-08-02: the 2025-11-18 table was a slow catch-up running to 2026-01-23, and the
    # 2025-12-09 table ("CFTC to Accelerate Publication of Backlogged COT Data") replaced
    # it with a faster one finishing 2025-12-29. Row-wise, the four weeks past the newer
    # table's end (report dates 2025-12-30 through 2026-01-20) survive from the superseded
    # plan, and three of the four disagree with CFTC's own published 2026 calendar by a
    # week: 2025-12-30 would be recorded as released 2026-01-13 when the calendar says
    # 2026-01-05. Because `announced` outranks `scheduled`, those stale rows would have
    # OVERWRITTEN correct dates with a provenance flag claiming they were announced, which
    # is worse than not having built this tier at all.
    #
    # Weeks the newest plan does not cover fall back to `scheduled`, then `derived`. That
    # is the safe direction: it loses precision rather than asserting a false fact.
    newest = out["announced_on"].max()
    if pd.notna(newest):
        out = out[out["announced_on"] == newest]
    out = (out.drop_duplicates(subset=["report_date"], keep="last")
              .drop(columns=["announced_on"])
              .sort_values("report_date")
              .reset_index(drop=True))
    return out


def sync_announced(*, fetch_html=None) -> dict:
    """Fetch the announcements page and merge its republished release dates. Idempotent.

    Writes into the same ``release_schedule.parquet`` as the `scheduled` tier, keyed by
    ``(report_date, source)`` so the two coexist per week and ``_schedule_map`` picks the
    higher-ranked one. No new plumbing: ``write_release_schedule`` already accepted a
    ``source`` column and ``_SOURCE_RANK`` already put `announced` above `scheduled`. The
    only thing that was ever missing here was a producer.
    """
    fetch_html = fetch_html or _fetch_html
    parsed = parse_announced_release_dates(fetch_html(ANNOUNCEMENTS_URL))
    existing = read_release_schedule()
    merged = (pd.concat([existing, parsed], ignore_index=True)
              if not existing.empty else parsed)
    merged = merged.drop_duplicates(subset=["report_date", "source"], keep="last")
    write_release_schedule(merged)
    return {"announced": len(parsed),
            "earliest": parsed["report_date"].min().date().isoformat(),
            "latest": parsed["report_date"].max().date().isoformat()}


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

    # Under the SAME lock ingest uses. This is a read-modify-write of every observations
    # partition, so running it concurrently with an ingest silently drops that ingest's
    # appended rows: backfill reads the file, the ingest appends and writes, backfill
    # writes back what it read. The store is then internally inconsistent in the worst
    # way available here, holding a revision row asserting a change to a value that no
    # longer exists in observations/. Each file's write is already atomic; atomicity per
    # file was never the missing piece.
    with vi._WriteLock(vintage.vintage_root()):
        return _backfill_locked(obs_dir, smap, counts, observed_window_days)


def _backfill_locked(obs_dir, smap, counts, observed_window_days: int) -> dict:
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


def _main_region(html: str) -> str:
    """The page's content region, or the whole document if its markers are absent.

    Scoping matters more than it looks. Scraping the document scraped the site's NAV: of
    the 95 rows this had stored by 2026-08-02, every single one was a menu entry, a footer
    link or a market-name list item ("Contact Us", "Privacy Policy", "CBT Corn (CFTC ID
    002602)"), and ``announcement_date`` was null on all 95. The store held no
    announcement at all while reporting 95 of them.
    """
    start = html.find('id="main-content"')
    if start < 0:
        return html
    end = html.find("<footer", start)
    return html[start:end if end > 0 else len(html)]


def _parse_announcements(html: str, *, url: str, scraped_at) -> list[dict]:
    """Best-effort: one row per announcement, keyed on its ``Month D, YYYY:`` heading.

    Never raises on unrecognised markup — a layout change must degrade to fewer rows, not
    a crash. That is why this stays heading-driven and text-based while
    ``parse_announced_release_dates`` raises: this is a corpus for a human to read, that
    one produces dates the store will treat as fact.

    Keyed on the heading rather than on ``<li>`` because the announcements are not list
    items. They are a date heading followed by prose, and sometimes a table. The heading
    is also what carries the date, which is the field the schema exists for.
    """
    rows = []
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", _main_region(html)))
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    hits = list(_ANNOUNCEMENT_HEADING.finditer(text))
    for i, m in enumerate(hits):
        body = text[m.end():hits[i + 1].start() if i + 1 < len(hits) else len(text)]
        try:
            date = pd.Timestamp(m.group(1)).date()
        except (ValueError, TypeError):
            date = None
        rows.append({
            "announcement_date": pd.Timestamp(date) if date else pd.NaT,
            # Heading INCLUDED, so a row still reads as the entry a human would see on
            # the page, and so the date survives in the text for any later extraction of
            # the affected_* columns. Bounded because the appropriations-lapse entries
            # inline a whole table, and the point of this column is attribution rather
            # than a second copy of the page.
            "raw_text": f"{m.group(0)} {body.strip()}".strip()[:2000],
            "affected_report_types": None,
            "affected_markets": None, "affected_date_from": pd.NaT,
            "affected_date_to": pd.NaT, "url": url,
            "scraped_at": pd.Timestamp(scraped_at),
        })
    return rows
