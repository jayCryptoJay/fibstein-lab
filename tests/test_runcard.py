import json
import pandas as pd
import pytest
from backend.config import Config
from backend.engine import simulate,metrics
from backend import runcard

def config(**kw):
    return Config(**({'start':'2025-01-01','end':'2025-01-02','balance':10000,'risk_pct':1,'max_open_risk_pct':5,
      'sizing':'fixed_notional','fixed_notional':1000,'htf_filter':'off','taker_bps':5,'slippage_bps':3,'spread_bps':2,
      'funding_mode':'off','stop_atr':1,'reward_risk':2,'participation_pct':20,'max_hold_hours':8,**kw}))

def engine_result():
    rows=[[100,100.2,99.8,100,100000] for _ in range(8)]; rows[-1]=[102,102.1,101.9,102,100000]
    f=pd.DataFrame(rows,columns=['open','high','low','close','volume'],index=pd.date_range('2025-01-01',periods=8,freq='1min',tz='UTC'),dtype=float)
    bundle=(f,{},{'tick_size':.01,'qty_step':.001,'min_qty':.001,'min_notional':1},[],{'rows':len(f),'pair':'test'},f)
    sig={int(f.index[1].timestamp()*1000):{'side':1,'atr':1.,'reference':100.,'regime':'range'}}
    r=simulate(config(),{'JTOUSDT':bundle},signal_overrides={'JTOUSDT':sig}); r.update(id='a'*16,kind='backtest',created='2025-01-02T00:00:00+00:00')
    return r

def trade(net,regime='range',side='long',pair='JTOUSDT',reason='stop',risk=10.,mfe=0.):
    return {'pair':pair,'side':side,'regime':regime,'reason':reason,'net_pnl':float(net),'net_r':net/risk,'initial_risk':risk,'mfe_usdt':mfe,
            'hold_hours':1.,'raw_pnl':float(net)+1,'gross_pnl':float(net)+.5,'fees':.5,'slippage':.3,'spread':.2,'funding':0.,'liquidation_fee':0.}

def result_of(trades,kind='backtest',**extra):
    equity=[{'timestamp':0,'equity':1000.}]
    for i,t in enumerate(trades): equity.append({'timestamp':i+1,'equity':equity[-1]['equity']+t['net_pnl']})
    return {'id':'b'*16,'kind':kind,'created':'2025-01-02T00:00:00+00:00','config':config(balance=1000).model_dump(mode='json'),
            'metrics':metrics(trades,equity,1000,50),'trades':trades,'warnings':['W1. Second sentence.','W2'],**extra}

def ids(result): return [f['id'] for f in runcard.findings(result)]
def one(result,fid): return next(f for f in runcard.findings(result) if f['id']==fid)

def test_card_copies_engine_numbers_and_keeps_every_warning():
    r=engine_result(); k=runcard.card(r,defaults=Config().model_dump(mode='json'))
    assert k['schema']=='fibstein.runcard/1'
    assert k['warnings']==r['warnings'] and len(k['warnings'])>=5
    c=k['costs']; assert c['net_pnl']==pytest.approx(c['raw_pnl']-c['total'],abs=.02)
    assert c['net_pnl']==pytest.approx(r['metrics']['net_pnl'],abs=.005)
    assert k['splits'][0]['metrics']['trades']==r['metrics']['trade_count']==1
    assert k['splits'][0]['held_out'] is False and k['verdict']['evidence']=='In-sample'
    assert k['signals']=={'total':1,'entered':1,'rejected':{},'rejected_total':0,'ignored_or_pending':0,'peak_open_positions':1}
    assert k['changed_from_default']['fixed_notional']==1000 and 'pairs' not in k['changed_from_default']
    json.dumps(k,allow_nan=False)

def test_holdout_is_exposed_as_status_only():
    k=runcard.card(engine_result())
    assert k['holdout']=={'status':'not_configured'}
    k=runcard.card(engine_result(),{'holdout':{'status':'passed','uses':1}})
    assert set(k['holdout'])<={'status','uses'}

def test_findings_are_deterministic():
    r=result_of([trade(-10,'range') for _ in range(15)]+[trade(5,'uptrend') for _ in range(25)])
    assert runcard.findings(r)==runcard.findings(r)

def test_loss_concentration_needs_more_than_its_share_of_trades():
    trades=[trade(-10,'range') for _ in range(15)]+[trade(-5,'uptrend') for _ in range(5)]+[trade(5,'uptrend') for _ in range(20)]
    f=one(result_of(trades),'loss_concentration_regime')
    assert f['data']=={'label':'range','loss_share':pytest.approx(150/175,abs=.001),'trade_share':.375,'trades':15,'net_pnl':-150.}
    assert '86% of losses came in regime range, which holds 38% of trades' in f['text']
    # Losses spread in proportion to trades are not a finding.
    even=[trade(-10,'range') for _ in range(12)]+[trade(10,'range') for _ in range(8)]+[trade(-10,'uptrend') for _ in range(12)]+[trade(10,'uptrend') for _ in range(8)]
    assert 'loss_concentration_regime' not in ids(result_of(even))

def test_single_source_profit_and_side_asymmetry():
    trades=[trade(10,'uptrend','long') for _ in range(20)]+[trade(-4,'range','short') for _ in range(20)]
    r=result_of(trades)
    assert one(r,'single_source_regime')['data']=={'label':'uptrend','net_pnl':200.,'rest':-80.}
    assert one(r,'side_asymmetry')['data']['losing']=='short'

def test_gave_back_counts_losers_that_reached_one_r():
    trades=[trade(-10,mfe=12) for _ in range(6)]+[trade(-10,mfe=2) for _ in range(6)]+[trade(20) for _ in range(20)]
    f=one(result_of(trades),'gave_back_1r'); assert f['data']=={'losers':12,'reached_1r':6}
    assert 'breakeven and trailing stops were off' in f['text']

def test_sample_size_and_cost_headroom_thresholds():
    r=result_of([trade(5) for _ in range(20)]); assert one(r,'sample_size')['severity']=='problem'
    r=result_of([trade(5) for _ in range(60)]); assert one(r,'sample_size')['severity']=='caution'
    r=result_of([trade(5) for _ in range(120)]); assert 'sample_size' not in ids(r)
    # raw 6.0, costs 1.0 per trade: headroom 6x, costs 17% of raw.
    f=one(r,'cost_headroom'); assert f['severity']=='info' and f['data']['breakeven_multiple']==6.0
    assert one(result_of([trade(-5) for _ in range(40)]),'losing_before_costs')['severity']=='problem'

def test_no_trades_is_the_only_finding():
    assert ids(result_of([]))==['no_trades']

def fold(n,selected,train_net,test_net,candidates=None):
    m=lambda net,r:{**metrics([],[{'timestamp':0,'equity':1000.},{'timestamp':1,'equity':1000.+net}],1000,0),'trade_count':50,'expectancy_r':r}
    ranking=candidates or [{'parameters':selected,'score':train_net,'eligible':True,'metrics':m(train_net,train_net/100)}]
    return {'fold':n,'train_start':'2025-01-01','train_end':'2025-01-10','test_start':'2025-01-10','test_end':'2025-01-20','selected':selected,
            'training_ranking':ranking,'test_metrics':m(test_net,test_net/100)}

def test_walkforward_findings_name_the_least_bad_pick_and_instability():
    trades=[trade(-5) for _ in range(40)]
    r=result_of(trades,'walkforward',folds=[fold(1,{'stop_atr':1},-20,-5),fold(2,{'stop_atr':2},30,-10)])
    assert one(r,'least_bad_pick')['data']=={'folds':[1]}
    assert one(r,'fold_consistency')['data']['profitable']==0
    assert one(r,'selection_stability')['severity']=='caution'
    assert one(r,'train_test_decay')['data']=={'training_expectancy_r':.05,'held_out_expectancy_r':-.075}
    k=runcard.card(r)
    assert [s['name'] for s in k['splits']]==['fold 1 training','fold 1 test','fold 2 training','fold 2 test','held-out total']
    assert [s['held_out'] for s in k['splits']]==[False,True,False,True,True]
    assert k['verdict']['evidence']=='Held out across 2 folds'
    assert k['breakdown']['regime'][0]['trades']==40  # Rebuilt from trades; walk-forward results carry no groups.

def test_stress_finding_reports_first_losing_multiplier():
    row=lambda mult,net:{'settings':{'stress_multiplier':mult},'metrics':metrics([],[{'timestamp':0,'equity':1000.},{'timestamp':1,'equity':1000.+net}],1000,0)}
    r={'id':'c'*16,'kind':'stress','config':config().model_dump(mode='json'),'rows':[row(1,40),row(2,-5),row(3,-30)],'warnings':[]}
    f=one(r,'cost_stress'); assert f['severity']=='problem' and 'turns negative at 2×' in f['text']
    r['rows']=[row(1,40),row(2,20),row(3,5)]; assert one(r,'cost_stress')['severity']=='info'
    k=runcard.card(r); assert k['verdict'] is None and len(k['rows'])==3 and k['costs'] is None

def test_engine_change_is_flagged():
    k=runcard.card(engine_result(),{'engine_current':False})
    assert 'engine_changed' in [f['id'] for f in k['findings']] and k['run']['engine_current'] is False

def test_digest_is_short_and_leads_with_the_verdict():
    r=engine_result(); k=runcard.card(r,{'selection':{'label':'best of 7','study_trials':1,'lineage_trials':7},'strategy':{'key':'trend_pullback','status':'draft','parent':None}})
    text=runcard.digest(k); lines=text.splitlines()
    assert lines[0].startswith('# Run '+'a'*16) and 'best of 7' in lines[2]
    assert any(line.startswith('**Verdict (In-sample):**') for line in lines[:6])
    assert len(lines)<=45
    assert f'## Assumptions ({len(r["warnings"])};' in text
