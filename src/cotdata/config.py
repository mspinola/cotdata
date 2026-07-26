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
    """The legacy aggregate manifest, written by BOTH halves.

    Kept for consumers pinned to an older cotdata. Current code writes it but does not
    read it when per-half manifests exist, so the read-modify-write hazard it carries no
    longer affects anyone on this version. It is dropped once every producer and consumer
    has moved (ADR-0007 step 1, second half).
    """
    return store_root() / "manifest.json"


def manifests_dir() -> Path:
    return store_root() / "manifests"


def manifest_path_for(half: str) -> Path:
    """The manifest owned by one producer half, `cot` or `prices`.

    One writer per file is the whole point: ``_touch_manifest`` is a read-modify-write, so
    two producers sharing a manifest eventually lose an entry. Splitting by half means the
    CFTC producer and the price producer never touch the same file, and it is the shape
    ADR-0007 needs when the price half moves out to its own package.
    """
    if half not in ("cot", "prices"):
        raise ValueError(f"unknown manifest half {half!r}; expected 'cot' or 'prices'")
    return manifests_dir() / f"{half}.json"
