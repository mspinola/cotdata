"""Store location + schema version. The store is set via COTDATA_STORE."""
import os
from pathlib import Path

# v2 — reconstructed volume promoted: prices carry Volume_Reconstructed /
# Volume_Source, and get_prices(volume="reconstructed") serves them. The store
# was migrated by a full producer pass (2026-07-14) and now carries v2 shape;
# schema_version() reflects the on-disk manifest, so a fresh/partial store can
# still read <2.
SCHEMA_VERSION = 2


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


def metadata_dir() -> Path:
    return store_root() / "metadata"


def cot_legacy_dir() -> Path:
    return store_root() / "cot_legacy"


def cot_disagg_dir() -> Path:
    return store_root() / "cot_disagg"


def cot_tff_dir() -> Path:
    return store_root() / "cot_tff"


def manifest_path() -> Path:
    return store_root() / "manifest.json"
