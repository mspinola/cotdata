# ADR-0003: the futures-and-options combined basis is inside cotdata's boundary

**Status:** accepted, 2026-10-31
**Context:** [ADR-0002](ADR-0002-supplemental-report-is-in-scope.md), crucible-stack ADR-0007
**Supersedes:** nothing. Amends the carry-forward recorded in
[docs/design/cot_vintage.md §3c](../design/cot_vintage.md).

## Decision, in one line

The CFTC publishes Legacy, Disaggregated and TFF on two bases, futures-only and
futures-and-options combined. cotdata fetches **both**, into separate store domains, and the
futures-only series stays the default everywhere it was the only one.

## Context

Since 2026-08-03 cotdata has carried one basis per report: futures-only for Legacy,
Disaggregated and TFF, and combined for the Supplemental, which the CFTC publishes on no other
basis. That was a deliberate carry-forward, not a boundary decision (cot_vintage.md §3c: "a
fetch-list change, with no schema migration"), and the canonical vintage schema has had
`combined` in its natural key from the start so the two series could never merge.

The combined files are the same report on CFTC's other aggregation: delta-weighted option
equivalents are added to the futures positions. Measured on 2026-10-27, ES open interest is
2,843,639 combined against 1,955,711 futures-only, GC 518,784 against 396,810, CL 2,606,478
against 1,886,280. Nothing in them is a price series or a contract specification, so nothing
crosses the ADR-0007 seam. The question is only whether "CFTC positioning" means the basis we
happened to fetch first or every basis the CFTC publishes, and ADR-0002 already answered the
same question for the fourth report: the boundary is the data, not the July 2026 inventory.

## Decision

In scope, on three conditions that the code enforces rather than documents:

1. **A directory per basis, never a column.** `cot_legacy_combined/`, `cot_disagg_combined/`,
   `cot_tff_combined/` beside the existing three. A consumer reading `cot_legacy/` gets
   exactly what it got before, and no reader can return rows of both bases in one frame.
2. **The default is the natural basis.** `get_cot(..., combined=None)` reads futures-only for
   the three and combined for the Supplemental; `combined=False` on the Supplemental raises.
   Coverage, `asof` and the vintage canonicalisers follow the same rule, and the coverage
   artifact for a report's natural basis keeps its filename.
3. **The file's own statement wins over the URL where it exists.** Disaggregated and TFF say
   `FutOnly_or_Combined`; the producer and the vintage ingest both refuse a file whose
   statement disagrees with the basis asked for. Legacy says nothing (checked on
   `dea_com_xls_2026` and `deacom_xls_2003`: 126 columns, identical names to the futures-only
   file), so there the two bases are separated by construction: one flag selects zip name,
   cache directory and store writer together.

## Consequences

- Seven store domains on the `cot` half of the manifest seam, seven producer actions, and
  `--cot-all` means all seven. Every deployed launcher calls `--cot-all`, so the new domains
  appear on the next scheduled run with no scheduler change; the first such run downloads the
  Legacy combined history (1995 onward, `deacom_xls_` before 2004) and the two
  Disaggregated/TFF combined bundles once.
- The vintage layer captures both bases of every annual zip and all seven current-week
  statics. `Source.combined` and the `combined` field on every snapshot record are the
  provenance; records written before this change have no such field and were all
  futures-only or Supplemental, which is what the reader assumes for them.
- The Supplemental's open interest can now be reconciled from inside the store. ADR-0002's
  "390/390 against Legacy combined" was established against a file the store did not hold;
  `cot_legacy_combined/` holds it.
- **Cross-basis comparisons are the new foot-gun.** `docs/design/cross-report-comparability.md`
  §4 and §5 are amended in the same change: open interest Legacy against TFF is still
  identical *on the same basis*, and any comparison across bases is not.
- **No change is proposed to ADR-0007.** This applies its rule, as ADR-0002 did.
