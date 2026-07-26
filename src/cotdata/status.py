"""Read-only store status (``cotdata-update --check``) and the producer run
summary. Both work off manifest.json only — no network, no Windows/norgatedata,
so ``--check`` runs anywhere the store is visible.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from typing import Optional

from . import config, store

STATUS_FILENAME = "status.json"


def status_path():
    return config.store_root() / STATUS_FILENAME

# Domains shown by --check, in report order.
_DOMAINS = ["prices", "metadata", "cot", "cot_legacy", "cot_disagg", "cot_tff"]
# An entry whose producer last touched it more than this many days behind its
# domain's newest write probably failed while its peers succeeded (a partial run).
_LAG_DAYS = 3


def _parse_date(s: Optional[str]) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat(s) if s else None
    except (ValueError, TypeError):
        return None


def _parse_write(s: Optional[str]) -> Optional[dt.datetime]:
    """Parse a manifest ``updated_at`` (ISO-8601, trailing Z) to a naive UTC datetime."""
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


def hist_code_names() -> set:
    """Entry names for symbols' predecessor (hist_code) COT contracts, e.g.
    ``RTY_23977A``. These are retired codes, legitimately frozen at an old date,
    so they should not be flagged as "lagging". Built from the registry."""
    from .registry import all_symbols, hist_code_scales
    names = set()
    for s in all_symbols():
        for hc, _ in hist_code_scales(s.hist_codes):
            names.add(f"{s.internal}_{hc}")
    return names


def summarize(manifest: dict, today: Optional[dt.date] = None,
              ignore_lag: Optional[set] = None) -> dict:
    """Pure summary of a manifest — one record per non-empty domain.

    ignore_lag: entry names never reported as lagging (e.g. retired hist_codes,
    which are frozen by design). They still count toward entries/rows/newest."""
    today = today or dt.date.today()
    ignore_lag = ignore_lag or set()
    out = {"schema_version": manifest.get("schema_version"),
           "today": today.isoformat(), "domains": {}}
    for domain in _DOMAINS:
        entries = manifest.get(domain, {})
        if not isinstance(entries, dict) or not entries:
            continue
        dated = {n: _parse_date(e.get("last_date")) for n, e in entries.items()}
        dates = [d for d in dated.values() if d]
        newest = max(dates) if dates else None
        writes = [e.get("updated_at") for e in entries.values() if e.get("updated_at")]

        # Lag is measured on WHEN THE PRODUCER LAST TOUCHED THE ENTRY, not on how
        # recent its data is. The check exists to catch a partial run (one entry
        # failed while its peers succeeded), and only updated_at distinguishes that
        # from a source that simply has nothing newer.
        #
        # Comparing last_date instead produced permanent false positives for the two
        # legitimate cases of an old-but-correct entry: thin markets that drop out of
        # the CFTC report below the reporting threshold and reappear months later
        # (NKD and ZO each have ~20 gaps over 10 days across 19 years, the longest
        # 168d and 294d), and retired hist_code contracts frozen by design. Both are
        # written on every pass, so both carry a current updated_at.
        parsed = {n: _parse_write(e.get("updated_at")) for n, e in entries.items()}
        usable = [w for w in parsed.values() if w]
        newest_write = max(usable) if usable else None
        lagging = []
        for name, e in entries.items():
            if name in ignore_lag:
                continue
            w = parsed[name]
            if w is None:
                # No usable write timestamp: report it rather than assume it is fine.
                # Silently passing here would turn "cannot check" into "all current",
                # which is the failure mode this whole check exists to avoid.
                lagging.append((name, e.get("updated_at") or "never", 9999))
                continue
            behind = (newest_write - w).days
            if behind > _LAG_DAYS:
                lagging.append((name, e.get("updated_at"), behind))
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


def format_report(manifest: dict, root: str = "", today: Optional[dt.date] = None,
                  ignore_lag: Optional[set] = None) -> str:
    """Human-readable --check report."""
    s = summarize(manifest, today, ignore_lag=ignore_lag)
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
                     f"NOT WRITTEN in the last run (>{_LAG_DAYS}d behind the domain's "
                     f"newest write, {d['last_write']}):")
            for name, wrote, behind in d["lagging"][:15]:
                L.append(f"    {name:<18}last written {wrote}  ({behind}d behind)")
            if len(d["lagging"]) > 15:
                L.append(f"    … and {len(d['lagging']) - 15} more")
    if not any(d["lagging"] for d in s["domains"].values()):
        L.append("")
        L.append("✓ every entry was written by the latest producer pass.")
    return "\n".join(L)


def print_check() -> None:
    """Entry point for ``cotdata-update --check``."""
    root = str(config.store_root())
    print(format_report(store.load_manifest(), root=root, ignore_lag=hist_code_names()))


def build_status_doc(manifest: dict, last_run: Optional[dict] = None,
                     today: Optional[dt.date] = None,
                     ignore_lag: Optional[set] = None) -> dict:
    """Machine-readable status document written to ``status.json`` after each run.

    Contract for external pollers:
      * ``newest_data[<domain>]`` — the date of the newest daily data for that
        domain. Advances ONLY when genuinely new data arrives → key on this to
        detect "there is new data" (e.g. compare prices vs your last-seen date).
      * ``generated_at`` — refreshed on every producer run (new data or not) →
        key on this only to detect "a run happened".
      * ``last_run`` — outcome of the most recent run (kinds, ok/failed counts).
    """
    s = summarize(manifest, today, ignore_lag=ignore_lag)
    domains = {
        name: {
            "newest_data": d["newest"],
            "last_write": d["last_write"],
            "entries": d["entries"],
            "rows": d["rows"],
            "lagging": len(d["lagging"]),
        }
        for name, d in s["domains"].items()
    }
    doc = {
        "generated_at": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "schema_version": s["schema_version"],
        # Flat map for dead-simple polling: domain -> newest data date.
        "newest_data": {name: d["newest_data"] for name, d in domains.items()},
        "domains": domains,
    }
    if last_run is not None:
        doc["last_run"] = last_run
    return doc


def write_status_file(last_run: Optional[dict] = None) -> str:
    """Rebuild status.json from the current manifest, atomically. Called at the end
    of a producer run so pollers see a consistent snapshot."""
    doc = build_status_doc(store.load_manifest(), last_run=last_run,
                           ignore_lag=hist_code_names())
    path = status_path()
    tmp = path.with_suffix(".json.tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True))
    os.replace(tmp, path)
    return str(path)


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
