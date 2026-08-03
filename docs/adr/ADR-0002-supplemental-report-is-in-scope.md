# ADR-0002: the Supplemental (CIT) report is inside narrowed cotdata's boundary

**Status:** accepted, 2026-08-03
**Context:** [ADR-0001](ADR-0001-cot-vintage-provenance-in-parquet.md) (local pointer to
crucible-stack ADR-0008), crucible-stack ADR-0007
**Supersedes:** nothing

## Decision, in one line

The CFTC **Supplemental (Commodity Index Trader)** report is CFTC positioning, so it is in
scope for the narrowed `cotdata`, on exactly the argument that admitted vintage provenance.

## Context

crucible-stack ADR-0007 narrows `cotdata` to **CFTC positioning only** and moves all price
series, futures included, out to `marketdata`. ADR-0008 then had to answer whether vintage
provenance — the raw snapshots, the bitemporal observations, the revision log — was inside
that narrowed boundary, and concluded it was: provenance about CFTC positioning is CFTC
positioning.

Adding a fourth report type raises the same question in a milder form, and it should not be
left implicit. Three reports were already here when ADR-0007 was accepted, so their being
in scope is inherited rather than decided.

## Decision

In scope. The Supplemental is a weekly CFTC positioning report on the same release
schedule, the same Tuesday as-of convention, and the same distribution channel as the three
already ingested. Refusing it would draw the boundary at "the reports we happened to have
in July 2026" rather than at "CFTC positioning", which is not a boundary anyone could apply
to the next case.

Nothing about it pulls price data back across the ADR-0007 seam. It carries no price series
and no contract specification. Its one novel property, being futures-and-options combined,
is a property of CFTC's own aggregation, not of anything `marketdata` owns.

## Consequences

- A fourth `report_type` in the canonical vintage schema, a fourth `cot_*` store domain
  (`cot_supplemental`), and a fourth producer action (`--cot-supplemental`), all on the
  `cot` half of the manifest seam. See the 0.4.0 changelog entry.
- The **category vocabulary check must stay per-`report_type`**. Supplemental reuses the
  labels `commercial` and `noncommercial`, and they do not mean what they mean under
  Legacy: they are net of index traders, and the report is combined where Legacy here is
  futures-only. `report_type` and `combined` are both in the natural key, so the two can
  never merge into one series, but a consumer summing across report types would still be
  wrong.
- **No change is proposed to ADR-0007 or ADR-0008.** This is an application of the rule
  ADR-0007 already states, not a new decision about the boundary, and those documents live
  in the `crucible-stack` checkout where this session has no business editing a shared
  working tree. Recording it here keeps it discoverable from the repo it constrains, which
  is the same reason ADR-0001 exists as a local pointer.

## The reason this is worth a file rather than a comment

The report exists to separate index flow from the rest, and the requesting consumer
(`crowdmon`) wants it because a single Swap Dealer fragility weight has been measured doing
incoherent work: on cocoa the largest net long is the swap dealer, so **fragile** capital
sits at w=0.4, while on gold the immovable physical hedger is a swap dealer with
Producer/Merchant at a tenth of the swap book, so **robust** capital sits at the same 0.4.
Opposite errors, one weight.

That makes it tempting to reason about the index/non-index split here. Do not: this package
ingests and validates, and the analysis is the companion handoff in `crowdmon`. The one
thing worth carrying across the seam is a warning the data supports and the CFTC prose does
not — Supplemental's Index Traders is a **Legacy-taxonomy** carve-out and does **not** nest
inside Disaggregated's Swap Dealer, so the two cannot be differenced to isolate levered
swap flow. Measured detail in
[docs/analysis/2026-08-03-cit-supplemental-measurements.md](../analysis/2026-08-03-cit-supplemental-measurements.md) §5.
