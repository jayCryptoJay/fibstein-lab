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

## Remaining limitations

Candle-based fills cannot reproduce the order book, exact intrabar ordering or exchange latency. Liquidation calculations use disclosed approximations, including trade-price rather than historical mark-price data. Funding timestamps are aligned to execution boundaries. Exchange rules require user overrides when historical rules are unavailable. Starter strategies have not been established as profitable. See the README and in-app methodology before interpreting results.
