"""Offside-capitulation evaluation on CFTC Legacy COT data.

Pre-registered by docs/handoffs/2026-08-23-offside-capitulation-evaluation.md.
Read that document before running or interpreting this; the definitions below
are frozen there and this script is their executable form.

The question: when a crowded speculative book is OFFSIDE (price has moved
against a positioning extreme) and then CAPITULATES (an unusually large
one-week unwind of its net, in the adverse direction), what do subsequent
returns do?  Two folk claims disagree on the sign:

  Claim A ("washout"):   capitulation ends the adverse move; forward returns
                         stop continuing against the ex-crowd, or revert.
  Claim B ("stampede"):  capitulation removes the last resistance; the
                         adverse move continues.

Everything is measured per market, backward-looking, with trailing windows.
No claim is made that a reader of the Friday release could have acted; this
is an event study on report-date-aligned data (the FFM test's §3.2 scope
refusal is inherited).

Data: CFTC Legacy futures-only annual zips (same URLs as
src/cotdata/providers/cftc.py) and daily continuous-futures closes from
stooq.com, sampled at each report date.  If no price source is reachable the
script degrades to COT-only descriptives and says so; it never fabricates.

Usage:
  python offside_capitulation_check.py             # full run (needs network)
  python offside_capitulation_check.py --selftest  # synthetic smoke test
  python offside_capitulation_check.py --cot-only  # skip prices deliberately

Deps: pandas numpy requests xlrd
"""

import argparse
import io
import sys
import warnings
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

CACHE = Path(__file__).resolve().parent / "occ_cache"

# Legacy futures-only market codes -> (name, stooq symbol candidates, tried in order)
MARKETS = {
    "088691": ("Gold", ["gc.f"]),
    "084691": ("Silver", ["si.f"]),
    "067651": ("Crude WTI", ["cl.f"]),
    "023651": ("Nat gas", ["ng.f"]),
    "002602": ("Corn", ["c.f", "zc.f"]),
    "099741": ("EUR FX", ["6e.f", "eurusd"]),
    "043602": ("10Y Note", ["zn.f", "ty.f"]),
    "13874A": ("E-mini S&P", ["es.f", "sp.f", "^spx"]),
}

COLS = ["Market_and_Exchange_Names", "Report_Date_as_MM_DD_YYYY",
        "CFTC_Contract_Market_Code", "Open_Interest_All",
        "NonComm_Positions_Long_All", "NonComm_Positions_Short_All"]

# ---- frozen parameters (see handoff §4; do not tune) -----------------------
RANK_WIN = 156          # trailing weeks for NPF percentile / thresholds
RANK_MIN = 104          # minimum observations before ranks are valid
EXT_HI, EXT_LO = 0.80, 0.20   # crowded-long / crowded-short percentile gates
ADV_LOOKBACK = 4        # weeks of adverse move defining "offside"
ADV_SIGMA = 1.0         # adverse move must be >= this many trailing sigmas
CAP_PCTL = 0.90         # |dNPF| must clear this trailing percentile
OFFSIDE_MEMORY = 2      # capitulation must follow offside within this many weeks
COOLDOWN = 8            # weeks between events per market+side
HORIZONS = (1, 4, 13)   # forward horizons, weeks
VOL_WIN = 52            # trailing weeks for weekly-return sigma scaling
BLOCK_WEEKS = 13        # calendar block size for the bootstrap
N_BOOT = 5000
SEED = 20260823

START_YEAR, END_YEAR = 1990, 2026


# ---------------------------------------------------------------- data layer
def cot_year_frame(year, requests):
    if year < 2004:
        url = f"https://www.cftc.gov/files/dea/history/deafut_xls_{year}.zip"
    else:
        url = f"https://www.cftc.gov/files/dea/history/dea_fut_xls_{year}.zip"
    zp = CACHE / url.rsplit("/", 1)[1]
    if not zp.exists():
        r = requests.get(url, timeout=120)
        r.raise_for_status()
        zp.write_bytes(r.content)
    with zipfile.ZipFile(zp) as zf:
        data = zf.open(zf.namelist()[0]).read()
    df = pd.read_excel(io.BytesIO(data), usecols=lambda c: c in COLS)
    df["CFTC_Contract_Market_Code"] = (
        df["CFTC_Contract_Market_Code"].astype(str).str.strip())
    df["Report_Date_as_MM_DD_YYYY"] = pd.to_datetime(df["Report_Date_as_MM_DD_YYYY"])
    return df


def load_cot(requests):
    frames, failures = [], []
    for y in range(START_YEAR, END_YEAR + 1):
        try:
            frames.append(cot_year_frame(y, requests))
        except Exception as e:  # noqa: BLE001 - report, don't die
            failures.append((y, repr(e)))
    if failures:
        print(f"COT downloads failed for {len(failures)} year(s): "
              f"{[y for y, _ in failures]}", file=sys.stderr)
        print(f"  first error: {failures[0][1]}", file=sys.stderr)
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)


def load_prices_stooq(requests):
    """Daily closes per market from stooq; returns dict code -> Series, and a log."""
    out, log = {}, []
    for code, (name, syms) in MARKETS.items():
        got = None
        for sym in syms:
            url = f"https://stooq.com/q/d/l/?s={sym}&i=d"
            try:
                r = requests.get(url, timeout=60)
                r.raise_for_status()
                px = pd.read_csv(io.StringIO(r.text))
                if "Close" not in px.columns or len(px) < 500:
                    raise ValueError(f"unusable payload ({len(px)} rows)")
                px["Date"] = pd.to_datetime(px["Date"])
                got = px.set_index("Date")["Close"].astype(float).sort_index()
                log.append(f"  {name}: stooq {sym}, {len(got)} daily bars "
                           f"{got.index[0].date()} .. {got.index[-1].date()}")
                break
            except Exception as e:  # noqa: BLE001
                log.append(f"  {name}: stooq {sym} FAILED ({e!r})")
        out[code] = got
    return out, log


def load_prices_local():
    """Optional local fallback: scripts/occ_prices_daily.csv with columns
    date,market_code,close - lets an executor supply prices from any store."""
    p = Path(__file__).resolve().parent / "occ_prices_daily.csv"
    if not p.exists():
        return None
    df = pd.read_csv(p, parse_dates=["date"])
    return {code: g.set_index("date")["close"].astype(float).sort_index()
            for code, g in df.groupby("market_code")}


# ------------------------------------------------------------- event builder
def rolling_pct_rank(s, win, minp):
    """Percentile rank of the last value within its trailing window."""
    def _rank(a):
        return (a[:-1] <= a[-1]).mean()
    return s.rolling(win, min_periods=minp).apply(_rank, raw=True)


def build_market_panel(cot_sub, price):
    """Weekly panel for one market: NPF states, event flags, forward outcomes."""
    sub = (cot_sub.sort_values("Report_Date_as_MM_DD_YYYY")
                  .drop_duplicates("Report_Date_as_MM_DD_YYYY")
                  .set_index("Report_Date_as_MM_DD_YYYY"))
    net = (sub["NonComm_Positions_Long_All"]
           - sub["NonComm_Positions_Short_All"]).astype(float)
    oi = sub["Open_Interest_All"].astype(float).replace(0, np.nan)
    npf = (net / oi).dropna()
    idx = npf.index

    # drop report weeks not ~7 days apart (the FFM rule: never difference a gap)
    gap = idx.to_series().diff().dt.days
    ok_gap = (gap >= 6) & (gap <= 8)

    d = pd.DataFrame(index=idx)
    d["npf"] = npf
    d["dnpf"] = npf.diff().where(ok_gap)
    d["side"] = np.sign(npf)

    d["rank"] = rolling_pct_rank(npf, RANK_WIN, RANK_MIN)
    d["ext_long"] = d["rank"] >= EXT_HI
    d["ext_short"] = d["rank"] <= EXT_LO

    cap_thresh = d["dnpf"].abs().rolling(RANK_WIN, min_periods=RANK_MIN)\
                                .quantile(CAP_PCTL)
    big = d["dnpf"].abs() >= cap_thresh
    prev_side = d["side"].shift(1)
    d["cap_long"] = big & (d["dnpf"] < 0) & (prev_side > 0)
    d["cap_short"] = big & (d["dnpf"] > 0) & (prev_side < 0)

    if price is not None:
        # sample the last close on or before each report date
        p = price.reindex(price.index.union(idx)).ffill().reindex(idx)
        lr = np.log(p).diff()
        d["p"] = p
        d["r4"] = np.log(p / p.shift(ADV_LOOKBACK))
        sig4 = d["r4"].rolling(RANK_WIN, min_periods=RANK_MIN).std()
        d["off_long"] = d["ext_long"] & (d["r4"] <= -ADV_SIGMA * sig4)
        d["off_short"] = d["ext_short"] & (d["r4"] >= ADV_SIGMA * sig4)
        sig1 = lr.rolling(VOL_WIN, min_periods=40).std()
        d["u"] = lr / sig1          # sigma-scaled weekly log return
        for h in HORIZONS:
            d[f"fu{h}"] = d["u"].shift(-1).rolling(h).sum().shift(-(h - 1))
    return d


def flag_events(d, side):
    """Offside-capitulation events for one side, with memory + cooldown."""
    off, cap = (d["off_long"], d["cap_long"]) if side == "long" \
        else (d["off_short"], d["cap_short"])
    off_recent = off.shift(1).fillna(False)
    for k in range(2, OFFSIDE_MEMORY + 1):
        off_recent |= off.shift(k).fillna(False)
    raw = cap & off_recent
    # cooldown
    out, last = [], None
    for t, v in raw.items():
        if v and (last is None or (t - last).days > COOLDOWN * 7):
            out.append(t)
            last = t
    ev = pd.Series(False, index=d.index)
    ev.loc[out] = True
    return ev, off_recent, cap


# ---------------------------------------------------------------- statistics
def block_key(ts):
    return (ts.year, (ts.dayofyear - 1) // (BLOCK_WEEKS * 7))


def boot_p(rows, col, rng):
    """Two-sided p for mean(col)=0 via calendar-block bootstrap, recentred
    on zero (the FFM p_null correction; a literal fraction-below test is
    uninformative on a bootstrap centred at the observed statistic)."""
    if len(rows) < 5:
        return (rows[col].mean() if len(rows) else np.nan), np.nan
    obs = rows[col].mean()
    grouped = rows.groupby(rows["t"].map(block_key))[col]
    sums = grouped.sum().to_numpy()
    counts = grouped.count().to_numpy()
    nb = len(sums)
    pick = rng.integers(0, nb, size=(N_BOOT, nb))
    means = sums[pick].sum(axis=1) / np.maximum(counts[pick].sum(axis=1), 1)
    means = means - means.mean()   # recentre on zero
    p = (np.abs(means) >= abs(obs)).mean()
    return obs, p


def summarize(events_df, label, rng):
    print(f"\n### {label}  (n = {len(events_df)})")
    if len(events_df) == 0:
        print("  no events")
        return
    hdr = f"  {'horizon':<9} {'mean u':>8} {'median u':>9} {'p_null':>8} {'n':>5}"
    print(hdr + "\n  " + "-" * (len(hdr) - 2))
    for h in HORIZONS:
        col = f"fu{h}"
        rows = events_df.dropna(subset=[col])
        obs, p = boot_p(rows, col, rng)
        med = rows[col].median() if len(rows) else np.nan
        print(f"  {h:>2}w      {obs:8.3f} {med:9.3f} {p:8.4f} {len(rows):5d}")


# ------------------------------------------------------------------ pipeline
def run(cot, prices, price_log):
    rng = np.random.default_rng(SEED)
    have_prices = prices is not None and any(v is not None for v in prices.values())

    print("=" * 78)
    print("OFFSIDE-CAPITULATION EVALUATION - run report")
    print("=" * 78)
    if price_log:
        print("\nPrice sources:")
        for line in price_log:
            print(line)
    if not have_prices:
        print("\nNO PRICE SOURCE RESOLVED. Degraded COT-only mode: capitulation")
        print("frequency/size only; offside conditioning and outcomes are")
        print("impossible without prices, and are NOT reported. This is a")
        print("blocked run, not a null result.")

    pooled = {("long", g): [] for g in ("event", "off_nocap", "cap_nooff")}
    pooled.update({("short", g): [] for g in ("event", "off_nocap", "cap_nooff")})
    per_market_sign = []

    for code, (name, _) in MARKETS.items():
        cot_sub = cot[cot["CFTC_Contract_Market_Code"] == code]
        if cot_sub.empty:
            print(f"\n{name}: no COT rows, skipped")
            continue
        price = prices.get(code) if have_prices else None
        d = build_market_panel(cot_sub, price)
        ncap = int(d["cap_long"].sum() + d["cap_short"].sum())
        print(f"\n{name}: {len(d)} report weeks "
              f"({d.index[0].date()} .. {d.index[-1].date()}), "
              f"{ncap} capitulation weeks "
              f"({ncap / max(len(d), 1) * 100:.1f}%)")
        if price is None:
            continue

        for side in ("long", "short"):
            # sign convention: positive forward u = CONTINUATION of the
            # adverse move (against the ex-crowd). Crowd long -> adverse is
            # down -> multiply raw forward u by -1; crowd short -> +1.
            sgn = -1.0 if side == "long" else 1.0
            ev, off_recent, cap = flag_events(d, side)

            def collect(mask, group, side=side, sgn=sgn):
                rows = d.loc[mask, [f"fu{h}" for h in HORIZONS]].copy() * sgn
                rows["t"] = rows.index
                rows["market"] = name
                pooled[(side, group)].append(rows)

            collect(ev, "event")
            collect(off_recent & ~cap, "off_nocap")
            collect(cap & ~off_recent, "cap_nooff")

            evrows = d.loc[ev]
            if len(evrows):
                m4 = (evrows["fu4"] * sgn).mean()
                per_market_sign.append((name, side, len(evrows), m4))

    if not have_prices:
        return

    print("\n" + "=" * 78)
    print("POOLED RESULTS - forward sigma-scaled returns, signed so that")
    print("POSITIVE = adverse move CONTINUES (claim B), NEGATIVE = it reverts")
    print("(claim A). p_null: two-sided, 13-week calendar-block bootstrap,")
    print("recentred on zero.")
    print("=" * 78)

    for side in ("long", "short"):
        crowd = "crowded-LONG specs" if side == "long" else "crowded-SHORT specs"
        for group, label in (
                ("event", f"OFFSIDE CAPITULATION, {crowd}  [primary at 4w]"),
                ("off_nocap", f"control: offside WITHOUT capitulation, {crowd}"),
                ("cap_nooff", f"control: capitulation WITHOUT offside, {crowd}")):
            frames = pooled[(side, group)]
            df = pd.concat(frames) if frames else pd.DataFrame(
                columns=[f"fu{h}" for h in HORIZONS] + ["t", "market"])
            summarize(df, label, rng)

    print("\nPer-market event mean at 4w (sign robustness, FFM §5.6 style):")
    print(f"  {'market':<12} {'side':<6} {'n':>4} {'mean u(4w)':>11}")
    for name, side, n, m4 in per_market_sign:
        print(f"  {name:<12} {side:<6} {n:>4} {m4:11.3f}")
    neg = sum(1 for *_, m in per_market_sign if m < 0)
    print(f"  sign count: {neg} of {len(per_market_sign)} market-sides negative "
          f"(claim A direction)")

    print("\nDeclared readings: 2 sides x 3 horizons on the event group = 6")
    print("(primary = the two 4w event readings), plus the same 6 on each of")
    print("two controls as contrasts. Anything else quoted from this run")
    print("must be counted as an addition.")


# ------------------------------------------------------------------ selftest
def selftest():
    """Synthetic smoke test: exercises the full pipeline offline."""
    global MARKETS
    rng = np.random.default_rng(7)
    idx = pd.date_range("2000-01-04", periods=900, freq="7D")
    cot_rows, prices = [], {}
    codes = list(MARKETS)[:3]
    for code in codes:
        drift = np.cumsum(rng.normal(0, 0.02, len(idx)))
        px_daily = pd.Series(
            100 * np.exp(np.interp(
                np.arange(len(idx) * 7), np.arange(len(idx)) * 7, drift)
                + rng.normal(0, 0.005, len(idx) * 7)),
            index=pd.date_range(idx[0] - pd.Timedelta(days=3),
                                periods=len(idx) * 7, freq="D"))
        prices[code] = px_daily
        npf = np.tanh(np.convolve(rng.normal(0, 1, len(idx)),
                                  np.ones(10) / 10, mode="same"))
        # inject occasional large unwinds so events exist
        jumps = rng.choice(len(idx) - 30, size=25, replace=False) + 20
        npf = pd.Series(npf, index=idx)
        for j in jumps:
            npf.iloc[j] = npf.iloc[j - 1] * 0.2
        oi = pd.Series(100000.0, index=idx)
        net = npf * oi
        for t in idx:
            cot_rows.append({
                "Market_and_Exchange_Names": code,
                "Report_Date_as_MM_DD_YYYY": t,
                "CFTC_Contract_Market_Code": code,
                "Open_Interest_All": oi[t],
                "NonComm_Positions_Long_All": max(net[t], 0) + 10000,
                "NonComm_Positions_Short_All": max(-net[t], 0) + 10000,
            })
    cot = pd.DataFrame(cot_rows)
    MARKETS = {c: (f"SYN-{c}", []) for c in codes}
    run(cot, prices, ["  synthetic data, selftest mode"])
    print("\nSELFTEST COMPLETE - pipeline ran end to end on synthetic data.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--cot-only", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        selftest()
        return

    import requests
    CACHE.mkdir(exist_ok=True)
    cot = load_cot(requests)
    if cot is None:
        print("FATAL: no COT data reachable. This environment cannot run the "
              "evaluation; report the block rather than fabricating results.",
              file=sys.stderr)
        sys.exit(2)

    prices, price_log = (None, [])
    if not args.cot_only:
        prices = load_prices_local()
        if prices is not None:
            price_log = ["  local occ_prices_daily.csv"]
        else:
            prices, price_log = load_prices_stooq(requests)
    run(cot, prices, price_log)


if __name__ == "__main__":
    main()
