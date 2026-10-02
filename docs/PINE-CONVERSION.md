# Converting a Pine Script strategy

Pine is not executed. A conversion rewrites the script's entry logic as an ordinary
FibStein strategy in `custom_strategies.py`, so it runs through the same engine, the
same cost model and the same walk-forward as everything else. This page is the
procedure and the rules for what may be converted today.

## What converts today

A script converts cleanly when its **entries** are a function of completed bars and
its **exits** fit the engine's model: an ATR-multiple stop, a reward-to-risk target,
a time limit, and optional trailing or breakeven stops.

A script does **not** convert yet when it closes on a signal, flips between long and
short, or sets a stop or target at a price the engine cannot express as an ATR
multiple (a fixed percent, a swing low, a band). The engine ignores new signals
while a position is open, so those exits would be silently dropped. That needs the
engine extension in phase 3 of `RESEARCH-PLAN.md`; until then, say so in the
conversion report instead of approximating.

## Mapping

| Pine | FibStein | Status |
|---|---|---|
| `close`, `open`, `high`, `low`, `volume` | `f.close`, `f.open`, `f.high`, `f.low`, `f.volume` | Supported |
| `x[1]`, `x[n]` | `x.shift(1)`, `x.shift(n)` | Supported. Never a negative shift. |
| `ta.ema(src, n)` | `ema(src, n)` from `backend.strategies` | Approximated: the seed during warmup can differ. Values converge after roughly three lengths. |
| `ta.sma`, `ta.highest`, `ta.lowest`, `ta.stdev` | `.rolling(n).mean()`, `.max()`, `.min()`, `.std(ddof=0)` | Supported |
| `ta.rsi(close, n)`, `ta.atr(n)` | `f.rsi`, `f.atr` when `n` equals the configured length; otherwise compute with `ewm(alpha=1/n, adjust=False)` | Approximated: same Wilder smoothing; the seed during warmup can differ. |
| `ta.vwap` | `f.vwap` | Supported for the daily UTC session only. |
| `ta.crossover(a, b)` | `(a > b) & (a.shift() <= b.shift())` | Supported |
| `ta.crossunder(a, b)` | `(a < b) & (a.shift() >= b.shift())` | Supported |
| `input.*` | A constant in the function, or an existing `Config` field such as `c.ema_fast` | Constants until `strategy_params` exists (phase 2). The config rejects unknown settings. |
| `strategy.entry("L", strategy.long)` | return `1` on that bar | Supported. The fill is the next execution candle's open, as in TradingView's default. |
| `strategy.entry("S", strategy.short)` | return `-1` on that bar | Supported |
| `strategy.exit` with an ATR stop and an R-multiple target | `stop_atr`, `reward_risk` settings | Supported |
| `strategy.exit` with a percent or price stop or target | none | Needs engine change (phase 3) |
| `strategy.close`, `strategy.close_all`, reversal by opposite entry | none | Needs engine change (phase 3) |
| Pyramiding, `default_qty_type`, percent-of-equity sizing | FibStein's risk sizing replaces it | Not converted; report the difference. |
| `request.security` on a higher timeframe with a `[1]` offset and lookahead off | The built-in 1h and 4h EMA gates, or resample `f` and shift by one completed bar | Approximated; must pass the look-ahead check. |
| `request.security` with `lookahead=barmerge.lookahead_on` and no `[1]` offset | none | Refused: it reads the future. |
| `varip`, `calc_on_every_tick`, `barstate.isrealtime`, `timenow` | none | Refused: realtime-only behaviour cannot be backtested honestly. |

## Procedure

1. Read the script and list every entry condition, exit, input and data request.
   Classify each against the table. If anything is refused or needs the engine
   change, stop and report it; do not convert the rest and hope.
2. Write the function. It receives the feature frame `f` and the config `c`, and
   returns a Series of `-1`, `0`, `1` on `f.index`. The index is already shifted to
   the time each bar becomes available, so a signal on row `t` uses only data known
   at `t`.

   ```python
   # custom_strategies.py, beside launch.py
   import numpy as np, pandas as pd
   from backend.strategies import register_strategy, ema

   def ema_cross(f, c):
       fast, slow = ema(f.close, 9), ema(f.close, 21)           # ta.ema(close, 9), ta.ema(close, 21)
       up = (fast > slow) & (fast.shift() <= slow.shift())       # ta.crossover(fast, slow)
       down = (fast < slow) & (fast.shift() >= slow.shift())     # ta.crossunder(fast, slow)
       return pd.Series(np.select([up, down], [1, -1], default=0), index=f.index)

   register_strategy('ema_cross', 'EMA cross (Pine)',
                     'Converted from Pine: 9/21 EMA cross. Exits by ATR stop, target and time limit.', ema_cross)
   ```
3. Run the look-ahead check. It must pass before any result is read.

   ```bash
   python -m backend.lab check ema_cross
   ```
4. Record the idea with its origin and the hypothesis, written before the first test.

   ```bash
   python -m backend.lab strategy add ema_cross --origin pine --hypothesis "Why this should make money."
   ```
5. Run it. In-sample first to see that it trades at all, then a walk-forward for
   evidence. Every run is counted as a trial.
6. Write a conversion report in the journal: what mapped, what was approximated,
   what was left out.

   ```bash
   python -m backend.lab note ema_cross "Mapped: both crosses. Approximated: EMA seed. Left out: percent-of-equity sizing."
   ```

## What the look-ahead check does and does not prove

`check` computes the strategy's signals on many prefixes of a synthetic series and
on the whole series, and fails if any signal inside a prefix changes once later
bars exist. That catches negative shifts, centred windows, whole-series statistics
(a z-score over all data, a global max), and unshifted higher-timeframe values.

It does not prove the logic matches the Pine script, and it uses synthetic prices,
so a leak that only appears on rare real conditions can slip through. Matching
TradingView trade by trade is the parity harness in phase 3: export the List of
Trades for the same symbol, timeframe and dates with costs off, and require at
least 95% matching entries after warmup with every mismatch explained.

## Expected differences from TradingView

- **Order of prices inside a bar.** TradingView assumes the path from which extreme
  is nearer the open. FibStein executes on 1-minute candles and takes the stop
  first when one candle touches both stop and target.
- **Costs.** TradingView applies commission and a fixed slippage in ticks. FibStein
  charges fees, half-spread, slippage, funding and liquidation fees, and sizes each
  position from risk. Net results will differ even when every entry matches.
- **Warmup.** EMA, RSI and ATR seeds differ, so the first signals can differ.
- **Fingerprint.** `custom_strategies.py` is part of the engine fingerprint. Adding
  or editing any strategy in it marks earlier held-out evidence, for every
  strategy, as produced by older code until it is rerun.
