"""Causal indicator preparation. Every signal index is its availability time."""
import numpy as np
import pandas as pd
from .data import aggregate
from .config import STRATEGIES

def ema(s,n): return s.ewm(span=n,adjust=False,min_periods=n).mean()

def features(raw,c):
    f=aggregate(raw,c.timeframe)
    f.index=f.index+pd.Timedelta(minutes=c.timeframe)
    f['fast']=ema(f.close,c.ema_fast); f['slow']=ema(f.close,c.ema_slow)
    prev=f.close.shift()
    tr=pd.concat([f.high-f.low,(f.high-prev).abs(),(f.low-prev).abs()],axis=1).max(axis=1)
    f['atr']=tr.ewm(alpha=1/c.atr_length,adjust=False,min_periods=c.atr_length).mean()
    delta=f.close.diff(); gain=delta.clip(lower=0).ewm(alpha=1/c.rsi_length,adjust=False,min_periods=c.rsi_length).mean()
    loss=(-delta.clip(upper=0)).ewm(alpha=1/c.rsi_length,adjust=False,min_periods=c.rsi_length).mean()
    f['rsi']=100-100/(1+gain/loss.replace(0,np.nan))
    f.loc[(loss==0)&(gain>0),'rsi']=100; f.loc[(loss==0)&(gain==0),'rsi']=50
    # Session belongs to the candle's opening day, including 23:45->00:00.
    day=(f.index-pd.Timedelta(minutes=c.timeframe)).normalize()
    pv=(f.high+f.low+f.close)/3*f.volume
    f['vwap']=pv.groupby(day).cumsum()/f.volume.groupby(day).cumsum().replace(0,np.nan)
    f['htf_long']=True; f['htf_short']=True
    for mins,key in [(60,'1h'),(240,'4h')]:
        if c.htf_filter not in [key,'both']: continue
        higher=aggregate(raw,mins); higher.index+=pd.Timedelta(minutes=mins)
        higher['ma']=ema(higher.close,c.htf_ema)
        aligned=higher[['close','ma']].reindex(f.index,method='ffill')
        f['htf_long'] &= aligned.close>aligned.ma
        f['htf_short'] &= aligned.close<aligned.ma
    f['regime']=np.where((f.fast-f.slow).abs()/f.atr<c.range_threshold,'range',np.where(f.fast>f.slow,'uptrend','downtrend'))
    return f

def trend_pullback(f,c):
    long=(f.fast>f.slow)&(f.close>f.slow)&(f.low<=f.fast)&(f.close>f.fast)&(f.close>f.open)
    short=(f.fast<f.slow)&(f.close<f.slow)&(f.high>=f.fast)&(f.close<f.fast)&(f.close<f.open)
    return pd.Series(np.select([long,short],[1,-1],default=0),index=f.index)

def breakout_retest(f,c):
    upper=f.high.rolling(c.breakout_lookback).max().shift(); lower=f.low.rolling(c.breakout_lookback).min().shift()
    signals=np.zeros(len(f),dtype=int); pending=None
    for i,row in enumerate(f.itertuples()):
        if not np.isfinite(row.atr): continue
        if pending:
            side,level,created=pending
            if i-created>c.retest_bars or (side==1 and row.close<level-row.atr) or (side==-1 and row.close>level+row.atr): pending=None
            elif i>created:
                tol=row.atr*c.retest_atr
                ok=(row.low<=level+tol and row.close>=level and row.close>row.open) if side==1 else (row.high>=level-tol and row.close<=level and row.close<row.open)
                if ok: signals[i]=side; pending=None; continue
        if pending is None:
            if row.close>upper.iloc[i]: pending=(1,float(upper.iloc[i]),i)
            elif row.close<lower.iloc[i]: pending=(-1,float(lower.iloc[i]),i)
    return pd.Series(signals,index=f.index)

def vwap_reversion(f,c):
    ranged=(f.fast-f.slow).abs()/f.atr<c.range_threshold
    long=ranged&(f.close<f.vwap-c.vwap_band_atr*f.atr)&(f.rsi<c.rsi_oversold)&(f.close>f.open)
    short=ranged&(f.close>f.vwap+c.vwap_band_atr*f.atr)&(f.rsi>c.rsi_overbought)&(f.close<f.open)
    return pd.Series(np.select([long,short],[1,-1],default=0),index=f.index)

REGISTRY={'trend_pullback':trend_pullback,'breakout_retest':breakout_retest,'vwap_reversion':vwap_reversion}

PLAN_FLAGS=['exit_long','exit_short','no_target','no_time_exit']
PLAN_PRICES=['stop_price','target_price','amend_stop','amend_target']

def register_strategy(key,name,rule,function):
    """Register trusted local Python code; function(features, config) -> Series[-1,0,1], or a plan frame.

    A plan frame has a `side` column of entries (-1, 0, 1) and any of these, all on the feature index:
    exit_long / exit_short (close that side at the next open), stop_price / target_price (levels for an
    entry on this bar), amend_stop / amend_target (new levels for the open position), no_target and
    no_time_exit (this entry has no profit target / is not closed by the time limit).
    """
    if key in REGISTRY: raise ValueError('Strategy key already registered.')
    REGISTRY[key]=function; STRATEGIES[key]={'name':name,'rule':rule}

def prepare(raw,c):
    if c.strategy not in REGISTRY: raise ValueError(f'Unknown strategy: {c.strategy}')
    f=features(raw,c); out=REGISTRY[c.strategy](f.copy(),c); plan=None
    if isinstance(out,pd.DataFrame):
        if 'side' not in out or not out.index.equals(f.index) or set(out.columns)-{'side',*PLAN_FLAGS,*PLAN_PRICES}:
            raise ValueError('A strategy plan needs an aligned `side` column and only known plan columns.')
        plan=out; side=out['side']
    else: side=out
    if not isinstance(side,pd.Series) or not side.index.equals(f.index) or not side.isin([-1,0,1]).all():
        raise ValueError('Strategy must return an aligned Series containing only -1, 0, 1.')
    valid=f[['fast','slow','atr','rsi']].notna().all(axis=1)&(f.atr>0)
    side=side.where(valid,0)
    side=side.where(~((side==1)&(~f.htf_long)),0).where(~((side==-1)&(~f.htf_short)),0)
    if c.direction=='long': side=side.clip(lower=0)
    if c.direction=='short': side=side.clip(upper=0)
    f['side']=side
    signals={int(t.timestamp()*1000):{'side':int(r.side),'atr':float(r.atr),'reference':float(r.close),'regime':r.regime} for t,r in f[f.side!=0].iterrows()}
    if plan is not None:
        # Entry filters above gate entries only. A strategy's own exits and levels are never filtered away.
        flags={k:plan[k].fillna(False).astype(bool) for k in PLAN_FLAGS if k in plan}
        prices={k:pd.to_numeric(plan[k],errors='coerce') for k in PLAN_PRICES if k in plan}
        for k,v in prices.items():
            if (v.dropna()<=0).any() or not np.isfinite(v.dropna()).all(): raise ValueError(f'Plan column {k} must hold positive finite prices or NaN.')
        for k,v in {**flags,**prices}.items(): f[k]=v
        active=pd.concat([f.side!=0,*flags.values(),*[v.notna() for v in prices.values()]],axis=1).any(axis=1)
        for t,r in f[active].iterrows():
            s=signals.setdefault(int(t.timestamp()*1000),{'side':0,'atr':float(r.atr),'reference':float(r.close),'regime':r.regime})
            for k in flags:
                if r[k]: s[k]=True
            for k in prices:
                if pd.notna(r[k]): s[k]=float(r[k])
    return signals,f
