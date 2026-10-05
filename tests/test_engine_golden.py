"""Frozen results for the built-in strategies.

Recorded from the v1.0.0 engine before signal exits were added, and reproduced exactly by
the current one. If these numbers move, the engine's behaviour changed for strategies
that never asked for it: stop and find out why before updating them.
"""
import numpy as np
import pandas as pd
import pytest
from backend.config import Config
from backend.engine import simulate

def market(n=30000,seed=7):
    # Integer steps keep every price exact, so the fixture is identical on any platform.
    rng=np.random.default_rng(seed); close=100+np.cumsum(rng.integers(-12,13,n))*.01; op=np.r_[100.,close[:-1]]
    return pd.DataFrame({'open':op,'high':np.maximum(op,close)+.02,'low':np.minimum(op,close)-.02,'close':close,'volume':rng.integers(500,1500,n).astype(float)},
                        index=pd.date_range('2025-01-01',periods=n,freq='1min',tz='UTC'))

GOLDEN={
 'trend_pullback':({},56,-106.309382,51.070581,.06061,['stop','target','time_exit']),
 'breakout_retest':({'entry_order':'limit','target_order':'limit'},50,-63.756014,30.539577,.029597,['stop','target','time_exit']),
 'vwap_reversion':({'margin_mode':'cross','trailing_atr':1.5,'breakeven_r':1,'htf_filter':'off','vwap_band_atr':.5,'rsi_oversold':45,'rsi_overbought':55,'range_threshold':2},
                   144,-340.433256,130.176972,.205684,['stop','target']),
}

@pytest.mark.parametrize('strategy',list(GOLDEN))
def test_built_in_strategies_reproduce_recorded_results(strategy):
    extra,trades,net,fees,funding,reasons=GOLDEN[strategy]; f=market()
    rates={int(t.timestamp()*1000):.0001 for t in pd.date_range('2025-01-01','2025-01-21',freq='8h',tz='UTC')}
    c=Config(start='2025-01-04',end='2025-01-21',strategy=strategy,funding_mode='off',risk_pct=1,max_open_risk_pct=4,**{'htf_filter':'1h','htf_ema':10,**extra})
    r=simulate(c,{'JTOUSDT':(f,rates,{'tick_size':.01,'qty_step':.001,'min_qty':.001,'min_notional':5},[],{'pair':'golden'},f)}); m=r['metrics']
    assert m['trade_count']==trades
    assert (m['net_pnl'],m['fees'],m['funding'])==(pytest.approx(net,abs=1e-5),pytest.approx(fees,abs=1e-5),pytest.approx(funding,abs=1e-5))
    assert sorted(set(t['reason'] for t in r['trades']))==reasons
    assert r['diagnostics']['exit_signals']==0 and r['diagnostics']['amended_levels']==0
