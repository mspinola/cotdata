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


def annual_sources(year: int) -> list[Source]:
    out = [Source("legacy", "annual_zip", "zip", _legacy_zip_url(year), year)]
    if year >= 2006:
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


def _latest_for_url(snapshots: list[dict], url: str) -> dict | None:
    """Most recent snapshot for a URL, by an EXPLICIT max over retrieved_at rather than
    'last element'. The list is append-only and chronological today, but an implicit
    ordering assumption is exactly what breaks when a merge or dedupe pass is added
    later. Ties fall back to list position, which preserves today's behaviour."""
    prev = [(s.get("retrieved_at") or "", i, s)
            for i, s in enumerate(snapshots) if s.get("source_url") == url]
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


def _tripwire_alert(expectation: str, outcome: str, prev_outcome: str | None) -> str | None:
    """The alert reason for one capture, or None.

    Two kinds of violation, deliberately triggered differently:

    **Content changed on a frozen year** always alerts. It is a discrete event with new
    bytes sitting next to the old ones, so it is diffable, and two restatements in
    consecutive weeks are two things worth knowing about, not one.

    **The detector went blind** (a frozen-in-window year that 304s or fails) alerts only on
    the TRANSITION into that state. It is a standing condition: if CFTC stopped re-touching
    the prior year's Last-Modified, a level-triggered alert would fire every single day
    forever, and an alert that never clears is one that gets ignored. Same reasoning
    that scopes the ingest revision alert to this run's snapshots. Edge-triggering means it
    is reported once, loudly, and then stays quiet until the pattern changes back.

    Note what is NOT an alert: a frozen year outside the two-year window returning 304.
    That is the correct, expected outcome there, and it is also the reason the prior-year
    slot matters: it is the only closed data whose CONTENT is checked at all.
    """
    if expectation == EXPECT_FROZEN_IN_WINDOW:
        if outcome == OUTCOME_CHANGED:
            return ("content changed on the frozen prior year: CFTC re-serves this file "
                    "weekly and it has been byte-identical, so a new sha here is the "
                    "retroactive-restatement signature")
        if outcome in (OUTCOME_NOT_MODIFIED, OUTCOME_FAILED) and prev_outcome == OUTCOME_DEDUPED:
            return (f"the frozen prior year stopped being re-served ({outcome}) after "
                    f"previously deduping. The weekly content check has gone blind, so a "
                    f"restatement would no longer be detected here")
        return None
    if expectation == EXPECT_FROZEN_OUT_OF_WINDOW and outcome == OUTCOME_CHANGED:
        return ("content changed on a closed year outside CFTC's regeneration window, "
                "which should never be re-touched at all")
    return None


def _annotate(rec: dict, *, prev: dict | None, now: dt.datetime) -> dict:
    """Attach expectation / outcome / tripwire_alert to a capture record."""
    expectation = capture_expectation(rec.get("report_year"), now.year)
    note = rec.get("note") or ""
    if note.startswith("fetch failed"):
        outcome = OUTCOME_FAILED
    elif rec.get("http_status") == 304:
        outcome = OUTCOME_NOT_MODIFIED
    elif prev is None:
        outcome = OUTCOME_FIRST
    elif rec.get("note") == "unchanged bytes (deduped)":
        outcome = OUTCOME_DEDUPED
    else:
        outcome = OUTCOME_CHANGED
    alert = _tripwire_alert(expectation, outcome, (prev or {}).get("outcome"))
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


def capture_source(source: Source, *, snapshots: list[dict], http_get, now: dt.datetime) -> dict:
    """Fetch one source, retain its bytes if new, and return a provenance record.

    Always returns a record (a fetch is always recorded, §4.1), including on 304.
    A new raw file is written ONLY when the content sha differs from the most recent
    snapshot for the same URL — a byte-identical regeneration is deduped (§3.4: a
    changed sha does not imply changed data, and an unchanged sha means nothing new to
    retain). Raw bytes, once written, are never rewritten.
    """
    prev = _latest_for_url(snapshots, source.url)
    res = http_get(source.url,
                   etag=(prev or {}).get("http_etag"),
                   last_modified=(prev or {}).get("http_last_modified"))
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
            "http_etag": (prev or {}).get("http_etag"),
            "http_last_modified": (prev or {}).get("http_last_modified"),
            "content_sha256": (prev or {}).get("content_sha256"),
            "byte_size": None,
            "local_path": (prev or {}).get("local_path"),
            "note": "304 not-modified",
        }, prev=prev, now=now)

    floor = _MIN_BYTES.get(source.source_kind, _MIN_BYTES_DEFAULT)
    if len(res.content) < floor:
        raise ValueError(
            f"{source.url} returned {len(res.content)} bytes, below the {floor}-byte floor "
            f"for {source.source_kind}. Refusing to retain a truncated/empty response as a "
            f"legitimate snapshot.")

    sha = hashlib.sha256(res.content).hexdigest()
    if prev and prev.get("content_sha256") == sha:
        # Byte-identical to what we already retained (zips regenerate): dedupe, no rewrite.
        return _annotate({
            **base,
            "snapshot_id": _snapshot_id(retrieved_at, source.url, sha[:8]),
            "http_status": res.status,
            "http_etag": res.etag,
            "http_last_modified": res.last_modified,
            "content_sha256": sha,
            "byte_size": len(res.content),
            "local_path": prev.get("local_path"),
            "note": "unchanged bytes (deduped)",
        }, prev=prev, now=now)

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
        prev is not None and source.report_year is not None
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
    }, prev=prev, now=now)


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
            rec = _failure_record(src, now, e, prev=_latest_for_url(snapshots, src.url))
            failed += 1
            print(f"  {src.report_type}/{src.source_kind} {src.report_year or ''}: "
                  f"fetch failed — {e}")
        snapshots.append(rec)   # visible to the next source's _latest_for_url
        records.append(rec)
        if rec.get("note") is None and rec.get("byte_size") is not None:
            new_files += 1
        # Write after EVERY source, not once at the end: the manifest is small and its
        # replace is atomic, so this costs nothing measurable and shrinks the crash window
        # from a whole run to a single source. Combined with the atomic raw write, an
        # interrupted run leaves a consistent store rather than unrecorded blobs.
        _write_manifest(m)
        if rate_limit_s and i < len(sources) - 1:
            time.sleep(rate_limit_s)

    return {"records": records, "new_files": new_files, "checks": len(records),
            "failed": failed,
            "tripwire_alerts": [r for r in records if r.get("tripwire_alert")]}


def _failure_record(source: Source, now: dt.datetime, error: Exception,
                    *, prev: dict | None = None) -> dict:
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
    }, prev=prev, now=now)
