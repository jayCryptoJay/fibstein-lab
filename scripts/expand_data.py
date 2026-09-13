"""Download and audit two complete years; derive only complete UTC bars."""
from pathlib import Path
import sys, json, hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd
from backend.data import DATA, download_archive, normalize, aggregate, utc, save_candles
from backend.config import PAIRS

START, END = '2024-01-01', '2026-01-01'
FRAMES = {'5m':5, '15m':15, '30m':30, '1h':60, '4h':240, '1d':1440}

def collect(pair):
    reports=[]
    for month in pd.period_range(START, '2025-12', freq='M'):
        stamp=str(month)
        download_archive(pair, month.start_time, (month+1).start_time,
                         progress=lambda message: print(message, flush=True))
        path=DATA/'binance_archive'/pair/f'{stamp}.csv.gz'
        f=normalize(pd.read_csv(path))
        expected=pd.date_range(utc(month.start_time), utc((month+1).start_time), freq='1min', inclusive='left')
        missing=expected.difference(f.index)
        extra=f.index.difference(expected)
        if len(extra): raise ValueError(f'{pair} {stamp}: timestamps outside month')
        fp=path.with_name(stamp+'.funding.csv')
        if not fp.exists(): raise ValueError(f'{pair} {stamp}: missing funding')
        funding=pd.read_csv(fp)
        if funding.empty or not pd.to_numeric(funding.rate,errors='coerce').notna().all():
            raise ValueError(f'{pair} {stamp}: invalid funding')
        counts={'1m':len(f)}
        for label,minutes in FRAMES.items():
            out=aggregate(f,minutes)
            target=DATA/'timeframes'/pair/label/f'{stamp}.csv.gz'
            target.parent.mkdir(parents=True,exist_ok=True)
            save_candles(out,target)
            counts[label]=len(out)
            if out.empty or len(out)>len(expected)//minutes: raise ValueError('Invalid aggregation count')
        report={'pair':pair,'month':stamp,'rows':counts,'missing_minutes':len(missing),
                'first_missing':[t.isoformat() for t in missing[:5]],
                'zero_volume_minutes':int((f.volume==0).sum()),'funding_events':len(funding),
                'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        reports.append(report)
        print(f'VALIDATED {pair} {stamp}: {len(f):,} minutes; missing={len(missing)}',flush=True)
    return reports

if __name__=='__main__':
    records=[]; errors=[]
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures={pool.submit(collect,p):p for p in PAIRS}
        for future in as_completed(futures):
            try: records.extend(future.result())
            except Exception as exc:
                errors.append({'pair':futures[future],'error':str(exc)})
                print('FAILED', errors[-1],flush=True)
    report={'start_inclusive':START,'end_exclusive':END,'source':'Binance USD-M perpetual monthly archives',
            'source_documentation':'https://github.com/binance/binance-public-data',
            'aggregation':'UTC open timestamps; complete source-minute bars only; no gap filling',
            'months':sorted(records,key=lambda r:(r['pair'],r['month'])),'errors':errors,
            'totals':{label:sum(r['rows'][label] for r in records) for label in ['1m',*FRAMES]}}
    (DATA/'coverage-2024-2025.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({'totals':report['totals'],'completed_pair_months':len(records),'errors':errors}),flush=True)
    if errors: sys.exit(1)
