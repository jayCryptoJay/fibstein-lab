# Research memory, run cards and the command line

Research memory sits beside the exact engine. Phase 2 step 1 changes selection
in `experiments.py`; step 2 adds versioning and final-holdout controls without
changing engine fills, market-data validation, strategies or Config.

| File | What it is |
|---|---|
| `backend/runcard.py` | Pure. Turns one saved run into a JSON card, a short Markdown digest and a list of fixed-rule findings. |
| `backend/holdout.py` | Locks fresh final dates, consumes attempts atomically and exposes only pass/fail. |
| `backend/registry.py` | Strategies, studies, trials, a holdout ledger and a journal, in `workspace/lab.sqlite3`. |
| `backend/causality.py` | Look-ahead check for any registered strategy. |
| `backend/lab.py` | Command line over all of the above: `python -m backend.lab --help`. |

The app shows the same data in the **Library** tab and in the **What the numbers
say** panel on every result.

## What gets recorded

Every saved run becomes one **study**, and every configuration it evaluated becomes
one **trial**, in the same database transaction as the run itself.

| Run kind | Trials |
|---|---|
| Backtest | One. |
| Compare | One per strategy compared. |
| Grid, walk-forward | One per distinct candidate, however many folds evaluated it. |
| Cost stress | One. The multipliers are assumptions about a single configuration, not rival candidates. |
| Final holdout | One, recorded before data access, even if evaluation fails or is interrupted. No performance metrics are stored. |

A trial is identified by its strategy version and a hash of its full, validated
settings. Rerunning identical settings on identical code does not add a trial; a grid's `stop_atr: 1` and a saved `1.0` are the same
trial. A strategy seen for the first time is registered automatically as a draft so
its trials are never dropped.

**Best of N** is the number of distinct trials across a strategy's whole lineage:
its root ancestor and every descendant, archived ones included. Nothing in the
registry deletes a trial.

Runs saved before the registry existed are picked up the first time their card is
read, or all at once with `python -m backend.lab sync`.

## Status and gates

`draft` → `candidate` → `promoted`, or `archived` from anywhere.

A status only moves through its gates. There is no override, in the command line,
the API or the app.

| Gate | Needed for | Passes when |
|---|---|---|
| `held_out_trades` | candidate | The latest walk-forward or grid run has at least 100 held-out trades. |
| `profitable_after_costs` | candidate | Its held-out net result is above zero. |
| `survives_doubled_costs` | candidate | Raw result minus twice the total execution cost is above zero. A linear estimate with fills unchanged, not a rerun. |
| `no_liquidations` | candidate | No held-out trade ended in liquidation. |
| `engine_unchanged` | candidate | The run was produced by the code that is on disk now. |
| `overfitting_checks` | promoted | Pending. Deflated Sharpe ratio and probability of backtest overfitting arrive in phase 2. |
| `final_holdout` | promoted | A consumed final window passed for the current strategy version and the same development evidence run. Legacy ledger entries cannot satisfy it. |

Two consequences worth knowing:

- **Nothing can be promoted yet.** Overfitting checks remain pending. Final
  evaluation refuses to consume a window until every other promotion gate passes.
- **Evidence is the latest held-out run, not the best one.** Choosing the best of
  several walk-forwards is the selection bias the gates exist to stop.

Archiving needs one of six reasons: `too_few_trades`, `cost_fragile`, `overfit`,
`unstable_params`, `failed_holdout`, `superseded`. An archived strategy can be
reopened as a draft; both moves are written to the journal.

## The run card

`python -m backend.lab card <run>` or `GET /api/runs/<run>/card`. Schema
`fibstein.runcard/1`, a few kilobytes.

| Field | Contents |
|---|---|
| `run` | ID, kind, created, strategy version, engine fingerprint, and whether that fingerprint is current. |
| `strategy` | Registry entry: status, origin, parent, hypothesis, archive reason. |
| `setup`, `changed_from_default` | Pairs, dates, timeframe and execution settings; then only the settings that differ from defaults. |
| `verdict` | The same plain-language verdict the app shows. |
| `splits` | Each date range with its key numbers, marked held out or not. Walk-forward lists every fold's training winner and test. |
| `rows` | Compare and stress runs: one entry per row. |
| `costs` | Raw result, each cost, total, net, cost share and breakeven multiple. |
| `breakdown` | Net result and trade count by pair, side, regime and exit reason, worst first. |
| `signals` | Signals seen, entered, refused by reason, and not acted on. |
| `selection` | Trials in this run, for this strategy and across the lineage, with the best-of-N label. |
| `parent` | Change in key numbers against the parent's latest run of the same kind, and whether the two are like for like. |
| `holdout` | Status only. Final-holdout numbers are never exposed here. |
| `findings` | Fixed-rule facts, ordered problem, caution, info. |
| `warnings` | Every modelling assumption from the run, in full. |

Numbers are copied from the engine's output and rounded for reading. Exact values
stay in the full export.

The **digest** (`lab digest <run>` or `/api/runs/<run>/digest`) is the same card as
about forty lines of Markdown. It shows the first sentence of each assumption; the
card keeps the full text.

## Findings

Rules live in `runcard.findings`, with thresholds as constants at the top of the
file. They read the engine's numbers; they do not recompute performance. Current
rules: liquidations, sample size, losing before costs, cost headroom, largest cost
component, loss concentration by regime or pair (only when a bucket's share of
losses exceeds its share of trades by 15 points), profit from a single regime or
pair, long/short asymmetry, refused and ignored signals, time-limit exits, losers
that were 1R in profit first, fold consistency, training-to-held-out decay, a fold
that deployed a candidate which lost in training, selection stability across
folds, cost stress, and same-period ranking.

Adding a rule is safe work in the same sense as adding a verdict: it is pure, and
`tests/test_runcard.py` shows the pattern.

## Command line

```bash
python -m backend.lab runs                      # saved runs, newest first
python -m backend.lab digest latest             # Markdown digest (ID, unique prefix, or "latest")
python -m backend.lab card 3f2a                 # JSON card
python -m backend.lab run --preset sample-jto   # exact engine, saved, digest printed
python -m backend.lab run --preset sample-jto --mode walkforward \
    --set start=2024-10-01 --grid grid.json     # grid.json: {"stop_atr":[1,1.5,2],"reward_risk":[1.5,2]}
python -m backend.lab library                   # every strategy, trial counts, latest held-out result
python -m backend.lab strategy add pullback_long --parent trend_pullback --origin ai --hypothesis "..."
python -m backend.lab strategy show trend_pullback
python -m backend.lab gates trend_pullback
python -m backend.lab promote trend_pullback    # one step, only if the gates for it pass
python -m backend.lab archive pullback_long --reason overfit --note "..."
python -m backend.lab reopen pullback_long
python -m backend.lab note trend_pullback "..."
python -m backend.lab journal                   # read this before proposing a new idea
python -m backend.lab check trend_pullback      # look-ahead check
python -m backend.lab sync                      # record saved runs the registry has not seen
```

`library`, `journal`, `gates`, `runs`, `strategy` and `check` accept `--json`.
`--set` takes plain values (`stop_atr=2`, `start=2024-10-01`). `--grid` takes inline
JSON or the path of a file containing it; a file avoids shell quoting on Windows.

Use the project's own interpreter: `.venv\Scripts\python.exe -m backend.lab ...` on
Windows, `.venv/bin/python -m backend.lab ...` elsewhere.

## API

`GET /api/runs/{id}/card`, `GET /api/runs/{id}/digest`, `GET /api/library`,
`GET /api/library/{key}`, `POST /api/library` (record an idea),
`POST /api/library/{key}` with `{"action": "promote" | "archive" | "reopen" | "hypothesis" | "note", "reason": ..., "text": ...}`.

## Known limits

- The engine fingerprint is byte-exact and covers `custom_strategies.py`. Editing
  any custom strategy, or changing line endings on the engine files, marks every
  strategy's earlier evidence as produced by older code. Per-strategy version IDs
  retain this conservative binding; there is no inferred dependency graph.
- `survives_doubled_costs` is a linear estimate. The cost-stress experiment reruns
  sizing and fills but multiplies spread and slippage only, and runs in-sample.
- Final evaluation remains blocked by the pending overfitting checks. This step
  does not implement or bypass them, or replace the linear doubled-cost estimate.
- A walk-forward result inherits the last fold's "Fewer than 100 trades" warning
  even when the held-out total is above 100. That comes from `experiments.py` and
  is left as it is; the card's `sample_size` finding uses the held-out total.

## Phase 2, step 1: rejected training sets

A fold with no qualifying training candidate has `selected: null`, `status: no_trade`
and a `no_trade_reason`. Its held-out equity and zero-trade metrics come from the
exact engine with signals disabled. Every training candidate is still recorded,
with no selected fold for a skipped window. Run cards expose the skip in both
splits and findings; cash folds are not parameter winners. Selection requires the
trade minimum and positive finite net P&L and expectancy R, ranked by expectancy R.

## Phase 2, step 2: versions and final holdout

`strategy_versions` assigns a deterministic content identity to each strategy key
and byte-exact engine digest. Studies and trials retain that identity. Migration
uses each historical run's stored digest; missing digests remain unknown, never
current. Changed code with the same settings counts as another trial. Historical
rows and archived lineages are retained. The engine fingerprint itself is
unchanged, including its sensitivity to CRLF and all of `custom_strategies.py`.

A final window is immutable and reserved once per strategy version. Locking it
freezes the latest development run and its last fold's training-selected settings
with the original balance. It never selects using test metrics. A last fold with
no selected candidate cannot be deployed. Final dates must follow the complete
development period and must not overlap previously logged access, saved studies
(including warmup), another reservation, or a legacy holdout attempt. Saved runs
are synced first; unreadable runs make freshness unverifiable and block locking.

The normal API/CLI run, download and CSV-import paths log data access before work.
A SQLite write transaction serializes this with locking. Failed jobs still make
their dates ineligible as fresh holdout data. Ordinary research cannot read a
reserved window, including through indicator warmup or another strategy. These
controls apply across all strategies, conservatively, because the owner can
learn from any displayed result. They are application controls, not protection
against directly reading cache files, calling engine functions or editing SQLite.
Existing off-app data inspection cannot be discovered automatically.

Evaluation requires unchanged code, unchanged development evidence and every
other promotion gate passing. There is no override. Before any final data access,
an immediate SQLite transaction writes a consumed attempt plus a study/trial
through `registry.record_run`. Concurrent callers cannot claim the same window.
Crashes and interrupted attempts remain consumed with a failing public status.
Archived/reopened strategies, child variants and code versions do not reset the
lineage attempt count. A changed version needs genuinely fresh, nonoverlapping
dates; it cannot reuse even an abandoned reserved window.

The exact `run_backtest` engine evaluates the frozen configuration. Pass requires
at least 100 trades, positive finite net P&L and expectancy R, zero liquidations,
and an unchanged engine digest at completion. It returns only `passed` or
`failed`. Metrics, trades, equity and engine exception text are not saved to
ordinary run files, cards, exports, studies or journal. Lifecycle states before
an attempt are `not_configured` and `locked`; `uses` counts every attempt across
the lineage, including legacy, failed and interrupted entries. Model assumptions
remain those documented for the unchanged exact engine.

```bash
python -m backend.lab strategy show trend_pullback --json
python -m backend.lab holdout lock trend_pullback --start 2025-03-01 --end 2025-04-01
python -m backend.lab holdout show trend_pullback
python -m backend.lab holdout evaluate <locked-window-id>
```

Dates above are examples and must actually be fresh for the workspace. Evaluation
currently refuses because overfitting checks remain pending; it does not burn an
attempt in that case. API equivalents: `POST /api/holdouts` with strategy/start/end,
`GET /api/holdouts/{strategy}`, and `POST /api/holdouts/{window}/evaluate`. The last
uses the existing job queue and returns only the decision. Strategy detail JSON
includes version history and lineage holdout status. No new dependency is added.
