"""Flow decomposition over canonical COT rows, for crowdmon-futures module spec §6.4.

Weekly ΔLong versus ΔShort per market/category, labelled into the four states that look
identical on a price chart and are entirely different setups:

    ΔLong +, ΔShort ~0   new longs           fresh conviction buying
    ΔLong ~0, ΔShort −   short covering      a rally with a FINITE fuel supply
    ΔLong ~0, ΔShort +   new shorts          fresh bearish conviction
    ΔLong −, ΔShort ~0   long liquidation    position exit, not fresh selling

Runs on ingested COT alone: no prices, no contract master, no multiplier. That is what
makes it the right first consumer of the canonical schema: every input is a column the
vintage layer already stores, so a failure here is a schema failure and nothing else.

**Why this lives in cotdata.** It is a READ-side function over cotdata's own canonical
rows: it writes no store domain, adds no manifest entry, and changes no producer/consumer
contract. ADR-0007 narrows cotdata along the axis of instrument domain (COT versus bars),
not derived-versus-raw, and ADR-0008 settles that provenance for COT data stays inside
that boundary. This is the same shape of thing: COT in, COT out, nothing else consulted.
The positioning ENGINE of the module spec (extremity, fragility, cross-market PCA) is a
different matter, because it needs prices, a contract master and configured weights, and
belongs in the crowdmon package where the spec puts it.

See docs/design/crowdmon_futures_cot_module.md §6.4.
"""
from __future__ import annotations

import pandas as pd

from . import config, vintage_ingest

# The grouping key: one series per market/category, walked in report_date order.
SERIES_KEY = ["market_code", "report_type", "combined", "category"]

NEW_LONGS = "new_longs"
SHORT_COVERING = "short_covering"
NEW_SHORTS = "new_shorts"
LONG_LIQUIDATION = "long_liquidation"
QUIET = "quiet"
FLOW_STATES = (NEW_LONGS, SHORT_COVERING, NEW_SHORTS, LONG_LIQUIDATION, QUIET)

OUT_COLUMNS = SERIES_KEY + [
    "report_date", "days_elapsed",
    "long_contracts", "short_contracts", "open_interest",
    "d_long", "d_short", "d_net", "d_oi",
    "state", "oi_corroborates",
]


class FlowError(ValueError):
    """The input is not a clean one-row-per-key-per-date panel."""


def _require_panel(canonical: pd.DataFrame) -> None:
    missing = [c for c in SERIES_KEY + ["report_date", "long_contracts", "short_contracts"]
               if c not in canonical.columns]
    if missing:
        raise FlowError(f"missing columns for flow decomposition: {missing}")
    dup = canonical.duplicated(subset=SERIES_KEY + ["report_date"])
    if dup.any():
        sample = canonical.loc[dup, SERIES_KEY + ["report_date"]].head(3).to_dict("records")
        raise FlowError(
            f"{int(dup.sum())} duplicate (key, report_date) rows, e.g. {sample}. "
            f"A diff over a key with two rows for one week silently compares two VINTAGES "
            f"of the same week and calls the revision a flow. Pass a point-in-time slice "
            f"(vintage_ingest.asof) rather than raw observations.")


def decompose(canonical: pd.DataFrame, *, min_frac_oi: float = 0.0) -> pd.DataFrame:
    """Label each week's positioning change per market/category.

    ``canonical`` is the long-form schema (``vintage_ingest.ALL_COLUMNS``), one row per
    natural key per report date. Raises rather than guessing if a key has two rows for one
    date, because that means two vintages and a diff across them is a revision, not a flow.

    **Classification is by dominant leg**, which is how the spec's "~0" resolves against
    real data where both legs always move a little: whichever of |ΔLong|, |ΔShort| is
    larger names the state, and its sign picks the direction. Exact ties go to the long
    leg, so the result is deterministic. This is parameter-free by default and therefore
    reproducible with nothing to tune.

    ``min_frac_oi`` optionally adds a dead zone: a week where BOTH legs move less than
    that fraction of the prior week's open interest is labelled ``quiet`` instead of being
    forced into a direction by noise. It defaults to 0.0 (no dead zone) deliberately: any
    non-zero value is a judgement, in the same class as the module spec's fragility
    weights, and belongs in a config the caller owns and can run sensitivity over, not
    baked in here as a fake default.

    The first observation of each series has no predecessor and is dropped: there is no
    such thing as its weekly change.
    """
    _require_panel(canonical)
    df = canonical.copy()
    df["report_date"] = pd.to_datetime(df["report_date"])
    df = df.sort_values(SERIES_KEY + ["report_date"], kind="mergesort")

    g = df.groupby(SERIES_KEY, dropna=False, sort=False)
    for src, dst in (("long_contracts", "d_long"), ("short_contracts", "d_short")):
        df[dst] = g[src].diff()
    df["d_oi"] = g["open_interest"].diff() if "open_interest" in df.columns else pd.NA
    df["d_net"] = df["d_long"] - df["d_short"]
    # Elapsed calendar days, not "one week". COT weeks are NOT uniformly seven days apart:
    # holiday shifts move them, and a capture gap or an early-history hole makes a single
    # diff span months. A caller comparing flow magnitudes across weeks must be able to
    # see that, so it is a column rather than an assumption.
    df["days_elapsed"] = g["report_date"].diff().dt.days
    # Prior-week OI is the dead-zone denominator: a threshold has to be knowable BEFORE
    # the week it judges, or it is fitted to the outcome it is classifying.
    prior_oi = g["open_interest"].shift(1) if "open_interest" in df.columns else None

    df = df[df["d_long"].notna() & df["d_short"].notna()].copy()
    df["state"] = _classify(df, prior_oi=None if prior_oi is None else prior_oi.loc[df.index],
                            min_frac_oi=min_frac_oi)
    df["oi_corroborates"] = _corroborate(df)
    return df.reindex(columns=OUT_COLUMNS).reset_index(drop=True)


def _classify(df: pd.DataFrame, *, prior_oi, min_frac_oi: float) -> pd.Series:
    d_long = df["d_long"].astype("float64")
    d_short = df["d_short"].astype("float64")
    mag_l, mag_s = d_long.abs(), d_short.abs()

    long_dominates = mag_l >= mag_s  # ties -> long leg, so the label is deterministic
    state = pd.Series(pd.NA, index=df.index, dtype="object")
    state = state.mask(long_dominates & (d_long > 0), NEW_LONGS)
    state = state.mask(long_dominates & (d_long <= 0), LONG_LIQUIDATION)
    state = state.mask(~long_dominates & (d_short > 0), NEW_SHORTS)
    state = state.mask(~long_dominates & (d_short <= 0), SHORT_COVERING)

    if min_frac_oi and prior_oi is not None:
        dead = prior_oi.astype("float64") * float(min_frac_oi)
        state = state.mask((mag_l <= dead) & (mag_s <= dead), QUIET)
    return state


def _corroborate(df: pd.DataFrame) -> pd.Series:
    """Does market open interest agree with the label?

    Futures are a closed zero-sum system, so contracts only exist because someone opened
    them. Fresh positioning (new longs, new shorts) should therefore coincide with RISING
    open interest, and exits (short covering, long liquidation) with falling open interest.
    When it does not, the label is describing a transfer of an existing position between
    categories rather than new or closed risk, which is a materially different event.

    Note the asymmetry this cannot escape: ``open_interest`` in the canonical schema is
    the MARKET total, repeated on every category row, because that is what the CFTC file
    reports. So this corroborates a per-category label against a market-level quantity. It
    is a real check and not a proof, which is why it is a separate column rather than
    something folded into the state.
    """
    if "d_oi" not in df.columns:
        return pd.Series(pd.NA, index=df.index, dtype="object")
    d_oi = pd.to_numeric(df["d_oi"], errors="coerce")
    opening = df["state"].isin([NEW_LONGS, NEW_SHORTS])
    closing = df["state"].isin([SHORT_COVERING, LONG_LIQUIDATION])
    out = pd.Series(pd.NA, index=df.index, dtype="object")
    out = out.mask(opening & d_oi.notna(), d_oi > 0)
    out = out.mask(closing & d_oi.notna(), d_oi < 0)
    return out


# ── Schema smoke test ───────────────────────────────────────────────────────
def zero_sum_check(canonical: pd.DataFrame) -> pd.DataFrame:
    """Per (report_date, market): do the category rows reconcile against open interest?

    The strongest check the canonical schema admits, and it needs no external data. In a
    futures market every long is somebody's short, so summing ``long_contracts`` across
    every category must equal the same sum of ``short_contracts``. If the category mapping
    dropped, duplicated or misrouted a column, that identity breaks immediately.

    ``oi_gap`` is what the sides fall short of open interest by. It is expected to be
    NON-ZERO and EQUAL on both sides for the Legacy report as stored today: the
    non-commercial SPREADING column is not in ``providers/cftc.py``'s ``TARGET_COLS``, so
    it never reaches the canonical rows. Spreading is a matched long and short held by one
    trader, which is exactly why it cancels out of the long-versus-short identity while
    still counting toward open interest. So ``long_total == short_total`` is the assertion,
    and ``oi_gap`` is a measurement to read, not a failure, until spreading is captured,
    at which point it should go to zero.

    Returns one row per (report_date, market_code, report_type, combined) with
    ``long_total``, ``short_total``, ``open_interest``, ``oi_gap`` and ``balanced``.
    """
    keys = ["report_date", "market_code", "report_type", "combined"]
    missing = [c for c in keys + ["long_contracts", "short_contracts"]
               if c not in canonical.columns]
    if missing:
        raise FlowError(f"missing columns for zero-sum check: {missing}")
    g = canonical.groupby(keys, dropna=False, sort=False)
    out = g.agg(long_total=("long_contracts", "sum"),
                short_total=("short_contracts", "sum"),
                open_interest=("open_interest", "max")).reset_index()
    out["balanced"] = out["long_total"] == out["short_total"]
    out["oi_gap"] = out["open_interest"] - out["long_total"]
    return out


# ── Sources ─────────────────────────────────────────────────────────────────
def from_vintage(*, as_of=None, market_code=None, report_type="legacy") -> pd.DataFrame:
    """Canonical rows out of the vintage store, as a point-in-time slice.

    This is the correct source: ``asof`` returns exactly one row per natural key, so the
    diff below can never straddle two vintages of one week. With no ``as_of`` it means
    "everything known now", which is still a consistent single vintage per key.
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
