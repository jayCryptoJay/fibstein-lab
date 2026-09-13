# FibStein Lab

A free, local USDT perpetual-futures research app with a React dashboard and an inspectable Python execution engine. This is a backtester, not a live trading bot. Starter strategies are research templates, not proven profitable systems.

## Start on Windows

1. Extract the entire ZIP. Do not run it from inside the ZIP viewer.
2. Install Python **3.12** if it is not already installed. The official download is [python.org](https://www.python.org/downloads/).
3. Double-click **Start-Windows.bat**. The first launch installs free dependencies into this folder's `.venv`; later launches reuse them.
4. Keep the terminal open. The app opens in your browser at `http://127.0.0.1:8765`.
5. Click **Use bundled January 2025 data**, then **Run backtest**.

No Node.js installation, exchange account, trading key, paid feed, AI subscription, or cloud hosting is needed to run the included build. Initial installation and new data downloads require internet access. Your computer's time, storage, electricity, and internet are not supplied by this software.

On macOS/Linux/WSL: `python3 launch.py` or `bash start.sh`. Python 3.11+ is supported by the declared dependencies; this release was tested on Python 3.12/Linux, not native Windows or Safari.

## Use on your iPhone

The computation runs on your computer; the ZIP is not an installable iPhone app. To use the responsive interface on trusted home Wi-Fi, start `python launch.py --lan`, keep the computer running, and open `http://YOUR_COMPUTER_LOCAL_IP:8765` in Safari. You may need to allow the app on your computer's **private** network firewall. This mode has no login: anyone on the reachable network can use the local app. **Never port-forward it or expose it publicly.** Default launch is loopback-only.

## What is included

- JTO, SOL, BTC, ETH, XRP, DOGE, AVAX, LINK, SUI and ADA against USDT; add more USDT symbols in Portfolio markets.
- Three parameterized strategies: trend pullback, breakout/retest and VWAP range reversion.
- 5m/15m/30m/1h signals; optional completed 1h/4h context; 1m execution, with optional coarser 5m execution.
- Long/short, market and conservative post-only limit entries, market/limit targets, ATR stops, trailing, price breakeven, time exits.
- Maker/taker fees, modeled spread/slippage, historical or explicitly estimated funding, configurable cost stress.
- Shared-equity portfolio accounting, order priority, risk/margin/exposure caps, prior-bar volume participation, manual contract rules.
- Persistent named presets, import/export settings, saved run history, CSV trade/equity exports and full JSON reports.
- Strategy comparisons, 1×/2×/3× execution-cost stress, bounded parameter search and expanding walk-forward testing.
- Expanded **actual historical** Binance USD-M candles and funding for all ten pairs: see `docs/EXPANDED-DATA.md` and `data/coverage-2024-2025.json` for audited coverage. Separate 5m, 15m, 30m, 1h, 4h and daily exports accompany canonical 1m candles. The original January 2025 quick sample still works; Jan 1–14 is its warmup and the end date Feb 1 is exclusive.

## A practical first workflow

1. Run the bundled sample to verify everything works. Do not interpret its performance as validation of an edge.
2. In Data, choose your source and download your selected pairs/date range. The downloader includes warmup, caches history and verifies Binance archive checksums. Longer runs are limited to three years each.
3. Under Execution costs, set **your venue's fee tier** and a defensible spread/slippage estimate. Defaults are labeled assumptions, not verified WEEX or other exchange fees.
4. Set risk, direction, leverage and stops; save a preset before comparing changes.
5. Run single-pair tests before the shared-equity portfolio. Simultaneous entries use the displayed pair order; change the JSON pair order if you want a different priority.
6. Review the trade log, costs, drawdown and warnings. Then use Compare for stress and out-of-sample tests.
7. Keep a genuinely unused final period. Repeatedly optimizing on a viewed holdout converts it into training data.

## Execution contract

Signal timestamps are candle-close availability times. A 15m candle opening at 10:00 becomes tradable at the next execution candle's 10:15 open. Higher-timeframe data is never read before close. Stop/target outcomes later inside a minute cannot release capital for that minute's initial entries.

Fees are applied to each side's **filled notional**. Market-fill price = reference ± (half modeled spread + modeled slippage), with adverse tick rounding attributed to slippage. Limit fills require configurable penetration; no spread credit is assumed. A limit-entry bar cannot take profit. Stops precede targets when their intrabar order is unknown. Trailing and price-breakeven amendments use closed execution candles and activate next candle. Price breakeven does not cover fees/funding.

Position risk sizing includes entry and stop-exit fees and modeled adverse stop execution, but cannot guarantee protection against gaps, funding or liquidation. Fixed notional is still capped by configured risk and available capital. Leverage controls margin; it does not double-count P&L.

Funding amounts use historical rates when selected. Millisecond settlement jitter is floored to an execution boundary; only positions already open before that boundary are charged/credited. The boundary's trade-price open is a mark-price proxy. The sub-minute sequence is unknowable from candles. Positive funding cost = paid; negative = received.

The accounting identity for every closed trade is:

`Net P&L = reference-price gross P&L - fees - slippage - spread - funding - liquidation fee`

Final equity equals starting equity plus the sum of closed-trade net P&L. All remaining positions are closed at the end of a test. Equity metrics use full-resolution execution-candle closes; dashboard charts are downsampled for responsiveness, but exports retain every equity observation.

## Important realism limits

- **Liquidation is approximate.** The engine uses trade-price candles, fixed configurable maintenance rates and estimated liquidation fees. Historical mark-price paths, tier changes, ADL and partial-liquidation rules are not reconstructed. Cross mode is an explicitly conservative simultaneous-adverse-extremes stress approximation, not an exchange liquidation replica.
- Generic tick/quantity/minimum-notional rules are used unless you provide overrides under Contract rules. They are prominently disclosed. Historical exchange filters are not automatically verified.
- Constant spread and slippage cannot establish historical order-book depth or queue position. Entry size is capped by previous-bar volume; exit depth capacity and partial fills are not reconstructed.
- OHLCV cannot prove that an order filled. Short-lived intra-candle account drawdowns can exceed reported candle-close drawdown. Recorded intrabar exit times identify the candle end, not a known trade timestamp. MAE/MFE use whole-bar extremes and can include prices from before an intrabar exit.
- Backtests reject gaps inside the requested execution period. Sparse warmup history triggers warnings or delays signals until indicators become available. No forward-filled candle fabrication is used.
- Historical funding is required in that mode; missing data is not silently set to zero. The current month may not yet have a Binance monthly funding archive. Use a suitable public adapter/import, shorten the dates, or knowingly choose estimated funding.
- Source differences matter: Binance data with manually configured WEEX fees is still a **Binance-price simulation**, not a WEEX historical execution backtest.
- The chosen assets are a fixed watchlist, not a historical investable-universe reconstruction; survivorship bias is not eliminated.
- OI, CVD, depth and liquidation-map strategies are not included because suitable historical inputs are not bundled. The app never synthesizes those inputs from candles.

## Historical sources

The archive downloader follows [Binance's public-data format and checksum documentation](https://github.com/binance/binance-public-data). It supports monthly history and completed daily files for an incomplete current month. Public REST adapters use [CCXT](https://github.com/ccxt/ccxt), restricted here to Bybit, OKX and Binance USD-M linear USDT swaps. Exchange access and history depth vary; the code does not bypass regional restrictions. The Binance archive path was exercised end-to-end. Other adapters were implemented and inspected against installed CCXT parsing code, but were not fully downloaded/tested from every venue in this environment.

CSV candles: `timestamp,open,high,low,close,volume`; one-minute candle OPEN timestamps, UTC, ISO strings or epoch seconds/milliseconds/microseconds/nanoseconds; base-asset volume. Funding CSV: `timestamp,rate`, ISO UTC timestamp and decimal rate, e.g. `0.0001` means 1 bp. Uploads are limited to 25 million candle-text characters per request; split larger imports by month.

## Validation and optimization

Compare runs all three templates with the current settings. Stress re-runs the engine with 1×, 2×, 3× spread/slippage; funding and fees are not multiplied. Recomputed position sizing/fills can mean net returns are not strictly monotonic.

Grid search tests at most 12 candidates on the first 60% of dates, ranks eligible candidates by `net_return_pct - max_drawdown_pct`, and runs the selected candidate on the untouched final 40%. Walk-forward divides that final portion into 1–5 consecutive test windows, expanding training before each one. Test folds start flat and compound their equity. Minimum training trade count is configurable. This is bounded exploratory selection, not a claim that the best mathematical strategy has been found.

Run tests after installing `requirements-dev.txt`:

```bash
python -m pytest tests -q
```

Tests cover manually calculable trades, fee/slippage identities, funding direction/timing, leverage, stop/target ambiguity, gap exits, limit penetration, trailing timing, portfolio capital timing, source validation, timestamp units, higher-timeframe prefix invariance and training-only parameter selection. Unit fixtures are synthetic and are never shown as historical performance.

## Add a strategy

Create `custom_strategies.py` beside `launch.py`. Import `register_strategy` from `backend.strategies`, then register a function `(features, config) -> pandas.Series` of exactly `-1, 0, 1` aligned to feature timestamps. The provided features include open/high/low/close/volume, fast/slow EMA, ATR, RSI, daily VWAP and completed higher-timeframe gates. See the existing three implementations for the interface. A custom strategy is trusted local Python code: do not install untrusted code. New custom parameters require fields in Config and dashboard controls. Pine Script is not executed directly.

## Development and persistence

The source frontend is React/Vite. To rebuild it: `cd frontend`, `npm ci`, `npm run build`. Start backend development with `python -m uvicorn backend.server:app --host 127.0.0.1 --port 8765`. Vite's development proxy points to that port.

`workspace/lab.sqlite3` stores named presets and run summaries; `workspace/runs/` stores full compressed results. `data/` stores price and funding caches. Keep these folders when updating. No result is sent to an external service. Docker is optional: `docker compose up --build` binds to loopback by default and persists the two folders. Docker installation was not exercised in this environment.

## Why these tools

The app reuses React/Vite, Recharts, pandas/NumPy, FastAPI, SQLite and CCXT. Freqtrade and Backtesting.py were reviewed as engine options; this release does **not** claim to use their engines. [Freqtrade's documented candle backtest assumes no slippage](https://www.freqtrade.io/en/stable/backtesting/#assumptions-made-by-backtesting). The custom, tested execution layer keeps this app's different cost and funding assumptions explicit. Source is MIT licensed; dependency notices are included. Public market data retains its provider's terms.
