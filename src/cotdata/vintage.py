"""COT vintage capture + provenance — step 1 of the crowdmon-futures build (handoff v0.2).

This module is purely ADDITIVE alongside the current-state store: it never touches the
existing ``cot_legacy/`` etc. write path, so existing consumers see byte-identical output.

Layout (all under ``$COTDATA_STORE/vintage/``):

    raw/{source_kind}/{year}/{retrieved_at}_{sha8}.{ext}   immutable, never rewritten
    observations/report_year=YYYY/*.parquet                change-only bitemporal rows
    revisions/detected_year=YYYY/*.parquet                 append-only, field-level
    release_schedule.parquet
    announcements.parquet
    snapshots.json                                         vintage provenance index

Provenance lives in its OWN ``vintage/snapshots.json`` rather than a block in the cot-half
manifest, because ``store.reconcile_manifest`` ghost-prunes any manifest entry without a
matching ``{name}.parquet`` — raw snapshot ids are not parquet files and would be wiped.
A self-owned file also matches the repo's existing "one writer per manifest file" split.

See docs/design/cot_vintage.md and crucible-stack ADR-0008.

COMMIT 1 (this file, first pass): raw snapshot CAPTURE only — fetch, hash, retain,
record. The immutable landing zone is the only time-critical piece: an uncaptured
weekly release is irrecoverable, whereas ingest/diff can run retroactively over
retained raw bytes at any later point.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from . import config

# Descriptive UA, overridable per deployment via COTDATA_USER_AGENT. Deliberately not a
# personal address in committed source: appropriate to send to CFTC, not to publish.
_DEFAULT_UA = "cotdata-vintage/0.1 (+https://github.com/mspinola/cotdata)"
_RATE_LIMIT_S = 1.0  # polite spacing between real network requests
SCHEMA_VERSION = 1

# Byte-level floor per source kind: the analogue of §5's row-count band. A truncated or
# empty 200 body would otherwise be retained as a legitimate snapshot (``content is None``
# does not catch ``b""``). Deliberately conservative — the smallest real annual zip
# (1986) is far above this, so the floor only ever catches a broken response.
_MIN_BYTES = {"annual_zip": 1024, "weekly_static": 1024}
_MIN_BYTES_DEFAULT = 512


def user_agent() -> str:
    return os.environ.get("COTDATA_USER_AGENT", "").strip() or _DEFAULT_UA


# ── Paths ───────────────────────────────────────────────────────────────────
def vintage_root() -> Path:
    """Root of the vintage subtree.

    Defaults to ``$COTDATA_STORE/vintage``, but ``COTDATA_VINTAGE_ROOT`` overrides it, and
    on a REPLICA that override is mandatory rather than cosmetic.

    The deployment syncs the store with ``robocopy /MIR``, which deletes anything at the
    destination that is absent at the source, excluding only ``_cache _raw citpy``. A
    vintage tree written on a replica is therefore destroyed by the next producer sync —
    the same trap docs/SYNCING.md already documents for ``citpy``, except that vintage data
    is IRREPLACEABLE by construction (CFTC serves current state only). Either run capture
    on the producer, where the tree syncs outward normally, or point this override at a
    path outside the mirrored store.
    """
    override = os.environ.get("COTDATA_VINTAGE_ROOT", "").strip()
    if override:
        return Path(override)
    return config.store_root() / "vintage"


def raw_dir(source_kind: str, year: int | str) -> Path:
    """Partition for retained raw bytes. For sources with no report year (the weekly
    static) the caller passes the CAPTURE year — 'current/' would be actively wrong the
    moment it stopped being current."""
    return vintage_root() / "raw" / source_kind / str(year)


def manifest_path() -> Path:
    """The snapshot provenance index.

    Deliberately NOT named manifest.json. Both deployed sync scripts exclude that name
    UNANCHORED — robocopy ``/XF manifest.json`` and rsync ``--exclude "manifest.json"``
    match at any depth — so a vintage/manifest.json would be silently stripped in transit,
    delivering raw bytes to a replica with no index: the "directory of opaque blobs"
    failure this module works hardest to prevent. The distinct name means existing sync
    scripts need no edit to carry it correctly.
    """
    return vintage_root() / "snapshots.json"


# ── Sources ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Source:
    """One fetchable CFTC file. ``report_year`` is None for the current-week static."""
    report_type: str          # legacy | disaggregated | tff
    source_kind: str          # annual_zip | weekly_static
    ext: str                  # zip | txt
    url: str
    report_year: int | None = None


# Annual-zip URL builders. The Legacy naming convention changed in 2004; disagg/TFF
# history starts in 2006. Duplicated from providers/ deliberately — capture stays
# decoupled from the parse path (non-goal: do not refactor existing fetch logic).
def _legacy_zip_url(year: int) -> str:
    if year < 2004:
        return f"https://www.cftc.gov/files/dea/history/deafut_xls_{year}.zip"
    return f"https://www.cftc.gov/files/dea/history/dea_fut_xls_{year}.zip"


# Disaggregated and TFF history at these URLs starts in 2010, NOT 2006. The reports
# themselves begin in 2006, which is what the providers record and what an earlier version
# of this list used, but cftc.gov serves 404 for fut_disagg_txt_2006..2009 and
# fut_fin_txt_2006..2009 (verified live in review). Using 2006 made every `fetch --all`
# record eight permanent failure snapshots that could never succeed.
_DISAGG_TFF_FIRST_YEAR = 2010


def annual_sources(year: int) -> list[Source]:
    out = [Source("legacy", "annual_zip", "zip", _legacy_zip_url(year), year)]
    if year >= _DISAGG_TFF_FIRST_YEAR:
        out.append(Source("disaggregated", "annual_zip", "zip",
                          f"https://www.cftc.gov/files/dea/history/fut_disagg_txt_{year}.zip", year))
        out.append(Source("tff", "annual_zip", "zip",
                          f"https://www.cftc.gov/files/dea/history/fut_fin_txt_{year}.zip", year))
    return out


# The current-week static. Its HTTP Last-Modified is a TRUE publication timestamp
# (spike 2026-07-30, docs/design/cot_vintage.md §3), so capturing it upgrades the
# `observed` release-date mechanism from poll-accurate to publication-accurate.
WEEKLY_STATIC = Source("legacy", "weekly_static", "txt",
                       "https://www.cftc.gov/dea/newcot/deafut.txt", None)


# ── HTTP ────────────────────────────────────────────────────────────────────
@dataclass
class HttpResult:
    status: int
    content: bytes | None
    etag: str | None = None
    last_modified: str | None = None


def _http_get(url: str, *, etag: str | None, last_modified: str | None) -> HttpResult:
    """Conditional GET with If-None-Match / If-Modified-Since. Real network path.

    Injected as ``http_get`` in tests so the capture logic runs fully offline.

    MEASURED 2026-07-30: cftc.gov serves NO ETag on any of these files, so If-None-Match
    can never fire — it is sent only in case that changes. If-Modified-Since does work
    (verified 304 against both a static year and the current year), and is what makes an
    --all sweep cheap: only the current and immediately-prior year actually transfer.
    """
    import requests  # local import: keeps the module importable without network deps

    headers = {"User-Agent": user_agent()}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    r = requests.get(url, headers=headers, timeout=180)
    if r.status_code == 304:
        return HttpResult(304, None)
    r.raise_for_status()
    return HttpResult(r.status_code, r.content,
                      etag=r.headers.get("ETag"),
                      last_modified=r.headers.get("Last-Modified"))


# ── Manifest (self-owned, append-only snapshot index) ───────────────────────
class CorruptManifestError(RuntimeError):
    """The vintage manifest exists but could not be parsed.

    Never recovered from by returning an empty manifest: the next write would then
    overwrite the damaged file with that empty structure, and the manifest is the ONLY
    mapping from snapshot_id to url / sha / retrieval time / parse status. Losing it
    leaves a directory of opaquely-named blobs while the raw bytes themselves survive —
    the worst outcome available in this module, and reachable from one interrupted write.
    """


def _read_manifest() -> dict:
    p = manifest_path()
    if not p.exists():
        return {"schema_version": SCHEMA_VERSION, "snapshots": []}
    try:
        m = json.loads(p.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        stamp = _utcnow().strftime("%Y%m%dT%H%M%SZ")
        quarantine = p.with_suffix(f".json.corrupt.{stamp}")
        try:
            os.replace(p, quarantine)
        except OSError:
            quarantine = None
        raise CorruptManifestError(
            f"{p} is unreadable ({e}). "
            + (f"Moved aside to {quarantine}. " if quarantine else "Could not move it aside. ")
            + "Raw bytes under vintage/raw/ are intact and self-describing (each filename "
              "carries its sha256 prefix); rebuild the index from them rather than "
              "letting an empty manifest overwrite the record."
        ) from e
    if not isinstance(m, dict):
        raise CorruptManifestError(f"{p} does not contain a JSON object.")
    m.setdefault("snapshots", [])
    m.setdefault("schema_version", SCHEMA_VERSION)
    return m


def _write_manifest(m: dict) -> None:
    p = manifest_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(m, indent=2, sort_keys=True))
    os.replace(tmp, p)


def _latest_with_content(snapshots: list[dict], url: str) -> dict | None:
    """Most recent snapshot for a URL that actually carries content.

    Deliberately not "most recent snapshot", and the distinction is load-bearing. A FAILURE
    record has ``content_sha256``, ``http_etag`` and ``http_last_modified`` all None, so
    comparing against it means the next fetch sends no If-Modified-Since, cannot match the
    dedupe test, and is classified as changed content.

    On a frozen year that produces a FALSE RESTATEMENT ALERT from a single network blip:
    the one alarm this subsystem exists to raise, fired by an event that says nothing
    about the data. It also re-retains bytes already on disk under a second filename
    carrying the identical sha. Both were reproduced before this was split out.

    Content questions ("is this the same file?") must therefore look past failures to the
    last snapshot that knows the file's identity.

    Note what this is NOT: "when did bytes last arrive?". A 304 record copies the sha,
    etag and Last-Modified forward from the snapshot it matched, precisely so the next
    conditional GET can be issued from it, so a 304 satisfies this filter. That is correct
    for identity and wrong for timing, which is what ``_latest_delivery`` is for.

    The max over ``retrieved_at`` is EXPLICIT rather than "last element". The list is
    append-only and chronological today, but an implicit ordering assumption is exactly
    what breaks when a merge or dedupe pass is added later. Ties fall back to list
    position, which preserves today's behaviour.
    """
    return _latest_matching(snapshots, url, lambda s: s.get("content_sha256"))


def _latest_delivery(snapshots: list[dict], url: str) -> dict | None:
    """Most recent snapshot for a URL where bytes actually came down the wire.

    ``byte_size`` is the discriminator, and it is the only field that means this: a 200
    sets it whether the bytes were retained or deduped, and a 304 or a failure leaves it
    None. Sha, etag and Last-Modified are all carried forward across a 304 and so cannot
    answer the question.

    Used by the blind-detector to measure how long a frozen year has been silent. Reading
    that off ``_latest_with_content`` instead reports "one day" forever, because every
    daily 304 refreshes the record it would measure from, which silently disables the
    alarm. Caught by the tests that pin the alert firing at all.
    """
    return _latest_matching(snapshots, url, lambda s: s.get("byte_size") is not None)


def _latest_matching(snapshots: list[dict], url: str, pred) -> dict | None:
    prev = [(s.get("retrieved_at") or "", i, s)
            for i, s in enumerate(snapshots)
            if s.get("source_url") == url and pred(s)]
    if not prev:
        return None
    return max(prev, key=lambda t: (t[0], t[1]))[2]


def _snapshot_id(retrieved_at: str, url: str, tag: str) -> str:
    """Unique per (retrieval second, url, content-state).

    The URL discriminator is load-bearing: _utcnow() truncates to whole seconds, so
    several sources returning 304 within one second would otherwise share the id
    ``{retrieved_at}_304`` — and update_snapshot patches EVERY record matching an id, so
    one later parse-status write would hit all of them. Rate limiting hides this in
    production; a test with rate_limit_s=0 walks straight into it.
    """
    url8 = hashlib.sha256(url.encode()).hexdigest()[:8]
    return f"{retrieved_at}_{tag}_{url8}"


def read_snapshots() -> list[dict]:
    """All recorded snapshot provenance rows, oldest first."""
    return _read_manifest()["snapshots"]


def update_snapshot(snapshot_id: str, **fields) -> bool:
    """Patch fields (e.g. ``parse_status``, ``parse_error``) on a recorded snapshot.
    Returns True if a matching snapshot was found and written."""
    m = _read_manifest()
    hit = False
    for rec in m["snapshots"]:
        if rec.get("snapshot_id") == snapshot_id:
            rec.update(fields)
            hit = True
    if hit:
        _write_manifest(m)
    return hit


# ── The frozen-year tripwire ────────────────────────────────────────────────
# CFTC's weekly regeneration job touches a ROLLING TWO-YEAR WINDOW: the current year and
# the immediately-prior year, nothing older (measured 2026-07-30: 2025 and 2026 shared an
# identical Last-Modified while 2022/2023/2024 sat on a single January bulk re-touch; see
# docs/design/cot_vintage_store_handoff.md §12.2). That gives three regimes, and each has a
# DIFFERENT expected outcome, which is what makes a violation meaningful rather than noise:
#
#   current year          new bytes every week. Real new data. Nothing to assert.
#   prior year (frozen)   re-served every week but byte-IDENTICAL, so the expected record
#                         is exactly "unchanged bytes (deduped)". This is the only regime
#                         where CFTC hands us a free weekly content check on closed data.
#   older (frozen)        never re-touched, so If-Modified-Since 304s and no bytes arrive.
#                         Content is not verified at all; a 200 with new bytes is the alert.
#
# The prior-year slot is the load-bearing one. It is the sole automated detector for the
# failure mode this subsystem exists to guard against (July 2008: reports restated back to
# 2007-07-03), and it costs one ~7 MB weekly transfer that dedupes to zero bytes on disk.
EXPECT_CHURN = "churn"                    # current year: new bytes are normal
EXPECT_FROZEN_IN_WINDOW = "frozen_in_window"    # prior year: re-served, must be identical
EXPECT_FROZEN_OUT_OF_WINDOW = "frozen_out_of_window"  # older: 304, content unverified
EXPECT_WEEKLY = "weekly"                  # the weekly static: new content every Friday


def capture_expectation(report_year: int | None, current_year: int) -> str:
    """Which regeneration regime a source sits in. See the block comment above."""
    if report_year is None:
        return EXPECT_WEEKLY
    if report_year >= current_year:
        return EXPECT_CHURN
    if report_year == current_year - 1:
        return EXPECT_FROZEN_IN_WINDOW
    return EXPECT_FROZEN_OUT_OF_WINDOW


# What actually happened on this fetch, as a single controlled token. Recorded on every
# snapshot so the tripwire can be EDGE-triggered off the previous record rather than
# re-firing on a standing condition.
OUTCOME_FIRST = "first"              # no prior snapshot for this url, a baseline
OUTCOME_CHANGED = "changed"          # 200, bytes differ from the last retained copy
OUTCOME_DEDUPED = "deduped"          # 200, bytes byte-identical to the last retained copy
OUTCOME_NOT_MODIFIED = "not_modified"  # 304, server declined to re-send
OUTCOME_FAILED = "failed"

# How long a frozen-in-window year may go without being re-served before the content check
# is genuinely blind.
#
# CFTC regenerates WEEKLY; the capture task runs DAILY. Six of every seven runs on the
# prior year therefore return 304 as the normal, documented cadence (§6: "the other six
# days return 304, because CFTC re-touches Last-Modified weekly"). An earlier version of
# this detector asked "did the LAST RUN see bytes?", which on a daily schedule is not a
# blindness test but a day-of-week test: it fired every Saturday, the morning after
# Friday's regeneration, on all three prior-year sources. Measured in the first week of
# production capture, 2026-08-01, where the 2025 baseline landed at 13:54Z and the alert
# fired at 21:00Z the same day, seven hours later.
#
# Blindness is a question about ELAPSED TIME since bytes last arrived, so that is what this
# measures. Nine days is one full weekly cycle plus two days of slack: a single skipped
# regeneration or a day of run outages does not trip it, two consecutive misses do.
BLIND_AFTER_DAYS = 9

# The phrase every blind alert carries, so a later run can tell whether this quiet period
# has already been reported. Load-bearing, not cosmetic: it is the edge in the edge-trigger.
_BLIND_MARKER = "gone blind"


def _quiet_days(delivery: dict | None, now: dt.datetime) -> float | None:
    """Days since bytes last arrived for this URL, or None if they never have."""
    prior = _parse_iso((delivery or {}).get("retrieved_at"))
    if prior is None:
        return None
    return (now - prior).total_seconds() / 86400.0


def _blind_already_reported(snapshots: list[dict], url: str, delivery: dict | None) -> bool:
    """Has the blind alert already fired for the CURRENT quiet period?

    The quiet period is identified by the delivery that began it, so the question is "is
    there a later record for this url that already carried the alert?".

    Keying the edge on the PREVIOUS RECORD's outcome instead is what made the alert recur:
    it correctly refused to fire on every day of a quiet period, but it re-armed the moment
    any content arrived, so a weekly re-serve produced a weekly alert forever. Keying it on
    the quiet period itself means one alert per period however long the period runs and
    however many runs fall inside it. It is also robust to a fetch failure landing mid
    period, which under an outcome-keyed edge could suppress the alert entirely.
    """
    if delivery is None:
        return False
    since = delivery.get("retrieved_at") or ""
    return any(s.get("source_url") == url
               and (s.get("retrieved_at") or "") > since
               and _BLIND_MARKER in (s.get("tripwire_alert") or "")
               for s in snapshots)


def _tripwire_alert(expectation: str, outcome: str, *, quiet_days: float | None,
                    already_reported: bool) -> str | None:
    """The alert reason for one capture, or None.

    Two kinds of violation, deliberately triggered differently:

    **Content changed on a frozen year** always alerts. It is a discrete event with new
    bytes sitting next to the old ones, so it is diffable, and two restatements in
    consecutive weeks are two things worth knowing about, not one.

    **The detector went blind** (a frozen-in-window year that stops being re-served) alerts
    once per quiet period, when that period passes ``BLIND_AFTER_DAYS``. It is a standing
    condition: if CFTC stopped re-touching the prior year's Last-Modified, a level-triggered
    alert would fire every single day forever, and an alert that never clears is one that
    gets ignored. Same reasoning that scopes the ingest revision alert to this run's
    snapshots.

    What it must NOT do is mistake the ordinary weekly gap for that condition. A daily task
    against a weekly regeneration sees six 304s between every two content-bearing fetches,
    so "last run saw bytes, this one did not" is satisfied once a week by healthy
    behaviour. See ``BLIND_AFTER_DAYS`` for the production run that demonstrated it.

    Elapsed time also covers the YEAR ROLLOVER hole that an outcome-keyed edge was widened
    to catch: a year that churned all through 2025 is still content-bearing in December, so
    if CFTC drops it at the January boundary the silence simply runs past nine days and
    alerts, whatever token the last run happened to record. The rollover is the single most
    likely moment for CFTC's regeneration window to shift, which makes it the worst
    possible blind spot, and it is now covered by the same rule as every other week rather
    than by a special case.

    A FETCH FAILURE is deliberately NOT a tripwire condition. Connectivity is not
    provenance: a dropped connection says nothing about whether CFTC restated anything, and
    routing it here turned one blip into a frozen-year restatement alarm on the daily run.
    Failures are counted and printed by ``fetch`` itself, which is where an operational
    problem belongs.

    Note what is NOT an alert: a frozen year outside the two-year window returning 304.
    That is the correct, expected outcome there, and it is also the reason the prior-year
    slot matters: it is the only closed data whose CONTENT is checked at all.
    """
    if expectation == EXPECT_FROZEN_IN_WINDOW:
        if outcome == OUTCOME_CHANGED:
            return ("content changed on the frozen prior year: CFTC re-serves this file "
                    "weekly and it has been byte-identical, so a new sha here is the "
                    "retroactive-restatement signature")
        if (outcome == OUTCOME_NOT_MODIFIED and not already_reported
                and quiet_days is not None and quiet_days > BLIND_AFTER_DAYS):
            return (f"the frozen prior year has not been re-served for {quiet_days:.0f} "
                    f"days, and CFTC re-touches it weekly. The weekly content check has "
                    f"gone blind, so a restatement would no longer be detected here")
        return None
    if expectation == EXPECT_FROZEN_OUT_OF_WINDOW and outcome == OUTCOME_CHANGED:
        return ("content changed on a closed year outside CFTC's regeneration window, "
                "which should never be re-touched at all")
    return None


def _annotate(rec: dict, *, history: list[dict], content: dict | None,
              now: dt.datetime) -> dict:
    """Attach expectation / outcome / tripwire_alert to a capture record.

    ``content`` is the previous snapshot that actually carried bytes. It answers both
    "have we ever seen this file?" and "how long has it been quiet?". Classifying FIRST off
    the previous snapshot of ANY kind was the half of the earlier dedupe fix that got
    missed: when the first record for a URL is a fetch failure, the recovering fetch found
    a non-None predecessor, fell through to CHANGED, and fired the same false restatement
    alert the fix existed to remove. It also left ``outcome`` and ``restatement_suspect``
    disagreeing, since only the latter had been re-pointed.

    ``history`` is every snapshot recorded so far, and exists only so the blind alert can
    tell whether it has already fired for the current quiet period.
    """
    expectation = capture_expectation(rec.get("report_year"), now.year)
    note = rec.get("note") or ""
    if note.startswith("fetch failed"):
        outcome = OUTCOME_FAILED
    elif rec.get("http_status") == 304:
        outcome = OUTCOME_NOT_MODIFIED
    elif content is None:
        outcome = OUTCOME_FIRST
    elif rec.get("note") == "unchanged bytes (deduped)":
        outcome = OUTCOME_DEDUPED
    else:
        outcome = OUTCOME_CHANGED
    url = rec.get("source_url")
    delivery = _latest_delivery(history, url)
    alert = _tripwire_alert(
        expectation, outcome,
        quiet_days=_quiet_days(delivery, now),
        already_reported=_blind_already_reported(history, url, delivery))
    # Year-end finalisation is the one benign way a frozen year legitimately changes, and
    # it happens on a known schedule. Say so in the message rather than teaching the reader
    # to discount every January alert, which would train them to discount all of them.
    if alert and outcome == OUTCOME_CHANGED and now.month == 1:
        alert += (". NOTE: it is January, when CFTC finalises the year just ended, so this "
                  "is the one time of year a frozen-year change is plausibly routine")
    rec["expectation"] = expectation
    rec["outcome"] = outcome
    rec["tripwire_alert"] = alert
    if alert:
        print(f"  *** TRIPWIRE: {rec.get('report_type')} {rec.get('report_year')}: {alert}.")
    return rec


# ── Capture ─────────────────────────────────────────────────────────────────
def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def _iso(ts: dt.datetime) -> str:
    return ts.isoformat().replace("+00:00", "Z")


def _parse_iso(s: str | None) -> dt.datetime | None:
    """Inverse of ``_iso``, tolerant of a missing or malformed stamp.

    ``fromisoformat`` only learned to read a trailing ``Z`` in 3.11, and this package
    supports 3.10, so the substitution is required rather than tidy. A stamp that will not
    parse returns None, which reads downstream as "we cannot date this", not as "zero days
    ago": the alert it feeds stays silent rather than firing on a parse bug.
    """
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def capture_source(source: Source, *, snapshots: list[dict], http_get, now: dt.datetime) -> dict:
    """Fetch one source, retain its bytes if new, and return a provenance record.

    Always returns a record (a fetch is always recorded, §4.1), including on 304.
    A new raw file is written ONLY when the content sha differs from the most recent
    snapshot for the same URL — a byte-identical regeneration is deduped (§3.4: a
    changed sha does not imply changed data, and an unchanged sha means nothing new to
    retain). Raw bytes, once written, are never rewritten.
    """
    # The last snapshot that actually SAW BYTES, which is a different question from the last
    # snapshot of any kind, and conflating them fires false restatement alerts. It answers
    # "is this the same file?", "how long has this been quiet?" and "have we ever seen this
    # file at all?" — every question the tripwire asks. See _latest_with_content.
    content = _latest_with_content(snapshots, source.url)
    res = http_get(source.url,
                   etag=(content or {}).get("http_etag"),
                   last_modified=(content or {}).get("http_last_modified"))
    retrieved_at = _iso(now)
    base = {
        "source_url": source.url,
        "source_kind": source.source_kind,
        "report_type": source.report_type,
        "report_year": source.report_year,
        "retrieved_at": retrieved_at,
        "parse_status": "pending",
        "parse_error": None,
    }

    if res.status == 304 or res.content is None:
        # Server says unchanged: record the check, retain nothing new, reuse prior file.
        return _annotate({
            **base,
            "snapshot_id": _snapshot_id(retrieved_at, source.url, "304"),
            "http_status": 304,
            "http_etag": (content or {}).get("http_etag"),
            "http_last_modified": (content or {}).get("http_last_modified"),
            "content_sha256": (content or {}).get("content_sha256"),
            "byte_size": None,
            "local_path": (content or {}).get("local_path"),
            "note": "304 not-modified",
        }, history=snapshots, content=content, now=now)

    floor = _MIN_BYTES.get(source.source_kind, _MIN_BYTES_DEFAULT)
    if len(res.content) < floor:
        raise ValueError(
            f"{source.url} returned {len(res.content)} bytes, below the {floor}-byte floor "
            f"for {source.source_kind}. Refusing to retain a truncated/empty response as a "
            f"legitimate snapshot.")

    sha = hashlib.sha256(res.content).hexdigest()
    if content and content.get("content_sha256") == sha:
        # Byte-identical to what we already retained (zips regenerate): dedupe, no rewrite.
        return _annotate({
            **base,
            "snapshot_id": _snapshot_id(retrieved_at, source.url, sha[:8]),
            "http_status": res.status,
            "http_etag": res.etag,
            "http_last_modified": res.last_modified,
            "content_sha256": sha,
            "byte_size": len(res.content),
            "local_path": content.get("local_path"),
            "note": "unchanged bytes (deduped)",
        }, history=snapshots, content=content, now=now)

    # New bytes: retain immutably, via a .part file + atomic replace. A crash during a
    # plain write_bytes would leave a TRUNCATED file already carrying the full sha in its
    # name — the filename asserts an integrity claim the contents don't satisfy, and any
    # sha-keyed recovery pass would then adopt it as valid.
    compact = retrieved_at.replace("-", "").replace(":", "")
    fname = f"{compact}_{sha[:8]}.{source.ext}"
    year = source.report_year if source.report_year is not None else now.year
    dest = raw_dir(source.source_kind, year) / fname
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    try:
        part.write_bytes(res.content)
        os.replace(part, dest)
    finally:
        if part.exists():
            part.unlink()
    try:
        rel = str(dest.relative_to(config.store_root()))
    except ValueError:
        rel = str(dest)  # COTDATA_VINTAGE_ROOT points outside the store

    # ── Restatement tripwire ────────────────────────────────────────────────
    # A CLOSED year's content is frozen, so a closed year whose sha CHANGES is the
    # 2008-style retroactive-restatement signature, the failure mode this whole subsystem
    # exists to detect, and it costs no extra machinery: it falls out of the ordinary
    # dedupe path. ``_annotate`` below classifies which frozen regime this is and writes
    # the human-readable reason; this flag stays because it is the field ``ingest`` and
    # every stored snapshot already key off.
    restatement_suspect = (
        content is not None and source.report_year is not None
        and source.report_year < now.year)

    return _annotate({
        **base,
        "snapshot_id": _snapshot_id(retrieved_at, source.url, sha[:8]),
        "restatement_suspect": restatement_suspect,
        "http_status": res.status,
        "http_etag": res.etag,
        "http_last_modified": res.last_modified,
        "content_sha256": sha,
        "byte_size": len(res.content),
        "local_path": rel,
        "note": None,
    }, history=snapshots, content=content, now=now)


def fetch(year: int | None = None, *, all_years: bool = False,
          include_weekly: bool = True, include_prior_year: bool = True,
          sources: list[Source] | None = None,
          http_get=_http_get, rate_limit_s: float = _RATE_LIMIT_S, now_fn=_utcnow) -> dict:
    """Capture the release-critical CFTC files into the immutable landing zone.

    Default: the current year's three annual zips (which carry the newest release), the
    PRIOR year's three (the frozen-year tripwire, see the block comment above
    ``capture_expectation``), plus the Legacy weekly static (for its true-publication
    Last-Modified). ``all_years`` walks 1986→current for a cold-start backfill of raw
    bytes. An explicit ``sources`` list overrides the year/weekly derivation (targeted
    capture; used in tests).

    The prior year is in the DEFAULT set rather than only on ``--all`` because the
    tripwire is worth nothing if it only runs when someone remembers to run it manually.
    It costs one ~7 MB transfer per week (CFTC re-touches Last-Modified weekly, so the
    other six days 304) and zero bytes on disk, because the content is byte-identical and
    dedupes. That is the whole price of the only automated retroactive-restatement
    detector in the stack. An explicit ``year`` is taken at face value, with no prior-year
    companion: that is a targeted capture, not the scheduled sweep.

    Returns ``{"records": [...], "new_files": n, "checks": n, "failed": n,
    "tripwire_alerts": [...]}``.
    """
    if sources is None:
        this_year = now_fn().year
        if all_years:
            years = list(range(1986, this_year + 1))
        elif year is not None:
            years = [year]
        else:
            years = [this_year] + ([this_year - 1] if include_prior_year else [])
        sources = []
        for y in years:
            sources.extend(annual_sources(y))
        if include_weekly:
            sources.append(WEEKLY_STATIC)

    m = _read_manifest()
    m["schema_version"] = SCHEMA_VERSION
    snapshots = m["snapshots"]
    new_files = 0
    failed = 0
    records = []
    for i, src in enumerate(sources):
        now = now_fn()
        try:
            rec = capture_source(src, snapshots=snapshots, http_get=http_get, now=now)
        except Exception as e:  # noqa: BLE001
            # One bad source must not kill the run. A cold-start --all is 120+ requests
            # and dies at the first naming variant or missing disagg year otherwise. The
            # failure is RECORDED (so it is visible and retryable), then the run goes on —
            # matching what the ingest path already does.
            rec = _failure_record(src, now, e, snapshots=snapshots,
                                  content=_latest_with_content(snapshots, src.url))
            failed += 1
            print(f"  {src.report_type}/{src.source_kind} {src.report_year or ''}: "
                  f"fetch failed — {e}")
        snapshots.append(rec)   # visible to the next source's _latest_with_content
        records.append(rec)
        if rec.get("note") is None and rec.get("byte_size") is not None:
            new_files += 1
        # Write after EVERY source, not once at the end: the manifest is small and its
        # replace is atomic, so this costs nothing measurable and shrinks the crash window
        # from a whole run to a single source.
        #
        # It does NOT eliminate that window, and an earlier version of this comment
        # claimed it did (caught in adversarial review). Raw bytes are written and
        # os.replace'd inside capture_source, then the manifest entry is appended here, so
        # a crash in between leaves a retained blob with no index entry. That is the
        # deliberate direction to fail in: the reverse ordering would leave an index entry
        # pointing at a file that does not exist, which is a lie rather than an omission.
        # The blob is also self-describing, since its filename carries the sha8 prefix, and
        # the only cost of the orphan is that the next fetch re-downloads and retains a
        # duplicate copy of bytes already on disk.
        _write_manifest(m)
        if rate_limit_s and i < len(sources) - 1:
            time.sleep(rate_limit_s)

    return {"records": records, "new_files": new_files, "checks": len(records),
            "failed": failed,
            "tripwire_alerts": [r for r in records if r.get("tripwire_alert")]}


def _failure_record(source: Source, now: dt.datetime, error: Exception,
                    *, snapshots: list[dict] | None = None,
                    content: dict | None = None) -> dict:
    retrieved_at = _iso(now)
    return _annotate({
        "source_url": source.url,
        "source_kind": source.source_kind,
        "report_type": source.report_type,
        "report_year": source.report_year,
        "retrieved_at": retrieved_at,
        "snapshot_id": _snapshot_id(retrieved_at, source.url, "failed"),
        "http_status": None,
        "http_etag": None,
        "http_last_modified": None,
        "content_sha256": None,
        "byte_size": None,
        "local_path": None,
        "parse_status": "pending",
        "parse_error": None,
        "note": f"fetch failed: {error}",
    }, history=snapshots or [], content=content, now=now)
