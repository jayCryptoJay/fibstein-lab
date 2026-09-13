"""Event-driven linear USDT perpetual simulation. No exchange credentials or orders."""
from dataclasses import dataclass
from math import floor,ceil
from collections import Counter
import numpy as np
import pandas as pd
from .data import utc,load_pair
from .strategies import prepare

def round_price(value,tick,up): return (ceil(value/tick-1e-10) if up else floor(value/tick+1e-10))*tick

def fill_price(reference,side,c,tick=1e-8,maker=False):
    if maker: return reference,0.,0.
    spread=reference*c.spread_bps*c.stress_multiplier/20000
    slip=reference*c.slippage_bps*c.stress_multiplier/10000
    filled=round_price(reference+side*(spread+slip),tick,side==1)
    # Tick rounding is included in slippage so the cost identity remains exact.
    return filled,max(0,side*(filled-reference)-spread),spread

@dataclass
class Position:
    pair:str; side:int; qty:float; entry:float; reference:float; entered:int
    stop:float; target:float; distance:float; margin:float; risk:float; entry_fee:float
    entry_slip:float; entry_spread:float; regime:str; atr:float
    funding:float=0.; mae:float=0.; mfe:float=0.; limit_entry:bool=False

def liquidation_price(p,c,mmr=None):
    m=(c.maintenance_margin_pct if mmr is None else mmr)/100+c.liquidation_fee_bps/10000
    collateral=p.margin-p.funding-p.entry_fee
    if p.side==1: return max(0,(p.qty*p.entry-collateral)/(p.qty*(1-m)))
    return (p.qty*p.entry+collateral)/(p.qty*(1+m))

def metrics(trades,equity,initial,exposed):
    pnl=np.array([t['net_pnl'] for t in trades],dtype=float); wins=pnl[pnl>0]; losses=pnl[pnl<0]
    values=np.array([x['equity'] for x in equity]); peak=np.maximum.accumulate(np.maximum(values,initial))
    draw=(values-peak)/np.maximum(peak,1e-12)*100
    for point,dd in zip(equity,draw): point['drawdown_pct']=float(dd)
    n=len(pnl); final=float(values[-1]) if len(values) else initial
    costs={key:float(sum(t[key] for t in trades)) for key in ['fees','slippage','spread','funding','liquidation_fee','raw_pnl','gross_pnl']}
    return {'initial_equity':initial,'final_equity':final,'net_pnl':final-initial,'net_return_pct':(final/initial-1)*100,
      'max_drawdown_pct':float(-draw.min()) if len(draw) else 0,'profit_factor':float(wins.sum()/-losses.sum()) if len(losses) else None,
      'win_rate_pct':float((pnl>0).mean()*100) if n else 0,'expectancy':float(pnl.mean()) if n else 0,
      'expectancy_r':float(np.mean([t['net_r'] for t in trades])) if n else 0,'trade_count':n,
      'average_win':float(wins.mean()) if len(wins) else 0,'average_loss':float(losses.mean()) if len(losses) else 0,
      'average_hold_hours':float(np.mean([t['hold_hours'] for t in trades])) if n else 0,'exposure_pct':exposed,
      'liquidations':sum('liquidation' in t['reason'] for t in trades),**costs}

def simulate(c,bundles,signal_overrides=None,progress=lambda x:None,cancel=lambda:False):
    prepared={}; warnings=[]; reports=[]
    start=int(utc(c.start).timestamp()*1000); end=int(utc(c.end).timestamp()*1000); step=c.execution_minutes*60000
    for pair,bundle in bundles.items():
        frame,rates,rules,warn,report,raw=bundle; warnings.extend(warn); reports.append(report)
        signals,indicators=prepare(raw,c) if signal_overrides is None else (signal_overrides.get(pair,{}),None)
        frame=frame[(frame.index>=utc(c.start))&(frame.index<utc(c.end))]
        prepared[pair]={'frame':frame,'rates':rates,'rules':rules,'signals':signals,'indicators':indicators,'prior_volume':bundle[0].volume.shift()}
    times=sorted(set.intersection(*[set(int(t.timestamp()*1000) for t in v['frame'].index) for v in prepared.values()]))
    if not times: raise ValueError('No common execution candles in selected date range.')
    index=pd.to_datetime(times,unit='ms',utc=True)
    for v in prepared.values():
        v['values']=v['frame'].reindex(index)[['open','high','low','close','volume']].to_numpy(dtype=float)
        v['prev_vol']=v['prior_volume'].reindex(index).fillna(0).to_numpy()
    positions={}; pending={}; trades=[]; balance=c.balance; equity=[{'timestamp':times[0],'equity':balance}]
    rejected=Counter(); exposure=0; peak_open=0; total_signals=0
    symbols=list(c.pairs) # Explicit priority controls simultaneous signals.

    def close(pair,reference,t,reason,maker=False):
        nonlocal balance
        p=positions.pop(pair); rule=prepared[pair]['rules']
        filled,slip,spread=fill_price(reference,-p.side,c,rule['tick_size'],maker)
        fee=abs(p.qty*filled)*(c.maker_bps if maker else c.taker_bps)/10000
        liqfee=abs(p.qty*filled)*c.liquidation_fee_bps/10000 if 'liquidation' in reason else 0
        gross=p.side*p.qty*(filled-p.entry); net=gross-p.entry_fee-fee-p.funding-liqfee
        balance+=gross-fee-liqfee
        trades.append({'pair':pair,'side':'long' if p.side==1 else 'short','entry_time':p.entered,'exit_time':t,
          'entry_price':p.entry,'exit_price':filled,'entry_reference':p.reference,'exit_reference':reference,
          'qty':p.qty,'notional':p.qty*p.entry,'margin':p.margin,'initial_risk':p.risk,'stop_price':p.stop,'target_price':p.target,
          'reason':reason,'regime':p.regime,'raw_pnl':p.side*p.qty*(reference-p.reference),'gross_pnl':gross,
          'fees':p.entry_fee+fee,'slippage':p.entry_slip+slip*p.qty,'spread':p.entry_spread+spread*p.qty,'funding':p.funding,
          'liquidation_fee':liqfee,'net_pnl':net,'net_r':net/p.risk if p.risk else 0,'hold_hours':(t-p.entered)/3600000,
          'mae_usdt':p.mae,'mfe_usdt':p.mfe,'entry_order':'limit' if p.limit_entry else 'market'})

    for i,t in enumerate(times):
        if i%1000==0:
            if cancel(): raise ValueError('Cancelled.')
            progress(f'Simulating · {i/len(times)*100:.0f}% · {len(trades)} closed trades')
        bars={s:prepared[s]['values'][i] for s in symbols}
        opens={s:float(r[0]) for s,r in bars.items()}; closed=set()
        had_position=bool(positions)
        # Settlement occurs before exits/entries at the same boundary; newly opened positions do not pay.
        for pair,p in positions.items():
            rate=prepared[pair]['rates'].get(t)
            if rate is not None and p.entered<t:
                payment=p.side*p.qty*opens[pair]*rate; p.funding+=payment; balance-=payment

        def manage(pair,new_limit=False,phase='bar'):
            if pair not in positions: return
            p=positions[pair]; op,hi,lo,cl,vol=bars[pair]; rules=prepared[pair]['rules']
            liq=liquidation_price(p,c,rules.get('maintenance_margin_pct')) if c.margin_mode=='isolated' else (-1 if p.side==1 else float('inf'))
            breached=lambda px,level: px<=level if p.side==1 else px>=level
            adverse=lo if p.side==1 else hi
            # Market open is observed; limit entry's preceding open cannot trigger its newly created stop.
            if phase=='open':
                if breached(op,liq): close(pair,float(op),t,'isolated_liquidation_gap'); closed.add(pair); return
                if breached(op,p.stop): close(pair,float(op),t,'stop_gap'); closed.add(pair); return
                if t-p.entered>=c.max_hold_hours*3600000:
                    close(pair,float(op),t,'time_exit'); closed.add(pair); return
                penetration=p.target*c.limit_penetration_bps/10000 if c.target_order=='limit' else 0
                if (op>=p.target+penetration if p.side==1 else op<=p.target-penetration):
                    close(pair,float(p.target if c.target_order=='limit' else op),t,'target_gap',c.target_order=='limit'); closed.add(pair)
                return
            p.mae=min(p.mae,p.qty*(lo-p.entry) if p.side==1 else p.qty*(p.entry-hi))
            favorable=cl if new_limit else hi if p.side==1 else lo
            p.mfe=max(p.mfe,p.side*p.qty*(favorable-p.entry))
            # With a continuous trade-price path, the closer adverse barrier is crossed first.
            liq_first=(liq>=p.stop if p.side==1 else liq<=p.stop)
            if liq_first and breached(adverse,liq): close(pair,float(liq),t+step,'isolated_liquidation'); closed.add(pair); return
            stop_hit=breached(adverse,p.stop)
            if stop_hit:
                projected,_,_=fill_price(p.stop,-p.side,c,rules['tick_size'])
                reason='stop_liquidation' if breached(projected,liq) else 'stop'
                close(pair,float(p.stop),t+step,reason); closed.add(pair); return
            if breached(adverse,liq): close(pair,float(liq),t+step,'isolated_liquidation'); closed.add(pair); return
            # On ambiguous entry-limit bars, suppress profitable target fills.
            if not new_limit:
                penetration=p.target*c.limit_penetration_bps/10000 if c.target_order=='limit' else 0
                hit=hi>=p.target+penetration if p.side==1 else lo<=p.target-penetration
                if hit:
                    ref=p.target if c.target_order=='limit' else max(op,p.target) if p.side==1 else min(op,p.target)
                    close(pair,float(ref),t+step,'target',c.target_order=='limit'); closed.add(pair); return
            # Amendments use this CLOSED execution candle and become effective on the next one.
            if c.breakeven_r and p.side*(cl-p.entry)>=p.distance*c.breakeven_r:
                p.stop=max(p.stop,p.entry) if p.side==1 else min(p.stop,p.entry)
            if c.trailing_atr:
                trail=cl-p.side*p.atr*c.trailing_atr
                p.stop=max(p.stop,trail) if p.side==1 else min(p.stop,trail)

        # Only observable boundary exits may release capital for this boundary's entries.
        for pair in list(positions): manage(pair,phase='open')
        for pair in symbols:
            sig=prepared[pair]['signals'].get(t)
            if sig: total_signals+=1
            if pair in positions or pair in closed: continue
            if pair in pending and t>=pending[pair]['expires']: pending.pop(pair); rejected['expired_limit']+=1
            if sig and pair not in pending:
                order=dict(sig); order['created']=t
                order['expires']=t+c.limit_expiry_bars*c.timeframe*60000
                if c.entry_order=='limit':
                    rule=prepared[pair]['rules']; side=order['side']
                    order['price']=round_price(order['reference']*(1-side*c.limit_offset_bps/10000),rule['tick_size'],side==-1)
                    if (side==1 and order['price']>=opens[pair]) or (side==-1 and order['price']<=opens[pair]):
                        rejected['post_only_would_cross']+=1; continue
                pending[pair]=order
            if pair not in pending: continue
            order=pending[pair]; side=order['side']; maker=c.entry_order=='limit'
            if maker:
                penetration=order['price']*c.limit_penetration_bps/10000
                if not (bars[pair][2]<=order['price']-penetration if side==1 else bars[pair][1]>=order['price']+penetration): continue
                reference=order['price']
            else: reference=opens[pair]
            pending.pop(pair)
            if len(positions)>=c.max_positions: rejected['position_limit']+=1; continue
            eq=balance+sum(p.side*p.qty*((p.entry if p.limit_entry and p.entered==t else opens[s])-p.entry) for s,p in positions.items())
            if eq<=0: rejected['insolvent']+=1; continue
            rule=prepared[pair]['rules']; tick=rule['tick_size']
            entry,slip,spread=fill_price(reference,side,c,tick,maker)
            distance=max(order['atr']*c.stop_atr,2*tick)
            stop=round_price(entry-side*distance,tick,side==-1)
            target=round_price(entry+side*distance*c.reward_risk,tick,side==1)
            if min(entry,stop,target)<=0: rejected['invalid_price']+=1; continue
            stop_fill,_,_=fill_price(stop,-side,c,tick)
            fee_rate=(c.maker_bps if maker else c.taker_bps)/10000
            unit_risk=side*(entry-stop_fill)+entry*fee_rate+stop_fill*c.taker_bps/10000
            if unit_risk<=0: rejected['invalid_risk']+=1; continue
            existing_risk=sum(p.risk for p in positions.values())
            risk_budget=max(0,min(eq*c.risk_pct/100,eq*c.max_open_risk_pct/100-existing_risk))
            quantity=risk_budget/unit_risk
            if c.sizing=='fixed_notional': quantity=min(quantity,c.fixed_notional/entry)
            margin=sum(p.margin for p in positions.values()); exposure_notional=sum(p.qty*opens[s] for s,p in positions.items())
            available=max(0,min(balance-margin,eq*c.max_margin_pct/100-margin))
            quantity=min(quantity,available/(entry/c.leverage+entry*fee_rate),max(0,eq*c.max_exposure_pct/100-exposure_notional)/entry,
                         prepared[pair]['prev_vol'][i]*c.participation_pct/100)
            quantity=floor(quantity/rule['qty_step']+1e-10)*rule['qty_step']
            if quantity<rule['min_qty'] or quantity*entry<rule['min_notional']:
                rejected['margin_risk_liquidity_or_minimum']+=1; continue
            entry_fee=quantity*entry*fee_rate
            p=Position(pair,side,quantity,entry,reference,t,stop,target,abs(entry-stop),quantity*entry/c.leverage,
                       quantity*unit_risk,entry_fee,quantity*slip,quantity*spread,order.get('regime','unknown'),order['atr'],limit_entry=maker)
            liq=liquidation_price(p,c,rule.get('maintenance_margin_pct'))
            if c.margin_mode=='isolated' and (stop_fill<=liq if side==1 else stop_fill>=liq):
                rejected['stop_beyond_liquidation']+=1; continue
            positions[pair]=p; balance-=entry_fee; had_position=True
        peak_open=max(peak_open,len(positions))
        # Intrabar outcomes happen AFTER all boundary allocation decisions.
        if c.margin_mode=='cross' and positions:
            adverse={s:float(bars[s][2] if p.side==1 else bars[s][1]) for s,p in positions.items()}
            stressed=balance+sum(p.side*p.qty*(adverse[s]-p.entry) for s,p in positions.items())
            maintenance=sum(p.qty*adverse[s]*(prepared[s]['rules'].get('maintenance_margin_pct',c.maintenance_margin_pct)/100+c.liquidation_fee_bps/10000) for s,p in positions.items())
            if stressed<=maintenance:
                for s in list(positions): close(s,adverse[s],t+step,'cross_liquidation_stress'); closed.add(s)
        for pair in list(positions): manage(pair,new_limit=positions[pair].limit_entry and positions[pair].entered==t)
        if had_position or positions: exposure+=1
        peak_open=max(peak_open,len(positions))
        value=balance+sum(p.side*p.qty*(bars[s][3]-p.entry) for s,p in positions.items())
        equity.append({'timestamp':t+step,'equity':float(value)})
    for pair in list(positions): close(pair,float(prepared[pair]['values'][-1][3]),times[-1]+step,'end_of_test')
    equity[-1]['equity']=float(balance)
    stats=metrics(trades,equity,c.balance,100*exposure/len(times))
    if stats['trade_count']<100: warnings.append('Fewer than 100 trades: treat estimates and rankings as preliminary.')
    if c.funding_mode=='estimate': warnings.append('Funding is an explicit constant-rate estimate, not historical payments.')
    if c.funding_mode=='off': warnings.append('Funding is excluded; perpetual futures net returns are incomplete.')
    warnings.extend(['Liquidation is an approximation using trade-price candles, a fixed maintenance rate, and estimated liquidation fees. Historical mark-price risk tiers are not reconstructed.',
      'Spread and slippage are modeled assumptions. OHLCV cannot establish queue position, order-book depth, or actual market impact.',
      'Entry size is capped by the previous execution candle volume. Exits use modeled costs without a historical depth capacity model.',
      'Funding uses historical rates when selected. Settlement timestamps are floored to the execution boundary and its open proxies mark price; sub-minute order/settlement ordering is not observable.',
      'Reported drawdown uses execution-candle closes; intra-candle account drawdown can be larger.'])
    if c.execution_minutes>1: warnings.append('5-minute execution is less precise: ambiguous stops/targets use adverse ordering.')
    if c.margin_mode=='cross': warnings.append('Cross margin is a conservative stress approximation: simultaneous adverse candle extremes can overstate liquidation risk.')
    groups={}
    for key in ['pair','side','regime','reason']:
        groups[key]=[]
        for label in sorted(set(t[key] for t in trades)):
            subset=[t for t in trades if t[key]==label]; pnls=[t['net_pnl'] for t in subset]
            groups[key].append({'label':label,'trades':len(subset),'net_pnl':sum(pnls),'win_rate_pct':100*sum(x>0 for x in pnls)/len(pnls),'expectancy':float(np.mean(pnls))})
    price={s:[{'timestamp':int(t.timestamp()*1000)+c.timeframe*60000,'close':float(r.close)} for t,r in v['frame'].resample(f'{c.timeframe}min').last().dropna().iterrows()] for s,v in prepared.items()}
    return {'config':c.model_dump(mode='json'),'metrics':stats,'equity':equity,'trades':trades,'groups':groups,'price':price,
      'warnings':list(dict.fromkeys(warnings)),'data_reports':reports,'diagnostics':{'signals':total_signals,'rejected':dict(rejected),'peak_open_positions':peak_open,'ambiguous_policy':'Stop before target; limit-entry bar cannot take profit; trailing changes effective next bar','priority':symbols}}

def run_backtest(c,progress=lambda x:None,cancel=lambda:False):
    bundles={}
    for pair in c.pairs:
        if cancel(): raise ValueError('Cancelled.')
        progress(f'Validating {pair}'); bundles[pair]=load_pair(pair,c)
    return simulate(c,bundles,progress=progress,cancel=cancel)
