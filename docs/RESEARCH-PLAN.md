# Research plan: Pine, mass testing, research memory, AI loop

A standing plan so a new session starts from the same place. The prompt below is
the brief; the rest is what running it found and what has been built since.
`AGENTS.md` overrides anything here.

## Status

| Phase | Scope | State |
|---|---|---|
| 1 | Run cards, findings, command line, registry with gates and archive reasons, Library view, look-ahead check, Pine conversion guide | **Built** (2 October 2026). 102 tests. See `RESEARCH-MEMORY.md`, `PINE-CONVERSION.md`. |
| 2 | Parallel runs, Optuna studies, `strategy_params`, locked holdout, overfitting gates, the two selection fixes | Steps 1 and 2 (selection fixes; versioning and final holdout) approved and implemented, 5 October 2026: 115 and 157 tests, verified on Python 3.11 and 3.12. `strategy_params`, parallel runs, Optuna and the overfitting gates are not approved yet. |
| 3 | Signal exits, long/short flips, per-signal stop and target prices, TradingView parity harness, optional faster engine | Not started. Needs approval: touches `engine.py`. |
| 4 | MCP server with a budgeted autonomous loop; XGBoost meta-labeling | Optional. |

Phase 1 decisions that differ from, or sharpen, the plan as first written:

- **Promotion is locked until phase 2.** `draft → candidate` works today on held-out
  evidence. `candidate → promoted` also needs the overfitting checks and the locked
  holdout. The holdout service is now implemented, but refuses evaluation until
  the still-pending overfitting checks pass. Promotion refuses. There is no override.
- **"Still profitable at 2× costs" is a linear estimate** from the held-out run
  (raw result minus twice total costs), not a rerun. A true rerun at doubled costs
  on the held-out windows belongs with the experiments work in phase 2.
- **Evidence is the latest held-out run, never the best.**
- **Pine conversion is on demand.** No script has been converted yet; the guide,
  the look-ahead check and the registry origin field are in place. Send a script.

Things noticed while building, left alone because they sit in protected files:

- The engine fingerprint includes `custom_strategies.py`, so adding any custom,
  Pine or AI strategy marks every strategy's earlier evidence as stale. The AI loop
  now has per-strategy version identities, conservatively bound to this same raw digest.
- A walk-forward result inherits the last fold's "Fewer than 100 trades" warning
  even when the held-out total is over 100.
- The first selection issue in `AGENTS.md` shows up on real data: on JTOUSDT,
  1 October 2024 to 1 February 2025, every candidate lost in training in all three
  folds and the least-bad one was traded anyway. The card now reports this as a
  finding; phase 2 step 1 now keeps these folds in cash.

## The brief

```text
Context
FibStein Lab is a local backtester for USDT perpetual futures (Python engine,
FastAPI, React). Read AGENTS.md first; its invariants override this prompt.

Goal: design, then build in phases, four capabilities
1. Pine Script: run or convert TradingView Pine strategies in FibStein.
2. Mass testing: evaluate thousands of parameter sets quickly (vectorbt-style),
   using ML such as XGBoost only where it genuinely helps.
3. Research memory: record every idea and trial; promote what passes objective
   gates, archive what fails with a reason, never lose count of what was tried.
4. AI loop: a compact, machine-readable view of results so an AI can explain
   them and propose the next improvement, inside guardrails.

Hard constraints
- Causality: signals are indexed at bar-close availability time. Converted Pine
  must not see the future (refuse request.security lookahead without a [1] offset).
- Cost identity stays exact: net = raw - fees - slippage - spread - funding - liq fee.
- Selection never sees held-out data. At scale: a locked final holdout that no
  optimizer, person or AI sees before promotion; evaluated once per strategy
  version and logged.
- Headline numbers come only from the exact engine; fast screening may only filter.
- No filled candles, no deleted warnings, no candle data in git.
- engine.py, data.py, experiments.py, requirements.txt pins: propose, then wait.
- Dependencies must be MIT/BSD/Apache-compatible (no AGPL; flag Commons Clause)
  and must not pull pandas to 3.x.
- Keep all 62 tests passing; add tests for every new behaviour.

Questions to answer
1. Pine: compare AI-assisted conversion to strategy plugins, the PyneCore runtime
   and a parser-based transpiler. Classify ta.*, input.*, strategy.entry/close/exit
   and request.security as supported, approximated, needs engine change, or refused.
   Strategies currently emit entries only, so specify the smallest engine extension
   for signal exits, reversals and per-signal stop/target prices. Verify with a
   parity harness against TradingView's exported List of Trades (same symbol,
   timeframe, dates, costs off): >=95% matching entries after warmup, every
   mismatch explained.
2. Mass testing: measure engine throughput first. Evaluate parallel processes,
   Optuna, a vectorized screener, and engine acceleration (only behind a test
   proving identical results). Say where XGBoost actually fits. Require trial
   counting, deflated Sharpe ratio, probability of backtest overfitting (CSCV),
   parameter-plateau checks, 2x/3x cost stress, and fixes for the two known
   selection issues in AGENTS.md.
3. Research memory: extend workspace/lab.sqlite3 with strategies (lineage,
   hypothesis), studies, trials and a holdout ledger. Define promotion gates and
   archive-reason codes. Archived work is kept and still counts toward trial totals.
4. AI loop: a versioned JSON run card plus a short Markdown digest, deterministic
   "findings" rules, a CLI, later an MCP server. Guardrails: the AI adds strategy
   files and parameter studies only, cannot edit the engine, sees holdout results
   only as pass/fail, and every attempt is logged as a trial against a budget.

Deliverable
A phased plan ordered by risk (safe now vs needs approval), with files, tests and
acceptance criteria per phase, and sources for library, licence and method claims.
```

## What shaped the plan

- **Speed.** One CPU core runs a 90-day backtest in about 2 s and a one-year
  backtest in about 10 s per pair. About 95% of that is the minute-by-minute loop,
  so 1,000 one-year candidates take about three hours. Trying every combination
  does not scale on its own.
- **Exits.** Strategies generate entries only. Exits come from settings: ATR stop,
  reward-to-risk target, time limit, trailing stop, breakeven. New signals are
  ignored while a position is open. Most Pine strategies close on a signal or flip
  sides, so this is the main obstacle for Pine.
- **What was already there.** `simulate()` accepts signals generated outside
  FibStein. Every run was already saved to SQLite and compressed JSON, tagged with
  a fingerprint of the engine code.

## 1. Pine Script

Convert each script into a normal FibStein strategy, then check it against
TradingView.

| Option | What it is | Licence | Verdict |
|---|---|---|---|
| AI-assisted conversion | Rewrites the logic as a `register_strategy` plugin, with a report of what mapped, what was approximated and what was refused | none | Recommended: no new dependency, uses FibStein's own cost model |
| PyneCore | Python runtime that reproduces how Pine executes bar by bar | Apache-2.0; its Pine-to-Python compiler is a closed, paid API | Useful second opinion for tricky indicators |
| pynescript | Parses Pine into a structure it can inspect; does not run it | LGPL-3.0 | Later, to flag unsupported features automatically |
| PineTS, PYNE | Full Pine runtimes | AGPL-3.0 | Avoid: bundling them would force FibStein under AGPL |

- Converts today: strategies whose exits fit FibStein's model.
- Needs the phase 3 engine extension: signal exits and long/short flips.
- Refused: `request.security` lookahead without a `[1]` offset.
- Parity check: export the List of Trades from TradingView for the same symbol
  (such as `BINANCE:SOLUSDT.P`), timeframe and dates with costs off, and compare
  trade by trade. Third-party guides say the export needs a paid plan.
- Tuning Pine inputs needs a new `strategy_params` setting (phase 2).

## 2. Testing a lot at once

Four levers, safest first.

1. **Parallel processes.** Identical results, roughly one run per CPU core at once.
   Today one job runs at a time and candidates run one after another.
2. **Optuna (MIT).** Concentrates on promising parameter ranges, typically a few
   hundred trials where a full grid needs thousands. Logs every trial to SQLite.
3. **Screen fast, confirm exactly.** A small numpy screener scores thousands of
   stop, target and hold combinations in seconds; only the top twenty or so go
   through the real engine, and only real-engine numbers are ever reported. Built
   in-house: vectorbt's Commons Clause restricts commercial use, and it would not
   reproduce FibStein's funding and liquidation model.
4. **Faster engine** (numba, for example), only behind a test proving results
   identical to today's engine on thousands of random scenarios.

**Where XGBoost fits.** It predicts outcomes; it does not search for parameters.
Two later uses: learning which of a strategy's signals to skip (meta-labeling), and
explaining which parameters drive results. Both are valid only if the model is
trained strictly inside each walk-forward training window. Optuna's parameter
importance already covers the second.

**The cost of testing thousands: the best result is increasingly luck.** Required
controls: count every trial and label the winner best of N (built); deflated
Sharpe ratio; probability of backtest overfitting; a plateau check that
neighbouring parameter values also work; cost stress at 2× and 3× (exists); a
locked final holdout used once per strategy version; and the two selection fixes
in `AGENTS.md` first, because thousands of candidates make both worse.

## 3. Research memory

Built in phase 1. See `RESEARCH-MEMORY.md` for the tables, gates and reason codes.

## 4. AI loop

- **Run card and digest:** built.
- **Findings:** built, as fixed rules in the style of `verdict.py`.
- **Research journal:** built. `python -m backend.lab journal` is read before
  proposing anything, so archived ideas are not retried.
- **Access:** command line built; MCP server is phase 4.
- **Guardrails still to build:** the AI may add strategy files and parameter
  studies only, cannot edit the engine, sees final-holdout results only as pass or
  fail (the card already exposes status only), and every attempt is logged as a
  trial (built) against a budget (not built).

This speeds up research. It cannot create an edge: the more that is tried, the
higher the bar the winner must clear.

## Sources

Collected in the planning session; licence terms can change, so recheck before
adding a dependency.

[PyneCore](https://github.com/PyneSys/pynecore) ·
[PyneCore: converting from Pine](https://pynecore.org/docs/getting-started/converting-from-pine/) ·
[pynescript](https://github.com/elbakramer/pynescript) ·
[PYNE](https://github.com/hoox-sh/pyne) ·
[PineTS](https://github.com/LuxAlgo/PineTS) ·
[vectorbt licence](https://vectorbt.dev/terms/license/) ·
[TradingView: export strategy data](https://www.tradingview.com/support/solutions/43000613680-how-to-export-strategy-data/) ·
[TradingView export plans (third-party)](https://www.backtestbase.com/education/tradingview-export-guide) ·
[Deflated Sharpe Ratio](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551) ·
[Probability of Backtest Overfitting](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253)

Step 1 verification: 10 new standard-library unittest cases pass on Python 3.12,
including exact-engine synthetic cash folds. Full pytest verification remains
pending because pinned test/server dependencies are unavailable in this environment.
No real-market data rerun and no Windows execution were performed.

## Phase 2, step 2 verification and review decisions

Version identities, migrations, locked final windows and lineage-wide attempt
counting are implemented. The step-2 PR is stacked on the separate step-1 branch.
No Optuna dependency or work from subsequent steps is included.

Expected test inventory: 143 (102 baseline, 10 step-1 cases, 31 step-2 cases).
Local Python 3.12 verification: 37 new cases passed, 4 API cases skipped for missing
FastAPI. `python -m pytest tests -q` fails before collection: pytest is unavailable
and pinned dependency installation is blocked. No GitHub workflow run was
available for the step-1 commit when checked. Both PRs must remain draft until
their full suites pass. No Windows or real market-data verification was performed.
The exact engine was exercised on synthetic candles for cash folds and holdout.

Owner review: final settings are the last development fold's training winner,
with the original starting balance. The final criterion is at least 100 trades,
positive finite net P&L and expectancy R, and zero liquidations. All other
promotion gates must pass before a final attempt can be consumed. Code versions
are conservative: an unrelated custom-code edit still invalidates evidence.
Reserved periods cannot be reused even by another strategy; access guards cover
the supported API/CLI and are not a sandbox against local code/database edits.
See RESEARCH-MEMORY.md for the precise protocol and commands.
