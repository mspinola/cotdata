# Handoff: ADR-0007 step 2, move price production out of `cotdata`

**Status:** **STEP 1 SHIPPED (2026-08-08).** The `marketdata` futures provider exists.
Steps 2–5 of §7 (contract specs are done; consumers not yet repointed; nothing deleted
from `cotdata`) remain. See `docs/handoffs/2026-08-08-adr7-step2-provider-shipped.md`,
which also records a store-layout finding this document did not anticipate.
Originally: **CLAIMED, NOT STARTED** — a work order and a re-measurement, no code
**Date:** 2026-08-04
**Lives at:** `cotdata/docs/handoffs/2026-08-04-adr7-step2-price-producer-split.md`
**Target:** a Claude Code session in a `cotdata` worktree, with `marketdata` beside it
**Decision it executes:** [crucible-stack ADR-0007](https://github.com/mspinola/crucible-stack/blob/main/docs/adr/ADR-0007-cotdata-is-cot-only-bars-live-in-marketdata.md),
**accepted 2026-07-27**. This proposes no new decision and asks for none
**Blocked on:** nothing technical. Three questions in §5 want an answer before code, and two
of them are ADR-0007's own open questions rather than new ones
**Companion:** `crowdmon/docs/design/amendments-2026-08-04.md` §D14 — the consumer-side
record of §4, filed there so the constraint is discoverable from the repo it binds
**Not this handoff:** step 3 (`livebook`'s six `contract_specs()` calls at `required=False`),
which ADR-0007 defers deliberately because it is a live book

> Announced before the first line of code, per the neighbouring repo's convention. If you
> were about to start it, say so and I will drop it.

---

## 0. Why now, and why this is a re-measurement rather than a proposal

ADR-0007 settled the direction on 2026-07-27: `cotdata` becomes CFTC positioning only, and
all bars, futures included, move to `marketdata`. Step 1 shipped. Step 3's prerequisite is
half built. **Step 2 has not started**, and `crowdmon`'s `CLAUDE.md` records it as "on ice…
nobody owns it. Do not start it without confirming that has changed."

That has now changed: the maintainer asked for it directly on 2026-08-04, in the course of
deciding which machine publishes the crowdmon damage panel.

The one thing this handoff adds to the ADR is a **re-measurement, and the number moved the
wrong way.**

## 1. Step 2 has grown ~11% while sitting on ice

Measured 2026-08-04 against `wc -l`, on `main` at `49ebc45`:

| file | ADR-0007 recorded (2026-07-27) | today | drift |
|---|---:|---:|---:|
| `src/cotdata/providers/databento.py` | 1,015 | **1,099** | +84 |
| `src/cotdata/providers/norgate.py` | 469 | **546** | +77 |
| `src/cotdata/prices.py` | 167 | **186** | +19 |
| `src/cotdata/providers/yfinance.py` | 70 | 70 | — |
| **total** | **1,721** ("roughly 1,700") | **1,901** | **+180** |

Plus `metadata/contract_specs.parquet` and the `--metadata` action, unchanged in scope.

ADR-0007 predicted this in as many words: *"`databento.py` arriving after this ADR was
written makes `cotdata` more price-heavy than the measurements above, which strengthens the
case and enlarges step 2 at the same time."* It was right, and the drift is now measured
rather than anticipated: **+180 lines in eight days, both price providers growing, `prices.py`
growing, and neither CFTC provider moving.**

That is the argument for doing it sooner rather than the argument for doing it at all. The
ADR already made the second one.

## 2. It is not a file move: `marketdata` has no futures provider

`marketdata/src/marketdata/providers/` contains `__init__.py` and `yfinance.py`. That is all.

The `futures` domain **is** declared in `adjust.DOMAIN_TIERS`, which ADR-0007 notes was done
so error messages are correct from the first day, but nothing implements it. So step 2 is
*write the Norgate futures provider into `marketdata`*, using `cotdata`'s as the reference,
and not *move a file across a seam*.

Whoever takes this should price that difference in before estimating. 1,901 lines of source
to read; the delivered artifact is a provider that did not exist.

## 3. The consumers, enumerated

Measured 2026-08-04. Every one of these is a read that step 2 repoints.

**`crowdmon`** — 6 modules, 10 call sites:

| module | call |
|---|---|
| `futures/riskunits.py:138` | `cotdata.get_prices(symbol, adjustment="propadj")` |
| `futures/notional.py:88` | `cotdata.get_prices(symbol, adjustment="unadj")` |
| `futures/volume.py:106,117` | `cotdata.get_prices(..., volume="front")` |
| `futures/alignment.py:106` | `cotdata.get_prices(...)["Close"]` |
| `futures/clustering.py:116` | `cotdata.get_prices(...)["Close"]` |
| `futures/contract_master.py:97,100,117,122` | `all_symbols`, `load_manifest`, `store.read_metadata` |

`contract_master.py:34` already documents itself as the call site ADR-0007 will repoint, and
`store.read_metadata()` is flagged there as the one non-`__all__` cotdata symbol the package
reaches for. That comment is the pre-existing marker for this work.

**`cotmetrics` / `cot-analyzer`** — ADR-0007 §"measured facts" records that nothing in
`cot-analyzer` calls a price vendor, every live read is `cotdata.get_prices`, and every one of
those is `adjustment="backadj"`.

**`npf`** — reads futures through `cotdata.get_prices`; ADR-0007's open question about
`propadj` records that a month-end Treasury verdict was **voided** by using `backadj` and
fixed by `propadj`, so this consumer has already been bitten by a tier choice.

## 4. The finding this handoff exists to record

> **SUPERSEDED IN ITS CONSUMER, NOT IN ITS CONCLUSION — see §8.1.** The constraint below
> is real and the producer enforces it. But `crowdmon` was deprecated on 2026-08-07, three
> days after this was written, and by 2026-08-08 had no consumers at all, so it can no
> longer carry the argument. §8.1 re-anchors it on `npf`, which makes the same case from a
> live book. **Read §8.1 before quoting this section.**

Stated nowhere before today, and it is the constraint that decides which machine can produce
a store the newest consumer can use.

**`crowdmon` needs `unadj` AND `propadj`. `propadj` is derived on read from `unadj` +
`backadj` (`cotdata.prices._ratio_adjust`), so BOTH stored tiers are its precondition.**
`contract_master.coverage()` already encodes this: `joinable` means specs plus `unadj` plus
`backadj`, and its docstring says the back-adjusted requirement is right "not for the reason
an earlier version of this docstring gave" — it is the precondition for the derived tier, not
a claim that `backadj` returns are correct. They are not; `riskunits` refuses them.

Now compose that with ADR-0007's scoping of databento:

> databento exists for exactly one reason: the `cot-analyzer` dashboard runs on a Linux
> server that cannot run Norgate. It is **not** a second research vendor, and its coverage
> should not be broadened toward parity with Norgate.

and its measured consequence:

> **Every one of those reads is `adjustment="backadj"`.** So databento owes exactly one
> series per symbol, not the full tier set.

**Therefore: a databento-backed futures store, as ADR-0007 scopes it, cannot feed `crowdmon`
at all.** One stored tier cannot produce `propadj`, `riskunits` refuses anything else, and the
refusal is a `raise` rather than a warning because the error is invisible to a spot check
(`backadj` percent vol is 201x too high for soybeans and **0.47x for gold**, which never goes
negative and passes every implausibility screen).

The practical consequence, which is what the maintainer was actually asking about:

**Only the Windows box can produce a store `crowdmon` can consume.** Not because of Python
(`cotdata` and `crowdmon` both declare `>=3.10`, and the Mac runs 3.11 under uv and builds the
full panel today), and not really because of Windows. Because it is the only machine with a
vendor that supplies **all the tiers**. `norgatedata` talks to a locally installed Norgate
Data Updater rather than an API (`providers/norgate.py:207,325`, both commented "Windows
producer only"), so the macOS box has no path to it at any Python version.

| box | Python >=3.10 | Norgate | can produce a crowdmon-consumable store |
|---|---|---|---|
| Windows | yes | **yes** | **yes** |
| macOS | yes, runs 3.11 today | no (no NDU for macOS) | no |
| Linux server | **no, runs 3.9** | no | no |

**What step 2 must not do:** narrow this further by accident. If the `marketdata` futures
producer ships databento-first, or ships Norgate but stores only `backadj` to match the
server's needs, `crowdmon` stops working and the failure is a `raise` inside `riskunits` on a
weekly scheduled job. Whoever writes the provider needs `unadj` and `backadj` **both stored**
as a hard requirement, not as a coverage nicety.

## 5. Three questions to answer before writing code

Two are ADR-0007's own open questions. The third is new and is the one this handoff was
written to surface.

**5.1 Does `propadj` stay derived-on-read?** ADR-0007 leaves this open but records that
evidence arrived on 2026-07-27 and pointed one way: the month-end Treasury seasonal was voided
on `backadj` and fixed on `propadj`, and `marketdata` already derives every tier on read.
**Recommendation: yes, and treat it as settled by that evidence.** §4 above is a second,
independent reason.

**5.2 Do the two store roots converge?** `COTDATA_STORE` and `MARKETDATA_STORE` are both set
from the shell profile and neither is set by any launcher, so ADR-0007 notes converging is
still cheap. **That is now less true than it was**: `crowdmon`'s `bin/publish_damage.sh` and
its launchd agent both default `COTDATA_STORE`, and `CROWDMON_STORE` joined the family on
2026-08-04 as a third root. Three roots is the moment to decide, not after.

**5.3 NEW: does the Linux server ever run a futures producer, or is a synced store
permanent?** ADR-0007 asks this and leaves it open. **2026-08-04 answered it de facto for at
least one consumer**: the crowding page ships as a synced artifact because that box runs
Python 3.9 against `crowdmon`'s `>=3.10` floor and cannot run the producer at any price
(`crowdmon` ADR-0001). Combined with §4, the server cannot produce a crowdmon-consumable store
even if its interpreter were raised, because it has no Norgate either.

**So "synced store" is not a temporary state pending step 2. It is the answer**, unless either
the server's interpreter is raised AND a full-tier non-Norgate vendor appears. Worth writing
into ADR-0007's open-questions section as resolved rather than leaving it reading as undecided.

## 6. What this handoff deliberately does not decide

- **Whether to split price production into its own repo** rather than into `marketdata`.
  ADR-0007 says `marketdata`, and ADR-0007 is accepted. A session that wants a different
  answer writes a superseding ADR; it does not quietly build something else.
- **Whether databento's coverage should broaden.** ADR-0007 says no, twice. §4 is a reason to
  revisit *someday* and is not a licence to.
- **Anything about `livebook`.** Step 3, deferred on purpose, live book.

## 7. Suggested order

1. Write `marketdata/src/marketdata/providers/norgate.py` against the `futures` domain already
   declared in `adjust.DOMAIN_TIERS`, storing `unadj` and `backadj` as a hard requirement (§4).
2. Port `contract_specs` and `--metadata`.
3. Repoint `crowdmon`'s ten call sites (§3). It is the smallest consumer and the one whose
   tier requirements are strictest, so it fails loudest if the provider is wrong. **Do it
   first, not last.**
   > **VOID — see §8.2.** `crowdmon` is deprecated and has no consumers. Do not repoint it.
   > This step, and the de-risking it was meant to provide, are gone.
4. Repoint `cotmetrics` / `cot-analyzer`, which are `backadj`-only and therefore easy.
5. Delete from `cotdata`, and update `crucible-stack` ADR-0007's "Status of the work".

Step 3 stays out. `npf` and `livebook` are a separate pass with a live book behind them.

---

## 8. Outcome, appended 2026-08-08

**§7 is complete.** Step 1 shipped as `marketdata` PR #7 and §7.2 came with it (full record:
[`2026-08-08-adr7-step2-provider-shipped.md`](2026-08-08-adr7-step2-provider-shipped.md));
§7.3 was voided by §8.2; §7.4 and §7.5 landed 2026-08-09. §8.5 records what §7.5 actually
deleted and the one thing it deliberately did not.

Body preserved verbatim above, per the register convention. This section carries the
corrections; §4 and §7.3 carry pointers to it.

### 8.1 §4's constraint stands. Its consumer does not

**The requirement is unchanged and is enforced three ways** in the shipped provider: both
tiers are fetched before either is written, a read finding one raises and names the
missing one, and a test covers both halves. Nothing here relaxes it.

What changed is that §4 argued it entirely from `crowdmon`, and `crowdmon` is gone.
`crowdmon/DEPRECATED.md` decides deprecation on **2026-08-07** — one day after this
handoff's own companion document, `crowdmon/docs/design/amendments-2026-08-04.md` §D14 —
after four pre-registered tests returned no positive result and the §10 validation came
back uninformative with its clean episodes spent. Its §3 was resolved **2026-08-08**: both
launchd jobs unloaded, `cot-analyzer`'s `/damage` page removed (that repo's PR #22), the
one open work order closed unstarted, and the package left with **no consumers at all** —
"nothing in `npf` or `livebook` ever imported it".

So §4's closing warning — that a `backadj`-only producer breaks `crowdmon` with a `raise`
inside `riskunits` on a weekly scheduled job — describes a job that no longer runs.

**Re-anchor on `npf`, which makes the case from a live book.**
`npf/books/treasury_seasonal.py` sets `RETURN_TIER = "propadj"`, amended 2026-07-26 after
its first run came back void:

> Norgate's `backadj` is ADDITIVE: roll gaps accumulate into the level, so the series is
> not a price and can cross zero. On the verdict window ZB's back-adjusted close runs
> −12.24 to 48.36 and is negative on 454 days, while the contract actually traded 72.66 to
> 112.19. A percent return needs a positive denominator, and **15 of ZB's 100
> verdict-window trades had their SIGN INVERTED.**

The same file needs both stored frames independently of `propadj`: *"Roll DETECTION still
reads backadj and unadj, because their difference IS the accumulated adjustment and is
precisely what steps at a roll."*

Fifteen sign-inverted trades in a live book is a stronger argument than the one §4 made,
and it survives the deprecation. §4's table of which box can produce a consumable store is
unaffected — it turns on Norgate supplying all the tiers, not on who consumes them.

### 8.2 §7.3 is void, and §7's ordering with it

Repointing a frozen package with zero consumers is waste. `crowdmon/DEPRECATED.md` §2
asks for the opposite: it wants that package's live pins **neutralised**, because "a frozen
repo should not have tests that depend on data collected after it was frozen". Repointing
would add a dependency on data collected after the freeze.

The consumer list in §3 now reads:

| consumer | needs | status |
|---|---|---|
| `crowdmon` (§3, 6 modules / 10 call sites) | both tiers + `propadj` | **dropped** |
| `npf` | both tiers + `propadj` (live book) | ADR-0007 **defers**, with `livebook` |
| `cotmetrics` / `cot-analyzer` | `backadj` only | the only repoint left |

**What is lost with it.** §7.3's instruction was not arbitrary: `crowdmon` was to go first
*because* it fails loudest, so it would prove the provider before the easy consumers
depended on it. Removing it removes that check. Nothing now exercises `propadj` until the
deferred `npf` pass runs, so **§7's "compare against `cotdata`'s existing `ES_backadj`" on
the Windows box is the main remaining evidence that the port preserved the numbers.** Run
it early rather than late.

**One question this handoff does not answer.** ADR-0007 defers `npf` on the grounds that it
is a live book, decided when it was one of two `propadj` consumers. It is now the only one.
Whether the deferral still holds is ADR-0007's call.

### 8.3 A finding §3's call-site table does not capture

`crowdmon`'s `ContractMaster.load()` couples to the *shape* of the manifest, not only to
`cotdata`'s functions:

```python
for name in load_manifest().get("prices", {}):
    sym, _, adj = str(name).rpartition("_")     # "ES_backadj" -> ("ES", "backadj")
```

`marketdata`'s entries live under `"bars"` and read `futures/norgate/ES_backadj`, so that
parse yields `futures/norgate/ES` and matches no registry symbol — every symbol would go
non-joinable. It fails loudly rather than silently. Recorded even though §8.2 retires the
`crowdmon` repoint, because the coupling is a manifest *format* rather than a call, and §3's
method — counting call sites — would not have found it in any consumer.

### 8.4 The §6 question about `MME`/`MFS`, answered

They are **not ported** to `marketdata` (Norgate carries no continuous series for either;
`cotdata` prices them off the EEM and EFA ETF proxies). This costs `crowdmon` nothing even
had it been repointed: both already fail `coverage()` for the same underlying reason,
reporting `missing: specs,unadj_price,backadj_price`, pinned by
`tests/test_contract_master_live.py` as the only two non-joinable, absent from both vintage
panels per the 2026-08-04 spec inventory, and `Role: heldout` in the deployed `params.yaml`.
The counts agree: 49 of 51 joinable, and 49 futures symbols ported.

### 8.5 What §7.5 deleted, and the exception it kept

Landed 2026-08-09. Gone from `cotdata`: `prices.py` (including the derived `propadj` tier),
`providers/norgate.py`, `providers/yfinance.py`, the `get_prices` / `roll_dates` exports,
`store.{write,upsert,read}_metadata`, the `--prices` / `--metadata` / `--prices-yahoo` /
`--require-final` / `--final-cutoff` / `--full` CLI flags, the `norgate` and `yahoo` extras,
and the `packaging` runtime dependency that existed only for `norgatedata`.

**`providers/databento.py` stays**, and with it `store.write_prices` / `read_prices`,
`config.prices_dir()` and the `prices` manifest half it writes through. §7.5 was scoped to
"delete the price surface", and databento is inside that surface by shape but outside it by
argument: ADR-0006 accepted it as a *validated provider-different alternative*, it is the
fleet's only intraday-capable source, and ADR-0007 never scoped a marketdata equivalent. So
`cotdata-prices` survives, scoped to `--ingest-databento` / `--build-databento`. The
*consumer* bar API left; the store-level pair the retained producer writes through did not.
That is the line, and it is worth stating because "cotdata is COT-only" is now true of its
public API and not yet of its store.

**Three findings the plan did not anticipate**, each a case of the deletion exposing
something §7.1 had left:

1. **`finals_ready()` had no caller.** It was ported in §7.1 but never wired to a CLI flag,
   so `cotdata-update --prices --require-final` was the only way to reach it. Deleting that
   would have left the Windows nightly job ungated, and the failure it prevents is silent —
   a fetch before Norgate settles writes a provisional bar over a real one with nothing in
   the store to say so. Fixed first, as `marketdata` PR #13, because the deletion was not
   safe without it.

2. **Six behaviours had tests only in `cotdata`.** §7.1 ported the provider and not its
   tests; `marketdata`'s suite covered the pure functions and left everything reachable only
   through `update()` untested — volume reconstruction, the volume-rank pick, the
   incremental window, `full=True`, the NDU-down abort, the all-null spec-row skip. Deleting
   here would have been the moment those stopped being tested anywhere. Ported in the same
   PR. **A file-count check would have missed this entirely**: the test *files* existed on
   both sides, with zero name overlap and a real coverage gap underneath.

3. **Two harnesses read the Norgate store by path.**
   `scripts/validate_databento_vs_norgate.py` and
   `scripts/investigate_databento_roll_rule.py` open `prices/<SYM>_backadj.parquet`
   directly rather than through an API, so no call-site grep finds them — the same class of
   coupling as §8.3, one layer out. Both now read `bars/futures/norgate/` first and fall
   back to the old layout, so the ADR-0006 parity gate still runs across the split.

**Known breakage, accepted.** `crowdmon` (frozen, archived) and
`npf/docs/crowdmon/reproduce_forced_flow_mechanism.py` still call `cotdata.get_prices`.
Both are point-in-time records under their repos' doc lifecycle and were left untouched on
purpose; §8.2 already voided the `crowdmon` repoint on the same grounds.

**Still open for `crucible-stack`:** ADR-0007's "Status of the work" needs updating, and it
now has a second decision to record — that databento remains in `cotdata` with no
marketdata equivalent, which is a live exception to the ADR's own boundary rather than a
step still to do.
