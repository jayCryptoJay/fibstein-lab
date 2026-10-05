# Release validation

Validated in Linux with Python 3.12 and local headless Chromium. Native Windows, Docker and Safari were not independently tested.

## Automated checks

62 tests cover accounting and cost attribution, funding signs and timing, risk and volume limits, stop/target ambiguity, gaps, liquidation calculations, trailing-stop timing, causal signals, prevention of future-capital reuse, data validation, CSV import atomicity, training-only parameter selection, complete-bar OHLCV aggregation on six resolutions, and recovery of interrupted archive caches.

The production frontend build succeeded. The release packaging script checks ZIP integrity and every packaged file against its SHA-256 manifest.

## End-to-end browser checks

Passed: initial render, bundled-data backtest, results and drawdown views, preset save, ten cached datasets, cost-stress experiment, methodology view, mobile settings changes, stale-result warning, and CSV export. No browser console errors were captured. Desktop viewport: 1536 × 1024. Mobile viewport: 390 × 844; document width remained 390 pixels.

Visual review checked the dashboard layout, graphite/purple/mint palette, typography, settings controls and spacing against the design concept. The bundled-data action and expandable settings are intentional functional additions. Screenshots were inspected; decorative mock window controls were omitted.

## Historical smoke tests

Using the bundled January 2025 Binance archive data and the January 15–February 1 sample window, the JTO trend template produced 53 trades and approximately −5.23% return. The ten-pair portfolio produced 271 trades and approximately −22.97%. Closed-trade net results reconciled with final cash. These are validation runs, not evidence of a profitable strategy.

Expansion check: April 2024 JTO backtests completed successfully with 5m, 15m, 30m and 1h signals using 1m execution and historical funding. They produced 154, 71, 50 and 37 trades respectively, with zero missing execution minutes. This checks data compatibility; it is not a strategy recommendation.

## Research memory additions (2 October 2026)

Validated in Linux with Python 3.11 and 3.12 and local headless Chromium. Native Windows was not independently tested.

102 tests pass: the original 62 unchanged, plus 40 covering trial counting and its idempotence, integer and float parameters hashing to one trial, lineage counts that include archived variants, each gate blocking on its own, promotion refusing while a gate is pending, evidence taken from the latest held-out run, run cards copying engine numbers and keeping every warning, each finding rule, the command line, and the look-ahead check catching a strategy that reads the next bar.

`engine.py`, `data.py`, `experiments.py`, `strategies.py` and `config.py` are byte-identical to the previous release, so the engine fingerprint is unchanged. Using real Binance archive data, the January 15 to February 1 JTO sample still produces 53 trades and approximately −5.23%.

End-to-end on real JTOUSDT data: a backtest, a six-candidate three-fold walk-forward, a cost stress and a strategy comparison were run through `python -m backend.lab run`; the registry counted 7 distinct trend-pullback trials across them, and the gates refused candidate status on a losing held-out result. In the browser at 1536 × 1024 and 390 × 844: the Library tab, recording an idea, adding a note, archiving, reopening, and the findings panel on backtest and walk-forward results. No console errors; document width matched the viewport.

## Selection fixes (5 October 2026)

Validated in Linux with Python 3.11 and 3.12 and local headless Chromium. Native Windows was not independently tested.

115 tests pass. `experiments.py` changed, so the engine fingerprint changed and earlier held-out evidence is marked as produced by older code. A candidate is now eligible only with the minimum training trades and positive training net P&L and expectancy in R; eligible candidates are ranked by expectancy in R; a fold with no eligible candidate stays in cash.

On real JTOUSDT data, 1 October 2024 to 1 February 2025, six candidates and three folds: every candidate lost in training on every fold, so all three held-out windows stayed in cash. Before this change the same run traded the least-bad candidate in each fold and lost 5.79%. The plain January 15 to February 1 backtest is unchanged at 53 trades and approximately −5.23%.

## Versioning and final holdout (5 October 2026)

Validated in Linux with Python 3.11 and 3.12 and local headless Chromium. Native Windows was not independently tested.

157 tests pass. No protected file changed in this step, so the engine fingerprint is the one set by the selection fixes.

On real JTOUSDT data with the exact engine, using fixture development evidence because no real configuration tried had an eligible training winner: a window whose month was not cached refused with "Nothing was consumed" and stayed locked; an ordinary run on the locked dates was refused; `holdout fetch` downloaded the window with warmup; the evaluation then ran once and could not be repeated. A lock made before a simulated code change refused to evaluate, was released, and its dates became usable again. A database created by the previous release migrated in place and kept its trial counts.

Evaluation through the real gates cannot be exercised yet: it requires the overfitting checks, which are not built.

## Signal exits and per-signal levels (5 October 2026)

Validated in Linux with Python 3.11 and 3.12. Native Windows was not independently tested.

183 tests pass. `engine.py` and `strategies.py` changed, so the engine fingerprint changed.

The change is additive: a strategy that returns a plain Series behaves exactly as before. This was checked two ways. On real JTOUSDT data, 1 October 2024 to 1 February 2025, 27 scenarios across the three built-in strategies (market and limit orders, isolated and cross margin, 5m, 15m and 1h signals, 1m and 5m execution, trailing and breakeven stops, cost stress, estimated funding) produced 5,105 trades whose trade lists, equity curves and metrics hash identically on the previous engine and this one. And `tests/test_engine_golden.py` pins three synthetic scenarios to numbers recorded from the previous engine.

New behaviour is covered by unit tests with hand-calculable prices: signal exit at the next open with the cost identity intact, reversal on one open, strategy-supplied stops and targets and their rejection on the wrong side of the fill, entries with no target or no time limit, stops and targets amended while a position is open, and a resting limit entry withdrawn by an exit signal. The look-ahead check now compares exits and price levels as well as entries.

## Pine scripts (5 October 2026)

Validated in Linux with Python 3.11 and 3.12 and local headless Chromium. Native Windows was not independently tested.

247 tests pass. `pine.py` joined the engine fingerprint and `strategies.py` gained one plan column (`unfiltered`), so the fingerprint changed. `engine.py`, `data.py` and `experiments.py` are unchanged from the signal-exits step; the three pinned engine scenarios did not move.

Indicators: 57 series are compared in the tests, bar by bar on 2,000 synthetic candles, with independent pandas and NumPy calculations of the published formulas: the SMA-seeded recursive averages, weighted, Hull, ALMA and regression averages, RSI, ATR, MACD, Bollinger and Keltner bands, stochastic, CCI, MFI, CMO, TSI, supertrend, DMI/ADX, pivots and the rest. All match to floating-point tolerance. Parabolic SAR is checked by property only. Higher-timeframe requests are compared with candles aggregated independently from the 1-minute data, for both the plain form and the offset-with-lookahead form.

Agreement with the engine: four scripts (a moving-average reversal, an RSI entry with a percentage stop and target placed a bar after the fill, a Bollinger entry with a bracket, and a higher-timeframe trend filter) were run on real JTOUSDT candles, 1 October 2024 to 1 February 2025, on 15-minute signals with default settings and on 5-minute signals long-only with limit targets. Across the eight runs the engine recorded 5,243 trades. 5,239 of them entered on the candle the script's own position model expected and exited inside the candle where that model closed; the other 4 were positions still open when the test ended. The engine refused 3 entries for size. The cost identity held on every trade.

In the browser at 1440 × 1000 and 390 × 844: paste, check, add, select and run a script, and a refused script with its line numbers. Document width matched the viewport.

Not verified: agreement with TradingView's Strategy Tester. No script's trade list has been compared with TradingView's on the same market.

## Remaining limitations

Candle-based fills cannot reproduce the order book, exact intrabar ordering or exchange latency. Liquidation calculations use disclosed approximations, including trade-price rather than historical mark-price data. Funding timestamps are aligned to execution boundaries. Exchange rules require user overrides when historical rules are unavailable. Starter strategies have not been established as profitable. See the README and in-app methodology before interpreting results.
