# The series routine: what Claude does on the producer box each evening

This file is the whole instruction set for the Claude Code Desktop **local routines** that
produce marketdata's `series` domain (the FOMO share, new 52-week highs and lows, the Cboe
put/call ratios). Copy it into your scheduler folder beside `run-series.cmd`, fill the three
markers (`REPLACE_WITH_CODE_ROOT`, e.g. `C:\Users\you\code`; `REPLACE_WITH_MARKETDATA_STORE_PATH`;
`REPLACE_WITH_SCHEDULER_DIR`), and point each routine at it with a one-line instruction:

> Follow REPLACE_WITH_SCHEDULER_DIR\series-routine.md exactly.

Why a routine and not a task: TradingView has no data API. The series reach the box through a
claude.ai connector, which only a Claude session can call. The routine is the transport; every
check is in `marketdata-update --build-tradingview`, which `run-series.cmd` runs (see that
file's header). Design: marketdata `docs/design/breadth-domain-scoping.md`, cot-analyzer
`docs/design/tradingview-breadth-scoping.md`.

## Routine settings

| field | value |
|---|---|
| Type | Local |
| Folder | `REPLACE_WITH_CODE_ROOT` |
| Schedule | Weekdays. Two routines: one about 18:30 ET, one about 19:45 ET (the second is the retry). Both after the 17:30 equities task and its retries, both before the 20:55 futures window, whose repeats sync the same replicas this wrapper syncs. |
| Instructions | the one line above |
| Permission mode | the default. The allow rules below remove the two prompts the probe showed (the bars call, the file write). Anything else the model tries still prompts, and a prompt stalls the run, which is the point. |

Allow rules, in `REPLACE_WITH_CODE_ROOT\.claude\settings.json` (project level; the docs say
user-level `~/.claude/settings.json` rules apply to routines too, so mirror them there if a
prompt still appears). The connector's server name in a rule is how the session prints the
tool: run one interactive call first and copy the name from `/permissions` or from the
prompt text. Two spellings are listed because the docs show `mcp__claude_ai_<server>__<tool>`
while sessions have printed `mcp__<connector-id>__<tool>`; an unmatched rule is harmless.

```json
{
  "permissions": {
    "allow": [
      "ToolSearch",
      "mcp__REPLACE_WITH_CONNECTOR_ID__mcp-tv-get-ohlcv",
      "mcp__claude_ai_TradingView__mcp-tv-get-ohlcv",
      "Edit(marketdata_store/_raw/tradingview/**)",
      "Edit(/c/Users/you/code/marketdata_store/_raw/tradingview/**)",
      "Bash(cmd //c \"C:/Users/you/code/cotdata/scheduler/run-series.cmd\")"
    ]
  }
}
```

`Edit` rather than `Write`: the docs state a path rule written for `Write` is accepted and
never consulted. Paths in rules are POSIX form even on Windows. The Bash rule matches the
whole command text, so step 4 below must be typed exactly as the rule has it.

## Steps

Do these in order, and nothing else.

1. **Load the TradingView tool schema.** The connector's tools are deferred; one schema-load
   call for `get_ohlcv` (the tool named `mcp-tv-get-ohlcv`) is required before it can be
   called.
2. **One bars call per symbol** in the table below: `symbol` as listed, `interval` `1D`,
   `count` `10`. If a call fails, skip that symbol and continue; the build will refuse it by
   name and the later routine will retry.
3. **Write each tool result verbatim** to
   `REPLACE_WITH_MARKETDATA_STORE_PATH\_raw\tradingview\<INTERNAL>\<YYYY-MM-DD>.json`, where
   `YYYY-MM-DD` is today's date in US Eastern time. Verbatim means the complete JSON object
   exactly as the tool returned it: no reformatting, no summary, no added or dropped keys, no
   rounding. Create the folders if they do not exist. Do not print the result back.
4. **Run the wrapper**, exactly this command and nothing else:
   `cmd //c "REPLACE_WITH_SCHEDULER_DIR_FORWARD/run-series.cmd"`
   (forward slashes, and the DOUBLED slash before `c` is load-bearing: the Bash tool is
   Git Bash, which rewrites a lone `/c` into a Windows path before cmd sees it, so
   `cmd /c` arrives mangled and fails with a message like "'ode' is not recognized".
   Measured on the first supervised run, 2026-09-17. The allow rule matches this exact
   text.)
5. **Report** the wrapper's exit code and the lines the build printed, one per symbol. Then
   stop.

Never alter a number. Never write under any other path. Never run any other command. Do not
retry a refused build: a refusal is a bad file (a human's to look at) or a stale one (the next
routine's to fix).

## The symbols

From marketdata's `registry.yaml`, classes `Market Breadth` and `Options Sentiment`. The
registry is the authority; if it and this table differ, the registry wins, and this table is
due an edit.

| internal (folder name) | `symbol` for the call |
|---|---|
| `NASDAQ_FOMO_5D` | `INDEX:NCFD` |
| `SPX_FOMO_5D` | `INDEX:S5FD` |
| `NASDAQ_PCT_ABOVE_20D` | `INDEX:NCTW` |
| `NASDAQ_PCT_ABOVE_200D` | `INDEX:NCTH` |
| `SPX_PCT_ABOVE_200D` | `INDEX:S5TH` |
| `NASDAQ_NH52W` | `INDEX:HIGQ` |
| `NASDAQ_NL52W` | `INDEX:LOWQ` |
| `NYSE_NH52W` | `INDEX:HIGN` |
| `NYSE_NL52W` | `INDEX:LOWN` |
| `CBOE_PCC` | `USI:PCC` |
| `CBOE_PCCE` | `USI:PCCE` |

## What a good night looks like

The first routine reports exit code 0 and one `+1 bar(s)` line per symbol (or `already
current` on a symbol the vendor has not updated yet). The second reports exit code 0 and
`already current` for every symbol. A `REFUSED` line names the file and the bar; leave the
file where it is and read the message, because the build never rewrites a stored bar and the
disagreement is either a vendor restatement or a transcription slip, and only a person can say
which. Next morning `verify-scheduling.ps1` reports the series freshness row green.

## Backfill, once, and not through the routine

A first fill of history is not a job for the routine: the routine writes what the model
returns, and with no stored bars for the overlap guard to check against, nothing would catch
a slip in a five-thousand-bar transcription. Two exact paths exist, and both feed the same
build and the same guards. Done 2026-09-17 by the first.

**Persisted tool results, from any Claude session.** A `get_ohlcv` call with `count` `5000`
returns more than the harness's tool-result limit, so the harness saves the connector's
response VERBATIM to a file under the session's `tool-results` directory before the model
sees it. That file is the raw file: copy it, unchanged, to
`REPLACE_WITH_MARKETDATA_STORE_PATH\_raw\tradingview\<INTERNAL>\<YYYY-MM-DD>-backfill.json`
(over the share from a Mac, or locally on the box). No transcription and no export. Validate
each file with the build's own parser before placing it if you can (`parse_raw` and
`check_anchors` in `marketdata.providers.tradingview`); the build will refuse it anyway if
not. The `count` cap is 5000 bars, which reaches 2006 on the older series and is the whole
published history on the newer ones.

**TradingView's chart export, when a session is not to hand.** Open each symbol on a 1D
chart, scroll all the way back so the whole history has loaded (the export contains only
loaded bars), then Export chart data, CSV. Convert it with
`marketdata-update --tradingview-csv <file> --symbols <INTERNAL>`, one file per call, which
writes a raw file in the connector's shape.

Then, either way, one build with the gate off, since a backfill is a snapshot rather than
tonight's pull:

    marketdata-update --build-tradingview --expect-session none

The registry anchors are checked on that build: for `NASDAQ_FOMO_5D` the 2026-07-29 close must
read 48.05. Two vendor conventions the build handles on its own, seen on that first fill: a
count of zero is printed as 0.01 and is stored as 0, and a put/call ratio bar with a zero
close is a vendor hole, dropped and named in the output. Then let the evening routines take
over; from the next night on they only ever add today's bar.
