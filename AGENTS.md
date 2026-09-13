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
  server.py        HTTP API, job queue, run persistence.
frontend/src/      React. Settings (left rail), Results (main), Views (data/compare/docs).
tests/             62 tests. All must pass before any commit.
presets/           Saved configurations, including the bundled sample.
data/              Cached candles. NOT tracked in git. See "Data" below.
```

## Running it

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest tests -q     # expect: 62 passed
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

**pandas 3.x rejects all valid data.** `data.py` line 42 checks minute alignment
with `f.index.asi8 % (60*10**9)`, which assumes nanosecond resolution. On pandas
3.0, `to_datetime(unit='ms')` returns `datetime64[ms]`, so `asi8` is in
milliseconds and every candle file is rejected as misaligned. Fix is
`f.index.as_unit('ns').asi8`. Four tests also assume ns via
`astype('int64')//10**9` and need the same treatment. Requirements currently pin
pandas 2.2.3, so this is latent, not live.

**Walk-forward can deploy a losing candidate.** If every candidate loses in
training, `eligible[0]` is simply the least-bad and still gets traded
out-of-sample. Adding a positive-expectancy floor is a wanted improvement.

**Selection score is scale-dependent.** `net_return_pct - max_drawdown_pct`
favours candidates that happened to take more trades. `expectancy_r` would be
less biased.

---

## Safe to work on

These are additive, well-isolated, and hard to get catastrophically wrong:

- Frontend components, styling, responsive behaviour, accessibility.
- Copy: labels, hints, empty states, error messages, methodology docs.
- New entries in `verdict.py` — it is pure, takes a metrics dict, returns a dict.
- New strategies via `register_strategy()` in `strategies.py`, provided the
  returned Series respects invariant 1.
- Tests. More of them, especially around cost accounting.
- Anything under `docs/`.

## Ask before touching

- `engine.py` — fill pricing, intrabar ordering, liquidation, sizing.
- `data.py` — validation rules, caching, funding alignment.
- `experiments.py` — fold boundaries, candidate selection.
- `requirements.txt` version pins.

## Style

Dense but readable. Existing code favours compact expressions and short names;
match the file you are editing rather than reformatting it. Comments explain
*why*, never *what* — the existing one-line comments above tricky blocks in
`engine.py` are the model. No emoji in code, commits, or UI copy.

Every change: run `pytest tests -q` and confirm 62 passing before you commit.

## Brand

FibStein Research. φ-as-candlestick mark, prismatic spectrum used as a data
scale rather than decoration, Fraunces for editorial voice, Inter for interface,
JetBrains Mono for every figure. Numbers are always set in the mono face,
including inline in prose. Dark ground, sentence case, no all-caps labels.
