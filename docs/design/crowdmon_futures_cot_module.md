# crowdmon futures COT module: moved

**This is a pointer, not the document.** The spec now lives in the `crowdmon` repo, which is
the package it describes:

**[crowdmon/docs/design/crowdmon_futures_cot_module.md](https://github.com/mspinola/crowdmon/blob/main/docs/design/crowdmon_futures_cot_module.md)**

Alongside it, and also canonical there:

- [`crowdmon_plain_language_summary.md`](https://github.com/mspinola/crowdmon/blob/main/docs/design/crowdmon_plain_language_summary.md),
  the same argument in prose and **the authoritative appendix §A.1-A.11**. Where the module
  spec and the appendix disagree, the appendix wins.
- [`amendments-2026-08-01.md`](https://github.com/mspinola/crowdmon/blob/main/docs/design/amendments-2026-08-01.md),
  every place a measurement contradicted either document.

## Why cotdata had a copy

The spec was drafted here in July 2026, while the vintage store it depends on was being built
and before `crowdmon` existed as a repo. `crowdmon` was created 2026-07-31 (as
`crowdmon-futures`, renamed 2026-08-01) and the design docs went with it. This file is what is
left behind so existing links keep resolving.

[ADR-0007](https://github.com/mspinola/crucible-stack/blob/main/docs/adr/ADR-0007-cotdata-is-cot-only-bars-live-in-marketdata.md)
is why it should not stay: cotdata is CFTC positioning, and a monitor's system design is a
consumer concern.

## Read this before re-syncing anything

The copy that moved on 2026-08-01 took a version predating the 2026-07-30 vintage amendments,
so for a day the destination copy was **104 lines shorter** than this one and carried **none**
of the four amendment blocks this file had. The diff was strictly one-way: 104 lines only
here, zero only there. It was restored before this file became a pointer, because pointing at
the shorter copy would have made the loss permanent and invisible.

Nothing is lost now, and git history holds the full text either way. But the episode is the
argument for this file being a pointer rather than a second copy: **two copies of a living
document diverge silently, and the divergence is only ever found by someone who happens to
diff them.**

## What cotdata does still own

These are cotdata's documents rather than crowdmon's, and they stay here:

| Document | What it is |
|---|---|
| [`cot_vintage.md`](cot_vintage.md) | the vintage store's design. cotdata owns the store |
| [`cot_vintage_store_handoff.md`](cot_vintage_store_handoff.md) | the vintage build's work order and outcome, including the §12.1 measured negative result on release dates |
| [`crowdmon_step2_normalisation.md`](crowdmon_step2_normalisation.md) | the layer-2 proposal as written here, **proposed and measured, never accepted**. crowdmon went on to build layer 2, and its amendments record where this proposal was wrong |

The `crowdmon_futures_` prefix on this filename and on `crowdmon_step2_normalisation.md` is
historically correct rather than stale: that is what the package was called when they were
written.
