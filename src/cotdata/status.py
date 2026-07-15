"""Read-only store status (``cotdata-update --check``) and the producer run
summary. Both work off manifest.json only — no network, no Windows/norgatedata,
so ``--check`` runs anywhere the store is visible.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from . import store, config

# Domains shown by --check, in report order.
_DOMAINS = ["prices", "metadata", "cot", "cot_legacy", "cot_disagg", "cot_tff"]
# An entry lagging more than this many days behind its domain's newest data
# probably failed to update while its peers succeeded (a partial run).
_LAG_DAYS = 3


def _parse_date(s: Optional[str]) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat(s) if s else None
    except (ValueError, TypeError):
        return None


def summarize(manifest: dict, today: Optional[dt.date] = None) -> dict:
    """Pure summary of a manifest — one record per non-empty domain."""
    today = today or dt.date.today()
    out = {"schema_version": manifest.get("schema_version"),
           "today": today.isoformat(), "domains": {}}
    for domain in _DOMAINS:
        entries = manifest.get(domain, {})
        if not isinstance(entries, dict) or not entries:
            continue
        dated = {n: _parse_date(e.get("last_date")) for n, e in entries.items()}
        dates = [d for d in dated.values() if d]
        newest = max(dates) if dates else None
        lagging = []
        if newest:
            for name, d in dated.items():
                behind = (newest - d).days if d else None
                if behind is not None and behind > _LAG_DAYS:
                    lagging.append((name, entries[name].get("last_date"), behind))
        writes = [e.get("updated_at") for e in entries.values() if e.get("updated_at")]
        out["domains"][domain] = {
            "entries": len(entries),
            "rows": sum(int(e.get("n_rows") or 0) for e in entries.values()),
            "newest": newest.isoformat() if newest else None,
            "oldest": min(dates).isoformat() if dates else None,
            "last_write": max(writes) if writes else None,
            "behind_today": (today - newest).days if newest else None,
            "lagging": sorted(lagging, key=lambda t: -t[2]),
        }
    return out


def format_report(manifest: dict, root: str = "", today: Optional[dt.date] = None) -> str:
    """Human-readable --check report."""
    s = summarize(manifest, today)
    target = config.SCHEMA_VERSION
    sv = s["schema_version"]
    sv_note = "" if sv == target else f"  ⚠ target {target} — run the producer to migrate"
    L = [f"cotdata store  ·  {root or '(COTDATA_STORE)'}",
         f"as of {s['today']}  ·  schema_version {sv}{sv_note}",
         ""]
    if not s["domains"]:
        L.append("store is empty — no data written yet.")
        return "\n".join(L)

    L.append(f"{'domain':<12}{'entries':>8}{'rows':>13}{'newest data':>14}"
             f"{'last write (UTC)':>22}{'behind':>8}")
    for domain, d in s["domains"].items():
        behind = "—" if d["behind_today"] is None else f"{d['behind_today']}d"
        L.append(f"{domain:<12}{d['entries']:>8}{d['rows']:>13,}"
                 f"{str(d['newest']):>14}{str(d['last_write']):>22}{behind:>8}")

    for domain, d in s["domains"].items():
        if d["lagging"]:
            L.append("")
            L.append(f"⚠ {domain}: {len(d['lagging'])} entr{'y' if len(d['lagging'])==1 else 'ies'} "
                     f"lag >{_LAG_DAYS}d behind newest ({d['newest']}):")
            for name, last, behind in d["lagging"][:15]:
                L.append(f"    {name:<18}{last}  ({behind}d behind)")
            if len(d["lagging"]) > 15:
                L.append(f"    … and {len(d['lagging']) - 15} more")
    if not any(d["lagging"] for d in s["domains"].values()):
        L.append("")
        L.append("✓ all entries current (none lag behind their domain's newest).")
    return "\n".join(L)


def print_check() -> None:
    """Entry point for ``cotdata-update --check``."""
    root = str(config.store_root())
    print(format_report(store.load_manifest(), root=root))


def run_summary(kind: str, ok: list, failed: list, total_rows: int,
                seconds: float, newest: Optional[str] = None) -> str:
    """Footer printed at the end of a producer run (also emailable later)."""
    n = len(ok) + len(failed)
    L = ["-" * 64,
         f"{kind}: {len(ok)}/{n} OK · {len(failed)} failed · "
         f"{total_rows:,} rows · {seconds:.0f}s"
         + (f" · newest {newest}" if newest else "")]
    if failed:
        for sym, err in failed:
            L.append(f"  ✗ {sym}: {str(err)[:80]}")
    return "\n".join(L)
