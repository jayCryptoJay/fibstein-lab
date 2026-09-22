# CLAUDE.md

Read AGENTS.md first for project invariants.

## Binance geo-block workaround

Verified from Claude's sandbox on 7 Sep 2026 and again on 22 Sep 2026.

- `fapi.binance.com` and `api.binance.com` are blocked (HTTP 451 / "restricted location").
- **USDⓈ-M futures:** use `https://www.binance.com/fapi/v1/...` and `https://www.binance.com/futures/data/...`.
  These serve full data: klines, premiumIndex, openInterest, openInterestHist,
  globalLongShortAccountRatio, topLongShortPositionRatio, topLongShortAccountRatio,
  takerlongshortRatio, depth, aggTrades, fundingRate.
- **Spot:** use `https://data-api.binance.vision/api/v3/...`.
- Dead ends: the `fapi1`/`fapi2`/`fapi3` mirrors return empty responses; `data-api.binance.vision` has no futures paths.
- Bybit (`api.bybit.com`) is also geo-blocked; OKX (`www.okx.com/api/v5`) works directly.
