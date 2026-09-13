"""Free public data, checksum-verified archive cache, strict OHLCV validation."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib, io, json, time, zipfile, gzip, urllib.request, urllib.error
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT/'data'
COLS = ['timestamp','open','high','low','close','volume']

def utc(value):
    t=pd.Timestamp(value)
    return t.tz_localize('UTC') if t.tzinfo is None else t.tz_convert('UTC')

def normalize(frame):
    f=frame.copy()
    f.columns=[str(x).strip().lower() for x in f.columns]
    f=f.rename(columns={'open_time':'timestamp','date':'timestamp','datetime':'timestamp','time':'timestamp'})
    if not set(COLS).issubset(f): raise ValueError('OHLCV requires timestamp, open, high, low, close, volume.')
    s=f['timestamp']
    numeric=pd.to_numeric(s,errors='coerce')
    if pd.api.types.is_datetime64_any_dtype(s):
        ts=pd.to_datetime(s,utc=True)
    elif numeric.notna().all():
        median=float(numeric.median())
        unit='ns' if median>1e17 else 'us' if median>1e14 else 'ms' if median>1e11 else 's'
        ts=pd.to_datetime(numeric,unit=unit,utc=True)
    else: ts=pd.to_datetime(s,utc=True,errors='raise',format='mixed')
    f=f[COLS[1:]].apply(pd.to_numeric,errors='raise'); f.index=pd.DatetimeIndex(ts)
    f.index.name='timestamp'
    duplicates=int(f.index.duplicated().sum())
    if duplicates:
        for _,g in f[f.index.duplicated(keep=False)].groupby(level=0):
            if not g.eq(g.iloc[0]).all().all(): raise ValueError('Conflicting duplicate candles found.')
        f=f[~f.index.duplicated(keep='first')]
    f=f.sort_index()
    if len(f)<2: raise ValueError('At least two candles are required.')
    if not np.isfinite(f.to_numpy()).all(): raise ValueError('Non-finite candle values.')
    if (f[['open','high','low','close']]<=0).any().any() or (f.volume<0).any(): raise ValueError('Invalid price or volume.')
    if ((f.high<f[['open','close','low']].max(axis=1)) | (f.low>f[['open','close','high']].min(axis=1))).any(): raise ValueError('Invalid OHLC candle range.')
    if (f.index.asi8 % (60*10**9)).any(): raise ValueError('Candles must use minute-aligned OPEN timestamps in UTC.')
    f.attrs['duplicates_removed']=duplicates
    return f

def aggregate(f,minutes):
    grouped=f.resample(f'{minutes}min',label='left',closed='left',origin='epoch')
    out=grouped.agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'})
    # Source frames are canonical 1m candles. Never fill a missing market candle.
    return out[grouped['close'].count()==minutes].dropna()

def fetch_bytes(url):
    error=None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'FibSteinLab/1.0'}),timeout=25) as r: return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (400,401,403,404,451): raise ValueError(f'Public data unavailable ({e.code}): {url}') from e
            error=e
        except (OSError,TimeoutError) as e: error=e
        time.sleep(.4*(attempt+1))
    raise ValueError(f'Data request failed: {url}: {error}')

def archive(pair,kind,period,stamp):
    folder=DATA/'archive'/kind/pair; folder.mkdir(parents=True,exist_ok=True)
    interval='' if kind=='fundingRate' else '/1m'
    filename=f'{pair}-{kind}-{stamp}' if kind=='fundingRate' else f'{pair}-1m-{stamp}'
    path=folder/f'{filename}.zip'
    url=f'https://data.binance.vision/data/futures/um/{period}/{kind}/{pair}{interval}/{filename}.zip'
    checksum_path=path.with_suffix('.sha256')
    if path.exists() and checksum_path.exists():
        content=path.read_bytes()
        if hashlib.sha256(content).hexdigest()==checksum_path.read_text().strip(): return content
    content=fetch_bytes(url)
    expected=fetch_bytes(url+'.CHECKSUM').decode().split()[0]
    if hashlib.sha256(content).hexdigest()!=expected: raise ValueError('Archive checksum mismatch.')
    temporary=path.with_suffix('.part'); temporary.write_bytes(content); temporary.replace(path)
    checksum_path.write_text(expected)
    return content

def unzip_csv(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names=[n for n in z.namelist() if n.endswith('.csv')]
        if len(names)!=1: raise ValueError('Unexpected archive contents.')
        return z.read(names[0])

def read_archive_candles(raw):
    raw=unzip_csv(raw)
    first=raw.splitlines()[0].decode().split(',')[0]
    f=pd.read_csv(io.BytesIO(raw),header=0 if not first.isdigit() else None)
    f=f.iloc[:,:6]; f.columns=COLS
    return normalize(f)

def read_archive_funding(raw):
    f=pd.read_csv(io.BytesIO(unzip_csv(raw)))
    required={'calc_time','last_funding_rate'}
    if not required.issubset(f): raise ValueError('Unexpected funding archive schema.')
    return pd.DataFrame({'timestamp':pd.to_datetime(f.calc_time,unit='ms',utc=True),'rate':f.last_funding_rate.astype(float),
                        'interval_hours':f.get('funding_interval_hours',8)})

def month_cache(pair,source,month): return DATA/source/pair/f'{month}.csv.gz'

def save_candles(frame,path):
    """Compress completely before committing a cache file."""
    temporary=path.with_suffix('.part')
    temporary.write_bytes(gzip.compress(frame.to_csv().encode(),compresslevel=6,mtime=0))
    temporary.replace(path)

def download_archive(pair,start,end,progress=lambda x:None,cancel=lambda:False):
    months=pd.period_range(utc(start).tz_localize(None), (utc(end)-pd.Timedelta(seconds=1)).tz_localize(None),freq='M')
    current=utc(datetime.now(timezone.utc)).tz_localize(None).to_period('M')
    for month in months:
        if cancel(): raise ValueError('Cancelled.')
        stamp=str(month); path=month_cache(pair,'binance_archive',stamp)
        funding_path=path.with_name(stamp+'.funding.csv')
        try:
            cached=normalize(pd.read_csv(path)) if path.exists() else None
        except (EOFError, gzip.BadGzipFile):
            # An interrupted older cache write can be rebuilt from verified archives.
            cached=None
        needed=pd.date_range(max(utc(start),utc(month.start_time)),min(utc(end),utc((month+1).start_time)),freq='1min',inclusive='left')
        if cached is None or len(needed.difference(cached.index)):
            progress(f'{pair} · downloading {stamp} candles')
            if month<current:
                f=read_archive_candles(archive(pair,'klines','monthly',stamp))
            else:
                days=pd.date_range(max(utc(start),utc(month.start_time)),min(utc(end),utc(datetime.now(timezone.utc)).normalize()),freq='D',inclusive='left')
                f=pd.concat([read_archive_candles(archive(pair,'klines','daily',d.strftime('%Y-%m-%d'))) for d in days])
            path.parent.mkdir(parents=True,exist_ok=True)
            if cached is not None: f=normalize(pd.concat([cached.reset_index(),f.reset_index()]))
            save_candles(f,path)
        if not funding_path.exists() and month<current:
            progress(f'{pair} · downloading {stamp} funding')
            try:
                funding=read_archive_funding(archive(pair,'fundingRate','monthly',stamp)); funding.to_csv(funding_path,index=False)
            except ValueError as e: progress(f'Funding unavailable for {pair} {stamp}: {e}')
        meta={'source':'Binance USD-M public archive','pair':pair,'month':stamp,'kind':'linear USDT perpetual',
              'retrieved_at':datetime.now(timezone.utc).isoformat(),'candle_open_timestamps':True,'rules':'User assumptions; historical exchange filters not provided','funding_complete_month':funding_path.exists()}
        path.with_name(stamp+'.meta.json').write_text(json.dumps(meta,indent=2))

def download_ccxt(pair,source,start,end,progress=lambda x:None,cancel=lambda:False):
    import ccxt
    ex=getattr(ccxt,source)({'enableRateLimit':True,'timeout':20000,'options':{'defaultType':'swap'}})
    markets=ex.load_markets(); symbol=pair[:-4]+'/USDT:USDT'
    market=markets.get(symbol)
    if not market or not market.get('swap') or not market.get('linear'): raise ValueError(f'{symbol} is not an available linear perpetual on {source}.')
    contract=float(market.get('contractSize') or 1)
    for month in pd.period_range(utc(start).tz_localize(None),(utc(end)-pd.Timedelta(seconds=1)).tz_localize(None),freq='M'):
        if cancel(): raise ValueError('Cancelled.')
        path=month_cache(pair,source,str(month)); path.parent.mkdir(parents=True,exist_ok=True)
        a=max(utc(start),utc(month.start_time)); b=min(utc(end),utc((month+1).start_time))
        cursor=int(a.timestamp()*1000); stop=int(b.timestamp()*1000); rows=[]
        while cursor<stop:
            if cancel(): raise ValueError('Cancelled.')
            progress(f'{pair} · {source} · {pd.to_datetime(cursor,unit="ms",utc=True)}')
            part=ex.fetch_ohlcv(symbol,'1m',since=cursor,limit=1000)
            part=[r for r in part if cursor<=r[0]<stop]
            if not part: break
            rows.extend(part); cursor=max(r[0] for r in part)+60000
        if not rows: raise ValueError(f'No history returned for {pair}; use an archive or CSV.')
        # These CCXT parsers already return base-asset volume for linear swaps.
        # Multiplying OKX base volume by contractSize again would corrupt capacity.
        f=pd.DataFrame(rows,columns=COLS)
        f=normalize(f)
        if path.exists(): f=normalize(pd.concat([pd.read_csv(path),f.reset_index()]))
        f.to_csv(path,compression='gzip')
        funding=[]; cursor=int(a.timestamp()*1000)
        if ex.has.get('fetchFundingRateHistory'):
            while cursor<stop:
                if cancel(): raise ValueError('Cancelled.')
                part=ex.fetch_funding_rate_history(symbol,since=cursor,limit=100)
                part=[r for r in part if cursor<=r['timestamp']<stop]
                if not part: break
                funding.extend({'timestamp':pd.to_datetime(r['timestamp'],unit='ms',utc=True),'rate':r['fundingRate']} for r in part)
                cursor=max(r['timestamp'] for r in part)+1
        if funding:
            fp=path.with_name(str(month)+'.funding.csv'); rates=pd.DataFrame(funding)
            if fp.exists(): rates=pd.concat([pd.read_csv(fp),rates])
            rates['timestamp']=pd.to_datetime(rates.timestamp,utc=True); rates.drop_duplicates('timestamp').sort_values('timestamp').to_csv(fp,index=False)
        path.with_name(str(month)+'.meta.json').write_text(json.dumps({'source':source,'kind':'linear USDT perpetual','pair':pair,'contract_size':contract,'retrieved_at':datetime.now(timezone.utc).isoformat(),'funding_complete_month':False,'rules':'Current exchange metadata is not historical risk tiers.'}))

def inventory():
    output=[]
    for source in ['binance_archive','bybit','okx','binanceusdm','csv']:
        folder=DATA/source
        if not folder.exists(): continue
        for p in folder.iterdir():
            files=sorted(p.glob('*.csv.gz'))
            if files: output.append({'pair':p.name,'source':source,'months':[f.name[:7] for f in files],'size_mb':round(sum(f.stat().st_size for f in files)/1e6,2),'funding_months':[f.name[:7] for f in p.glob('*.funding.csv')]})
    return output

def load_pair(pair,c):
    files=sorted((DATA/c.source/pair).glob('*.csv.gz'))
    if not files: raise ValueError(f'{pair}: no cached {c.source} data. Download it in Data first, or load the bundled sample preset.')
    # 1.5x higher-timeframe EMA length plus one day prevents an arbitrary cold start.
    warmup=max(c.htf_ema*4*1.5/24, c.ema_slow*c.timeframe*2/1440,3)
    a=utc(c.start); b=utc(c.end); begin=a-pd.Timedelta(days=warmup)
    files=[p for p in files if p.name[:7]>=begin.strftime('%Y-%m') and p.name[:7]<=b.strftime('%Y-%m')]
    if not files: raise ValueError(f'{pair}: requested dates are not cached.')
    f=normalize(pd.concat([pd.read_csv(p) for p in files],ignore_index=True)); f=f[(f.index>=begin)&(f.index<b)]
    expected=pd.date_range(a,b,freq='1min',inclusive='left'); missing=expected.difference(f.index)
    if len(missing): raise ValueError(f'{pair}: {len(missing):,} missing minutes in requested range (first: {missing[0]}). Download full coverage or shorten the dates.')
    warnings=[]
    if len(f[f.index<a])<int(warmup*1440*.85): warnings.append(f'{pair}: limited warmup; signals wait for sufficient completed indicators.')
    funding=[]
    if c.funding_mode=='historical':
        for month in pd.period_range(a.tz_localize(None),(b-pd.Timedelta(seconds=1)).tz_localize(None),freq='M'):
            fp=DATA/c.source/pair/f'{month}.funding.csv'
            if not fp.exists(): raise ValueError(f'{pair}: historical funding missing for {month}. Download it or explicitly select Estimated funding.')
            funding.append(pd.read_csv(fp))
        funding=pd.concat(funding,ignore_index=True); funding['timestamp']=pd.to_datetime(funding.timestamp,utc=True,format='mixed')
        if funding.empty: raise ValueError(f'{pair}: historical funding file contains no events.')
        funding=funding.drop_duplicates('timestamp').sort_values('timestamp')
        if not np.isfinite(funding.rate.to_numpy(dtype=float)).all(): raise ValueError('Invalid historical funding rate.')
        # Archive monthly files establish coverage. Imported / API rates must bracket the window closely.
        if c.source!='binance_archive':
            actual=funding[(funding.timestamp>=a)&(funding.timestamp<b)]
            ts=actual.timestamp
            max_interval=pd.Timedelta(hours=c.funding_interval_hours)
            edges=[a,*list(ts),b]
            if len(actual)==0 or any(y-x>max_interval for x,y in zip(edges,edges[1:])):
                raise ValueError(f'{pair}: funding coverage has a gap. Supply all settlement events or explicitly use Estimated funding.')
        funding=funding[(funding.timestamp>=a)&(funding.timestamp<b)]
        rates={int(utc(r.timestamp).floor(f'{c.execution_minutes}min').timestamp()*1000):float(r.rate) for r in funding.itertuples()}
    elif c.funding_mode=='estimate':
        rates={int(t.timestamp()*1000):c.funding_bps/10000 for t in pd.date_range(a.floor('D'),b,freq=f'{c.funding_interval_hours}h',inclusive='left') if t>=a}
    else: rates={}
    rules={'tick_size':1e-8,'qty_step':1e-8,'min_qty':1e-8,'min_notional':5,**c.rules.get(pair,{})}
    if pair not in c.rules: warnings.append(f'{pair}: tick/quantity/minimum rules are generic assumptions; edit Contract rules for your venue.')
    report={'pair':pair,'source':c.source,'start':f.index[0].isoformat(),'end':(f.index[-1]+pd.Timedelta(minutes=1)).isoformat(),'rows':len(f),'missing_minutes':0,'zero_volume_candles':int((f.volume==0).sum()),'duplicates_removed':int(f.attrs.get('duplicates_removed',0)),'funding_events':len(rates),'sha256':hashlib.sha256(pd.util.hash_pandas_object(f,index=True).values.tobytes()).hexdigest()}
    return aggregate(f,c.execution_minutes),rates,rules,warnings,report,f

def import_csv(pair,text,funding_text=None):
    f=normalize(pd.read_csv(io.StringIO(text)))
    candle_writes=[]; funding_writes=[]
    # Validate every file and merge before any write, so invalid input is not partially imported.
    for month,group in f.groupby(f.index.strftime('%Y-%m')):
        path=month_cache(pair,'csv',month)
        if path.exists(): group=normalize(pd.concat([pd.read_csv(path),group.reset_index()]))
        candle_writes.append((path,group))
    if funding_text:
        r=pd.read_csv(io.StringIO(funding_text))
        if not {'timestamp','rate'}.issubset(r): raise ValueError('Funding CSV needs timestamp and rate (decimal, e.g. 0.0001).')
        r['timestamp']=pd.to_datetime(r.timestamp,utc=True,format='mixed')
        r['rate']=pd.to_numeric(r.rate,errors='raise')
        if not np.isfinite(r.rate).all(): raise ValueError('Invalid funding rates.')
        for month,g in r.groupby(r.timestamp.dt.strftime('%Y-%m')):
            path=DATA/'csv'/pair/f'{month}.funding.csv'
            if path.exists(): g=pd.concat([pd.read_csv(path),g])
            g['timestamp']=pd.to_datetime(g.timestamp,utc=True,format='mixed')
            for _,same in g[g.timestamp.duplicated(keep=False)].groupby('timestamp'):
                if same.rate.nunique()>1: raise ValueError('Conflicting duplicate funding events.')
            funding_writes.append((path,g.drop_duplicates('timestamp').sort_values('timestamp')))
    for path,group in candle_writes:
        path.parent.mkdir(parents=True,exist_ok=True); temp=path.with_suffix('.part'); group.to_csv(temp,compression='gzip'); temp.replace(path)
    for path,g in funding_writes:
        path.parent.mkdir(parents=True,exist_ok=True); temp=path.with_suffix('.part'); g.to_csv(temp,index=False); temp.replace(path)
    return {'rows':len(f),'pair':pair,'source':'csv'}
