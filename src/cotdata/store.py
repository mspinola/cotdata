"""Canonical store I/O: atomic Parquet writes + a manifest. The store is the
contract between producers (write) and consumers (read)."""
import datetime as dt
import json
import logging
import os
import tempfile
from pathlib import Path

import pandas as pd

from . import config

logger = logging.getLogger(__name__)


def _atomic_write_parquet(df: pd.DataFrame, path: Path) -> None:
    """Write to a temp file in the same dir, then os.replace — so a consumer
    syncing/reading concurrently never sees a half-written parquet."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    os.close(fd)
    try:
        df.to_parquet(tmp)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# ── Metadata ──────────────────────────────────────────────────────────────
# Unlike prices/COT (one parquet per symbol), contract specs live in ONE table
# keyed by Symbol. So a *scoped* refresh must upsert (see upsert_metadata) — a
# plain write_metadata would replace the whole table and drop unlisted markets.
def write_metadata(df: pd.DataFrame, source: str = "norgate") -> None:
    _atomic_write_parquet(df, config.metadata_dir() / "contract_specs.parquet")
    _touch_manifest("metadata", "contract_specs", df, source)


def upsert_metadata(df: pd.DataFrame, source: str = "norgate") -> None:
    """Merge `df` into the existing contract_specs table by ``Symbol``: rows for
    symbols present in `df` are replaced/added; rows for symbols NOT in `df` are
    kept. Use for a scoped (subset-of-symbols) refresh so it never drops the
    contract specs of markets that weren't in the request. Use write_metadata to
    replace the whole table (a full registry regeneration)."""
    existing = read_metadata()
    if not existing.empty and "Symbol" in existing.columns:
        keep = existing[~existing["Symbol"].isin(df["Symbol"])]
        merged = pd.concat([keep, df], ignore_index=True)
    else:
        merged = df
    merged = merged.sort_values("Symbol").reset_index(drop=True)
    write_metadata(merged, source=source)


def read_metadata() -> pd.DataFrame:
    p = config.metadata_dir() / "contract_specs.parquet"
    return pd.read_parquet(p) if p.exists() else pd.DataFrame()


# ── Prices ────────────────────────────────────────────────────────────────
def write_prices(symbol: str, adjustment: str, df: pd.DataFrame, source: str) -> None:
    _atomic_write_parquet(df, config.prices_dir() / f"{symbol}_{adjustment}.parquet")
    _touch_manifest("prices", f"{symbol}_{adjustment}", df, source)


def read_prices(symbol: str, adjustment: str) -> pd.DataFrame:
    p = config.prices_dir() / f"{symbol}_{adjustment}.parquet"
    return pd.read_parquet(p) if p.exists() else pd.DataFrame()


# ── COT Legacy ────────────────────────────────────────────────────────────
def write_cot_legacy(name: str, df: pd.DataFrame, source: str) -> None:
    _atomic_write_parquet(df, config.cot_legacy_dir() / f"{name}.parquet")
    _touch_manifest("cot_legacy", name, df, source)


def read_cot_legacy(name: str) -> pd.DataFrame:
    p = config.cot_legacy_dir() / f"{name}.parquet"
    return pd.read_parquet(p) if p.exists() else pd.DataFrame()


# ── COT Disaggregated ─────────────────────────────────────────────────────
def write_cot_disagg(name: str, df: pd.DataFrame, source: str) -> None:
    _atomic_write_parquet(df, config.cot_disagg_dir() / f"{name}.parquet")
    _touch_manifest("cot_disagg", name, df, source)


def read_cot_disagg(name: str) -> pd.DataFrame:
    p = config.cot_disagg_dir() / f"{name}.parquet"
    return pd.read_parquet(p) if p.exists() else pd.DataFrame()


# ── COT TFF (Traders in Financial Futures) ────────────────────────────────
def write_cot_tff(name: str, df: pd.DataFrame, source: str) -> None:
    _atomic_write_parquet(df, config.cot_tff_dir() / f"{name}.parquet")
    _touch_manifest("cot_tff", name, df, source)


def read_cot_tff(name: str) -> pd.DataFrame:
    p = config.cot_tff_dir() / f"{name}.parquet"
    return pd.read_parquet(p) if p.exists() else pd.DataFrame()



# ── Manifest ──────────────────────────────────────────────────────────────
# ── The COT / price seam ──────────────────────────────────────────────────
# Which producer half owns each domain. This is the seam ADR-0007 makes explicit:
# the CFTC producer writes the `cot` half, the price producers write the `prices`
# half, and they never touch the same manifest file. Listing every domain here (and
# refusing unknown ones) is what stops a new domain quietly joining the wrong side.
HALVES = ("cot", "prices")
_DOMAIN_HALF = {
    "prices": "prices",
    "metadata": "prices",
    "cot": "cot",
    "cot_legacy": "cot",
    "cot_disagg": "cot",
    "cot_tff": "cot",
}


def half_for(kind: str) -> str:
    """The producer half owning `kind`. Raises on an undeclared domain, so adding one
    forces a decision about which side of the seam it belongs on."""
    try:
        return _DOMAIN_HALF[kind]
    except KeyError:
        raise ValueError(
            f"domain {kind!r} is not assigned to a producer half. Add it to "
            f"store._DOMAIN_HALF ('cot' or 'prices') before writing it."
        ) from None


def _empty_manifest() -> dict:
    return {"schema_version": config.SCHEMA_VERSION, "metadata": {}, "prices": {},
            "cot_legacy": {}, "cot_disagg": {}, "cot_tff": {}}


def _read_json(p) -> dict:
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except json.JSONDecodeError:
        return {}


def _overlay(dest: dict, src: dict, only_domains=None) -> None:
    for key, value in src.items():
        if key == "schema_version":
            dest["schema_version"] = max(int(dest.get("schema_version", 0) or 0),
                                         int(value or 0))
            continue
        if only_domains is not None and key not in only_domains:
            continue
        if isinstance(value, dict):
            dest.setdefault(key, {}).update(value)
        else:
            dest[key] = value


def load_manifest() -> dict:
    """The manifest, assembled from the per-half files.

    Nothing writes the legacy aggregate any more. It is still read, but ONLY for a
    half whose own file does not exist yet, so an un-migrated store keeps working
    while a migrated one never touches it. The fallback is per-half rather than
    all-or-nothing because a store can be half migrated: the price producer may have
    run on the new code while the COT producer has not, leaving prices in
    manifests/prices.json and everything else still in the aggregate.

    Run ``cotdata-update --migrate-manifests`` once to convert a store and silence
    the warning. The fallback goes away in a later release.
    """
    merged: dict = {}
    for half in HALVES:
        _overlay(merged, _read_json(config.manifest_path_for(half)))

    legacy = _read_json(config.manifest_path())
    if legacy:
        # Fall back per DOMAIN, not per half. A half file existing does not mean the
        # half is complete: a real store was found with manifests/prices.json holding
        # `prices` but not `metadata`, because the price producer had run on the new
        # code while the metadata producer had not. A per-half rule would have hidden
        # `metadata` entirely until the migration ran.
        missing = {k for k, v in legacy.items()
                   if k != "schema_version" and isinstance(v, dict) and k not in merged}
        if missing:
            _overlay(merged, legacy, only_domains=missing)
            logger.warning(
                "reading the legacy manifest.json for %s. Nothing writes that file any "
                "more, and a file-level sync between two stores resolves it "
                "last-writer-wins. Run 'cotdata-update --migrate-manifests' once on "
                "this store.", ", ".join(sorted(missing)))
        merged["schema_version"] = max(int(merged.get("schema_version", 0) or 0),
                                       int(legacy.get("schema_version", 0) or 0))

    if not merged:
        return _empty_manifest()
    merged.setdefault("schema_version", config.SCHEMA_VERSION)
    return merged


def schema_version() -> int:
    """Schema version recorded in the *store's* manifest — the version of the data
    on disk, which is NOT the same as config.SCHEMA_VERSION (the library's target)
    until a producer pass has re-written the store. Consumers key cache
    invalidation on this so a schema bump forces a rebuild."""
    return int(load_manifest().get("schema_version", 0))


def require_schema(min_version: int) -> None:
    """Fail fast if the store predates a schema the caller depends on. Lets a
    consumer refuse to run against a stale store rather than silently read the
    old shape."""
    v = schema_version()
    if v < min_version:
        raise RuntimeError(
            f"cotdata store schema_version={v} < required {min_version}. "
            f"Re-run the producer (e.g. norgate.update) to migrate the store."
        )


def migrate_manifests() -> dict:
    """Split a legacy ``manifest.json`` into per-half files. Idempotent.

    Run once per store when upgrading. Entries already present in a half file win, so
    re-running cannot resurrect stale bookkeeping and an already-migrated store is
    left alone. Returns ``{half: n_entries_added}``.
    """
    legacy = _read_json(config.manifest_path())
    added = {h: 0 for h in HALVES}
    if not legacy:
        return added
    for half in HALVES:
        part = _read_json(config.manifest_path_for(half))
        for kind, entries in legacy.items():
            if kind == "schema_version" or not isinstance(entries, dict):
                continue
            try:
                if half_for(kind) != half:
                    continue
            except ValueError:
                continue  # a retired domain the current code no longer declares
            dest = part.setdefault(kind, {})
            for name, entry in entries.items():
                if name not in dest:
                    dest[name] = entry
                    added[half] += 1
        if part:
            part["schema_version"] = max(int(part.get("schema_version", 0) or 0),
                                         int(legacy.get("schema_version", 0) or 0))
            _atomic_write_json(part, config.manifest_path_for(half))
    return added


def _touch_manifest(kind: str, name: str, df: pd.DataFrame, source: str) -> None:
    """Record one entry into its half's manifest.

    Only the half file is written. The legacy aggregate held both halves in ONE file,
    which made it unsafe in two ways: two producers doing a read-modify-write on it
    lose each other's entries, and a file-level sync between two stores resolves it
    last-writer-wins and silently discards one side. The per-half files are disjoint,
    so both problems go away by construction.
    """
    half = half_for(kind)
    last = None
    if len(df) and isinstance(df.index, pd.DatetimeIndex):
        last = str(df.index.max().date())
    entry = {
        "last_date": last,
        "n_rows": int(len(df)),
        "source": source,
        "updated_at": dt.datetime.now(dt.timezone.utc)
                        .replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }

    part = _read_json(config.manifest_path_for(half))
    part.setdefault(kind, {})[name] = entry
    part["schema_version"] = config.SCHEMA_VERSION
    _atomic_write_json(part, config.manifest_path_for(half))


def _atomic_write_json(m: dict, path) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(m, indent=2, sort_keys=True))
    os.replace(tmp, path)


def _write_manifest(m: dict) -> None:
    """Write the legacy aggregate. Retained for callers outside this module."""
    _atomic_write_json(m, config.manifest_path())


# domain -> the directory holding its {name}.parquet files
_DOMAIN_DIRS = {
    "prices": config.prices_dir,
    "metadata": config.metadata_dir,
    "cot_legacy": config.cot_legacy_dir,
    "cot_disagg": config.cot_disagg_dir,
    "cot_tff": config.cot_tff_dir,
}


def _domain_dir(domain: str) -> Path:
    fn = _DOMAIN_DIRS.get(domain)
    return fn() if fn else (config.store_root() / domain)  # unknown/dead domain


def reconcile_manifest() -> dict:
    """Prune manifest entries whose parquet file is missing — ghosts left by old
    naming schemes (bare CFTC codes before the ``{symbol}_{code}`` convention, the
    retired ``cot`` domain, …) — and drop domains left empty. Returns
    ``{domain: [pruned names]}``.

    Provably safe: only removes bookkeeping for files that do not exist on disk,
    never deletes or renames data.

    Operates on each manifest FILE in place rather than on the merged view, so a
    prune is written back to the file the entry actually lives in. The legacy
    aggregate is pruned too when it is still present, so an un-migrated store can be
    cleaned without migrating first.
    """
    pruned: dict = {}

    def _prune(doc: dict) -> bool:
        changed = False
        for domain in [k for k, v in doc.items() if isinstance(v, dict)]:
            d = _domain_dir(domain)
            gone = [n for n in doc[domain] if not (d / f"{n}.parquet").exists()]
            if gone:
                for n in gone:
                    del doc[domain][n]
                pruned.setdefault(domain, []).extend(gone)
                changed = True
            if not doc[domain]:
                del doc[domain]
                changed = True
        return changed

    targets = [config.manifest_path_for(h) for h in HALVES] + [config.manifest_path()]
    for path in targets:
        doc = _read_json(path)
        if doc and _prune(doc):
            _atomic_write_json(doc, path)

    return {k: sorted(set(v)) for k, v in pruned.items()}
