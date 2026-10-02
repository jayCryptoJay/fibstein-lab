"""Look-ahead check for any registered strategy.

A causal strategy gives the same signals for the bars it has already seen whether or
not later bars exist. Run a strategy on a prefix of the data and on all of it: any
signal that differs inside the prefix was computed from the future. This is the
check every converted Pine script and every AI-written strategy must pass before
its results mean anything (AGENTS.md, invariant 1).
"""
import numpy as np
import pandas as pd
from .config import Config
from .strategies import prepare

CUTS = 40          # A one-bar peek only shows at the prefix's last bar, so one cut would usually miss it.
SEEDS = (19, 7)


def synthetic(n=12000, seed=19):
    """Random-walk 1-minute candles. A fixture for causality only, never performance."""
    rng = np.random.default_rng(seed); close = 100 * np.exp(np.cumsum(rng.normal(0, .001, n))); op = np.r_[close[0], close[:-1]]
    return pd.DataFrame({'open': op, 'high': np.maximum(op, close) * 1.0005, 'low': np.minimum(op, close) * .9995, 'close': close,
                         'volume': rng.uniform(500, 1500, n)}, index=pd.date_range('2025-01-01', periods=n, freq='1min', tz='UTC'))


def prefix_invariant(strategy, config=None, frames=None, cuts=CUTS):
    """Return {ok, conclusive, signals_checked, mismatches}. `ok` is False on any look-ahead."""
    # Higher-timeframe gates are applied after the strategy and tested on their own; off here so more signals are compared.
    c = config or Config(strategy=strategy, htf_filter='off')
    frames = frames if frames is not None else [synthetic(seed=s) for s in SEEDS]
    checked = 0; mismatches = []
    for raw in frames:
        full, _ = prepare(raw, c); rng = np.random.default_rng(len(raw))
        for n in sorted(int(x) for x in rng.integers(len(raw) // 2, len(raw), cuts)):
            part, _ = prepare(raw.iloc[:n], c)
            # A bar is available one minute after the prefix's last candle opens, never later.
            limit = int(raw.index[n - 1].timestamp() * 1000) + 60000
            seen = {t: s for t, s in full.items() if t <= limit}
            checked += len(seen)
            for t in sorted(set(part) | set(seen)):
                a, b = part.get(t), seen.get(t)
                if (a and a['side']) != (b and b['side']):
                    mismatches.append({'timestamp': t, 'prefix_minutes': n, 'prefix_side': a and a['side'], 'full_side': b and b['side']})
    return {'strategy': c.strategy, 'ok': not mismatches, 'conclusive': checked > 0, 'signals_checked': checked, 'mismatches': mismatches[:20]}
