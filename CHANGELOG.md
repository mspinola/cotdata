# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **`propadj` price adjustment** — a proportional (ratio) back-adjusted view
  derived on read from the stored `unadj` + `backadj` series via
  `get_prices(symbol, adjustment="propadj")`. It preserves daily percentage
  returns and stays strictly positive, unlike Norgate's additive `backadj`.
  Motivated by **DC (Class III Milk)**: additive back-adjustment drove 46.7% of
  `DC_backadj` closes ≤ 0 (range −9.83 to 23.09), making price-based stops and
  R-multiples unusable; `propadj` yields a strictly-positive DC series
  (4.68–25.01) over the full 1997–2026 history. Recommended for low-priced,
  long-history contracts. Derived from already-stored series — no producer
  re-run or schema change required. ([#23](https://github.com/mspinola/cotdata/pull/23))
