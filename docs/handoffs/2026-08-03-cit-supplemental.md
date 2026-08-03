# Handoff: CIT Supplemental ingestion

**Status:** complete (branch `claude/cit-supplemental-ingestion-8686f0`)
**Date:** 2026-08-03
**Lives at:** `cotdata/docs/handoffs/2026-08-03-cit-supplemental.md`
**Target:** Claude Code session, `cotdata` worktree
**Depends on:** vintage subsystem (PR #78, merged)
**Deliverable:** Supplemental report ingested, vintage-captured, exposed through the canonical schema
**Companion:** `crowdmon/docs/handoffs/2026-08-03-index-share.md` — unblocked once this merges

> Executed 2026-08-03. The original brief is kept below **verbatim**; every place the data
> contradicted it is corrected inline in a blockquote marked **CORRECTION**, and the
> report-back is §7. Measurements: [docs/analysis/2026-08-03-cit-supplemental-measurements.md](../analysis/2026-08-03-cit-supplemental-measurements.md).

---

## 0. Why

`crowdmon`'s fragility weights assign Swap Dealer a single `w = 0.4`. Two measurements have shown that one number is doing incoherent work:

- **Cocoa** — the largest net long is the Swap Dealer, so the *fragile* capital sits at 0.4 rather than 1.0.
- **Gold** — the immovable physical-hedger side is a swap dealer, with Producer/Merchant at a tenth of the swap book, so the *robust* capital sits at 0.4 rather than 0.1.

Opposite errors, same weight. A swap dealer's net is the residual of hedged client positions, so its fragility depends on the clients: index funds are unlevered and sticky, levered macro accounts are not. The Supplemental report is the only public source that separates index flow.

**Scope.** The Supplemental report is CFTC positioning, so it falls inside ADR-0007's narrowed boundary on the same argument that admitted vintage provenance. Record this in the existing vintage ADR or a short new one — do not leave it implicit.

> **Done:** [ADR-0002](../adr/ADR-0002-supplemental-report-is-in-scope.md), a new short
> local ADR rather than an edit to ADR-0001. ADR-0001 is a pointer to crucible-stack
> ADR-0008, which lives in a sibling checkout; the workspace working agreement says to
> record such things in the consumer's own docs rather than editing a shared working tree.
> No change is proposed to ADR-0007 or ADR-0008, because this is an application of the rule
> ADR-0007 already states rather than a new boundary decision.

---

## 1. What this report is

| Property | Value |
|---|---|
| Coverage | 13 select agricultural contracts |
| Basis | **Futures-and-options combined only** — there is no futures-only variant |
| History | January 2006 → present, annual compressed archive |
| Cadence | Weekly, same Tuesday as-of / Friday release as the rest |
| Categories | Non-Commercial (long / short / spreading), Commercial (long / short), **Index Traders** (long / short), Non-Reportable |

**The taxonomy is Legacy, not Disaggregated.** Index Traders is carved out of *both* the commercial and non-commercial buckets. It does **not** nest inside Disaggregated's Swap Dealer, and the two cannot be differenced to isolate levered swap flow. Any relationship between them is an inference across differently-partitioned reports.

**Verify the contract list from the data rather than assuming it.** Sources give both 12 and 13; the count has changed over the report's life. Derive the covered set per year from the files and report it, including any market that enters or leaves.

> **CORRECTION, three items.**
>
> 1. **"Coverage: 13" is right only from 2013.** It is 12 markets 2006-2012 and 13 from
>    2013, when Soybean Meal (026603) entered. That is the only entry, and there are no
>    exits in 21 years. Both circulating counts are correct for part of the history.
> 2. **"Carved out of *both* the commercial and non-commercial buckets" understates it by
>    one bucket.** Differencing against Legacy combined shows a third source,
>    non-commercial **spreading**:
>    `CIT_Long = ΔComm_Long + ΔNonComm_Long + ΔNonComm_Spread`, exact to the rounding in
>    §4. Spreading was ~0% of the index book in 2006 and is ~9% now, so the omission was
>    harmless when the report started and is not now. The rest of the paragraph stands and
>    is the single most important line in this handoff: Index Traders does **not** nest
>    inside Disaggregated's Swap Dealer.
> 3. **"There is no futures-only variant" is true, and the file cannot tell you so.**
>    Unlike Disaggregated and TFF the Supplemental carries no `FutOnly_or_Combined` column,
>    so the existing `_combined_flag` would have defaulted it to `False`. Established
>    instead by matching open interest against both Legacy series: 390/390 against
>    futures-and-options combined, 0/390 against futures-only.
>
> Confirmed as written: January 2006 start, annual compressed archive, Tuesday as-of with
> holiday shifts to Monday, and the category list.

---

## 2. Ingestion

Add `supplemental` as a fourth `report_type` alongside `legacy`, `disaggregated`, `tff`.

- Annual compressed archive, same fetch pattern as the existing three
- **`combined` is always `True`** for this report — assert it, never infer it
- Route through the vintage layer from the first fetch: raw snapshot capture, change-only observation writes, field-level revisions. No exceptions.

> **CORRECTION: "same fetch pattern as the existing three" is not quite the pattern.**
> Disaggregated and TFF are served as a 2006-2016 bundle plus individual years, and their
> individual years 404 before 2010. The Supplemental has **no history bundle**
> (`dea_cit_txt_hist_2006_2016.zip` is a 404) and **every year from 2006 returns 200**, so
> `annual_sources` needed its own 2006 floor rather than sharing the existing 2010 one.
> The date column also differs and its name changed in 2013; see the measurements doc §2.

### Schema

The canonical observation schema has Disaggregated/TFF category columns. Supplemental introduces `spread_contracts` on a non-commercial category and has no direct analogue for the disaggregated splits.

**Preferred approach:** keep one observation table, extend the category vocabulary with `non_commercial`, `commercial`, `index_traders`, `non_reportable`, and let `report_type` disambiguate. `spread_contracts` already exists and is nullable.

**Do not** map Supplemental categories onto Disaggregated names. `commercial` here is not `producer_merchant`, and conflating them will silently corrupt every downstream sum. The category vocabulary check must be per-`report_type`.

If the existing schema cannot carry this cleanly, say so and propose an alternative before implementing one.

> **The schema absorbed it with no changes.** One observation table, the natural key
> untouched, `spread_contracts` populated on `noncommercial` only, cr4/cr8 null because the
> report publishes no concentration ratios. Nothing was added to `VALUE_FIELDS` or
> `ALL_COLUMNS`, so no stored `row_sha256` moves.
>
> **DEVIATION on the category spellings**, deliberate. Shipped as `commercial`,
> `noncommercial`, `index_trader`, `nonreportable` rather than the brief's
> `non_commercial` / `index_traders` / `non_reportable`. The repo's existing vocabulary
> spells these `noncommercial` and `nonreportable` in all three other report types, so a
> new spelling would make `category == "nonreportable"` silently miss every Supplemental
> row — a real footgun — while leaving `commercial`, the genuinely confusable label,
> identical either way. The distinguishing-by-spelling argument fails on exactly the
> category that needed it, so consistency wins and `report_type` does the disambiguating,
> as the brief itself specifies.

---

## 3. Why vintage matters more here than elsewhere

The 2018 trader reclassification — the only documented instance of positions being moved between categories, and the failure mode §4.5 of the vintage handoff exists to detect — **affected the Supplemental report specifically**. Some traders were removed from and others added to the index classification; individually small, noticeable in aggregate.

So this is the series with the strongest known reclassification history, and it is also the one where reclassification does the most analytical damage, since the whole point of the report is the index/non-index boundary.

Capture from the first fetch. Category-migration detection (vintage handoff §4.5, currently deferred) should be un-deferred for this report type once a quarter of snapshots exists.

> **Done, with a limit worth stating.** All 21 years are captured and ingested, and every
> future fetch is captured from the first one. But **the 2018 reclassification is not
> recoverable from this**, and nothing in this work makes it so: CFTC serves current state
> only, so the 2018 archive we hold today is the post-reclassification restatement. The
> vintage store detects the *next* one; it cannot reconstruct the last one.
>
> Nothing in the data marks 2018 either. The identity exception rate (§4) is flat across
> the whole history with no break at 2018, which is consistent with a reclassification that
> moved traders between categories without disturbing totals — that is precisely why it is
> invisible to every check except a vintage diff.
>
> Category-migration detection stays deferred, as instructed: a quarter of snapshots does
> not exist yet. Earliest sensible date is **2026-11-01**, the same date §7.8 of the
> crowdmon spec already waits on.

---

## 4. Validation

Existing rules apply, plus:

- `combined == True` on every row
- Category vocabulary enforced per `report_type` — an unknown label raises
- The Legacy identity: reportable long + non-reportable long should reconcile to open interest within tolerance. **Report the exception rate rather than suppressing it.**
- Covered-market set per year emitted as a coverage artifact, so entries and exits are visible rather than silent

> **All four in place.** Exception rate reported in §7 and in the measurements doc §4, not
> suppressed: it is 45% of rows off by a contract or two, which is combined-report rounding
> rather than a data problem, and the control experiment that establishes that is in the
> measurements doc. Coverage artifact is `vintage/coverage/supplemental.parquet`, written
> by `cotdata-vintage coverage`, derived from the observations rather than from a list in
> source.

---

## 5. Tests

| Test | Assertion |
|---|---|
| Combined flag | Every Supplemental row has `combined == True` |
| Vocabulary isolation | Disaggregated category names rejected under `report_type = supplemental`, and vice versa |
| No cross-report mixing | A query for one report type never returns rows from another |
| Vintage round-trip | Snapshot → ingest → re-ingest is a no-op; a changed field emits one revision row |
| Coverage | Covered-market set derived from data matches the emitted artifact |

Fixtures from real files trimmed to two or three markets, committed for offline runs.

> **All five, in `tests/test_cit_supplemental.py` (20 tests), plus parse-level tests for
> the date-column rename and the assert-don't-infer path.** Fixtures are the real 2026 and
> 2012 archives trimmed to three markets and four weeks. Two years rather than one because
> the pair *is* the header rename. `tests/_gen_cit_fixtures.py` regenerates them and selects
> whole source lines rather than round-tripping through pandas, which would have dropped
> the zero padding on `"001602"` and the space padding on `"CBT "` — the byte-level quirks
> the parser is being tested against.

---

## 6. Report back

- The covered-market set per year, and any entries or exits over the history
- Whether the canonical schema absorbed the taxonomy cleanly or needed changes
- The Legacy-identity exception rate, and whether it is stable across history
- Confirmation that the first Supplemental fetch is vintage-captured
- Anything the data contradicted in this handoff, corrected in place

**Do not do the analysis.** That is the companion handoff in `crowdmon`, and it should run against a merged, tested ingestion rather than alongside one.

---

## 7. Report back (answers)

**Covered-market set.** 12 markets 2006-2012, 13 from 2013. One entry, no exits:
Soybean Meal (026603) from 2013-01-08. **Six** renames without a code change, in two waves
(four NYBOT to ICE in 2007, the two wheats relabelled in 2013, including 001612 changing
exchange from KCBT to CBOT and keeping its code), which is why the coverage artifact keys
on `market_code` and not on name — and why it takes the name at the latest report date
rather than the lexicographic max, since `'I' < 'N'` makes max-of-strings report the
pre-2007 NYBOT name forever. Full table: measurements doc §3.

**Schema.** Absorbed cleanly, no changes to the natural key, `VALUE_FIELDS` or
`ALL_COLUMNS`. One deliberate deviation on category spellings, §2 above.

**Legacy-identity exception rate.** Summing the four canonical categories per side, the
identity is exact on 55.2% (long) and 55.4% (short) of 13,584 market-weeks, and the
residual is **never more than 2 contracts** against a tolerance of 4. Counting a
market-week as a breach if either side is off gives a **breach** rate of 67.7%. Two
different rates; keep the qualifier attached.

Both are **stable across the whole history**: exact-rate per-year 51.6% to 59.5% (sd
2.1pp), breach-rate per-year 62.6% to 71.7% (sd 2.7pp), no trend and no break at 2013 or
2018.

The cause is combined reporting, established by control: on the same 2026 weeks and the
same identity, Legacy **futures-only** is exact on 99.7% of rows and Legacy **combined**
shows the identical +/-1 pattern on 10%. Combined reports publish delta-weighted option
equivalents rounded to whole contracts, independently per category, so `n` categories admit
at most `n` contracts of error — which is the bound `rounding_tolerance` already returned.
`validate()` raises **zero** soft warnings across all 21 years.

**Vintage capture.** Confirmed end to end through the shipped code path: 21 raw snapshots
captured, ingested to 54,336 observations across 13,584 market-weeks, zero revisions and
zero warnings on first ingest, and a full replay of all 21 snapshots wrote zero further
observations. `supplemental` is in the default `fetch` set from 2006 onward, so every
weekly release is captured from now on without further action.

**Contradictions.** Four, all corrected in place above: the market count (§1), the
carve-out being three-way rather than two-way (§1), the combined flag being unreadable from
the file (§1), and the fetch pattern differing from the other three reports (§2).

**Not done, deliberately.** No analysis: the index/non-index question is the companion
handoff's. No category-migration detection: it stays deferred until a quarter of snapshots
exists, earliest 2026-11-01. The 2018 reclassification is **not** recoverable and this work
does not make it so (§3).
