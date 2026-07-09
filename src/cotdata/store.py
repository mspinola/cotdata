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


# ── Prices ────────────────────────────────────────────────────────────────
def write_prices(symbol: str, adjustment: str, df: pd.DataFrame, source: str) -> None:
    _atomic_write_parquet(df, config.prices_dir() / f"{symbol}_{adjustment}.parquet")
    _touch_manifest("prices", f"{symbol}_{adjustment}", df, source)


def read_prices(symbol: str, adjustment: str) -> pd.DataFrame:
    p = config.prices_dir() / f"{symbol}_{adjustment}.parquet"
    return pd.read_parquet(p) if p.exists() else pd.DataFrame()


# ── COT ───────────────────────────────────────────────────────────────────
def write_cot(name: str, df: pd.DataFrame, source: str) -> None:
    _atomic_write_parquet(df, config.cot_dir() / f"{name}.parquet")
    _touch_manifest("cot", name, df, source)


def read_cot(name: str) -> pd.DataFrame:
    p = config.cot_dir() / f"{name}.parquet"
    return pd.read_parquet(p) if p.exists() else pd.DataFrame()


# ── Manifest ──────────────────────────────────────────────────────────────
def load_manifest() -> dict:
    p = config.manifest_path()
    if p.exists():
        return json.loads(p.read_text())
    return {"schema_version": config.SCHEMA_VERSION, "prices": {}, "cot": {}}


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
    tmp = config.manifest_path().with_suffix(".json.tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(m, indent=2, sort_keys=True))
    os.replace(tmp, config.manifest_path())
