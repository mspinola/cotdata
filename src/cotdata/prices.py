"""Consumer price API. Reads the store and returns the shape downstream code
expects (the old fetch_daily_ohlc contract). No network, cross-platform."""
from typing import Optional

import numpy as np
import pandas as pd

from . import store

_COLS = ["Open", "High", "Low", "Close", "Volume", "Open Interest"]
_OHLC = ["Open", "High", "Low", "Close"]


def _ratio_adjust(symbol: str) -> pd.DataFrame:
    """Derive a proportional (ratio) back-adjusted OHLC series from the stored
    unadjusted + additive-back-adjusted series. Empty if either is missing.

    WHY. Norgate only publishes ADDITIVE (arithmetic) back-adjustment — the
    ``_CCB`` continuous (see providers/norgate.py). For a low-priced, long-history
    contract like DC (Class III Milk, ~$15–20/cwt over ~29y) the additive
    accumulation of roll gaps drives ~47% of back-adjusted closes ≤ 0 (down to
    −9.83). A close-based stop, an R-multiple, or a % return is meaningless on a
    non-positive series, so CMR cannot use DC's ``backadj`` at all. A ratio-adjusted
    series preserves percentage returns and stays strictly positive.

    This is a consumer-side derivation (no network, no Windows/norgatedata), in the
    same spirit as the reconstructed-volume view: the transform lives in the data
    layer so consumers ask for what they want. It is a pure function of two series
    already in the store, so it needs no producer re-run or schema bump.

    METHOD. The additive series ``B`` and the unadjusted series ``U`` differ by an
    offset ``O = B − U`` that is piecewise-constant and steps only at rolls (each
    roll's step is Norgate's stitched calendar spread; verified on DC — every step
    >$0.0001 lands on a Delivery-Month change). At roll ``r`` the recovered spread,
    measured on the last day the OLD contract is front (day ``r−1``), is
    ``s = O[r−1] − O[r] = F_new(r−1) − F_old(r−1)``; the roll ratio is
    ``k = (U[r−1] + s) / U[r−1] = F_new/F_old``. Each historical segment is scaled
    by the cumulative product of ``k`` for all rolls at/after it, anchoring the
    most-recent segment to 1 (actual prices). Result: identical daily % returns to
    ``U`` within a segment, gap-free true returns across each roll (sign-identical
    to ``B``), strictly positive, anchored to the real current price. O/H/L/C are
    scaled by the per-row segment factor; Volume/Open Interest/Delivery Month and
    the reconstruction columns pass through from the unadjusted frame unchanged.
    """
    U = store.read_prices(symbol, "unadj")
    B = store.read_prices(symbol, "backadj")
    if U.empty or B.empty or "Close" not in U or "Close" not in B:
        return pd.DataFrame()

    U = U.copy()
    U.index = pd.to_datetime(U.index).tz_localize(None).normalize()
    B.index = pd.to_datetime(B.index).tz_localize(None).normalize()
    U = U.sort_index()
    idx = U.index.intersection(B.index.sort_values())
    if len(idx) == 0:
        return pd.DataFrame()
    U = U.loc[idx]
    b_close = B["Close"].reindex(idx)

    # Roll boundaries: prefer the semantic Delivery-Month change (matches
    # roll_dates); fall back to material offset steps when it is absent.
    offset = b_close - U["Close"]
    if "Delivery Month" in U.columns:
        dm = U["Delivery Month"]
        roll = dm.ne(dm.shift()) & dm.shift().notna()
    else:
        roll = offset.diff().abs() > 1e-4
        roll.iloc[0] = False

    seg = roll.cumsum()                    # segment id, increments at each roll
    spread = (-offset.diff()).where(roll, 0.0)   # F_new(r−1) − F_old(r−1)
    u_prev = U["Close"].shift(1)
    ratio = pd.Series(1.0, index=idx)
    ok = roll & (u_prev > 0)
    ratio[ok] = (u_prev[ok] + spread[ok]) / u_prev[ok]

    # Per-segment cumulative factor, anchored so the most recent segment = 1
    # (kept at actual prices). Walk rolls back-to-front, compounding each ratio.
    roll_ratios = ratio[roll].to_numpy()
    n_seg = int(seg.iloc[-1])
    factors = np.ones(n_seg + 1)
    for s in range(n_seg - 1, -1, -1):
        factors[s] = factors[s + 1] * roll_ratios[s]
    factor = pd.Series(factors[seg.to_numpy()], index=idx)

    out = U.copy()
    for c in _OHLC:
        if c in out.columns:
            out[c] = out[c] * factor
    return out


def get_prices(symbol: str, adjustment: str = "backadj",
               start: Optional[str] = None,
               volume: str = "front") -> pd.DataFrame:
    """Daily bars for `symbol`.

    adjustment:
      'backadj' : additive (arithmetic) back-adjustment — settlement close,
                  gap-free rolls, shape-preserving. Preserves absolute daily
                  price *changes*. Default for signals + stops.
      'unadj'   : raw front-month prices (absolute price / point-value sizing).
      'propadj' : proportional (ratio) back-adjustment, DERIVED on read from
                  unadj + backadj (see `_ratio_adjust`). Preserves daily *percent*
                  returns and stays strictly positive — use it for low-priced,
                  long-history contracts (e.g. DC / Class III Milk) where additive
                  back-adjustment accumulates roll gaps below zero and breaks
                  price-based stops and R-multiples.

    volume: which series the `Volume` column carries —
      'front'         : continuous front-month volume as Norgate reports it
                        (default — output is byte-identical to the pre-v2 API).
      'reconstructed' : true market volume (first + second expiring contract,
                        with per-row fall-back to front-month where individual
                        contracts are unavailable). The intent view: the fall-back
                        policy lives here, in the data layer, so consumers ask for
                        what they want instead of re-deriving it. Adds a
                        'Volume_Source' column ('reconstructed' / 'raw') for audit.

    Returns Open/High/Low/Close/Volume/Open Interest indexed by tz-naive Date
    (plus 'Delivery Month' if the producer carried it, plus 'Volume_Source' when
    volume='reconstructed'), or empty if absent.
    """
    if volume not in ("front", "reconstructed"):
        raise ValueError(f"volume must be 'front' or 'reconstructed', got {volume!r}")

    df = _ratio_adjust(symbol) if adjustment == "propadj" else store.read_prices(symbol, adjustment)
    if df.empty:
        return df
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df.index.name = "Date"
    df = df.sort_index()
    if start:
        df = df[df.index >= pd.Timestamp(start)]
    for c in _COLS:
        if c not in df.columns:
            df[c] = float("nan")

    keep = list(_COLS)
    if volume == "reconstructed":
        # Swap in reconstructed volume with a per-row raw fall-back. The producer
        # already writes Volume_Reconstructed==Volume on 'raw' rows, so reading it
        # is fall-back-safe where the column exists; guard for older/partial data
        # (pre-reconstruction parquet, or a stray NaN) by filling from front-month.
        if "Volume_Reconstructed" in df.columns:
            df["Volume"] = df["Volume_Reconstructed"].where(
                df["Volume_Reconstructed"].notna(), df["Volume"])
            df["Volume_Source"] = (
                df["Volume_Source"] if "Volume_Source" in df.columns else "reconstructed")
        else:
            # Store predates reconstruction → everything is front-month.
            df["Volume_Source"] = "raw"
        keep = keep + ["Volume_Source"]

    if "Delivery Month" in df.columns:
        keep = keep + ["Delivery Month"]
    return df[keep].copy()


def roll_dates(symbol: str, adjustment: str = "backadj") -> pd.DatetimeIndex:
    """Exact roll dates = days where the underlying Delivery Month changes.
    Empty if the producer didn't carry 'Delivery Month'."""
    df = get_prices(symbol, adjustment)
    if df.empty or "Delivery Month" not in df.columns:
        return pd.DatetimeIndex([])
    changed = df["Delivery Month"].ne(df["Delivery Month"].shift())
    return df.index[changed.fillna(False)]
