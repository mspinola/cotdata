"""COT vintage capture + provenance — step 1 of the crowdmon-futures build (handoff v0.2).

This module is purely ADDITIVE alongside the current-state store: it never touches the
existing ``cot_legacy/`` etc. write path, so existing consumers see byte-identical output.

Layout (all under ``$COTDATA_STORE/vintage/``):

    raw/{source_kind}/{year}/{retrieved_at}_{sha8}.{ext}   immutable, never rewritten
    observations/report_year=YYYY/*.parquet                change-only bitemporal rows
    revisions/detected_year=YYYY/*.parquet                 append-only, field-level
    release_schedule.parquet
    announcements.parquet
    manifest.json                                          vintage provenance index

Provenance lives in its OWN ``vintage/manifest.json`` rather than a block in the cot-half
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

_UA = "cotdata-vintage/0.1 (COT research; contact matt.spinola@gmail.com)"
_RATE_LIMIT_S = 1.0  # polite spacing between real network requests
SCHEMA_VERSION = 1


# ── Paths ───────────────────────────────────────────────────────────────────
def vintage_root() -> Path:
    return config.store_root() / "vintage"


def raw_dir(source_kind: str, year: int | str) -> Path:
    return vintage_root() / "raw" / source_kind / str(year)


def manifest_path() -> Path:
    return vintage_root() / "manifest.json"


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
    """
    import requests  # local import: keeps the module importable without network deps

    headers = {"User-Agent": _UA}
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
def _read_manifest() -> dict:
    p = manifest_path()
    if not p.exists():
        return {"schema_version": SCHEMA_VERSION, "snapshots": []}
    try:
        m = json.loads(p.read_text())
    except json.JSONDecodeError:
        return {"schema_version": SCHEMA_VERSION, "snapshots": []}
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
    prev = [s for s in snapshots if s.get("source_url") == url]
    return prev[-1] if prev else None


def read_snapshots() -> list[dict]:
    """All recorded snapshot provenance rows, oldest first."""
    return _read_manifest()["snapshots"]


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
        return {
            **base,
            "snapshot_id": f"{retrieved_at}_304",
            "http_status": 304,
            "http_etag": (prev or {}).get("http_etag"),
            "http_last_modified": (prev or {}).get("http_last_modified"),
            "content_sha256": (prev or {}).get("content_sha256"),
            "byte_size": None,
            "local_path": (prev or {}).get("local_path"),
            "note": "304 not-modified",
        }

    sha = hashlib.sha256(res.content).hexdigest()
    if prev and prev.get("content_sha256") == sha:
        # Byte-identical to what we already retained (zips regenerate): dedupe, no rewrite.
        return {
            **base,
            "snapshot_id": f"{retrieved_at}_{sha[:8]}",
            "http_status": res.status,
            "http_etag": res.etag,
            "http_last_modified": res.last_modified,
            "content_sha256": sha,
            "byte_size": len(res.content),
            "local_path": prev.get("local_path"),
            "note": "unchanged bytes (deduped)",
        }

    # New bytes: retain immutably.
    compact = retrieved_at.replace("-", "").replace(":", "")
    fname = f"{compact}_{sha[:8]}.{source.ext}"
    dest = raw_dir(source.source_kind, source.report_year or "current") / fname
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(res.content)
    rel = str(dest.relative_to(config.store_root()))
    return {
        **base,
        "snapshot_id": f"{retrieved_at}_{sha[:8]}",
        "http_status": res.status,
        "http_etag": res.etag,
        "http_last_modified": res.last_modified,
        "content_sha256": sha,
        "byte_size": len(res.content),
        "local_path": rel,
        "note": None,
    }


def fetch(year: int | None = None, *, all_years: bool = False,
          include_weekly: bool = True, sources: list[Source] | None = None,
          http_get=_http_get, rate_limit_s: float = _RATE_LIMIT_S, now_fn=_utcnow) -> dict:
    """Capture the release-critical CFTC files into the immutable landing zone.

    Default: the current year's three annual zips (which carry the newest release) plus
    the Legacy weekly static (for its true-publication Last-Modified). ``all_years``
    walks 1986→current for a cold-start backfill of raw bytes. An explicit ``sources``
    list overrides the year/weekly derivation (targeted capture; used in tests).

    Returns ``{"records": [...], "new_files": n, "checks": n}``.
    """
    if sources is None:
        this_year = now_fn().year
        years = range(1986, this_year + 1) if all_years else [year or this_year]
        sources = []
        for y in years:
            sources.extend(annual_sources(y))
        if include_weekly:
            sources.append(WEEKLY_STATIC)

    m = _read_manifest()
    snapshots = m["snapshots"]
    new_files = 0
    records = []
    for i, src in enumerate(sources):
        rec = capture_source(src, snapshots=snapshots, http_get=http_get, now=now_fn())
        snapshots.append(rec)   # visible to the next source's _latest_for_url
        records.append(rec)
        if rec.get("note") is None and rec.get("byte_size") is not None:
            new_files += 1
        if rate_limit_s and i < len(sources) - 1:
            time.sleep(rate_limit_s)

    m["schema_version"] = SCHEMA_VERSION
    _write_manifest(m)
    return {"records": records, "new_files": new_files, "checks": len(records)}
