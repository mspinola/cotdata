"""Store location + schema version. The store is set via COTDATA_STORE."""
import os
from pathlib import Path

SCHEMA_VERSION = 1


def store_root() -> Path:
    root = os.environ.get("COTDATA_STORE", "").strip()
    if not root:
        raise RuntimeError(
            "COTDATA_STORE is not set. Point it at the shared data store "
            "(the synced folder holding prices/, cot/, manifest.json)."
        )
    return Path(root)


def prices_dir() -> Path:
    return store_root() / "prices"


def cot_dir() -> Path:
    return store_root() / "cot"


def manifest_path() -> Path:
    return store_root() / "manifest.json"
