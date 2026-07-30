"""COT vintage ingest — change-only bitemporal observations + field-level revisions.

Commit 2 of the vintage subsystem. Sits on top of the raw snapshots captured by
``vintage.py``: parse → canonical long schema → validate → change-only write → emit
revisions. pandas/pyarrow throughout (no database — crucible-stack ADR-0008).

Canonical natural key: ``(report_date, market_code, report_type, combined, category)``.
A point-in-time read is "greatest ``observed_at`` <= t per natural key" — no valid_to
column. Change-only writes mean storage grows with revisions, not with time.

See docs/design/cot_vintage.md.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
from pathlib import Path

import pandas as pd

from . import vintage

# ── Schema ──────────────────────────────────────────────────────────────────
NATURAL_KEY = ["report_date", "market_code", "report_type", "combined", "category"]

# Fields that constitute the *value* of an observation. row_sha256 is computed over
# these only: not over provenance (observed_at/snapshot_id), not over release_date
# (resolved separately, so a release-date backfill must NOT read as a data revision),
# not over market_name (descriptive).
VALUE_FIELDS = ["long_contracts", "short_contracts", "spread_contracts",
                "trader_count_long", "trader_count_short", "open_interest",
                "cr4_net_long", "cr4_net_short", "cr8_net_long", "cr8_net_short"]

# market_name is descriptive, not a value: it is deliberately OUT of the hash, so a CFTC
# market rename never registers as a revision. Consequence to know before debugging it:
# once a natural key is written, its stored market_name is never updated by a later
# ingest (that row is a change-only skip). The market_code is the identity.
PROVENANCE_FIELDS = ["release_date", "release_date_source", "market_name",
                     "observed_at", "snapshot_id", "row_sha256", "is_tombstone"]

ALL_COLUMNS = NATURAL_KEY + ["market_name"] + VALUE_FIELDS + [
    "release_date", "release_date_source", "observed_at", "snapshot_id",
    "row_sha256", "is_tombstone"]

# Controlled vocabulary per report type (validation §5). "nonreportable" is common
# to all three; the rest come from each report's reporting categories.
CATEGORIES = {
    "legacy": {"commercial", "noncommercial", "nonreportable"},
    "disaggregated": {"producer_merchant", "swap", "managed_money",
                      "other_reportable", "nonreportable"},
    "tff": {"dealer", "asset_manager", "leveraged", "other_reportable", "nonreportable"},
}


# ── Paths ───────────────────────────────────────────────────────────────────
def _obs_dir() -> Path:
    return vintage.vintage_root() / "observations"


def _rev_dir() -> Path:
    return vintage.vintage_root() / "revisions"


def _obs_path(report_year: int) -> Path:
    return _obs_dir() / f"report_year={report_year}" / "observations.parquet"


def _rev_path(detected_year: int) -> Path:
    return _rev_dir() / f"detected_year={detected_year}" / "revisions.parquet"


# ── Hashing ─────────────────────────────────────────────────────────────────
def _naive_utc(ts) -> pd.Timestamp:
    """Normalise a timestamp to tz-naive UTC — the repo stores tz-naive datetimes
    throughout (Report_Date, price index), so vintage timestamps match that convention
    and never trip tz-aware/tz-naive arithmetic."""
    t = pd.Timestamp(ts)
    if t.tzinfo is not None:
        t = t.tz_convert("UTC").tz_localize(None)
    return t


def _norm(v) -> str:
    """Canonical string form of one value, for hashing and field comparison.

    Deliberately independent of any third-party scalar formatting: numpy/pandas scalars
    are unwrapped to Python natives via ``.item()`` and formatted explicitly. The hash is
    a PERMANENT artifact — if it moved with a numpy/pandas formatting change (numpy 2.0
    already changed scalar repr once) every stored row would appear revised at once, a
    silent mass revision event across every market. Non-integer floats only occur in the
    cr4/cr8 ratios, which is exactly where that would bite.
    """
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass  # non-scalar or unsupported type → fall through to explicit formatting
    if hasattr(v, "item"):
        try:
            v = v.item()  # np.int64/np.float64/np.bool_ → int/float/bool
        except (AttributeError, ValueError):
            pass
    if isinstance(v, bool):  # must precede int: bool is a subclass of int
        return "1" if v else "0"
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else format(v, ".10g")
    if isinstance(v, int):
        return str(v)
    return str(v)


def row_sha256(row: dict) -> str:
    payload = "|".join(f"{f}={_norm(row.get(f))}" for f in VALUE_FIELDS)
    return hashlib.sha256(payload.encode()).hexdigest()


# ── Canonicalisation from the existing wide parsers ─────────────────────────
def canonicalize_legacy(wide: pd.DataFrame, *, combined: bool = False) -> pd.DataFrame:
    """Melt a Legacy wide frame (the shape ``providers/cftc.py`` emits, Report_Date
    index) into canonical long rows: three category rows per (report_date, market)."""
    rows = []
    for report_date, r in wide.iterrows():
        market_code = str(r["CFTC_Contract_Market_Code"])
        market_name = r.get("Market_and_Exchange_Names")
        oi = r.get("Open_Interest_All")
        cats = {
            "commercial": ("Comm_Positions_Long_All", "Comm_Positions_Short_All",
                           "Traders_Comm_Long_All", "Traders_Comm_Short_All"),
            "noncommercial": ("NonComm_Positions_Long_All", "NonComm_Positions_Short_All",
                              "Traders_NonComm_Long_All", "Traders_NonComm_Short_All"),
            "nonreportable": ("NonRept_Positions_Long_All", "NonRept_Positions_Short_All",
                              None, None),
        }
        for category, (lc, sc, tl, ts) in cats.items():
            rows.append({
                "report_date": pd.Timestamp(report_date).normalize(),
                "market_code": market_code,
                "market_name": market_name,
                "report_type": "legacy",
                "combined": combined,
                "category": category,
                "long_contracts": r.get(lc),
                "short_contracts": r.get(sc),
                "spread_contracts": pd.NA,
                "trader_count_long": r.get(tl) if tl else pd.NA,
                "trader_count_short": r.get(ts) if ts else pd.NA,
                "open_interest": oi,
                "cr4_net_long": pd.NA, "cr4_net_short": pd.NA,
                "cr8_net_long": pd.NA, "cr8_net_short": pd.NA,
            })
    return pd.DataFrame(rows)


# ── Validation (§5) ─────────────────────────────────────────────────────────
class ValidationError(ValueError):
    pass


class ConsistencyError(RuntimeError):
    """The change-hash and the field-level diff disagreed.

    These are two different comparison paths: the hash compares a STORED row_sha256
    against a freshly computed one, while the field loop compares parquet-read values
    against fresh ones. If they ever disagree we would write an observation row with no
    corresponding revision rows — a revision that silently lost its detail. Raising turns
    any future dtype/round-trip drift into a loud failure instead of a data-quality
    mystery discovered months later.
    """


def validate(canonical: pd.DataFrame) -> list[str]:
    """Fail loudly (raise) on structural problems; return a list of soft warnings.

    Raises rather than partially ingesting on: missing natural-key columns, null
    natural-key values, or categories outside the controlled vocabulary. Warns (does
    not fail) on ``long+short+spread > open_interest`` — definitional edge cases exist.
    """
    missing = [c for c in NATURAL_KEY if c not in canonical.columns]
    if missing:
        raise ValidationError(f"missing natural-key columns: {missing}")
    if canonical.empty:
        raise ValidationError("empty canonical frame — refusing to ingest nothing")
    for c in NATURAL_KEY:
        if canonical[c].isna().any():
            raise ValidationError(f"null values in natural-key column {c!r}")
    for rt, grp in canonical.groupby("report_type"):
        vocab = CATEGORIES.get(rt)
        if vocab is None:
            raise ValidationError(f"unknown report_type {rt!r}")
        bad = set(grp["category"]) - vocab
        if bad:
            raise ValidationError(f"categories {bad} outside vocabulary for {rt!r}")

    warnings = []
    for _, r in canonical.iterrows():
        oi = r.get("open_interest")
        parts = [r.get("long_contracts"), r.get("short_contracts"), r.get("spread_contracts")]
        s = sum(p for p in parts if p is not None and not pd.isna(p))
        if oi is not None and not pd.isna(oi) and s > oi:
            warnings.append(f"{r['report_date'].date()} {r['market_code']} "
                            f"{r['category']}: long+short+spread ({s}) > OI ({oi})")
    return warnings


# ── Observation store I/O ───────────────────────────────────────────────────
def read_observations(report_years=None) -> pd.DataFrame:
    d = _obs_dir()
    if not d.exists():
        return pd.DataFrame(columns=ALL_COLUMNS)
    parts = sorted(d.glob("report_year=*/observations.parquet"))
    if report_years is not None:
        want = {int(y) for y in report_years}
        parts = [p for p in parts if int(p.parent.name.split("=")[1]) in want]
    if not parts:
        return pd.DataFrame(columns=ALL_COLUMNS)
    return pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)


def _latest_by_key(obs: pd.DataFrame) -> pd.DataFrame:
    """Most recent observation per natural key, with a DETERMINISTIC tie-break.

    Ordering is (observed_at, snapshot_id) ascending → take the last per key. When two
    snapshots share an ``observed_at`` (same-second ingests), the lexicographically
    greater ``snapshot_id`` wins rather than whichever row happened to land first in
    file/append order. Capture snapshot_ids lead with a compact retrieved_at timestamp,
    so that ordering also tracks retrieval time. A stable sort keeps it reproducible."""
    if obs.empty:
        return obs
    ordered = obs.sort_values(["observed_at", "snapshot_id"], kind="mergesort")
    # Return the grouped rows DIRECTLY. Round-tripping through obs.loc[idx] would be a
    # duplicate-label hazard: any caller that built `obs` without ignore_index=True has
    # repeated index labels, and .loc on those returns EVERY matching row — silently
    # yielding several rows per natural key, which reads as data corruption rather than
    # an indexing bug.
    return ordered.groupby(NATURAL_KEY, dropna=False, sort=False).tail(1)


class _WriteLock:
    """Advisory single-writer lock over the vintage subtree.

    The observation/revision writes are read-concat-rewrite: atomic per file, but two
    concurrent ingest processes would resolve last-writer-wins and silently drop one
    side's rows. The COT producer is single-writer by design (the same reason the store
    manifests are split per half), so this lock exists to convert that silent data-loss
    mode into a loud error, not to support concurrency.
    """

    def __init__(self, root: Path):
        self.path = root / ".ingest.lock"

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            holder = ""
            try:
                holder = f" (held by pid {self.path.read_text().strip()})"
            except OSError:
                pass
            raise RuntimeError(
                f"another cotdata vintage ingest is writing this store{holder}. "
                f"The vintage writers are single-writer by design. If no such process is "
                f"running, remove the stale lock: {self.path}") from None
        with os.fdopen(fd, "w") as fh:
            fh.write(str(os.getpid()))
        return self

    def __exit__(self, *exc):
        try:
            self.path.unlink()
        except OSError:
            pass
        return False


def _append_parquet(path: Path, new_rows: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = pd.read_parquet(path)
        combined = pd.concat([existing, new_rows], ignore_index=True)
    else:
        combined = new_rows
    tmp = path.with_suffix(".parquet.tmp")
    combined.to_parquet(tmp)
    tmp.replace(path)


# ── Ingest (change-only) ────────────────────────────────────────────────────
def ingest_canonical(canonical: pd.DataFrame, *, snapshot_id: str,
                     observed_at: dt.datetime | None = None,
                     validate_input: bool = True) -> dict:
    """Change-only write of a canonical frame + field-level revision emission.

    Idempotent: re-ingesting an identical frame writes zero observations and zero
    revisions, because every row's ``row_sha256`` matches the latest stored value for
    its natural key. Returns ``{"observations": n, "revisions": n, "warnings": [...]}``.
    """
    warnings = validate(canonical) if validate_input else []
    if observed_at is None:
        observed_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    observed_ts = _naive_utc(observed_at)

    prior = _latest_by_key(read_observations())
    prior_by_key = {}
    for _, r in prior.iterrows():
        prior_by_key[tuple(r[k] for k in NATURAL_KEY)] = r

    new_obs: dict[int, list] = {}
    revisions: list[dict] = []
    detected_at = observed_ts

    for _, row in canonical.iterrows():
        rec = row.to_dict()
        rec["row_sha256"] = row_sha256(rec)
        key = tuple(rec[k] for k in NATURAL_KEY)
        prev = prior_by_key.get(key)
        if prev is not None and prev["row_sha256"] == rec["row_sha256"]:
            continue  # unchanged value → change-only skip

        rec["observed_at"] = observed_ts
        rec["snapshot_id"] = snapshot_id
        rec.setdefault("release_date", pd.NaT)
        rec.setdefault("release_date_source", "unknown")
        rec.setdefault("is_tombstone", False)
        ry = pd.Timestamp(rec["report_date"]).year
        new_obs.setdefault(ry, []).append(rec)

        if prev is not None:
            report_date = pd.Timestamp(rec["report_date"])
            age_days = int((detected_at.normalize() - report_date.normalize()).days)
            n_before = len(revisions)
            for f in VALUE_FIELDS:
                old, new = prev.get(f), rec.get(f)
                if _norm(old) == _norm(new):
                    continue
                delta = pct = None
                try:
                    if not pd.isna(old) and not pd.isna(new):
                        delta = float(new) - float(old)
                        pct = (delta / float(old)) if float(old) != 0 else None
                except (TypeError, ValueError):
                    pass
                revisions.append({
                    **{k: rec[k] for k in NATURAL_KEY},
                    "field": f, "old_value": _norm(old), "new_value": _norm(new),
                    "delta": delta, "pct_delta": pct,
                    "old_snapshot_id": prev.get("snapshot_id"),
                    "new_snapshot_id": snapshot_id,
                    "detected_at": detected_at, "age_days": age_days,
                    "revision_id": hashlib.sha256(
                        f"{key}|{f}|{prev.get('snapshot_id')}|{snapshot_id}".encode()
                    ).hexdigest()[:16],
                })
            if len(revisions) == n_before:
                raise ConsistencyError(
                    f"row_sha256 changed for {key} but no VALUE_FIELDS differ "
                    f"(stored {prev['row_sha256'][:12]} vs computed {rec['row_sha256'][:12]}). "
                    f"The hash and the field-diff comparison paths disagree — refusing to "
                    f"write an observation with no revision detail.")

    n_obs = 0
    with _WriteLock(vintage.vintage_root()):
        for ry, recs in new_obs.items():
            frame = pd.DataFrame(recs).reindex(columns=ALL_COLUMNS)
            _append_parquet(_obs_path(ry), frame)
            n_obs += len(recs)

        if revisions:
            by_year: dict[int, list] = {}
            for rv in revisions:
                by_year.setdefault(pd.Timestamp(rv["detected_at"]).year, []).append(rv)
            for dy, recs in by_year.items():
                _append_parquet(_rev_path(dy), pd.DataFrame(recs))

    return {"observations": n_obs, "revisions": len(revisions), "warnings": warnings}


# ── Point-in-time read (§4 / acceptance §4) ─────────────────────────────────
def asof(t, *, report_date=None, market_code=None, report_type=None) -> pd.DataFrame:
    """The dataset as known at timestamp ``t``: for each natural key, the row with the
    greatest ``observed_at`` <= t. Rows first observed after ``t`` are absent."""
    obs = read_observations()
    if obs.empty:
        return obs
    t = _naive_utc(t)
    obs = obs[obs["observed_at"] <= t]
    if report_date is not None:
        obs = obs[obs["report_date"] == pd.Timestamp(report_date).normalize()]
    if market_code is not None:
        obs = obs[obs["market_code"] == str(market_code)]
    if report_type is not None:
        obs = obs[obs["report_type"] == report_type]
    return _latest_by_key(obs).reset_index(drop=True)


def read_revisions(detected_years=None) -> pd.DataFrame:
    d = _rev_dir()
    if not d.exists():
        return pd.DataFrame()
    parts = sorted(d.glob("detected_year=*/revisions.parquet"))
    if detected_years is not None:
        want = {int(y) for y in detected_years}
        parts = [p for p in parts if int(p.parent.name.split("=")[1]) in want]
    if not parts:
        return pd.DataFrame()
    return pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
