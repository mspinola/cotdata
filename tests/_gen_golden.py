"""Regenerate the current/ golden baseline fixture. Run on a CLEAN tree (before the
vintage subsystem alters anything) so the guard in test_current_baseline.py is meaningful:

    PYTHONPATH=src python tests/_gen_golden.py
"""
import tempfile
from pathlib import Path

from test_current_baseline import golden_frame  # type: ignore


def main() -> None:
    import os

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["COTDATA_STORE"] = tmp
        from cotdata import store
        store.write_cot_legacy("GOLD_088691", golden_frame(), source="cftc")
        src = Path(tmp) / "cot_legacy" / "GOLD_088691.parquet"
        dest = Path(__file__).parent / "fixtures" / "golden" / "cot_legacy_golden.parquet"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())
        print(f"wrote {dest} ({dest.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
