# Testing a Pine Script strategy

Paste a TradingView `strategy()` script into the **Pine** tab, check it, and add it
to the strategies. It then runs through the same engine, the same cost model and
the same walk-forward as everything else.

```bash
python -m backend.lab pine check my-script.pine      # the conversion report; nothing is saved
python -m backend.lab pine add my-script.pine --hypothesis "Why this should make money."
python -m backend.lab pine list
python -m backend.lab run --preset sample-jto --set strategy=pine_my_script_ab12cd
```

## How it works

The script is not rewritten into Python. `backend/pine.py` reads it and runs it one
completed signal candle at a time, the way TradingView's own runtime does on
history. On candle N it can read candle N and earlier ones and nothing else, so a
script cannot look ahead here even if it tries. Every added script is also put
through the look-ahead check before it is saved.

What the script decides becomes a plan for the engine:

| The script does | The engine does |
|---|---|
| `strategy.entry` | Enters at the next open: the first execution candle after the signal candle closed. |
| `strategy.entry` in the other direction | Closes and reverses on that same open. |
| `strategy.close`, `strategy.close_all` | Closes at the next open. |
| `strategy.exit(stop=, limit=)` | Uses those prices as the stop and target, checked on 1-minute (or 5-minute) execution candles. |
| The same `strategy.exit` called again with new prices | Moves the stop or target from the next open. |

Everything else stays with Settings and the engine: fees, spread, slippage, funding,
position sizing, leverage, liquidation, exposure limits and contract rules. A
script's `commission_value`, `default_qty_value`, `initial_capital` and `qty=` are
read and ignored.

## Three kinds of answer

The check sorts everything the script asks for.

**Exact.** Entries, reversals, closes, stop and limit prices, `var` state, history
(`close[1]`, `(high - low)[3]`), functions, tuples, `if` and `switch` as values,
`for` and `while`, inputs at their defaults, and the indicators listed below.
Indicator formulas are tested against independent calculations in
`tests/test_pine.py`.

**Differs.** Shown in the report with the lines involved. The script still runs,
but not identically to TradingView:

| What | How it differs |
|---|---|
| Fills | Always the next open with Settings costs. TradingView's own cost and size settings are not used. |
| `process_orders_on_close`, `immediately=true` | TradingView fills at the signal candle's close. FibStein fills at the next open, the first price that can be traded after the signal. |
| A script with no stop at entry | The engine cannot size or liquidate a position without a stop, so the entry starts with the Settings ATR stop, measured from the signal candle's close. If the script places its stop a bar later (the common `if strategy.position_size > 0` pattern), that stop replaces it from then on. |
| `strategy.position_size`, `position_avg_price`, `opentrades`, `closedtrades` | The script sees the position TradingView would report: filled at the next open, no costs. `position_size` is 1, -1 or 0. The engine's real position can differ after an entry it refused (margin, liquidity, a stop on the wrong side of the fill) or a liquidation. |
| `request.security` | See below. |
| `barstate.islast` | Always false. Knowing which bar is last is information from the future. |
| Pivot highs and lows | A bar equal to the pivot is allowed before it and not after it. TradingView's handling of exact ties is not documented. |

**Refused.** The script is not added. Nothing is approximated silently.

| What | Why |
|---|---|
| Pine v4 and older | Convert in the Pine Editor ("Convert code to v6") and paste again. |
| `indicator()` and `library()` scripts | Nothing to test without `strategy.entry`. |
| `strategy.entry` with `limit=` or `stop=`, `strategy.order`, `strategy.cancel` | Resting entry orders are not modelled for scripts yet. |
| Partial exits (`qty`, `qty_percent` below 100) and `pyramiding` above 1 | The engine holds one whole position per market. |
| `strategy.exit` with `profit=`, `loss=`, `trail_points`, `trail_price`, `trail_offset` | Tick distances and the built-in trailing stop are not modelled. A `stop=` price the script moves itself is. |
| `strategy.equity`, `netprofit`, `openprofit`, win and loss counts | The engine computes the account with its own costs; a script cannot depend on TradingView's version of it. |
| `request.security` for another symbol, a lower timeframe, weekly or monthly bars, `gaps_on`, or `lookahead_on` without an offset | Only the tested market is loaded, and an unfinished higher-timeframe candle is the future. |
| `varip`, `calc_on_order_fills`, `timenow`, `last_bar_index` | Live-chart behaviour or knowledge of where history ends. |
| Arrays, matrices, maps, user-defined types, methods, enums, `import` | Not built yet. |
| `syminfo.mintick`, `time()` with a session, values read back from drawings | Not available to a strategy here. |

## Higher timeframes

`request.security(syminfo.tickerid, "60", expression)` is answered by running the
same script on 60-minute candles built from the chart's own candles. A
higher-timeframe candle is used only when every chart candle inside it exists and
the last of them has closed. The value then holds until the next one closes.

That is what TradingView shows on historical bars. On a live chart TradingView can
show the unfinished candle, which is why such scripts repaint; a backtest must not
use it, and this one cannot.

The usual non-repainting form is supported exactly:
`request.security(syminfo.tickerid, "60", close[1], lookahead = barmerge.lookahead_on)`
returns the last candle that had closed when the chart candle opened.

The timeframe must be the chart timeframe or a whole multiple of it that divides a
day (`"30"`, `"60"`, `"240"`, `"D"`), and the request must be at the top level of
the script, not inside a function or block.

## Settings and a script

| Setting | Effect on a Pine strategy |
|---|---|
| Costs, funding, leverage, sizing, exposure and position limits | Apply as always. |
| Direction | Applies. With long only, a short entry closes a long and opens nothing. |
| ATR stop multiple | Used only for entries the script gives no stop. |
| Target order (market or limit) | Applies to the script's targets. |
| Higher-timeframe filter, target multiple, maximum hold | Not applied. |
| Trailing distance, breakeven trigger, limit entry order | Must be off. The run is refused otherwise, because the script's own model of its position would no longer match the engine. |

No order is placed before the test's start date. Indicators still warm up on the
earlier candles.

## Indicators

`ta.sma ema rma wma vwma hma swma alma linreg`, `ta.rsi stoch cci mfi cmo tsi wpr`,
`ta.atr tr`, `ta.macd bb kc`, `ta.supertrend dmi sar`, `ta.highest lowest
highestbars lowestbars`, `ta.stdev variance dev`, `ta.change mom roc`,
`ta.crossover crossunder cross`, `ta.barssince valuewhen rising falling cum`,
`ta.pivothigh pivotlow`, `ta.vwap` (daily UTC session), `ta.obv`, `math.*`, `nz`,
`na`, `fixnan`, `timestamp`, `timeframe.change`.

Recursive averages are seeded as Pine seeds them: with the simple average of the
first full window. An indicator called inside an `if` advances only on the bars
where it runs, as in Pine. In v5 both sides of `and` and `or` are evaluated; in v6
evaluation stops at the first side that settles the answer; v5 keeps whole numbers
when dividing two whole numbers and v6 does not.

## Identity and trial counting

A saved script's key ends in a hash of its logic: `pine_ema_pullback_b4a569`.
Comments, blank lines and spacing do not change the hash. Any change to the logic,
including an input's default, does. So:

- An edited script is a new strategy. Opening a saved script, changing it and
  adding it again records the new one as a variant of the old, and "best of N"
  counts trials across both.
- Inputs are fixed at their defaults. Trying ten values of one input means adding
  ten variants, and all ten are counted. A way to search inputs inside one
  strategy needs `strategy_params`, which is not approved yet.
- A script file edited on disk no longer matches its key and is skipped at
  start-up, not trusted.
- Scripts are never deleted. Archive a finished one in the Library.

A backtest of a script is in-sample. For held-out evidence, run an expanding
walk-forward on it with a single candidate, for example `--grid '{"stop_atr":[1.5]}'`:
each training period then only decides whether the script trades the next one.

## What has and has not been verified

Verified: every indicator against an independent calculation; the language
features in `tests/test_pine.py`; that the engine takes the trades the script's own
position model expects, on synthetic candles in the tests and on four months of
real JTOUSDT candles (see `VALIDATION.md`); the look-ahead check on every script
added.

Not verified: trade-for-trade agreement with TradingView's Strategy Tester on the
same market. The formulas follow TradingView's published definitions, but no
script's trade list has been compared with TradingView's yet. Treat the first
result of an important script as a claim to check: export its trade list from
TradingView and compare entry times.

## For a strategy written in Python

A Python strategy registered with `register_strategy` can return the same plan a
script produces. See the docstring of `register_strategy` in
`backend/strategies.py` and prove it with `python -m backend.lab check <key>`.
