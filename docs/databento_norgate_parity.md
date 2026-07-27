# databento vs Norgate parity (ADR-0006)

Status of the databento back-adjusted price build measured against the Norgate build,
and what still blocks promoting databento to a full drop-in provider.

## Why this exists

ADR-0006 builds each provider's continuous series independently (single provider per
symbol, no cross-vendor stitching). The absolute price LEVELS are allowed to differ
because each provider's roll calendar and back-adjust anchor float on their own. What
must agree is the SHAPE: additive (Panama) back-adjustment preserves absolute daily
price changes, so `Close.diff()` on any non-roll day should match between the two
builds. `scripts/validate_databento_vs_norgate.py` quantifies that per symbol
(change correlation, scale ratio, roll-date agreement).

## The run

Built all 40 databento-capable symbols into a separate server store and compared
`backadj` against the local Norgate store over the databento history (2010-06 to
present):

```
COTDATA_STORE=~/code/cotdata_store_server \
COTDATA_DATABENTO_RAW=~/code/cotdata_store/_raw/databento \
cotdata-update --build-databento

COTDATA_STORE=~/code/cotdata_store python scripts/validate_databento_vs_norgate.py \
  --norgate-store ~/code/cotdata_store \
  --databento-store ~/code/cotdata_store_server \
  --symbols <...>
```

## Result: three buckets

| Bucket | N | Symbols | Reading |
|---|---|---|---|
| Clean | 14 | ES NQ YM RTY EMD NKD GC PL PA 6E 6B 6C BTC ETH | change corr >= 0.996, scale ~1.0. Ready. |
| Units (x100) | 3 | SI HG 6J | Shape agrees (corr >0.998). Pure unit difference. Fixed. |
| Roll-cal minor | 12 | HO ZL ZW KE 6A 6S 6M 6N ZN ZT ZF ZB | corr 0.95 to 0.994. Borderline. |
| Roll-cal MAJOR | 11 | CL RB NG ZC ZS ZM ZO DC LE HE GF | corr 0.33 to 0.94. The blocker. |

The clean bucket is the quarterly-roll financials, metals, and crypto. The two
problem buckets are the monthly-contract commodities and livestock.

## Finding 1: units (SI, HG, 6J), resolved

databento reports true dollars (silver $58/oz, copper $4.5/lb, JPY $0.0061). The
toolchain's convention, inherited from Norgate, quotes silver and copper in cents and
JPY in the IMM x100 form, so those three came out 100x off in both unadj and backadj.
The daily-change correlation already sat above 0.998, confirming it was units only,
not the back-adjustment.

Fixed in the build with a small deny-by-default `_PRICE_SCALE` allowlist
(`{"SI": 100, "HG": 100, "6J": 100}`) applied to the price columns before the roll-gap
math, never to Volume or Open Interest. After a rebuild, SI/HG/6J scale ratio is ~1.0
and SI passes parity outright. HG and 6J still sit just under the strict 0.999
correlation gate, but that residual is the roll-calendar difference below, not units.

## Finding 2: roll calendar (the monthly-roll set), open

databento's `.n.0` continuous rolls on its own open-interest rule (read from the
`instrument_id` change). For the monthly-contract commodities and livestock that places
rolls on almost entirely different dates than Norgate (near-zero common roll dates), so
between the two roll points the two series track different delivery months whose daily
changes genuinely differ. The back-adjusted shape then diverges: CL 0.78, HE 0.67,
DC 0.33. This is not a bug, it is a different continuous methodology, but it means
databento CL is a materially different series from Norgate CL, so a book validated on
one would behave differently on the other.

### Hypothesis

databento offers three continuous roll rules, chosen by the middle letter of the
continuous symbol: `c` (calendar, roll on expiration), `n` (open interest, current
choice), `v` (volume). Norgate's monthly-commodity rolls may line up far better with
`v` or `c` than with `n`.

### How to test it

`scripts/investigate_databento_roll_rule.py` pulls each rule's daily bars (ohlcv only,
so it is a cheap one-request-per-rule pull, no statistics), reads the roll dates off the
`instrument_id` changes, and scores each rule against Norgate's roll dates (from the
`Delivery Month` column). Needs `DATABENTO_API_KEY` and a Norgate-built store:

```
DATABENTO_API_KEY=... python scripts/investigate_databento_roll_rule.py \
  --norgate-store ~/code/cotdata_store \
  --symbols CL ZS NG HE ZC LE --tol-days 3
```

If one rule beats `.n` broadly, switch the producer's `_FEEDS` root (and re-ingest the
affected symbols). If different symbols favor different rules, add a per-symbol
roll-rule field to the registry rather than a single global change.

## Decision (2026-07-27)

The roll-rule investigation is complete. It is per-symbol, not global: energy (CL, NG) tracks
Norgate on the calendar roll `.c` (near-perfect, 0.995 to 1.000 roll-date match), grains (ZS,
ZC) on the volume roll `.v` (best but loose, a few days off each roll, so about 0.95 not 0.999),
and the quarterly financials and metals already match on the open-interest roll `.n`.

That per-symbol roll rule is documented here but intentionally NOT built. The server will source
Norgate via a Windows-to-Linux store sync rather than databento. That is risk-free (the dashboard
then shows exactly what local research shows, the same Norgate data) and lower maintenance than
carrying a per-symbol roll-rule table plus a grain series that never quite matches. databento
remains a validated, provider-different alternative source, not the server's price provider.
ADR-0006 is Accepted on that basis (see its Outcome section).
