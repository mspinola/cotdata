"""COT vintage ingest — change-only bitemporal observations + field-level revisions.

Commit 2 of the vintage subsystem. Sits on top of the raw snapshots captured by
``vintage.py``: parse → canonical long schema → validate → change-only write → emit
revisions. pandas/pyarrow throughout (no database — crucible-stack ADR-0008).

Canonical natural key: ``(report_date, market_code, report_type, combined, category)``.
A point-in-time read is "greatest ``observed_at`` <= t per natural key" — no valid_to
column. Change-only writes mean storage grows with revisions, not with time.

See docs/design/cot_vintage.md.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
from pathlib import Path

import pandas as pd

from . import vintage

# ── Schema ──────────────────────────────────────────────────────────────────
NATURAL_KEY = ["report_date", "market_code", "report_type", "combined", "category"]

# Ceiling on how much of a POSITION column may be null before ingest refuses (spec §5's
# "null-rate per column within a sane band"). Generous on purpose: it exists to catch a
# format change that coerced a whole column away, not to police the odd blank cell.
# Measured on the real 2026 files, the true null rate on these columns is 0%.
_MAX_NULL_RATE = 0.20

# Fields that constitute the *value* of an observation. row_sha256 is computed over
# these only: not over provenance (observed_at/snapshot_id), not over release_date
# (resolved separately, so a release-date backfill must NOT read as a data revision),
# not over market_name (descriptive).
VALUE_FIELDS = ["long_contracts", "short_contracts", "spread_contracts",
                "trader_count_long", "trader_count_short", "open_interest",
                "cr4_net_long", "cr4_net_short", "cr8_net_long", "cr8_net_short"]

# market_name is descriptive, not a value: it is deliberately OUT of the hash, so a CFTC
# market rename never registers as a revision. Consequence to know before debugging it:
# once a natural key is written, its stored market_name is never updated by a later
# ingest (that row is a change-only skip). The market_code is the identity.
PROVENANCE_FIELDS = ["release_date", "release_date_source", "market_name",
                     "observed_at", "snapshot_id", "row_sha256", "is_tombstone"]

ALL_COLUMNS = NATURAL_KEY + ["market_name"] + VALUE_FIELDS + [
    "release_date", "release_date_source", "observed_at", "snapshot_id",
    "row_sha256", "is_tombstone"]

# Controlled vocabulary per report type (validation §5). "nonreportable" is common
# to all four; the rest come from each report's reporting categories.
#
# The vocabulary is deliberately checked PER REPORT TYPE and never globally. Supplemental
# reuses "commercial" and "noncommercial", and those labels do NOT mean what they mean
# under Legacy: they are net of index traders, and the whole report is futures-and-options
# combined where Legacy here is futures-only. Nothing can silently merge the two, because
# both ``report_type`` and ``combined`` are in the natural key, but a consumer summing
# across report types would still be wrong, so keep the report type attached.
#
# Reusing the labels rather than minting "non_commercial"/"non_reportable" spellings is a
# deliberate choice against the source handoff: an alternative spelling would have made
# ``category == "nonreportable"`` silently miss every Supplemental row while leaving the
# genuinely confusable label, "commercial", identical anyway. Consistency loses nothing
# the natural key was not already carrying.
CATEGORIES = {
    "legacy": {"commercial", "noncommercial", "nonreportable"},
    "disaggregated": {"producer_merchant", "swap", "managed_money",
                      "other_reportable", "nonreportable"},
    "tff": {"dealer", "asset_manager", "leveraged", "other_reportable", "nonreportable"},
    "supplemental": {"commercial", "noncommercial", "index_trader", "nonreportable"},
}


# ── Paths ───────────────────────────────────────────────────────────────────
def _obs_dir() -> Path:
    return vintage.vintage_root() / "observations"


def _rev_dir() -> Path:
    return vintage.vintage_root() / "revisions"


def _obs_path(report_year: int) -> Path:
    return _obs_dir() / f"report_year={report_year}" / "observations.parquet"


def _rev_path(detected_year: int) -> Path:
    return _rev_dir() / f"detected_year={detected_year}" / "revisions.parquet"


# ── Hashing ─────────────────────────────────────────────────────────────────
def _naive_utc(ts) -> pd.Timestamp:
    """Normalise a timestamp to tz-naive UTC — the repo stores tz-naive datetimes
    throughout (Report_Date, price index), so vintage timestamps match that convention
    and never trip tz-aware/tz-naive arithmetic."""
    t = pd.Timestamp(ts)
    if t.tzinfo is not None:
        t = t.tz_convert("UTC").tz_localize(None)
    return t


def _norm(v) -> str:
    """Canonical string form of one value, for hashing and field comparison.

    Deliberately independent of any third-party scalar formatting: numpy/pandas scalars
    are unwrapped to Python natives via ``.item()`` and formatted explicitly. The hash is
    a PERMANENT artifact — if it moved with a numpy/pandas formatting change (numpy 2.0
    already changed scalar repr once) every stored row would appear revised at once, a
    silent mass revision event across every market. Non-integer floats only occur in the
    cr4/cr8 ratios, which is exactly where that would bite.
    """
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass  # non-scalar or unsupported type → fall through to explicit formatting
    if hasattr(v, "item"):
        try:
            v = v.item()  # np.int64/np.float64/np.bool_ → int/float/bool
        except (AttributeError, ValueError):
            pass
    if isinstance(v, bool):  # must precede int: bool is a subclass of int
        return "1" if v else "0"
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else format(v, ".10g")
    if isinstance(v, int):
        return str(v)
    return str(v)


def row_sha256(row: dict) -> str:
    payload = "|".join(f"{f}={_norm(row.get(f))}" for f in VALUE_FIELDS)
    return hashlib.sha256(payload.encode()).hexdigest()


# ── Canonicalisation from the existing wide parsers ─────────────────────────
def canonicalize_legacy(wide: pd.DataFrame, *, combined: bool = False) -> pd.DataFrame:
    """Melt a Legacy wide frame (the shape ``providers/cftc.py`` emits, Report_Date
    index) into canonical long rows: three category rows per (report_date, market)."""
    rows = []
    for report_date, r in wide.iterrows():
        market_code = str(r["CFTC_Contract_Market_Code"])
        market_name = r.get("Market_and_Exchange_Names")
        oi = r.get("Open_Interest_All")
        cats = {
            "commercial": ("Comm_Positions_Long_All", "Comm_Positions_Short_All",
                           "Traders_Comm_Long_All", "Traders_Comm_Short_All"),
            "noncommercial": ("NonComm_Positions_Long_All", "NonComm_Positions_Short_All",
                              "Traders_NonComm_Long_All", "Traders_NonComm_Short_All"),
            "nonreportable": ("NonRept_Positions_Long_All", "NonRept_Positions_Short_All",
                              None, None),
        }
        for category, (lc, sc, tl, ts) in cats.items():
            rows.append({
                "report_date": pd.Timestamp(report_date).normalize(),
                "market_code": market_code,
                "market_name": market_name,
                "report_type": "legacy",
                "combined": combined,
                "category": category,
                "long_contracts": r.get(lc),
                "short_contracts": r.get(sc),
                "spread_contracts": pd.NA,
                "trader_count_long": r.get(tl) if tl else pd.NA,
                "trader_count_short": r.get(ts) if ts else pd.NA,
                "open_interest": oi,
                "cr4_net_long": pd.NA, "cr4_net_short": pd.NA,
                "cr8_net_long": pd.NA, "cr8_net_short": pd.NA,
            })
    out = pd.DataFrame(rows)
    # Coerce exactly as the disagg/TFF path does. Without this the null-rate band in
    # validate() is VACUOUS for Legacy (found in review): a value arriving as "200,000"
    # after a CFTC format change stays an object column, passes every check, and hashes
    # differently from the numeric form, which is precisely the fabricated-revision failure
    # the band exists to prevent, on the one report type where it could not see it.
    #
    # Confirmed not to move any stored hash: across all 95 markets and 40 years the real
    # values are already int64, so to_numeric is an identity and row_sha256 is unchanged.
    # _norm normalises int/float/numpy alike, so the dtype could not have mattered anyway.
    for f in VALUE_FIELDS:
        if f in out.columns:
            out[f] = pd.to_numeric(out[f], errors="coerce")
    return out


# ── Disaggregated / TFF canonicalisation ────────────────────────────────────
# Both reports are far richer than Legacy, and the canonical schema was designed for them
# rather than for Legacy. Three columns that are permanently null on Legacy are populated
# here: per-category SPREADING, per-category TRADER COUNTS, and the CR4/CR8 concentration
# ratios. Everything the module spec's positioning engine actually keys on (Managed Money,
# Leveraged Funds) lives only in these two reports.
#
# Each entry is (long, short, spread, traders_long, traders_short); None means the report
# genuinely does not publish that field for that category, not that it is missing.
# Producer/Merchant and Non-Reportable have no spreading column by design, and
# Non-Reportable has no trader counts because it is defined as everyone below the
# reporting threshold.
_DISAGG_CATEGORIES = {
    "producer_merchant": ("Prod_Merc_Positions_Long_All", "Prod_Merc_Positions_Short_All",
                          None, "Traders_Prod_Merc_Long_All", "Traders_Prod_Merc_Short_All"),
    # NOTE the double underscore in three of these. It is a typo in CFTC's own header row,
    # present on Short and Spread but NOT on Long, and it has been there for years. Both
    # spellings are accepted below so that the day they fix it is not the day this breaks.
    "swap": ("Swap_Positions_Long_All", "Swap__Positions_Short_All",
             "Swap__Positions_Spread_All", "Traders_Swap_Long_All", "Traders_Swap_Short_All"),
    "managed_money": ("M_Money_Positions_Long_All", "M_Money_Positions_Short_All",
                      "M_Money_Positions_Spread_All", "Traders_M_Money_Long_All",
                      "Traders_M_Money_Short_All"),
    "other_reportable": ("Other_Rept_Positions_Long_All", "Other_Rept_Positions_Short_All",
                         "Other_Rept_Positions_Spread_All", "Traders_Other_Rept_Long_All",
                         "Traders_Other_Rept_Short_All"),
    "nonreportable": ("NonRept_Positions_Long_All", "NonRept_Positions_Short_All",
                      None, None, None),
}

_TFF_CATEGORIES = {
    "dealer": ("Dealer_Positions_Long_All", "Dealer_Positions_Short_All",
               "Dealer_Positions_Spread_All", "Traders_Dealer_Long_All",
               "Traders_Dealer_Short_All"),
    "asset_manager": ("Asset_Mgr_Positions_Long_All", "Asset_Mgr_Positions_Short_All",
                      "Asset_Mgr_Positions_Spread_All", "Traders_Asset_Mgr_Long_All",
                      "Traders_Asset_Mgr_Short_All"),
    "leveraged": ("Lev_Money_Positions_Long_All", "Lev_Money_Positions_Short_All",
                  "Lev_Money_Positions_Spread_All", "Traders_Lev_Money_Long_All",
                  "Traders_Lev_Money_Short_All"),
    "other_reportable": ("Other_Rept_Positions_Long_All", "Other_Rept_Positions_Short_All",
                         "Other_Rept_Positions_Spread_All", "Traders_Other_Rept_Long_All",
                         "Traders_Other_Rept_Short_All"),
    "nonreportable": ("NonRept_Positions_Long_All", "NonRept_Positions_Short_All",
                      None, None, None),
}

# Concentration ratios are per MARKET, not per category, so they repeat on every category
# row exactly as open_interest does. Only the NET ratios are in the canonical schema; the
# gross ones (Conc_Gross_LE_*) are published too but have no column to land in.
_CONCENTRATION = {
    "cr4_net_long": "Conc_Net_LE_4_TDR_Long_All",
    "cr4_net_short": "Conc_Net_LE_4_TDR_Short_All",
    "cr8_net_long": "Conc_Net_LE_8_TDR_Long_All",
    "cr8_net_short": "Conc_Net_LE_8_TDR_Short_All",
}

# Supplemental (Commodity Index Trader). Same tuple shape as the two above:
# (long, short, spread, traders_long, traders_short).
#
# Two things the other reports do not do. First, every position column except
# Non-Reportable carries a ``_NoCIT`` suffix, because the index-trader book has been
# carved OUT of it: this report's "commercial" is commercial-minus-index-traders, not
# Legacy's commercial. Second, the spelling is CFTC's own and carries two long-standing
# typos, "Postions" (missing the i) on the spread column and "NComm" rather than
# "NonComm" on the position columns while the Pct_/Traders_ columns use "NonComm". Both
# spellings are handled below rather than normalised, so the day CFTC fixes them is not
# the day this breaks: ``_resolve`` already tries the underscore variants, and the
# NComm/NonComm pair is listed explicitly.
#
# Non-Reportable has no ``_NoCIT`` variant because index traders are reportable by
# definition, so nothing is carved out of it. Verified: its values match the Legacy
# COMBINED file exactly on 390/390 2026 market-weeks.
_SUPPLEMENTAL_CATEGORIES = {
    "noncommercial": ("NComm_Positions_Long_All_NoCIT", "NComm_Positions_Short_All_NoCIT",
                      "NComm_Postions_Spread_All_NoCIT",
                      "Traders_NonComm_Long_All_NoCIT", "Traders_NonComm_Short_All_NoCIT"),
    "commercial": ("Comm_Positions_Long_All_NoCIT", "Comm_Positions_Short_All_NoCIT",
                   None, "Traders_Comm_Long_All_NoCIT", "Traders_Comm_Short_All_NoCIT"),
    "index_trader": ("CIT_Positions_Long_All", "CIT_Positions_Short_All",
                     None, "Traders_CIT_Long_All", "Traders_CIT_Short_All"),
    "nonreportable": ("NonRept_Positions_Long_All", "NonRept_Positions_Short_All",
                      None, None, None),
}

_REPORT_DATE_COL = "Report_Date_as_MM_DD_YYYY"


# CFTC's header spellings that have varied, or are outright typos, across the four
# reports. Each is a substitution tried on a missing column name before giving up.
# "Postions" and "Spead" are typos in CFTC's own header row; "NComm" vs "NonComm" is an
# inconsistency WITHIN the Supplemental file, whose position columns say NComm while its
# Pct_/Traders_ columns say NonComm.
_HEADER_VARIANTS = (
    ("__", "_"), ("_Positions", "__Positions"),
    ("Positions", "Postions"), ("Postions", "Positions"),
    ("Spread", "Spead"), ("Spead", "Spread"),
    ("NonComm", "NComm"), ("NComm", "NonComm"),
)

# How many substitutions may COMPOSE. One is not enough for the column that needs it
# most: ``NComm_Postions_Spread_All_NoCIT`` carries two defects at once, so the realistic
# CFTC cleanup (fix the typo and normalise the prefix in one pass, giving
# ``NonComm_Positions_Spread_All_NoCIT``) is exactly the case a single-substitution
# search cannot reach. Raising there would be safe but would still be an outage on a
# Friday release. Bounded rather than unbounded because the substitutions are not
# confluent — ``_Positions``/``__Positions`` and ``__``/``_`` invert each other — so an
# unbounded closure would loop.
_MAX_HEADER_SUBSTITUTIONS = 3


def _header_candidates(name: str) -> list[str]:
    """Every spelling of ``name`` reachable in at most _MAX_HEADER_SUBSTITUTIONS steps.

    Breadth-first so the fewest-edits spellings are tried first, which matters because
    two candidates could in principle both exist in one file. Deterministic: the variant
    tuple's order fixes the enumeration order.

    VERIFIED not to collide: across every column name the four canonicalisers ask for,
    no candidate of one target is a candidate of another, and no candidate resolves to a
    real column of a DIFFERENT field in the shipped 2026 Legacy, Disaggregated, TFF and
    Supplemental headers. Pinned by test_header_variants_cannot_resolve_to_another_field.
    """
    seen = {name}
    frontier = [name]
    out = []
    for _ in range(_MAX_HEADER_SUBSTITUTIONS):
        nxt = []
        for cur in frontier:
            for old, new in _HEADER_VARIANTS:
                alt = cur.replace(old, new)
                if alt not in seen:
                    seen.add(alt)
                    out.append(alt)
                    nxt.append(alt)
        frontier = nxt
    return out


def _resolve(wide: pd.DataFrame, name: str | None) -> pd.Series | None:
    """Look a column up, tolerating CFTC's known header spelling variants.

    Raises rather than silently producing a null column. A canonicaliser that quietly
    returns nulls for Managed Money because a header was renamed would write those nulls
    as OBSERVATIONS, and the next real value would then be recorded as a revision. A
    renamed column must fail loudly at ingest, not decay into fake revision history.

    A spelling variant is NOT a rename. The variants are a closed, enumerated list of
    spellings CFTC has actually shipped for the same field; anything outside it still
    raises, which is what keeps this a tolerance rather than a fuzzy match.
    """
    if name is None:
        return None
    if name in wide.columns:
        return wide[name]
    for alt in _header_candidates(name):
        if alt in wide.columns:
            return wide[alt]
    raise ValidationError(
        f"column {name!r} not found (nor a known spelling variant). CFTC changed a "
        f"header: map it explicitly rather than letting this category ingest as nulls, "
        f"which would be recorded as real observations and then as revisions when it "
        f"came back.")


def _combined_flag(wide: pd.DataFrame, override: bool | None) -> bool:
    """Read ``combined`` from the file instead of hardcoding it.

    Disagg and TFF both carry a ``FutOnly_or_Combined`` column stating which series the
    file is. Only futures-only files are fetched today, so this is constant-``FutOnly``,
    but reading it means that adding the combined files later is purely a fetch-list
    change: the canonicaliser is already correct, and the two series can never be silently
    merged into one time series because ``combined`` is in the natural key.
    """
    if override is not None:
        return override
    if "FutOnly_or_Combined" not in wide.columns:
        return False
    vals = {str(v).strip().lower() for v in wide["FutOnly_or_Combined"].dropna().unique()}
    if not vals:
        return False
    if len(vals) > 1:
        raise ValidationError(
            f"file mixes futures-only and combined rows ({sorted(vals)}). These are "
            f"different series and must not share a time series (module spec §3).")
    return vals.pop() == "combined"


def _canonicalize(wide: pd.DataFrame, *, report_type: str, categories: dict,
                  combined: bool | None) -> pd.DataFrame:
    """Melt a wide Disagg/TFF frame into canonical long rows, one per category.

    Vectorised per category rather than per row: these files are 190 columns wide and a
    per-row loop over a cold-start backfill is minutes of pure overhead.
    ``canonicalize_legacy`` is deliberately NOT routed through here. Its output feeds
    ``row_sha256``, which is a permanent artifact, and rewriting the code path that
    produces already-stored hashes to save a little duplication would risk registering
    every stored row as revised at once.
    """
    if _REPORT_DATE_COL in wide.columns:
        report_date = pd.to_datetime(wide[_REPORT_DATE_COL])
    else:
        report_date = pd.to_datetime(pd.Series(wide.index, index=wide.index))
    report_date = report_date.dt.normalize()
    is_combined = _combined_flag(wide, combined)

    base = {
        "report_date": report_date.to_numpy(),
        "market_code": wide["CFTC_Contract_Market_Code"].astype(str).to_numpy(),
        "market_name": wide.get("Market_and_Exchange_Names"),
        "report_type": report_type,
        "combined": is_combined,
        "open_interest": _resolve(wide, "Open_Interest_All").to_numpy(),
    }
    for field, col in _CONCENTRATION.items():
        base[field] = wide[col].to_numpy() if col in wide.columns else pd.NA

    frames = []
    for category, (lc, sc, sp, tl, ts) in categories.items():
        part = pd.DataFrame(base)
        part["category"] = category
        part["long_contracts"] = _resolve(wide, lc).to_numpy()
        part["short_contracts"] = _resolve(wide, sc).to_numpy()
        for field, col in (("spread_contracts", sp), ("trader_count_long", tl),
                           ("trader_count_short", ts)):
            s = _resolve(wide, col)
            part[field] = pd.NA if s is None else s.to_numpy()
        frames.append(part)
    out = pd.concat(frames, ignore_index=True)

    # Trader-count columns arrive as STRINGS, because CFTC writes "." for a suppressed
    # count (published counts are withheld where too few traders would be identifiable).
    # Measured on the 2026 Disaggregated file: 3,578 of 7,847 Managed Money long counts
    # are suppressed, so this is a routine state and not an error. Coercing to null is the
    # correct reading of ".", and it must happen HERE rather than being left to parquet,
    # which refuses the mixed column outright.
    #
    # Coercing every value field, not just the trader counts, is deliberate: the whole
    # point is that a suppression marker or a stray pad must never reach row_sha256 as the
    # literal string, because a later numeric value for the same key would then be
    # recorded as a revision that never happened.
    for f in VALUE_FIELDS:
        if f in out.columns:
            out[f] = pd.to_numeric(out[f], errors="coerce")
    return out.reindex(columns=[c for c in ALL_COLUMNS if c in out.columns])


def canonicalize_disagg(wide: pd.DataFrame, *, combined: bool | None = None) -> pd.DataFrame:
    """Disaggregated futures report to canonical long rows (five categories per market)."""
    return _canonicalize(wide, report_type="disaggregated",
                         categories=_DISAGG_CATEGORIES, combined=combined)


def canonicalize_tff(wide: pd.DataFrame, *, combined: bool | None = None) -> pd.DataFrame:
    """Traders in Financial Futures report to canonical long rows."""
    return _canonicalize(wide, report_type="tff",
                         categories=_TFF_CATEGORIES, combined=combined)


def canonicalize_supplemental(wide: pd.DataFrame) -> pd.DataFrame:
    """Supplemental (Commodity Index Trader) report to canonical long rows.

    ``combined`` is ASSERTED True rather than read, and takes no override. There is no
    futures-only Supplemental to select between, so an override could only ever be wrong,
    and every other path into this schema decides ``combined`` from the file. Making the
    one report that cannot say so in its own bytes look like the others would put a
    guessed value into the natural key.

    Measured rather than assumed (docs/analysis/2026-08-03-cit-supplemental-measurements.md):
    this report's ``Open_Interest_All`` matches the Legacy futures-and-options-combined
    file on 390 of 390 2026 market-weeks and the Legacy futures-only file on 0 of 390.
    ``NonRept_Positions_Long_All`` matches combined on the same 390.

    The file carries no ``FutOnly_or_Combined`` column today. If CFTC ever adds one, this
    raises unless it agrees, so the assertion cannot rot into a stale constant.
    """
    if "FutOnly_or_Combined" in wide.columns:
        claimed = {str(v).strip().lower()
                   for v in wide["FutOnly_or_Combined"].dropna().unique()}
        if claimed - {"combined"}:
            raise ValidationError(
                f"the Supplemental file now carries FutOnly_or_Combined={sorted(claimed)}, "
                f"but this report has only ever been futures-and-options combined. Either "
                f"CFTC began publishing a futures-only variant, in which case the fetch "
                f"list and this assertion both need updating, or the wrong file was "
                f"passed to canonicalize_supplemental.")
    return _canonicalize(wide, report_type="supplemental",
                         categories=_SUPPLEMENTAL_CATEGORIES, combined=True)


# ── Validation (§5) ─────────────────────────────────────────────────────────
class ValidationError(ValueError):
    pass


class ConsistencyError(RuntimeError):
    """The change-hash and the field-level diff disagreed.

    These are two different comparison paths: the hash compares a STORED row_sha256
    against a freshly computed one, while the field loop compares parquet-read values
    against fresh ones. If they ever disagree we would write an observation row with no
    corresponding revision rows — a revision that silently lost its detail. Raising turns
    any future dtype/round-trip drift into a loud failure instead of a data-quality
    mystery discovered months later.
    """


def validate(canonical: pd.DataFrame) -> list[str]:
    """Fail loudly (raise) on structural problems; return a list of soft warnings.

    Raises rather than partially ingesting on: missing natural-key columns, null
    natural-key values, or categories outside the controlled vocabulary. Warns (does not
    fail) when a SIDE total breaches open interest.

    **The side total is the invariant, not the per-row sum.** An earlier version compared
    one category's ``long + short + spread`` against total open interest, which is not a
    real bound: the two sides of a market are counted separately, so a category holding
    80k long and 294k short in a 383k-OI market sums to 374k without anything being wrong.
    Measured against the real store it fired on 811 of 5,778 gold rows (14%), pure noise,
    and a soft warning that cries wolf at that rate is one nobody reads.

    What actually must hold is per side, summed across every category: every long
    contract in the market belongs to exactly one category, so ``Σ long <= OI`` and
    ``Σ short <= OI``. Verified against 1,926 weeks of gold: the two side totals matched
    each other on every single week. (They fall SHORT of OI by a constant, equal amount on
    both sides, which is the non-commercial spreading column that ``providers/cftc.py``
    does not capture (see ``vintage_flow.zero_sum_check``). That gap is expected and is
    not warned about.)
    """
    missing = [c for c in NATURAL_KEY if c not in canonical.columns]
    if missing:
        raise ValidationError(f"missing natural-key columns: {missing}")
    if canonical.empty:
        raise ValidationError("empty canonical frame — refusing to ingest nothing")
    for c in NATURAL_KEY:
        if canonical[c].isna().any():
            raise ValidationError(f"null values in natural-key column {c!r}")
    for rt, grp in canonical.groupby("report_type"):
        vocab = CATEGORIES.get(rt)
        if vocab is None:
            raise ValidationError(f"unknown report_type {rt!r}")
        bad = set(grp["category"]) - vocab
        if bad:
            raise ValidationError(f"categories {bad} outside vocabulary for {rt!r}")

    # Duplicate natural keys within ONE frame. The read side already refuses these
    # (crowdmon.futures.io.require_one_row_per_key, which is where the check went when
    # vintage_flow.decompose was removed); the write side must too, and for a worse reason.
    # Ingesting both writes two rows sharing an identical (observed_at, snapshot_id), which
    # exhausts _latest_by_key's tie-break and leaves the winner decided by append order.
    # Both also diff against the same prior row, so revisions/ gains two contradictory
    # entries for one detection. Raising is the only safe answer: there is no principled
    # way to pick which of two rows claiming the same key is the real one.
    dup = canonical.duplicated(subset=NATURAL_KEY, keep=False)
    if dup.any():
        sample = canonical.loc[dup, NATURAL_KEY].head(3).to_dict("records")
        raise ValidationError(
            f"{int(dup.sum())} rows share a natural key within one frame, e.g. {sample}. "
            f"Two rows for one key have no defined ordering, so the stored 'latest' would "
            f"be whichever happened to be appended last.")

    warnings = []

    # NULL-RATE BAND (spec §5). The canonicalisers coerce every value field with
    # errors="coerce", which is correct for CFTC's "." suppression marker but is exactly
    # the mechanism that would silently swallow a CHANGED VALUE FORMAT in a column whose
    # name never moved (thousands separators appearing, a unit suffix, a footnote glyph).
    # _resolve catches a renamed column; nothing caught a renamed *format*, and the
    # consequence is worse than a crash: the nulls get written as real observations, and
    # the next genuine value is then recorded as a revision that never happened. Raising
    # on a mass-null column turns that into a loud failure at the point of ingest.
    # Checked PER CATEGORY, not over the whole frame. Each canonical category is melted
    # from its own source column, so one broken column is only 1/n of the rows: with five
    # disaggregated categories a wholly-coerced Managed Money column is a 20% frame-wide
    # null rate, which any band loose enough to be safe would wave straight through. Per
    # category the same failure is 100%, which is unmissable.
    fields = [f for f in ("long_contracts", "short_contracts", "open_interest")
              if f in canonical.columns]
    for (rt, cat), grp in canonical.groupby(["report_type", "category"],
                                            dropna=False, sort=False):
        for f in fields:
            null_rate = grp[f].isna().mean()
            if null_rate > _MAX_NULL_RATE:
                raise ValidationError(
                    f"{null_rate:.0%} of {f!r} is null for {rt}/{cat}, above the "
                    f"{_MAX_NULL_RATE:.0%} band. A position column is never mostly blank "
                    f"in a real CFTC file, so this is a parse or format change, not data. "
                    f"Refusing to write nulls as observations: they would be recorded as "
                    f"revisions when the values return. (Trader counts are excluded, "
                    f"since CFTC genuinely suppresses roughly half of them.)")

    if "open_interest" not in canonical.columns:
        return warnings
    keys = ["report_date", "market_code", "report_type", "combined"]
    for key, grp in canonical.groupby(keys, dropna=False, sort=False):
        oi = grp["open_interest"].max()
        if pd.isna(oi):
            continue
        tol = rounding_tolerance(len(grp))
        for side in ("long_contracts", "short_contracts"):
            total = pd.to_numeric(grp.get(side), errors="coerce").sum()
            spread = pd.to_numeric(grp.get("spread_contracts"), errors="coerce").sum()
            if total + spread > oi + tol:
                report_date, market_code = key[0], key[1]
                warnings.append(
                    f"{pd.Timestamp(report_date).date()} {market_code}: "
                    f"{side} summed across categories ({total + spread:.0f}) > OI ({oi}) "
                    f"by more than the {tol}-contract rounding tolerance")
    return warnings


def rounding_tolerance(n_categories: int) -> int:
    """Contracts by which the category totals may miss open interest without it meaning
    anything.

    Derived from the mechanism, not fitted to the residual. CFTC publishes a handful of
    **Consolidated** contracts (market codes carrying a ``+`` suffix: S&P 500, NASDAQ-100
    and DJIA Consolidated) which aggregate several contract sizes onto one unit, so each
    category figure is independently rounded. Summing ``n`` independently rounded values
    admits at most ``n`` contracts of error, which is the bound returned here.

    Measured against the 2026 files, the worst observed breach is well inside it: Legacy 1
    contract (bound 3), TFF 2 (bound 5), Disaggregated 0. Every breach in those three
    reports falls in one of those three Consolidated markets and nowhere else, so the
    tolerance costs no real sensitivity. Without it, 48 off-by-one warnings fire on the
    2026 files alone, which is exactly the cry-wolf rate the previous version of this check
    was corrected for.

    **Supplemental breaks the "only in Consolidated markets" half of that**, and the
    mechanism above is why the bound still holds. It is futures-and-options COMBINED, so
    the published figures are delta-weighted option equivalents rounded to whole
    contracts, and every category is rounded independently — the same n-addends argument,
    applying to every market rather than to three. Measured over all 13,584 market-weeks
    from 2006 to 2026, the derived category total misses open interest by at most 2
    contracts, against a bound of 4, and does so on 45% of rows rather than 0.4%. The
    control is decisive: on the SAME 2026 weeks the Legacy futures-only file is exact on
    99.7% of rows while the Legacy combined file shows the identical +/-1 pattern on 10%.
    Combined reporting is the cause, not the Supplemental's extra category.
    """
    return max(1, int(n_categories))


# ── Observation store I/O ───────────────────────────────────────────────────
def read_observations(report_years=None) -> pd.DataFrame:
    d = _obs_dir()
    if not d.exists():
        return pd.DataFrame(columns=ALL_COLUMNS)
    parts = sorted(d.glob("report_year=*/observations.parquet"))
    if report_years is not None:
        want = {int(y) for y in report_years}
        parts = [p for p in parts if int(p.parent.name.split("=")[1]) in want]
    if not parts:
        return pd.DataFrame(columns=ALL_COLUMNS)
    return pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)


def _latest_by_key(obs: pd.DataFrame) -> pd.DataFrame:
    """Most recent observation per natural key, with a DETERMINISTIC tie-break.

    Ordering is (observed_at, snapshot_id) ascending → take the last per key. When two
    snapshots share an ``observed_at`` (same-second ingests), the lexicographically
    greater ``snapshot_id`` wins rather than whichever row happened to land first in
    file/append order. Capture snapshot_ids lead with a compact retrieved_at timestamp,
    so that ordering also tracks retrieval time. A stable sort keeps it reproducible."""
    if obs.empty:
        return obs
    ordered = obs.sort_values(["observed_at", "snapshot_id"], kind="mergesort")
    # Return the grouped rows DIRECTLY. Round-tripping through obs.loc[idx] would be a
    # duplicate-label hazard: any caller that built `obs` without ignore_index=True has
    # repeated index labels, and .loc on those returns EVERY matching row — silently
    # yielding several rows per natural key, which reads as data corruption rather than
    # an indexing bug.
    return ordered.groupby(NATURAL_KEY, dropna=False, sort=False).tail(1)


class _WriteLock:
    """Advisory single-writer lock over the vintage subtree.

    The observation/revision writes are read-concat-rewrite: atomic per file, but two
    concurrent ingest processes would resolve last-writer-wins and silently drop one
    side's rows. The COT producer is single-writer by design (the same reason the store
    manifests are split per half), so this lock exists to convert that silent data-loss
    mode into a loud error, not to support concurrency.
    """

    def __init__(self, root: Path):
        self.path = root / ".ingest.lock"

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            holder = ""
            try:
                holder = f" (held by pid {self.path.read_text().strip()})"
            except OSError:
                pass
            raise RuntimeError(
                f"another cotdata vintage ingest is writing this store{holder}. "
                f"The vintage writers are single-writer by design. If no such process is "
                f"running, remove the stale lock: {self.path}") from None
        with os.fdopen(fd, "w") as fh:
            fh.write(str(os.getpid()))
        return self

    def __exit__(self, *exc):
        try:
            self.path.unlink()
        except OSError:
            pass
        return False


def _append_parquet(path: Path, new_rows: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = pd.read_parquet(path)
        combined = pd.concat([existing, new_rows], ignore_index=True)
    else:
        combined = new_rows
    tmp = path.with_suffix(".parquet.tmp")
    combined.to_parquet(tmp)
    tmp.replace(path)


# ── Ingest (change-only) ────────────────────────────────────────────────────
def ingest_canonical(canonical: pd.DataFrame, *, snapshot_id: str,
                     observed_at: dt.datetime | None = None,
                     validate_input: bool = True) -> dict:
    """Change-only write of a canonical frame + field-level revision emission.

    Idempotent: re-ingesting an identical frame writes zero observations and zero
    revisions, because every row's ``row_sha256`` matches the latest stored value for
    its natural key. Returns ``{"observations": n, "revisions": n, "warnings": [...]}``.
    """
    warnings = validate(canonical) if validate_input else []
    if observed_at is None:
        observed_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    observed_ts = _naive_utc(observed_at)

    # Diff against what was known AS OF this snapshot's observed_at, not against whatever
    # is newest in the store. Without the filter the comparison is not bitemporal at all:
    # re-ingesting an older snapshot compares it to a LATER value and emits a reversed
    # revision (300 -> 100) plus a re-revision, growing revisions/ without bound on every
    # replay. Found in review, after a first fix corrected only the observed_at stamp.
    #
    # On the forward path this changes nothing, because observed_at is then the newest
    # timestamp in the store and the filter admits every row. On a replay it makes the
    # operation a true no-op: the row the snapshot itself wrote is the latest as of its own
    # observed_at, so the hash matches and nothing is written.
    obs = read_observations()
    if not obs.empty:
        obs = obs[obs["observed_at"] <= observed_ts]
    prior = _latest_by_key(obs)
    prior_by_key = {}
    for _, r in prior.iterrows():
        prior_by_key[tuple(r[k] for k in NATURAL_KEY)] = r

    new_obs: dict[int, list] = {}
    revisions: list[dict] = []
    detected_at = observed_ts

    for _, row in canonical.iterrows():
        rec = row.to_dict()
        rec["row_sha256"] = row_sha256(rec)
        key = tuple(rec[k] for k in NATURAL_KEY)
        prev = prior_by_key.get(key)
        if prev is not None and prev["row_sha256"] == rec["row_sha256"]:
            continue  # unchanged value → change-only skip

        rec["observed_at"] = observed_ts
        rec["snapshot_id"] = snapshot_id
        rec.setdefault("release_date", pd.NaT)
        rec.setdefault("release_date_source", "unknown")
        rec.setdefault("is_tombstone", False)
        ry = pd.Timestamp(rec["report_date"]).year
        new_obs.setdefault(ry, []).append(rec)

        if prev is not None:
            report_date = pd.Timestamp(rec["report_date"])
            age_days = int((detected_at.normalize() - report_date.normalize()).days)
            n_before = len(revisions)
            for f in VALUE_FIELDS:
                old, new = prev.get(f), rec.get(f)
                if _norm(old) == _norm(new):
                    continue
                delta = pct = None
                try:
                    if not pd.isna(old) and not pd.isna(new):
                        delta = float(new) - float(old)
                        pct = (delta / float(old)) if float(old) != 0 else None
                except (TypeError, ValueError):
                    pass
                revisions.append({
                    **{k: rec[k] for k in NATURAL_KEY},
                    "field": f, "old_value": _norm(old), "new_value": _norm(new),
                    "delta": delta, "pct_delta": pct,
                    "old_snapshot_id": prev.get("snapshot_id"),
                    "new_snapshot_id": snapshot_id,
                    "detected_at": detected_at, "age_days": age_days,
                    "revision_id": hashlib.sha256(
                        f"{key}|{f}|{prev.get('snapshot_id')}|{snapshot_id}".encode()
                    ).hexdigest()[:16],
                })
            if len(revisions) == n_before:
                raise ConsistencyError(
                    f"row_sha256 changed for {key} but no VALUE_FIELDS differ "
                    f"(stored {prev['row_sha256'][:12]} vs computed {rec['row_sha256'][:12]}). "
                    f"The hash and the field-diff comparison paths disagree — refusing to "
                    f"write an observation with no revision detail.")

    n_obs = 0
    with _WriteLock(vintage.vintage_root()):
        for ry, recs in new_obs.items():
            frame = pd.DataFrame(recs).reindex(columns=ALL_COLUMNS)
            _append_parquet(_obs_path(ry), frame)
            n_obs += len(recs)

        if revisions:
            by_year: dict[int, list] = {}
            for rv in revisions:
                by_year.setdefault(pd.Timestamp(rv["detected_at"]).year, []).append(rv)
            for dy, recs in by_year.items():
                _append_parquet(_rev_path(dy), pd.DataFrame(recs))

    return {"observations": n_obs, "revisions": len(revisions), "warnings": warnings}


# ── Point-in-time read (§4 / acceptance §4) ─────────────────────────────────
def asof(t, *, report_date=None, market_code=None, report_type=None) -> pd.DataFrame:
    """The dataset as known at timestamp ``t``: for each natural key, the row with the
    greatest ``observed_at`` <= t. Rows first observed after ``t`` are absent."""
    obs = read_observations()
    if obs.empty:
        return obs
    t = _naive_utc(t)
    obs = obs[obs["observed_at"] <= t]
    if report_date is not None:
        obs = obs[obs["report_date"] == pd.Timestamp(report_date).normalize()]
    if market_code is not None:
        obs = obs[obs["market_code"] == str(market_code)]
    if report_type is not None:
        obs = obs[obs["report_type"] == report_type]
    return _latest_by_key(obs).reset_index(drop=True)


# ── Coverage (which markets a report could be scored on at all) ─────────────
# A report's covered market set is NOT a constant, and treating it as one is how a
# consumer silently reports on 12 markets while believing it has 13. The Supplemental is
# the live case: Soybean Meal (026603) entered in 2013 and the other twelve have run
# since 2006, which is why both counts circulate in the wild. Derived from the stored
# observations rather than from a list in source, so it cannot drift from the data.
COVERAGE_COLUMNS = ["report_type", "report_year", "market_code", "market_name",
                    "first_report_date", "last_report_date", "weeks"]


def _coverage_dir() -> Path:
    return vintage.vintage_root() / "coverage"


def coverage_path(report_type: str) -> Path:
    return _coverage_dir() / f"{report_type}.parquet"


def clean_market_name(v) -> str | None:
    """One market name, whitespace-stripped, with every flavour of null mapped to None.

    ``astype(str)`` is NOT good enough and the difference is version-dependent: under
    pandas 2 it turns a missing name into the literal ``"nan"``, and under pandas 3's str
    dtype it leaves the float NaN in place. The first silently wins any lexicographic
    comparison (lowercase sorts above every real CFTC name, which are uppercase); the
    second raises ``TypeError`` comparing str to float. Both were reachable, because
    ``market_name`` is descriptive and ``validate`` does not require it.
    """
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    s = str(v).strip()
    return s or None


def latest_market_name(names, dates) -> str | None:
    """The name carried at the LATEST report date, skipping nulls.

    Deliberately not ``max(names)``. Lexicographic max is not "current" and picks the
    STALE name on four real markets: Cocoa, Cotton, Sugar and Coffee all renamed from
    "... - NEW YORK BOARD OF TRADE" to "... - ICE FUTURES U.S.", and ``'I' < 'N'``, so
    the max is the pre-rename name for every year after the change. Wheat happens to
    come out right under either rule, which is how it went unnoticed.
    """
    pairs = [(d, n) for d, n in zip(dates, map(clean_market_name, names)) if n is not None]
    if not pairs:
        return None
    return max(pairs, key=lambda t: t[0])[1]


def _coverage_from(df: pd.DataFrame, report_type: str) -> pd.DataFrame:
    """Shared aggregation behind both coverage entry points.

    ``df`` carries report_date / market_code / market_name. One implementation because
    the producer derives coverage from the downloaded FILES and the vintage layer derives
    it from the stored OBSERVATIONS, and two copies of the name rule drifted apart once
    already.
    """
    if df.empty:
        return pd.DataFrame(columns=COVERAGE_COLUMNS)
    df = df.copy()
    df["report_date"] = pd.to_datetime(df["report_date"])
    df["report_year"] = df["report_date"].dt.year
    rows = []
    for (year, code), g in df.groupby(["report_year", "market_code"], sort=True):
        rows.append({
            "report_type": report_type,
            "report_year": int(year),
            "market_code": code,
            "market_name": latest_market_name(g["market_name"], g["report_date"]),
            "first_report_date": g["report_date"].min(),
            "last_report_date": g["report_date"].max(),
            "weeks": int(g["report_date"].nunique()),
        })
    return pd.DataFrame(rows, columns=COVERAGE_COLUMNS)


def derive_coverage(report_type: str) -> pd.DataFrame:
    """Which markets appear in the observation store for ``report_type``, per report year.

    Change-only writes do not lose coverage: a market-week's first sighting is always
    written, so every (report_year, market_code) that was ever ingested has at least one
    row here, and ``weeks`` counts DISTINCT report dates so later revisions do not
    inflate it.

    This is the LIVE read. ``read_coverage`` returns whatever the artifact last recorded,
    which is a snapshot with no freshness guarantee: prefer this one when correctness
    matters and the artifact when you want what was published.
    """
    obs = read_observations()
    if obs.empty:
        return pd.DataFrame(columns=COVERAGE_COLUMNS)
    obs = obs[obs["report_type"] == report_type]
    return _coverage_from(obs[["report_date", "market_code", "market_name"]], report_type)


def write_coverage(report_type: str) -> tuple[Path, pd.DataFrame]:
    """Emit the coverage artifact for ``report_type``. Rewritten in full each time,
    because it is a derived view of the observations and never a log.

    Takes the same single-writer lock as ingest. Not for the write, which is atomic, but
    for the READ underneath it: ``read_observations`` concats one parquet per report year
    while ``ingest_canonical`` rewrites them one at a time, so an unlocked derive can see
    a run part-way through and emit a torn coverage set. Failing loudly is the repo's
    answer everywhere else the two writers could interleave.
    """
    with _WriteLock(vintage.vintage_root()):
        cov = derive_coverage(report_type)
        path = coverage_path(report_type)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".parquet.tmp")
        cov.to_parquet(tmp, index=False)
        tmp.replace(path)
    return path, cov


def read_coverage(report_type: str) -> pd.DataFrame:
    p = coverage_path(report_type)
    return pd.read_parquet(p) if p.exists() else pd.DataFrame(columns=COVERAGE_COLUMNS)


def coverage_changes(cov: pd.DataFrame) -> list[dict]:
    """Markets entering or leaving the covered set, year over year.

    The point of the artifact: an entry or exit is a break in every pooled statistic
    computed across the universe, and it is invisible in a per-market read.

    **Only CONSECUTIVE years are compared**, and a gap emits a ``gap`` record instead.
    Coverage is derived from what was INGESTED, not from what CFTC published, so a store
    holding 2006 and 2026 and nothing between would otherwise report Soybean Meal as
    entering in 2026 — an ingest artifact presented as a fact about the market. Refusing
    to answer across a gap is the only honest option: the years that would settle it are
    the ones that are missing.
    """
    if cov.empty:
        return []
    by_year = {int(y): dict(zip(g["market_code"], g["market_name"]))
               for y, g in cov.groupby("report_year", sort=True)}
    years = sorted(by_year)
    out = []
    for prev, cur in zip(years, years[1:]):
        if cur != prev + 1:
            out.append({"report_year": cur, "change": "gap", "market_code": "",
                        "market_name": f"no observations for {prev + 1}-{cur - 1}, so "
                                       f"entries and exits across the gap are unknowable"})
            continue
        for code in sorted(set(by_year[cur]) - set(by_year[prev])):
            out.append({"report_year": cur, "change": "enter", "market_code": code,
                        "market_name": by_year[cur][code]})
        for code in sorted(set(by_year[prev]) - set(by_year[cur])):
            out.append({"report_year": cur, "change": "exit", "market_code": code,
                        "market_name": by_year[prev][code]})
    return out


def read_revisions(detected_years=None) -> pd.DataFrame:
    d = _rev_dir()
    if not d.exists():
        return pd.DataFrame()
    parts = sorted(d.glob("detected_year=*/revisions.parquet"))
    if detected_years is not None:
        want = {int(y) for y in detected_years}
        parts = [p for p in parts if int(p.parent.name.split("=")[1]) in want]
    if not parts:
        return pd.DataFrame()
    return pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
