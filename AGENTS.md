# AGENTS.md

Read this before changing anything. It exists because this project has a few
invariants that look like ordinary code but are the entire reason the tool is
worth using. Breaking one produces a backtester that still runs, still returns
plausible numbers, and is quietly wrong — the exact failure this project exists
to avoid.

If you are an AI assistant picking up work here, the short version: the engine
and `data.py` are load-bearing and conservative by design. Prefer the frontend,
docs, and additive modules. When in doubt, write a test and ask.

---

## What this is

A local-first backtester for linear USDT perpetual futures. Python engine,
FastAPI server, React frontend, runs on the user's machine. Its differentiator
is not strategy logic — it is honest cost accounting and a refusal to present
in-sample results as evidence.

```
backend/
  config.py        Pydantic settings. Every knob, validated, with units in the name.
  data.py          Download, checksum, validate, cache candles + funding. Strict.
  strategies.py    Causal indicator prep + three built-in strategies.
  engine.py        Event-driven simulation. The core.
  experiments.py   Walk-forward, grid, compare, cost-stress.
  verdict.py       Turns a metrics dict into one plain-language sentence.
  runcard.py       Turns a saved run into a JSON card, a Markdown digest and fixed-rule findings. Pure.
  registry.py      Research memory: strategies, studies, trials, gates, journal. Never deletes.
  causality.py     Look-ahead check for any registered strategy.
  pine.py          Runs a pasted Pine Script v5/v6 strategy candle by candle and turns its orders into a plan.
  holdout.py       Final holdout: lock fresh dates, check data, consume one attempt, report pass or fail only.
  lab.py           Command line over the three above: python -m backend.lab --help
  server.py        HTTP API, job queue, run persistence.
  paths.py         Where the program's files are and where the user's research is kept (FIBSTEIN_HOME).
frontend/src/      React. Settings (left rail), Results (main), Views (data/compare/docs).
tests/             251 tests. All must pass before any commit.
presets/           Saved configurations, including the bundled sample.
packaging/         PyInstaller recipe for the one-folder app. Built and tried by scripts/build_app.py.
data/              Cached candles. NOT tracked in git. See "Data" below.
```

## Running it

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest tests -q     # expect: 251 passed
.venv/bin/python launch.py              # serves the app
```

Frontend dev: `cd frontend && npm install && npm run dev`.

---

## Invariants — do not break these

**1. Causality. Every signal is indexed at the time it becomes available.**

In `strategies.py`, `features()` does `f.index = f.index + Timedelta(minutes=timeframe)`.
That single line is what makes a signal derived from a bar available only *after*
that bar closed. Higher-timeframe filters are shifted the same way before being
forward-filled. If you remove or "simplify" either shift, the backtest starts
trading on information it could not have had, and results will look excellent.
Any new strategy must return a Series aligned to the already-shifted index.

**2. The cost identity must stay exact: `net_pnl == raw_pnl - fees - slippage - spread - funding - liquidation_fee`.**

`verdict.py` and the cost rail in the UI both depend on this reconciling to the
cent. It currently does. If you touch fill pricing, fee application, or funding
settlement, verify the identity numerically before committing — a test that only
checks "net is negative" will not catch a double-counted spread.

**3. Parameter selection must never see the held-out window.**

`experiments.py` picks a winner using training-period metrics only, then runs the
held-out window with those parameters. `tests/test_walkforward_selects_training_winner_only`
guards this. Do not "improve" selection by scoring on test metrics, and do not
let a fold's test range leak into the next fold's candidate ranking.

**4. Never fill a missing market candle.**

`aggregate()` drops any aggregated bar that does not contain the full count of
1-minute candles, and `load_pair` refuses a range with missing minutes rather
than interpolating. Gaps are real information about the market. Do not add
`ffill`, `interpolate`, or a "tolerate small gaps" flag.

**5. The warnings list is a feature, not noise.**

`simulate()` returns explicit statements about where the model approximates the
exchange — liquidation, spread, depth, funding timestamps, drawdown resolution.
Do not delete them to make output cleaner. They may be collapsed in the UI (they
are), but they must remain in the payload and in exports.

**6. No candle data in git.** See below.

**7. Every trial is counted, and none is ever removed.**

Saving a run records it as a study and its candidates as trials, in the same
transaction (`server.persist_result` → `registry.record_run`). "Best of N" is
counted across a strategy's whole lineage, archived variants included. Do not add
a delete, a reset, or a way to save a run without recording it, and do not let a
status move without passing its gates: `registry.promote` has no override on
purpose. See `docs/RESEARCH-MEMORY.md`.

**8. A final holdout is spent once and shows only pass or fail.**

`holdout.evaluate` consumes the attempt before the engine starts, so a crash or a
cancel cannot become a retry, and it never saves or returns a metric. Do not add a
retry, a result export, or a way to run ordinary research on a live lock's dates.
The other direction matters as much: do not consume an attempt for a reason that is
not the strategy's fault. Gates and data coverage are checked first, and a lock that
was never evaluated can be released. See `docs/RESEARCH-MEMORY.md`.

---

## Data

Candles live in `data/` and are downloaded on demand from Binance's public
archive, with SHA256 verification against the published checksum. A full set is
~200MB across 2,000+ files.

`.gitignore` excludes `data/` entirely. Do not commit candle files, funding CSVs,
or `.meta.json` sidecars, and do not add a "just this one small dataset"
exception. Anyone can regenerate the cache from the Data tab in a few minutes.

If you need fixture data for a test, generate it in the test (see `tests/` for
the existing `day()` helper) rather than committing a file.

---

## Known issues

**A Pine script is refused, never bent to fit.** `pine.py` runs a script one
completed candle at a time, so it cannot look ahead by construction, and it refuses
with a line number whatever the engine cannot honour (resting entries, partial
exits, another symbol, anything that reads the account). Do not turn a refusal into
a quiet approximation. A new built-in function needs a test against an independent
calculation in `tests/test_pine.py`. The interpreter is part of the engine
fingerprint, and each saved script's key carries a hash of its logic, so an edited
script is a new strategy whose trials are counted with its parent.

**Research data is never inside the program folder of an installed app.** A source
checkout keeps `workspace/` and `data/` in the project, as always. The installed
app keeps them in a per-user folder (`backend/paths.py`), so replacing the app
cannot lose the registry. Do not write user data under `paths.APP`. Release builds
fetch their sample candles at build time with the checksum-verified downloader;
invariant 6 still holds for the repository.

**Engine results are pinned.** `tests/test_engine_golden.py` holds results recorded
from the v1.0.0 engine for the three built-in strategies. Signal exits were added
without moving them, and 27 real-data scenarios (5,105 trades) matched the old
engine trade for trade. An engine change that moves these numbers has changed
behaviour for strategies that did not ask for it.

**pandas 3.x rejects all valid data.** `data.py` line 42 checks minute alignment
with `f.index.asi8 % (60*10**9)`, which assumes nanosecond resolution. On pandas
3.0, `to_datetime(unit='ms')` returns `datetime64[ms]`, so `asi8` is in
milliseconds and every candle file is rejected as misaligned. Fix is
`f.index.as_unit('ns').asi8`. Four tests also assume ns via
`astype('int64')//10**9` and need the same treatment. Requirements currently pin
pandas 2.2.3, so this is latent, not live.

**Selection safety (phase 2, step 1).** Training candidates must meet the trade
minimum and have positive finite net P&L and expectancy R. When none qualifies,
the held-out fold runs through the exact engine with signals disabled. It remains
in cash and every attempted candidate still counts.

**Selection score.** Candidates are ranked by `expectancy_r`, with deterministic
grid-order tie breaking. Held-out metrics never participate in ranking.

---

## Safe to work on

These are additive, well-isolated, and hard to get catastrophically wrong:

- Frontend components, styling, responsive behaviour, accessibility.
- Copy: labels, hints, empty states, error messages, methodology docs.
- New entries in `verdict.py` — it is pure, takes a metrics dict, returns a dict.
- New findings in `runcard.py` — also pure. A finding reads the engine's numbers;
  it never recomputes performance.
- New strategies via `register_strategy()` in `strategies.py`, provided the
  returned Series respects invariant 1. Prove it: `python -m backend.lab check <key>`.
  A strategy may return a plan frame instead, with its own exits, stops and targets
  (see `register_strategy`); the same check covers every column of it.
  Pine scripts are added in the Pine tab or with `python -m backend.lab pine add`; see `docs/PINE-CONVERSION.md`.
- Tests. More of them, especially around cost accounting.
- Anything under `docs/`.

## Ask before touching

- `engine.py` — fill pricing, intrabar ordering, liquidation, sizing.
- `data.py` — validation rules, caching, funding alignment.
- `experiments.py` — fold boundaries, candidate selection.
- `pine.py` — how a script is executed and how its position is modelled. A change here changes every saved script's results.
- `requirements.txt` version pins.

## Style

Dense but readable. Existing code favours compact expressions and short names;
match the file you are editing rather than reformatting it. Comments explain
*why*, never *what* — the existing one-line comments above tricky blocks in
`engine.py` are the model. No emoji in code, commits, or UI copy.

Every change: run `pytest tests -q` and confirm 251 passing before you commit. Every pull request also
runs the suite on Linux, Windows and macOS (`.github/workflows/tests.yml`); a red check there is a finding, not noise. If your
environment cannot install the pinned dependencies, say so in the pull request and
keep it draft until someone has run the full suite.

Before proposing a new strategy or variant, read the journal
(`python -m backend.lab journal`) so an archived idea is not retried, and read
`docs/RESEARCH-PLAN.md` for what is built and what still needs approval.

## Brand

FibStein Research. φ-as-candlestick mark, prismatic spectrum used as a data
scale rather than decoration, Fraunces for editorial voice, Inter for interface,
JetBrains Mono for every figure. Numbers are always set in the mono face,
including inline in prose. Dark ground, sentence case, no all-caps labels.
