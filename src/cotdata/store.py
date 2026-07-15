"""Canonical store I/O: atomic Parquet writes + a manifest. The store is the
contract between producers (write) and consumers (read)."""
import json
import os
import tempfile
import datetime as dt
from pathlib import Path

import pandas as pd

from . import config


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
def write_metadata(df: pd.DataFrame, source: str = "norgate") -> None:
    _atomic_write_parquet(df, config.metadata_dir() / "contract_specs.parquet")
    _touch_manifest("metadata", "contract_specs", df, source)


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
def load_manifest() -> dict:
    p = config.manifest_path()
    if p.exists():
        return json.loads(p.read_text())
    return {"schema_version": config.SCHEMA_VERSION, "metadata": {}, "prices": {}, "cot_legacy": {}, "cot_disagg": {}, "cot_tff": {}}


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
            f"Re-run the producer (e.g. norgate.update) to migrate the store — "
            f"see docs/plan_promote_reconstructed_volume.md."
        )


def _touch_manifest(kind: str, name: str, df: pd.DataFrame, source: str) -> None:
    m = load_manifest()
    last = None
    if len(df) and isinstance(df.index, pd.DatetimeIndex):
        last = str(df.index.max().date())
    m.setdefault(kind, {})[name] = {
        "last_date": last,
        "n_rows": int(len(df)),
        "source": source,
        "updated_at": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    m["schema_version"] = config.SCHEMA_VERSION
    _write_manifest(m)


def _write_manifest(m: dict) -> None:
    tmp = config.manifest_path().with_suffix(".json.tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(m, indent=2, sort_keys=True))
    os.replace(tmp, config.manifest_path())


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

    Provably safe: only removes bookkeeping for files that do not exist on disk;
    never deletes or renames data.
    """
    m = load_manifest()
    pruned: dict = {}
    for domain in [k for k, v in m.items() if isinstance(v, dict)]:
        d = _domain_dir(domain)
        gone = [name for name in m[domain] if not (d / f"{name}.parquet").exists()]
        if gone:
            for name in gone:
                del m[domain][name]
            pruned[domain] = sorted(gone)
        if not m[domain]:
            del m[domain]
    if pruned:
        _write_manifest(m)
    return pruned
