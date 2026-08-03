# Handoff: index share and the swap-dealer weight

**Status:** blocked — do not start until `cotdata` CIT Supplemental ingestion is merged
**Date:** 2026-08-03
**Lives at:** `crowdmon/docs/handoffs/2026-08-03-index-share.md`
**Target:** Claude Code session, `crowdmon` worktree
**Depends on:** `cotdata/docs/handoffs/2026-08-03-cit-supplemental.md`
**Deliverable:** an empirical basis for the Swap Dealer weight, or a stated finding that none exists

> Update `Status:` to `complete (PR #NN)` when executed.

---

## 0. The question

Swap Dealer currently carries a single `w = 0.4`. Two markets show that number failing in opposite directions:

- **Cocoa** — Swap Dealer holds the largest net long, so fragile capital is underweighted at 0.4
- **Gold** — Swap Dealer is the immovable physical-hedging side, so robust capital is overweighted at 0.4

A swap dealer's net is the residual of hedged client positions. Its fragility depends on the clients: unlevered index funds are sticky, levered accounts are not. The Supplemental report separates index flow, so for the markets it covers the question is answerable rather than a matter of judgement.

**Read §3 before planning the work.** The constraints are severe and shape what can honestly be concluded.

---

## 1. Measure

Restricted to the 13 Supplemental markets, over the full 2006→present history.

**Index prominence:**

```
index_gross_share = (L_IT + S_IT) / (2 · OI)
index_long_share  = L_IT / L_total
index_net         = L_IT − S_IT
```

**Persistence** — the property that actually matters for fragility. Index positions should be sticky if they are what they claim to be:

- Autocorrelation of `index_net` at 1, 4 and 12 weeks
- Standard deviation of weekly `Δindex_net / OI`, compared against the same statistic for Managed Money and for Swap Dealer in the Disaggregated report on the same markets
- Behaviour during the sharpest drawdown weeks in each market: does index positioning fall, hold, or rise? **This is the single most informative measurement in the handoff** — stickiness under stress is precisely what a low fragility weight asserts.

**Relationship to Swap Dealer** — inference across reports, not identity:

- Correlation between `index_gross_share` (Supplemental) and `swap_gross_share` (Disaggregated) per market, and week-to-week within market
- Where they diverge, note it. A market where the swap book is large and the index book is small is where `w_SD = 0.4` is most likely wrong in the fragile direction.

**Cocoa and live cattle in full.** Cocoa motivated the question; live cattle is the appendix's worked example. Give each a complete walkthrough in the established house style: numbers carried through, arithmetic shown, prose reading at the end.

---

## 2. Weight sensitivity

Recompute the §B33–B36 headline figures — template rate by stratum, `A_agnostic` median, `Q_sell`/`Q_buy` — across `w_SD ∈ {0.2, 0.4, 0.7}`, restricted to Supplemental markets so results are comparable to the index measurement.

Report how much each conclusion moves. If the template rate is insensitive to `w_SD` across that range, the weight matters less than the effort implies and that is worth knowing. If it is highly sensitive, the weight is load-bearing and needs justifying rather than asserting.

**Do not change the weight table in this session.** Produce the evidence; the decision is separate and follows.

---

## 3. Constraints — read before concluding anything

**Thirteen agricultural markets only.** No metals. Gold, silver and copper — three of the seven always-template markets, and the case where a swap dealer sits on the *immovable* side — are outside coverage and cannot be resolved this way, ever, by this report. Whatever is concluded for ag does not transfer to metals without an argument that is not in this data.

**Combined futures-and-options, against a futures-only Disaggregated store.** Different basis, different denominators. Compare ratios within each report; never difference across them. Any cross-report statement is an inference and should be labelled as one.

**Legacy taxonomy.** Index Traders is carved from both commercial and non-commercial, so it does not nest inside Swap Dealer. `index_gross_share` and `swap_gross_share` are two views of overlapping populations, not a decomposition.

**Classification instability from §B36.** 22 of 39 markets are extreme over the pooled window but only 17 in both halves; cocoa runs 0.976 then 0.100. Any conclusion tied to template classification inherits that instability. Prefer statements about position behaviour over statements about classification.

---

## 4. Report back

- Index prominence and persistence tables, per market
- **The stress-week behaviour result**, called out separately
- Index-versus-swap relationship, with the cross-report caveat carried
- Cocoa and live cattle walkthroughs
- Weight sensitivity: how much each headline conclusion moves across `w_SD ∈ {0.2, 0.4, 0.7}`
- **An explicit statement of what remains unresolved for metals**, which is most of the original question
- Anything contradicting this handoff, corrected in place

A negative result is a real outcome. If index positioning turns out not to be meaningfully stickier than swap positioning generally, that retires the premise for a per-market weight and is worth reporting plainly.
