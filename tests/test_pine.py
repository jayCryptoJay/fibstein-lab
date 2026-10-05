import json
import numpy as np
import pandas as pd
import pytest
from backend import lab,pine,registry,strategies
from backend.causality import prefix_invariant,synthetic
from backend.config import Config,STRATEGIES
from backend.data import aggregate
from backend.engine import simulate
from backend.strategies import features,prepare

HEAD='//@version=5\nstrategy("Test")\n'
ENTRY='if close > open\n    strategy.entry("L", strategy.long)\n'
CROSS=HEAD+'''fast = ta.sma(close, 5)
slow = ta.sma(close, 13)
if ta.crossover(fast, slow)
    strategy.entry("Long", strategy.long)
if ta.crossunder(fast, slow)
    strategy.entry("Short", strategy.short)
'''
BRACKET=HEAD+'''atr = ta.atr(14)
if ta.crossover(close, ta.sma(close, 20)) and strategy.position_size == 0
    strategy.entry("L", strategy.long)
    strategy.exit("XL", "L", stop = close - 2 * atr, limit = close + 3 * atr)
'''

def config(**kw): return Config(**{'timeframe':15,'start':'2025-01-01','end':'2025-01-22','htf_filter':'off',**kw})

@pytest.fixture(scope='module')
def raw():
    r=synthetic(n=30000,seed=3); rng=np.random.default_rng(1)
    # Wider ranges and uneven volume, so stops, targets and volume-weighted indicators are all exercised.
    r['volume']=rng.uniform(100,5000,len(r)); r['high']=r[['open','close']].max(axis=1)*(1+rng.uniform(0,.002,len(r)))
    r['low']=r[['open','close']].min(axis=1)*(1-rng.uniform(0,.002,len(r))); return r

@pytest.fixture(scope='module')
def f(raw): return features(raw,config())

def run(source,f,watch=(),**kw): return pine.Program(source).execute(f,config(**kw),watch=watch)
def watched(source,f,*names,**kw): return {k:np.array(v,dtype=float) for k,v in run(source,f,names,**kw).watched.items()}
def same(a,b):
    a=np.asarray(a,dtype=float); b=np.asarray(b,dtype=float)
    return bool((np.isnan(a)==np.isnan(b)).all() and np.allclose(a[~np.isnan(a)],b[~np.isnan(b)],rtol=1e-9,atol=1e-9))
def seeded(x,n,alpha):
    """Pine's recursive averages: the first value is the plain mean of the first full window."""
    x=np.asarray(x,dtype=float); out=np.full(len(x),np.nan); v=np.nan
    for i in range(len(x)):
        if np.isnan(v): v=x[i-n+1:i+1].mean() if i>=n-1 else np.nan
        else: v=alpha*x[i]+(1-alpha)*v
        out[i]=v
    return out
ema=lambda x,n: seeded(x,n,2/(n+1)); rma=lambda x,n: seeded(x,n,1/n)
def refused(source): return ' | '.join(x['text'] for x in pine.check(source,dry_run=False)['refused'])

# ---- reading the script

def test_comments_spacing_and_wrapping_do_not_change_a_script_but_logic_does():
    plain=pine.Program(CROSS); assert not plain.report.refused and plain.report.version==5
    noisy=CROSS.replace('fast = ta.sma(close, 5)','// a comment\n\nfast   =   ta.sma(close,\n     5)   // wrapped inside brackets')
    assert pine.Program(noisy).digest==plain.digest
    assert pine.Program(CROSS.replace('13','14')).digest!=plain.digest
    # Indentation is logic in Pine: moving a line out of its block is a different script.
    assert pine.Program(CROSS.replace('    strategy.entry("Short"','strategy.entry("Short"')).digest!=plain.digest

def test_a_statement_wraps_onto_a_line_that_is_not_indented_by_four(f):
    w=watched(HEAD+'total = open +\n  high +\n       low\n'+ENTRY,f,'total')
    assert same(w['total'],f.open+f.high+f.low)

def test_version_decides_whole_number_division_and_short_circuiting(f):
    body='ratio = 7 / 2\ncalled() =>\n    strategy.entry("L", strategy.long)\n    true\nskip = close < 0 and called()\n'
    v5=run(HEAD+body,f,['ratio']); v6=run(HEAD.replace('=5','=6')+body,f,['ratio'])
    assert v5.watched['ratio'][-1]==3 and v6.watched['ratio'][-1]==3.5
    # v5 evaluates both sides of `and`, so the function runs and orders; v6 stops at the false left side.
    assert v5.stats['long_entries']>0 and v6.stats['long_entries']==0

def test_language_features_compute_what_they_say(f):
    src=HEAD+'''pick(x, n, kind = "ema") =>
    switch kind
        "sma" => ta.sma(x, n)
        => ta.ema(x, n)
pair(x) =>
    a = pick(x, 5, "sma")
    [a, pick(x, 5)]
[simple, smooth] = pair(close)
var int streak = 0
streak := close > close[1] ? streak + 1 : 0
float top = na
for i = 0 to 9
    if na(top) or close[i] > top
        top := close[i]
body = 0.0
k = 0
while k < 3
    body += math.max(close[k] - open[k], 0)
    k += 1
side = if close > simple
    1
else if close < simple
    -1
else
    0
spread = (high - low)[2]
'''+ENTRY
    w=watched(src,f,'simple','smooth','streak','top','body','side','spread'); c=f.close
    assert same(w['simple'],c.rolling(5).mean()) and same(w['smooth'],ema(c,5))
    assert same(w['top'][20:],c.rolling(10).max()[20:]) and same(w['spread'],(f.high-f.low).shift(2))
    assert same(w['body'][20:],sum((c.shift(k)-f.open.shift(k)).clip(lower=0) for k in range(3))[20:])
    up=(c>c.shift()).to_numpy(); streak=np.zeros(len(c))
    for i in range(1,len(c)): streak[i]=streak[i-1]+1 if up[i] else 0
    assert same(w['streak'],streak) and same(w['side'][10:],np.sign(c-c.rolling(5).mean())[10:])

def test_each_call_site_keeps_its_own_state_and_a_conditional_call_sees_only_its_bars(f):
    src=HEAD+'avg(x) => ta.ema(x, 10)\na = avg(close)\nb = avg(high)\nfloat c = na\nif bar_index % 2 == 0\n    c := ta.sma(close, 3)\nprev = 0.0\nif bar_index % 3 == 0\n    prev := a[1]\n'+ENTRY
    w=watched(src,f,'a','b','c','prev'); i=np.arange(len(f))
    assert same(w['a'],ema(f.close,10)) and same(w['b'],ema(f.high,10))
    # An indicator inside an `if` advances only on the bars where it runs: this is Pine's documented behaviour.
    assert same(w['c'][i%2==0],f.close[i%2==0].rolling(3).mean())
    # A script variable's history is by bar even when it is read from a block that runs rarely.
    assert same(w['prev'][i%3==0][5:],pd.Series(ema(f.close,10)).shift().to_numpy()[i%3==0][5:])

# ---- indicators against independent calculations

def test_indicators_match_independent_calculations(f):
    src=HEAD+'''a_sma = ta.sma(close, 20)
a_ema = ta.ema(close, 20)
a_rma = ta.rma(close, 14)
a_wma = ta.wma(close, 10)
a_rsi = ta.rsi(close, 14)
a_atr = ta.atr(14)
a_tr = ta.tr
a_std = ta.stdev(close, 20)
[bb_m, bb_u, bb_l] = ta.bb(close, 20, 2)
[m_l, m_s, m_h] = ta.macd(close, 12, 26, 9)
a_hi = ta.highest(high, 20)
a_lo = ta.lowest(20)
a_st = ta.stoch(close, high, low, 14)
a_cci = ta.cci(hlc3, 20)
a_roc = ta.roc(close, 5)
a_chg = ta.change(close, 3)
a_vwap = ta.vwap
a_vwma = ta.vwma(close, 20)
a_obv = ta.obv
a_sum = math.sum(close, 5)
a_up = ta.crossover(close, a_sma) ? 1 : 0
a_since = ta.barssince(ta.crossover(close, a_sma))
a_when = ta.valuewhen(ta.crossover(close, a_sma), close, 0)
[kc_m, kc_u, kc_l] = ta.kc(close, 20, 1.5)
a_wpr = ta.wpr(14)
'''+ENTRY
    names='a_sma a_ema a_rma a_wma a_rsi a_atr a_tr a_std bb_m bb_u bb_l m_l m_s m_h a_hi a_lo a_st a_cci a_roc a_chg a_vwap a_vwma a_obv a_sum a_up a_since a_when kc_m kc_u kc_l a_wpr'.split()
    w=watched(src,f,*names); c,hi,lo,v=f.close,f.high,f.low,f.volume; d=c.diff(); prev=c.shift()
    tr=pd.concat([hi-lo,(hi-prev).abs(),(lo-prev).abs()],axis=1).max(axis=1); first_na=tr.where(prev.notna())
    up=rma(d.clip(lower=0),14); down=rma((-d).clip(lower=0),14); sd=c.rolling(20).std(ddof=0); mean=c.rolling(20).mean(); tp=(hi+lo+c)/3
    mad=lambda s,n: s.rolling(n).apply(lambda x: np.abs(x-x.mean()).mean(),raw=True); line=ema(c,12)-ema(c,26); signal=ema(line,9)
    cross=((c>mean)&(prev<=mean.shift())).to_numpy(); since=np.full(len(c),np.nan); when=np.full(len(c),np.nan); n=None; last=np.nan
    for i in range(len(c)):
        if cross[i]: n=0; last=c.iloc[i]
        elif n is not None: n+=1
        since[i]=np.nan if n is None else n; when[i]=last
    kr=ema(first_na,20); hh=hi.rolling(14).max(); ll=lo.rolling(14).min()
    expected={'a_sma':mean,'a_ema':ema(c,20),'a_rma':rma(c,14),'a_wma':c.rolling(10).apply(lambda x: np.dot(x,np.arange(1,11))/55,raw=True),
      'a_rsi':np.where(down==0,100,np.where(up==0,0,100-100/(1+up/down)))+0*up,'a_atr':rma(tr,14),'a_tr':first_na,'a_std':sd,'bb_m':mean,
      'bb_u':mean+2*sd,'bb_l':mean-2*sd,'m_l':line,'m_s':signal,'m_h':line-signal,'a_hi':hi.rolling(20).max(),'a_lo':lo.rolling(20).min(),
      'a_st':100*(c-ll)/(hh-ll),'a_cci':(tp-tp.rolling(20).mean())/(.015*mad(tp,20)),'a_roc':100*(c-c.shift(5))/c.shift(5),'a_chg':c-c.shift(3),
      'a_vwap':f.vwap,'a_vwma':(c*v).rolling(20).mean()/v.rolling(20).mean(),'a_obv':(np.sign(d).fillna(0)*v).cumsum(),'a_sum':c.rolling(5).sum(),
      'a_up':cross.astype(float),'a_since':since,'a_when':when,'kc_m':ema(c,20),'kc_u':ema(c,20)+1.5*kr,'kc_l':ema(c,20)-1.5*kr,'a_wpr':100*(c-hh)/(hh-ll)}
    wrong=[k for k in names if not same(w[k],expected[k])]
    assert not wrong,wrong

def test_less_common_indicators_match_independent_calculations(f):
    src=HEAD+'''b_hma = ta.hma(close, 16)
b_lin = ta.linreg(close, 20, 0)
b_dev = ta.dev(close, 20)
b_var = ta.variance(close, 20)
b_mom = ta.mom(close, 5)
b_swma = ta.swma(close)
b_alma = ta.alma(close, 9, 0.85, 6)
b_mfi = ta.mfi(hlc3, 14)
b_cmo = ta.cmo(close, 9)
b_tsi = ta.tsi(close, 13, 25)
b_ph = ta.pivothigh(high, 3, 2)
b_pl = ta.pivotlow(3, 2)
b_hb = ta.highestbars(high, 10)
b_lb = ta.lowestbars(10)
b_rise = ta.rising(close, 3) ? 1 : 0
b_fall = ta.falling(close, 3) ? 1 : 0
b_cum = ta.cum(volume)
b_cross = ta.cross(close, ta.sma(close, 20)) ? 1 : 0
b_fix = fixnan(ta.pivothigh(high, 3, 2))
b_round = math.round(close, 1) + math.round(-2.5) + math.floor(close) + math.max(open, close, hl2) + math.abs(open - close) + math.sqrt(volume) + math.pow(close, 2) + math.log(close) + nz(close[5000], 7)
b_week = dayofweek * 100 + hour + minute / 100.0
'''+ENTRY
    names='b_hma b_lin b_dev b_var b_mom b_swma b_alma b_mfi b_cmo b_tsi b_ph b_pl b_hb b_lb b_rise b_fall b_cum b_cross b_fix b_round b_week'.split()
    w=watched(src,f,*names); c,hi,lo,v,o=f.close,f.high,f.low,f.volume,f.open; n=len(c); tp=(hi+lo+c)/3; d=c.diff()
    wma=lambda x,k: pd.Series(np.asarray(x,dtype=float)).rolling(k).apply(lambda z: np.dot(z,np.arange(1,k+1))/(k*(k+1)/2),raw=True).to_numpy()
    m=.85*8; sig=9/6; weights=np.exp(-(np.arange(9)-m)**2/(2*sig*sig))
    flow=tp*v; dtp=tp.diff(); upper=flow.where(~(dtp<=0),0).rolling(14).sum(); lower=flow.where(~(dtp>=0),0).rolling(14).sum()     # The first bar has no change and counts on both sides, as in the published formula.
    gain=d.clip(lower=0).rolling(9).sum(); loss=(-d).clip(lower=0).rolling(9).sum(); double=lambda x: ema(ema(x,25),13)
    def pivot(x,left,right,sign):
        x=x.to_numpy()*sign; out=np.full(n,np.nan)
        for i in range(left+right,n):
            p=x[i-right]
            if (x[i-right-left:i-right]<=p).all() and (x[i-right+1:i+1]<p).all(): out[i]=p*sign
        return out
    def offset(x,k,best):
        x=x.to_numpy(); out=np.full(n,np.nan)
        for i in range(k-1,n):
            window=x[i-k+1:i+1][::-1]; out[i]=-int(np.flatnonzero(window==best(window))[0])
        return out
    mean=c.rolling(20).mean(); prev=c.shift(); cross=(((c>mean)&(prev<=mean.shift()))|((c<mean)&(prev>=mean.shift()))).astype(float)
    ph=pivot(hi,3,2,1); opened=f.index-pd.Timedelta(minutes=15)
    expected={'b_hma':wma(2*wma(c,8)-wma(c,16),4),'b_lin':c.rolling(20).apply(lambda z: np.polyval(np.polyfit(np.arange(20),z,1),19),raw=True),
      'b_dev':c.rolling(20).apply(lambda z: np.abs(z-z.mean()).mean(),raw=True),'b_var':c.rolling(20).var(ddof=0),'b_mom':c-c.shift(5),
      'b_swma':(c.shift(3)+2*c.shift(2)+2*c.shift(1)+c)/6,'b_alma':c.rolling(9).apply(lambda z: np.dot(z,weights)/weights.sum(),raw=True),
      'b_mfi':100-100/(1+upper/lower),'b_cmo':100*(gain-loss)/(gain+loss),'b_tsi':double(d)/double(d.abs()),'b_ph':ph,'b_pl':pivot(lo,3,2,-1),
      'b_hb':offset(hi,10,np.max),'b_lb':offset(lo,10,np.min),'b_rise':(c>prev.rolling(3).max()).astype(float),'b_fall':(c<prev.rolling(3).min()).astype(float),'b_cum':v.cumsum(),'b_cross':cross,
      'b_fix':pd.Series(ph).ffill(),'b_round':np.floor(c*10+.5)/10-2+np.floor(c)+np.maximum(np.maximum(o,c),(hi+lo)/2)+(o-c).abs()+np.sqrt(v)+c**2+np.log(c)+7,
      'b_week':((opened.dayofweek+1)%7+1)*100+opened.hour+opened.minute/100}
    wrong=[k for k in names if not same(w[k],expected[k])]
    assert not wrong,wrong
    assert np.isfinite(w['b_ph']).sum()>50 and np.isfinite(w['b_mfi'][20:]).all() and (np.abs(w['b_tsi'][80:])<=1).all()

def test_supertrend_and_dmi_follow_their_published_definitions(f):
    w=watched(HEAD+'[st, direction] = ta.supertrend(3, 10)\n[plus, minus, adx] = ta.dmi(14, 14)\n'+ENTRY,f,'st','direction','plus','minus','adx')
    c,hi,lo=f.close.to_numpy(),f.high,f.low; prev=f.close.shift()
    tr=pd.concat([hi-lo,(hi-prev).abs(),(lo-prev).abs()],axis=1).max(axis=1); atr=rma(tr,10); mid=((hi+lo)/2).to_numpy()
    n=len(c); upper=mid+3*atr; lower=mid-3*atr; fu=np.zeros(n); fl=np.zeros(n); st=np.full(n,np.nan); way=np.full(n,np.nan)
    for i in range(n):
        pl=0 if i==0 or np.isnan(fl[i-1]) else fl[i-1]; pu=0 if i==0 or np.isnan(fu[i-1]) else fu[i-1]; pc=c[i-1] if i else np.nan
        fl[i]=lower[i] if (lower[i]>pl or pc<pl) else pl; fu[i]=upper[i] if (upper[i]<pu or pc>pu) else pu
        way[i]=1 if i==0 or np.isnan(atr[i-1]) else (-1 if c[i]>fu[i] else 1) if st[i-1]==pu else (1 if c[i]<fl[i] else -1)
        st[i]=fl[i] if way[i]==-1 else fu[i]
    assert same(w['st'],st) and same(w['direction'],way) and set(way[50:])=={1,-1}
    um=hi.diff(); dm=-lo.diff(); pdm=np.where(um.isna(),np.nan,np.where((um>dm)&(um>0),um,0)); mdm=np.where(dm.isna(),np.nan,np.where((dm>um)&(dm>0),dm,0))
    smooth=rma(tr.where(prev.notna()),14); plus=pd.Series(100*rma(pdm,14)/smooth).ffill().to_numpy(); minus=pd.Series(100*rma(mdm,14)/smooth).ffill().to_numpy()
    total=plus+minus
    assert same(w['plus'],plus) and same(w['minus'],minus) and same(w['adx'],100*rma(np.abs(plus-minus)/np.where(total==0,1,total),14))

def test_parabolic_sar_stays_on_the_far_side_of_price_and_flips(f):
    sar=watched(HEAD+'s = ta.sar(0.02, 0.02, 0.2)\n'+ENTRY,f,'s')['s'][2:]; hi=f.high.to_numpy()[2:]; lo=f.low.to_numpy()[2:]
    below=sar<=lo; above=sar>=hi
    # Never inside the candle it belongs to, and it changes side now and then rather than every bar.
    assert (below|above).all() and 20<int(np.abs(np.diff(below.astype(int))).sum())<len(sar)/4

# ---- higher timeframes

def test_request_security_returns_only_completed_higher_timeframe_candles(raw,f):
    src=HEAD+'''hour = request.security(syminfo.tickerid, "60", close)
slow = request.security(syminfo.tickerid, "60", ta.ema(close, 20), gaps = barmerge.gaps_off, lookahead = barmerge.lookahead_off)
[top, bottom] = request.security(syminfo.tickerid, "240", [high, low])
here = request.security(syminfo.tickerid, timeframe.period, close)
before = request.security(syminfo.tickerid, "60", close[1], lookahead = barmerge.lookahead_on)
'''+ENTRY
    w=watched(src,f,'hour','slow','top','bottom','here','before')
    def completed(minutes,pick,at):
        h=aggregate(raw,minutes); h.index=h.index+pd.Timedelta(minutes=minutes); return pick(h).reindex(at,method='ffill')
    assert same(w['hour'],completed(60,lambda h:h.close,f.index)) and same(w['here'],f.close)
    assert same(w['slow'],completed(60,lambda h:pd.Series(ema(h.close,20),index=h.index),f.index))
    assert same(w['top'],completed(240,lambda h:h.high,f.index)) and same(w['bottom'],completed(240,lambda h:h.low,f.index))
    # The offset-with-lookahead idiom: the last candle that had closed when this chart candle opened.
    assert same(w['before'],completed(60,lambda h:h.close,f.index-pd.Timedelta(minutes=15)))
    # 00:15, 00:30 and 00:45 are inside the first hour: nothing has closed yet.
    assert np.isnan(w['hour'][:3]).all() and not np.isnan(w['hour'][3])

@pytest.mark.parametrize('call,why',[('request.security("BINANCE:BTCUSDT", "60", close)','another symbol'),
    ('request.security(syminfo.tickerid, "60", close, lookahead = barmerge.lookahead_on)','before it has closed'),
    ('request.security(syminfo.tickerid, "W", close)','not supported'),('request.security(syminfo.tickerid, "5", close)','whole multiple'),
    ('request.security(syminfo.tickerid, "100", close)','divides a day'),('request.security(syminfo.tickerid, "60", close, gaps = barmerge.gaps_on)','gaps_on'),
    ('request.financial(syminfo.tickerid, "EPS", "FQ")','not supported yet')])
def test_requests_that_cannot_be_answered_honestly_are_refused(call,why):
    r=pine.check(HEAD+f'x = {call}\n'+ENTRY,dry_run=False)
    assert not r['ok'] and why in r['refused'][0]['text'] and r['refused'][0]['line']==3

# ---- orders

def test_a_reversal_script_becomes_entries_with_the_other_side_closed(f):
    m=run(CROSS,f,stop_atr=10); p=m.plan(f.index); longs=p[p.side==1]; shorts=p[p.side==-1]
    # With a stop too far to be reached, every entry after the first flips the position: it closes the other side on the same open.
    assert len(longs)>5 and len(shorts)>5 and (longs.exit_short.sum()+shorts.exit_long.sum())==len(longs)+len(shorts)-1
    assert not longs.exit_long.any() and not shorts.exit_short.any() and m.stats['signal_exits']==len(longs)+len(shorts)-1
    m=run(CROSS,f); p=m.plan(f.index); longs=p[p.side==1]; shorts=p[p.side==-1]
    # The script has no stop, so each entry carries the Settings ATR stop, measured from the signal close. No target, no time limit, no Settings trend gate.
    assert np.allclose(longs.stop_price,(f.close-1.5*f.atr)[longs.index]) and np.allclose(shorts.stop_price,(f.close+1.5*f.atr)[shorts.index])
    assert p[p.side!=0][['no_target','no_time_exit','unfiltered']].all().all() and p.target_price.isna().all()
    assert m.stats['settings_stops']==len(longs)+len(shorts)
    wider=run(CROSS,f,stop_atr=3).plan(f.index); assert np.allclose(wider.stop_price[wider.side==1],(f.close-3*f.atr)[wider.index[wider.side==1]])

def test_bracket_prices_travel_with_the_entry_and_later_changes_become_amendments(f):
    p=run(BRACKET,f).plan(f.index); entries=p[p.side==1]; atr=rma(pd.concat([f.high-f.low,(f.high-f.close.shift()).abs(),(f.low-f.close.shift()).abs()],axis=1).max(axis=1),14)
    at=f.index.get_indexer(entries.index)
    assert len(entries)>5 and np.allclose(entries.stop_price,f.close.to_numpy()[at]-2*atr[at]) and np.allclose(entries.target_price,f.close.to_numpy()[at]+3*atr[at])
    assert not entries.no_target.any() and p.amend_stop.isna().all()
    trailing=HEAD+'var float trail = na\nif strategy.position_size == 0 and close > ta.sma(close, 20)\n    trail := low - ta.atr(14)\n    strategy.entry("L", strategy.long)\nif strategy.position_size > 0\n    trail := math.max(trail, low - ta.atr(14))\nstrategy.exit("X", "L", stop = trail)\n'
    m=run(trailing,f); p=m.plan(f.index); moved=p.amend_stop.dropna()
    assert m.stats['moved_levels']==len(moved)>10 and p.stop_price.notna().sum()==m.stats['long_entries'] and m.stats['settings_stops']==0
    # A trailing stop for a long only ever rises while the position is open.
    position=(p.side==1).cumsum(); assert all(g.dropna().is_monotonic_increasing for _,g in p.amend_stop.groupby(position))

def test_close_and_position_reads_follow_the_scripts_own_fills(f):
    src=HEAD+'size = strategy.position_size\nprice = strategy.position_avg_price\nif bar_index % 20 == 5\n    strategy.entry("L", strategy.long)\nif bar_index % 20 == 9\n    strategy.close("L")\nif bar_index % 20 == 12\n    strategy.close("other")\n'
    m=run(src,f,['size','price'],stop_atr=10); p=m.plan(f.index); i=np.arange(len(f)); live=np.array(m.active)
    size=np.array(m.watched['size']); price=np.array(m.watched['price']); o=f.open.to_numpy()
    # The close leaves the position it names; a close for an id that is not open does nothing.
    assert (p.side.to_numpy()[live&(i%20==5)]==1).all() and p.exit_long.to_numpy()[(size==1)&(i%20==9)].all() and not p.exit_long.to_numpy()[i%20==12].any()
    held=np.flatnonzero(size==1); flat=np.flatnonzero(size==0)
    # Filled at the open after the signal: in the position from bar 6 of each cycle through bar 9.
    assert set(held%20)<={6,7,8,9} and len(held)>100 and np.isnan(price[flat]).all()
    entry=held[held%20==6]; assert np.allclose(price[entry],o[entry])
    assert m.trades and all(side==1 and a%20==6 for side,a,b in m.trades)

def test_direction_setting_turns_an_opposite_entry_into_a_close(f):
    both=run(CROSS,f).plan(f.index); longs=run(CROSS,f,direction='long').plan(f.index)
    assert (longs.side>=0).all() and (longs.side==1).sum()>5
    # Where the script would flip short, a long-only run still leaves the long.
    assert longs.exit_long.sum()>5 and not longs.exit_short.any()

def test_no_order_is_placed_before_the_test_starts(f):
    early=run(CROSS,f).plan(f.index); late=run(CROSS,f,start='2025-01-10').plan(f.index); start=pd.Timestamp('2025-01-10',tz='UTC')
    assert (early.side!=0)[early.index<start].sum()>10 and not (late.side!=0)[late.index<start].any() and (late.side!=0).sum()>5
    # Indicators still warm up on the earlier candles: the first signal after the start does not wait for them.
    assert (late.side!=0)[late.index<start+pd.Timedelta(days=2)].any()

def test_a_level_for_the_side_being_left_is_not_attached_to_the_new_entry(f):
    src=CROSS+'if strategy.position_size > 0\n    strategy.exit("XL", stop = low * 0.99)\nif strategy.position_size < 0\n    strategy.exit("XS", stop = high * 1.01)\n'
    p=run(src,f).plan(f.index); longs=p[p.side==1]; shorts=p[p.side==-1]; c=f.close
    assert (longs.stop_price<c[longs.index]).all() and (shorts.stop_price>c[shorts.index]).all()

def test_the_settings_trend_gate_does_not_remove_a_scripts_entries(raw,monkeypatch):
    monkeypatch.setitem(strategies.REGISTRY,'pine_gate',pine.strategy_function(pine.Program(CROSS)))
    off,_=prepare(raw,config(strategy='pine_gate')); gated,frame=prepare(raw,config(strategy='pine_gate',htf_filter='both'))
    entries=lambda signals: {t:s['side'] for t,s in signals.items() if s['side']}
    assert len(entries(off))>20 and entries(gated)==entries(off) and not (frame.htf_long&frame.htf_short).all()
    # A Python plan without the opt-out is still gated: the flag is the only way past the filter.
    plain=lambda f,c: pd.DataFrame({'side':pine.strategy_function(pine.Program(CROSS))(f,c).side},index=f.index)
    monkeypatch.setitem(strategies.REGISTRY,'plain_gate',plain)
    assert len(entries(prepare(raw,config(strategy='plain_gate',htf_filter='both'))[0]))<len(entries(off))

def test_settings_a_script_cannot_be_combined_with_are_refused(f):
    for kw in ({'entry_order':'limit'},{'trailing_atr':1},{'breakeven_r':1}):
        with pytest.raises(ValueError,match='manages its own'): pine.strategy_function(pine.Program(CROSS))(f,config(**kw))

# ---- through the exact engine

def engine(source,raw,monkeypatch,**kw):
    key='pine_engine_test'; program=pine.Program(source); monkeypatch.setitem(strategies.REGISTRY,key,pine.strategy_function(program))
    c=config(strategy=key,pairs=['JTOUSDT'],sizing='fixed_notional',fixed_notional=500,participation_pct=20,funding_mode='off',**kw)
    bundle=(raw,{},{'tick_size':.0001,'qty_step':.001,'min_qty':.001,'min_notional':1},[],{'rows':len(raw),'pair':'test'},raw)
    return simulate(c,{'JTOUSDT':bundle}),program.execute(features(raw,c),c),c

@pytest.mark.parametrize('source,kw',[(CROSS,{}),(BRACKET,{}),(BRACKET,{'target_order':'limit','timeframe':5}),(CROSS,{'direction':'short','timeframe':30})])
def test_the_engine_takes_the_trades_the_script_expects(raw,monkeypatch,source,kw):
    r,m,c=engine(source,raw,monkeypatch,**kw); start=int(pd.Timestamp(c.start,tz='UTC').timestamp()*1000)
    expected={(side,m.TC[a-1]):(m.T[b],m.TC[b]) for side,a,b in m.trades if m.TC[a-1]>=start}
    trades=[t for t in r['trades'] if t['reason']!='end_of_test']
    assert len(trades)>10 and not r['diagnostics']['rejected'] and len(trades)==len(expected)
    for t in trades:
        # Same entry candle, and the engine's exit falls inside the candle where the script's own model closed.
        lo,hi=expected[(1 if t['side']=='long' else -1,t['entry_time'])]; assert lo<=t['exit_time']<=hi
        assert t['net_pnl']==pytest.approx(t['raw_pnl']-t['fees']-t['slippage']-t['spread']-t['funding']-t['liquidation_fee'])
    assert any('sets its own exits' in x for x in r['warnings']) and not any(t['reason']=='time_exit' for t in r['trades'])

def test_a_scripts_result_is_pinned(raw,monkeypatch):
    r,_,_=engine(BRACKET,raw,monkeypatch); m=r['metrics']
    assert (m['trade_count'],round(m['net_pnl'],4))==(GOLDEN['trades'],GOLDEN['net'])

GOLDEN={'trades':42,'net':2.263}

def test_converted_scripts_pass_the_look_ahead_check(monkeypatch):
    for name,source in (('pine_look_a',CROSS),('pine_look_b',BRACKET)):
        monkeypatch.setitem(strategies.REGISTRY,name,pine.strategy_function(pine.Program(source)))
        out=prefix_invariant(name); assert out['ok'] and out['conclusive'] and out['signals_checked']>100

# ---- the report

@pytest.mark.parametrize('source,why',[('//@version=4\nstrategy("x")\n'+ENTRY,'Convert code'),('strategy("x")\n'+ENTRY,'no //@version'),
    ('//@version=5\nindicator("x")\nplot(close)\n','indicator or library'),(HEAD+'a = array.new<float>(0)\n'+ENTRY,'Arrays'),
    (HEAD+'if close > open\n    strategy.entry("L", strategy.long, limit = close * 0.99)\n','resting order'),
    (HEAD+ENTRY+'strategy.exit("x", "L", trail_points = 100, trail_offset = 50)\n','trailing stop'),
    (HEAD+ENTRY+'strategy.exit("x", "L", qty_percent = 50, limit = close * 1.1)\n','part of a position'),
    (HEAD+ENTRY+'strategy.exit("x", "L", loss = 100)\n','ticks'),(HEAD+'varip int n = 0\n'+ENTRY,'varip'),
    ('//@version=5\nstrategy("x", pyramiding = 3)\n'+ENTRY,'pyramiding'),('//@version=5\nstrategy("x", calc_on_order_fills = true)\n'+ENTRY,'calc_on_order_fills'),
    (HEAD+'if strategy.equity > 1000\n    strategy.entry("L", strategy.long)\n','reads the account'),(HEAD+'x = ta.frobnicate(close)\n'+ENTRY,'not a function'),
    (HEAD+'if clsoe > open\n    strategy.entry("L", strategy.long)\n','not a variable'),(HEAD+'x = (close +\n'+ENTRY,'could not be read'),
    (HEAD+'plot(close)\n','never calls strategy.entry'),(HEAD+ENTRY+'strategy.exit("x", "L", stop = low - syminfo.mintick)\n','mintick'),
    (HEAD+'strategy.order("a", strategy.long)\n'+ENTRY,'resting orders'),(HEAD+'type Bar\n    float o\n'+ENTRY,'User-defined types'),
    (HEAD+'p = line.get_price(line.new(bar_index, low, bar_index, high), bar_index)\n'+ENTRY,'reads a value back'),
    (HEAD+'ok = not na(time(timeframe.period, "0930-1600"))\n'+ENTRY,'session'),(HEAD+'if timenow > 0\n    strategy.entry("L", strategy.long)\n','wall clock'),
    (HEAD+'x = last_bar_index\n'+ENTRY,'from the future'),('','no //@version'),('not pine £','unexpected character')])
def test_what_cannot_be_honoured_is_refused_with_a_reason(source,why):
    assert why in refused(source)

@pytest.mark.parametrize('source,why',[(HEAD+'if close[-1] > close\n    strategy.entry("L", strategy.long)\n','from the future'),
    (HEAD+'x = "a" + 1\n'+ENTRY,'could not be evaluated'),(HEAD+'i = 0\nwhile true\n    i += 1\n'+ENTRY,'loops ran more than')])
def test_faults_that_only_show_when_the_script_runs_are_refused_with_their_line(source,why):
    r=pine.check(source); assert not r['ok'] and why in r['refused'][0]['text'] and r['refused'][0]['line']>=3

def test_the_report_says_what_is_exact_what_differs_and_what_is_skipped():
    src='//@version=5\nstrategy("Report", initial_capital = 500, process_orders_on_close = true)\nn = input.int(14, "Length")\nplot(close)\n'+BRACKET.split('\n',2)[2]+'if barstate.islast\n    label.new(bar_index, high, "end")\n'
    r=pine.check(src); texts=lambda key: ' | '.join(x['text'] for x in r[key])
    assert r['ok'] and r['title']=='Report' and r['version']==5 and r['look_ahead']=={'ok':True,'conclusive':True,'signals_checked':r['look_ahead']['signals_checked']}
    assert r['inputs']==[{'line':3,'kind':'int','title':'Length','default':14}] and r['entries']==[{'id':'L','direction':'long'}] and len(r['exits'])==2
    assert {'ta.atr','ta.crossover','ta.sma'}<=set(r['indicators']) and r['dry_run']['long_entries']>0 and r['dry_run']['bars']==2000
    assert all(x in texts('approximated') for x in ('reads its own position','process_orders_on_close','always false','next open'))
    assert 'replaced by Settings' in texts('ignored') and 'Plots, drawings' in texts('ignored') and 4 in r['ignored'][1]['lines']
    json.dumps(r,allow_nan=False)

# ---- saved scripts

@pytest.fixture
def clean():
    before=set(strategies.REGISTRY); yield
    for key in set(strategies.REGISTRY)-before: strategies.REGISTRY.pop(key); STRATEGIES.pop(key,None)

def test_adding_saves_registers_and_keys_by_logic(tmp_path,clean):
    saved=pine.add(CROSS.replace('"Test"','"MA cross 5/13"'),tmp_path)
    assert saved['key'].startswith('pine_ma_cross_5_13_') and len(saved['key'])<=40 and saved['key'] in strategies.REGISTRY and STRATEGIES[saved['key']]['name']=='MA cross 5/13'
    assert (tmp_path/f'{saved["key"]}.pine').exists() and not saved['existing'] and saved['report']['ok']
    again=pine.add('// a note\n'+CROSS.replace('"Test"','"MA cross 5/13"'),tmp_path); assert again['key']==saved['key'] and again['existing']
    edited=pine.add(CROSS.replace('"Test"','"MA cross 5/13"').replace('13)','21)'),tmp_path,parent=saved['key'])
    assert edited['key']!=saved['key'] and edited['parent']==saved['key'] and len(pine.listing(tmp_path))==2
    assert pine.read(tmp_path,saved['key'])['source'].startswith('//@version=5')
    with pytest.raises(ValueError,match='cannot be tested yet'): pine.add('//@version=5\nindicator("x")\n',tmp_path)
    with pytest.raises(ValueError): pine.read(tmp_path,'../secrets')

def test_saved_scripts_load_at_start_and_a_changed_file_is_not_trusted(tmp_path,clean):
    a=pine.add(CROSS,tmp_path,'First'); b=pine.add(BRACKET,tmp_path,'Second')
    for key in (a['key'],b['key']): strategies.REGISTRY.pop(key); STRATEGIES.pop(key)
    (tmp_path/f'{b["key"]}.pine').write_text(BRACKET.replace('2 * atr','5 * atr'),encoding='utf-8')
    assert pine.load_saved(tmp_path)=={'loaded':[a['key']],'skipped':[b['key']]} and a['key'] in strategies.REGISTRY and b['key'] not in strategies.REGISTRY

def test_api_adds_a_script_to_the_library_with_its_lineage(tmp_path,monkeypatch,clean):
    from backend import server
    monkeypatch.setattr(server,'DB',tmp_path/'lab.sqlite3'); monkeypatch.setattr(server,'PINE',tmp_path/'pine')
    assert server.pine_check(server.PineScript(source=CROSS))['ok']
    first=server.pine_add(server.PineScript(source=CROSS,name='Cross',hypothesis='Trends persist.'))
    second=server.pine_add(server.PineScript(source=CROSS.replace('13)','21)'),name='Cross slow',parent=first['key']))
    with registry.session(server.DB) as db:
        rows={r['key']:r for r in registry.library(db)}
        assert rows[first['key']]['origin']=='pine' and rows[first['key']]['hypothesis']=='Trends persist.' and rows[second['key']]['parent']==first['key']
        assert registry.family(db,second['key'])==[first['key'],second['key']]
    assert server.meta()['strategies'][first['key']]['name']=='Cross' and [x['key'] for x in server.pine_list()]==[second['key'],first['key']]
    assert server.pine_read(first['key'])['source']==CROSS
    for bad in (server.PineScript(source='//@version=5\nindicator("x")\n'),server.PineScript(source=CROSS.replace('13)','34)'),parent='missing')):
        with pytest.raises(server.HTTPException): server.pine_add(bad)
    with pytest.raises(server.HTTPException): server.pine_read('pine_missing')
    assert len(server.pine_list())==2

def test_command_line_checks_adds_and_lists(tmp_path,monkeypatch,capsys,clean):
    monkeypatch.setattr(lab,'WORKSPACE',tmp_path); path=tmp_path/'cross.pine'; path.write_text(CROSS,encoding='utf-8')
    lab.main(['pine','check',str(path)]); out=capsys.readouterr().out
    assert out.startswith('Ready to test · Pine v5') and 'look-ahead: none found' in out and 'entries: Long (long), Short (short)' in out
    lab.main(['pine','add',str(path),'--name','Cross','--hypothesis','Trends persist.','--json']); key=json.loads(capsys.readouterr().out)['key']
    lab.main(['pine','list','--json']); assert [x['key'] for x in json.loads(capsys.readouterr().out)]==[key]
    lab.main(['pine','show',key]); assert capsys.readouterr().out==CROSS
    lab.main(['strategy','show',key,'--json']); assert json.loads(capsys.readouterr().out)['origin']=='pine'
    bad=tmp_path/'bad.pine'; bad.write_text('//@version=4\nstrategy("x")\n',encoding='utf-8')
    with pytest.raises(SystemExit): lab.main(['pine','check',str(bad)])
    assert 'Cannot be tested yet' in capsys.readouterr().out
    with pytest.raises(SystemExit,match='cannot be tested yet'): lab.main(['pine','add',str(bad)])
