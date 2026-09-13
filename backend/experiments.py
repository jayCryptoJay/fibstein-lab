"""Bounded searches: parameter selection never sees the held-out window."""
from itertools import product
import pandas as pd
from .config import Config
from .data import load_pair,utc
from .engine import simulate,metrics

def changed(c,**kw): return Config.model_validate({**c.model_dump(),**kw})

def experiment(c,mode,grid=None,folds=3,min_trades=20,progress=lambda x:None,cancel=lambda:False):
    bundles={p:load_pair(p,c) for p in c.pairs}
    def run(config,label):
        return simulate(config,bundles,progress=lambda msg:progress(f'{label} · {msg}'),cancel=cancel)
    if mode in ('compare','stress'):
        options=[{'strategy':s} for s in ['trend_pullback','breakout_retest','vwap_reversion']] if mode=='compare' else [{'stress_multiplier':x} for x in [1,2,3]]
        rows=[]; inherited=[]
        for option in options:
            r=run(changed(c,**option),str(option)); rows.append({'settings':option,'metrics':r['metrics']}); inherited.extend(r['warnings'])
        return {'kind':mode,'config':c.model_dump(mode='json'),'rows':rows,'data_reports':[b[4] for b in bundles.values()], 'warnings':list(dict.fromkeys(['Same-period comparison is exploratory, not out-of-sample evidence.','Strategies may trade different opportunities. Cost stress reruns sizing and fills; net returns need not be monotonic.',*inherited]))}
    grid=grid or {'stop_atr':[1,1.5,2],'reward_risk':[1.5,2]}
    if set(grid)-{'stop_atr','reward_risk','ema_fast','ema_slow','vwap_band_atr','breakout_lookback'}: raise ValueError('Unsupported search parameter.')
    if not grid or any(not isinstance(v,list) or not v for v in grid.values()): raise ValueError('Grid values must be nonempty arrays.')
    combinations=list(product(*grid.values()))
    if len(combinations)>12: raise ValueError('Maximum 12 candidates per experiment; narrow the grid.')
    candidates=[dict(zip(grid,x)) for x in combinations]
    for candidate in candidates: changed(c,**candidate)
    days=(c.end-c.start).days
    if days<10: raise ValueError('Use at least 10 days for chronological validation.')
    if not 1<=folds<=5 or not 1<=min_trades<=10000: raise ValueError('Folds must be 1–5; minimum trades 1–10000.')
    split=c.start+pd.Timedelta(days=int(days*.6))
    if mode=='grid': folds=1
    remaining=(c.end-split).days
    if remaining<folds: raise ValueError('Insufficient held-out days for the fold count.')
    boundaries=[split+pd.Timedelta(days=remaining*i//folds) for i in range(folds)]+[c.end]
    fold_results=[]; oos_trades=[]; oos_equity=[]; balance=c.balance; exposure_weighted=0.; total_bars=0
    for i in range(folds):
        train_end=boundaries[i]; test_end=boundaries[i+1]; ranking=[]
        for j,option in enumerate(candidates):
            result=run(changed(c,end=train_end,**option),f'Fold {i+1}/{folds}, candidate {j+1}/{len(candidates)}')
            m=result['metrics']; score=m['net_return_pct']-m['max_drawdown_pct']
            ranking.append({'parameters':option,'score':score,'eligible':m['trade_count']>=min_trades,'metrics':m})
        ranking.sort(key=lambda x:x['score'],reverse=True)
        eligible=[x for x in ranking if x['eligible']]
        if not eligible: raise ValueError(f'Fold {i+1}: no candidate reached {min_trades} training trades. Lengthen the range or explicitly lower the minimum.')
        winner=eligible[0]
        test=run(changed(c,start=train_end,end=test_end,balance=balance,**winner['parameters']),f'Fold {i+1}/{folds}, held-out test')
        fold_results.append({'fold':i+1,'train_start':str(c.start),'train_end':str(train_end),'test_start':str(train_end),'test_end':str(test_end),
                             'selected':winner['parameters'],'training_ranking':ranking,'test_metrics':test['metrics']})
        oos_trades.extend(test['trades']); oos_equity.extend(test['equity'] if not oos_equity else test['equity'][1:])
        n=len(test['equity'])-1; total_bars+=n; exposure_weighted+=test['metrics']['exposure_pct']*n
        balance=test['metrics']['final_equity']
        if balance<=0: break
    result={'kind':mode,'config':c.model_dump(mode='json'),'folds':fold_results,'equity':oos_equity,'trades':oos_trades,
            'metrics':metrics(oos_trades,oos_equity,c.balance,exposure_weighted/max(total_bars,1)),
            'warnings':['Only held-out trades are included in these headline metrics. Positions are closed at fold boundaries; equity compounds across tests.',
                        'Training score = net return % minus maximum drawdown %. This is a heuristic, not proof of an optimal strategy.',
                        'Repeatedly inspecting held-out results makes them part of research; reserve a fresh final period before trading.',
                        'All engine execution, liquidation and funding approximations also apply.']}
    if result['metrics']['trade_count']<100: result['warnings'].append('Fewer than 100 held-out trades: insufficient evidence for stable estimates.')
    result['warnings']=list(dict.fromkeys([*result['warnings'],*test.get('warnings',[])]))
    result['data_reports']=[b[4] for b in bundles.values() if b is not None]
    return result
