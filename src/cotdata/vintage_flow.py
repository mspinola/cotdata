"""The zero-sum identity over canonical COT rows, and the two ways to source them.

`zero_sum_check` is the strongest check the canonical schema admits and it needs no
external data: in a futures market every long is somebody's short, so a single misrouted,
dropped or duplicated category column breaks the identity on the first week. That is a
statement about cotdata's own parse, which is why it lives here.

**Flow decomposition was here and has been removed.** It was built 2026-07-30 alongside
this check, and `crowdmon.futures.flow` was built independently on 2026-08-01. The two
were carried side by side as "defensible alternatives answering slightly different
questions" until somebody measured them, which is the whole argument for measuring:

    cotdata.vintage_flow.decompose == crowdmon.futures.flow.decompose
                                      at tolerance=1.0, gap rule off

On 27 markets and 135,835 transitions from 2006 to 2026 that held at **100.000000% label
agreement with zero mismatches**, and `d_long`, `d_short` and `d_net` were identical on
every row. Not a similar approach. The same function, with this copy hard-wired to the
corner of the parameter space where nothing is ever `mixed` and no interval is ever
refused. Both take the dominant leg as `argmax(|ΔLong|, |ΔShort|)`, both break exact ties
to the long leg, and both treat a doubly-unmoved week as `quiet` unconditionally.

So this was never a second opinion, it was a less capable copy: it could not decline to
call a genuinely two-sided week, and it differenced across a 294-day absence as though it
were a week. The general implementation stays, in the package the module spec puts the
positioning engine in, and is reached with `from crowdmon.futures import decompose`.

**The dedup could not go the other way.** `crowdmon/tests/test_boundaries.py` forbids
cotdata from importing crowdmon, because a producer that depends on its consumer inverts
the whole dependency direction. The equivalence is therefore asserted from that side, in
`crowdmon/tests/test_flow_equivalence.py`, and the measurement is written up in
`crowdmon/docs/design/amendments-2026-08-02.md` §B29.

What remains here is COT in, COT out, consulting nothing else, which is what ADR-0007 and
ADR-0008 leave inside cotdata's boundary.
"""
from __future__ import annotations

import pandas as pd

from . import config, vintage_ingest


class FlowError(ValueError):
    """The input is not a clean one-row-per-key-per-date panel."""


# ── Schema smoke test ───────────────────────────────────────────────────────
def zero_sum_check(canonical: pd.DataFrame) -> pd.DataFrame:
    """Per (report_date, market): do the category rows reconcile against open interest?

    The strongest check the canonical schema admits, and it needs no external data. In a
    futures market every long is somebody's short, so summing ``long_contracts`` across
    every category must equal the same sum of ``short_contracts``. If the category mapping
    dropped, duplicated or misrouted a column, that identity breaks immediately.

    Spreading is a matched long and short held by one trader, so it is added to BOTH side
    totals: it cancels out of the long-versus-short identity but still counts toward open
    interest. ``oi_gap`` is therefore what the sides fall short of open interest by, and
    what it should be depends on the report:

    | Report | Expected ``balanced`` | Expected ``oi_gap`` | Measured 2026 |
    |---|---|---|---|
    | Legacy | always | **non-zero**, equals the uncaptured non-commercial spreading | 149,412/149,412 balanced, gap never zero |
    | Disaggregated | always | **zero**, spreading is captured per category | 7,847/7,847 balanced, gap 0 everywhere |
    | TFF | to within a contract or two | zero, same reason | 2,463/2,500 exact, worst case 2 contracts |

    The Legacy gap is the known defect: ``NonComm_Positions_Spread_All`` is not in
    ``providers/cftc.py``'s ``TARGET_COLS``, so it never reaches the canonical rows. It is
    a measurement to read, not a failure.

    The TFF residual is CFTC's own rounding, not a mapping error: every one of the 37
    off-by-one-or-two rows falls in the three "Consolidated" equity index markets (S&P 500,
    NASDAQ-100, DJIA), which aggregate several contract sizes into a common unit and so
    involve a division. Treat a TFF imbalance of 1 or 2 contracts in a Consolidated market
    as expected, and anything larger or anywhere else as a real break.

    Returns one row per (report_date, market_code, report_type, combined) with
    ``long_total``, ``short_total``, ``spread_total``, ``open_interest``, ``oi_gap``,
    ``imbalance`` and ``balanced``.
    """
    keys = ["report_date", "market_code", "report_type", "combined"]
    missing = [c for c in keys + ["long_contracts", "short_contracts"]
               if c not in canonical.columns]
    if missing:
        raise FlowError(f"missing columns for zero-sum check: {missing}")
    df = canonical.copy()
    if "spread_contracts" not in df.columns:
        df["spread_contracts"] = pd.NA
    for c in ("long_contracts", "short_contracts", "spread_contracts", "open_interest"):
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    g = df.groupby(keys, dropna=False, sort=False)
    out = g.agg(long_side=("long_contracts", "sum"),
                short_side=("short_contracts", "sum"),
                spread_total=("spread_contracts", "sum"),
                open_interest=("open_interest", "max"),
                n_categories=("long_contracts", "size")).reset_index()
    out["long_total"] = out["long_side"] + out["spread_total"].fillna(0)
    out["short_total"] = out["short_side"] + out["spread_total"].fillna(0)
    out["imbalance"] = out["long_total"] - out["short_total"]
    out["balanced"] = out["imbalance"] == 0
    # Exact balance is the assertion; within_tolerance is what an automated check should
    # use, so the three Consolidated markets do not produce a permanent red light.
    tol = out["n_categories"].map(vintage_ingest.rounding_tolerance)
    out["within_tolerance"] = out["imbalance"].abs() <= tol
    out["oi_gap"] = out["open_interest"] - out["long_total"]
    return out.drop(columns=["long_side", "short_side"])


# ── Sources ─────────────────────────────────────────────────────────────────
def from_vintage(*, as_of=None, market_code=None, report_type="legacy") -> pd.DataFrame:
    """Canonical rows out of the vintage store, as a point-in-time slice.

    This is the correct source: ``asof`` returns exactly one row per natural key, so a
    consumer differencing these rows can never straddle two vintages of one week and call
    a revision a flow. With no ``as_of`` it means "everything known now", which is still a
    consistent single vintage per key.

    That guarantee used to be enforced here, by a `_require_panel` check in `decompose`.
    It moved out with `decompose`; `crowdmon.futures.io.require_one_row_per_key` is the
    same refusal on the consuming side, and this function is what makes it satisfiable.
    """
    t = pd.Timestamp.max if as_of is None else as_of
    return vintage_ingest.asof(t, market_code=market_code, report_type=report_type)


def from_current_store(market_code: str) -> pd.DataFrame:
    """Canonical rows built from the EXISTING current-state parquet for one market.

    Present because the vintage series begins at first capture and is therefore short,
    while the current-state store holds Legacy history back to 1986. That makes this the
    only way to exercise the canonical schema over four decades and 95 markets today.

    **Not point-in-time.** These are current values with revisions already applied, so a
    result computed from here is contaminated by hindsight and must not be used to
    evaluate anything. It is for validating the schema and for looking at history, which
    is why it is a separate function with a separate name rather than a flag.
    """
    from . import store
    wide = store.read_cot_legacy(str(market_code))
    if wide.empty:
        raise FlowError(f"no current-state Legacy table for market_code {market_code!r} "
                        f"(store: {config.cot_legacy_dir()})")
    return vintage_ingest.canonicalize_legacy(wide)
