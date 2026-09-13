import numpy as np
import pandas as pd
import pytest
from backend.config import Config
from backend.engine import simulate,fill_price,Position,liquidation_price
from backend.data import normalize,aggregate
from backend.strategies import prepare,features

def config(**kw):
    return Config(**({'start':'2025-01-01','end':'2025-01-02','balance':10000,'risk_pct':1,'max_open_risk_pct':5,
      'sizing':'fixed_notional','fixed_notional':1000,'htf_filter':'off','maker_bps':0,'taker_bps':0,
      'slippage_bps':0,'spread_bps':0,'funding_mode':'off','stop_atr':1,'reward_risk':2,'participation_pct':20,
      'max_hold_hours':8,**kw}))

def frame(rows=None,n=8):
    rows=rows or [[100,100.2,99.8,100,100000] for _ in range(n)]
    return pd.DataFrame(rows,columns=['open','high','low','close','volume'],index=pd.date_range('2025-01-01',periods=len(rows),freq='1min',tz='UTC'),dtype=float)

def ms(f,i): return int(f.index[i].timestamp()*1000)

def bundle(f,rates=None):
    return (f,rates or {},{'tick_size':.01,'qty_step':.001,'min_qty':.001,'min_notional':1},[],{'rows':len(f),'pair':'test'},f)

def run(f,c=None,side=1,signals=None,rates=None):
    c=c or config(); sig=signals if signals is not None else {ms(f,1):{'side':side,'atr':1.,'reference':100.,'regime':'range'}}
    return simulate(c,{'JTOUSDT':bundle(f,rates)},signal_overrides={'JTOUSDT':sig})

def test_flat_roundtrip_no_costs():
    r=run(frame()); assert r['metrics']['net_pnl']==pytest.approx(0); assert len(r['trades'])==1

@pytest.mark.parametrize('side',[1,-1])
def test_fee_on_entry_and_exit_notional(side):
    f=frame();f.iloc[-1,0:4]=[102,102.1,101.9,102] if side==1 else [98,98.1,97.9,98]
    r=run(f,config(taker_bps=10,reward_risk=5),side);t=r['trades'][0]
    assert t['qty']==10
    assert t['fees']==pytest.approx(10*(100+t['exit_price'])*.001)
    assert t['net_pnl']==pytest.approx(t['gross_pnl']-t['fees'])

def test_cost_identity_does_not_double_count_slippage():
    t=run(frame(),config(taker_bps=5,slippage_bps=3,spread_bps=2))['trades'][0]
    assert t['net_pnl']==pytest.approx(t['raw_pnl']-t['fees']-t['slippage']-t['spread']-t['funding']-t['liquidation_fee'])

@pytest.mark.parametrize('side',[1,-1])
def test_market_fill_adverse_both_sides(side):
    p,slip,spread=fill_price(100,side,config(slippage_bps=10,spread_bps=4),.01)
    assert side*(p-100)==pytest.approx(.12)
    assert slip==pytest.approx(.1);assert spread==pytest.approx(.02)

def test_leverage_does_not_multiply_fixed_notional_profit():
    f=frame();f.iloc[-1]=[100,100.8,99.9,100.5,100000]
    a=run(f,config(leverage=2)); b=run(f,config(leverage=10))
    assert a['metrics']['net_pnl']==pytest.approx(b['metrics']['net_pnl'])
    assert a['trades'][0]['margin']==pytest.approx(b['trades'][0]['margin']*5)

@pytest.mark.parametrize('side',[1,-1])
def test_historical_funding_sign_and_open_boundary(side):
    f=frame();r=run(f,side=side,rates={ms(f,1):.0001,ms(f,3):.0002})
    assert r['trades'][0]['funding']==pytest.approx(side*.2)
    assert r['metrics']['net_pnl']==pytest.approx(-side*.2)

def test_negative_funding_credits_long():
    f=frame();r=run(f,rates={ms(f,3):-.001});assert r['metrics']['net_pnl']==pytest.approx(1)

def test_stop_before_target_in_ambiguous_bar():
    f=frame();f.iloc[2]=[100,103,98,100,100000]
    t=run(f)['trades'][0];assert t['reason']=='stop';assert t['exit_price']==99;assert t['net_pnl']==-10

def test_gap_stop_uses_open_not_stop():
    f=frame();f.iloc[2]=[97,98,96,97,100000]
    t=run(f)['trades'][0];assert t['reason']=='stop_gap';assert t['exit_price']==97;assert t['net_pnl']==-30

def test_target_gap_is_known_before_later_bar_low():
    f=frame();f.iloc[2]=[103,104,97,100,100000]
    t=run(f)['trades'][0];assert t['reason']=='target_gap';assert t['exit_price']==103

def test_risk_sizing_includes_fees_and_fills():
    c=config(sizing='risk',risk_pct=.5,taker_bps=10,slippage_bps=5,spread_bps=2)
    t=run(frame(),c)['trades'][0];assert t['initial_risk']<=50.000001;assert t['initial_risk']>49

def test_prior_volume_caps_quantity():
    f=frame();f.iloc[0,4]=20;t=run(f,config(participation_pct=1))['trades'][0];assert t['qty']==pytest.approx(.2)

def test_zero_prior_volume_rejects_entry():
    f=frame();f.iloc[0,4]=0;r=run(f);assert not r['trades'];assert r['diagnostics']['rejected']

def test_limit_touch_not_fill():
    f=frame();f.iloc[:,2]=99.9
    r=run(f,config(entry_order='limit',limit_offset_bps=10,limit_penetration_bps=1))
    assert not r['trades']

def test_limit_entry_bar_cannot_take_profit():
    f=frame();f.iloc[1]=[100,105,99.5,100,100000]
    c=config(entry_order='limit',limit_offset_bps=10,limit_penetration_bps=1)
    t=run(f,c)['trades'][0];assert t['entry_price']==pytest.approx(99.9);assert t['reason']=='end_of_test'

def test_post_only_crossing_order_rejected():
    f=frame();f.iloc[1]=[98,99,97,98,100000]
    assert not run(f,config(entry_order='limit',limit_offset_bps=10))['trades']

def test_limit_maker_fee_and_no_entry_spread():
    f=frame();f.iloc[1]=[100,100.2,99.5,100,100000]
    t=run(f,config(entry_order='limit',limit_offset_bps=10,maker_bps=2,taker_bps=5,spread_bps=2,slippage_bps=3))['trades'][0]
    assert t['fees']==pytest.approx(t['qty']*(t['entry_price']*.0002+t['exit_price']*.0005))
    assert t['spread']==pytest.approx(t['qty']*t['exit_reference']*.0001)

def test_trailing_amendment_not_applied_to_same_bar():
    f=frame();f.iloc[2]=[100,101,99.6,100.9,100000];f.iloc[3]=[100.9,101,100.2,100.3,100000]
    t=run(f,config(trailing_atr=.5,reward_risk=5))['trades'][0]
    assert t['exit_time']==ms(f,4);assert t['exit_price']==pytest.approx(100.4)

def test_breakeven_uses_close_and_next_bar():
    f=frame();f.iloc[2]=[100,101.2,99.5,101,100000];f.iloc[3]=[101,101.2,99.9,100,100000]
    t=run(f,config(breakeven_r=1,reward_risk=5))['trades'][0];assert t['exit_time']==ms(f,4);assert t['exit_price']==100

def test_no_profit_from_signal_candle():
    f=frame();f.iloc[0]=[90,100,89,100,100000]
    t=run(f)['trades'][0];assert t['entry_time']==ms(f,1);assert t['entry_price']==100

def test_margin_limit_across_shared_portfolio():
    f=frame();c=config(pairs=['JTOUSDT','SOLUSDT'],max_positions=1)
    s={ms(f,1):{'side':1,'atr':1,'reference':100}}
    r=simulate(c,{p:bundle(f) for p in c.pairs},signal_overrides={p:s for p in c.pairs})
    assert len(r['trades'])==1;assert r['diagnostics']['rejected']['position_limit']==1

def test_intrabar_exit_does_not_finance_boundary_entry():
    f=frame();f.iloc[2]=[100,103,99.9,102,100000]
    c=config(pairs=['JTOUSDT','SOLUSDT'],max_positions=1)
    s1={ms(f,1):{'side':1,'atr':1,'reference':100}};s2={ms(f,2):{'side':1,'atr':1,'reference':100}}
    r=simulate(c,{'JTOUSDT':bundle(f),'SOLUSDT':bundle(frame())},signal_overrides={'JTOUSDT':s1,'SOLUSDT':s2})
    assert [t['pair'] for t in r['trades']]==['JTOUSDT']

def test_open_exit_can_release_capital_at_boundary():
    f=frame();f.iloc[2]=[103,103.1,102.9,103,100000]
    c=config(pairs=['JTOUSDT','SOLUSDT'],max_positions=1)
    r=simulate(c,{'JTOUSDT':bundle(f),'SOLUSDT':bundle(frame())},signal_overrides={'JTOUSDT':{ms(f,1):{'side':1,'atr':1,'reference':100}},'SOLUSDT':{ms(f,2):{'side':1,'atr':1,'reference':100}}})
    assert len(r['trades'])==2

def test_time_exit_and_final_equity_reconcile():
    f=frame(n=12);r=run(f,config(max_hold_hours=.1));t=r['trades'][0]
    assert t['reason']=='time_exit';assert t['hold_hours']==pytest.approx(.1)
    assert r['equity'][-1]['equity']==pytest.approx(10000+sum(t['net_pnl'] for t in r['trades']))

def test_gap_liquidation_is_charged():
    f=frame();f.iloc[2]=[70,71,69,70,100000]
    r=run(f);assert r['trades'][0]['reason']=='isolated_liquidation_gap';assert r['metrics']['liquidation_fee']>0

def test_stop_beyond_liquidation_rejected():
    f=frame();c=config(leverage=25,stop_atr=10)
    r=run(f,c);assert not r['trades'];assert r['diagnostics']['rejected']['stop_beyond_liquidation']==1

@pytest.mark.parametrize('side',[1,-1])
def test_isolated_liquidation_equation(side):
    p=Position('JTOUSDT',side,10,100,100,0,99,102,1,200,10,1,0,0,'range',1,funding=2)
    c=config();lp=liquidation_price(p,c)
    eq=p.margin-p.funding-p.entry_fee+side*p.qty*(lp-p.entry)
    maint=p.qty*lp*(c.maintenance_margin_pct/100+c.liquidation_fee_bps/10000)
    assert eq==pytest.approx(maint)

def test_duplicate_conflict_rejected():
    f=frame().reset_index().rename(columns={'index':'timestamp'});dup=f.iloc[[0]].copy();dup['close']=100.1
    with pytest.raises(ValueError,match='Conflicting'): normalize(pd.concat([f,dup]))

def test_identical_duplicate_removed():
    f=frame().reset_index().rename(columns={'index':'timestamp'});r=normalize(pd.concat([f,f.iloc[[0]]]))
    assert len(r)==len(f);assert r.attrs['duplicates_removed']==1

def test_incomplete_aggregate_is_not_forward_filled():
    f=frame(n=10).drop(frame(n=10).index[2]);r=aggregate(f,5);assert len(r)==1;assert r.index[0].minute==5

def test_invalid_ohlc_rejected():
    f=frame().reset_index().rename(columns={'index':'timestamp'});f.loc[0,'high']=90
    with pytest.raises(ValueError,match='range'): normalize(f)

@pytest.mark.parametrize('strategy',['trend_pullback','breakout_retest','vwap_reversion'])
def test_signals_are_prefix_invariant(strategy):
    rng=np.random.default_rng(19);n=20000;close=100*np.exp(np.cumsum(rng.normal(0,.001,n)));op=np.r_[close[0],close[:-1]]
    f=pd.DataFrame({'open':op,'high':np.maximum(op,close)*1.0005,'low':np.minimum(op,close)*.9995,'close':close,'volume':1000},index=pd.date_range('2025-01-01',periods=n,freq='1min',tz='UTC'))
    c=config(strategy=strategy,htf_filter='both',htf_ema=10)
    a,fa=prepare(f.iloc[:15000],c);b,fb=prepare(f,c)
    assert a=={t:s for t,s in b.items() if t<=ms(f,14999)+60000}
    pd.testing.assert_frame_equal(fa,fb.loc[fa.index])

def test_config_rejects_nonfinite_and_invalid_dates():
    with pytest.raises(ValueError): config(risk_pct=float('nan'))
    with pytest.raises(ValueError): config(end='2024-12-31')
    with pytest.raises(ValueError): config(pairs=['BTCUSD'])

def test_empty_strategy_does_not_report_infinite_profit_factor():
    r=run(frame(),signals={});assert r['metrics']['profit_factor'] is None;assert r['metrics']['trade_count']==0
