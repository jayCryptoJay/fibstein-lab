# Expanded historical data

Verified coverage: 2024-01-01 00:00 UTC through 2026-01-01 00:00 UTC (exclusive), for JTO, SOL, BTC, ETH, XRP, DOGE, AVAX, LINK, SUI and ADA against USDT. These are Binance USD-M linear perpetuals, not spot prices.

| Resolution | Candles across all 10 pairs |
| --- | ---: |
| 1m | 10,526,400 |
| 5m | 2,105,280 |
| 15m | 701,760 |
| 30m | 350,880 |
| 1h | 175,440 |
| 4h | 43,860 |
| 1d | 7,310 |

The independent audit verified all 240 complete source months and all 1,440 derived monthly exports, with zero missing minutes. It also checked 24,123 funding events, finding no observed gaps larger than the adjacent reported funding intervals plus a 60-second tolerance. See `data/independent-audit-2024-2025.json` for the precise checks and limitations.

Check `data/coverage-2024-2025.json` for actual completed months, missing minutes, funding-event counts and row totals. Missing data is never synthesized. Downloaded ZIPs are verified against the provider's SHA-256 checksum before normalization.

## Using the data

The app automatically reads the expanded `data/binance_archive` cache. Select that source and choose a cached date range. Start on January 15, 2024 or later to leave January 1–14 for indicator warmup. End dates are exclusive. Try one pair and three months first; multi-year ten-pair tests at one-minute execution resolution consume considerably more memory and time.

The strategy selector currently supports 5m, 15m, 30m and 1h signals; execution remains 1m or 5m. The app derives signal candles from canonical minute data. Separate 5m, 15m, 30m, 1h, 4h and daily CSVs are also supplied under `data/timeframes/PAIR/INTERVAL/`. The 4h and daily exports are useful for external analysis; this data expansion does not add those signal intervals to the app.

All timestamps identify bar OPEN in UTC. A candle's completed OHLCV becomes available only at its close. Higher-timeframe bars include exactly the required number of source minutes; incomplete bars are omitted. Monthly boundaries align with these UTC intervals. Volume is base-asset volume. Funding remains separate settlement events, never summed into OHLCV.

## Research split

One practical split is January 15–December 31, 2024 for development, January–June 2025 for validation, and July–December 2025 as a final holdout. Avoid adjusting a strategy after observing its holdout result. Leave pre-window data cached for warmup. More candles are not independent trades, and the seven resolutions describe the same market history rather than seven independent samples.

This dataset does not include historical order books, exact mark-price candles, open interest or liquidation prints. It does not establish profitable strategies or remove the engine's disclosed fill/liquidation limitations.

Source documentation: https://github.com/binance/binance-public-data

To resume or reproduce downloads and exports, run `python scripts/expand_data.py` from the project directory after installing requirements. Cached candle files are reused; derived exports and the coverage report are regenerated.
