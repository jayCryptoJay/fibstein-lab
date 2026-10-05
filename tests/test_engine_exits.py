import json
import numpy as np
import pandas as pd
import pytest
from backend import strategies
from backend.causality import prefix_invariant,synthetic
from backend.config import Config
from backend.engine import simulate
from backend.strategies import prepare

def config(**kw):
    return Config(**({'start':'2025-01-01','end':'2025-01-02','balance':10000,'risk_pct':1,'max_open_risk_pct':5,
      'sizing':'fixed_notional','fixed_notional':1000,'htf_filter':'off','maker_bps':0,'taker_bps':0,
      'slippage_bps':0,'spread_bps':0,'funding_mode':'off','stop_atr':1,'reward_risk':2,'participation_pct':20,
      'max_hold_hours':8,**kw}))

FLAT=[100,100.2,99.8,100,100000]
# Entry fills at bar 1's open (100). Price is near 101 from bar 3; stop 99 and target 102 are never touched.
UP=[FLAT,FLAT,[100,101.1,99.9,101,100000],[101,101.2,100.9,101,100000],[101,101.2,100.9,101,100000],[101,101.2,100.9,101,100000],[101,101.2,100.9,101,100000],[101,101.2,100.9,101,100000]]

def frame(rows):
    return pd.DataFrame(rows,columns=['open','high','low','close','volume'],index=pd.date_range('2025-01-01',periods=len(rows),freq='1min',tz='UTC'),dtype=float)

def ms(f,i): return int(f.index[i].timestamp()*1000)
def sig(side=0,**kw): return {'side':side,'atr':1.,'reference':100.,'regime':'range',**kw}

def run(rows,signals,c=None):
    f=frame(rows); bundle=(f,{},{'tick_size':.01,'qty_step':.001,'min_qty':.001,'min_notional':1},[],{'rows':len(f),'pair':'test'},f)
    return simulate(c or config(),{'JTOUSDT':bundle},signal_overrides={'JTOUSDT':{ms(f,i):s for i,s in signals.items()}}),f

def test_signal_exit_fills_at_the_next_open_and_costs_still_reconcile():
    r,f=run(UP,{1:sig(1),4:sig(exit_long=True)},config(taker_bps=5,slippage_bps=3,spread_bps=2,reward_risk=5)); t=r['trades'][0]
    assert (t['reason'],t['exit_time'],t['exit_reference'])==('signal_exit',ms(f,4),101)
    assert t['raw_pnl']==pytest.approx(t['qty']*(101-100))
    assert t['net_pnl']==pytest.approx(t['raw_pnl']-t['fees']-t['slippage']-t['spread']-t['funding']-t['liquidation_fee'])
    assert r['metrics']['net_pnl']==pytest.approx(t['net_pnl'])
    assert r['diagnostics']['exit_signals']==1 and r['diagnostics']['signals']==1
    assert any('sets its own exits' in w for w in r['warnings'])

def test_exit_for_the_other_side_is_ignored():
    r,_=run(UP,{1:sig(1),4:sig(exit_short=True)})
    assert [t['reason'] for t in r['trades']]==['end_of_test']

def test_entry_signal_alone_never_closes_or_flips_an_open_position():
    r,_=run(UP,{1:sig(1),4:sig(-1)})
    assert [(t['side'],t['reason']) for t in r['trades']]==[('long','end_of_test')]
    assert not any('sets its own exits' in w for w in r['warnings'])

def test_reversal_closes_and_opens_on_the_same_open():
    r,f=run(UP,{1:sig(1),4:sig(-1,exit_long=True)}); a,b=r['trades']
    assert (a['side'],a['reason'],a['exit_time'],a['exit_price'])==('long','signal_exit',ms(f,4),101)
    assert (b['side'],b['entry_time'],b['entry_price'],b['reason'])==('short',ms(f,4),101,'end_of_test')
    assert r['metrics']['net_pnl']==pytest.approx(10)

def test_reentry_after_a_boundary_stop_needs_reversal_intent():
    rows=[FLAT,FLAT,FLAT,FLAT]+[[98.5,98.6,98.4,98.5,100000]]*4
    plain,_=run(rows,{1:sig(1),4:sig(-1)})
    assert [(t['side'],t['reason']) for t in plain['trades']]==[('long','stop_gap')]
    flip,f=run(rows,{1:sig(1),4:sig(-1,exit_long=True)})
    assert [(t['side'],t['reason']) for t in flip['trades']]==[('long','stop_gap'),('short','end_of_test')]
    assert flip['trades'][1]['entry_time']==ms(f,4)

def test_strategy_levels_replace_the_settings():
    rows=[FLAT,FLAT,[100,100.5,99.9,100.3,100000]]+[FLAT]*3
    r,_=run(rows,{1:sig(1,stop_price=99.5,target_price=100.4)}); t=r['trades'][0]
    assert (t['stop_price'],t['target_price'],t['reason'],t['exit_reference'])==(pytest.approx(99.5),pytest.approx(100.4),'target',pytest.approx(100.4))
    # Risk is sized from the strategy's stop: 0.5 a unit, not the 1.0 the ATR setting would give.
    assert t['initial_risk']==pytest.approx(t['qty']*.5)

def test_stop_alone_takes_its_target_from_reward_to_risk():
    r,_=run([FLAT]*6,{1:sig(1,stop_price=99.6)})
    assert (r['trades'][0]['stop_price'],r['trades'][0]['target_price'])==(pytest.approx(99.6),pytest.approx(100.8))

@pytest.mark.parametrize('side,levels,reason',[(1,{'stop_price':100.5},'stop_not_beyond_entry'),(-1,{'stop_price':99.5},'stop_not_beyond_entry'),
    (1,{'target_price':99.9},'target_not_beyond_entry'),(-1,{'target_price':100.1},'target_not_beyond_entry')])
def test_levels_on_the_wrong_side_of_the_fill_are_refused(side,levels,reason):
    r,_=run([FLAT]*6,{1:sig(side,**levels)})
    assert r['trades']==[] and r['diagnostics']['rejected']=={reason:1}

def test_no_target_holds_through_the_default_target_and_exports_cleanly():
    rows=[FLAT,FLAT,[100,103,100,103,100000]]+[[103,103.2,102.8,103,100000]]*4
    assert run(rows,{1:sig(1)})[0]['trades'][0]['reason']=='target'
    r,_=run(rows,{1:sig(1,no_target=True)}); t=r['trades'][0]
    assert (t['reason'],t['target_price'],t['exit_price'])==('end_of_test',None,103)
    json.dumps(r,allow_nan=False)

def test_no_time_exit_ignores_the_hold_limit():
    c=config(max_hold_hours=.084)
    assert run([FLAT]*12,{1:sig(1)},c)[0]['trades'][0]['reason']=='time_exit'
    assert run([FLAT]*12,{1:sig(1,no_time_exit=True)},c)[0]['trades'][0]['reason']=='end_of_test'

def test_amended_stop_applies_from_that_open_and_can_gap():
    rows=UP[:4]+[[101,101.2,100.7,101,100000]]+UP[5:]
    assert run(rows,{1:sig(1)})[0]['trades'][0]['reason']=='end_of_test'
    r,f=run(rows,{1:sig(1),4:sig(amend_stop=100.8)}); t=r['trades'][0]
    assert (t['reason'],t['exit_reference'],t['exit_time'],t['stop_price'])==('stop',pytest.approx(100.8),ms(f,4)+60000,pytest.approx(100.8))
    assert r['diagnostics']['amended_levels']==1
    # A stop moved above the market is already hit: it fills at the open, not at the stop.
    t=run(rows,{1:sig(1),4:sig(amend_stop=101.5)})[0]['trades'][0]
    assert (t['reason'],t['exit_reference'])==('stop_gap',101)

def test_amended_target_gives_a_target_to_a_position_that_had_none():
    r,f=run(UP,{1:sig(1,no_target=True),3:sig(amend_target=101.1)}); t=r['trades'][0]
    assert (t['reason'],t['exit_reference'],t['target_price'])==('target',pytest.approx(101.1),pytest.approx(101.1))

def test_exit_signal_withdraws_a_resting_limit_entry():
    c=config(entry_order='limit',limit_offset_bps=50)
    assert run([FLAT]*8,{1:sig(1)},c)[0]['diagnostics']['rejected']=={}
    r,_=run([FLAT]*8,{1:sig(1),2:sig(exit_long=True)},c)
    assert r['trades']==[] and r['diagnostics']['rejected']=={'cancelled_by_exit_signal':1}

def plan_strategy(f,c):
    up=(f.close>f.close.shift())&(f.close.shift()>f.close.shift(2)); down=(f.close<f.close.shift())&(f.close.shift()<f.close.shift(2))
    return pd.DataFrame({'side':np.select([up,down],[1,-1],default=0),'exit_long':down,'exit_short':up,
                         'stop_price':np.where(up,f.low.rolling(5).min()*.99,np.where(down,f.high.rolling(5).max()*1.01,np.nan)),'no_target':True},index=f.index)

def test_plan_frame_becomes_signals_and_entry_filters_leave_exits_alone(monkeypatch):
    monkeypatch.setitem(strategies.REGISTRY,'plan',plan_strategy); raw=synthetic(4000)
    both,f=prepare(raw,Config(strategy='plan',htf_filter='off'))
    longs,_=prepare(raw,Config(strategy='plan',htf_filter='off',direction='long'))
    assert {'exit_long','exit_short','stop_price','no_target'}<=set(f.columns)
    shorts=[t for t,s in both.items() if s['side']==-1]; assert shorts
    # Long-only removes the short entry but keeps the instruction to leave the long.
    assert all(longs[t]['side']==0 and longs[t]['exit_long'] for t in shorts)
    entry=next(s for s in both.values() if s['side']==1)
    assert entry['no_target'] is True and entry['stop_price']>0 and 'exit_short' in entry and 'exit_long' not in entry

def test_plain_series_strategies_produce_the_same_signals_as_before():
    signals,f=prepare(synthetic(4000),Config(strategy='trend_pullback',htf_filter='off'))
    assert signals and all(set(s)=={'side','atr','reference','regime'} for s in signals.values())
    assert not {'exit_long','stop_price'}&set(f.columns)

@pytest.mark.parametrize('bad',[lambda f:pd.DataFrame({'exit_long':True},index=f.index),lambda f:pd.DataFrame({'side':0,'surprise':1},index=f.index),
    lambda f:pd.DataFrame({'side':0,'stop_price':-1.},index=f.index),lambda f:pd.DataFrame({'side':0},index=f.index[::-1])])
def test_malformed_plans_are_rejected(monkeypatch,bad):
    monkeypatch.setitem(strategies.REGISTRY,'bad',lambda f,c:bad(f))
    with pytest.raises(ValueError): prepare(synthetic(2000),Config(strategy='bad',htf_filter='off'))

def test_look_ahead_in_an_exit_or_a_level_is_caught(monkeypatch):
    monkeypatch.setitem(strategies.REGISTRY,'plan',plan_strategy)
    assert prefix_invariant('plan')['ok']
    peek_exit=lambda f,c: pd.DataFrame({'side':0,'exit_long':f.close.shift(-1)<f.close},index=f.index)
    monkeypatch.setitem(strategies.REGISTRY,'peek_exit',peek_exit)
    assert not prefix_invariant('peek_exit')['ok']
    peek_stop=lambda f,c: pd.DataFrame({'side':(f.close>f.open).astype(int),'stop_price':f.low.shift(-1)*.99},index=f.index)
    monkeypatch.setitem(strategies.REGISTRY,'peek_stop',peek_stop)
    assert not prefix_invariant('peek_stop')['ok']
