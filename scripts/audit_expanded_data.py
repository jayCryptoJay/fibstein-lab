"""Independently re-read saved exports and verify their contents against minute data."""
from pathlib import Path
import sys, json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from backend.data import DATA, normalize, aggregate, utc
from backend.config import PAIRS
from scripts.expand_data import FRAMES

def audit():
    coverage=json.loads((DATA/'coverage-2024-2025.json').read_text())
    assert not coverage['errors'],coverage['errors']
    assert len(coverage['months'])==240
    audited=0; funding_reports=[]
    for pair in PAIRS:
        events=[]
        for month in pd.period_range('2024-01','2025-12',freq='M'):
            stamp=str(month)
            source=normalize(pd.read_csv(DATA/'binance_archive'/pair/f'{stamp}.csv.gz'))
            expected=pd.date_range(utc(month.start_time),utc((month+1).start_time),freq='1min',inclusive='left')
            assert source.index.equals(expected),f'{pair} {month}: incomplete minutes'
            for label,minutes in FRAMES.items():
                saved=pd.read_csv(DATA/'timeframes'/pair/label/f'{stamp}.csv.gz',index_col='timestamp')
                saved.index=pd.to_datetime(saved.index,utc=True)
                pd.testing.assert_frame_equal(saved,aggregate(source,minutes),check_freq=False,
                                              check_dtype=False,rtol=1e-10,atol=1e-8)
                audited+=1
            funding=pd.read_csv(DATA/'binance_archive'/pair/f'{stamp}.funding.csv')
            funding['timestamp']=pd.to_datetime(funding.timestamp,utc=True,format='mixed')
            assert len(funding)>0 and np.isfinite(funding.rate).all()
            assert ((funding.timestamp>=utc(month.start_time))&(funding.timestamp<utc((month+1).start_time))).all()
            events.append(funding)
        events=pd.concat(events,ignore_index=True).sort_values('timestamp')
        assert not events.timestamp.duplicated().any()
        interval=pd.to_numeric(events.interval_hours)
        assert np.isfinite(interval).all() and (interval>0).all()
        gaps=events.timestamp.diff().dt.total_seconds()/3600
        allowance=pd.concat([interval,interval.shift()],axis=1).max(axis=1)+1/60
        suspect=events.loc[gaps>allowance,'timestamp']
        funding_reports.append({'pair':pair,'events':len(events),'largest_gap_hours':float(gaps.max()),
            'gaps_exceeding_adjacent_reported_intervals':[t.isoformat() for t in suspect]})
        print(f'AUDITED {pair}: all monthly exports match source',flush=True)
    result={'status':'passed','monthly_exports_verified':audited,'complete_minute_months':240,
            'funding':funding_reports,'funding_gap_test':'Observed gap versus larger adjacent reported interval, with 60 seconds tolerance; not proof of exchange schedule completeness.'}
    (DATA/'independent-audit-2024-2025.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result),flush=True)

if __name__=='__main__': audit()
